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
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
VERSION = os.environ.get("SHERLOCK_STOPCHECK_VERSION", "v44")
STOPCHECK = SHERLOCK / "skills" / VERSION / "tools" / "stopcheck.py"

# Generous: the gate itself must answer in a couple of seconds, and even a
# slow box has to come back well inside this. The pre-fix code does not
# return at all, so any finite bound reproduces the bug.
HANG_BOUND_S = 30


def load_stopcheck():
    spec = importlib.util.spec_from_file_location("stopcheck_marker_lifecycle_v44", STOPCHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


class StrictMarkerLifecycle(unittest.TestCase):
    """A controlled run may seal only the marker validated by a real Stop."""

    def test_missing_marker_blocks_when_the_runner_requires_a_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            event = json.dumps({"cwd": raw, "hook_event_name": "Stop",
                                "last_assistant_message": "done"})
            env = dict(os.environ)
            env["SHERLOCK_STRICT_MARKER_LIFECYCLE"] = "1"
            proc = subprocess.run(
                [sys.executable, str(STOPCHECK)], input=event, env=env,
                capture_output=True, text=True, timeout=HANG_BOUND_S)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(payload.get("decision"), "block", payload)
            self.assertIn("active marker", payload.get("reason", ""))

    def test_success_archives_exact_marker_before_retirement(self):
        stopcheck = load_stopcheck()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            marker_dir = root / ".sherlock"
            marker_dir.mkdir()
            marker = marker_dir / "active.json"
            original = (json.dumps({
                "version": 36, "active": True, "workspace": str(root),
                "skill_root": "skill", "corpus": "corpus", "out": "work",
                "mode": "single", "worklists": ["worklist.tsv"],
            }, sort_keys=True) + "\n").encode()
            marker.write_bytes(original)
            stopcheck.read_hook_input = lambda: {"cwd": str(root)}
            stopcheck.evaluate_stop = lambda _event, _workspace, _deadline: (
                "allow", "Sherlock complete", str(marker))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"SHERLOCK_STRICT_MARKER_LIFECYCLE": "1"}), \
                    contextlib.redirect_stdout(stdout):
                rc = stopcheck.main()
            self.assertEqual(rc, 0)
            self.assertFalse(marker.exists())
            self.assertEqual((marker_dir / "completed.json").read_bytes(), original)

    def test_existing_archive_blocks_and_keeps_the_active_marker(self):
        stopcheck = load_stopcheck()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            marker_dir = root / ".sherlock"
            marker_dir.mkdir()
            marker = marker_dir / "active.json"
            marker.write_text('{"active":true}\n', encoding="utf-8")
            completed = marker_dir / "completed.json"
            completed.write_text("forged\n", encoding="utf-8")
            stopcheck.read_hook_input = lambda: {"cwd": str(root)}
            stopcheck.evaluate_stop = lambda _event, _workspace, _deadline: (
                "allow", "Sherlock complete", str(marker))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"SHERLOCK_STRICT_MARKER_LIFECYCLE": "1"}), \
                    contextlib.redirect_stdout(stdout):
                rc = stopcheck.main()
            payload = json.loads(stdout.getvalue().strip().splitlines()[-1])
            self.assertEqual(rc, 0)
            self.assertEqual(payload.get("decision"), "block", payload)
            self.assertTrue(marker.exists())
            self.assertEqual(completed.read_text(encoding="utf-8"), "forged\n")

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
