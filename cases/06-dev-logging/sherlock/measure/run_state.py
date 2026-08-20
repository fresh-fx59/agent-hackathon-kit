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
                 "process_start_ticks", "pgid", "boot_id_sha256", "command_sha256")
SECRET_MARKERS = re.compile(r"(?:bearer\s+|(?:sk|ghp|glpat|xox[baprs])-|AKIA[0-9A-Z]{16}|-----BEGIN .*PRIVATE KEY-----|(?:password|token|api[_-]?key)\s*[:=])", re.I)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe(value):
    if isinstance(value, (dict, list, tuple, set)):
        raise ValueError("non-scalar value rejected")
    if isinstance(value, str) and SECRET_MARKERS.search(value):
        raise ValueError("secret-shaped value rejected")
    return value


def _normalize(fields):
    unknown = set(fields) - set(STATUS_FIELDS)
    if unknown:
        raise ValueError("unknown state field rejected")
    row = {name: None for name in STATUS_FIELDS}
    row.update(fields)
    row["schema"] = 1
    row["updated_at"] = _now()
    row["pid"] = os.getpid() if row["pid"] is None else row["pid"]
    for value in row.values():
        _safe(value)
    return row


def write_status(path: str, **fields) -> dict:
    row = _normalize(fields)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".status.json.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(row, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return row


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
