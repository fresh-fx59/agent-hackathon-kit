#!/usr/bin/env python3
"""Bounded, authenticated, provider-free status projection for Sherlock traces."""
import argparse
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent
MIB = 1024 * 1024
MAX_JSON, MAX_EVENTS, MAX_COMPLETED = MIB, 4 * MIB, 64 * MIB
MAX_EVENT_ROWS, MAX_COMPLETED_ROWS = 4096, 100000
MAX_DISCOVERY, MAX_DISCOVERY_BYTES = 256, 32 * MIB
HEX = re.compile(r"^[0-9a-f]{64}$")
CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SECRET = re.compile(r"(?:bearer\s+|(?:sk|ghp|glpat|xox[baprs])-|akia[0-9a-z]{16}|"
                    r"(?:password|token|api[_-]?key)\s*[:=])", re.I)
PHASES = {"STAGING", "QWEN_RUNNING", "ATTEMPT_STARTED", "ATTEMPT_FINISHED",
          "RECOVERY_DECIDED", "FINISHED_UNCHECKED", "RUN_FAILED", "VERIFYING",
          "ACCEPTED", "REJECTED"}
TERMINAL = {"RUN_FAILED", "ACCEPTED", "REJECTED"}
CONTROLLER_PHASES = {"FIXING", "TESTING", "HEALTH_CHECKING", "READY",
                     "QWEN_RUNNING", "VERIFYING", "DONE", "REJECTED",
                     "RUNNER_FAILED", "BLOCKED", "BLOCKED_UNKNOWN"}
STATUS_BASE = {"schema", "run_tag", "phase", "updated_at", "pid", "attempt",
               "dataset", "arm", "trace_dir", "detail", "session_id", "reason",
               "exit_code", "duration_s", "upstream_log", "inflight_path"}
PROCESS_FIELDS = {"process_start_ticks", "pgid", "boot_id_sha256", "command_sha256"}
STATUS_ADDITIONS = {"primary_failure"}
PRIMARY_FAILURE_CODES = frozenset({
    # Authenticated verifier-local closed vocabulary; do not import it from a
    # mutable sibling helper.
    "ATTRIBUTION_UNAVAILABLE", "CACHE_TERMS_INCOMPLETE", "COMPACTION_OUTPUT_CLIPPED",
    "EXPECTED_IDENTITY_UNKNOWN", "GENERATION_WINDOW_EXCEEDED",
    "LANE_ABORT_UNREADABLE", "LANE_ACCOUNTING_INCOMPLETE", "LANE_AUDIT_FAILED",
    "LEDGER_EMPTY", "LEDGER_MALFORMED", "LEDGER_MISSING", "NO_PROGRESS",
    "CLEAR_NOT_EFFECTIVE", "TARGET_REFUSED", "STAGE_STALLED", "WRAPPER_NONZERO",
    "DRIVER_EXIT", "BUDGET_EXCEEDED", "OUTPUT_BUDGET_EXHAUSTED_BY_REASONING",
    "PER_REQUEST_TOKEN_GATE_BREACHED", "PER_REQUEST_TOKEN_GATE_UNMEASURED",
    "PROMPT_CACHE_COLLAPSE", "REASONING_CONTENT_NOT_RELAYED",
    "RETURNED_MODEL_FAMILY_MISMATCH", "RETURNED_MODEL_UNKNOWN",
    "ROUTE_ADVANCE_COUNTERS_INCONSISTENT", "ROUTE_ADVANCE_HISTORY_UNREADABLE",
    "ROUTE_ADVANCE_UNRECORDED", "ROUTE_IDENTITY_UNREADABLE", "USAGE_UNREADABLE",
    "RATE_SNAPSHOT_INVALID", "RATE_SNAPSHOT_CHANGED", "ACTION_BUDGET_INVALID",
    "MAX_PROVIDER_CALLS", "MAX_PROMPT_TOKENS", "MAX_COMPLETION_TOKENS",
    "MAX_WALL_TIME_S", "MAX_ESTIMATED_COST_RUB",
    "HARNESS_QUALIFICATION_MISSING", "TARGET_PROBE_NOT_AUTHORIZED",
    "TARGET_PROBE_BUDGET", "TARGET_CONTRACT_FAILED", "TARGET_IDENTITY_MISMATCH",
    "TARGET_IDENTITY_UNVERIFIABLE", "TARGET_RECEIPT_EXPIRED", "TARGET_RECEIPT_USED",
    "APPROVAL_REPLAYED", "FULL_RUN_NOT_AUTHORIZED", "INPUTS_INCOMPARABLE",
    "BILLING_UNKNOWN",
})
MANIFEST_KEYS = {"schema", "run_tag", "dataset", "arm", "trace", "commitment",
                 "corpus", "expected", "artifacts", "skill", "target",
                 "target_profile", "input_identity", "health_receipt", "manifest_sha256"}
SHORT_VALIDITY = {"schema", "valid", "reasons", "run_tag", "manifest_sha256",
                  "candidate_sha256", "hmac_sha256"}
FULL_ADDITIONS = {"result_stream_sha256", "upstream_sha256", "work_sha256",
                  "artifact_only", "transport", "usage", "delivery", "inventory",
                  "identity", "checkers", "contamination"}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if not spec or not spec.loader: raise ValueError("TRACE_UNRESOLVED")
    value = importlib.util.module_from_spec(spec); old = sys.dont_write_bytecode
    try: sys.dont_write_bytecode = True; spec.loader.exec_module(value)
    finally: sys.dont_write_bytecode = old
    return value


MANIFEST = module("sherlock_status_manifest", HERE / "run-manifest.py")
VALIDITY = module("sherlock_status_validity", HERE / "validate-run.py")


class Missing(Exception): pass
class Unsafe(Exception): pass


