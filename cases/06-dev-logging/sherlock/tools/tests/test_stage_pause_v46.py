#!/usr/bin/env python3
"""A durable non-final stage handoff must let Qwen end its current turn."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SHERLOCK = Path(__file__).resolve().parents[2]
PACKAGE = SHERLOCK / "skills" / "v46"


class StagePauseV46Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS exposes the same temp directory through /var and /private/var;
        # marker safety deliberately requires the canonical workspace path.
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        self.prepare("# id\tverdict\n", "triage")

    def prepare(self, worklist, done, partial=False):
        self.work.mkdir(exist_ok=True)
        (self.work / "worklist.tsv").write_text(worklist, encoding="utf-8")
        subprocess.run([sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                        "init", "--work", str(self.work)], check=True,
                       capture_output=True, text=True)
        command = [sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                   "handoff", "--work", str(self.work), "--done", done]
        if partial:
            command.append("--partial")
        subprocess.run(command,
                       check=True, capture_output=True, text=True)
        marker = self.root / ".sherlock" / "active.json"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(json.dumps({
            "version": 36, "active": True, "workspace": str(self.root),
            "skill_root": str(PACKAGE.resolve()), "corpus": str(self.corpus),
            "out": str(self.work), "mode": "single", "worklists": ["worklist.tsv"],
        }) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def stop(self, session_id="stage-session", message="stage complete"):
        return subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "stopcheck.py")],
            input=json.dumps({"cwd": str(self.root), "hook_event_name": "Stop",
                              "session_id": session_id,
                              "last_assistant_message": message}),
            text=True, capture_output=True, cwd=self.root,
            env={**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)}, check=False)

    def mutate_receipt(self, mutate):
        checkpoint = self.work / "checkpoint.json"
        row = json.loads(checkpoint.read_text(encoding="utf-8"))
        mutate(row["pending_handoff"])
        checkpoint.write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_triage_handoff_pauses_without_retiring_active_marker(self):
        result = self.stop()
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "allow")
        self.assertIn("handoff", decision["reason"])
        self.assertTrue((self.root / ".sherlock" / "active.json").is_file())
        self.assertFalse((self.root / ".sherlock" / "completed.json").exists())

    def test_same_stop_event_is_idempotent_but_another_session_cannot_replay(self):
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "allow")
        retry = json.loads(self.stop().stdout)
        self.assertEqual(retry["decision"], "allow")
        self.assertIn("already accepted", retry["reason"])
        other_session = json.loads(self.stop("another-session").stdout)
        self.assertEqual(other_session["decision"], "block")
        self.assertIn("already consumed", other_session["reason"])
        changed_event = json.loads(self.stop(message="a different Stop event").stdout)
        self.assertEqual(changed_event["decision"], "block")
        self.assertIn("already consumed", changed_event["reason"])

    def test_changed_handoff_block_fails_closed(self):
        (self.work / "handoff.txt").write_text("forged\n", encoding="utf-8")
        decision = json.loads(self.stop().stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("does not match", decision["reason"])

    def test_invalid_receipt_kind_fails_closed(self):
        self.mutate_receipt(lambda receipt: receipt.__setitem__("kind", "forged"))
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "block")

    def test_invalid_receipt_state_fails_closed(self):
        self.mutate_receipt(lambda receipt: receipt.__setitem__("state", "forged"))
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "block")

    def test_receipt_boundary_sequence_must_be_current(self):
        self.mutate_receipt(
            lambda receipt: receipt.__setitem__("boundary_seq", receipt["boundary_seq"] + 1))
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "block")

    def test_changed_worklist_seal_fails_closed(self):
        (self.work / "worklist.tsv").write_text("# altered\n", encoding="utf-8")
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "block")

    def test_partial_stage_receipt_may_pause_with_unresolved_work(self):
        shutil.rmtree(self.work)
        self.prepare("# id\tverdict\nrow-1\t?\n", "triage", partial=True)
        decision = json.loads(self.stop().stdout)
        self.assertEqual(decision["decision"], "allow")
        self.assertTrue((self.root / ".sherlock" / "active.json").is_file())

    def test_draft_to_repair_receipt_may_pause(self):
        (self.work / "report.md").write_text("# Отчёт\n\n### Н-1\nФакт\n",
                                               encoding="utf-8")
        subprocess.run([sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                        "handoff", "--work", str(self.work), "--done", "draft"],
                       check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(self.stop().stdout)["decision"], "allow")

    def test_no_checkpoint_uses_existing_final_delivery_gate(self):
        (self.work / "checkpoint.json").unlink()
        decision = json.loads(self.stop().stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertNotIn("stage handoff", decision["reason"])

    def test_repair_to_done_remains_on_final_delivery_path(self):
        # Build the two earlier full transitions, then the final repair handoff.
        (self.work / "report.md").write_text("# Отчёт\n\n### Н-1\nФакт\n",
                                               encoding="utf-8")
        subprocess.run([sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                        "handoff", "--work", str(self.work), "--done", "draft"],
                       check=True, capture_output=True, text=True)
        subprocess.run([sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                        "handoff", "--work", str(self.work), "--done", "repair"],
                       check=True, capture_output=True, text=True)
        decision = json.loads(self.stop().stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertTrue((self.root / ".sherlock" / "active.json").is_file())


if __name__ == "__main__":
    unittest.main()
