#!/usr/bin/env python3
"""Contract checks for the installed-Qwen /clear PTY capture fixture."""
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "qwen_clear_reseed_pty_capture",
    ROOT / "tools" / "tests" / "qwen_clear_reseed_pty_capture.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClearReseedResultContract(unittest.TestCase):
    def test_requires_clear_session_transition_and_exact_reseed_prompt(self):
        rows = [
            {"event": "SessionStart", "session_id": "session-a", "source": "startup"},
            {"event": "UserPromptSubmit", "session_id": "session-a", "prompt": "initial"},
            {"event": "SessionStart", "session_id": "session-b", "source": "clear"},
            {"event": "UserPromptSubmit", "session_id": "session-b", "prompt": "reseed"},
        ]
        result = MODULE.assess_boundary(rows, "reseed")
        self.assertTrue(result["passed"])
        self.assertEqual(result["pre_clear_session_id"], "session-a")
        self.assertEqual(result["post_clear_session_id"], "session-b")

    def test_rejects_a_reseed_without_a_clear_session_start(self):
        rows = [
            {"event": "SessionStart", "session_id": "session-a", "source": "startup"},
            {"event": "UserPromptSubmit", "session_id": "session-a", "prompt": "initial"},
            {"event": "UserPromptSubmit", "session_id": "session-b", "prompt": "reseed"},
        ]
        result = MODULE.assess_boundary(rows, "reseed")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "CLEAR_SESSION_START_MISSING")

    def test_anchors_pre_clear_prompt_before_a_slash_skill_prompt(self):
        rows = [
            {"event": "SessionStart", "session_id": "session-a", "source": "startup"},
            {"event": "UserPromptSubmit", "session_id": "session-a", "prompt": "initial"},
            {"event": "SessionStart", "session_id": "session-b", "source": "clear"},
            {"event": "UserPromptSubmit", "session_id": "session-b", "prompt": "skill context", "submitted_prompt": "/sherlock"},
            {"event": "UserPromptSubmit", "session_id": "session-b", "prompt": "reseed"},
        ]
        result = MODULE.assess_boundary(rows, "reseed")
        self.assertTrue(result["passed"])
        self.assertEqual(result["pre_clear_session_id"], "session-a")


if __name__ == "__main__":
    unittest.main()