class HeldDir:
    """A directory identity held across every read and checked before return."""
    def __init__(self, path, fd=None):
        self.path = MANIFEST.clean_abs(path)
        self.fd = MANIFEST._open_dir(self.path, "STATUS") if fd is None else fd
        self.identity = os.fstat(self.fd)

    @classmethod
    def child(cls, parent, name):
        if not isinstance(name, str) or name in ("", ".", "..") or os.sep in name: raise Unsafe()
        info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode): raise Unsafe()
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, dir_fd=parent.fd); held = cls(os.path.join(parent.path, name), fd)
        if (info.st_dev, info.st_ino) != (held.identity.st_dev, held.identity.st_ino):
            held.close(); raise Unsafe()
        return held

    def read(self, name, maximum):
        if not isinstance(name, str) or not name or "/" in name or name in (".", ".."): raise Unsafe()
        try: before = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError: raise Missing()
        if not stat.S_ISREG(before.st_mode): raise Unsafe()
        try: fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=self.fd)
        except FileNotFoundError: raise Missing()
        except OSError: raise Unsafe()
        try:
            opened = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino): raise Unsafe()
            chunks, total = [], 0
            while total <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - total))
                if not chunk: break
                chunks.append(chunk); total += len(chunk)
            if total > maximum: raise Unsafe()
            after = os.fstat(fd)
            if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                    (after.st_dev, after.st_ino, after.st_size)): raise Unsafe()
            return b"".join(chunks)
        finally: os.close(fd)

    def kind(self, name):
        try: info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError: return "missing"
        if stat.S_ISREG(info.st_mode): return "file"
        if stat.S_ISDIR(info.st_mode): return "dir"
        return "unsafe"

    def check(self):
        try: current = os.stat(self.path, follow_symlinks=False)
        except OSError: raise Unsafe()
        if not stat.S_ISDIR(current.st_mode) or ((current.st_dev, current.st_ino) !=
                (self.identity.st_dev, self.identity.st_ino)): raise Unsafe()

    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd = None


def digest(data): return hashlib.sha256(data).hexdigest()
def is_hex(value): return isinstance(value, str) and HEX.fullmatch(value) is not None
def digest_or_null(value): return value is None or is_hex(value)
def uint(value, nullable=False):
    return (nullable and value is None) or (type(value) is int and 0 <= value <= 10 ** 15)
def number(value, nullable=False):
    return ((nullable and value is None) or (not isinstance(value, bool) and
            isinstance(value, (int, float)) and 0 <= value <= 10 ** 15))


def label(value, maximum=128):
    try: return MANIFEST.identity(value) if len(value) <= maximum else None
    except Exception: return None


def safe(value, maximum=160):
    if (not isinstance(value, str) or not value or len(value) > maximum or
            any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or SECRET.search(value)):
        return "?"
    return value


def safe_path(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            any(ord(c) < 32 or ord(c) == 127 for c in value) or SECRET.search(value)):
        return "?"
    return value


def timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 64: return None
    try: parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError): return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0) else None


def strict_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row: raise ValueError("duplicate JSON key")
        row[key] = value
    return row


def parse_json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=strict_object)


def json_read(held, name, maximum=MAX_JSON, retry=False):
    for attempt in range(2 if retry else 1):
        try: data = held.read(name, maximum)
        except Missing: return "missing", None, 0
        except Exception: return "invalid", None, 0
        try: value = parse_json(data)
        except (UnicodeError, ValueError, TypeError):
            if attempt == 0 and retry: continue
            return "invalid", None, len(data)
        return ("ok", value, len(data)) if isinstance(value, dict) else ("invalid", None, len(data))
    return "invalid", None, 0


def external_snapshot(path, maximum=MAX_JSON):
    try:
        canonical = MANIFEST.clean_abs(path)
        if canonical != path or len(path) > 4096: return "invalid", None
        parent = HeldDir(os.path.dirname(canonical))
        try:
            name = os.path.basename(canonical)
            kind = parent.kind(name)
            if kind == "missing": return "missing", None
            if kind != "file": return "invalid", None
            data = parent.read(name, maximum); parent.check(); return "ok", data
        finally: parent.close()
    except Missing: return "missing", None
    except Exception: return "invalid", None


def external_read(path, maximum=MAX_JSON):
    state, data = external_snapshot(path, maximum)
    return data if state == "ok" else None


def manifest_shape(row, trace, commitment_file):
    if set(row) != MANIFEST_KEYS or row.get("schema") != 3: return False
    if any(label(row.get(name)) is None for name in ("run_tag", "dataset", "arm")): return False
    seal = row.get("manifest_sha256"); unsigned = dict(row); unsigned.pop("manifest_sha256", None)
    if not is_hex(seal) or not hmac.compare_digest(seal, digest(MANIFEST.canonical(unsigned))): return False
    trace_row, commitment, target, health = (row.get("trace"), row.get("commitment"),
                                              row.get("target"), row.get("health_receipt"))
    if not isinstance(trace_row, dict) or set(trace_row) != {"path", "identity_sha256"}: return False
    if (trace_row.get("path") != trace or not is_hex(trace_row.get("identity_sha256")) or
            trace_row["identity_sha256"] != digest(trace.encode())): return False
    if not isinstance(commitment, dict) or set(commitment) != {"path", "identity_sha256", "key_id"}: return False
    if (commitment.get("path") != commitment_file or not is_hex(commitment.get("key_id")) or
            commitment.get("identity_sha256") != digest(commitment_file.encode())): return False
    target_keys = {"version", "requested_model", "expected_returned_identity", "provider", "lane", "identity_sha256"}
    if not isinstance(target, dict) or set(target) != target_keys: return False
    if any(label(target.get(name)) is None for name in target_keys - {"identity_sha256"}): return False
    target_unsigned = {name: target[name] for name in target_keys - {"identity_sha256"}}
    if target.get("identity_sha256") != digest(MANIFEST.canonical(target_unsigned)): return False
    if (not isinstance(health, dict) or set(health) != {"path", "bytes", "sha256"} or
            safe_path(health.get("path")) == "?" or not uint(health.get("bytes")) or
            not is_hex(health.get("sha256"))): return False
    return True


