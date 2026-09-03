#!/usr/bin/env python3
"""REGRESSION LOCK — stopcheck must never block on stdin.

WHAT THIS COST. Free acceptance run 20260902T193433Z-v44 reached stage
`repair` with a clean startup, 4 verified clears and 123 upstream calls,
then died `rc=2 STAGE_STALLED`. Its transcript shows the target running the
arm's own gate tool from the shell:

    Shell {"command":"python3 /opt/sherlock-arm/log-rca/tools/stopcheck.py
      --work .../work --report .../work/report.md", "timeout":600000}
    Command timed out after 600000ms before it could complete.
    There was no output before it timed out.

Ten minutes, no output. The driver was right to call it a stall; the arm
was the thing that stalled.

ROOT CAUSE. `read_hook_input()` does a bare `sys.stdin.read()`. As a Stop
hook that is correct — qwen writes the event and closes the pipe. Called
from a SHELL, stdin is a pipe nobody ever writes to or closes, so the read
never returns. And `stopcheck.py`'s own guards cannot save it: the
`TOTAL_TIMEOUT = 50` deadline and its SIGALRM watchdog are both armed in
`main()` AFTER the read. A 50-second budget defended by a watchdog that is
not yet running is not a budget.

WHY A SHELL INVOCATION IS EXPECTED, not misuse: SKILL.md has the model run
its own gates, and v44 added the `--gate-tool` escape for exactly that. So
"don't call it that way" is not a fix — the input has to be gated.

PROVEN on the real installed arm before the fix:
    sleep 300 | stopcheck.py --work W --report W/report.md   -> rc 124, no output
    stopcheck.py --work W --report W/report.md < /dev/null   -> instant allow

RUN THIS BEFORE THE FIX: the open-stdin case hangs and the subprocess
timeout fires.
RUN THIS AFTER: it returns a decision within the stdin gate, every time.
"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
VERSION = os.environ.get("SHERLOCK_STOPCHECK_VERSION", "v44")
STOPCHECK = SHERLOCK / "skills" / VERSION / "tools" / "stopcheck.py"

# Generous: the gate itself must answer in a couple of seconds, and even a
# slow box has to come back well inside this. The pre-fix code does not
# return at all, so any finite bound reproduces the bug.
HANG_BOUND_S = 30


class StdinGate(unittest.TestCase):
    def test_open_stdin_does_not_hang(self):
        """A pipe nobody writes to must not stop the gate from answering."""
        # THE PIPE HAS TO BE HELD OPEN BY SOMEONE ELSE. `stdin=PIPE` plus
        # `communicate()` with no input CLOSES the write end immediately, so
        # the read returns EOF and the bug does not reproduce — the first
        # version of this test passed against the broken code for exactly
        # that reason. A separate long-lived writer reproduces what a shell
        # tool call actually hands the process: a pipe nobody writes to and
        # nobody closes.
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import time; time.sleep(300)"],
                                  stdout=subprocess.PIPE)
        proc = subprocess.Popen(
            [sys.executable, str(STOPCHECK), "--work", str(SHERLOCK)],
            stdin=holder.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        started = time.time()
        try:
            out, err = proc.communicate(timeout=HANG_BOUND_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            holder.kill()
            holder.communicate()
            self.fail(
                "stopcheck.py never answered with stdin held open for %ds — "
                "this is the 600s hang that killed free run "
                "20260902T193433Z-v44 (sys.stdin.read() before the watchdog "
                "is armed)" % HANG_BOUND_S)
        holder.kill()
        holder.communicate()
        took = time.time() - started
        self.assertTrue(
            out.strip(),
            "stopcheck.py answered nothing at all (stdout empty); the real "
            "run's symptom was «There was no output before it timed out»")
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertIn(payload.get("decision"), ("allow", "block"),
                      "stopcheck must emit a hook decision, got %r" % (out,))
        # It must not merely finish — it must finish because the gate closed,
        # not because something else happened to unblock it.
        self.assertLess(took, HANG_BOUND_S,
                        "answered only at the very edge of the bound")

    def test_closed_stdin_still_works(self):
        """The existing, always-worked path must be untouched."""
        proc = subprocess.run(
            [sys.executable, str(STOPCHECK), "--work", str(SHERLOCK)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=HANG_BOUND_S)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIn(payload.get("decision"), ("allow", "block"))

    def test_real_hook_payload_still_read(self):
        """A hook that writes its event and closes must still be parsed.

        The fix must gate the read, never skip it: if stopcheck stopped
        reading its hook input it would allow every Stop and the seatbelt
        would be gone.
        """
        event = json.dumps({"cwd": str(SHERLOCK)})
        proc = subprocess.run(
            [sys.executable, str(STOPCHECK)], input=event,
            capture_output=True, text=True, timeout=HANG_BOUND_S)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIn(payload.get("decision"), ("allow", "block"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
