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
NEAR_CAP = 0.95          # flag duration_ms >= 95 % of a known cap
STOPCHECK_MARGIN_S = 10  # v53 stopcheck ceiling = outer - 10 s


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def delivery_fields(data, cwd):
    """msg/report sizes and hashes for the loop detector (spec items 7, 9)."""
    out = {}
    try:
        obj = json.loads(data.decode("utf-8")) if data.strip() else {}
    except (ValueError, UnicodeDecodeError):
        obj = {}
    msg = obj.get("last_assistant_message") if isinstance(obj, dict) else None
    if isinstance(msg, str):
        out.update(msg_chars=len(msg), msg_sha256=_sha(msg.encode("utf-8")))
    base = obj.get("cwd") if isinstance(obj, dict) and isinstance(obj.get("cwd"), str) else cwd
    report = os.path.join(base, "work", "report.md")
    try:
        body = open(report, "rb").read()
        out.update(report_bytes=len(body), report_sha256=_sha(body),
                   report_chars=len(body.decode("utf-8", "replace")))
    except OSError:
        pass
    return out, base


def stopcheck_detail(base, started_ts):
    """The v53 per-gate detail JSON written during THIS invocation, if any."""
    root = os.environ.get("SHERLOCK_INDEX_ROOT") or os.path.join(base, "index")
    path = os.path.join(root, "verdicts", "stopcheck-detail.json")
    try:
        if os.stat(path).st_mtime < started_ts - 1:
            return None, None
        raw = open(path, "rb").read()
        return json.loads(raw.decode("utf-8")), raw
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None


def near_cap(duration_ms):
    try:
        outer = float(os.environ.get("SHERLOCK_STOP_HOOK_TIMEOUT_S", ""))
    except ValueError:
        return None
    hits = [(abs(duration_ms - cap * 1000), name) for name, cap in
            (("outer", outer), ("stopcheck_ceiling", outer - STOPCHECK_MARGIN_S))
            if cap > 0 and NEAR_CAP * cap * 1000 <= duration_ms <= 1.05 * cap * 1000]
    return min(hits)[1] if hits else None


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


VERDICT_KEY_NAME = "verdict-hmac.key"


def hook_env():
    """Give ONLY the Stop-hook child the verdict-signing key (v53 spec v4.2 item 2).

    The key lives next to the hook log in the harness trace dir (0700, file 0400),
    outside the Qwen workspace; Qwen and the model shell never get the variable.
    Residual: a same-uid process that finds the file can still read it.
    """
    env = dict(os.environ)
    log = os.environ.get("SHERLOCK_STOP_HOOK_LOG")
    if log:
        key = os.path.join(os.path.dirname(log), VERDICT_KEY_NAME)
        if os.path.isfile(key):
            env["SHERLOCK_VERDICT_KEY_FILE"] = key
    return env


def main(argv):
    if not argv:
        sys.stderr.write("stop-hook-log: no hook command given\n")
        return 2
    data = sys.stdin.buffer.read()
    started = datetime.datetime.now(datetime.timezone.utc)
    try:
        proc = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=hook_env())
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
            fields, base = delivery_fields(data, os.getcwd())
            row.update(fields)
            detail, raw = stopcheck_detail(base, started.timestamp())
            if detail is not None:
                row["stopcheck_detail"] = detail
                if isinstance(detail, dict):
                    for key in ("cache", "cache_key"):
                        if key in detail:
                            row[key] = detail[key]
                ddir = os.environ.get("SHERLOCK_STOP_HOOK_DETAIL_DIR")
                if ddir:
                    os.makedirs(ddir, exist_ok=True)
                    name = started.strftime("%Y%m%dT%H%M%S.%fZ") + ".json"
                    with open(os.path.join(ddir, name), "wb") as fh:
                        fh.write(raw)
                    row["stopcheck_detail_file"] = name
            row["duration_near_cap"] = near_cap(row["duration_ms"])
        except Exception as exc:  # observability never changes the hook result
            row["observability_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            sys.stderr.write("stop-hook-log: could not append %s: %s\n" % (log, exc))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