def trace_auth(trace, commitment_file, commitment_key, held=None, snapshot=None):
    own = held is None; held = HeldDir(trace) if own else held
    try:
        state, row, byte_count = snapshot or json_read(held, "run-manifest.json")
        actual_file = MANIFEST.clean_abs(commitment_file)
        if state != "ok" or not manifest_shape(row, held.path, actual_file): raise ValueError
        key, key_id, _ = MANIFEST._commitment_key(commitment_key)
        if row["commitment"]["key_id"] != key_id: raise ValueError
        MANIFEST._verify_commitment(actual_file, row, held.path, key, key_id); held.check()
        return held, row, key, byte_count
    except Exception:
        if own: held.close()
        raise ValueError("TRACE_UNRESOLVED")


def link_auth(parent, run_tag, commitment_file, commitment_key, held=None, now=None):
    own = held is None; parent = HeldDir(parent) if own else held
    try:
        if label(run_tag) is None: raise ValueError
        state, row, _ = json_read(parent, "controller-child.json")
        fields = {"schema", "parent_trace", "parent_identity_sha256", "child_run_tag",
                  "child_trace", "child_manifest_sha256", "linked_at", "key_id", "hmac_sha256"}
        when = timestamp(row.get("linked_at")) if state == "ok" else None
        now = now or dt.datetime.now(dt.timezone.utc)
        paths_ok = (all(isinstance(row.get(name), str) and safe_path(row[name]) != "?" and
                    MANIFEST.clean_abs(row[name]) == row[name] for name in ("parent_trace", "child_trace"))
                    if state == "ok" else False)
        if (state != "ok" or set(row) != fields or row.get("schema") != 1 or not paths_ok or
                row.get("parent_trace") != parent.path or
                row.get("parent_identity_sha256") != digest(parent.path.encode()) or
                row.get("child_run_tag") != run_tag or when is None or
                when > now + dt.timedelta(seconds=60) or
                any(not is_hex(row.get(name)) for name in
                    ("parent_identity_sha256", "child_manifest_sha256", "key_id", "hmac_sha256"))):
            raise ValueError
        key, key_id, _ = MANIFEST._commitment_key(commitment_key)
        unsigned = dict(row); supplied = unsigned.pop("hmac_sha256")
        if (row["key_id"] != key_id or not hmac.compare_digest(supplied,
                hmac.new(key, MANIFEST.canonical(unsigned), hashlib.sha256).hexdigest())): raise ValueError
        child, manifest, _, _ = trace_auth(row["child_trace"], commitment_file, commitment_key)
        if (manifest["run_tag"] != run_tag or manifest["manifest_sha256"] != row["child_manifest_sha256"] or
                child.path != row["child_trace"]):
            child.close(); raise ValueError
        parent.check(); return parent, child, manifest, key
    except Exception:
        if own: parent.close()
        raise ValueError("TRACE_UNRESOLVED")


def status_projection(held, manifest):
    state, row, size = json_read(held, "status.json", retry=True)
    if state != "ok": return None, ["STATUS_INVALID"], size
    keys = set(row)
    if (not STATUS_BASE.issubset(keys) or keys - STATUS_BASE - PROCESS_FIELDS - STATUS_ADDITIONS or
            row.get("schema") != 1 or row.get("run_tag") != manifest["run_tag"] or
            label(row.get("phase")) is None or
            timestamp(row.get("updated_at")) is None or not uint(row.get("attempt"), True) or
            not uint(row.get("pid"), True) or
            (row.get("primary_failure") is not None and
             row.get("primary_failure") not in PRIMARY_FAILURE_CODES)):
        return None, ["STATUS_INVALID"], size
    if row["phase"] not in PHASES:
        row = dict(row); row["phase"] = "UNKNOWN"
        return row, ["PHASE_UNKNOWN"], size
    return row, [], size


def valid_transport(row):
    return (isinstance(row, dict) and set(row) == {"exit_code", "status", "duration_s"} and
            row.get("status") in (None, "success", "error") and uint(row.get("exit_code"), True) and
            number(row.get("duration_s"), True))


def valid_usage(row):
    return (isinstance(row, dict) and set(row) == {"turns", "input_tokens", "output_tokens"} and
            all(uint(row.get(name), True) for name in row))


def valid_delivery(row):
    keys = {"channel", "relation", "divergent", "message_sha256", "message_bytes",
            "artifact_sha256", "artifact_bytes", "delivered_sha256", "delivered_bytes"}
    return (isinstance(row, dict) and set(row) == keys and
            row.get("channel") in {"none", "message", "file", "both"} and
            row.get("relation") in {"none", "message-only", "file-only", "identical",
                                    "file-repeats-message", "message-repeats-file", "divergent"} and
            type(row.get("divergent")) is bool and row["divergent"] == (row["relation"] == "divergent") and
            all(is_hex(row.get(name)) for name in ("message_sha256", "artifact_sha256", "delivered_sha256")) and
            all(uint(row.get(name)) for name in ("message_bytes", "artifact_bytes", "delivered_bytes")))


def valid_identity(row):
    return (isinstance(row, dict) and set(row) == {"requested_sha256", "returned_sha256", "successful_calls"} and
            is_hex(row.get("requested_sha256")) and digest_or_null(row.get("returned_sha256")) and
            uint(row.get("successful_calls")))


