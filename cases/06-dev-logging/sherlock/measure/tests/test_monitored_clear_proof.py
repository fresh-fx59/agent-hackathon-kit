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
        self.skill_root = self.observer / "skill"
        self.skill_root.mkdir()
        self.skill_body = "# Sherlock\n\nFollow the durable stage protocol.\n"
        (self.skill_root / "SKILL.md").write_text(
            "---\nname: sherlock\n---\n" + self.skill_body, encoding="utf-8")
        self.invocation = "/sherlock reseed exact"

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

    def expanded(self, invocation):
        return "Qwen skill expansion:\n" + self.skill_body + "\n" + invocation

    def chain(self, *, clear_source="clear", clear_session="new",
              invocation=None, prompt=None):
        invocation = self.invocation if invocation is None else invocation
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="old", monotonic=10, prompt="initial")
        anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
        self.append("session-start-events.jsonl", phase="SessionStart",
                    session=clear_session, monotonic=20, source=clear_source,
                    cwd="/workspace")
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session=clear_session, monotonic=30,
                    prompt=self.expanded(invocation) if prompt is None else prompt,
                    submitted_prompt=invocation)
        return anchor

    def proof(self, anchor):
        return DRIVE.monitored_clear_evidence(
            self.observer, self.nonce, anchor, self.invocation, self.skill_root)

    def test_exact_clear_and_single_combined_invocation_is_accepted(self):
        anchor = self.chain()
        proof = self.proof(anchor)
        self.assertEqual(proof["state"], "complete")
        self.assertEqual(proof["old_session_id"], "old")
        self.assertEqual(proof["new_session_id"], "new")
        self.assertEqual(proof["expected_invocation_sha256"],
                         hashlib.sha256(self.invocation.encode()).hexdigest())
        self.assertEqual(proof["skill_body_sha256"],
                         hashlib.sha256(self.skill_body.encode()).hexdigest())

    def test_terminal_newline_matches_exact_qwen_projection_and_retains_typed_hash(self):
        typed = "/sherlock\n\nreseed exact\n"
        submitted = "/sherlock\n\nreseed exact"
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="old", monotonic=10, prompt="initial")
        anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
        self.append("session-start-events.jsonl", phase="SessionStart",
                    session="new", monotonic=20, source="clear", cwd="/workspace")
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=30, prompt=self.expanded(typed),
                    submitted_prompt=submitted)

        proof = DRIVE.monitored_clear_evidence(
            self.observer, self.nonce, anchor, typed, self.skill_root)
        self.assertEqual(proof["expected_invocation_sha256"],
                         hashlib.sha256(typed.encode()).hexdigest())
        self.assertEqual(proof["submitted_invocation_sha256"],
                         hashlib.sha256(submitted.encode()).hexdigest())

    def test_startup_terminal_newline_keeps_typed_and_canonical_hashes(self):
        typed = "/sherlock\n\nstartup exact\n"
        submitted = "/sherlock\n\nstartup exact"
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=10, prompt=self.expanded(typed),
                    submitted_prompt=submitted)

        proof = DRIVE.monitored_start_evidence(
            self.observer, self.nonce, typed, self.skill_root)
        self.assertEqual(proof["expected_invocation_sha256"],
                         hashlib.sha256(typed.encode()).hexdigest())
        self.assertEqual(proof["submitted_invocation_sha256"],
                         hashlib.sha256(submitted.encode()).hexdigest())

    def test_empty_qwen_continuation_after_combined_invocation_is_ignored(self):
        anchor = self.chain()
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="new", monotonic=40, prompt="")
        rows = (self.observer / "root-boundary-events.jsonl").read_text().splitlines()
        continuation_raw = base64.b64decode(json.loads(rows[2])["input_base64"])
        self.assertEqual(json.loads(continuation_raw), {
            "hook_event_name": "UserPromptSubmit", "prompt": "", "session_id": "new"})
        self.assertEqual(self.proof(anchor)["state"], "complete")

    def test_second_actual_input_and_any_actual_preceding_input_refuse(self):
        for first in (True, False):
            with self.subTest(first=first):
                self.tearDown(); self.setUp()
                self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                            session="old", monotonic=10, prompt="initial")
                anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
                self.append("session-start-events.jsonl", phase="SessionStart",
                            session="new", monotonic=20, source="clear", cwd="/workspace")
                if first:
                    self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                                session="new", monotonic=30, prompt="wrong expansion",
                                submitted_prompt="/sherlock")
                    monotonic = 40
                else:
                    monotonic = 30
                self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                            session="new", monotonic=monotonic,
                            prompt=self.expanded(self.invocation),
                            submitted_prompt=self.invocation)
                if not first:
                    self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                                session="new", monotonic=40,
                                prompt="wrong actual", submitted_prompt="wrong actual")
                with self.assertRaises(DRIVE.ClearProofError):
                    self.proof(anchor)

    def test_wrong_session_arguments_or_expansion_refuse(self):
        cases = [
            {"clear_session": "old"},
            {"invocation": "/sherlock altered"},
            {"prompt": "unexpanded prompt"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.tearDown(); self.setUp()
                anchor = self.chain(**changes)
                with self.assertRaises(DRIVE.ClearProofError):
                    self.proof(anchor)

    def test_provider_message_counts_cannot_substitute_for_root_hooks(self):
        self.append("root-boundary-events.jsonl", phase="UserPromptSubmit",
                    session="old", monotonic=10, prompt="initial")
        anchor = DRIVE.capture_clear_anchor(self.observer, self.nonce)
        (self.observer / "provider-ledger.jsonl").write_text(
            '{"messages_count":2,"session":"child"}\n', encoding="utf-8")
        self.assertEqual(self.proof(anchor)["state"], "await_clear")

    def test_stale_anchor_and_tampered_raw_input_refuse(self):
        anchor = self.chain()
        stale = dict(anchor, input_sha256="0" * 64)
        with self.assertRaises(DRIVE.ClearProofError):
            self.proof(stale)
        path = self.observer / "root-boundary-events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]["input_sha256"] = "0" * 64
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaises(DRIVE.ClearProofError):
            self.proof(anchor)

    def test_zero_stage_budget_has_no_deadline(self):
        self.assertIsNone(DRIVE.stage_deadline(0, now=100.0))
        self.assertEqual(DRIVE.stage_deadline(15, now=100.0), 115.0)
        with self.assertRaises(ValueError):
            DRIVE.stage_deadline(-1, now=100.0)


if __name__ == "__main__":
    unittest.main()
