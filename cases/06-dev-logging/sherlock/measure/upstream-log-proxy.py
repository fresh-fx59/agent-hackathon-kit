#!/usr/bin/env python3
"""Name the model that actually answered, one JSONL line per request.

`[SP]deepseek-v4-flash` is an ALIAS, not a model. Measured 2026-08-01 over 40
byte-identical requests carrying tool-call history, it answered as two distinct
identities that are ~19x apart on whether they emit a tool call at all:

    deepseek-v4-flash (lower)    1/21 tool calls =  4.8 %
    DeepSeek-V4-Flash (caps)    17/19 tool calls = 89.5 %

The arm under test IS a tool-execution mechanism (`logmap.py`, `citecheck`), so
which upstream a turn lands on can decide whether the arm runs — independent of
the skill. And qwen-code stamps the REQUESTED alias on every assistant message,
never the returned name, so no recorded row could be attributed to an upstream.
That is not fixable by re-reading runs; it has to be captured as it happens.

`run-case.sh` already takes SHERLOCK_BASE_URL from the environment, so this is a
URL swap rather than a harness change:

    UPSTREAM_BASE=https://linkapi.ai/v1 UPSTREAM_LOG=…/upstream.jsonl \
    LISTEN_PORT=8791 python3 measure/upstream-log-proxy.py &
    SHERLOCK_BASE_URL=http://127.0.0.1:8791/v1 bash measure/one-defect.sh v11 D04

It is deliberately dumb: it forwards bytes, it never rewrites a request, and it
never logs the Authorization header. If it cannot parse a response it records
`returned_model: null` rather than guessing — an unmeasured value is null, never
a default. → measure/probes/upstream-split.sh

Set UPSTREAM_BODY_DIR to also keep every request and response body on disk, so a
finished run can be replayed instead of inferred. See the REPLAYABLE TRACES note
below for the layout and for what those files contain.
"""
import fcntl
import gzip
import json
import os
import socket
import re
import stat
import tempfile
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lane_guard import (DEFAULT_CACHE_MIN_CALLS, DEFAULT_CACHE_MIN_RATE,
                        cache_breach, cache_tokens, model_family, same_family)

UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://linkapi.ai/v1").rstrip("/")
UPSTREAM_LOG = os.environ.get("UPSTREAM_LOG", "upstream.jsonl")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8791"))
RUN_TAG = os.environ.get("RUN_TAG", "")
RUN_ATTEMPT = os.environ.get("RUN_ATTEMPT", "")
RUN_ATTEMPT_FILE = os.environ.get("RUN_ATTEMPT_FILE", "")
UPSTREAM_INFLIGHT = os.environ.get("UPSTREAM_INFLIGHT", "")
UPSTREAM_BUDGET_STATE = os.environ.get("UPSTREAM_BUDGET_STATE", "")
UPSTREAM_EXPECTED_RETURNED_IDENTITY = os.environ.get(
    "UPSTREAM_EXPECTED_RETURNED_IDENTITY", "")
PROXY_INSTANCE = str(uuid.uuid4())
# THE 177,000-TOKEN CEILING WAS A MODEL-ID PARSING ARTIFACT, not a real limit.
# qwen-code sizes the context window from the model id, and its own normalize()
# turns "[SP]deepseek-v4-flash" into "[sp]deepseek-v4-flash", which matches no
# entry in its table and falls back to DEFAULT_TOKEN_LIMIT = 200,000 — from
# which the 177,000 hard limit follows. Drop the prefix and the same table gives
# /^deepseek-v4/ => 1,000,000. Verified by running qwen-code's own normalize().
# linkapi needs the prefix; qwen-code must not see it. So qwen-code is given the
# clean id and this restores the alias on the way out.
UPSTREAM_MODEL = os.environ.get("UPSTREAM_MODEL", "")
# RIDE OUT A PROVIDER BURST. linkapi's 400s are transient and minute-scale —
# measured 2026-08-02, and NOT explained by request size or shape (both
# controlled for, interleaved, 12/12 succeeded at the size and shape that had
# just failed). The problem is that qwen-code's own retry budget is SHORTER than
# a burst: D11 took 4 × 400 at 143 KB then a 200, then 5 × 400 at 171 KB and the
# run was over — 98,515 tokens billed, no row. This proxy is the only thing in
# the path that can wait longer than the client will.
# Off by default (0) so the proxy stays a pass-through unless a runner asks.
UPSTREAM_RETRY_MAX = int(os.environ.get("UPSTREAM_RETRY_MAX", "0") or 0)
UPSTREAM_RETRY_BASE_MS = int(os.environ.get("UPSTREAM_RETRY_BASE_MS", "2000") or 2000)
# DO NOT RIDE A DEAD STREAM. urlopen's timeout is a PER-SOCKET-READ deadline, not
# a call budget, so `for raw in resp:` on a stream that sends nothing blocks for
# up to UPSTREAM_READ_TIMEOUT — half an hour. MEASURED on the v36 winevtx run: 20
# of 185 calls returned a well-formed SSE stream carrying no content, costing
# 2,094,389 prompt tokens for zero output and 2,927 s; two ran 311 s and 502 s.
# The ~126 s client stream timeout never applies, because this proxy owns the
# upstream socket.
#
# Keyed on CONTENT, deliberately. `returned_model == "[SP]deepseek-v4-flash"`
# predicted the dud 11 times out of 11 on that run, but aborting on an alias
# blacklist is a special case that moves the cliff the day the provider picks a
# different one. A deadline on "no content yet" catches every signature,
# including the ones not yet seen.
# Off by default (0) so the proxy stays a pass-through unless a runner asks.
UPSTREAM_FIRST_TOKEN_MS = int(os.environ.get("UPSTREAM_FIRST_TOKEN_MS", "0") or 0)
# The long read timeout, named once instead of inlined at the urlopen call,
# because the first-token deadline has to restore exactly this value.
UPSTREAM_READ_TIMEOUT = float(os.environ.get("UPSTREAM_READ_TIMEOUT", "1800") or 1800)