def validity_projection(held, manifest, key):
    state, row, _ = json_read(held, "validity.json")
    pending = ({"state": "pending", "reason": None, "count": 0}, "pending", None, [])
    if state == "missing": return pending
    invalid = ({"state": "invalid", "reason": None, "count": 0}, "pending", None,
               ["VALIDITY_INVALID"])
    if state != "ok" or set(row) not in (SHORT_VALIDITY, SHORT_VALIDITY | FULL_ADDITIONS): return invalid
    reasons = row.get("reasons")
    if (row.get("schema") != 1 or type(row.get("valid")) is not bool or
            not isinstance(reasons, list) or len(reasons) > 32 or
            not all(isinstance(x, str) and CODE.fullmatch(x) for x in reasons) or
            row.get("valid") != (len(reasons) == 0) or row.get("run_tag") != manifest["run_tag"] or
            row.get("manifest_sha256") != manifest["manifest_sha256"] or
            not digest_or_null(row.get("candidate_sha256")) or not is_hex(row.get("hmac_sha256"))): return invalid
    try: expected_hmac = VALIDITY.sign(row, key)
    except Exception: return invalid
    if not hmac.compare_digest(row["hmac_sha256"], expected_hmac): return invalid
    delivery, returned, diagnostics = "pending", None, []
    if set(row) == SHORT_VALIDITY | FULL_ADDITIONS:
        if (not all(digest_or_null(row.get(name)) for name in
                    ("result_stream_sha256", "upstream_sha256", "work_sha256")) or
                type(row.get("artifact_only")) is not bool or not valid_transport(row.get("transport")) or
                not valid_usage(row.get("usage")) or not valid_delivery(row.get("delivery")) or
                not valid_identity(row.get("identity"))): return invalid
        identity = row["identity"]
        if identity["requested_sha256"] != digest(manifest["target"]["requested_model"].encode()): return invalid
        returned = identity["returned_sha256"]; item = row["delivery"]
        delivery = {"channel": item["channel"], "relation": item["relation"],
                    "divergent": item["divergent"], "message_bytes": item["message_bytes"],
                    "artifact_bytes": item["artifact_bytes"], "delivered_bytes": item["delivered_bytes"]}
        if row["artifact_only"]: diagnostics.append("ARTIFACT_ONLY")
    verdict = {"state": "accepted" if row["valid"] else "rejected",
               "reason": reasons[0] if reasons else None, "count": len(reasons)}
    return verdict, delivery, returned, diagnostics


def budget_projection(held, manifest, key):
    state, row, _ = json_read(held, "controller-receipt.json")
    if state == "missing": return "not_reported", []
    fields = {"schema", "run_tag", "manifest_sha256", "observed_at",
              "attempts_charged", "request_bytes", "input_tokens", "output_tokens",
              "wall_seconds", "consecutive_provider_failures", "limits", "verdict",
              "reason", "key_id", "hmac_sha256"}
    limit_fields = {"max_upstream_attempts", "max_request_bytes", "max_wall_seconds",
                    "max_consecutive_provider_failures"}
    valid = (state == "ok" and set(row) == fields and row.get("schema") == 1 and
             row.get("run_tag") == manifest["run_tag"] and
             row.get("manifest_sha256") == manifest["manifest_sha256"] and
             timestamp(row.get("observed_at")) is not None and
             all(uint(row.get(name)) for name in ("attempts_charged", "request_bytes",
                                                   "wall_seconds",
                                                   "consecutive_provider_failures")) and
             row.get("input_tokens") is None and row.get("output_tokens") is None and
             isinstance(row.get("limits"), dict) and set(row["limits"]) == limit_fields and
             all(type(row["limits"].get(name)) is int and row["limits"][name] > 0
                 for name in limit_fields) and row.get("verdict") in ("WITHIN", "EXCEEDED") and
             (row.get("reason") is None or (isinstance(row.get("reason"), str) and
                                             CODE.fullmatch(row["reason"]))) and
             is_hex(row.get("key_id")) and is_hex(row.get("hmac_sha256")))
    if not valid: return "invalid", ["BUDGET_INVALID"]
    unsigned = dict(row); supplied = unsigned.pop("hmac_sha256")
    key_id = hashlib.sha256(key).hexdigest()
    expected = hmac.new(key, MANIFEST.canonical(unsigned), hashlib.sha256).hexdigest()
    if row["key_id"] != key_id or not hmac.compare_digest(supplied, expected):
        return "invalid", ["BUDGET_INVALID"]
    projected = {name: row[name] for name in
                 ("verdict", "reason", "attempts_charged", "request_bytes",
                  "input_tokens", "output_tokens", "wall_seconds",
                  "consecutive_provider_failures", "limits")}
    projected["state"] = "reported"
    return projected, []


def controller_projection(parent, manifest):
    state, row, _ = json_read(parent, "status.json", retry=True)
    fields = {"schema", "controller_id", "phase", "updated_at", "child_run_tag",
              "child_manifest_sha256", "reason"}
    if (state != "ok" or set(row) != fields or row.get("schema") != 1 or
            label(row.get("controller_id")) is None or row.get("phase") not in CONTROLLER_PHASES or
            timestamp(row.get("updated_at")) is None or
            row.get("child_run_tag") != manifest["run_tag"] or
            row.get("child_manifest_sha256") != manifest["manifest_sha256"] or
            (row.get("reason") is not None and
             (not isinstance(row.get("reason"), str) or CODE.fullmatch(row["reason"]) is None))):
        return None, ["CONTROLLER_INVALID"]
    return {"phase": row["phase"], "updated_at": row["updated_at"],
            "reason": row["reason"]}, []


def jsonl_rows(held, name, maximum, row_limit):
    try: data = held.read(name, maximum)
    except Missing: return [], False
    except Exception: return [], True
    complete = data if not data or data.endswith(b"\n") else data[:data.rfind(b"\n") + 1]
    lines = complete.splitlines()
    if len(lines) > row_limit: return [], True
    values = []
    try:
        for raw in lines:
            if not raw: return [], True
            value = parse_json(raw)
            if not isinstance(value, dict): return [], True
            values.append(value)
    except (UnicodeError, ValueError, TypeError): return [], True
    return values, False


