#!/usr/bin/env python3
"""Focused terminal-outcome regressions for the sealed GPT-5.5 launcher."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "run_v52_gpt55_comparison", HERE / "run-v52-gpt55-comparison.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
CAPTURED_R2_LEDGER = Path(
    "/tmp/sherlock-v52-gpt55-winevtx-r2-final/control/trace/upstream.jsonl")


class TerminalClassificationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gpt55-terminal-test-"))
        self.trace = self.root / "trace"
        self.trace.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def ledger(self, rows):
        (self.trace / "upstream.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def failure(self, call, *args):
        with self.assertRaises(RUNNER.TerminalFailure) as raised:
            call(*args)
        return raised.exception

    def test_transport_rows_are_not_identity_mismatches(self):
        # This is the R2 failure shape: success on the required model, then a
        # disconnected request and a provider 502 without a returned identity.
        self.ledger([
            {"status": 200, "returned_model": "gpt-5.5"},
            {"status": None, "returned_model": None,
             "error": "Remote end closed connection without response"},
            {"status": 502, "returned_model": None,
             "upstream_error": "unknown provider for model gpt-5.5"},
        ])
        error = self.failure(RUNNER.validate_terminal_ledger, self.trace)
        self.assertEqual(error.status, "transport_failed")
        self.assertIn("2 failed", str(error))

    def test_successful_response_with_wrong_identity_is_identity_failure(self):
        self.ledger([{"status": 200, "returned_model": "other"}])
        error = self.failure(RUNNER.validate_terminal_ledger, self.trace)
        self.assertEqual(error.status, "identity_failed")

    def test_complete_exact_identity_ledger_is_accepted(self):
        self.ledger([{"status": 200, "returned_model": "gpt-5.5", "stream": True,
                      "stream_complete": True}])
        self.assertEqual(len(RUNNER.validate_terminal_ledger(self.trace)), 1)

    def test_explicit_strict_recovery_exempts_only_its_withheld_attempt(self):
        self.ledger([
            {"client_request_id": "request-a", "status": 200, "returned_model": None,
             "stream": True, "stream_complete": False, "strict_buffer_failure": True},
            {"client_request_id": "request-a", "status": 200, "returned_model": "gpt-5.5",
             "stream": True, "stream_complete": True, "strict_buffer_recovery": 1},
        ])
        self.assertEqual(len(RUNNER.validate_terminal_ledger(
            self.trace, strict_buffer_enabled=True)), 2)

    def test_strict_failure_without_a_matching_recovery_still_fails(self):
        self.ledger([{"client_request_id": "request-a", "status": 200, "returned_model": "gpt-5.5", "stream": True,
                      "stream_complete": False, "strict_buffer_failure": True}])
        error = self.failure(RUNNER.validate_terminal_ledger, self.trace, True)
        self.assertEqual(error.status, "transport_failed")

    def test_observer_skips_only_explicit_withheld_missing_identity(self):
        self.ledger([{"status": 200, "returned_model": None, "stream": True,
                      "stream_complete": False, "strict_buffer_failure": True}])
        self.assertIsNone(RUNNER.observe_trace(self.trace, strict_buffer_enabled=True))
        error = self.failure(RUNNER.observe_trace, self.trace)
        self.assertEqual(error.status, "identity_failed")

    def test_strict_mode_never_exempts_a_returned_wrong_identity(self):
        self.ledger([{"status": 200, "returned_model": "other", "stream": True,
                      "stream_complete": False, "strict_buffer_failure": True}])
        error = self.failure(RUNNER.observe_trace, self.trace, True)
        self.assertEqual(error.status, "identity_failed")

    def test_recovery_cannot_clear_a_later_unrelated_terminal_failure(self):
        self.ledger([
            {"client_request_id": "request-a", "status": 200, "returned_model": None,
             "stream": True, "stream_complete": False, "strict_buffer_failure": True},
            {"client_request_id": "request-a", "status": 200, "returned_model": "gpt-5.5",
             "stream": True, "stream_complete": True, "strict_buffer_recovery": 1},
            {"client_request_id": "request-b", "status": 200, "returned_model": None,
             "stream": True, "stream_complete": False, "strict_buffer_failure": True},
            # A recovery marker on another request must not clear request-b.
            {"client_request_id": "request-c", "status": 200, "returned_model": "gpt-5.5",
             "stream": True, "stream_complete": True, "strict_buffer_recovery": 1},
        ])
        error = self.failure(RUNNER.validate_terminal_ledger, self.trace, True)
        self.assertEqual(error.status, "transport_failed")

    def test_interleaved_requests_must_recover_in_their_own_client_order(self):
        self.ledger([
            {"client_request_id": "request-a", "status": 200, "returned_model": None,
             "stream": True, "stream_complete": False, "strict_buffer_failure": True},
            {"client_request_id": "request-b", "status": 200, "returned_model": None,
             "stream": True, "stream_complete": False, "strict_buffer_failure": True},
            {"client_request_id": "request-a", "status": 200, "returned_model": "gpt-5.5",
             "stream": True, "stream_complete": True, "strict_buffer_recovery": 1},
            {"client_request_id": "request-b", "status": 200, "returned_model": "gpt-5.5",
             "stream": True, "stream_complete": True, "strict_buffer_recovery": 1},
        ])
        self.assertEqual(len(RUNNER.validate_terminal_ledger(
            self.trace, strict_buffer_enabled=True)), 4)

    def test_prepare_binds_strict_buffer_transport(self):
        proxy = self.root / "proxy.py"
        qwen = self.root / "qwen"
        proxy.write_text("# proxy", encoding="utf-8")
        (self.root / "lane_guard.py").write_text("# guard", encoding="utf-8")
        qwen.write_text("# qwen", encoding="utf-8")
        args = SimpleNamespace(prepared_root=str(self.root), control_root=str(self.root / "control"),
                               qwen=str(qwen), proxy=str(proxy),
                               upstream_base="http://127.0.0.1:8317/v1",
                               authorization="approved")
        with patch.object(RUNNER, "prepared_inventory", return_value=(self.root, [])):
            RUNNER.prepare(args)
        manifest = RUNNER.read_json(self.root / "control" / "manifest.json")
        self.assertEqual(manifest["transport"], {
            "mode": "strict_buffer_retry", "retry_max": 2, "deadline_seconds": 600})

    @unittest.skipUnless(CAPTURED_R2_LEDGER.is_file(), "captured R2 ledger unavailable")
    def test_captured_r2_ledger_classifies_as_transport_failure(self):
        error = self.failure(RUNNER.validate_terminal_ledger, CAPTURED_R2_LEDGER.parent)
        self.assertEqual(error.status, "transport_failed")

    def qwen_output(self, events):
        path = self.root / "qwen-output.json"
        path.write_text(json.dumps(events), encoding="utf-8")
        return path

    def test_api_error_overrides_qwen_success_wrapper(self):
        output = self.qwen_output([
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "[API Error: stream ID 33; INTERNAL_ERROR]"}]}},
            {"type": "result", "subtype": "success", "is_error": False},
        ])
        error = self.failure(RUNNER.validate_qwen_output, output)
        self.assertEqual(error.status, "qwen_protocol_failed")

    def metadata(self, verdict, gates, *, inputs_changed=False):
        report = self.work / "report.md"
        worklist = self.work / "worklist.tsv"
        rules = self.work / "rules.tsv"
        report.write_text("report", encoding="utf-8")
        worklist.write_text("worklist", encoding="utf-8")
        rules.write_text("rules", encoding="utf-8")
        receipt = self.work / "validation" / "20260907T000000Z-test"
        receipt.mkdir(parents=True)
        payload = {
            "verdict": verdict, "inputs_changed_during_validation": inputs_changed,
            "inputs_after": {
                "report": {"sha256": RUNNER.file_digest(report)},
                "worklist": {"sha256": RUNNER.file_digest(worklist)},
                "rules": {"sha256": RUNNER.file_digest(rules)},
            },
            "gates": gates,
        }
        (receipt / "metadata.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_blocking_validator_rejects_format_only_report(self):
        self.metadata("blocking", {
            "reportcheck": {"exit_code": 0, "parsed_blocking": 0},
            "citecheck": {"exit_code": 1, "parsed_blocking": 193},
        })
        error = self.failure(RUNNER.validate_finalizer_receipt, self.work)
        self.assertEqual(error.status, "validation_failed")

    def test_clean_validator_receipt_must_bind_current_report_hash(self):
        self.metadata("clean", self.clean_gates())
        (self.work / "report.md").write_text("changed", encoding="utf-8")
        error = self.failure(RUNNER.validate_finalizer_receipt, self.work)
        self.assertEqual(error.status, "validation_failed")

    def test_clean_validator_receipt_bound_to_report_is_accepted(self):
        self.metadata("clean", self.clean_gates())
        self.assertIsNone(RUNNER.validate_finalizer_receipt(self.work))

    def clean_gates(self):
        return {name: {"exit_code": 0, "parsed_blocking": 0}
                for name in RUNNER.FINALIZER_GATES}

    def test_clean_receipt_missing_required_gate_is_rejected(self):
        gates = self.clean_gates()
        del gates["statecheck"]
        self.metadata("clean", gates)
        error = self.failure(RUNNER.validate_finalizer_receipt, self.work)
        self.assertEqual(error.status, "validation_failed")

    def test_clean_receipt_must_bind_current_worklist_and_rules_hashes(self):
        self.metadata("clean", self.clean_gates())
        (self.work / "worklist.tsv").write_text("changed", encoding="utf-8")
        error = self.failure(RUNNER.validate_finalizer_receipt, self.work)
        self.assertEqual(error.status, "validation_failed")


if __name__ == "__main__":
    unittest.main()
