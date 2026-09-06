#!/usr/bin/env python3
"""v49 admits tools only on the safe side of a durable handoff receipt."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SHERLOCK = Path(__file__).resolve().parents[2]
PACKAGE = SHERLOCK / "skills" / os.environ.get("SHERLOCK_TEST_PACKAGE", "v49")


class StageBoundaryV49Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        self.work.mkdir()
        (self.work / "worklist.tsv").write_text("# id\tverdict\n", encoding="utf-8")
        marker = self.root / ".sherlock" / "active.json"
        marker.parent.mkdir()
        marker.write_text(json.dumps({
            "version": 36, "active": True, "workspace": str(self.root),
            "skill_root": str(PACKAGE.resolve()), "corpus": str(self.corpus),
            "out": str(self.work), "mode": "single", "worklists": ["worklist.tsv"],
        }) + "\n", encoding="utf-8")
        self.run_checkpoint("init")
        self.run_checkpoint("handoff", "--done", "triage")

    def tearDown(self):
        self.temp.cleanup()

    def run_checkpoint(self, *args):
        return subprocess.run([sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                               *args, "--work", str(self.work)],
                              check=True, capture_output=True, text=True)

    def gate(self, session_id="old-session"):
        return subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "boundarycheck.py")],
            input=json.dumps({"cwd": str(self.root), "hook_event_name": "PreToolUse",
                              "session_id": session_id, "tool_name": "write_file",
                              "tool_input": {"path": "work/sentinel.txt", "content": "no"}}),
            text=True, capture_output=True, cwd=self.root,
            env={**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)}, check=False)

    def stop(self, session_id="old-session"):
        return subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "stopcheck.py")],
            input=json.dumps({"cwd": str(self.root), "hook_event_name": "Stop",
                              "session_id": session_id, "last_assistant_message": "handoff"}),
            text=True, capture_output=True, cwd=self.root,
            env={**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)}, check=False)

    def test_pending_handoff_denies_tool_with_qwen_pretool_shape(self):
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertFalse(result["continue"])
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertFalse((self.work / "sentinel.txt").exists())

    def test_pending_seal_mismatch_fails_closed(self):
        with (self.work / "worklist.tsv").open("a", encoding="utf-8") as handle:
            handle.write("tampered\n")
        self.assertFalse(json.loads(self.gate().stdout)["continue"])

    def test_malformed_checkpoint_fails_closed(self):
        (self.work / "checkpoint.json").write_text("{", encoding="utf-8")
        self.assertFalse(json.loads(self.gate().stdout)["continue"])

    def test_no_active_marker_preserves_unrelated_workspace(self):
        (self.root / ".sherlock" / "active.json").unlink()
        self.assertTrue(json.loads(self.gate().stdout)["continue"])

    def test_active_handoff_without_session_identity_fails_closed(self):
        done = self.gate()
        event = {"cwd": str(self.root), "hook_event_name": "PreToolUse",
                 "tool_name": "write_file", "tool_input": {}}
        result = subprocess.run([sys.executable, str(PACKAGE / "tools" / "boundarycheck.py")],
                                input=json.dumps(event), text=True, capture_output=True,
                                cwd=self.root, env={**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["continue"])

    def test_valid_final_handoff_does_not_claim_a_stage_pause(self):
        self.run_checkpoint("init")  # consumes the old receipt as the next stage begins
        (self.work / "report.md").write_text("### Н-1 finding\n", encoding="utf-8")
        self.run_checkpoint("handoff", "--done", "draft")
        self.run_checkpoint("init")
        (self.work / "report.md").write_text("### Н-1 finding\n", encoding="utf-8")
        self.run_checkpoint("handoff", "--done", "repair")
        self.assertTrue(json.loads(self.gate().stdout)["continue"])

    def test_consumed_handoff_blocks_old_session_and_allows_fresh_one(self):
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "allow")
        self.assertFalse(json.loads(self.gate("old-session").stdout)["continue"])
        self.assertTrue(json.loads(self.gate("fresh-session").stdout)["continue"])

    def test_consumed_receipt_allows_fresh_stage_worklist_change(self):
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "allow")
        with (self.work / "worklist.tsv").open("a", encoding="utf-8") as handle:
            handle.write("next-stage-row\n")
        self.assertTrue(json.loads(self.gate("fresh-session").stdout)["continue"])

    def test_consumed_receipt_missing_stop_identity_fails_closed(self):
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "allow")
        checkpoint = self.work / "checkpoint.json"
        row = json.loads(checkpoint.read_text(encoding="utf-8"))
        row["pending_handoff"].pop("stop_session_id")
        checkpoint.write_text(json.dumps(row) + "\n", encoding="utf-8")
        self.assertFalse(json.loads(self.gate("fresh-session").stdout)["continue"])

    def test_malformed_receipt_fails_closed(self):
        checkpoint = self.work / "checkpoint.json"
        row = json.loads(checkpoint.read_text(encoding="utf-8"))
        row["pending_handoff"]["kind"] = "forged"
        checkpoint.write_text(json.dumps(row) + "\n", encoding="utf-8")
        self.assertFalse(json.loads(self.gate().stdout)["continue"])


if __name__ == "__main__":
    unittest.main()