def event_projection(held, tag):
    values, bad = jsonl_rows(held, "status-events.jsonl", MAX_EVENTS, MAX_EVENT_ROWS)
    chosen, recovery, attempt, unknown = {"event": "unknown", "at": None}, "none", None, False
    for row in values:
        if label(row.get("run_tag")) is None: bad = True; continue
        if row["run_tag"] != tag: continue
        event, when = row.get("event"), timestamp(row.get("ts"))
        if when is None: bad = True; continue
        if event not in PHASES:
            chosen = {"event": "unknown", "at": row["ts"]}; unknown = True; continue
        chosen = {"event": event, "at": row["ts"]}
        if uint(row.get("attempt"), True) and row.get("attempt") is not None: attempt = row["attempt"]
        if event == "RECOVERY_DECIDED": recovery = "scheduled"
        elif recovery == "scheduled" and event == "ATTEMPT_STARTED": recovery = "running"
        elif recovery == "running" and event == "ATTEMPT_FINISHED": recovery = "complete"
        elif recovery != "none" and event == "RUN_FAILED": recovery = "exhausted"
    diagnostics = (["EVENT_INVALID"] if bad else []) + (["EVENT_UNKNOWN"] if unknown else [])
    return chosen, ("unknown" if bad or unknown else recovery), attempt, diagnostics


def inflight_projection(held, tag):
    state, row, _ = json_read(held, "upstream-inflight.json")
    if state == "missing": return None, []
    if state != "ok" or set(row) != {"requests"} or not isinstance(row.get("requests"), dict):
        return None, ["UPSTREAM_INVALID"]
    count = 0; fields = {"started_at", "request_bytes", "path", "requested_model", "attempt",
                         "run_tag", "pid", "proxy_instance"}
    for request_id, item in row["requests"].items():
        if (not isinstance(request_id, str) or not request_id or len(request_id) > 256 or
                not isinstance(item, dict) or set(item) != fields or timestamp(item.get("started_at")) is None or
                not uint(item.get("request_bytes")) or safe_path(item.get("path")) == "?" or
                (item.get("requested_model") is not None and label(item.get("requested_model")) is None) or
                not uint(item.get("attempt")) or
                label(item.get("run_tag")) is None or not uint(item.get("pid")) or
                not isinstance(item.get("proxy_instance"), str) or not item["proxy_instance"] or
                len(item["proxy_instance"]) > 256): return None, ["UPSTREAM_INVALID"]
        if item["run_tag"] == tag: count += 1
    return count, []


def completed_projection(held, tag, expected):
    values, bad = jsonl_rows(held, "upstream-completed.jsonl", MAX_COMPLETED, MAX_COMPLETED_ROWS)
    own, successful, identities, missing_identity = 0, 0, set(), False
    for row in values:
        if label(row.get("run_tag")) is None: bad = True; continue
        if row["run_tag"] != tag: continue
        own += 1; status_code = row.get("status"); returned = row.get("returned_model")
        if ((status_code is not None and (type(status_code) is not int or not 0 <= status_code <= 999)) or
                (returned is not None and label(returned) is None) or
                (row.get("requested_model") is not None and label(row.get("requested_model")) is None)):
            bad = True; continue
        if type(status_code) is int and 200 <= status_code < 300:
            successful += 1
            if returned is None: missing_identity = True
            else: identities.add(returned)
    if bad: return {"completed": 0, "successful": 0, "identity": "unknown"}, ["UPSTREAM_INVALID"]
    if missing_identity or not identities: identity = "unknown"
    elif len(identities) > 1: identity = "mixed"
    elif identities == {expected}: identity = "exact"
    else: identity = "wrong"
    return {"completed": own, "successful": successful, "identity": identity}, []


def process_proof(state, proc_root="/"):
    required = ("pid", "process_start_ticks", "pgid", "boot_id_sha256", "command_sha256")
    if not isinstance(state, dict) or any(state.get(name) is None for name in required): return "unverified", []
    pid, start, pgid = state.get("pid"), state.get("process_start_ticks"), state.get("pgid")
    if (not uint(pid) or pid == 0 or not uint(start) or not uint(pgid) or pgid == 0 or
            not is_hex(state.get("boot_id_sha256")) or not is_hex(state.get("command_sha256"))):
        return "stale", ["PROCESS_STALE"]
    base = MANIFEST.clean_abs(proc_root)
    stat_data = external_read(os.path.join(base, "proc", str(pid), "stat"), 65536)
    cmdline = external_read(os.path.join(base, "proc", str(pid), "cmdline"), MIB)
    boot = external_read(os.path.join(base, "proc/sys/kernel/random/boot_id"), 4096)
    if stat_data is None or cmdline is None or boot is None: return "unverified", []
    try:
        text = stat_data.decode("ascii"); close = text.rfind(")"); tail = text[close + 2:].split()
        actual_pgid, actual_start = int(tail[2]), int(tail[19])
    except (UnicodeError, ValueError, IndexError): return "stale", ["PROCESS_STALE"]
    facts = (actual_start == start, actual_pgid == pgid, pgid == pid,
             digest(boot.strip()) == state["boot_id_sha256"], digest(cmdline) == state["command_sha256"])
    return ("confirmed", []) if all(facts) else ("stale", ["PROCESS_STALE"])