# REPLAYABLE TRACES. Everything above this line is metadata ABOUT a call, and no
# amount of it answers the first question anyone asks of a finished run: what
# exactly was sent to the model, and what exactly did it answer? That gap has
# been paid for twice already. The r2 empty-HTTP-200 had to be diagnosed by
# reading qwen-code's own minified bundle, because no request body existed to
# look at. And a run's gate output survived only as model-side prose in the
# trajectory, so "did the tool really say that?" was unanswerable. Bodies are
# big — a winevtx request peaks near 620 KB and a run makes ~113 calls — so this
# is opt-in, lands OUTSIDE the JSONL, and is gzipped.
#
# WHAT THE CAPTURED FILES CONTAIN, plainly: the request file is the WHOLE prompt
# — system prompt, skill body, every tool result, and therefore the corpus log
# lines the model was fed. The response file is the model's whole answer. These
# files are as sensitive as the corpus itself; treat a body directory the way
# you treat the corpus directory.
#
# There is NO credential in them. The key travels in the Authorization header,
# and headers are never written here: `headers` is built once in `_relay` and
# handed to urllib, and nothing in this file serialises it. Verified by
# inspection of every write path (record(), _write_budget, _update_inflight,
# _capture_write) — only `record()` touches the ledger and only bodies reach
# BODY_DIR. `_scrub` is deliberately NOT applied to a captured body: it exists
# to keep a key out of a provider's error prose, and running those patterns over
# a 620 KB prompt would rewrite the one thing a replay needs, the exact bytes.
BODY_DIR = os.environ.get("UPSTREAM_BODY_DIR", "")
# Generous on purpose: 32 MiB is ~50x the largest request measured on this lane,
# so the cap only fires on something already pathological — and when it fires it
# says so in the row rather than leaving a short file that reads as complete.
_BODY_MAX_DEFAULT = 32 * 1024 * 1024
BODY_MAX_BYTES = int(os.environ.get("UPSTREAM_BODY_MAX_BYTES") or _BODY_MAX_DEFAULT)
if BODY_MAX_BYTES <= 0:
    # A zero or negative cap would capture nothing while marking every row
    # truncated: a file that exists, is empty, and blames the body. Refuse the
    # value instead of encoding it — the same rule as `_positive_env`.
    raise ValueError("UPSTREAM_BODY_MAX_BYTES must be a positive integer")


def _positive_env(name):
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError("%s must be a positive integer" % name)
    return value


_BUDGET_LIMITS = {
    "max_upstream_attempts": _positive_env("UPSTREAM_MAX_UPSTREAM_ATTEMPTS"),
    "max_request_bytes": _positive_env("UPSTREAM_MAX_REQUEST_BYTES"),
    "max_wall_seconds": _positive_env("UPSTREAM_MAX_WALL_SECONDS"),
    "max_consecutive_provider_failures": _positive_env(
        "UPSTREAM_MAX_CONSECUTIVE_PROVIDER_FAILURES"),
}
_BUDGET_ENABLED = bool(UPSTREAM_BUDGET_STATE)
if _BUDGET_ENABLED and (not RUN_TAG or not UPSTREAM_EXPECTED_RETURNED_IDENTITY or
                        any(value is None for value in _BUDGET_LIMITS.values())):
    raise ValueError("controlled proxy requires run identity and all finite limits")
# Only statuses that are transient on this lane. A 401/404/422 is a real defect
# in the request and retrying it just burns the context again for nothing.
_RETRYABLE = {400, 408, 429, 500, 502, 503, 504}

_BASE_PATH = urlsplit(UPSTREAM_BASE).path.rstrip("/")     # e.g. "/v1"
_LOG_LOCK = threading.Lock()
_INFLIGHT_LOCK = threading.Lock()
_BUDGET_LOCK = threading.Lock()
_MAX_BUDGET_BYTES = 1024 * 1024
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")

# Hop-by-hop headers are per-connection and must not be relayed (RFC 7230 §6.1).
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host",
        "content-length", "accept-encoding"}


# A credential must never reach the ledger. This proxy already never logs the
# Authorization header, but `upstream_error` carries the provider's own words, and a
# provider that quotes the caller's key back in a refusal would write it here —
# verbatim, durable, in every run directory. Scrubbing happens in record() rather
# than at each call site so any field added later is covered without being
# remembered. The patterns are deliberately narrow: over-redaction would undo the
# reason this field exists, which is that 60 failures on D04 were bare integers.
_SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<redacted>"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1<redacted>"),
    (re.compile(r"(?i)\b(api[-_]?key|token|secret)(\"?\s*[:=]\s*\"?)"
                r"[A-Za-z0-9._~+/=-]{16,}"), r"\1\2<redacted>"),
)


