#!/usr/bin/env python3
"""Harness-side Stop-hook wrapper: log each decision, then pass it through.

Usage (Qwen settings): python3 "$SHERLOCK_STOP_HOOK_WRAPPER" python3 "$QWEN_SKILL_ROOT/tools/stopcheck.py"

The wrapped hook receives the same stdin bytes. Its stdout, stderr and exit code
are forwarded unchanged. One JSON line per invocation is appended to
$SHERLOCK_STOP_HOOK_LOG. A logging failure is reported on stderr and never
changes the hook's result. The skill's own stopcheck.py is not modified.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

CLIP = 2000


def summarize_input(data):
    summary = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    try:
        obj = json.loads(data.decode("utf-8")) if data.strip() else None
    except (ValueError, UnicodeDecodeError):
        return summary
    if isinstance(obj, dict):
        summary["keys"] = sorted(obj)
        for key in ("hook_event_name", "session_id", "stop_hook_active", "cwd"):
            if key in obj:
                summary[key] = obj[key]
    return summary


def parse_decision(stdout):
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "decision" in obj:
            return obj.get("decision"), obj.get("reason"), bool(obj.get("failedOpen"))
    return None, None, False


def main(argv):
    if not argv:
        sys.stderr.write("stop-hook-log: no hook command given\n")
        return 2
    data = sys.stdin.buffer.read()
    started = datetime.datetime.now(datetime.timezone.utc)
    try:
        proc = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rc, out, err, spawn_error = proc.returncode, proc.stdout, proc.stderr, None
    except OSError as exc:
        rc, out, err, spawn_error = 127, b"", str(exc).encode(), str(exc)
    sys.stdout.buffer.write(out); sys.stdout.buffer.flush()
    sys.stderr.buffer.write(err); sys.stderr.buffer.flush()
    log = os.environ.get("SHERLOCK_STOP_HOOK_LOG")
    if log:
        decision, reason, failed_open = parse_decision(out)
        row = {"ts": started.isoformat(), "command": argv, "input": summarize_input(data),
               "exit_code": rc, "decision": decision, "reason": reason,
               "failed_open": failed_open, "spawn_error": spawn_error,
               "stdout": out[:CLIP].decode("utf-8", "replace"),
               "stderr": err[:CLIP].decode("utf-8", "replace"),
               "duration_ms": int((datetime.datetime.now(datetime.timezone.utc) - started).total_seconds() * 1000)}
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            sys.stderr.write("stop-hook-log: could not append %s: %s\n" % (log, exc))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
