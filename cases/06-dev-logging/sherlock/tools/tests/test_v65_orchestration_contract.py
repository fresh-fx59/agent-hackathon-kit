"""Provider-free checks for the v65 stage-boundary contract.

The model launcher is outside this package.  These checks pin the executable
boundary enforcement and the shipped repair instructions that the launcher
must follow.
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "v65"
STOPCHECK = (SKILL / "tools" / "stopcheck.py").read_text(encoding="utf-8")
VERIFY = (SKILL / "reference" / "draft-and-verify.md").read_text(encoding="utf-8")
CHECKPOINT = (SKILL / "tools" / "checkpoint.py").read_text(encoding="utf-8")


class V65OrchestrationContractTest(unittest.TestCase):
    def test_repair_instructions_cap_check_rounds_at_two_and_require_fresh_session(self):
        self.assertRegex(VERIFY, r"CAP IN-SESSION.*ROUNDS AT 2")
        self.assertIn("A fresh `repair` session", VERIFY)
        self.assertIn("checkpoint.py handoff --work ./work --done repair --partial", VERIFY)

    def test_stage_boundary_requires_session_identity_and_binds_one_stop_event(self):
        self.assertIn('session = event.get("session_id")', STOPCHECK)
        self.assertIn("stage handoff Stop event lacks a session identity", STOPCHECK)
        self.assertIn('receipt.get("stop_session_id") == session', STOPCHECK)
        self.assertIn('receipt.get("stop_event_sha256") == event_hash', STOPCHECK)
        self.assertIn("stage handoff receipt was already consumed", STOPCHECK)

    def test_repair_is_a_real_stage_with_partial_handoff(self):
        self.assertIn('STAGES = ("triage", "draft", "repair", "done")', CHECKPOINT)
        self.assertIn('from_stage in ("triage", "draft", "repair")', STOPCHECK)
        self.assertIn('(from_stage, to_stage) == ("repair", "done")', STOPCHECK)


if __name__ == "__main__":
    unittest.main()
