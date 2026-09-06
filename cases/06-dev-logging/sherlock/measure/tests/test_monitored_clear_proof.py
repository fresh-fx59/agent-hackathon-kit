#!/usr/bin/env python3
import base64
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "interactive_drive_clear_test", ROOT / "measure/interactive-drive.py")
DRIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVE)


class MonitoredClearProofTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.observer = Path(self.temp.name)
        self.nonce = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def append(self, name, *, phase, session, monotonic, **payload):
        raw = dict(payload, hook_event_name=phase, session_id=session)
        raw_bytes = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        path = self.observer / name
        sequence = len(path.read_text().splitlines()) if path.exists() else 0
        row = {
            "schema": 1, "run_nonce": self.nonce, "sequence": sequence,
            "phase": phase, "session_id": session,
            "input_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "input_base64": base64.b64encode(raw_bytes).decode(),
            "monotonic_ns": monotonic,
        }
        if phase == "SessionStart":
            row.update(source=payload["source"], cwd=payload["cwd"])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    def chain(self, *, clear_source="clear", clear_session="new",
              slash="/sherlock", reseed="resume exact"):
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="old", monotonic=10, prompt="initial")
        anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
        self.append("session-start-events.jsonl", phase="SessionStart",
                    session=clear_session, monotonic=20, source=clear_source,
                    cwd="/workspace")
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session=clear_session, monotonic=30,
                    prompt="expanded skill", submitted_prompt=slash)
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session=clear_session, monotonic=40,
                    prompt=reseed, submitted_prompt=reseed)
        return anchor

    def test_exact_clear_skill_and_reseed_chain_is_accepted(self):
        anchor = self.chain()
        proof = DRIVE.monitored_clear_evidence(
            self.observer, self.nonce, anchor, "/sherlock", "resume exact")
        self.assertEqual(proof["state"], "complete")
        self.assertEqual(proof["old_session_id"], "old")
        self.assertEqual(proof["new_session_id"], "new")

    def test_empty_continuations_without_submitted_prompt_wait_for_reseed(self):
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="old", monotonic=10, prompt="initial")
        anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
        self.append("session-start-events.jsonl", phase="SessionStart",
                    session="new", monotonic=20, source="clear", cwd="/workspace")
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=30, prompt="expanded skill",
                    submitted_prompt="/sherlock")
        # These are retained raw Qwen continuation hook inputs: prompt is empty
        # and submitted_prompt is absent because no human prompt was submitted.
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=40, prompt="")
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=50, prompt="")

        rows = (self.observer / "root-boundary-events.jsonl").read_text().splitlines()
        continuation_raw = base64.b64decode(json.loads(rows[2])["input_base64"])
        self.assertEqual(json.loads(continuation_raw), {
            "hook_event_name": "UserPromptSubmit", "prompt": "", "session_id": "new"})
        self.assertEqual(DRIVE.monitored_clear_evidence(
            self.observer, self.nonce, anchor, "/sherlock", "resume exact")["state"],
            "await_reseed")
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=60, prompt="resume exact",
                    submitted_prompt="resume exact")
        self.assertEqual(DRIVE.monitored_clear_evidence(
            self.observer, self.nonce, anchor, "/sherlock", "resume exact")["state"],
            "complete")

    def test_nonempty_submitted_prompt_and_wrong_session_empty_row_refuse(self):
        cases = [
            ("new", {"prompt": "wrong actual", "submitted_prompt": "wrong actual"}),
            ("other", {"prompt": ""}),
            ("new", {"prompt": "", "submitted_prompt": ""}),
        ]
        for session, payload in cases:
            with self.subTest(session=session, payload=payload):
                self.tearDown(); self.setUp()
                self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                            session="old", monotonic=10, prompt="initial")
                anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
                self.append("session-start-events.jsonl", phase="SessionStart",
                            session="new", monotonic=20, source="clear", cwd="/workspace")
                self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                            session="new", monotonic=30, prompt="expanded skill",
                            submitted_prompt="/sherlock")
                self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                            session=session, monotonic=40, **payload)
                with self.assertRaises(DRIVE.ClearProofError):
                    DRIVE.monitored_clear_evidence(
                        self.observer, self.nonce, anchor, "/sherlock", "resume exact")

    def test_provider_message_counts_cannot_substitute_for_root_hooks(self):
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="old", monotonic=10, prompt="initial")
        anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
        (self.observer / "provider-ledger.jsonl").write_text(
            '{"messages_count":2,"session":"child"}\n', encoding="utf-8")
        self.assertEqual(DRIVE.monitored_clear_evidence(
            self.observer, self.nonce, anchor, "/sherlock", "resume")["state"],
            "await_clear")

    def test_wrong_source_same_session_wrong_slash_and_wrong_reseed_refuse(self):
        cases = [
            {"clear_source": "startup"},
            {"clear_session": "old"},
            {"slash": "/other"},
            {"reseed": "wrong"},
        ]
        for index, changes in enumerate(cases):
            with self.subTest(changes=changes):
                self.tearDown(); self.setUp()
                anchor = self.chain(**changes)
                with self.assertRaises(DRIVE.ClearProofError):
                    DRIVE.monitored_clear_evidence(
                        self.observer, self.nonce, anchor, "/sherlock", "resume exact")

    def test_stale_anchor_and_tampered_raw_input_refuse(self):
        anchor = self.chain()
        stale = dict(anchor, input_sha256="0" * 64)
        with self.assertRaises(DRIVE.ClearProofError):
            DRIVE.monitored_clear_evidence(
                self.observer, self.nonce, stale, "/sherlock", "resume exact")
        path = self.observer / "root-boundary-events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]["input_sha256"] = "0" * 64
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaises(DRIVE.ClearProofError):
            DRIVE.monitored_clear_evidence(
                self.observer, self.nonce, anchor, "/sherlock", "resume exact")

    def test_zero_stage_budget_has_no_deadline(self):
        self.assertIsNone(DRIVE.stage_deadline(0, now=100.0))
        self.assertEqual(DRIVE.stage_deadline(15, now=100.0), 115.0)
        with self.assertRaises(ValueError):
            DRIVE.stage_deadline(-1, now=100.0)


if __name__ == "__main__":
    unittest.main()
