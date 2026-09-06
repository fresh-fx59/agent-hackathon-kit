#!/usr/bin/env python3
"""Exact monitored acceptance outranks a stale Qwen rejection repaint.

V50 fixture r1 retained ``Unknown command: /sherlock`` in a repaint after the
matching UserPromptSubmit hook had already proved Qwen accepted the invocation.
The driver must not type the task a second time in that same session.  A hook is
not a blanket override: absence of the proof still keeps the genuine rejection
retry, and a contradictory proof remains an error for drive() to report as rc8.
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER_PATH = HERE.parent / "interactive-drive.py"
spec = importlib.util.spec_from_file_location("interactive_drive", DRIVER_PATH)
DRIVE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DRIVE)


class FakeSession:
    type_skill = DRIVE.Session.type_skill

    def __init__(self, screen_outputs, on_wait_ready=None):
        self._temporary = tempfile.TemporaryDirectory()
        self.transcript_path = os.path.join(self._temporary.name, "screen.log")
        self.transcript = open(self.transcript_path, "ab+")
        self.screen_outputs = iter(screen_outputs)
        self.on_wait_ready = on_wait_ready
        self.invocations = []
        self.ready_waits = 0

    def close(self):
        self.transcript.close()
        self._temporary.cleanup()

    def type(self, invocation, settle=1.5):
        self.invocations.append(invocation)
        with open(self.transcript_path, "ab") as output:
            output.write(next(self.screen_outputs).encode("utf-8"))
        self.transcript.seek(0, os.SEEK_END)

    def pump(self, _seconds):
        pass

    def wait_ready(self):
        self.ready_waits += 1
        if self.on_wait_ready:
            self.on_wait_ready()
        return True


class SkillAcceptanceStaleBannerV50Test(unittest.TestCase):
    needle = "Unknown command: /sherlock"

    def run_skill(self, session, accepted):
        events = []
        try:
            result = session.type_skill("/sherlock", "task", 0,
                                        lambda kind, detail: events.append((kind, detail)),
                                        attempts=2, accepted=accepted)
            return result, events
        finally:
            session.close()

    def test_delayed_exact_acceptance_after_stale_banner_prevents_retry(self):
        calls = [0]

        def accepted():
            calls[0] += 1
            return calls[0] >= 3

        session = FakeSession([self.needle])
        result, events = self.run_skill(session, accepted)
        self.assertTrue(result)
        self.assertEqual(session.invocations, ["/sherlock task"])
        self.assertNotIn("skill_command_rejected", [event[0] for event in events])

    def test_acceptance_during_readiness_wait_prevents_next_submission(self):
        proof_ready = [False]
        session = FakeSession([self.needle],
                              on_wait_ready=lambda: proof_ready.__setitem__(0, True))
        result, events = self.run_skill(session, lambda: proof_ready[0])
        self.assertTrue(result)
        self.assertEqual(session.invocations, ["/sherlock task"])
        self.assertEqual([event[0] for event in events].count("skill_command_rejected"), 1)
        self.assertEqual(session.ready_waits, 1)

    def test_no_exact_acceptance_retains_real_unknown_command_retry(self):
        original = DRIVE.SKILL_REJECT_PROBE_S
        DRIVE.SKILL_REJECT_PROBE_S = 0
        try:
            session = FakeSession([self.needle, "accepted command output"])
            result, events = self.run_skill(session, lambda: False)
        finally:
            DRIVE.SKILL_REJECT_PROBE_S = original
        self.assertTrue(result)
        self.assertEqual(session.invocations, ["/sherlock task", "/sherlock task"])
        self.assertEqual([event[0] for event in events].count("skill_command_rejected"), 1)

    def test_contradictory_exact_proof_is_not_converted_to_rejection(self):
        session = FakeSession([self.needle])
        try:
            with self.assertRaises(DRIVE.ClearProofError):
                session.type_skill("/sherlock", "task", 0, lambda *_: None,
                                   attempts=2,
                                   accepted=lambda: (_ for _ in ()).throw(
                                       DRIVE.ClearProofError("wrong root hook")))
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
