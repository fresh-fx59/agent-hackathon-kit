#!/usr/bin/env python3
"""Boot all four mock services, wait until healthy, stop them on Ctrl-C.

    python3 mocks/run_all.py

Ports (override with env vars): tracker TRACKER_PORT=8801, quality
QUALITY_PORT=8802, forge FORGE_PORT=8803, tms TMS_PORT=8804.

Mid-build friendly: a mock whose app.py has not been written yet is reported
as MISSING and skipped -- the rest still come up.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# (service name, port env var, default port)
MOCKS = [
    ("tracker", "TRACKER_PORT", 8801),
    ("quality", "QUALITY_PORT", 8802),
    ("forge", "FORGE_PORT", 8803),
    ("tms", "TMS_PORT", 8804),
]

HEALTH_TIMEOUT_S = 20


def health_ok(port):
    """True when GET /health answers {"ok": true}."""
    req = urllib.request.Request(
        "http://127.0.0.1:%d/health" % port,
        headers={"User-Agent": "agent-hackathon-kit/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=1) as resp:
            return json.load(resp).get("ok") is True
    except Exception:
        return False


def terminate(procs):
    """SIGTERM every child, escalate to SIGKILL after a grace period."""
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 5
    for proc in procs:
        remaining = max(0.1, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main():
    procs = []
    entries = []  # (name, port, proc_or_None, status)

    for name, env_var, default_port in MOCKS:
        port = int(os.environ.get(env_var, default_port))
        app_path = os.path.join(HERE, name, "app.py")
        if not os.path.exists(app_path):
            entries.append([name, port, None, "MISSING (mocks/%s/app.py not written yet)" % name])
            continue
        env = dict(os.environ)
        env[env_var] = str(port)
        proc = subprocess.Popen([sys.executable, app_path], env=env)
        procs.append(proc)
        entries.append([name, port, proc, "starting"])

    if not procs:
        print("No mock apps found under %s -- nothing to run." % HERE)
        return 1

    # Poll every started service until healthy (or its process dies).
    deadline = time.time() + HEALTH_TIMEOUT_S
    while time.time() < deadline:
        pending = False
        for entry in entries:
            name, port, proc, status = entry
            if status != "starting":
                continue
            if proc.poll() is not None:
                entry[3] = "DOWN (exited with code %s)" % proc.returncode
            elif health_ok(port):
                entry[3] = "UP"
            else:
                pending = True
        if not pending:
            break
        time.sleep(0.3)
    for entry in entries:
        if entry[3] == "starting":
            entry[3] = "DOWN (no /health answer within %ds)" % HEALTH_TIMEOUT_S

    print()
    print("%-10s %-6s %s" % ("service", "port", "status"))
    print("%-10s %-6s %s" % ("-" * 8, "-" * 5, "-" * 6))
    for name, port, _proc, status in entries:
        print("%-10s %-6s %s" % (name, port, status))
    print()

    if any(status.startswith("DOWN") for _n, _p, _pr, status in entries):
        print("Some services failed to start -- stopping everything.")
        terminate(procs)
        return 1

    print("Mocks are up. Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
            for name, port, proc, status in entries:
                if proc is not None and proc.poll() is not None:
                    print("Service %r died unexpectedly (code %s) -- stopping everything."
                          % (name, proc.returncode))
                    terminate(procs)
                    return 1
    except KeyboardInterrupt:
        print("\nStopping mocks...")
    terminate(procs)
    print("All mocks stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
