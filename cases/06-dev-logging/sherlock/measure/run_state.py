#!/usr/bin/env python3
"""Durable, credential-free run snapshots and append-only events."""
import argparse
import datetime as _dt
import fcntl
import json
import os
import re
import tempfile

STATUS_FIELDS = ("schema", "run_tag", "phase", "updated_at", "pid", "attempt",
                 "dataset", "arm", "trace_dir", "detail", "session_id", "reason",
                 "exit_code", "duration_s", "upstream_log", "inflight_path",
                 "process_start_ticks", "pgid", "boot_id_sha256", "command_sha256",
                 "primary_failure")
TERMINAL_FAILURES = {"RUN_FAILED", "REJECTED"}
PRIMARY_FAILURE_CODES = frozenset({
    # Current runner and lane terminal causes.
    "ATTRIBUTION_UNAVAILABLE", "LANE_ABORT_UNREADABLE", "LANE_ACCOUNTING_INCOMPLETE",
    "LANE_AUDIT_FAILED", "NO_PROGRESS", "CLEAR_NOT_EFFECTIVE", "TARGET_REFUSED",
    "STAGE_STALLED", "WRAPPER_NONZERO", "DRIVER_EXIT", "BUDGET_EXCEEDED",
    "RATE_SNAPSHOT_INVALID", "RATE_SNAPSHOT_CHANGED", "ACTION_BUDGET_INVALID",
    "MAX_PROVIDER_CALLS", "MAX_PROMPT_TOKENS", "MAX_COMPLETION_TOKENS",
    "MAX_WALL_TIME_S", "MAX_ESTIMATED_COST_RUB",
    # Admission and probe failure contract.
    "HARNESS_QUALIFICATION_MISSING", "TARGET_PROBE_NOT_AUTHORIZED",
    "TARGET_PROBE_BUDGET", "TARGET_CONTRACT_FAILED", "TARGET_IDENTITY_MISMATCH",
    "TARGET_IDENTITY_UNVERIFIABLE", "TARGET_RECEIPT_EXPIRED", "TARGET_RECEIPT_USED",
    "APPROVAL_REPLAYED", "FULL_RUN_NOT_AUTHORIZED", "INPUTS_INCOMPARABLE",
    "BILLING_UNKNOWN",
})
SECRET_MARKERS = re.compile(r"(?:bearer\s+|(?:sk|ghp|glpat|xox[baprs])-|AKIA[0-9A-Z]{16}|-----BEGIN .*PRIVATE KEY-----|(?:password|token|api[_-]?key)\s*[:=])", re.I)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe(value):
    if isinstance(value, (dict, list, tuple, set)):
        raise ValueError("non-scalar value rejected")
    if isinstance(value, str) and SECRET_MARKERS.search(value):
        raise ValueError("secret-shaped value rejected")
    return value


def _failure_code(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        _safe(value)
    except ValueError:
        return None
    return value if value in PRIMARY_FAILURE_CODES else None


def state_event(event: str, *, exit_code=None, reason=None, previous=None) -> dict:
    """Project a terminal observation without allowing later layers to rewrite it."""
    prior = previous.get("primary_failure") if isinstance(previous, dict) else None
    primary_failure = _failure_code(prior)
    if primary_failure is None and event in TERMINAL_FAILURES:
        primary_failure = _failure_code(reason)
    return {
        "terminal_observation": event,
        "wrapper_exit_code": exit_code,
        "primary_failure": primary_failure,
    }


def _previous_status(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _normalize(fields, previous=None):
    unknown = set(fields) - set(STATUS_FIELDS)
    if unknown:
        raise ValueError("unknown state field rejected")
    row = {name: None for name in STATUS_FIELDS}
    row.update(fields)
    row["schema"] = 1
    row["updated_at"] = _now()
    row["pid"] = os.getpid() if row["pid"] is None else row["pid"]
    terminal = state_event(
        row["phase"], exit_code=row["exit_code"], reason=row["reason"],
        previous=previous,
    )
    # A caller may describe a failure in `reason`, but only a closed, recognised
    # code on the first terminal transition becomes machine-readable state.
    # Every later observation retains that first cause, including after restart.
    row["primary_failure"] = terminal["primary_failure"]
    for value in row.values():
        _safe(value)
    return row


def write_status(path: str, **fields) -> dict:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # The sidecar survives atomic replacement of status.json, so all coordinated
    # writers serialize read -> normalize -> replace as one transaction.
    lock_path = os.path.abspath(path) + ".lock"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            row = _normalize(fields, previous=_previous_status(path))
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix=".status.json.", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(row, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
            return row
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append_event(path: str, event: str, **fields) -> dict:
    row = _normalize(fields)
    row["event"] = _safe(event)
    row["ts"] = _now()
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return row


def _args():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("set", "event"):
        p = sub.add_parser(command)
        p.add_argument("path")
        if command == "event": p.add_argument("event")
        for name in STATUS_FIELDS:
            if name not in ("schema", "updated_at"):
                p.add_argument("--" + name.replace("_", "-"))
    return parser.parse_args()


def main():
    args = _args()
    fields = {name: getattr(args, name) for name in STATUS_FIELDS if hasattr(args, name) and getattr(args, name) is not None}
    for name in ("pid", "attempt", "process_start_ticks", "pgid"):
        if name in fields:
            fields[name] = int(fields[name])
    if args.command == "set":
        write_status(args.path, **fields)
    else:
        append_event(args.path, args.event, **fields)


if __name__ == "__main__":
    main()