def _scrub(value):
    if not isinstance(value, str):
        return value
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def record(**row):
    row = {k: _scrub(v) for k, v in row.items()}
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if RUN_TAG:
        row["run_tag"] = RUN_TAG
    line = json.dumps(row, ensure_ascii=False)
    with _LOG_LOCK:                       # ThreadingHTTPServer ⇒ concurrent turns
        with open(UPSTREAM_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class BudgetUnknown(Exception):
    pass


def _budget_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _strict_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError("duplicate JSON key")
        row[key] = value
    return row


def _read_budget():
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(UPSTREAM_BUDGET_STATE)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise BudgetUnknown()
        fd = os.open(UPSTREAM_BUDGET_STATE, os.O_RDONLY | nofollow)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_BUDGET_BYTES or
                    (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise BudgetUnknown()
            chunks = []; total = 0
            while total <= _MAX_BUDGET_BYTES:
                chunk = os.read(fd, min(65536, _MAX_BUDGET_BYTES + 1 - total))
                if not chunk: break
                chunks.append(chunk); total += len(chunk)
            finished = os.fstat(fd)
            if (total > _MAX_BUDGET_BYTES or total != finished.st_size or
                    (opened.st_dev, opened.st_ino, opened.st_size) !=
                    (finished.st_dev, finished.st_ino, finished.st_size)):
                raise BudgetUnknown()
        finally:
            os.close(fd)
        return json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_strict_object)
    except BudgetUnknown:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise BudgetUnknown() from exc


def _budget_shape(row):
    fields = {"schema", "run_tag", "updated_at", "attempts_charged",
              "request_bytes", "consecutive_provider_failures", "limits",
              "verdict", "reason"}
    return (isinstance(row, dict) and set(row) == fields and row.get("schema") == 1 and
            row.get("run_tag") == RUN_TAG and row.get("limits") == _BUDGET_LIMITS and
            all(type(row.get(name)) is int and row[name] >= 0 for name in
                ("attempts_charged", "request_bytes", "consecutive_provider_failures")) and
            row.get("verdict") in ("WITHIN", "EXCEEDED") and
            (row.get("reason") is None or
             (isinstance(row.get("reason"), str) and _REASON_CODE.fullmatch(row["reason"]))))


def _write_budget(row):
    directory = os.path.dirname(os.path.abspath(UPSTREAM_BUDGET_STATE))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upstream-budget-state.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(row, target, ensure_ascii=False, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, UPSTREAM_BUDGET_STATE)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _reconcile_completed(row):
    """Keep durable reservations at least as large as completed paid rows."""
    attempts = 0
    request_bytes = 0
    try:
        with _LOG_LOCK, open(UPSTREAM_LOG, encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                completed = json.loads(line)
                if not isinstance(completed, dict):
                    raise BudgetUnknown()
                if completed.get("run_tag") != RUN_TAG:
                    continue
                charged = completed.get("request_bytes")
                if type(charged) is not int or charged < 0:
                    raise BudgetUnknown()
                attempts += 1
                request_bytes += charged
    except FileNotFoundError:
        return row
    except (OSError, ValueError, TypeError) as exc:
        raise BudgetUnknown() from exc
    row["attempts_charged"] = max(row["attempts_charged"], attempts)
    row["request_bytes"] = max(row["request_bytes"], request_bytes)
    if row["verdict"] == "WITHIN":
        if row["attempts_charged"] > row["limits"]["max_upstream_attempts"]:
            row.update(verdict="EXCEEDED", reason="MAX_UPSTREAM_ATTEMPTS")
        elif row["request_bytes"] > row["limits"]["max_request_bytes"]:
            row.update(verdict="EXCEEDED", reason="MAX_REQUEST_BYTES")
    return row


def _budget_update(change):
    if not _BUDGET_ENABLED:
        return None
    lock_path = UPSTREAM_BUDGET_STATE + ".lock"
    directory = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(directory, exist_ok=True)
    with _BUDGET_LOCK, open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            row = _read_budget()
            if not _budget_shape(row):
                raise BudgetUnknown()
            row = _reconcile_completed(row)
            changed = change(dict(row))
            changed["updated_at"] = _budget_timestamp()
            if not _budget_shape(changed):
                raise BudgetUnknown()
            _write_budget(changed)
            return changed
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _reserve_budget(request_bytes):
    def reserve(row):
        if row["verdict"] == "EXCEEDED":
            return row
        attempts = row["attempts_charged"] + 1
        total_bytes = row["request_bytes"] + request_bytes
        if attempts > row["limits"]["max_upstream_attempts"]:
            row.update(verdict="EXCEEDED", reason="MAX_UPSTREAM_ATTEMPTS")
        elif total_bytes > row["limits"]["max_request_bytes"]:
            row.update(verdict="EXCEEDED", reason="MAX_REQUEST_BYTES")
        else:
            row["attempts_charged"] = attempts
            row["request_bytes"] = total_bytes
        return row
    row = _budget_update(reserve)
    return row is None or row["verdict"] == "WITHIN"


def _record_budget_result(success):
    def finish(row):
        row["consecutive_provider_failures"] = (
            0 if success else row["consecutive_provider_failures"] + 1)
        if (not success and row["consecutive_provider_failures"] >=
                row["limits"]["max_consecutive_provider_failures"]):
            row.update(verdict="EXCEEDED", reason="MAX_CONSECUTIVE_PROVIDER_FAILURES")
        return row
    return _budget_update(finish)


# ===========================================================================
# LANE INTEGRITY — abort the run, on the call that proves it, not days later.
#
# WHAT WENT WRONG. The v37 full run made 180 metered calls and every signal
# this proxy owned said healthy. linkapi had silently answered 93 of them as
# `deepseek-v4-pro-0813` while the run believed it was measuring
# `deepseek-v4-flash`. The two identities are two provider cache pools, so the
# prompt-cache hit rate collapsed 68.1 % -> 28.0 % and fresh prompt tokens went
# 5.92M -> 13.38M. Nothing objected. A human found it days later by diffing
# this ledger by hand.
#
# WHY THE GUARD IS HERE AND NOT IN run-bench.sh. run-bench.sh can only look
# after the CLI has exited, i.e. after all 180 calls are billed. This process
# sees `returned_model` on call 1 and the cumulative cache rate on call 20. The
# money argument settles it: aborting here costs one wrong call, aborting there
# costs the run. Both exist anyway — measure/lane-audit.py re-checks the
# finished ledger, because a proxy that DIED is also a proxy that saw nothing,
# and that must not read as clean.
#
# WHAT ABORTING MEANS, given the lane's contract. This helper never kills the
# CLI and never exits the process: `upstream_lane_start` promises a working
# base URL, and a proxy that vanishes mid-run would leave a stripped model id
# pointed at nothing. Instead it does exactly what the existing paid-budget
# breach does — refuse every subsequent request with 503 and mark the budget
# state EXCEEDED, which bench-controller.sh already polls, terminates the run
# group on, and persists as a BLOCKED phase with this reason code. In addition
# it writes a standalone abort marker next to the ledger, so a run launched
# WITHOUT the controller (every direct paid launcher) still has the diagnosis
# in its artifacts rather than only in stderr.
#
# The abort is one-way and first-reason-wins: a run that trips is over, and the
# first observation is the one that explains it.
UPSTREAM_LANE_ABORT = os.environ.get("UPSTREAM_LANE_ABORT", "")
# Disable for a genuinely cold lane — a first-ever run against a new provider
# has no cache to hit and would trip this honestly. Nothing else may turn it
# off: "the run kept failing so we disabled the check" is how v37 happens twice.
UPSTREAM_CACHE_GUARD = os.environ.get("UPSTREAM_CACHE_GUARD", "1") != "0"
UPSTREAM_CACHE_MIN_RATE = float(
    os.environ.get("UPSTREAM_CACHE_MIN_RATE") or DEFAULT_CACHE_MIN_RATE)
UPSTREAM_CACHE_MIN_CALLS = int(
    os.environ.get("UPSTREAM_CACHE_MIN_CALLS") or DEFAULT_CACHE_MIN_CALLS)

_LANE_LOCK = threading.Lock()
_LANE_ABORT = None          # first breach, {"reason": ..., "detail": ...}
_LANE_MARKER_WRITTEN = threading.Event()   # set once abort.json exists on disk
_LANE_CACHE = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0}


def _write_lane_abort(row):
    """Durable, atomic, and never overwritten — the first reason is the reason."""
    if not UPSTREAM_LANE_ABORT:
        return
    directory = os.path.dirname(os.path.abspath(UPSTREAM_LANE_ABORT))
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".upstream-lane-abort.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(row, target, ensure_ascii=False, sort_keys=True)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, UPSTREAM_LANE_ABORT)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except OSError:
        # The marker is one of three places the breach is recorded (ledger row,
        # budget state, marker). Losing one must not stop the refusal.
        pass


def _lane_trip(reason, detail):
    """Trip the lane, and do not let a refusal go out before the marker exists.

    THE RACE THIS CLOSES. The marker write is mkstemp -> write -> fsync ->
    os.replace. `save_trace` in run-bench.sh kills the proxy the moment the CLI
    exits, and the CLI exits as soon as it sees the refusal. Measured on this
    box, 11 of 12 trials: the refusal was served, the proxy was terminated
    inside the fsync, and an orphan `.upstream-lane-abort.XXXXXXXX` was left
    holding the complete correct row while `abort.json` never appeared. No
    marker, no `LANE ABORT` on stderr - and combined with the dead RC-5 audit
    the run then reported no lane verdict at all.

    The fix is a barrier, not a reordering. `_LANE_ABORT` is still published
    first, so the very next request is refused rather than relayed (the whole
    value of the guard is the call that does NOT happen). `_lane_refusal` then
    BLOCKS on `_LANE_MARKER_WRITTEN` before it answers, so no client can learn
    the lane is dead until the diagnosis is durable. The fsync stays: on a
    direct paid launcher this marker is the only artifact that carries the
    reason, so it has to survive a crash as well as a kill.
    """
    global _LANE_ABORT
    with _LANE_LOCK:
        if _LANE_ABORT is not None:
            return
        _LANE_ABORT = {"reason": reason, "detail": detail}
    row = {"schema": 1, "run_tag": RUN_TAG, "reason": reason, "detail": detail,
           "observed_at": _budget_timestamp(),
           "expected_returned_identity": UPSTREAM_EXPECTED_RETURNED_IDENTITY,
           "cache_min_rate": UPSTREAM_CACHE_MIN_RATE,
           "cache_min_calls": UPSTREAM_CACHE_MIN_CALLS,
           "cache_observed": dict(_LANE_CACHE)}
    _write_lane_abort(row)
    sys.stderr.write("upstream-log-proxy: LANE ABORT %s — %s\n" % (reason, detail))
    sys.stderr.flush()
    _LANE_MARKER_WRITTEN.set()

    def mark(state):
        # Never downgrade an existing breach; the paid-budget reasons and this
        # one are equally terminal and the first one is the true cause.
        if state["verdict"] == "WITHIN":
            state.update(verdict="EXCEEDED", reason=reason)
        return state

    try:
        _budget_update(mark)
    except BudgetUnknown:
        pass


def _lane_observe(status, returned, usage):
    """Judge one completed call. Called after its ledger row is written.

    Only calls the provider actually ANSWERED are judged. A 400 or a dead
    socket names no model, and tripping on that would abort the run on exactly
    the transient burst this proxy exists to ride out — that shape is already
    covered by MAX_CONSECUTIVE_PROVIDER_FAILURES. A 2xx that names no model at
    all is likewise not treated as a mismatch here (it is not evidence of a
    substitution), but it is NOT treated as clean either: it fails the existing
    `success` test above, and lane-audit.py refuses a whole ledger in which no
    2xx row ever named a model (RETURNED_MODEL_UNKNOWN).
    """
    if not (type(status) is int and 200 <= status < 300):
        return
    if (UPSTREAM_EXPECTED_RETURNED_IDENTITY and isinstance(returned, str)
            and returned.strip()
            and not same_family(UPSTREAM_EXPECTED_RETURNED_IDENTITY, returned)):
        _lane_trip("RETURNED_MODEL_FAMILY_MISMATCH",
                   "requested %s (family %s), provider answered as %s (family %s)"
                   % (UPSTREAM_EXPECTED_RETURNED_IDENTITY,
                      model_family(UPSTREAM_EXPECTED_RETURNED_IDENTITY),
                      returned, model_family(returned)))
        return
    if not UPSTREAM_CACHE_GUARD:
        return
    billed, hit = cache_tokens(usage)
    if not billed:
        return
    with _LANE_LOCK:
        _LANE_CACHE["calls"] += 1
        _LANE_CACHE["prompt_tokens"] += billed
        _LANE_CACHE["cached_tokens"] += hit
        calls = _LANE_CACHE["calls"]
        prompt_tokens = _LANE_CACHE["prompt_tokens"]
        cached_tokens = _LANE_CACHE["cached_tokens"]
    detail = cache_breach(calls, prompt_tokens, cached_tokens,
                          UPSTREAM_CACHE_MIN_RATE, UPSTREAM_CACHE_MIN_CALLS)
    if detail:
        _lane_trip("PROMPT_CACHE_COLLAPSE", detail)


def _update_inflight(request_id, row=None):
    """Atomically add or remove one request from the trace-local live map."""
    if not UPSTREAM_INFLIGHT:
        return
    directory = os.path.dirname(os.path.abspath(UPSTREAM_INFLIGHT))
    os.makedirs(directory, exist_ok=True)
    lock_path = UPSTREAM_INFLIGHT + ".lock"
    with _INFLIGHT_LOCK, open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                with open(UPSTREAM_INFLIGHT, encoding="utf-8") as source:
                    requests = json.load(source).get("requests", {})
            except (OSError, ValueError, AttributeError):
                requests = {}
            if row is None:
                requests.pop(request_id, None)
            else:
                requests[request_id] = row
            if not requests:
                try:
                    os.unlink(UPSTREAM_INFLIGHT)
                except FileNotFoundError:
                    pass
                return
            temporary = UPSTREAM_INFLIGHT + ".tmp.%d.%s" % (os.getpid(), uuid.uuid4().hex)
            try:
                with open(temporary, "w", encoding="utf-8") as target:
                    json.dump({"requests": requests}, target, ensure_ascii=False, sort_keys=True)
                    target.write("\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, UPSTREAM_INFLIGHT)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _attempt():
    if RUN_ATTEMPT_FILE:
        try:
            return open(RUN_ATTEMPT_FILE, encoding="utf-8").read().strip()[:32]
        except OSError:
            pass
    return RUN_ATTEMPT


# How much of an upstream error body is kept. 300 chars is enough for every
# provider message seen on this lane ("Upstream request failed", a rate-limit
# notice, a context-length refusal) and short enough that a body which echoes
# part of the REQUEST cannot drag corpus lines into the log. It lands in the run
# dir next to the trajectory, and it never contains a header.
_ERR_CHARS = 300


def _err_text(payload):
    """The provider's own words for a refusal, truncated.

    Sixty 400s were recorded on D04 as a bare integer while this very body was
    read and thrown away in the retry path. "Rate limit exceeded" and "invalid
    request" are indistinguishable in a status column, and three separate
    theories about these failures were argued from counts because of it.
    """
    if not payload:
        return None
    return payload.decode("utf-8", "replace").strip()[:_ERR_CHARS] or None


def _capture_new(request_id):
    return {"request_id": request_id, "request_file": None, "response_file": None,
            "request_truncated": False, "response_truncated": False, "error": None}


def _capture_row(capture):
    """The fields that make the JSONL -> file join explicit instead of guessed.

    Empty when capture is off, so a run without UPSTREAM_BODY_DIR keeps writing
    rows with exactly the keys every previous run wrote — the ledger's shape is
    already parsed by validate-run.py and by hand-written probes, and a new key
    appearing unbidden is how a "no-op" flag breaks a reader.
    """
    if not BODY_DIR:
        return {}
    return {"request_id": capture["request_id"],
            "body_request_file": capture["request_file"],
            "body_response_file": capture["response_file"],
            "body_request_truncated": capture["request_truncated"],
            "body_response_truncated": capture["response_truncated"],
            "body_capture_error": capture["error"]}


def _capture_write(name, chunks, capture, which):
    """gzip `chunks` into BODY_DIR/name, temp-then-rename, and never raise.

    This code sits in the paid request path, so every failure mode has to end as
    a note in the row. A capture that could abort or stall a relay would trade
    away the run it is trying to observe — the same trade `_update_inflight` and
    the `record()` call sites already refuse with their bare `except OSError`.
    Temp-then-rename means a reader never finds a half-written .gz and mistakes
    a truncated member for a truncated body.
    """
    total = 0
    truncated = False
    kept = []
    for chunk in chunks:
        room = BODY_MAX_BYTES - total
        if room <= 0:
            truncated = True
            break
        if len(chunk) > room:
            kept.append(chunk[:room])
            total += room
            truncated = True
            break
        kept.append(chunk)
        total += len(chunk)
    temporary = os.path.join(BODY_DIR, ".%s.%d.%s.tmp"
                             % (name, os.getpid(), uuid.uuid4().hex))
    try:
        os.makedirs(BODY_DIR, exist_ok=True)
        with gzip.open(temporary, "wb", compresslevel=6) as target:
            for chunk in kept:
                target.write(chunk)
        os.replace(temporary, os.path.join(BODY_DIR, name))
    except Exception as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        capture["error"] = ("%s: %s" % (which, exc))[:300]
        return
    capture[which + "_file"] = name
    capture[which + "_truncated"] = truncated


def _stream_socket(resp):
    """The raw socket under an HTTPResponse, or None if it cannot be reached.

    Needed because the deadline CANNOT live inside `for raw in resp:` — that
    loop calls readline() and blocks there, so a check in the loop body only
    runs once a line has already arrived, which is exactly the event that is
    never coming. The deadline has to be armed on the socket itself.
    """
    fp = getattr(resp, "fp", None)
    for attr in ("_sock", "raw"):
        obj = getattr(fp, attr, None)
        if obj is None:
            continue
        if hasattr(obj, "settimeout"):
            return obj
        inner = getattr(obj, "_sock", None)
        if hasattr(inner, "settimeout"):
            return inner
    return fp if hasattr(fp, "settimeout") else None


def _scan_obj(obj, state):
    """Pull what we care about out of one response object.

    `usage` and `finish_reason` were BOTH being read and thrown away here, and
    both absences cost real diagnostic time. qwen-code sends
    `stream_options={"include_usage": true}`, so every stream already carries a
    usage object — dropping it is why "did the English translation cut TOKENS
    or only BYTES?" could not be answered from any run log and needed an
    offline tokenizer with a documented caveat instead. It also means a run
    that clamps on TOKENS could only ever be checked in BYTES, through an
    estimated 3.42 bytes/token. `finish_reason` is the whole difference between
    "the max_tokens clamp truncated us" (length), "the model stopped" (stop),
    and qwen's own NO_FINISH_REASON throw (absent) — the r2 empty-HTTP-200
    diagnosis had to be reconstructed from a source read of the CLI bundle.
    Both are integers and a short enum: no message text, no corpus, nothing new
    for `_scrub` to worry about.
    """
    if not isinstance(obj, dict):
        return
    # A REFUSAL SPLICED INTO A 200 IS STILL A REFUSAL. The v36 run's ledger
    # reported "0 non-200s" while three calls carried
    # {"error":{"message":"Concurrency limit exceeded for account, ...}} in the
    # body. Only the type and a short reason are kept: never the message text,
    # which can echo the request.
    err = obj.get("error")
    if isinstance(err, dict) and not state["error"]:
        kind = err.get("type") or err.get("code") or "error"
        state["error"] = "upstream_error_in_200:%s" % str(kind)[:64]
    if obj.get("model") and not state["returned_model"]:
        state["returned_model"] = obj["model"]
    if isinstance(obj.get("usage"), dict):
        state["usage"] = obj["usage"]          # last one wins: the final chunk
    for ch in obj.get("choices") or []:
        if not isinstance(ch, dict):
            continue
        if ch.get("finish_reason"):
            state["finish_reason"] = ch["finish_reason"]
        for key in ("message", "delta"):
            part = ch.get(key)
            if isinstance(part, dict) and part.get("tool_calls"):
                state["tool_call"] = True
            # CONTENT, not merely "an event". A usage-only chunk is an event and
            # carries nothing; that distinction is the whole deadline.
            if isinstance(part, dict) and (part.get("content")
                                           or part.get("tool_calls")
                                           or part.get("reasoning_content")):
                if not state["content_events"] and state.get("stream_started_at"):
                    state["ttft_ms"] = int(
                        (time.time() - state["stream_started_at"]) * 1000)
                state["content_events"] += 1


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "upstream-log-proxy/1"

    def log_message(self, *a):
        pass                                        # the JSONL is the log

    def do_GET(self):
        if self.path.rstrip("/") in ("/healthz", "/health"):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._relay(b"")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._relay(self.rfile.read(n) if n else b"")

    # ------------------------------------------------------------------
    def _upstream_url(self):
        path = self.path
        if _BASE_PATH and path.startswith(_BASE_PATH):
            path = path[len(_BASE_PATH):]
        return UPSTREAM_BASE + (path if path.startswith("/") else "/" + path)

    def _relay(self, body):
        # A tripped lane answers nothing. Checked before the body is even
        # inspected: the whole value of the guard is the call that does NOT
        # happen.
        if _LANE_ABORT is not None:
            self._lane_refusal()
            return
        t0 = time.time()
        requested = sent = None
        if UPSTREAM_MODEL and body:
            # Rewrite ONLY the model field, and only when the body parses as the
            # object we expect. A proxy that reformats a request it did not
            # fully understand is a proxy that corrupts one silently.
            try:
                obj = json.loads(body)
                if isinstance(obj, dict) and obj.get("model"):
                    requested = obj["model"]
                    obj["model"] = UPSTREAM_MODEL
                    sent = UPSTREAM_MODEL
                    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass
        if requested is None:
            try:
                requested = (json.loads(body or b"{}") or {}).get("model")
            except Exception:
                pass

        # LOG THE OUTBOUND OUTPUT BUDGET. `prompt + max_tokens` is what the
        # provider checks, not the prompt alone, and an unclamped qwen-code
        # auto-escalates max_tokens with a 64K floor — which is how a request
        # whose prompt fits still comes back an empty HTTP 200 (vllm#3851).
        # request_bytes alone could never show that, so every past diagnosis of
        # a truncated run had to guess at this number. One integer, no body.
        request_max_tokens = None
        try:
            request_max_tokens = (json.loads(body or b"{}") or {}).get("max_tokens")
        except Exception:
            pass
        if not isinstance(request_max_tokens, int):
            request_max_tokens = None

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in _HOP}
        # identity so the body stays scannable; the client gets it un-encoded,
        # which is what every OpenAI-compatible client already accepts.
        headers["Accept-Encoding"] = "identity"
        req = urllib.request.Request(self._upstream_url(), data=body or None,
                                     headers=headers, method=self.command)
        request_id = uuid.uuid4().hex
        capture = _capture_new(request_id)
        if BODY_DIR:
            # The bytes captured are the bytes actually SENT — after the
            # UPSTREAM_MODEL rewrite above, not as the client wrote them. A
            # replay has to reproduce what the provider saw, and the rewrite is
            # exactly the kind of proxy-side edit a reader would never guess.
            _capture_write("%s.req.json.gz" % request_id, [body], capture, "request")
        try:
            _update_inflight(request_id, {
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "request_bytes": len(body), "path": self.path,
                "requested_model": requested, "request_max_tokens": request_max_tokens,
                "attempt": _attempt(),
                "run_tag": RUN_TAG, "pid": os.getpid(), "proxy_instance": PROXY_INSTANCE,
            })
        except OSError:
            pass  # observability can never alter request delivery
        # HTTP 200 only says the front door accepted the request. Providers can
        # splice an HTTP error into SSE after many successful turns; the client
        # sees malformed JSON while a status-only ledger says success.
        state = {"returned_model": None, "tool_call": False, "error": None,
                 "usage": None, "finish_reason": None,
                 "stream_events": 0, "stream_parse_errors": 0,
                 "content_events": 0, "ttft_ms": None, "stream_started_at": None,
                 "deadline_unenforceable": False,
                 "stream_complete": None, "stream_bytes": 0,
                 "response_valid": False, "res_chunks": []}
        status = None
        attempt = 0
        try:
          while True:
            attempt += 1
            t_try = time.time()
            try:
                if not _reserve_budget(len(body)):
                    self._budget_refusal()
                    return
            except BudgetUnknown:
                self._budget_refusal()
                return
            try:
                resp = urllib.request.urlopen(req, timeout=UPSTREAM_READ_TIMEOUT)
                status = resp.getcode()
                break
            except urllib.error.HTTPError as e:
                resp, status = e, e.code
                if (status in _RETRYABLE and attempt <= UPSTREAM_RETRY_MAX):
                    # EVERY attempt is recorded, because every attempt re-uploads
                    # the whole context and is therefore billed. An invisible
                    # retry is the "failed calls are free" mistake all over again.
                    # Read the body BEFORE recording — it was already being read
                    # and discarded here, which is why every 400 in this ledger
                    # is a bare status code with no reason attached.
                    try:
                        raw = e.read()
                    except Exception:
                        raw = b""
                    why = _err_text(raw)
                    if BODY_DIR:
                        # upstream_error keeps 300 chars so the ledger stays
                        # readable; the file keeps all of it, which is where a
                        # provider's structured refusal actually lives.
                        _capture_write("%s.a%d.res.json.gz" % (request_id, attempt),
                                       [raw], capture, "response")
                    try:
                        record(**_capture_row(capture),
                               request_max_tokens=request_max_tokens, requested_model=requested, returned_model=None,
                               tool_call=False, status=status, attempt=attempt,
                               duration_ms=int((time.time() - t_try) * 1000),
                               sent_model=sent, request_bytes=len(body),
                               path=self.path, stream=False, upstream_error=why)
                    except OSError:
                        pass
                    try:
                        _record_budget_result(False)
                    except BudgetUnknown:
                        self._budget_refusal()
                        return
                    time.sleep(min(60.0, (UPSTREAM_RETRY_BASE_MS / 1000.0)
                                   * (2 ** (attempt - 1))))
                    req = urllib.request.Request(self._upstream_url(),
                                                 data=body or None,
                                                 headers=headers,
                                                 method=self.command)
                    continue
                break
            except Exception as e:                   # DNS, TLS, refused, timeout
                try:
                    record(**_capture_row(capture),
                           request_max_tokens=request_max_tokens, requested_model=requested, returned_model=None,
                           tool_call=False, status=None, error=str(e)[:300],
                           attempt=attempt,
                           duration_ms=int((time.time() - t0) * 1000), sent_model=sent,
                           request_bytes=len(body), path=self.path, stream=False)
                except OSError:
                    pass
                try:
                    _record_budget_result(False)
                except BudgetUnknown:
                    pass
                self.send_response(502)
                payload = json.dumps({"error": {"message": "proxy: %s" % e}}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

          ctype = (resp.headers.get("Content-Type") or "")
          streaming = "text/event-stream" in ctype.lower()
          try:
              if streaming:
                  self._pump_stream(resp, state)
              else:
                  self._pump_whole(resp, status, state)
          finally:
              success = (type(status) is int and 200 <= status < 300 and
                         state["returned_model"] == UPSTREAM_EXPECTED_RETURNED_IDENTITY and
                         ((streaming and state["stream_complete"] is True and
                           state["stream_parse_errors"] == 0) or
                          (not streaming and state["response_valid"])))
              try:
                  _record_budget_result(success)
              except BudgetUnknown:
                  pass
              if BODY_DIR:
                  # Written in the same `finally` that records the row, so a
                  # client disconnect mid-stream still leaves the bytes that DID
                  # arrive on disk — a partial stream is the case worth reading.
                  _capture_write("%s.a%d.res.%s.gz"
                                 % (request_id, attempt, "sse" if streaming else "json"),
                                 state["res_chunks"], capture, "response")
              try:
                  record(**_capture_row(capture),
                         request_max_tokens=request_max_tokens, requested_model=requested,
                         returned_model=state["returned_model"], attempt=attempt,
                         tool_call=state["tool_call"], status=status,
                         usage=state["usage"], finish_reason=state["finish_reason"],
                         duration_ms=int((time.time() - t0) * 1000), sent_model=sent,
                         request_bytes=len(body), path=self.path, stream=streaming,
                         upstream_error=state["error"], stream_events=state["stream_events"],
                         content_events=state["content_events"], ttft_ms=state["ttft_ms"],
                         deadline_unenforceable=state["deadline_unenforceable"] or None,
                         stream_parse_errors=state["stream_parse_errors"],
                         stream_complete=state["stream_complete"], stream_bytes=state["stream_bytes"])
              except OSError:
                  pass
              # AFTER the row, always. The call that trips the guard has to be
              # in the ledger, or the artifact that explains the abort is the
              # one call the ledger does not contain.
              _lane_observe(status, state["returned_model"], state["usage"])
        finally:
            try:
                _update_inflight(request_id)
            except OSError:
                pass

    def _lane_refusal(self):
        """403, not 503 - a lane breach is a verdict, not a transport hiccup.

        qwen-code's `defaultShouldRetry` retries every 5xx, `hasRetryAfterStatus`
        treats 503 as a Retry-After status, and
        `FALLBACK_ELIGIBLE_STATUS_CODES = {429, 503, 529}` classes it as
        fallback-eligible (chunks/chunk-YDJRMQU4.js:41,49 and
        chunks/chunk-7IV52LTO.js:187 of @qwen-code/qwen-code). A 503 refusal
        therefore burned the client's 7-attempt retry budget and then presented
        the run's own verdict as "the provider is down" - the opposite of the
        diagnosis. 403 is in none of those sets: not >= 500, not a rate-limit
        code, not fallback-eligible. One refusal, one honest error.

        The PAID-BUDGET refusal below deliberately keeps its 503: that path is
        wired into MAX_CONSECUTIVE_PROVIDER_FAILURES accounting and the
        controller's polling, and moving it is not this branch's business.
        """
        # Never tell the client the lane is dead before the marker that says
        # WHY is on disk - see _lane_trip. Bounded, because a stuck disk must
        # not turn a refusal into a hang.
        _LANE_MARKER_WRITTEN.wait(timeout=30)
        breach = _LANE_ABORT or {}
        payload = json.dumps({"error": {"message": "proxy: lane aborted (%s)"
                                        % breach.get("reason", "UNKNOWN")}}).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _budget_refusal(self):
        payload = b'{"error":{"message":"proxy: paid budget unavailable"}}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _relay_headers(self, resp, status, extra=()):
        self.send_response(status)
        for k, v in resp.headers.items():
            if k.lower() in _HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        for k, v in extra:
            self.send_header(k, v)

    def _pump_whole(self, resp, status, state):
        payload = resp.read()
        if BODY_DIR:
            state["res_chunks"].append(payload)
        # The body is read exactly once and then relayed verbatim, so recording
        # the reason cannot consume the client's copy — a proxy that logs why and
        # hands the client an empty 400 has moved the blindness, not removed it.
        if status and status >= 400:
            state["error"] = _err_text(payload)
        try:
            parsed = json.loads(payload.decode("utf-8", "replace"))
            state["response_valid"] = isinstance(parsed, dict)
            _scan_obj(parsed, state)
        except Exception:
            pass
        self._relay_headers(resp, status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _iter_with_deadline(self, resp, state, sock):
        """Yield stream lines, giving up if no CONTENT arrives in time.

        A usage-only chunk is an event and carries nothing, so the deadline is
        keyed on content, not on events. Once one real delta lands the socket is
        put back on the long read timeout: a call that has started answering is
        alive and may take as long as it takes.
        """
        it = iter(resp)
        while True:
            # TWO CLOCKS, because one is not enough. settimeout() bounds a
            # SINGLE read, so an upstream that drips a usage-only keepalive
            # faster than the deadline resets it forever: MEASURED at 8.06 s
            # against a 0.8 s deadline, zero content, no error recorded. The
            # elapsed check below is the budget; the socket timeout is what
            # catches the stream that says nothing at all.
            if (UPSTREAM_FIRST_TOKEN_MS and not state["content_events"]
                    and state["stream_started_at"] is not None
                    and (time.time() - state["stream_started_at"]) * 1000
                    > UPSTREAM_FIRST_TOKEN_MS):
                state["error"] = "first_token_deadline_exceeded"
                return
            try:
                raw = next(it)
            except StopIteration:
                return
            except socket.timeout:
                if state["content_events"]:
                    raise
                state["error"] = "first_token_deadline_exceeded"
                return
            except OSError as exc:                 # ssl/socket timeouts subclass it
                if not state["content_events"] and "timed out" in str(exc).lower():
                    state["error"] = "first_token_deadline_exceeded"
                    return
                raise
            yield raw
            if (sock is not None and state["content_events"]
                    and sock.gettimeout() != UPSTREAM_READ_TIMEOUT):
                sock.settimeout(UPSTREAM_READ_TIMEOUT)

    def _pump_stream(self, resp, state):
        # No Content-Length is known up front and chunked framing is one more
        # thing to get wrong, so the response ends at connection close.
        self._relay_headers(resp, 200, extra=[("Connection", "close")])
        self.end_headers()
        self.close_connection = True
        state["stream_started_at"] = time.time()
        sock = _stream_socket(resp) if UPSTREAM_FIRST_TOKEN_MS else None
        if UPSTREAM_FIRST_TOKEN_MS and sock is None:
            # Never pretend to be armed. Recorded in its own field, NOT in
            # `error`: `_scan_obj` only fills `error` when it is empty, so
            # putting a diagnostic there would mask a real rate_limit_error
            # spliced into the 200 body — the two fixes cancelling out.
            state["deadline_unenforceable"] = True
        if sock is not None:
            sock.settimeout(UPSTREAM_FIRST_TOKEN_MS / 1000.0)
        for raw in self._iter_with_deadline(resp, state, sock):
            state["stream_bytes"] += len(raw)
            if BODY_DIR:
                # Appended before the relay write so a client that hangs up
                # cannot cost us the chunk we already read off the wire. Raw,
                # unparsed: the concatenation IS the stream as it arrived.
                state["res_chunks"].append(raw)
            self.wfile.write(raw)
            self.wfile.flush()
            line = raw.strip()
            if line.startswith(b"data:"):
                chunk = line[5:].strip()
                if chunk == b"[DONE]":
                    state["stream_complete"] = True
                elif chunk:
                    state["stream_events"] += 1
                    try:
                        _scan_obj(json.loads(chunk.decode("utf-8", "replace")), state)
                    except Exception:
                        state["stream_parse_errors"] += 1
                        # Do not persist arbitrary model text or corpus lines.
                        # Keep only a gateway status embedded by a broken stream.
                        match = re.search(br"HTTP/\d(?:\.\d)?\s+(\d{3})", chunk)
                        state["error"] = ("malformed_sse_embedded_http_status:%s" %
                                          match.group(1).decode("ascii") if match else
                                          "malformed_sse_json")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Proxy)
    srv.daemon_threads = True
    sys.stderr.write("upstream-log-proxy: 127.0.0.1:%d -> %s (log %s)\n"
                     % (LISTEN_PORT, UPSTREAM_BASE, UPSTREAM_LOG))
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
