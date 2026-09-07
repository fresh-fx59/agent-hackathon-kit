#!/usr/bin/env python3
"""Run a JSON argv without a shell; always record child failure or completion."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    parser.add_argument("--done", required=True)
    args = parser.parse_args()
    done = Path(args.done)
    if done.exists():
        raise SystemExit("refusing existing terminal receipt")
    row = {"status": "launch_failed", "exit_code": 2}
    try:
        command = json.loads(Path(args.command).read_text())
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("command must be a nonempty JSON string array")
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        print(json.dumps({"event": "child_started", "pid": child.pid}), flush=True)
        row["child_pid"] = child.pid
        row["exit_code"] = child.wait()
        row["status"] = "completed" if row["exit_code"] == 0 else "child_failed"
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        row["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with done.open("x") as stream:
            json.dump(row, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(row), flush=True)
    return row["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