def health_projection(data, asset, target, now=None):
    if data is None: return "unknown"
    if (not isinstance(asset, dict) or len(data) != asset.get("bytes") or digest(data) != asset.get("sha256")):
        return "invalid"
    try: row = parse_json(data)
    except (UnicodeError, ValueError, TypeError): return "invalid"
    fields = {"schema", "checked_at", "expires_at", "lane", "provider", "requested_model",
              "shape", "tools", "sizes_kb", "history", "verdict"}
    if not isinstance(row, dict) or not fields.issubset(row) or row.get("schema") != 1: return "invalid"
    checked, expires = timestamp(row.get("checked_at")), timestamp(row.get("expires_at")); now = now or dt.datetime.now(dt.timezone.utc)
    if checked is None or expires is None or checked > now + dt.timedelta(seconds=60): return "invalid"
    if now - checked > dt.timedelta(minutes=15) or expires <= now: return "stale"
    if expires <= checked or expires - checked > dt.timedelta(minutes=15): return "invalid"
    if (row.get("lane"), row.get("provider"), row.get("requested_model")) != (
            target.get("lane"), target.get("provider"), target.get("requested_model")): return "mismatch"
    if (row.get("shape") != "history" or not uint(row.get("tools")) or row["tools"] < 25 or
            not isinstance(row.get("sizes_kb"), list) or not all(type(x) is int for x in row["sizes_kb"]) or
            not {100, 250, 400}.issubset(row["sizes_kb"]) or not isinstance(row.get("history"), list) or
            not row["history"] or row.get("verdict") != "HEALTHY"): return "invalid"
    seen, identities = set(), set()
    for item in row["history"]:
        if (not isinstance(item, dict) or type(item.get("size_kb")) is not int or
                item.get("status") != 200 or label(item.get("returned_model")) is None): return "invalid"
        seen.add(item["size_kb"]); identities.add(item["returned_model"])
    if not {100, 250, 400}.issubset(seen) or identities != {target.get("expected_returned_identity")}:
        return "mismatch"
    return "healthy"


def path_projection(trace, manifest, diagnostics):
    values = {"path": trace, "manifest_sha256": manifest["manifest_sha256"]}
    for key, name in (("status_path", "status.json"), ("events_path", "status-events.jsonl"),
                      ("inflight_path", "upstream-inflight.json"),
                      ("completed_path", "upstream-completed.jsonl")): values[key] = os.path.join(trace, name)
    for name, value in list(values.items()):
        if name != "manifest_sha256" and safe_path(value) == "?":
            values[name] = "?"; diagnostics.append("PATH_REDACTED")
    return values


def project(trace, manifest, key, selection="direct", proc_root="/", held=None):
    own = held is None; held = HeldDir(trace) if own else held
    try:
        diagnostics = []; state, found, _ = status_projection(held, manifest); diagnostics.extend(found)
        phase = state["phase"] if state else "UNKNOWN"; updated = state["updated_at"] if state else None
        verdict, delivery, returned_digest, found = validity_projection(held, manifest, key); diagnostics.extend(found)
        budget, found = budget_projection(held, manifest, key); diagnostics.extend(found)
        last_event, recovery, event_attempt, found = event_projection(held, manifest["run_tag"]); diagnostics.extend(found)
        inflight, found = inflight_projection(held, manifest["run_tag"]); diagnostics.extend(found)
        completed, found = completed_projection(held, manifest["run_tag"],
                                                 manifest["target"]["expected_returned_identity"]); diagnostics.extend(found)
        completed["inflight"] = inflight; process, found = process_proof(state, proc_root); diagnostics.extend(found)
        asset = manifest["health_receipt"]
        health_state, health_data = external_snapshot(asset["path"])
        health = "invalid" if health_state == "invalid" else health_projection(
            health_data, asset, manifest["target"])
        if health == "invalid": diagnostics.append("HEALTH_INVALID")
        elif health == "stale": diagnostics.append("HEALTH_STALE")
        elif health == "mismatch": diagnostics.append("HEALTH_MISMATCH")
        expected_digest = digest(manifest["target"]["expected_returned_identity"].encode())
        target_identity = "unknown" if returned_digest is None else ("exact" if returned_digest == expected_digest else "wrong")
        paths = path_projection(held.path, manifest, diagnostics)
        row = {"schema": 1, "selection": selection, "run_tag": safe(manifest["run_tag"]),
               "phase": phase, "updated_at": updated, "dataset": safe(manifest["dataset"]),
               "arm": safe(manifest["arm"]), "trace": paths, "last_event": last_event,
               "attempt": event_attempt if event_attempt is not None else (state.get("attempt") if state else None),
               "wrapper_exit_code": state.get("exit_code") if state else None,
               "primary_failure": state.get("primary_failure") if state else None,
               "recovery": recovery, "upstream": completed, "process": process,
               "target": {"provider": safe(manifest["target"]["provider"]),
                          "requested_model": safe(manifest["target"]["requested_model"]),
                          "lane": safe(manifest["target"]["lane"]), "identity": target_identity,
                          "requested_sha256": digest(manifest["target"]["requested_model"].encode()),
                          "returned_sha256": returned_digest},
               "health": health, "budget": budget, "validity": verdict,
               "delivery": delivery, "diagnostics": sorted(set(diagnostics))[:16]}
        held.check(); return row
    finally:
        if own: held.close()


def discover(root, commitment_file, commitment_key, held=None):
    own = held is None; root = HeldDir(root) if own else held
    diagnostics, candidates, total = [], [], 0
    try:
        names = sorted(os.listdir(root.fd), key=os.fsencode)
        if len(names) > MAX_DISCOVERY: return None, ["DISCOVERY_LIMIT"]
        for name in names:
            try: child = HeldDir.child(root, name)
            except (OSError, Unsafe): continue
            try:
                snapshot = json_read(child, "run-manifest.json")
                total += snapshot[2]
                if total > MAX_DISCOVERY_BYTES: return None, ["DISCOVERY_LIMIT"]
                try:
                    child, manifest, key, manifest_bytes = trace_auth(
                        child.path, commitment_file, commitment_key, child, snapshot)
                except Exception: diagnostics.append("DISCOVERY_MANIFEST_INVALID"); continue
                state, found, status_bytes = status_projection(child, manifest); total += status_bytes
                if total > MAX_DISCOVERY_BYTES: return None, ["DISCOVERY_LIMIT"]
                if found: diagnostics.append("DISCOVERY_STATUS_INVALID"); continue
                if state["phase"] in TERMINAL: continue
                candidates.append((timestamp(state["updated_at"]), os.fsencode(name), child, manifest, key)); child = None
            finally:
                if child is not None: child.close()
        if not candidates: return None, sorted(set(diagnostics + ["NO_ACTIVE_RUN"]))
        candidates.sort(key=lambda item: (item[0], item[1]))
        if len(candidates) > 1 and candidates[-1][0] == candidates[-2][0]:
            for item in candidates: item[2].close()
            return None, sorted(set(diagnostics + ["AMBIGUOUS_ACTIVE_RUN"]))
        selected = candidates.pop()
        for item in candidates: item[2].close()
        root.check(); return selected, sorted(set(diagnostics))
    finally:
        if own: root.close()


