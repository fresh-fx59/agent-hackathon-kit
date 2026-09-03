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
never logs the Authorization header (and when it OWNS that header, via
UPSTREAM_API_KEY_FILE, it never logs the key it read either). If it cannot parse a response it records
`returned_model: null` rather than guessing — an unmeasured value is null, never
a default. → measure/probes/upstream-split.sh

Set UPSTREAM_BODY_DIR to also keep every request and response body on disk, so a
finished run can be replayed instead of inferred. See the REPLAYABLE TRACES note
below for the layout and for what those files contain.
"""
import datetime
import errno
import hashlib
import math
import fcntl
import gzip
import hmac
import json
import os
import socket
import re
import stat
import subprocess
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
from lane_guard import (CACHE_JUDGEMENT_TERMS, DEFAULT_CACHE_MIN_CALLS,
                        DEFAULT_CACHE_MIN_RATE, cache_cost_fact,
                        cache_judgement, cache_terms, cache_tokens,
                        GENERATION_WINDOW_EXCEEDED,
                        COMPACTION_SUMMARY_RESERVE,
                        compaction_request_class, completion_clipped,
                        deterministic_refusal, last_message_text, model_family,
                        note_cache_call, same_family)

UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://linkapi.ai/v1").rstrip("/")
UPSTREAM_LOG = os.environ.get("UPSTREAM_LOG", "upstream.jsonl")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8791"))
RUN_TAG = os.environ.get("RUN_TAG", "")
RUN_ATTEMPT = os.environ.get("RUN_ATTEMPT", "")
RUN_ATTEMPT_FILE = os.environ.get("RUN_ATTEMPT_FILE", "")
UPSTREAM_INFLIGHT = os.environ.get("UPSTREAM_INFLIGHT", "")
UPSTREAM_BUDGET_STATE = os.environ.get("UPSTREAM_BUDGET_STATE", "")
UPSTREAM_ACTION_BUDGET = os.environ.get("UPSTREAM_ACTION_BUDGET", "").strip()
UPSTREAM_RATE_SNAPSHOT = os.environ.get("UPSTREAM_RATE_SNAPSHOT", "").strip()
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

# ── THE PRE-SEND TOKEN GATE ────────────────────────────────────────────────
# THE ONLY TRUE WALL ON THIS LANE, and it has to live here because nothing in
# the client is one. Read out of the installed qwen-code 0.22.0 bundle:
# `hard = W - 23,000` does NOT block a send — `shouldForceFromHard` is
# `!exactRoute && isHardTier && hardRescueFailureCount < 3`, so once three
# hard-tier rescues have failed the code logs «hard-tier rescue skipped … relying
# on reactive overflow recovery», sets the compression info to NOOP, and
# `shouldStopAfterHardRescue(false, …)` returns false: the oversized prompt goes
# out. Run r6 put 334,339 tokens on the wire that way against a 262,000 ceiling.
# `model.sessionTokenLimit` is exact but compares the PREVIOUS response's count,
# so it cannot stop the turn that balloons. The proxy is the last thing that sees
# a request before it leaves the box.
#
# 0 declares no ceiling and judges nothing, so every existing lane is untouched.
UPSTREAM_PER_REQUEST_TOKEN_GATE = int(
    os.environ.get("UPSTREAM_PER_REQUEST_TOKEN_GATE", "0") or 0)
# RECALIBRATED 2026-08-28 AGAINST A COMPLETE PAID RUN, because the previous
# calibration measured the WRONG RATIO and the wall leaked.
#
# The estimator divides CHARACTERS (`body.decode("utf-8", "replace")`), but 3.40
# was derived from `request_bytes / usage.prompt_tokens`. On this Cyrillic-heavy
# corpus bytes run ~8 % above characters, so a byte-calibrated divisor is far too
# high for a character-fed estimator. Result, replaying the complete ledger of run
# 20260827T173511Z-v41 (341 rows, 337 ANSWERED calls — every answered call, not a
# sample):
#
#   under-estimates at 3.40 ............ 42 of 337 (12.5 %)
#   worst deficit ...................... 6,434 tokens (est 219,480, actual 225,914)
#   implied TRUE chars/token ........... min 3.2898  p1 3.3019  median 3.5480  max 5.2974
#   (byte ratio, for contrast ......... min 3.4868  median 3.7932 — a different number)
#
# The run stayed under its 262,000 gate only because its peak prompt+budget was
# 236,678: luck, not the wall.
#
# THE SHAPE OF THE ANSWER IS STILL A SINGLE DIVISOR, deliberately. A per-script
# ratio was considered and rejected: the observed spread (3.29 → 5.30) is not
# explained by script alone — tool schemas are ASCII and tokenize DENSELY, so the
# cheap "Cyrillic is worse" split would have to be calibrated per mixture and would
# add a second thing to get wrong. Counting bytes instead of characters was also
# rejected: it is the ratio that just failed, and it makes the divisor depend on the
# encoding rather than on the text. One number, below the measured floor, is the
# shape a wall wants.
#
# THE MARGIN. 3.10 = 3.2898 / 1.061, i.e. 5.8 % below the observed minimum. The
# margin is sized so a corpus a little denser than anything measured is still
# over-estimated, and it is bounded on the other side by usefulness: replayed over
# the same 337 calls at 3.10 the peak `estimate + max_tokens` is 252,039, still
# under the 262,000 gate, so this divisor would NOT have spuriously refused a
# single call of the run it was calibrated on. Cost: the median over-estimate rises
# to 14.5 %, and an over-estimate is RECOVERABLE (the refusal is shaped so qwen
# compacts and retries — see pre_send_refusal_text) while a breach is not.
#
# WHAT COULD STILL DEFEAT IT: any input that tokenizes denser than 3.10 characters
# per token — long runs of emoji, CJK, base64/hex blobs, or a provider swapping
# tokenizers. A ratio is a model of somebody else's tokenizer and can never be
# proven safe for unseen text; every call row records `estimated_prompt_tokens`
# beside the provider's own count, so the next run re-derives this number instead of
# arguing about it (measure/tests/test_conservative_estimator_v42.py replays it).
#
# The estimate divides the WHOLE serialised request — messages AND tool schemas,
# which were 113,061 of the v40 peak request's characters — so it cannot miss a
# third of the prompt the way a messages-only estimate would.
UPSTREAM_CHARS_PER_TOKEN_CALIBRATED = 3.10
# The observed floor the calibration is anchored to. An operator may make the
# estimate MORE conservative (a smaller divisor); a LARGER one is an unsafe knob,
# so it is clamped, loudly, rather than honoured — a wall an env var can open is
# not a wall.
UPSTREAM_CHARS_PER_TOKEN_OBSERVED_MIN = 3.2898


def _calibrated_chars_per_token(raw, warn=sys.stderr):
    """Clamp the divisor to the calibrated ceiling. Louder than it is clever."""
    try:
        value = float(raw) if str(raw).strip() else UPSTREAM_CHARS_PER_TOKEN_CALIBRATED
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        value = UPSTREAM_CHARS_PER_TOKEN_CALIBRATED
    if value > UPSTREAM_CHARS_PER_TOKEN_CALIBRATED:
        print("upstream-log-proxy: REFUSING UPSTREAM_CHARS_PER_TOKEN=%s — above the "
              "calibrated safe ceiling %.2f (observed minimum %.4f over 337 answered "
              "calls of run 20260827T173511Z-v41); clamped to %.2f."
              % (raw, UPSTREAM_CHARS_PER_TOKEN_CALIBRATED,
                 UPSTREAM_CHARS_PER_TOKEN_OBSERVED_MIN,
                 UPSTREAM_CHARS_PER_TOKEN_CALIBRATED),
              file=warn, flush=True)
        value = UPSTREAM_CHARS_PER_TOKEN_CALIBRATED
    return value


UPSTREAM_CHARS_PER_TOKEN = _calibrated_chars_per_token(
    os.environ.get("UPSTREAM_CHARS_PER_TOKEN", "3.10"))
# RIDE OUT A PROVIDER BURST. linkapi's 400s are transient and minute-scale —
# measured 2026-08-02, and NOT explained by request size or shape (both
# controlled for, interleaved, 12/12 succeeded at the size and shape that had
# just failed). The problem is that qwen-code's own retry budget is SHORTER than
# a burst: D11 took 4 × 400 at 143 KB then a 200, then 5 × 400 at 171 KB and the
# run was over — 98,515 tokens billed, no row. This proxy is the only thing in
# the path that can wait longer than the client will.
# Off by default (0) so the proxy stays a pass-through unless a runner asks.
UPSTREAM_RETRY_MAX = int(os.environ.get("UPSTREAM_RETRY_MAX", "0") or 0)
# THIS LANE'S MEASURED GENERATION WINDOW, in seconds. CloseRouter's is 90: a
# call that dies at or near it carrying the gateway's timeout chunk was cut by
# the PROVIDER'S clock, and retrying a deterministic clock cut is pure waste.
# Unset, 0 or -1 means this lane declares no window, and then nothing below is
# ever judged against one - the ledger keeps the exact shape it has today.
try:
    GENERATION_WINDOW_S = float(
        os.environ.get("UPSTREAM_GENERATION_WINDOW_S", "-1") or -1)
except ValueError:
    GENERATION_WINDOW_S = -1.0
UPSTREAM_RETRY_BASE_MS = int(os.environ.get("UPSTREAM_RETRY_BASE_MS", "2000") or 2000)
# RETRY THE SUBSTITUTED CALL - DO NOT KILL THE RUN, AND DO NOT TOLERATE IT.
#
# MEASURED 2026-08-26 on `[次]deepseek-v4-flash`, 51 paid calls: 50 answered as
# the flash family and ONE as `deepseek-v4-pro`. The lane guard did its job and
# aborted, which was the right call given the only two options it had. But at
# ~2 % per call a 180-call run has a ~97 % chance of meeting at least one
# substitution, so a strict per-call abort means the run can NEVER finish, and
# ~50 good calls are thrown away every time.
#
# The answer is NOT a tolerance threshold. Tolerating a wrong-model response
# means the report is partly built on a model we did not request - a validity
# compromise, and a cap that merely moves the cliff. The answer is that a
# wrong-model response must never reach the client at all: DISCARD it and
# re-issue the same request upstream. The substitution is random, so a retry
# lands on the right model ~98 % of the time. Cost: one wasted call. Validity:
# untouched, because no wrong-model byte is ever relayed.
#
# BOUNDED AND FAIL-CLOSED. After this many discards on ONE client request the
# proxy stops retrying, trips the lane exactly as before, and REFUSES the
# client instead of handing back the wrong-model body - strictly stronger than
# the pre-retry behaviour, which relayed the offending body and then tripped.
#
# Off by default (0) so the proxy stays a pass-through unless a runner asks;
# measure/upstream-lane.sh turns it on for every lane it starts. It is
# deliberately NOT wired to UPSTREAM_RETRY_MAX / SHERLOCK_UPSTREAM_RETRY: those
# govern retrying a provider ERROR (a 400 burst) and the paid runners set them
# to 0 on purpose. This is a different failure and gets its own switch, so
# `SHERLOCK_UPSTREAM_RETRY=0` cannot silently disable it.
UPSTREAM_SUBSTITUTION_RETRY_MAX = int(
    os.environ.get("UPSTREAM_SUBSTITUTION_RETRY_MAX", "0") or 0)
# THE UPSTREAM CREDENTIAL IS THE PROXY'S, NOT THE CLIENT'S.
#
# Until now the key was pinned into the qwen child's environment at launch
# (run-bench.sh: OPENAI_API_KEY="$SHERLOCK_API_KEY") and this proxy relayed
# whatever Authorization header arrived. Changing keys therefore meant killing
# the run. That is not an academic cost: a key that routes on new-api's `auto`
# group was measured returning a DIFFERENT model than requested on 6/20 calls,
# where a single-group key was 20/20 clean at the same minute — and every
# wrong-model answer is a discarded retry at a flat 0.05 CNY.
#
# With UPSTREAM_API_KEY_FILE set, the proxy owns the credential: it reads the
# file itself and REPLACES the inbound Authorization header. A swap is then an
# atomic file write, effective on the very next request, no restart.
#
# UNSET = EXACTLY TODAY'S BEHAVIOUR. The client's header is forwarded verbatim,
# so every existing test and every other lane is untouched. The feature only
# exists when a runner names a file; measure/upstream-lane.sh does.
UPSTREAM_API_KEY_FILE = os.environ.get("UPSTREAM_API_KEY_FILE", "").strip()
# A credential is short. This cap is not a tuning knob, it is the torn-read
# guard: anything larger is not a key and must not be sent as one.
_MAX_KEY_BYTES = 4096
# THE UPSTREAM ROUTE IS A FILE TOO, FOR THE SAME REASON THE KEY IS.
#
# UPSTREAM_BASE, UPSTREAM_MODEL and UPSTREAM_EXPECTED_RETURNED_IDENTITY are read
# ONCE, at import. Changing the provider or the model therefore meant killing the
# proxy, which means killing the run: 2h42m and ~14 CNY, measured three times.
# And it is not a hypothetical need - the three paid v38 runs failed on the
# PROVIDER, not the harness, and the fix (CloseRouter, 1/27th the cost) is a
# different base URL and a different model id. Being unable to change route
# without a restart is what turned a provider outage into a lost run.
#
# With UPSTREAM_ROUTE_FILE set, the proxy reads a small JSON object on EVERY
# relayed call and uses it for that call:
#
#   {"schema": 1,
#    "base": "https://api.closerouter.dev/v1",
#    "model": "deepseek/deepseek-v4-flash-0731",
#    "expected_returned_identity": "deepseek/deepseek-v4-flash-0731",
#    "key_file": "/abs/path/upstream.key",   # optional, overrides the env one
#    "generation": 3}                        # monotonic, writer-set
#
# BASE, MODEL AND EXPECTED IDENTITY MOVE TOGETHER, ALWAYS. This is the whole
# reason the route is one file and not three env vars made hot-swappable one at
# a time. `model_family` keeps the vendor prefix, so
# same_family('deepseek/deepseek-v4-flash-0731', 'deepseek-v4-flash-0731') is
# FALSE (measured 2026-08-26): a CloseRouter base with a linkapi identity trips
# the lane guard on the very first call. A route in which those three can be
# observed out of step is not a smaller bug than a restart, it is a worse one.
# Hence Route is captured ONCE per upstream call and threaded through the relay,
# the substitution judgement and the ledger row.
#
# UNSET = EXACTLY TODAY'S BEHAVIOUR, byte for byte: the module-level env values
# govern and no route field is written to the ledger. A pass-through unless a
# runner asks, exactly like UPSTREAM_SUBSTITUTION_RETRY_MAX.
UPSTREAM_ROUTE_FILE = os.environ.get("UPSTREAM_ROUTE_FILE", "").strip()
# A route is a handful of short strings. Same reasoning as _MAX_KEY_BYTES: this
# is the torn-read guard, not a tuning knob.
_MAX_ROUTE_BYTES = 4096
# In action-budget mode a model rewrite larger than this is refused after the
# pre-route reservation and before a credential/socket.  It gives the reserve a
# finite proof while preserving the route file's broader non-budget use.
_ACTION_ROUTE_MODEL_OVERHEAD_BYTES = 256
# STREAMING IS THE ONLY CASE THAT MATTERS HERE. Every one of the 51 rows on the
# v38 run - the substituted one included - is `"stream": true`, so a fix that
# only handled whole JSON bodies would be a no-op on the real lane.
#
# A stream can be retried only while NOTHING has been written to the client, so
# the proxy holds the head of the stream back until it learns which model is
# answering. That is cheap: the id arrives in the first `data:` chunk and the
# measured ttft on that run is 0 ms.
#
# If the model is STILL unknown after this much held data or this much time the
# head is released anyway - a proxy that buffers a whole answer is a proxy that
# has broken streaming. A substitution discovered after that release cannot be
# retried (bytes are on the wire), so it falls through to the existing lane
# abort. That is the honest fallback; it is never a silent pass-through.
UPSTREAM_SUBSTITUTION_HOLD_BYTES = int(
    os.environ.get("UPSTREAM_SUBSTITUTION_HOLD_BYTES") or 262144)
UPSTREAM_SUBSTITUTION_HOLD_MS = int(
    os.environ.get("UPSTREAM_SUBSTITUTION_HOLD_MS") or 20000)
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
ACTION_LIMITS = ("max_provider_calls", "max_prompt_tokens", "max_completion_tokens",
                 "max_wall_time_s", "max_estimated_cost_rub")
_ACTION_BUDGET_ENABLED = bool(UPSTREAM_ACTION_BUDGET)
# Schema-1 is the pre-existing controller guard.  Schema-2 is deliberately
# opt-in: a normal lane must not silently acquire a price input it never
# declared, and a probe must not accidentally use the byte-only guard.
_BUDGET_ENABLED = bool(UPSTREAM_BUDGET_STATE) and not _ACTION_BUDGET_ENABLED
if _BUDGET_ENABLED and (not RUN_TAG or
                        not (UPSTREAM_EXPECTED_RETURNED_IDENTITY or UPSTREAM_ROUTE_FILE) or
                        any(value is None for value in _BUDGET_LIMITS.values())):
    raise ValueError("controlled proxy requires run identity and all finite limits")
# Only statuses that are transient on this lane. A 401/404/422 is a real defect
# in the request and retrying it just burns the context again for nothing.
_RETRYABLE = {400, 408, 429, 500, 502, 503, 504}

# The base path (e.g. "/v1") is per-ROUTE now, not per-process: see
# Route.base_path. Nothing may compute it from the module global any more, or a
# swapped base would keep the old prefix.
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
    now = time.time()
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    # MILLISECONDS, BECAUSE ONE SECOND WAS NOT ENOUGH. On run
    # 20260831T214240Z-v43 a qwen auto-compaction at 22:51:31Z and a driver
    # batch_boundary at 22:51:32Z had to be separated by decoding a gzipped
    # request body and looking for a <state_snapshot> marker. Two integers
    # would have answered it.
    row["ts_ms"] = int(now * 1000)
    # THE DISCRIMINATOR. `pre_send_refused` rows carry no `usage` and no
    # `body_*` keys, so a reader that assumes one schema crashes on them and
    # the four context-overflow refusals that ended a run stay invisible.
    # Refusal rows are the ones that carry an `event`; everything else is a
    # completed call.
    row.setdefault("kind", "refusal" if row.get("event") else "call")
    if RUN_TAG:
        row["run_tag"] = RUN_TAG
    line = json.dumps(row, ensure_ascii=False)
    with _LOG_LOCK:                       # ThreadingHTTPServer ⇒ concurrent turns
        with open(UPSTREAM_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            # Completion accounting is allowed only after this append survives
            # the crash window.  JSONL is the action-mode completion journal.
            # Schema 1 predates this durable completion journal.  Keep its
            # writer's latency/I/O contract byte-for-byte; schema 2 alone
            # makes a ledger row an accounting fact and therefore fsyncs it.
            if _ACTION_BUDGET_ENABLED:
                fh.flush()
                os.fsync(fh.fileno())


def estimate_prompt_tokens(body):
    """A DELIBERATELY CONSERVATIVE estimate of what the provider will count.

    The whole serialised request is divided by the calibrated chars-per-token —
    messages, tool schemas, everything — because the provider counts the whole
    prompt and a gate that under-counts is not a wall. Returns 0 on an
    unparseable body, so a malformed request is never refused on a number nobody
    can defend; the existing paths already judge those.

    CALIBRATED ON A COMPLETE POPULATION, NOT A SAMPLE: run 20260827T173511Z-v41,
    341 ledger rows, all 337 ANSWERED calls, comparing this function's output
    against the provider's own `usage.prompt_tokens`. The previous divisor (3.40,
    itself derived from BYTES while this function counts CHARACTERS) under-counted
    42 of those 337 calls, worst deficit 6,434 tokens. The implied true ratio
    bottoms out at 3.2898 chars/token; the divisor is 3.10, 5.8 % below that floor,
    and at 3.10 the same 337 calls yield ZERO under-estimates (minimum surplus
    5,825 tokens) while still peaking at 252,039 estimate+budget under a 262,000
    gate. Re-checkable: every row records `estimated_prompt_tokens` beside
    `usage.prompt_tokens`, and the distilled triples are replayed by
    measure/tests/test_conservative_estimator_v42.py against
    measure/tests/fixtures/v41-prompt-token-calibration.json.

    STILL DEFEATABLE by text that tokenizes denser than 3.10 chars/token — emoji,
    CJK, base64/hex blobs, or a provider tokenizer change. See the constant above.
    """
    if not body:
        return 0
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return 0
    ratio = (UPSTREAM_CHARS_PER_TOKEN if UPSTREAM_CHARS_PER_TOKEN > 0
             else UPSTREAM_CHARS_PER_TOKEN_CALIBRATED)
    return int(len(text) / ratio)


def pre_send_refusal_text(gate, estimated, declared):
    """The provider's own dialect, on purpose — see the module docstring above.

    qwen only compacts and retries when
    `getContextLengthExceededInfo(error).isExceeded`, which needs the text to
    match one of CONTEXT_LENGTH_PATTERNS and NONE of TIMEOUT_PATTERNS. And
    `parseTokenCounts` recovers the numbers from «maximum context length is N
    tokens … requested M tokens», feeding them to the reactive compression as
    limitTokens / actualTokens. A refusal the client cannot classify would end
    the run, which is worse than the breach it prevents — so the wording is a
    contract, not prose, and measure/tests/test_pre_send_token_gate.py pins it
    against the bundle's own regexes.
    """
    return ("This model's maximum context length is %d tokens, however you "
            "requested %d tokens (%d in the messages, %d for the completion). "
            "Please reduce the length of the messages. "
            "[proxy pre-send gate: context_length_exceeded]"
            % (gate, estimated + declared, estimated, declared))


class KeyUnavailable(Exception):
    """The key file is not usable. Carries a diagnosis that never holds a key.

    `transient` marks the faults that are a RACE with the writer rather than a
    verdict about the file — the swap landed between our lstat and our open, or
    between our open and our last read. Those are retried; everything else
    refuses immediately.
    """

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


def _read_api_key_once(path=None):
    """Return the upstream credential, read FRESH from the configured key file.

    `path` defaults to UPSTREAM_API_KEY_FILE; a route file may name its own key
    file, because a provider swap is also a credential swap and the two must
    never be observable out of step (see UPSTREAM_ROUTE_FILE).

    NO CACHING, DELIBERATELY. The alternative — cache plus an mtime check — is
    faster and wrong at the only moment that matters: `os.replace` can land
    inside the same mtime tick as the read that preceded it (st_mtime is
    coarse on many filesystems, and a swap is a single fast write), so a cache
    keyed on mtime can serve the OLD key after a swap and never notice. The
    cost of being unconditionally right is one open+read of a <4 KiB file per
    upstream call whose own latency is measured in seconds. That is not a
    trade; the cache buys nothing here.

    TORN READS. The writer's contract is `os.replace` (atomic rename), so a
    reader sees either the whole old file or the whole new one. This function
    does not TRUST that contract, because a careless writer using `>` would
    truncate in place: the file is opened once and the (dev, ino, size) triple
    is checked before, at open, and after the read, and a mismatch is a refusal
    rather than a short key. The format check below is the second net — a
    truncated key is still a plausible-looking string, so length alone cannot
    catch it, but a key that fails the shape check certainly is rejected.

    FAILS CLOSED. Every failure raises. There is no path from here that returns
    the client's header, an empty string, or a guess. Raising a diagnosis and
    refusing the request costs one failed call; sending a request with the
    wrong credential — or with the client's stale one — silently corrupts a
    paid measurement, which is the thing this whole lane exists to prevent.
    """
    path = UPSTREAM_API_KEY_FILE if path is None else path
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise KeyUnavailable("key file cannot be stat'ed (%s)" % exc.strerror,
                             transient=exc.errno == errno.ENOENT)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise KeyUnavailable("key file is not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise KeyUnavailable("key file cannot be opened (%s)" % exc.strerror,
                             transient=exc.errno == errno.ENOENT)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise KeyUnavailable("key file is not a regular file")
        if opened.st_size > _MAX_KEY_BYTES:
            raise KeyUnavailable("key file is larger than %d bytes" % _MAX_KEY_BYTES)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise KeyUnavailable("key file was replaced while being opened", transient=True)
        chunks = []
        total = 0
        while total <= _MAX_KEY_BYTES:
            chunk = os.read(fd, min(65536, _MAX_KEY_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        finished = os.fstat(fd)
        if total > _MAX_KEY_BYTES:
            raise KeyUnavailable("key file is larger than %d bytes" % _MAX_KEY_BYTES)
        if (total != finished.st_size or
                (opened.st_dev, opened.st_ino, opened.st_size) !=
                (finished.st_dev, finished.st_ino, finished.st_size)):
            raise KeyUnavailable("key file changed size while being read", transient=True)
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise KeyUnavailable("key file is not valid UTF-8")
    key = text.strip()
    if not key:
        raise KeyUnavailable("key file is empty or only whitespace")
    # A credential is one token. Interior whitespace or a newline means we are
    # holding a fragment, a multi-line file, or something that is not a key —
    # and a header value containing CR/LF is a header-injection vector.
    if len(key.split()) != 1:
        raise KeyUnavailable("key file does not contain exactly one token")
    if not all(33 <= ord(c) <= 126 for c in key):
        raise KeyUnavailable("key file contains non-printable characters")
    return key


# A swap is one `os.replace`, so the window in which a reader can catch the
# file mid-substitution is microseconds wide — but this proxy reads the file on
# EVERY paid call, so "microseconds, sometimes" is a 503 on a live run, and a
# refused call is a burnt call. Losing the race is not a verdict about the
# file; it is a signal to look again. Bounded, because a file that keeps
# changing under us must still eventually fail closed rather than spin.
_KEY_READ_RETRIES = 5
_KEY_READ_RETRY_S = 0.005


def _read_api_key(path=None):
    """_read_api_key_once, retried past a racing writer. See KeyUnavailable."""
    last = None
    for attempt in range(_KEY_READ_RETRIES):
        try:
            # Called with NO argument when the module default applies, so a
            # test that monkeypatches a zero-arg _read_api_key_once still works.
            return _read_api_key_once() if path is None else _read_api_key_once(path)
        except KeyUnavailable as exc:
            if not exc.transient:
                raise
            last = exc
            if attempt + 1 < _KEY_READ_RETRIES:
                time.sleep(_KEY_READ_RETRY_S)
    raise last


class BudgetUnknown(Exception):
    pass


def _budget_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _valid_budget_timestamp(value):
    if type(value) is not str:
        return False
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _strict_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError("duplicate JSON key")
        row[key] = value
    return row


def _read_small_regular_json(path, maximum=65536):
    """Read an immutable, tiny control file without following a symlink."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError("not a regular file")
        fd = os.open(path, os.O_RDONLY | nofollow)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum or
                    (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise ValueError("control file changed")
            raw = os.read(fd, maximum + 1)
            after = os.fstat(fd)
            if len(raw) > maximum or (opened.st_dev, opened.st_ino, opened.st_size) != (
                    after.st_dev, after.st_ino, after.st_size):
                raise ValueError("control file changed")
        finally:
            os.close(fd)
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid control file") from exc


def _strict_action_budget(path):
    row = _read_small_regular_json(path)
    if type(row) is not dict or set(row) != {"schema", "run_tag", "limits"} or \
            row.get("schema") != 1 or type(row.get("run_tag")) is not str or \
            not row["run_tag"] or row["run_tag"] != RUN_TAG:
        raise ValueError("invalid action budget")
    limits = row["limits"]
    if type(limits) is not dict or set(limits) != set(ACTION_LIMITS):
        raise ValueError("invalid action limits")
    for name in ACTION_LIMITS:
        value = limits[name]
        if type(value) not in (int, float) or isinstance(value, bool) or \
                not math.isfinite(value) or value < 0:
            raise ValueError("invalid action limit")
    return {"schema": 1, "run_tag": RUN_TAG,
            "limits": {name: limits[name] for name in ACTION_LIMITS}}


def _strict_rate_snapshot(path):
    row = _read_small_regular_json(path)
    fields = {"schema", "run_tag", "effective_at", "source", "sha256",
              "prompt_rub_per_token", "completion_rub_per_token"}
    if type(row) is not dict or set(row) != fields or row.get("schema") != 1 or \
            type(row.get("run_tag")) is not str or row["run_tag"] != RUN_TAG:
        raise ValueError("invalid rate snapshot")
    if any(type(row.get(name)) is not str or not row[name].strip()
           for name in ("effective_at", "source", "sha256")) or \
            not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
        raise ValueError("invalid rate snapshot")
    try:
        effective = datetime.datetime.fromisoformat(
            row["effective_at"].replace("Z", "+00:00"))
        if effective.tzinfo is None:
            raise ValueError("timezone required")
        age = time.time() - effective.timestamp()
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError("invalid rate snapshot") from exc
    # A price sheet older than one day is a different price sheet.  A snapshot
    # noticeably in the future is equally not evidence available at dispatch.
    if age > 86400 or age < -300:
        raise ValueError("stale rate snapshot")
    canonical = {name: row[name] for name in fields if name != "sha256"}
    digest = hashlib.sha256(json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(row["sha256"], digest):
        raise ValueError("rate snapshot digest mismatch")
    for name in ("prompt_rub_per_token", "completion_rub_per_token"):
        value = row[name]
        if type(value) not in (int, float) or isinstance(value, bool) or \
                not math.isfinite(value) or value < 0:
            raise ValueError("invalid rate snapshot")
    return {name: row[name] for name in fields}


_ACTION_BUDGET = None
_RATE_SNAPSHOT = None
_ACTION_BUDGET_REASON = None
if _ACTION_BUDGET_ENABLED:
    try:
        if not UPSTREAM_BUDGET_STATE or not RUN_TAG:
            raise ValueError("action budget requires state and run tag")
        _ACTION_BUDGET = _strict_action_budget(UPSTREAM_ACTION_BUDGET)
    except ValueError:
        _ACTION_BUDGET_REASON = "ACTION_BUDGET_INVALID"
    try:
        _RATE_SNAPSHOT = _strict_rate_snapshot(UPSTREAM_RATE_SNAPSHOT)
    except ValueError:
        _RATE_SNAPSHOT = None
        if _ACTION_BUDGET_REASON is None:
            _ACTION_BUDGET_REASON = "RATE_SNAPSHOT_INVALID"


# ======================================================================
# THE ROUTE. One provider + model + expected identity, read fresh per call.
# ======================================================================
class RouteUnavailable(Exception):
    """The route file is not usable. Carries a diagnosis and a REASON CODE.

    `code` exists so the fail-closed cases are NAMED rather than described:
    a counter keyed on it is written into the ledger event and asserted by
    measure/tests/test_upstream_route_file.py, so no refusal reason can be
    computed, printed and never checked.

    `transient` marks the faults that are a RACE with the writer rather than a
    verdict about the file - exactly as in KeyUnavailable.
    """

    def __init__(self, message, code="ROUTE_UNUSABLE", transient=False):
        super().__init__(message)
        self.code = code
        self.transient = transient


class Route(object):
    """ONE upstream route, captured ONCE and used for ONE upstream call.

    Immutable on purpose. Rule 5 of the design: base, model and expected
    identity used for a single relay MUST come from the same read, so a swap
    landing mid-call can never make the proxy send model A to provider B and
    judge the answer against identity C. Everything downstream of `_relay`
    takes this object; nothing downstream reads the module globals.
    """

    __slots__ = ("base", "model", "expected_identity", "key_file",
                 "generation", "base_path", "source")

    def __init__(self, base, model, expected_identity, key_file, generation,
                 source):
        self.base = base
        self.model = model
        self.expected_identity = expected_identity
        self.key_file = key_file
        self.generation = generation
        self.base_path = urlsplit(base).path.rstrip("/")
        self.source = source                      # "env" | "file"

    def url_for(self, path):
        if self.base_path and path.startswith(self.base_path):
            path = path[len(self.base_path):]
        return self.base + (path if path.startswith("/") else "/" + path)

    def ledger_fields(self):
        """The route columns for a ledger row.

        EMPTY when no route file is configured, deliberately: a run that does
        not use the feature keeps the exact ledger shape every previous run
        wrote, and lane_guard.audit_ledger falls back to its global --expected
        for those rows. Same discipline as `discarded_substitution`.
        """
        if self.source == "env":
            return {}
        return {"route_generation": self.generation,
                "route_base": self.base,
                "route_expected_identity": self.expected_identity}


# Named, so a provider that starts serving a broken route file shows up as a
# count per REASON rather than as a pile of 503s. Written into the
# `route_unavailable` ledger event and asserted by the tests.
_ROUTE_REFUSALS = {}
_ROUTE_LOCK = threading.Lock()


def _note_route_refusal(code):
    with _ROUTE_LOCK:
        _ROUTE_REFUSALS[code] = _ROUTE_REFUSALS.get(code, 0) + 1
        return dict(_ROUTE_REFUSALS)


def _env_route():
    """Today's behaviour, expressed as a Route. No file, no new failure mode."""
    return Route(UPSTREAM_BASE, UPSTREAM_MODEL,
                 UPSTREAM_EXPECTED_RETURNED_IDENTITY, UPSTREAM_API_KEY_FILE,
                 None, "env")


def _route_line(obj, key, required=True):
    """A single-line, non-empty string field. Anything else is a refusal."""
    if key not in obj:
        if not required:
            return ""
        raise RouteUnavailable("route file has no %r field" % key,
                               "ROUTE_FIELD_MISSING")
    value = obj[key]
    if not isinstance(value, str):
        raise RouteUnavailable("route field %r is not a string" % key,
                               "ROUTE_FIELD_NOT_STRING")
    value = value.strip()
    if not value:
        raise RouteUnavailable("route field %r is empty" % key,
                               "ROUTE_FIELD_BLANK")
    # A control character in a model id or a URL is a header/URL-injection
    # vector and is never part of a legitimate value.
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RouteUnavailable(
            "route field %r contains a control character" % key,
            "ROUTE_FIELD_NOT_ONE_LINE")
    return value


# A route file names a key FILE, never a key. Any field that looks like it
# carries a credential is a refusal, not a value to be quietly ignored: the
# route file is not 0600-by-contract the way the key file is, and a secret that
# lands in it would be a secret in the trace dir nobody meant to put there.
_ROUTE_CREDENTIAL_FIELDS = ("key", "api_key", "apikey", "token", "secret",
                            "authorization", "bearer", "password")


def _route_from_obj(obj):
    if not isinstance(obj, dict):
        raise RouteUnavailable("route file is not a JSON object",
                               "ROUTE_NOT_OBJECT")
    for field in _ROUTE_CREDENTIAL_FIELDS:
        if field in obj:
            raise RouteUnavailable(
                "route file carries a %r field - a route names a key_file "
                "PATH, never a credential" % field, "ROUTE_CARRIES_CREDENTIAL")
    schema = obj.get("schema")
    if type(schema) is not int or schema != 1:
        raise RouteUnavailable("route file schema is %r, not 1" % (schema,),
                               "ROUTE_SCHEMA_UNSUPPORTED")
    base = _route_line(obj, "base")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise RouteUnavailable(
            "route base is not an absolute http/https URL", "ROUTE_BASE_INVALID")
    base = base.rstrip("/")
    model = _route_line(obj, "model")
    expected = _route_line(obj, "expected_returned_identity")
    key_file = _route_line(obj, "key_file", required=False)
    if key_file and not key_file.startswith("/"):
        raise RouteUnavailable("route key_file is not an absolute path",
                               "ROUTE_KEY_FILE_NOT_ABSOLUTE")
    generation = obj.get("generation")
    if generation is None:
        generation = None
    elif type(generation) is not int or generation < 0:
        raise RouteUnavailable(
            "route generation %r is not a non-negative integer" % (generation,),
            "ROUTE_GENERATION_INVALID")
    return Route(base, model, expected, key_file, generation, "file")


def _read_route_once(path):
    """Return the Route in `path`, read FRESH. Same discipline as the key.

    NO CACHING, DELIBERATELY, and for the identical reason: `os.replace` can
    land inside the same st_mtime tick as the read that preceded it, so a cache
    keyed on mtime can serve the OLD route after a swap and never notice. The
    cost of being unconditionally right is one open+read of a <4 KiB file per
    upstream call whose own latency is measured in seconds. That is not a trade.

    TORN READS. The writer's contract is an atomic rename, and this function
    does not TRUST it: the (dev, ino, size) triple is checked before, at open,
    and after the read, and a mismatch is a refusal rather than a half route.
    The JSON parse is the second net - but only the second, because a truncated
    JSON object can still parse (`{"schema": 1}` is a prefix of every route
    file ever written) and would then fail as a MISSING FIELD, which is a
    refusal too.

    FAILS CLOSED, IN EVERY DIRECTION. There is no fallback to the env values
    and no fallback to the previously-seen route. A call sent to the wrong
    provider, or judged against the wrong expected identity, silently corrupts
    a paid measurement - which is the thing this whole lane exists to prevent.
    A refused call costs one call; a wrong one costs the run.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RouteUnavailable("route file cannot be stat'ed (%s)" % exc.strerror,
                               "ROUTE_FILE_UNSTATABLE",
                               transient=exc.errno == errno.ENOENT)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise RouteUnavailable("route file is not a regular file",
                               "ROUTE_NOT_REGULAR")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise RouteUnavailable("route file cannot be opened (%s)" % exc.strerror,
                               "ROUTE_FILE_UNREADABLE",
                               transient=exc.errno == errno.ENOENT)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise RouteUnavailable("route file is not a regular file",
                                   "ROUTE_NOT_REGULAR")
        if opened.st_size > _MAX_ROUTE_BYTES:
            raise RouteUnavailable(
                "route file is larger than %d bytes" % _MAX_ROUTE_BYTES,
                "ROUTE_OVERSIZED")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise RouteUnavailable("route file was replaced while being opened",
                                   "ROUTE_REPLACED_WHILE_OPENING", transient=True)
        chunks = []
        total = 0
        while total <= _MAX_ROUTE_BYTES:
            chunk = os.read(fd, min(65536, _MAX_ROUTE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        finished = os.fstat(fd)
        if total > _MAX_ROUTE_BYTES:
            raise RouteUnavailable(
                "route file is larger than %d bytes" % _MAX_ROUTE_BYTES,
                "ROUTE_OVERSIZED")
        if (total != finished.st_size or
                (opened.st_dev, opened.st_ino, opened.st_size) !=
                (finished.st_dev, finished.st_ino, finished.st_size)):
            raise RouteUnavailable("route file changed size while being read",
                                   "ROUTE_CHANGED_WHILE_READING", transient=True)
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RouteUnavailable("route file is not valid UTF-8", "ROUTE_NOT_UTF8")
    if not text.strip():
        raise RouteUnavailable("route file is empty", "ROUTE_EMPTY")
    try:
        obj = json.loads(text, object_pairs_hook=_strict_object)
    except ValueError as exc:
        raise RouteUnavailable("route file is not parseable JSON (%s)" % exc,
                               "ROUTE_UNPARSEABLE")
    return _route_from_obj(obj)


# Same bound and the same reasoning as _KEY_READ_RETRIES: a swap is one
# rename, so losing the race is a signal to look again, not a verdict - but a
# file that keeps changing under us must still eventually fail closed.
_ROUTE_READ_RETRIES = 5
_ROUTE_READ_RETRY_S = 0.005


def _read_route(path):
    last = None
    for attempt in range(_ROUTE_READ_RETRIES):
        try:
            return _read_route_once(path)
        except RouteUnavailable as exc:
            if not exc.transient:
                raise
            last = exc
            if attempt + 1 < _ROUTE_READ_RETRIES:
                time.sleep(_ROUTE_READ_RETRY_S)
    raise last


# THE ONE FUNNEL. Every route in this process comes from here, so a future
# in-process advance (exhausting the substitution cap and moving to the next
# provider) has exactly one place to change: it rewrites the file this reads,
# or it overrides `_ROUTE_SOURCE["file"]`. Nothing else in the module reads
# UPSTREAM_ROUTE_FILE.
_ROUTE_SOURCE = {"file": UPSTREAM_ROUTE_FILE}


def _current_route():
    path = _ROUTE_SOURCE["file"]
    if not path:
        return _env_route()
    return _read_route(path)


def _apply_route_model(body, route):
    """Rewrite the request's `model` to the route's, and report both ids.

    Byte-identical to the pre-route code when `route` is the env route: rewrite
    ONLY the model field, and only when the body parses as the object we expect.
    A proxy that reformats a request it did not fully understand is a proxy that
    corrupts one silently.
    """
    requested = sent = None
    out = body
    if route.model and body:
        try:
            obj = json.loads(body)
            if isinstance(obj, dict) and obj.get("model"):
                requested = obj["model"]
                obj["model"] = route.model
                sent = route.model
                out = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        except Exception:
            pass
    if requested is None:
        try:
            requested = (json.loads(body or b"{}") or {}).get("model")
        except Exception:
            pass
    return out, requested, sent


# ======================================================================
# ADVANCE THE ROUTE INSTEAD OF DYING. Exhausting the substitution cap must
# STOP BEING FATAL.
#
# MEASURED on the 463-row v38 ledger (20260826T132832Z-v38.upstream.jsonl):
# 183 of 463 answers (40 %) were the wrong model, and the per-call retry
# histogram is a clean geometric tail - 98 calls needed 1 retry, then 46, 19,
# 10, 2, 1, 1, 1, 1, 1, 1, 1, and ONE call needed 13 against a cap of 12. That
# single call ended a 2h42m run and 23.15 CNY of work with a 192-byte report,
# after 280 good calls had already landed.
#
# Raising the cap buys one order of magnitude and costs nothing until the
# provider drifts again: a lottery ticket, not a fix. When a provider will not
# serve the model we asked for, the answer is to CHANGE PROVIDER - on the live
# run, through the route file - not to kill two hours of work.
#
# WHAT IS NOT WEAKENED, ON ANY PATH:
#   * The wrong-model body is still never relayed. Not one byte, on any route.
#   * With NO fallback list the end state is today's, exactly: _lane_trip with
#     RETURNED_MODEL_FAMILY_MISMATCH, the client refused, and no new artifact.
#     The feature is opt-in per launch and writes nothing when it is off.
#   * Each route is tried at most once, and a route walked past is never
#     returned to: the index is monotonic for the whole run.
#   * UPSTREAM_ROUTE_ADVANCE_MAX caps advances FOR THE WHOLE RUN (default: the
#     length of the list), so a flapping provider cannot walk the list once per
#     call and quietly bill the run for every provider we own.
#
# AN INVISIBLE FAILOVER IS A LIE ABOUT THE MEASUREMENT. A report synthesised
# across two models is a different scientific object than one from a single
# model. So every advance is recorded twice - a `route_advance` event in the
# ledger AND a line in TRACE.upstream.route-advances.jsonl - every ledger row
# already carries its route_generation, and lane-audit.py prints a MULTI-ROUTE
# RUN line the operator cannot miss and refuses a history that does not match
# the ledger.
# ======================================================================
UPSTREAM_ROUTE_FALLBACKS = os.environ.get("UPSTREAM_ROUTE_FALLBACKS", "").strip()
# Where the advance history goes. Derived from the ledger by default, so the
# two artifacts are always named after the same trace.
UPSTREAM_ROUTE_ADVANCES = os.environ.get("UPSTREAM_ROUTE_ADVANCES", "").strip()
if not UPSTREAM_ROUTE_ADVANCES:
    _stem = UPSTREAM_LOG[:-6] if UPSTREAM_LOG.endswith(".jsonl") else UPSTREAM_LOG
    UPSTREAM_ROUTE_ADVANCES = _stem + ".route-advances.jsonl"
# A fallback list is a handful of routes. Same reasoning as _MAX_ROUTE_BYTES.
_MAX_FALLBACKS_BYTES = 64 * 1024
# ONE WRITER, NOT TWO. hack/swap-upstream-route.sh already does the atomic
# temp-file+rename, the 0600-before-rename, the printable-field refusal and the
# refusal to LOWER a generation. A second, in-process writer would be a weaker
# copy of all four, so the advance SHELLS OUT to the same script the operator
# uses by hand. Advances are rare (at most len(fallbacks) per run), so the
# subprocess costs nothing measurable.
_DEFAULT_ROUTE_SWAP = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, os.pardir, os.pardir, os.pardir, "hack",
    "swap-upstream-route.sh"))
UPSTREAM_ROUTE_SWAP = (os.environ.get("UPSTREAM_ROUTE_SWAP", "").strip()
                       or _DEFAULT_ROUTE_SWAP)


class FallbacksUnusable(ValueError):
    """The fallback list is not usable. Raised AT STARTUP, never mid-run.

    A fallback route we cannot parse is a route we would discover was broken
    at the exact moment the run needed it - twenty minutes and ~14 CNY in. So
    the whole list is validated with the SAME validator the live route file
    goes through, before the socket is even bound.
    """


def _load_fallbacks(path):
    """The ordered fallback routes, validated by `_route_from_obj` itself."""
    if not path:
        return []
    if not os.path.isabs(path):
        raise FallbacksUnusable(
            "UPSTREAM_ROUTE_FALLBACKS must be an absolute path: %r" % path)
    try:
        with open(path, "rb") as source:
            raw = source.read(_MAX_FALLBACKS_BYTES + 1)
    except OSError as exc:
        raise FallbacksUnusable("UPSTREAM_ROUTE_FALLBACKS %s could not be read: %s"
                                % (path, exc.strerror))
    if len(raw) > _MAX_FALLBACKS_BYTES:
        raise FallbacksUnusable("UPSTREAM_ROUTE_FALLBACKS is larger than %d bytes"
                                % _MAX_FALLBACKS_BYTES)
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FallbacksUnusable(
            "UPSTREAM_ROUTE_FALLBACKS is not parseable JSON (%s)" % exc)
    if not isinstance(obj, list):
        raise FallbacksUnusable(
            "UPSTREAM_ROUTE_FALLBACKS must be a JSON array of route objects")
    routes = []
    for index, item in enumerate(obj, 1):
        if not isinstance(item, dict):
            raise FallbacksUnusable("fallback %d is not a JSON object" % index)
        # THE GENERATION IS THE LIVE FILE'S, NOT THE LIST'S. It is the receipt
        # for which write is live and it only ever goes up; a list entry naming
        # one could only fight the writer. Refused rather than ignored - a
        # silently dropped field is a promise the file appears to make and does
        # not keep.
        if "generation" in item:
            raise FallbacksUnusable(
                "fallback %d names a generation (FALLBACK_CARRIES_GENERATION) - "
                "the generation belongs to the live route file and is set by "
                "the writer" % index)
        try:
            routes.append(_route_from_obj(item))
        except RouteUnavailable as exc:
            raise FallbacksUnusable("fallback %d is unusable: %s (%s)"
                                    % (index, exc, exc.code))
    return routes


_FALLBACKS = _load_fallbacks(UPSTREAM_ROUTE_FALLBACKS)
if _FALLBACKS and not UPSTREAM_ROUTE_FILE:
    # Nowhere to write the new route means no advance is possible, and a
    # feature that is configured but inert is worse than one that is off.
    raise FallbacksUnusable(
        "UPSTREAM_ROUTE_FALLBACKS is set but UPSTREAM_ROUTE_FILE is not - an "
        "advance is a write to the live route file, so there is nowhere to "
        "advance to")
_advance_max_raw = os.environ.get("UPSTREAM_ROUTE_ADVANCE_MAX", "").strip()
if _advance_max_raw:
    UPSTREAM_ROUTE_ADVANCE_MAX = int(_advance_max_raw)
    if UPSTREAM_ROUTE_ADVANCE_MAX < 0:
        raise ValueError("UPSTREAM_ROUTE_ADVANCE_MAX must not be negative")
else:
    UPSTREAM_ROUTE_ADVANCE_MAX = len(_FALLBACKS)

# NAMED COUNTERS, and every one of them reaches a verdict. `advances_attempted`
# minus `advances_performed` is the number of times the run wanted another
# provider and did not have one; `requests_refused` is what that cost the
# client. lane_guard.audit_ledger checks all five against the events on disk
# (ROUTE_ADVANCE_COUNTERS_INCONSISTENT), so none of them can be computed,
# printed and never checked - the defect every earlier PR in this series shipped.
_ADVANCE_COUNTERS = {"advances_attempted": 0, "advances_performed": 0,
                     "advances_blocked": 0, "routes_exhausted": 0,
                     "requests_refused": 0}
_ADVANCE_STATE = {"next": 0}
_ADVANCE_LOCK = threading.Lock()


def _advance_write(name, value):
    with _ADVANCE_LOCK:
        _ADVANCE_COUNTERS[name] += value
        return dict(_ADVANCE_COUNTERS)


def _route_summary(route, generation=None):
    return {"base": route.base, "model": route.model,
            "expected_identity": route.expected_identity,
            "generation": route.generation if generation is None else generation}


def _record_advance(row):
    """Twice, deliberately: the ledger AND the advance history.

    The ledger is what a reader of the run already opens, so the failover has
    to be visible there. The sidecar is what survives a ledger a later tool
    filters by `status`, and it is the file lane-audit.py checks against the
    ledger. Neither write may be the reason an advance does not happen, so both
    are best-effort - the counters and the trip decision do not depend on them.
    """
    try:
        record(returned_model=None, **row)
    except OSError:
        pass
    try:
        directory = os.path.dirname(os.path.abspath(UPSTREAM_ROUTE_ADVANCES))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with _LOG_LOCK:
            with open(UPSTREAM_ROUTE_ADVANCES, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    except OSError:
        pass


def _swap_route(target, route_file):
    """Write the next route with the ONE writer. Returns (ok, detail)."""
    command = [UPSTREAM_ROUTE_SWAP]
    if target.key_file:
        command += ["--key-file", target.key_file]
    # No --generation: the writer reads the LIVE generation and writes one past
    # it, which is the only definition that cannot go backwards under a
    # concurrent hand swap.
    command += [route_file, target.base, target.model, target.expected_identity]
    if not os.access(UPSTREAM_ROUTE_SWAP, os.X_OK):
        return False, "no executable writer at %s" % UPSTREAM_ROUTE_SWAP
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "writer failed: %s" % exc
    if done.returncode != 0:
        return False, ("writer exited %d: %s"
                       % (done.returncode,
                          done.stdout.decode("utf-8", "replace").strip()[:300]))
    return True, ""


def _advance_route(from_route, exhausted_attempts, request_id):
    """Move to the next provider/model/identity. True when the caller may retry.

    Called ONLY on substitution-retry exhaustion for one client request. On
    False the caller trips the lane exactly as it did before this feature
    existed - and when no fallback list is configured that is the first thing
    that happens, before a counter is touched or a byte is written, so the
    off state is byte-identical to today.
    """
    if not _FALLBACKS:
        return False
    counters = _advance_write("routes_exhausted", 1)
    counters = _advance_write("advances_attempted", 1)
    route_file = _ROUTE_SOURCE["file"]
    with _ADVANCE_LOCK:
        index = _ADVANCE_STATE["next"]
        performed = _ADVANCE_COUNTERS["advances_performed"]
        blocked = None
        if not route_file:
            blocked = "NO_ROUTE_FILE"
        elif index >= len(_FALLBACKS):
            # Checked BEFORE the cap: by default the cap IS the length of the
            # list, and "there is no next provider" is the primary fact. The cap
            # only has its own reason code when it is set lower than the list.
            blocked = "FALLBACK_LIST_EXHAUSTED"
        elif performed >= UPSTREAM_ROUTE_ADVANCE_MAX:
            blocked = "ADVANCE_CAP_REACHED"
        else:
            # Claimed inside the lock: two concurrent exhaustions must not both
            # take the same fallback, and a route walked past is never returned
            # to for the rest of the run.
            _ADVANCE_STATE["next"] = index + 1
        target = None if blocked else _FALLBACKS[index]
    event = {"event": "route_advance", "schema": 1, "request_id": request_id,
             "reason": "SUBSTITUTION_RETRY_EXHAUSTED",
             "exhausted_attempts": exhausted_attempts,
             "route_index": index, "from": _route_summary(from_route)}
    if blocked is None:
        ok, detail = _swap_route(target, route_file)
        if ok:
            # VERIFY THE WRITE BY READING IT BACK through the same funnel every
            # relayed call uses. A swap we cannot read is a swap that would
            # refuse every following call with a 503; better to trip here, with
            # the reason, than to hand the run a route nobody can read.
            try:
                landed = _current_route()
            except RouteUnavailable as exc:
                ok, detail = False, "route unreadable after the write: %s" % exc
        if not ok:
            blocked = "WRITER_FAILED"
            event["writer_detail"] = detail
        else:
            counters = _advance_write("advances_performed", 1)
            event["to"] = _route_summary(landed)
            event["counters"] = counters
            event["observed_at"] = _budget_timestamp()
            _record_advance(event)
            sys.stderr.write(
                "upstream-log-proxy: ROUTE ADVANCE #%d after %d discarded "
                "substitutions - %s (%s) -> %s (%s), generation %s\n"
                % (counters["advances_performed"], exhausted_attempts,
                   from_route.base, from_route.expected_identity,
                   landed.base, landed.expected_identity, landed.generation))
            sys.stderr.flush()
            return True
    # A BLOCKED ADVANCE IS A REFUSED CLIENT, always: the caller's very next act
    # is _lane_trip + _lane_refusal. Counted here so the two numbers cannot
    # drift apart, and so the event on disk already carries the cost.
    _advance_write("advances_blocked", 1)
    counters = _advance_write("requests_refused", 1)
    event["event"] = "route_advance_blocked"
    event["blocked_reason"] = blocked
    event["counters"] = counters
    event["observed_at"] = _budget_timestamp()
    _record_advance(event)
    sys.stderr.write("upstream-log-proxy: ROUTE ADVANCE BLOCKED %s - no route "
                     "left to serve %s, tripping the lane\n"
                     % (blocked, from_route.expected_identity))
    sys.stderr.flush()
    return False



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
    if _ACTION_BUDGET_ENABLED:
        counters = ("provider_calls", "prompt_tokens", "completion_tokens",
                    "wall_time_s", "estimated_cost_rub")
        observed = ("provider_calls", "prompt_tokens", "completion_tokens")
        fields = {"schema", "run_tag", "updated_at", "limits", "rate_snapshot",
                  "budget_assurance", "projected", "observed", "completed_overshoot",
                  "observed_usage_unknown", "completed_attempt_ids", "verdict", "reason"}
        return (isinstance(row, dict) and set(row) == fields and row.get("schema") == 2 and
                row.get("run_tag") == RUN_TAG and row.get("limits") ==
                (_ACTION_BUDGET or {"limits": {name: 1 for name in ACTION_LIMITS}})["limits"] and
                row.get("rate_snapshot") == _RATE_SNAPSHOT and
                row.get("budget_assurance") == "client_pre_dispatch" and
                _valid_budget_timestamp(row.get("updated_at")) and
                all(type(row.get(group)) is dict for group in
                    ("projected", "observed", "completed_overshoot")) and
                set(row["projected"]) == set(counters) and
                set(row["observed"]) == set(observed) and
                set(row["completed_overshoot"]) == set(observed) and
                all(type(row["projected"][name]) in (int, float) and
                    not isinstance(row["projected"][name], bool) and
                    math.isfinite(row["projected"][name]) and row["projected"][name] >= 0
                    for name in counters) and
                all(type(row["projected"][name]) is int and
                    not isinstance(row["projected"][name], bool)
                    for name in ("provider_calls", "prompt_tokens", "completion_tokens")) and
                all(type(row[group][name]) is int and row[group][name] >= 0
                    for group in ("observed", "completed_overshoot") for name in observed) and
                type(row.get("observed_usage_unknown")) is int and
                row["observed_usage_unknown"] >= 0 and
                type(row.get("completed_attempt_ids")) is list and
                all(type(value) is str and re.fullmatch(r"[0-9a-f]{32}\.a[1-9][0-9]*", value)
                    for value in row["completed_attempt_ids"]) and
                len(set(row["completed_attempt_ids"])) == len(row["completed_attempt_ids"]) and
                row["observed"]["provider_calls"] == len(row["completed_attempt_ids"]) and
                row["projected"]["provider_calls"] >= row["observed"]["provider_calls"] and
                row["observed_usage_unknown"] <= row["observed"]["provider_calls"] and
                all(row["completed_overshoot"][name] == max(
                    row["observed"][name] - row["limits"]["max_" + name], 0)
                    for name in observed) and
                row.get("verdict") in ("WITHIN", "EXCEEDED") and
                (row.get("reason") is None or
                 (isinstance(row.get("reason"), str) and _REASON_CODE.fullmatch(row["reason"])) ))
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


def _action_initial_budget():
    return {"schema": 2, "run_tag": RUN_TAG, "updated_at": _budget_timestamp(),
            "limits": ((_ACTION_BUDGET or {}).get("limits") or
                       {name: 1 for name in ACTION_LIMITS}),
            "rate_snapshot": _RATE_SNAPSHOT,
            "budget_assurance": "client_pre_dispatch",
            "projected": {"provider_calls": 0, "prompt_tokens": 0,
                          "completion_tokens": 0, "wall_time_s": 0.0,
                          "estimated_cost_rub": 0.0},
            "observed": {"provider_calls": 0, "prompt_tokens": 0,
                         "completion_tokens": 0},
            "observed_usage_unknown": 0,
            "completed_attempt_ids": [],
            "completed_overshoot": {"provider_calls": 0, "prompt_tokens": 0,
                                    "completion_tokens": 0},
            "verdict": "WITHIN", "reason": None}


def _initialize_action_budget():
    """Create schema-2 once; an existing damaged state is unknown, never zero."""
    if not _ACTION_BUDGET_ENABLED:
        return
    lock_path = UPSTREAM_BUDGET_STATE + ".lock"
    directory = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(directory, exist_ok=True)
    with _BUDGET_LOCK, open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                _read_budget()
            except BudgetUnknown:
                if os.path.exists(UPSTREAM_BUDGET_STATE):
                    return
                _write_budget(_action_initial_budget())
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _action_reservation_path():
    # This is intentionally a separate append-only journal.  State is a
    # convenient cache of the projected totals, but a crash after admission or
    # a plausible state rollback must never make an accepted envelope vanish.
    return UPSTREAM_BUDGET_STATE + ".reservations.jsonl"


def _valid_action_estimate(estimate):
    return (type(estimate) is dict and set(estimate) == {
            "provider_calls", "prompt_tokens", "completion_tokens", "wall_time_s",
            "estimated_cost_rub"} and
            all(type(estimate[name]) is int and estimate[name] >= 0 for name in
                ("provider_calls", "prompt_tokens", "completion_tokens")) and
            all(type(estimate[name]) in (int, float) and not isinstance(estimate[name], bool)
                and math.isfinite(estimate[name]) and estimate[name] >= 0 for name in
                ("wall_time_s", "estimated_cost_rub")))


def _append_action_reservation(reservation_id, estimate):
    """Durably append an accepted envelope before state or route/key access."""
    if (type(reservation_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", reservation_id)
            or not _valid_action_estimate(estimate)):
        raise BudgetUnknown()
    path = _action_reservation_path()
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    row = {"schema": 1, "run_tag": RUN_TAG, "action_reservation_id": reservation_id,
           "action_estimate": estimate}
    try:
        with open(path, "a", encoding="utf-8") as target:
            target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            target.flush()
            os.fsync(target.fileno())
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise BudgetUnknown() from exc


def _reconcile_action_reservations(row):
    """Rebuild every projected dimension from the durable admission journal."""
    totals = {"provider_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "wall_time_s": 0.0, "estimated_cost_rub": 0.0}
    seen = set()
    try:
        with open(_action_reservation_path(), encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                entry = json.loads(line, object_pairs_hook=_strict_object)
                if not isinstance(entry, dict) or set(entry) != {
                        "schema", "run_tag", "action_reservation_id", "action_estimate"} or \
                        entry.get("schema") != 1 or entry.get("run_tag") != RUN_TAG or \
                        type(entry.get("action_reservation_id")) is not str or \
                        not re.fullmatch(r"[0-9a-f]{32}", entry["action_reservation_id"]) or \
                        entry["action_reservation_id"] in seen or \
                        not _valid_action_estimate(entry.get("action_estimate")):
                    raise BudgetUnknown()
                seen.add(entry["action_reservation_id"])
                for name, value in entry["action_estimate"].items():
                    totals[name] += value
    except FileNotFoundError:
        if any(row["projected"].values()):
            raise BudgetUnknown()
        return row
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise BudgetUnknown() from exc
    # The append is ordered before state replacement.  Thus both a crash in
    # that window and a post-run state edit converge to the journal total; no
    # projected cap can be reopened by changing just the mutable state file.
    row["projected"] = totals
    return row


def _reconcile_completed(row):
    """Keep durable reservations at least as large as completed paid rows."""
    if _ACTION_BUDGET_ENABLED:
        # The fsync'd JSONL rows are a journal, not an optional diagnostic.  A
        # crash after row fsync but before state replacement is reconciled here;
        # IDs make replay idempotent.  A malformed journal is unknown, never a
        # reason to reopen a paid cap.
        row = _reconcile_action_reservations(row)
        try:
            with _LOG_LOCK, open(UPSTREAM_LOG, encoding="utf-8") as source:
                for line in source:
                    if not line.strip():
                        continue
                    completed = json.loads(line, object_pairs_hook=_strict_object)
                    if not isinstance(completed, dict):
                        raise BudgetUnknown()
                    if completed.get("run_tag") != RUN_TAG:
                        continue
                    attempt_id = completed.get("action_attempt_id")
                    if attempt_id is None:
                        continue
                    if completed.get("action_contact_completed") is not True or \
                            type(attempt_id) is not str or not re.fullmatch(
                                r"[0-9a-f]{32}\.a[1-9][0-9]*", attempt_id):
                        raise BudgetUnknown()
                    _apply_action_completion(row, attempt_id, completed.get("usage"))
        except FileNotFoundError:
            return row
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise BudgetUnknown() from exc
        return row
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
    if not (_BUDGET_ENABLED or _ACTION_BUDGET_ENABLED):
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


def _estimate_dispatch(body, max_output_tokens, rates):
    """The pre-route paid envelope: a byte-token upper bound, never an average.

    Route lookup must happen after durable admission.  Action mode accepts only a
    `_ACTION_ROUTE_MODEL_OVERHEAD_BYTES` rewrite, and model rewriting replaces
    (rather than appends) one
    JSON value, so `len(client body) + _MAX_ROUTE_BYTES` bounds the exact body
    later sent on every accepted route.  One provider token per UTF-8 byte is a
    conservative byte-fallback bound; if that envelope cannot fit, fail closed.
    """
    if type(max_output_tokens) is not int or isinstance(max_output_tokens, bool) or \
            max_output_tokens <= 0:
        raise ValueError("request max_tokens is required")
    if type(body) is not bytes:
        raise ValueError("request bytes required")
    prompt_tokens = len(body) + _ACTION_ROUTE_MODEL_OVERHEAD_BYTES
    estimated_cost = (prompt_tokens * rates["prompt_rub_per_token"] +
                      max_output_tokens * rates["completion_rub_per_token"])
    if not math.isfinite(estimated_cost) or estimated_cost < 0:
        raise ValueError("invalid dispatch estimate")
    return {"provider_calls": 1, "prompt_tokens": prompt_tokens,
            "completion_tokens": max_output_tokens,
            "wall_time_s": UPSTREAM_READ_TIMEOUT,
            "estimated_cost_rub": estimated_cost}


def _reserve_dispatch(estimate):
    """Atomically make a worst-case dispatch reservation before urlopen()."""
    if not _ACTION_BUDGET_ENABLED:
        return True
    if _ACTION_BUDGET_REASON is not None:
        def unavailable(row):
            if row["verdict"] == "WITHIN":
                row.update(verdict="EXCEEDED", reason=_ACTION_BUDGET_REASON)
            return row
        _budget_update(unavailable)
        return False
    if not _valid_action_estimate(estimate):
        raise BudgetUnknown()
    def reserve(row):
        # Re-read under the same lock that commits this admission: a snapshot
        # becoming stale or being replaced between startup and contact is a
        # refusal, not permission inherited from an old process view.
        try:
            fresh_rates = _strict_rate_snapshot(UPSTREAM_RATE_SNAPSHOT)
        except ValueError:
            row.update(verdict="EXCEEDED", reason="RATE_SNAPSHOT_INVALID")
            return row
        if fresh_rates != row["rate_snapshot"]:
            row.update(verdict="EXCEEDED", reason="RATE_SNAPSHOT_CHANGED")
            return row
        if row["verdict"] == "EXCEEDED":
            return row
        projected = dict(row["projected"])
        for name, value in estimate.items():
            projected[name] += value
        for name in ACTION_LIMITS:
            counter = name.removeprefix("max_")
            if projected[counter] > row["limits"][name]:
                row.update(verdict="EXCEEDED", reason="MAX_" + name.upper())
                return row
        # Deliberately never released: an answer lost to a crash may still have
        # been billed.  Journal first, then state: a crash between them is
        # rebuilt on restart rather than reopening even one cap.
        _append_action_reservation(uuid.uuid4().hex, estimate)
        # Completed usage is a second, honest counter below.
        row["projected"] = projected
        return row
    row = _budget_update(reserve)
    return row is not None and row["verdict"] == "WITHIN"


def _mark_action_refusal(reason):
    def refuse(row):
        if row["verdict"] == "WITHIN":
            row.update(verdict="EXCEEDED", reason=reason)
        return row
    _budget_update(refuse)


def _apply_action_completion(row, attempt_id, usage):
    """Apply one fsync'd provider contact exactly once; unknown usage is honest."""
    if attempt_id in row["completed_attempt_ids"]:
        return
    if type(attempt_id) is not str or not re.fullmatch(r"[0-9a-f]{32}\.a[1-9][0-9]*", attempt_id):
        raise BudgetUnknown()
    known = type(usage) is dict
    values = {}
    if known:
        for name in ("prompt_tokens", "completion_tokens"):
            value = usage.get(name)
            if type(value) is not int or isinstance(value, bool) or value < 0:
                known = False
                break
            values[name] = value
    observed = dict(row["observed"])
    observed["provider_calls"] += 1
    if known:
        observed["prompt_tokens"] += values["prompt_tokens"]
        observed["completion_tokens"] += values["completion_tokens"]
    else:
        row["observed_usage_unknown"] += 1
    row["observed"] = observed
    row["completed_attempt_ids"] = row["completed_attempt_ids"] + [attempt_id]
    row["completed_overshoot"] = {
        name: max(observed[name] - row["limits"]["max_" + name], 0)
        for name in ("provider_calls", "prompt_tokens", "completion_tokens")}


def _record_completed_usage(usage, attempt_id):
    """Reconcile a ledger-durable provider contact, including error/no-usage."""
    if not _ACTION_BUDGET_ENABLED:
        return None
    def complete(row):
        _apply_action_completion(row, attempt_id, usage)
        return row
    return _budget_update(complete)


def _record_budget_result(success):
    if _ACTION_BUDGET_ENABLED:
        return None
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
# `calls`/`prompt_tokens`/`cached_tokens` stay the lane TOTALS — the abort
# marker's `cache_observed` is the cross-check against the ledger's own sums and
# nothing may change what those three mean. The six CACHE_JUDGEMENT_TERMS live
# in the same dict beside them: the cache floor is a PROXY SIGNAL for a
# substitution, so it may judge only the billed calls whose identity was not
# directly confirmed. See lane_guard.CACHE_JUDGEMENT_TERMS for the measured
# CloseRouter evidence that made this the rule.
_LANE_CACHE = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0}
_LANE_CACHE.update(cache_terms())
# THE WASTED CALL IS REAL MONEY. A provider that starts substituting on half of
# its calls has to show up loudly rather than silently triple the bill, so every
# discard is counted here AND flagged in its own ledger row
# (`discarded_substitution`). measure/lane_guard.py re-derives these numbers
# from the finished ledger and lane-audit.py writes them into the run's
# artifacts, so the count survives the proxy.
_SUBSTITUTION = {"discarded": 0, "prompt_tokens": 0, "cached_tokens": 0,
                 "by_model": {}}


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


def _lane_trip(reason, detail, expected=None):
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
           "expected_returned_identity": (
               UPSTREAM_EXPECTED_RETURNED_IDENTITY if expected is None else expected),
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


def _is_substitution(returned, expected):
    """True only when the provider NAMED a model and it is the wrong family.

    `expected` is the identity of the ROUTE THIS CALL WAS SENT ON, never the
    module global. A route swap changes base, model and expected identity
    together, and `model_family` keeps the vendor prefix - so judging a
    CloseRouter answer against a linkapi identity would call every single call
    a substitution. The per-call identity is not a refinement here, it is the
    difference between a working swap and a lane that trips on call one.

    A 2xx that names nothing is NOT evidence of a substitution and is never
    retried on that basis - it is an unmeasured row, which lane-audit.py
    refuses at the end of the run as RETURNED_MODEL_UNKNOWN.
    """
    return bool(expected and isinstance(returned, str) and returned.strip()
                and not same_family(expected, returned))


def _note_substitution(returned, usage):
    """Count one discarded wrong-model answer, and what it cost."""
    try:
        billed, hit = cache_tokens(usage)
    except Exception:
        billed = hit = 0
    name = returned if isinstance(returned, str) and returned.strip() else "?"
    with _LANE_LOCK:
        _SUBSTITUTION["discarded"] += 1
        _SUBSTITUTION["prompt_tokens"] += billed
        _SUBSTITUTION["cached_tokens"] += hit
        _SUBSTITUTION["by_model"][name] = _SUBSTITUTION["by_model"].get(name, 0) + 1
        total = _SUBSTITUTION["discarded"]
    sys.stderr.write("upstream-log-proxy: DISCARDED SUBSTITUTION #%d - %s "
                     "(%d prompt tokens billed for nothing)\n" % (total, name, billed))
    sys.stderr.flush()


def _lane_observe(status, returned, usage, expected):
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
    if _is_substitution(returned, expected):
        _lane_trip("RETURNED_MODEL_FAMILY_MISMATCH",
                   "requested %s (family %s), provider answered as %s (family %s)"
                   % (expected, model_family(expected),
                      returned, model_family(returned)),
                   expected=expected)
        return
    if not UPSTREAM_CACHE_GUARD:
        return
    billed, hit = cache_tokens(usage)
    if not billed:
        return
    # THE DIRECT SIGNAL, ON THIS CALL. `_is_substitution` already returned above
    # for a wrong family, so reaching here with a named model of the expected
    # family means the identity of this billed call is directly confirmed and
    # the cache floor — a proxy for exactly that measurement — may not judge it.
    # A 2xx naming NO model, or a lane with no declared identity, is
    # unconfirmed, which is the v37 shape the floor was calibrated on.
    confirmed = bool(expected and isinstance(returned, str) and returned.strip()
                     and same_family(expected, returned))
    with _LANE_LOCK:
        _LANE_CACHE["calls"] += 1
        _LANE_CACHE["prompt_tokens"] += billed
        _LANE_CACHE["cached_tokens"] += hit
        note_cache_call(_LANE_CACHE, billed, hit, confirmed)
        terms = {key: _LANE_CACHE[key] for key in CACHE_JUDGEMENT_TERMS}
        calls = _LANE_CACHE["calls"]
    breach = cache_judgement(terms, UPSTREAM_CACHE_MIN_RATE,
                             UPSTREAM_CACHE_MIN_CALLS)
    if breach:
        _lane_trip(*breach)
        return
    # LOUDLY, WHILE THE RUN IS ALIVE. The v39 CloseRouter lane paid for 2.4M
    # uncached prompt tokens and the only thing that mentioned it killed the
    # run. Printed at every min-calls boundary so the figure is in the run's
    # stderr whether or not anything later goes wrong.
    if calls >= UPSTREAM_CACHE_MIN_CALLS and calls % UPSTREAM_CACHE_MIN_CALLS == 0:
        sys.stderr.write("upstream-log-proxy: %s\n"
                         % cache_cost_fact(terms, UPSTREAM_CACHE_MIN_RATE,
                                           UPSTREAM_CACHE_MIN_CALLS))
        sys.stderr.flush()


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
    queue = [resp]
    seen = set()
    while queue:
        obj = queue.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        if hasattr(obj, "settimeout"):
            return obj
        for attr in ("fp", "raw", "_sock", "sock"):
            try:
                queue.append(getattr(obj, attr, None))
            except Exception:
                pass
    return None


def _action_remaining_deadline(state, sock):
    """Arm one monotonic action deadline across every socket read."""
    deadline = state.get("action_deadline")
    if deadline is None:
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("action wall deadline exceeded")
    if sock is not None:
        sock.settimeout(remaining)


def _read_whole_with_deadline(resp, state):
    if not _ACTION_BUDGET_ENABLED:
        return resp.read()
    sock = _stream_socket(resp)
    if sock is None:
        raise TimeoutError("action wall deadline unenforceable")
    try:
        if sock.fileno() < 0:
            # HTTP/1.0 test/close-delimited responses may already be wholly in
            # BufferedReader when headers arrive; no future socket read exists.
            return resp.read()
    except (OSError, ValueError):
        return resp.read()
    chunks = []
    while True:
        if getattr(resp, "length", None) == 0:
            return b"".join(chunks)
        _action_remaining_deadline(state, sock)
        # HTTPResponse.read(n) is permitted to wait for all n bytes, which
        # turns a dripped Content-Length body back into an unbounded aggregate.
        # read1 returns the currently available buffered/socket fragment.
        reader = getattr(resp, "read1", None)
        chunk = reader(65536) if reader is not None else resp.read(1)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


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
    def _relay(self, body):
        # A tripped lane answers nothing. Checked before the body is even
        # inspected: the whole value of the guard is the call that does NOT
        # happen.
        if _LANE_ABORT is not None:
            self._lane_refusal()
            return

        # LOG THE OUTBOUND OUTPUT BUDGET. `prompt + max_tokens` is what the
        # provider checks, not the prompt alone, and an unclamped qwen-code
        # auto-escalates max_tokens with a 64K floor — which is how a request
        # whose prompt fits still comes back an empty HTTP 200 (vllm#3851).
        # request_bytes alone could never show that, so every past diagnosis of
        # a truncated run had to guess at this number. One integer, no body.
        request_max_tokens = None
        parsed_request = None
        try:
            parsed_request = json.loads(body or b"{}", object_pairs_hook=_strict_object)
            request_max_tokens = (parsed_request or {}).get("max_tokens")
        except Exception:
            if _ACTION_BUDGET_ENABLED:
                try:
                    _mark_action_refusal("REQUEST_JSON_INVALID")
                except BudgetUnknown:
                    pass
                self._budget_refusal()
                return
        if not isinstance(request_max_tokens, int):
            request_max_tokens = None

        # The action envelope is the paid contact boundary.  It judges the
        # actual serialized bytes and required output cap before route lookup,
        # credential reads, or a socket open.
        dispatch_estimate = None
        if _ACTION_BUDGET_ENABLED:
            try:
                if _ACTION_BUDGET_REASON is not None:
                    if not _reserve_dispatch({}):
                        self._budget_refusal()
                        return
                dispatch_estimate = _estimate_dispatch(body, request_max_tokens,
                                                       _RATE_SNAPSHOT)
            except (BudgetUnknown, ValueError, TypeError, KeyError):
                try:
                    _mark_action_refusal("REQUEST_MAX_TOKENS_INVALID")
                except BudgetUnknown:
                    pass
                self._budget_refusal()
                return

        # THE WALL. Before the route is read, before the credential is attached,
        # before one byte leaves this box.
        if UPSTREAM_PER_REQUEST_TOKEN_GATE > 0:
            estimated = estimate_prompt_tokens(body)
            declared = request_max_tokens or 0
            if estimated + declared > UPSTREAM_PER_REQUEST_TOKEN_GATE:
                self._pre_send_refusal(estimated, declared)
                return

        client_headers = {k: v for k, v in self.headers.items()
                          if k.lower() not in _HOP}
        # identity so the body stays scannable; the client gets it un-encoded,
        # which is what every OpenAI-compatible client already accepts.
        client_headers["Accept-Encoding"] = "identity"
        # THE SUBSTITUTION LOOP, and the only thing it does is decide whether
        # to ask again. Each turn is one COMPLETE upstream call with its own
        # ledger row, its own budget reservation and its own captured bodies;
        # a turn that saw a wrong-model answer wrote nothing to the client, so
        # re-issuing is safe. `discarded` is both the counter and the cap, and
        # the last permitted turn fails closed inside `_relay_once`.
        #
        # ONE ROUTE PER UPSTREAM CALL. The route is read at the top of each
        # turn, not once for the whole client request, because each turn IS a
        # separate billed upstream call - and because the next fix (advance the
        # route when the substitution cap is spent) has to be able to take
        # effect between turns. Within a turn the tuple never changes: `route`
        # is passed down and NOTHING below reads the module globals, so a swap
        # landing mid-call cannot make this call send model A to provider B and
        # judge it against identity C.
        discarded = 0
        reserved_next_turn = False
        while True:
            # A paid turn obtains its durable token before it can even learn a
            # route or credential.  Substitution/fallback returns to this top;
            # ordinary HTTP retries reserve again inside `_relay_once`.
            if _ACTION_BUDGET_ENABLED:
                try:
                    if not reserved_next_turn and not _reserve_dispatch(dispatch_estimate):
                        self._budget_refusal()
                        return
                except BudgetUnknown:
                    self._budget_refusal()
                    return
                reserved_next_turn = False
            try:
                route = _current_route()
            except RouteUnavailable as exc:
                # FAIL CLOSED. No fallback to the env route and no fallback to
                # the route we saw last time: a call sent to the wrong provider
                # silently corrupts a paid measurement.
                self._route_refusal(exc)
                return
            sent_body, requested, sent = _apply_route_model(body, route)
            if _ACTION_BUDGET_ENABLED and len(sent_body) > (
                    len(body) + _ACTION_ROUTE_MODEL_OVERHEAD_BYTES):
                # This should be unreachable for an accepted route; treating a
                # violated proof as an admission failure keeps it a hard wall.
                try:
                    _mark_action_refusal("MAX_MAX_PROMPT_TOKENS")
                except BudgetUnknown:
                    pass
                self._budget_refusal()
                return
            headers = dict(client_headers)
            # OWN THE CREDENTIAL. When a key file is configured the client's
            # Authorization header is REPLACED, never merged and never trusted,
            # so the qwen child's launch-time environment stops deciding which
            # upstream account a paid call bills to. Read per request: see
            # _read_api_key for why there is no cache. The ROUTE may name its
            # own key file, because a provider swap is also a credential swap.
            key_file = route.key_file or UPSTREAM_API_KEY_FILE
            if key_file:
                try:
                    headers["Authorization"] = "Bearer " + _read_api_key(key_file)
                except KeyUnavailable as exc:
                    # FAIL CLOSED. Not "fall back to the client's header" — that
                    # is the silent wrong-account call this feature removes. The
                    # diagnosis names the file and the fault; it cannot name a
                    # key, because no key was obtained on this path.
                    self._key_refusal(str(exc), key_file)
                    return
            outcome = self._relay_once(sent_body, headers, requested, sent,
                                       request_max_tokens, discarded, route,
                                       dispatch_estimate, reserved_first=True)
            if outcome == "SUBSTITUTED":
                discarded += 1
                continue
            if outcome != "EXHAUSTED":
                return
            # THE CAP IS SPENT. Before this fix that was the end of the run.
            # Nothing has reached the client - the wrong-model body was
            # discarded, as always - so the request can be re-issued verbatim
            # on another provider. `_advance_route` decides whether there is
            # one; when there is not (and when no fallback list is configured
            # at all) it answers False and we trip exactly as before.
            spent = self._exhausted
            if _ACTION_BUDGET_ENABLED:
                # A fallback is a new paid dispatch.  Its durable envelope must
                # exist before `_advance_route` writes or verifies a route, not
                # merely before the later socket open.  Keep it conservative if
                # advance subsequently fails.
                try:
                    if not _reserve_dispatch(dispatch_estimate):
                        self._budget_refusal()
                        return
                except BudgetUnknown:
                    self._budget_refusal()
                    return
                reserved_next_turn = True
            if _advance_route(route, discarded + 1, spent["request_id"]):
                # A FRESH BUDGET ON THE NEW ROUTE. The old route's discards
                # were the old provider's failure, not this one's.
                discarded = 0
                continue
            # FAIL CLOSED, with the ORIGINAL reason code: the lane really did
            # meet a substitution it could not get past, and that is what the
            # marker must say.
            _lane_observe(spent["status"], spent["returned_model"],
                          spent["usage"], route.expected_identity)
            self._lane_refusal()
            return

    def _relay_once(self, body, headers, requested, sent, request_max_tokens,
                    discarded, route, dispatch_estimate=None, reserved_first=False):
        """One upstream call.

        Returns "SUBSTITUTED" when the provider answered as the wrong model
        family, the answer was discarded WITHOUT relaying a byte of it, and a
        retry is still allowed on THIS route. Returns "EXHAUSTED" when the same
        thing happened and the cap is spent - the client has NOT been answered
        and `_relay` decides whether another provider can serve it. Anything
        else means the client has been answered and `_relay` must stop.
        """
        retryable = discarded < UPSTREAM_SUBSTITUTION_RETRY_MAX
        # HOLD ON THE LAST ATTEMPT TOO. Holding only while a retry remains
        # would relay the wrong-model body on the attempt that exhausts the
        # cap — the exact thing the abort is supposed to prevent, arrived at
        # by the back door. The hold is on whenever the feature is on; whether
        # the discarded call is re-issued or ends the run is decided after.
        hold = UPSTREAM_SUBSTITUTION_RETRY_MAX > 0
        # Every ledger row this turn writes carries the route it was sent on,
        # so lane_guard.audit_ledger can judge a mid-flight-swapped run row by
        # row instead of declaring a family mismatch on every pre-swap call.
        route_fields = route.ledger_fields()
        # THE ONLY FIELD THAT PROVES /clear ACTUALLY CLEARED. A fresh session sends
        # exactly two messages — the system prompt and the seed user message. Run
        # 20260831T214240Z-v43 typed /clear 124 times and only 5 request bodies
        # ever had messages == 2; the rest climbed to 633 in one conversation.
        # Reading it here costs nothing: the body is already parsed. Ledger
        # fields written below: "messages_count" and "session_id".
        messages_count = None
        session_id = None
        try:
            parsed_body = json.loads(body or b"{}")
        except Exception:
            parsed_body = None
        if isinstance(parsed_body, dict):
            messages = parsed_body.get("messages")
            if isinstance(messages, list):
                messages_count = len(messages)
            for key in ("session_id", "user", "metadata"):
                value = parsed_body.get(key)
                if isinstance(value, str) and value:
                    session_id = value
                    break
                if isinstance(value, dict) and isinstance(value.get("session_id"), str):
                    session_id = value["session_id"]
                    break
        t0 = time.time()
        req = urllib.request.Request(route.url_for(self.path), data=body or None,
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
                 "response_valid": False, "res_chunks": [],
                 # The substitution retry's own state: bytes held back from the
                 # client while the answering model is still unknown, whether
                 # the head has been released, and the discard verdict.
                 "held": [], "held_bytes": 0, "released": False,
                 "substituted": False}
        status = None
        attempt = 0
        outcome = "DONE"
        try:
          while True:
            attempt += 1
            action_attempt_id = "%s.a%d" % (request_id, attempt)
            t_try = time.time()
            try:
                allowed = (True if _ACTION_BUDGET_ENABLED and reserved_first and attempt == 1
                           else (_reserve_dispatch(dispatch_estimate)
                                 if _ACTION_BUDGET_ENABLED else _reserve_budget(len(body))) )
                if not allowed:
                    self._budget_refusal()
                    return
            except BudgetUnknown:
                self._budget_refusal()
                return
            try:
                if _ACTION_BUDGET_ENABLED:
                    # The reservation is exactly this enforced end-to-end bound,
                    # not urllib's per-read timeout.
                    state["action_deadline"] = time.monotonic() + UPSTREAM_READ_TIMEOUT
                resp = urllib.request.urlopen(req, timeout=UPSTREAM_READ_TIMEOUT)
                status = resp.getcode()
                break
            except urllib.error.HTTPError as e:
                resp, status = e, e.code
                # THE BODY IS READ FOR EVERY 400, not only a retried one: the
                # class of a 400 lives in the provider's words, and a
                # deterministic refusal must never enter the burst path. When
                # the body is read here THIS branch owns the row and the
                # client's copy — nothing falls through to the pump, so one
                # attempt still writes exactly one ledger row.
                eager = (status == 400
                         or (status in _RETRYABLE and attempt <= UPSTREAM_RETRY_MAX))
                if eager:
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
                    # ONE duration, judged and recorded. Two `time.time()`
                    # reads would leave a ledger row whose own number does not
                    # quite match the number the class was decided on, and that
                    # is a row nobody can explain six weeks later.
                    attempt_ms = int((time.time() - t_try) * 1000)
                    refusal = deterministic_refusal(
                        status, why, duration_ms=attempt_ms,
                        generation_window_s=GENERATION_WINDOW_S)
                    if BODY_DIR:
                        # upstream_error keeps 300 chars so the ledger stays
                        # readable; the file keeps all of it, which is where a
                        # provider's structured refusal actually lives.
                        _capture_write("%s.a%d.res.json.gz" % (request_id, attempt),
                                       [raw], capture, "response")
                    # The class is written ONLY when there is one, so a
                    # clean ledger keeps the exact shape every previous run
                    # wrote and no reader sees a new field appear unbidden.
                    named = {"upstream_refusal_class": refusal} if refusal else {}
                    try:
                        record(**_capture_row(capture), **named, **route_fields,
                               request_max_tokens=request_max_tokens, requested_model=requested, returned_model=None,
                               tool_call=False, status=status, attempt=attempt,
                               duration_ms=attempt_ms,
                               sent_model=sent, request_bytes=len(body),
                               path=self.path, stream=False, upstream_error=why,
                               messages_count=messages_count, session_id=session_id,
                               **({"action_attempt_id": action_attempt_id,
                                   "action_contact_completed": True}
                                  if _ACTION_BUDGET_ENABLED else {}))
                        if _ACTION_BUDGET_ENABLED:
                            _record_completed_usage(None, action_attempt_id)
                    except OSError:
                        pass
                    try:
                        _record_budget_result(False)
                    except BudgetUnknown:
                        self._budget_refusal()
                        return
                    if (refusal is None and status in _RETRYABLE
                            and attempt <= UPSTREAM_RETRY_MAX):
                        time.sleep(min(60.0, (UPSTREAM_RETRY_BASE_MS / 1000.0)
                                       * (2 ** (attempt - 1))))
                        req = urllib.request.Request(route.url_for(self.path),
                                                     data=body or None,
                                                     headers=headers,
                                                     method=self.command)
                        continue
                    # TERMINAL. Either the refusal is deterministic (retrying
                    # is pure waste) or the budget is spent. The row is already
                    # written and the body already read, so hand the client the
                    # provider's own refusal verbatim from here.
                    self._relay_error(status, e, raw)
                    return outcome
                break
            except Exception as e:                   # DNS, TLS, refused, timeout
                try:
                    record(**_capture_row(capture), **route_fields,
                           request_max_tokens=request_max_tokens, requested_model=requested, returned_model=None,
                           tool_call=False, status=None, error=str(e)[:300],
                           attempt=attempt,
                           duration_ms=int((time.time() - t0) * 1000), sent_model=sent,
                           request_bytes=len(body), path=self.path, stream=False,
                           messages_count=messages_count, session_id=session_id,
                           **({"action_attempt_id": action_attempt_id,
                               "action_contact_completed": True}
                              if _ACTION_BUDGET_ENABLED else {}))
                    if _ACTION_BUDGET_ENABLED:
                        _record_completed_usage(None, action_attempt_id)
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
                  self._pump_stream(resp, state, hold, route.expected_identity)
                  if (_ACTION_BUDGET_ENABLED and state["error"] and
                          not state["released"]):
                      payload = json.dumps({"error": {
                          "message": "proxy: action wall deadline exceeded"}}).encode()
                      self.send_response(502)
                      self.send_header("Content-Type", "application/json")
                      self.send_header("Content-Length", str(len(payload)))
                      self.end_headers()
                      self.wfile.write(payload)
                      state["released"] = True
              else:
                  self._pump_whole(resp, status, state, hold, route.expected_identity)
          finally:
              success = (type(status) is int and 200 <= status < 300 and
                         state["returned_model"] == route.expected_identity and
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
              # A DISCARDED ATTEMPT IS FLAGGED, NEVER HIDDEN, AND NEVER
              # COUNTED AS A CALL. The keys are written ONLY on a discard, so a
              # clean run keeps the exact ledger shape every previous run wrote
              # and no existing reader sees a new field appear unbidden.
              # measure/lane_guard.py skips these rows for the identity check
              # and for the cache rate, and counts them separately as money
              # spent on nothing.
              # THE PROVIDER'S CLOCK, NAMED IN THE ROW. r3's nine dead calls
              # were HTTP 200s carrying a gateway error chunk at ~90.4 s and
              # the ledger said nothing about why - so "I was cut by the
              # provider's clock" could not be answered from the run's own
              # artifacts. Written ONLY when there is one, so a lane with no
              # declared window keeps the exact ledger shape it has today.
              row_duration_ms = int((time.time() - t0) * 1000)
              window_fields = {}
              if deterministic_refusal(
                      status, state["error"], duration_ms=row_duration_ms,
                      generation_window_s=GENERATION_WINDOW_S
              ) == GENERATION_WINDOW_EXCEEDED:
                  window_fields = {
                      "upstream_refusal_class": GENERATION_WINDOW_EXCEEDED}
              # A COMPLETION THE BUDGET CUT, NAMED WHILE THE RUN IS ALIVE
              # (fix 7). THE PROXY IS THE ONLY PLACE THIS CAN HAPPEN: it holds
              # the request body and the response at the same moment. The
              # request says whether this turn was qwen summarising ITSELF —
              # a compaction, or a <state_snapshot> — and the response says
              # whether it was cut at `finish_reason: "length"`. qwen never
              # tells the harness either, interactive-drive.py sees neither,
              # and the acceptance gate reads a report file after the money is
              # gone. That is how run 20260827T173511Z-v41 clipped four of its
              # own state summaries and nothing noticed for a day.
              #
              # Written ONLY on a clipped row, so a run that was never cut
              # keeps the exact ledger shape every previous run wrote.
              clip_fields = {}
              if completion_clipped({"finish_reason": state["finish_reason"]}):
                  clip_fields = {
                      "completion_clipped": True,
                      "clipped_request_class": compaction_request_class(
                          last_message_text(body)),
                  }
                  # LOUD, on the run's own stderr, and loudest for the class
                  # that corrupts the run rather than merely truncating an
                  # answer. A clipped compaction means every later turn
                  # reasons from a memory that stops mid-sentence.
                  try:
                      sys.stderr.write(
                          "upstream-log-proxy: %s COMPLETION CLIPPED at "
                          "max_tokens=%s (finish_reason=length, request class "
                          "%s)%s\n"
                          % ("RUN-INTEGRITY:"
                             if clip_fields["clipped_request_class"] != "work"
                             else "DEFECT:",
                             request_max_tokens,
                             clip_fields["clipped_request_class"],
                             " — qwen was writing its OWN memory and was cut; "
                             "it needs %d tokens to finish a summary"
                             % COMPACTION_SUMMARY_RESERVE
                             if clip_fields["clipped_request_class"] != "work"
                             else ""))
                      sys.stderr.flush()
                  except Exception:
                      pass          # observability never alters delivery
              discard_fields = {}
              if state["substituted"]:
                  discard_fields = {"discarded_substitution": True,
                                    "substitution_attempt": discarded + 1,
                                    "substitution_retry_exhausted": not retryable}
              ledger_durable = False
              try:
                  record(**_capture_row(capture), **discard_fields,
                         **window_fields, **route_fields, **clip_fields,
                         request_max_tokens=request_max_tokens, requested_model=requested,
                         returned_model=state["returned_model"], attempt=attempt,
                         tool_call=state["tool_call"], status=status,
                         usage=state["usage"], finish_reason=state["finish_reason"],
                         duration_ms=row_duration_ms, sent_model=sent,
                         request_bytes=len(body), path=self.path, stream=streaming,
                         messages_count=messages_count, session_id=session_id,
                         # THE ESTIMATE, BESIDE THE PROVIDER'S OWN COUNT. One
                         # integer, so UPSTREAM_CHARS_PER_TOKEN can be re-derived
                         # from any future run's ledger instead of argued about —
                         # and so a lane whose real ratio drifts below the divisor
                         # is visible in an artifact rather than only in a breach.
                         estimated_prompt_tokens=estimate_prompt_tokens(body),
                         upstream_error=state["error"], stream_events=state["stream_events"],
                         content_events=state["content_events"], ttft_ms=state["ttft_ms"],
                         deadline_unenforceable=state["deadline_unenforceable"] or None,
                         stream_parse_errors=state["stream_parse_errors"],
                         stream_complete=state["stream_complete"], stream_bytes=state["stream_bytes"],
                         **({"action_attempt_id": action_attempt_id,
                             "action_contact_completed": True}
                            if _ACTION_BUDGET_ENABLED else {}))
                  if _ACTION_BUDGET_ENABLED:
                      # record() fsyncs this completion journal row before the
                      # state reconciliation below; retrying it is idempotent.
                      _record_completed_usage(state["usage"], action_attempt_id)
                  ledger_durable = True
              except OSError:
                  pass
              if ledger_durable:
                  try:
                      _record_completed_usage(state["usage"], action_attempt_id)
                  except BudgetUnknown:
                      # Reservation remains durable.  Unknown reconciliation is
                      # not grounds to free it or send another paid request.
                      pass
              if state["substituted"]:
                  try:
                      resp.close()        # the rest of a discarded stream is waste
                  except Exception:
                      pass
                  _note_substitution(state["returned_model"], state["usage"])
                  if retryable:
                      # Nothing reached the client and nothing was counted
                      # towards the lane: ask the provider again.
                      outcome = "SUBSTITUTED"
                  else:
                      # THE CAP IS SPENT ON THIS ROUTE. Still nothing relayed,
                      # so the decision is _relay's: advance to the next
                      # provider, or trip the lane with this exact observation
                      # and refuse the client. The judgement is handed up
                      # rather than made here because only _relay knows whether
                      # a fallback exists - and it must be made in ONE place,
                      # or a future route would be tripped on by two.
                      outcome = "EXHAUSTED"
                      self._exhausted = {"request_id": request_id,
                                         "status": status,
                                         "returned_model": state["returned_model"],
                                         "usage": state["usage"]}
              else:
                  # AFTER the row, always. The call that trips the guard has to
                  # be in the ledger, or the artifact that explains the abort is
                  # the one call the ledger does not contain.
                  _lane_observe(status, state["returned_model"], state["usage"],
                                route.expected_identity)
        finally:
            try:
                _update_inflight(request_id)
            except OSError:
                pass
        return outcome

    def _pre_send_refusal(self, estimated, declared):
        """Refuse locally, in a shape the client can recover from.

        HTTP 400 with the OpenAI `context_length_exceeded` code, because that is
        what an OpenAI-compatible provider returns for this and therefore what
        every client already handles. Deliberately NOT 403 (the lane-breach
        code): a lane breach is a verdict and must end the run, while this is a
        request the client should shrink and retry — qwen's reactive compression
        path does exactly that once the error text classifies as an overflow.
        Deliberately NOT 5xx either: qwen's `defaultShouldRetry` retries every
        5xx blindly, which would re-send the same oversized prompt.
        """
        message = pre_send_refusal_text(UPSTREAM_PER_REQUEST_TOKEN_GATE,
                                       estimated, declared)
        # An EVENT row, not a call row: nothing was sent, nothing was billed and
        # no model answered, so no accounting or identity check may read it as a
        # call. `returned_model` stays absent for the same reason.
        record(event="pre_send_refused", schema=1,
               per_request_token_gate=UPSTREAM_PER_REQUEST_TOKEN_GATE,
               estimated_prompt_tokens=estimated,
               request_max_tokens=declared,
               chars_per_token=UPSTREAM_CHARS_PER_TOKEN,
               returned_model=None,
               detail=message)
        payload = json.dumps({"error": {
            "message": message,
            "type": "invalid_request_error",
            "param": "messages",
            "code": "context_length_exceeded"}}).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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

    def _key_refusal(self, why, key_file=None):
        """Refuse a request we cannot credential, and say exactly why.

        The message is built only from the constant reasons in _read_api_key
        and the configured PATH — never from file CONTENT — so neither the
        client body, stderr, nor the ledger can carry a key on this path. It is
        also routed through record()'s scrubber for the same reason the
        upstream_error field is.
        """
        key_file = UPSTREAM_API_KEY_FILE if key_file is None else key_file
        detail = "proxy: upstream key unavailable: %s (%s)" % (why, key_file)
        try:
            record(event="key_unavailable", path=self.path, reason=why,
                   key_file=key_file, returned_model=None)
        except OSError:
            pass  # observability can never be the reason a refusal is not sent
        payload = json.dumps({"error": {"message": detail}}).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _route_refusal(self, exc):
        """Refuse a request we cannot ROUTE, and say exactly why.

        503 and not 403: an unusable route file is a configuration fault the
        operator can repair with one atomic write, and the very next request
        must then succeed - which is exactly what the key refusal already
        does. It is deliberately NOT a lane trip: the lane is not compromised,
        no call was made, nothing was billed, and tripping the lane would turn
        a fat-fingered swap into a dead run.

        The message is built only from the constant reasons in _read_route_once
        and the configured PATHS - never from file CONTENT - so neither the
        client, stderr, nor the ledger can carry a credential on this path. A
        route file is refused outright if it carries a credential-looking
        field, so there is nothing to leak in the first place.
        """
        counters = _note_route_refusal(exc.code)
        path = _ROUTE_SOURCE["file"]
        detail = "proxy: upstream route unavailable: %s (%s)" % (exc, path)
        try:
            record(event="route_unavailable", path=self.path, reason=str(exc),
                   route_reason_code=exc.code, route_file=path,
                   route_refusals=counters, returned_model=None)
        except OSError:
            pass  # observability can never be the reason a refusal is not sent
        sys.stderr.write("upstream-log-proxy: ROUTE UNAVAILABLE %s — %s\n"
                         % (exc.code, exc))
        sys.stderr.flush()
        payload = json.dumps({"error": {"message": detail}}).encode("utf-8")
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

    def _pump_whole(self, resp, status, state, hold=False, expected=""):
        try:
            payload = _read_whole_with_deadline(resp, state)
        except (TimeoutError, socket.timeout, OSError):
            state["error"] = "action_wall_deadline_exceeded"
            state["response_valid"] = False
            try:
                resp.close()
            except Exception:
                pass
            payload = json.dumps({"error": {"message": "proxy: action wall deadline exceeded"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            state["released"] = True
            return
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
        # THE DISCARD, on the easy path: nothing has been written yet, so a
        # wrong-model body simply never becomes a response.
        if hold and _is_substitution(state["returned_model"], expected):
            state["substituted"] = True
            return
        self._relay_headers(resp, status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        state["released"] = True

    def _relay_error(self, status, resp, raw):
        """The provider's own refusal, verbatim and exactly once.

        A proxy that logs the reason and hands the client an empty 400 has
        moved the blindness, not removed it.
        """
        try:
            self._relay_headers(resp, status,
                                extra=[("Content-Length", str(len(raw)))])
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            pass

    def _iter_with_deadline(self, resp, state, sock):
        """Yield stream lines, giving up if no CONTENT arrives in time.

        A usage-only chunk is an event and carries nothing, so the deadline is
        keyed on content, not on events. Once one real delta lands the socket is
        put back on the long read timeout: a call that has started answering is
        alive and may take as long as it takes.
        """
        # `HTTPResponse.__iter__` is `readline()`: a fragmented, unterminated
        # SSE event can make several successful socket reads while it waits for
        # one newline, resetting a per-read timeout each time.  In action mode
        # own the buffering and deadline check between bounded raw fragments.
        if _ACTION_BUDGET_ENABLED:
            buffered = b""
            reader = getattr(resp, "read1", None)
            if not callable(reader):
                state["error"] = "action_wall_deadline_unenforceable"
                return
            while True:
                try:
                    _action_remaining_deadline(state, sock)
                    raw = reader(4096)
                except TimeoutError:
                    state["error"] = "action_wall_deadline_exceeded"
                    return
                except socket.timeout:
                    state["error"] = "action_wall_deadline_exceeded"
                    return
                except OSError as exc:
                    if "timed out" in str(exc).lower():
                        state["error"] = "action_wall_deadline_exceeded"
                        return
                    raise
                if not raw:
                    if buffered:
                        yield buffered
                    return
                buffered += raw
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    yield line + b"\n"
            return

        it = iter(resp)
        while True:
            try:
                _action_remaining_deadline(state, sock)
            except TimeoutError:
                state["error"] = "action_wall_deadline_exceeded"
                return
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
            if (sock is not None and not _ACTION_BUDGET_ENABLED and state["content_events"]
                    and sock.gettimeout() != UPSTREAM_READ_TIMEOUT):
                sock.settimeout(UPSTREAM_READ_TIMEOUT)

    def _release_head(self, resp, state):
        """Send the stream headers and everything held back, exactly once."""
        if state["released"]:
            return
        # No Content-Length is known up front and chunked framing is one more
        # thing to get wrong, so the response ends at connection close.
        self._relay_headers(resp, 200, extra=[("Connection", "close")])
        self.end_headers()
        self.close_connection = True
        state["released"] = True
        held, state["held"] = state["held"], []
        for raw in held:
            self.wfile.write(raw)
        self.wfile.flush()

    def _hold_expired(self, state):
        """Stop holding: this stream is not going to name its model in time."""
        started = state["stream_started_at"]
        return (state["held_bytes"] > UPSTREAM_SUBSTITUTION_HOLD_BYTES
                or (started is not None and (time.time() - started) * 1000
                    > UPSTREAM_SUBSTITUTION_HOLD_MS))

    def _pump_stream(self, resp, state, hold=False, expected=""):
        state["stream_started_at"] = time.time()
        sock = _stream_socket(resp) if (UPSTREAM_FIRST_TOKEN_MS or _ACTION_BUDGET_ENABLED) else None
        if UPSTREAM_FIRST_TOKEN_MS and sock is None:
            # Never pretend to be armed. Recorded in its own field, NOT in
            # `error`: `_scan_obj` only fills `error` when it is empty, so
            # putting a diagnostic there would mask a real rate_limit_error
            # spliced into the 200 body — the two fixes cancelling out.
            state["deadline_unenforceable"] = True
        if sock is not None:
            if _ACTION_BUDGET_ENABLED:
                _action_remaining_deadline(state, sock)
            elif UPSTREAM_FIRST_TOKEN_MS:
                sock.settimeout(UPSTREAM_FIRST_TOKEN_MS / 1000.0)
        # Action mode delays its 200 until the complete stream is inside the
        # monotonic reservation.  Once headers are sent a later deadline can
        # only close a 200 stream, which is not a bounded paid outcome.
        if not hold and not _ACTION_BUDGET_ENABLED:
            # Byte-for-byte the pre-retry behaviour when the feature is off:
            # headers first, then relay each line as it arrives.
            self._release_head(resp, state)
        try:
            for raw in self._iter_with_deadline(resp, state, sock):
                state["stream_bytes"] += len(raw)
                if BODY_DIR:
                    # Appended before the relay write so a client that hangs up
                    # cannot cost us the chunk we already read off the wire. Raw,
                    # unparsed: the concatenation IS the stream as it arrived.
                    state["res_chunks"].append(raw)
                # PARSED BEFORE IT IS RELAYED, not after. The whole retry rests
                # on knowing the model id before the first byte leaves for the
                # client; for every other purpose the two orders are identical.
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
                if state["released"]:
                    self.wfile.write(raw)
                    self.wfile.flush()
                    continue
                state["held"].append(raw)
                state["held_bytes"] += len(raw)
                if _is_substitution(state["returned_model"], expected):
                    # DISCARD. Not one byte of this stream was relayed, so the
                    # request can be re-issued as if it had never happened.
                    state["substituted"] = True
                    state["held"] = []
                    return
                if state["returned_model"] or self._hold_expired(state):
                    self._release_head(resp, state)
        finally:
            # Anything held that is NOT a discard must still be delivered,
            # including a stream that ended, errored or timed out before it
            # ever named a model. Holding is a delay, never a loss.
            if (not state["substituted"] and not (_ACTION_BUDGET_ENABLED and
                                                   state["error"] is not None)):
                self._release_head(resp, state)


def main():
    _initialize_action_budget()
    srv = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Proxy)
    srv.daemon_threads = True
    sys.stderr.write("upstream-log-proxy: 127.0.0.1:%d -> %s (log %s)\n"
                     % (LISTEN_PORT, UPSTREAM_BASE, UPSTREAM_LOG))
    if UPSTREAM_ROUTE_FILE:
        sys.stderr.write("upstream-log-proxy: route file %s (read per call; "
                         "base/model/identity come from it, not the env)\n"
                         % UPSTREAM_ROUTE_FILE)
    if _FALLBACKS:
        sys.stderr.write(
            "upstream-log-proxy: %d fallback route(s), at most %d advance(s) "
            "for the whole run; exhausting the substitution cap advances "
            "instead of ending the run (history: %s)\n"
            % (len(_FALLBACKS), UPSTREAM_ROUTE_ADVANCE_MAX,
               UPSTREAM_ROUTE_ADVANCES))
        for index, route in enumerate(_FALLBACKS, 1):
            sys.stderr.write("upstream-log-proxy:   fallback %d: %s %s (identity %s)\n"
                             % (index, route.base, route.model,
                                route.expected_identity))
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