def empty_projection(codes):
    return {"schema": 1, "selection": "none", "run_tag": None, "phase": "UNKNOWN",
            "updated_at": None, "dataset": None, "arm": None,
            "trace": {"path": "?", "manifest_sha256": None, "status_path": "?",
                      "events_path": "?", "inflight_path": "?", "completed_path": "?"},
            "last_event": {"event": "unknown", "at": None}, "attempt": None,
            "recovery": "unknown", "upstream": {"inflight": None, "completed": 0,
            "successful": 0, "identity": "unknown"}, "process": "unverified",
            "target": {"provider": "?", "requested_model": "?", "lane": "?",
                       "identity": "unknown", "requested_sha256": None, "returned_sha256": None},
            "health": "unknown", "budget": "not_reported", "validity": {"state": "pending",
            "reason": None, "count": 0}, "delivery": "pending", "diagnostics": codes[:16]}


TARGET_PROBE_MANIFEST_KEYS = {"schema", "action", "created_at", "expires_at", "nonce",
                              "target_profile_sha256", "probe_budget_sha256",
                              "fixture_manifest_sha256", "input_package_sha256",
                              "rate_snapshot_sha256"}
TARGET_PROBE_AUTH_KEYS = {"schema", "action_nonce", "manifest_raw_sha256",
                          "nonce_record_path", "nonce_record_sha256", "nonce_root",
                          "probe_root", "trace_path", "bench_status_sha256",
                          "run_verdict_sha256"}


def target_read(held, name):
    """Read one target-probe input from its held directory, never by pathname."""
    try:
        info = os.stat(name, dir_fd=held.fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise Unsafe()
        raw = held.read(name, MAX_JSON)
        row = parse_json(raw)
        if not isinstance(row, dict): raise Unsafe()
        held.check()
        return raw, row
    except Exception as exc:
        raise ValueError("TRACE_UNRESOLVED") from exc


def target_external_nonce(path):
    """Hold the nonce parent while proving its exact durable singleton bytes."""
    try:
        canonical = MANIFEST.clean_abs(path)
        if canonical != path or len(path) > 4096: raise Unsafe()
        parent = HeldDir(os.path.dirname(canonical))
        try:
            name = os.path.basename(canonical)
            info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise Unsafe()
            raw = parent.read(name, MAX_JSON); parent.check()
            return raw
        finally:
            parent.close()
    except Exception as exc:
        raise ValueError("TRACE_UNRESOLVED") from exc


def target_probe_projection(path):
    """Authenticate the separate, one-shot operator target-probe authority."""
    trace = HeldDir(path)
    root = work = runs = expected = None
    try:
        root_path = MANIFEST.clean_abs(os.path.dirname(os.path.dirname(os.path.dirname(trace.path))))
        if trace.path != os.path.join(root_path, "probe-work", "runs", "target-contract-probe"):
            raise ValueError("TRACE_UNRESOLVED")
        root = HeldDir(root_path); work = HeldDir.child(root, "probe-work")
        runs = HeldDir.child(work, "runs"); expected = HeldDir.child(runs, "target-contract-probe")
        if (expected.identity.st_dev, expected.identity.st_ino) != (trace.identity.st_dev, trace.identity.st_ino):
            raise ValueError("TRACE_UNRESOLVED")
        raw_manifest, manifest = target_read(trace, "probe-manifest.json")
        if (set(manifest) != TARGET_PROBE_MANIFEST_KEYS or manifest.get("schema") != 1 or
                manifest.get("action") != "target_contract_probe" or
                not isinstance(manifest.get("nonce"), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["nonce"]) or
                timestamp(manifest.get("created_at")) is None or timestamp(manifest.get("expires_at")) is None or
                timestamp(manifest["expires_at"]) <= timestamp(manifest["created_at"]) or
                timestamp(manifest["expires_at"]) <= dt.datetime.now(dt.timezone.utc) or
                not all(is_hex(manifest.get(name)) for name in TARGET_PROBE_MANIFEST_KEYS if name.endswith("_sha256"))):
            raise ValueError("TRACE_UNRESOLVED")
        for name, field in (("target-profile.json", "target_profile_sha256"),
                            ("probe-budget.json", "probe_budget_sha256"),
                            ("probe-rate-snapshot.json", "rate_snapshot_sha256"),
                            ("fixture-manifest.json", "fixture_manifest_sha256"),
                            ("input-package.json", "input_package_sha256")):
            raw, _ = target_read(trace, name)
            if not hmac.compare_digest(digest(raw), manifest[field]): raise ValueError("TRACE_UNRESOLVED")
        raw_auth, authorization = target_read(trace, "action-authorization.json")
        if set(authorization) != TARGET_PROBE_AUTH_KEYS or authorization.get("schema") != 1:
            raise ValueError("TRACE_UNRESOLVED")
        nonce_root = authorization.get("nonce_root"); nonce_path = authorization.get("nonce_record_path")
        expected_nonce_path = (os.path.join(nonce_root, manifest["nonce"] + ".json")
                               if isinstance(nonce_root, str) else None)
        if (authorization.get("action_nonce") != manifest["nonce"] or
                authorization.get("manifest_raw_sha256") != digest(raw_manifest) or
                authorization.get("probe_root") != root.path or authorization.get("trace_path") != trace.path or
                not isinstance(nonce_root, str) or not isinstance(nonce_path, str) or
                nonce_path != expected_nonce_path or MANIFEST.clean_abs(nonce_root) != nonce_root or
                MANIFEST.clean_abs(nonce_path) != nonce_path or
                not all(is_hex(authorization.get(name)) for name in
                        ("nonce_record_sha256", "bench_status_sha256", "run_verdict_sha256")) or
                not hmac.compare_digest(authorization["bench_status_sha256"], digest(Path(__file__).read_bytes())) or
                not hmac.compare_digest(authorization["run_verdict_sha256"], digest((HERE / "run-verdict.py").read_bytes()))):
            raise ValueError("TRACE_UNRESOLVED")
        nonce_raw = target_external_nonce(nonce_path)
        wanted = (json.dumps({"nonce": manifest["nonce"], "manifest_sha256": digest(raw_manifest)},
                             sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if (not hmac.compare_digest(nonce_raw, wanted) or
                not hmac.compare_digest(digest(nonce_raw), authorization["nonce_record_sha256"])):
            raise ValueError("TRACE_UNRESOLVED")
        state, _, _ = status_projection(trace, {"run_tag": "target-contract-probe"})
        row = {"schema": 1, "selection": "target-probe", "run_tag": "target-contract-probe",
               "phase": state["phase"] if state else "UNKNOWN",
               "wrapper_exit_code": state.get("exit_code") if state else None,
               "authenticated": True, "authority": "operator-approved-target-probe",
               "health": "not_applicable", "validity": "not_applicable",
               "diagnostics": []}
        trace.check(); expected.check(); runs.check(); work.check(); root.check()
        return row
    finally:
        for held in (expected, runs, work, root, trace):
            if held is not None:
                try: held.close()
                except OSError: pass


def wrap_path(value):
    value = safe_path(value)
    if value == "?": return ["?"]
    return [value[index:index + 100] for index in range(0, len(value), 100)] or ["?"]


def render(row):
    run_tag, selection = safe(row.get("run_tag"), 64), safe(row.get("selection")); phase = row.get("phase") if row.get("phase") in PHASES else "UNKNOWN"
    upstream = row.get("upstream") if isinstance(row.get("upstream"), dict) else {}
    budget = row.get("budget")
    budget_text = ("%s:%s" % (budget.get("state", "?"), budget.get("verdict", "?"))
                   if isinstance(budget, dict) else safe(budget))
    delivery = row.get("delivery"); delivery_text = delivery.get("channel", "?") if isinstance(delivery, dict) else "pending"
    lines = ["%s %s %s" % (phase, run_tag, selection),
             "attempt=%s recovery=%s upstream=%s" % (row.get("attempt"), safe(row.get("recovery")), upstream.get("completed")),
             "process=%s health=%s budget=%s" % (safe(row.get("process")), safe(row.get("health")), budget_text),
             "validity=%s delivery=%s" % (safe(row.get("validity", {}).get("state")), safe(delivery_text))]
    reason = row.get("validity", {}).get("reason")
    if reason is not None: lines.append("reason=%s" % safe(reason, 64))
    trace = row.get("trace") if isinstance(row.get("trace"), dict) else {}
    trace_lines = wrap_path(trace.get("path", "?")); lines.extend(trace_lines)
    status_lines = wrap_path(trace.get("status_path", "?"))
    if len(trace_lines) + len(status_lines) <= 3: lines.extend(status_lines)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("trace_or_root")
    parser.add_argument("--commitment-file"); parser.add_argument("--commitment-key")
    parser.add_argument("--target-probe", action="store_true")
    parser.add_argument("--run-tag", action="append"); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.commitment_file) != bool(args.commitment_key):
        parser.error("commitment file and key must be supplied together")
    if args.target_probe and (args.commitment_file or args.commitment_key or args.run_tag):
        parser.error("target-probe is mutually exclusive with commitment authority")
    if not args.target_probe and not args.commitment_file:
        parser.error("commitment file and key are required outside target-probe mode")
    run_tag_supplied = args.run_tag is not None
    if run_tag_supplied and len(args.run_tag) != 1:
        sys.stderr.write("TRACE_UNRESOLVED\n"); return 2
    args.run_tag = args.run_tag[0] if args.run_tag else None
    held = parent = child = root = None
    try:
        if args.target_probe:
            row = target_probe_projection(args.trace_or_root)
            sys.stdout.write(json.dumps(row, sort_keys=True) + "\n" if args.json else render(row)); return 0
        path = MANIFEST.clean_abs(args.trace_or_root); held = HeldDir(path); kind = held.kind("run-manifest.json")
        if run_tag_supplied:
            parent, child, manifest, key = link_auth(path, args.run_tag, args.commitment_file,
                                                     args.commitment_key, held); held = None
            row = project(child.path, manifest, key, "link", held=child); parent.check(); child.check()
            controller, codes = controller_projection(parent, manifest)
            row["diagnostics"] = sorted(set(row["diagnostics"] + codes))[:16]
            if controller is not None: row["controller"] = controller
        elif kind == "file":
            child, manifest, key, _ = trace_auth(path, args.commitment_file, args.commitment_key, held); held = None
            row = project(child.path, manifest, key, held=child); child.check()
        elif kind == "missing":
            root = held; held = None; found, codes = discover(path, args.commitment_file, args.commitment_key, root)
            if found is None: row = empty_projection(codes)
            else:
                _, _, child, manifest, key = found; row = project(child.path, manifest, key, "discovery", held=child)
                row["diagnostics"] = sorted(set(row["diagnostics"] + codes))[:16]
            root.check()
        else: raise ValueError("TRACE_UNRESOLVED")
        sys.stdout.write(json.dumps(row, sort_keys=True) + "\n" if args.json else render(row)); return 0
    except Exception:
        sys.stderr.write("TRACE_UNRESOLVED\n"); return 2
    finally:
        seen = set()
        for item in (held, parent, child, root):
            if item is not None and id(item) not in seen:
                seen.add(id(item))
                try: item.close()
                except OSError: pass


if __name__ == "__main__": raise SystemExit(main())
