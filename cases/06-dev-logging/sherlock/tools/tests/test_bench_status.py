#!/usr/bin/env python3
"""Provider-free contract tests for the read-only bench status projection."""
import datetime as dt
import contextlib
import importlib.util
import io
import json
import hashlib
import hmac
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
TOOL = SHERLOCK / "eval" / "bench" / "bench-status.py"
MANIFEST_TEST = HERE / "test_run_manifest.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURES = load("bench_status_manifest_fixture", MANIFEST_TEST)


class BenchStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fx = FIXTURES.Fixture(self.temp.name)
        self.fx.stage()
        self.manifest = self.fx.create()

    def tearDown(self):
        self.temp.cleanup()

    def status(self, trace=None, *extra):
        trace = trace or self.fx.trace
        command = [sys.executable, str(TOOL), str(trace), "--commitment-file",
                   str(self.fx.commitment), "--commitment-key", str(self.fx.commitment_key), *extra]
        return subprocess.run(command, text=True, capture_output=True)

    def write_status(self, trace=None, **updates):
        trace = trace or self.fx.trace
        row = {"schema": 1, "run_tag": "run-001", "phase": "QWEN_RUNNING",
               "updated_at": "2026-08-20T10:00:00Z", "pid": 1, "attempt": 2,
               "dataset": "fixture", "arm": "v28", "trace_dir": str(trace), "detail": None,
               "session_id": None, "reason": None, "exit_code": None, "duration_s": None,
               "upstream_log": None, "inflight_path": None}
        row.update(updates)
        (Path(trace) / "status.json").write_text(json.dumps(row), encoding="utf-8")

    def projection(self, trace=None, *extra):
        result = self.status(trace, *extra, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def stage_target_probe_authority(self):
        """Build the smallest real target-probe authority graph, without HMACs."""
        root = Path(self.temp.name) / "target-probe-root"
        trace = root / "probe-work" / "runs" / "target-contract-probe"
        trace.mkdir(parents=True)
        nonce_root = root / "nonce-records"; nonce_root.mkdir()
        raw = {}
        for name in ("target-profile.json", "probe-budget.json", "probe-rate-snapshot.json",
                     "fixture-manifest.json", "input-package.json", "probe/prompt.txt"):
            value = {"schema": 1, "name": name}
            raw[name] = (json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                         if name.endswith(".json") else b"sealed raw qwen prompt\n")
            destination = trace / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw[name])
        sha = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
        manifest = {
            "schema": 1, "action": "target_contract_probe",
            "created_at": "2026-09-04T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
            "nonce": "a" * 64,
            "target_profile_sha256": sha["target-profile.json"],
            "probe_budget_sha256": sha["probe-budget.json"],
            "fixture_manifest_sha256": sha["fixture-manifest.json"],
            "input_package_sha256": sha["input-package.json"],
            "rate_snapshot_sha256": sha["probe-rate-snapshot.json"],
            "prompt_sha256": sha["probe/prompt.txt"],
        }
        manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        (trace / "probe-manifest.json").write_bytes(manifest_raw)
        nonce_path = nonce_root / ("a" * 64 + ".json")
        nonce_raw = (json.dumps({"nonce": manifest["nonce"], "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()},
                                sort_keys=True, separators=(",", ":")) + "\n").encode()
        nonce_path.write_bytes(nonce_raw)
        authorization = {
            "schema": 1, "action_nonce": manifest["nonce"],
            "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "nonce_record_path": os.path.realpath(nonce_path),
            "nonce_record_sha256": hashlib.sha256(nonce_raw).hexdigest(),
            "nonce_root": os.path.realpath(nonce_root), "probe_root": os.path.realpath(root),
            "trace_path": os.path.realpath(trace),
            "bench_status_sha256": hashlib.sha256(TOOL.read_bytes()).hexdigest(),
            "run_verdict_sha256": hashlib.sha256((SHERLOCK / "eval" / "bench" / "run-verdict.py").read_bytes()).hexdigest(),
        }
        (trace / "action-authorization.json").write_text(
            json.dumps(authorization, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return root, trace

    def write_validity(self, valid=True, reasons=(), full=False, **updates):
        row = {"schema": 1, "valid": valid, "reasons": list(reasons),
               "run_tag": "run-001", "manifest_sha256": self.manifest["manifest_sha256"],
               "candidate_sha256": "0" * 64}
        if full:
            row.update({"result_stream_sha256": "1" * 64, "upstream_sha256": "2" * 64,
                        "work_sha256": "3" * 64, "artifact_only": False,
                        "transport": {"exit_code": None, "status": "success", "duration_s": 1.5},
                        "usage": {"turns": 2, "input_tokens": 10, "output_tokens": 5},
                        "delivery": {"channel": "message", "relation": "message-only",
                                     "divergent": False, "message_sha256": "4" * 64,
                                     "message_bytes": 12, "artifact_sha256": "5" * 64,
                                     "artifact_bytes": 0, "delivered_sha256": "6" * 64,
                                     "delivered_bytes": 12},
                        "inventory": {}, "identity": {"requested_sha256": hashlib.sha256(
                            self.manifest["target"]["requested_model"].encode()).hexdigest(),
                        "returned_sha256": hashlib.sha256(
                            self.manifest["target"]["expected_returned_identity"].encode()).hexdigest(),
                        "successful_calls": 1}, "checkers": [], "contamination": {}})
        row.update(updates)
        validity = load("bench_status_validity_writer", SHERLOCK / "eval" / "bench" / "validate-run.py")
        row["hmac_sha256"] = validity.sign(row, self.fx.commitment_key.read_bytes())
        (self.fx.trace / "validity.json").write_text(json.dumps(row), encoding="utf-8")
        return row

    def write_link(self, parent, **updates):
        key = self.fx.commitment_key.read_bytes()
        row = {"schema": 1, "parent_trace": str(parent.resolve()),
               "parent_identity_sha256": hashlib.sha256(str(parent.resolve()).encode()).hexdigest(),
               "child_run_tag": "run-001", "child_trace": str(self.fx.trace.resolve()),
               "child_manifest_sha256": self.manifest["manifest_sha256"],
               "linked_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
               "key_id": hashlib.sha256(key).hexdigest()}
        row.update(updates)
        row["hmac_sha256"] = hmac.new(key, json.dumps(row, sort_keys=True,
            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        (parent / "controller-child.json").write_text(json.dumps(row), encoding="utf-8")
        return row

    def write_controller_receipt(self, **updates):
        key = self.fx.commitment_key.read_bytes()
        row = {
            "schema": 1, "run_tag": "run-001",
            "manifest_sha256": self.manifest["manifest_sha256"],
            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "attempts_charged": 3, "request_bytes": 4096,
            "input_tokens": None, "output_tokens": None, "wall_seconds": 12,
            "consecutive_provider_failures": 1,
            "limits": {"max_upstream_attempts": 10, "max_request_bytes": 10000,
                       "max_wall_seconds": 300,
                       "max_consecutive_provider_failures": 4},
            "verdict": "WITHIN", "reason": None,
            "key_id": hashlib.sha256(key).hexdigest(),
        }
        row.update(updates)
        row["hmac_sha256"] = hmac.new(
            key, json.dumps(row, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256).hexdigest()
        (self.fx.trace / "controller-receipt.json").write_text(json.dumps(row))
        return row

    def add_trace(self, name, tag, phase, updated):
        trace = self.fx.root / name
        manifest = self.fx.create(trace=str(trace), run_tag=tag)
        self.write_status(trace, run_tag=tag, phase=phase, updated_at=updated, trace_dir=str(trace))
        return trace, manifest

    def test_direct_authenticated_trace_projects_fixed_safe_shape(self):
        self.write_status()
        row = self.projection()
        self.assertEqual(set(row), {"schema", "selection", "run_tag", "phase", "updated_at", "dataset", "arm", "trace", "last_event", "attempt", "wrapper_exit_code", "primary_failure", "recovery", "upstream", "process", "target", "health", "budget", "validity", "delivery", "diagnostics"})
        self.assertEqual((row["selection"], row["phase"], row["run_tag"]), ("direct", "QWEN_RUNNING", "run-001"))
        self.assertEqual(row["process"], "unverified")
        self.assertEqual(row["budget"], "not_reported")

    def test_root_discovers_unique_newest_authenticated_active_child(self):
        root = Path(self.temp.name) / "runs"; root.mkdir()
        old, new = root / "old", root / "new"
        old.mkdir(); new.mkdir()
        for target in (old, new):
            for name in ("run-manifest.json",):
                (target / name).write_bytes((self.fx.trace / name).read_bytes())
        self.write_status(old, updated_at="2026-08-20T10:00:00Z")
        self.write_status(new, updated_at="2026-08-20T10:01:00Z")
        # Their sealed trace identities deliberately do not match: unsafe children are ignored.
        row = self.projection(root)
        self.assertEqual(row["selection"], "none")
        self.assertIn("NO_ACTIVE_RUN", row["diagnostics"])

    def test_status_projection_accepts_optional_persisted_primary_failure(self):
        """A schema-1 terminal snapshot can carry its immutable failure cause."""
        self.write_status(phase="RUN_FAILED", exit_code=2, reason="WRAPPER_NONZERO",
                          primary_failure="NO_PROGRESS")

        row = self.projection()

        self.assertEqual(row["phase"], "RUN_FAILED")
        self.assertEqual(row["primary_failure"], "NO_PROGRESS")

    def test_status_projection_rejects_unknown_primary_failure_vocabulary(self):
        """Authenticated snapshots cannot turn caller detail into machine semantics."""
        self.write_status(phase="RUN_FAILED", exit_code=2, reason="ordinary detail",
                          primary_failure="BILLED_999_RUB")

        row = self.projection()

        self.assertIsNone(row["primary_failure"])
        self.assertIn("STATUS_INVALID", row["diagnostics"])

    def test_tampered_manifest_never_projects_facts(self):
        self.write_status()
        path = self.fx.trace / "run-manifest.json"
        row = json.loads(path.read_text()); row["dataset"] = "tampered"
        path.write_text(json.dumps(row), encoding="utf-8")
        result = self.status(self.fx.trace, "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRACE_UNRESOLVED", result.stdout + result.stderr)

    def test_authenticated_controller_link_selects_only_its_child(self):
        parent = Path(self.temp.name) / "controller-runtime"; parent.mkdir()
        self.write_status()
        key = self.fx.commitment_key.read_bytes(); key_id = hashlib.sha256(key).hexdigest()
        row = {"schema": 1, "parent_trace": str(parent.resolve()),
               "parent_identity_sha256": hashlib.sha256(str(parent.resolve()).encode()).hexdigest(),
               "child_run_tag": "run-001", "child_trace": str(self.fx.trace.resolve()),
               "child_manifest_sha256": self.manifest["manifest_sha256"],
               "linked_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
               "key_id": key_id}
        row["hmac_sha256"] = hmac.new(key, json.dumps(row, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        (parent / "controller-child.json").write_text(json.dumps(row), encoding="utf-8")
        got = self.projection(parent, "--run-tag", "run-001")
        self.assertEqual((got["selection"], got["run_tag"]), ("link", "run-001"))
        row["child_run_tag"] = "other"; (parent / "controller-child.json").write_text(json.dumps(row), encoding="utf-8")
        self.assertNotEqual(self.status(parent, "--run-tag", "run-001").returncode, 0)

    def test_linked_controller_is_display_only_and_cannot_accept_its_child(self):
        """Controller DONE must not replace the validator's pending validity."""
        parent = self.fx.root / "controller"; parent.mkdir()
        self.write_link(parent)
        controller = {"schema": 1, "controller_id": "controller-1", "phase": "DONE",
                      "updated_at": "2026-08-20T10:00:00Z", "child_run_tag": "run-001",
                      "child_manifest_sha256": self.manifest["manifest_sha256"], "reason": None}
        (parent / "status.json").write_text(json.dumps(controller))
        self.write_status(phase="FINISHED_UNCHECKED")
        got = self.projection(parent, "--run-tag", "run-001")
        self.assertEqual(got["controller"], {"phase": "DONE",
                                               "updated_at": "2026-08-20T10:00:00Z",
                                               "reason": None})
        self.assertEqual(got["validity"]["state"], "pending")
        self.assertEqual(got["phase"], "FINISHED_UNCHECKED")

    def test_linked_controller_status_requires_exact_child_identity(self):
        parent = self.fx.root / "controller"; parent.mkdir(); self.write_link(parent)
        row = {"schema": 1, "controller_id": "controller-1", "phase": "READY",
               "updated_at": "2026-08-20T10:00:00Z", "child_run_tag": "other",
               "child_manifest_sha256": self.manifest["manifest_sha256"], "reason": None}
        (parent / "status.json").write_text(json.dumps(row))
        self.write_status()
        got = self.projection(parent, "--run-tag", "run-001")
        self.assertNotIn("controller", got)
        self.assertIn("CONTROLLER_INVALID", got["diagnostics"])

    def test_authenticated_controller_budget_receipt_is_projected(self):
        self.write_status(); self.write_controller_receipt()
        got = self.projection()
        self.assertEqual(got["budget"], {
            "state": "reported", "verdict": "WITHIN", "reason": None,
            "attempts_charged": 3, "request_bytes": 4096,
            "input_tokens": None, "output_tokens": None, "wall_seconds": 12,
            "consecutive_provider_failures": 1,
            "limits": {"max_upstream_attempts": 10, "max_request_bytes": 10000,
                       "max_wall_seconds": 300,
                       "max_consecutive_provider_failures": 4}})

    def test_bad_or_malformed_budget_receipt_is_invalid_never_zero(self):
        self.write_status()
        for mutation in ("bad-hmac", "non-null-tokens", "extra-field"):
            with self.subTest(mutation=mutation):
                row = self.write_controller_receipt()
                if mutation == "bad-hmac":
                    row["hmac_sha256"] = "0" * 64
                elif mutation == "non-null-tokens":
                    row["input_tokens"] = 0
                else:
                    row["free"] = True
                (self.fx.trace / "controller-receipt.json").write_text(json.dumps(row))
                self.assertEqual(self.projection()["budget"], "invalid")

    def test_duplicate_key_and_oversized_reason_receipts_are_invalid(self):
        self.write_status(); self.write_controller_receipt()
        receipt = self.fx.trace / "controller-receipt.json"
        raw = receipt.read_text().strip()
        receipt.write_text('{"schema":1,' + raw[1:] + "\n", encoding="utf-8")
        self.assertEqual(self.projection()["budget"], "invalid")
        self.write_controller_receipt(reason="X" * 65, verdict="EXCEEDED")
        self.assertEqual(self.projection()["budget"], "invalid")

    def test_validity_hmac_changes_pending_to_accepted(self):
        self.write_status()
        validity = load("bench_status_validity", SHERLOCK / "eval" / "bench" / "validate-run.py")
        key = self.fx.commitment_key.read_bytes()
        row = {"schema": 1, "valid": True, "reasons": [], "run_tag": "run-001",
               "manifest_sha256": self.manifest["manifest_sha256"], "candidate_sha256": "0" * 64}
        row["hmac_sha256"] = validity.sign(row, key)
        (self.fx.trace / "validity.json").write_text(json.dumps(row), encoding="utf-8")
        self.assertEqual(self.projection()["validity"]["state"], "accepted")

    def test_validity_missing_malformed_bad_hmac_and_rejected_are_distinct(self):
        self.write_status()
        self.assertEqual(self.projection()["validity"]["state"], "pending")
        cases = [({"raw": b"{"}, "invalid"),
                 ({"valid": False, "reasons": ["checker_failed"]}, "rejected"),
                 ({"valid": True, "hmac_sha256": "f" * 64}, "invalid")]
        for updates, expected in cases:
            with self.subTest(expected=expected):
                if "raw" in updates:
                    (self.fx.trace / "validity.json").write_bytes(updates["raw"])
                else:
                    row = self.write_validity(valid=updates.get("valid", True),
                                              reasons=updates.get("reasons", ()))
                    if "hmac_sha256" in updates:
                        row["hmac_sha256"] = updates["hmac_sha256"]
                        (self.fx.trace / "validity.json").write_text(json.dumps(row))
                got = self.projection()
                self.assertEqual(got["validity"]["state"], expected)
                if expected == "invalid": self.assertIn("VALIDITY_INVALID", got["diagnostics"])

    def test_validity_exact_schemas_delivery_and_identity_projection(self):
        self.write_status(); self.write_validity(full=True)
        got = self.projection()
        self.assertEqual(got["delivery"], {"channel": "message",
            "relation": "message-only", "divergent": False, "message_bytes": 12,
            "artifact_bytes": 0, "delivered_bytes": 12})
        self.assertEqual(got["target"]["identity"], "exact")
        for relation in ("none", "message-only", "file-only", "identical",
                         "file-repeats-message", "message-repeats-file", "divergent"):
            with self.subTest(relation=relation):
                base = self.write_validity(full=True)
                base["delivery"]["relation"] = relation
                base["delivery"]["divergent"] = relation == "divergent"
                validity = load("bench_status_delivery_writer", SHERLOCK / "eval" / "bench" / "validate-run.py")
                base["hmac_sha256"] = validity.sign(base, self.fx.commitment_key.read_bytes())
                (self.fx.trace / "validity.json").write_text(json.dumps(base))
                self.assertEqual(self.projection()["delivery"]["relation"], relation)
        invalid = [
            {"extra": 1},
            {"transport": {"exit_code": True, "status": "success", "duration_s": 1}},
            {"usage": {"turns": -1, "input_tokens": 1, "output_tokens": 1}},
            {"delivery": {"channel": "message", "relation": "message-only"}},
            {"identity": {"requested_sha256": "7" * 64, "returned_sha256": None,
                          "successful_calls": -1}},
        ]
        for update in invalid:
            with self.subTest(update=next(iter(update))):
                self.write_validity(full=True, **update)
                self.assertEqual(self.projection()["validity"]["state"], "invalid")

    def test_events_and_upstream_ignore_foreign_rows_and_project_exact_tag(self):
        self.write_status()
        rows = [{"run_tag": "other", "event": "ACCEPTED", "ts": "2026-08-20T10:01:00Z"},
                {"run_tag": "run-001", "event": "ATTEMPT_STARTED", "ts": "2026-08-20T10:02:00Z", "attempt": 3},
                {"run_tag": "run-001", "event": "RECOVERY_DECIDED", "ts": "2026-08-20T10:03:00Z"}]
        (self.fx.trace / "status-events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
        completed = [{"run_tag": "run-001", "status": 200, "returned_model": "qwen-real",
                      "requested_model": "qwen-target"},
                     {"run_tag": "other", "status": 200, "returned_model": "wrong",
                      "requested_model": "qwen-target"}]
        (self.fx.trace / "upstream-completed.jsonl").write_text("".join(json.dumps(x) + "\n" for x in completed), encoding="utf-8")
        got = self.projection()
        self.assertEqual(got["last_event"]["event"], "RECOVERY_DECIDED")
        self.assertEqual((got["recovery"], got["upstream"]["completed"], got["upstream"]["identity"]), ("scheduled", 1, "exact"))

    def test_event_tail_and_recovery_state_matrix(self):
        self.write_status()
        cases = [
            (["RECOVERY_DECIDED"], "scheduled"),
            (["RECOVERY_DECIDED", "ATTEMPT_STARTED"], "running"),
            (["RECOVERY_DECIDED", "ATTEMPT_STARTED", "ATTEMPT_FINISHED"], "complete"),
            (["RECOVERY_DECIDED", "RUN_FAILED"], "exhausted"),
        ]
        for events, expected in cases:
            with self.subTest(expected=expected):
                rows = [{"run_tag": "run-001", "event": event,
                         "ts": "2026-08-20T10:%02d:00Z" % i, "attempt": i}
                        for i, event in enumerate(events)]
                data = "".join(json.dumps(row) + "\n" for row in rows) + '{"ignored":'
                (self.fx.trace / "status-events.jsonl").write_text(data)
                got = self.projection()
                self.assertEqual((got["last_event"]["event"], got["recovery"]),
                                 (events[-1], expected))
        (self.fx.trace / "status-events.jsonl").write_text('{"run_tag":"run-001"}\n')
        got = self.projection(); self.assertEqual(got["recovery"], "unknown")
        self.assertIn("EVENT_INVALID", got["diagnostics"])
        (self.fx.trace / "status-events.jsonl").write_text(json.dumps({"run_tag": "run-001",
            "event": "FUTURE_FREEFORM", "ts": "2026-08-20T10:00:00Z"}) + "\n")
        got = self.projection(); self.assertEqual(got["last_event"]["event"], "unknown")
        self.assertIn("EVENT_UNKNOWN", got["diagnostics"])

    def test_inflight_and_completed_exact_tag_identity_matrix(self):
        self.write_status()
        requests = {
            "secret-one": {"started_at": "2026-08-20T10:00:00Z", "request_bytes": 10,
                "path": "/v1/messages", "requested_model": "qwen-target", "attempt": 1,
                "run_tag": "run-001", "pid": 2, "proxy_instance": "private"},
            "secret-two": {"started_at": "2026-08-20T10:00:01Z", "request_bytes": 11,
                "path": "/v1/messages", "requested_model": "qwen-target", "attempt": 1,
                "run_tag": "run-001", "pid": 3, "proxy_instance": "private"},
            "foreign": {"started_at": "2026-08-20T10:00:02Z", "request_bytes": 12,
                "path": "/v1/messages", "requested_model": "qwen-target", "attempt": 1,
                "run_tag": "other", "pid": 4, "proxy_instance": "private"}}
        (self.fx.trace / "upstream-inflight.json").write_text(json.dumps({"requests": requests}))
        completed = [{"run_tag": "run-001", "status": 200, "returned_model": "qwen-real",
                      "requested_model": "qwen-target"},
                     {"run_tag": "run-001", "status": 502, "returned_model": None,
                      "requested_model": "qwen-target"},
                     {"run_tag": "other", "status": 200, "returned_model": "wrong",
                      "requested_model": "qwen-target"}]
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            "".join(json.dumps(x) + "\n" for x in completed) + '{"partial":')
        got = self.projection()
        self.assertEqual(got["upstream"], {"inflight": 2, "completed": 2,
            "successful": 1, "identity": "exact"})
        self.assertNotIn("secret-one", json.dumps(got))
        requests["secret-one"]["requested_model"] = None
        (self.fx.trace / "upstream-inflight.json").write_text(json.dumps({"requests": requests}))
        self.assertEqual(self.projection()["upstream"]["inflight"], 2)
        for raw, expected in ((None, None), ('{"requests":{}}', 0), ('{"requests":[]}', None)):
            with self.subTest(raw=raw):
                path = self.fx.trace / "upstream-inflight.json"
                if raw is None: path.unlink(missing_ok=True)
                else: path.write_text(raw)
                got = self.projection(); self.assertEqual(got["upstream"]["inflight"], expected)

    def test_completed_wrong_mixed_unknown_and_malformed(self):
        self.write_status()
        cases = [([], "unknown"),
                 ([{"run_tag": "run-001", "status": 200, "returned_model": "other",
                    "requested_model": "qwen-target"}], "wrong"),
                 ([{"run_tag": "run-001", "status": 200, "returned_model": "qwen-real",
                    "requested_model": "qwen-target"}, {"run_tag": "run-001", "status": 200,
                    "returned_model": "other", "requested_model": "qwen-target"}], "mixed")]
        for rows, expected in cases:
            with self.subTest(expected=expected):
                (self.fx.trace / "upstream-completed.jsonl").write_text(
                    "".join(json.dumps(x) + "\n" for x in rows))
                self.assertEqual(self.projection()["upstream"]["identity"], expected)
        (self.fx.trace / "upstream-completed.jsonl").write_text('{}\n')
        self.assertIn("UPSTREAM_INVALID", self.projection()["diagnostics"])

    def test_link_auth_substitution_future_old_and_terminal_selection(self):
        self.write_status(phase="ACCEPTED")
        parent = self.fx.root / "parent"; parent.mkdir()
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)).isoformat().replace("+00:00", "Z")
        self.write_link(parent, linked_at=old)
        self.assertEqual(self.projection(parent, "--run-tag", "run-001")["phase"], "ACCEPTED")
        cases = [
            {"parent_identity_sha256": "0" * 64}, {"child_trace": str(parent)},
            {"child_manifest_sha256": "0" * 64}, {"key_id": "0" * 64},
            {"linked_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).isoformat().replace("+00:00", "Z")},
            {"extra": "x"},
        ]
        for updates in cases:
            with self.subTest(field=next(iter(updates))):
                self.write_link(parent, **updates)
                self.assertNotEqual(self.status(parent, "--run-tag", "run-001").returncode, 0)

    def test_link_rejects_duplicate_json_keys(self):
        self.write_status(); parent = self.fx.root / "parent"; parent.mkdir()
        row = self.write_link(parent)
        raw = json.dumps(row, separators=(",", ":"))
        raw = raw.replace('"child_run_tag":"run-001"',
                          '"child_run_tag":"run-001","child_run_tag":"run-001"')
        (parent / "controller-child.json").write_text(raw, encoding="utf-8")
        self.assertNotEqual(self.status(parent, "--run-tag", "run-001").returncode, 0)

        self.write_link(parent)
        self.assertNotEqual(self.status(parent, "--run-tag", "run-001",
                                        "--run-tag", "run-001").returncode, 0)
        self.assertNotEqual(self.status(parent, "--run-tag", "").returncode, 0)

    def test_link_rejects_commitment_and_key_substitution(self):
        self.write_status(); parent = self.fx.root / "parent"; parent.mkdir(); self.write_link(parent)
        wrong_file = self.fx.root / "wrong-ledger"; wrong_file.write_text("")
        result = subprocess.run([sys.executable, str(TOOL), str(parent), "--commitment-file",
            str(wrong_file), "--commitment-key", str(self.fx.commitment_key), "--run-tag", "run-001"],
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        wrong_key = self.fx.root / "wrong-key"; wrong_key.write_bytes(b"x" * 32); wrong_key.chmod(0o600)
        result = subprocess.run([sys.executable, str(TOOL), str(parent), "--commitment-file",
            str(self.fx.commitment), "--commitment-key", str(wrong_key), "--run-tag", "run-001"],
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_discovery_zero_newest_tie_terminal_malformed_and_immediate_only(self):
        root = self.fx.root / "runs"; root.mkdir()
        zero = self.projection(root); self.assertIn("NO_ACTIVE_RUN", zero["diagnostics"])
        old, _ = self.add_trace("runs/old", "old", "QWEN_RUNNING", "2026-08-20T10:00:00+00:00")
        new, _ = self.add_trace("runs/new", "new", "VERIFYING", "2026-08-20T12:00:00Z")
        terminal, _ = self.add_trace("runs/terminal", "terminal", "ACCEPTED", "2026-08-20T13:00:00Z")
        malformed, _ = self.add_trace("runs/malformed", "malformed", "QWEN_RUNNING", "not-a-time")
        nested = root / "nested"; nested.mkdir(); self.add_trace("runs/nested/deep", "deep", "QWEN_RUNNING", "2026-08-20T14:00:00Z")
        got = self.projection(root); self.assertEqual(got["run_tag"], "new")
        self.assertIn("DISCOVERY_STATUS_INVALID", got["diagnostics"])
        self.write_status(old, run_tag="old", updated_at="2026-08-20T12:00:00Z", trace_dir=str(old))
        got = self.projection(root); self.assertEqual(got["selection"], "none")
        self.assertIn("AMBIGUOUS_ACTIVE_RUN", got["diagnostics"])
        self.assertNotEqual(self.status(root, "--run-tag", "new").returncode, 0)
        self.assertEqual(self.projection(terminal)["phase"], "ACCEPTED")

    def test_discovery_rejects_symlink_and_entry_limit_without_partial_selection(self):
        root = self.fx.root / "runs"; root.mkdir(); self.add_trace("real", "real", "QWEN_RUNNING", "2026-08-20T10:00:00Z")
        os.symlink(self.fx.trace, root / "link")
        for index in range(257): (root / ("z%03d" % index)).mkdir()
        got = self.projection(root)
        self.assertEqual(got["selection"], "none"); self.assertIn("DISCOVERY_LIMIT", got["diagnostics"])

    def test_discovery_aggregate_limit_counts_even_rejected_manifests(self):
        root = self.fx.root / "runs"; root.mkdir()
        child = root / "bad"; child.mkdir(); (child / "run-manifest.json").write_text("{}")
        tool = load("bench_status_discovery_limit", TOOL); tool.MAX_DISCOVERY_BYTES = 1
        found, codes = tool.discover(str(root), str(self.fx.commitment), str(self.fx.commitment_key))
        self.assertIsNone(found); self.assertIn("DISCOVERY_LIMIT", codes)

    def test_process_proof_requires_all_five_exact_fields(self):
        tool = load("bench_status_process", TOOL); root = self.fx.root / "fake-root"; proc = root / "proc" / "321"
        proc.mkdir(parents=True); boot = root / "proc/sys/kernel/random"; boot.mkdir(parents=True)
        boot_bytes = b"boot-id\n"; (boot / "boot_id").write_bytes(boot_bytes)
        fields = ["321", "(worker)", "S", "1", "321"] + ["0"] * 16 + ["987"]
        (proc / "stat").write_text(" ".join(fields)); command = b"python\x00worker.py\x00"; (proc / "cmdline").write_bytes(command)
        exact = {"pid": 321, "process_start_ticks": 987, "pgid": 321,
                 "boot_id_sha256": hashlib.sha256(boot_bytes.strip()).hexdigest(),
                 "command_sha256": hashlib.sha256(command).hexdigest()}
        self.assertEqual(tool.process_proof(exact, str(root))[0], "confirmed")
        for field in exact:
            with self.subTest(field=field):
                changed = dict(exact); changed[field] = None if field == "pid" else 1
                self.assertNotEqual(tool.process_proof(changed, str(root))[0], "confirmed")
        self.assertEqual(tool.process_proof({"pid": 321}, str(root))[0], "unverified")
        self.assertEqual(tool.process_proof(exact, str(self.fx.root / "absent"))[0], "unverified")

    def test_health_states_and_bound_snapshot(self):
        tool = load("bench_status_health", TOOL)
        data = self.fx.health.read_bytes(); asset = self.manifest["health_receipt"]
        target = self.manifest["target"]; now = dt.datetime.now(dt.timezone.utc)
        self.assertEqual(tool.health_projection(data, asset, target, now), "healthy")
        row = json.loads(data)
        for update, expected in [({"expires_at": "2020-01-01T00:00:00Z"}, "stale"),
                                 ({"provider": "other"}, "mismatch"),
                                 ({"tools": 1}, "invalid")]:
            with self.subTest(expected=expected):
                changed = dict(row); changed.update(update); blob = json.dumps(changed).encode()
                changed_asset = {"path": asset["path"], "bytes": len(blob),
                                 "sha256": hashlib.sha256(blob).hexdigest()}
                self.assertEqual(tool.health_projection(blob, changed_asset, target, now), expected)
        self.assertEqual(tool.health_projection(None, asset, target, now), "unknown")

    def test_unknown_phase_artifact_only_and_health_symlink_fail_closed(self):
        self.write_status(phase="NEW_FUTURE_PHASE")
        got = self.projection()
        self.assertEqual(got["phase"], "UNKNOWN")
        self.assertIn("PHASE_UNKNOWN", got["diagnostics"])

        self.write_status()
        self.write_validity(valid=False, reasons=["transport_failed"], full=True,
                            artifact_only=True,
                            transport={"exit_code": 1, "status": "error", "duration_s": 1.5},
                            usage={"turns": None, "input_tokens": None, "output_tokens": None})
        got = self.projection()
        self.assertEqual(got["validity"], {"state": "rejected", "count": 1,
                                           "reason": "transport_failed"})
        self.assertEqual(got["delivery"]["channel"], "message")
        self.assertIn("ARTIFACT_ONLY", got["diagnostics"])

        receipt = self.fx.health
        saved = receipt.read_bytes()
        receipt.unlink()
        target = self.fx.root / "other-health.json"
        target.write_bytes(saved)
        os.symlink(target, receipt)
        got = self.projection()
        self.assertEqual(got["health"], "invalid")
        self.assertIn("HEALTH_INVALID", got["diagnostics"])

    def test_held_directory_detects_parent_path_swap(self):
        tool = load("bench_status_swap", TOOL)
        held = tool.HeldDir(str(self.fx.trace))
        moved = self.fx.root / "moved-trace"
        try:
            self.fx.trace.rename(moved)
            self.fx.trace.mkdir()
            with self.assertRaises(tool.Unsafe):
                held.check()
        finally:
            held.close()

    def test_transient_status_decode_and_sidecar_row_limits(self):
        tool = load("bench_status_limits", TOOL)
        row = {"schema": 1}

        class Transient:
            def __init__(self): self.reads = 0
            def read(self, _name, _maximum):
                self.reads += 1
                return b"{" if self.reads == 1 else json.dumps(row).encode()

        held = Transient()
        self.assertEqual(tool.json_read(held, "status.json", retry=True)[:2], ("ok", row))
        self.assertEqual(held.reads, 2)

        event = {"run_tag": "run-001", "event": "STAGING", "ts": "2026-08-20T10:00:00Z"}
        completed = {"run_tag": "run-001", "status": 200,
                     "returned_model": "qwen-real", "requested_model": "qwen-target"}
        (self.fx.trace / "status-events.jsonl").write_text(
            (json.dumps(event) + "\n") * 2, encoding="utf-8")
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            (json.dumps(completed) + "\n") * 2, encoding="utf-8")
        directory = tool.HeldDir(str(self.fx.trace))
        try:
            self.assertTrue(tool.jsonl_rows(directory, "status-events.jsonl", tool.MAX_EVENTS, 1)[1])
            self.assertTrue(tool.jsonl_rows(directory, "upstream-completed.jsonl", tool.MAX_COMPLETED, 1)[1])
            self.assertEqual(tool.json_read(directory, "status-events.jsonl", maximum=1)[0], "invalid")
        finally:
            directory.close()

    def test_sidecar_bounds_nofollow_redaction_and_performance_boundary(self):
        self.write_status(detail="token=super-secret")
        for name in ("status.json", "validity.json", "status-events.jsonl", "upstream-inflight.json", "upstream-completed.jsonl"):
            with self.subTest(name=name):
                path = self.fx.trace / name
                if path.exists(): path.unlink()
                os.symlink(self.fx.health, path)
                got = self.projection()
                self.assertNotIn("super-secret", json.dumps(got))
                path.unlink()
        tool = load("bench_status_perf", TOOL)
        with mock.patch.object(tool.MANIFEST, "inspect_corpus", side_effect=AssertionError("corpus opened")), \
             mock.patch.object(tool.MANIFEST, "file_asset", side_effect=AssertionError("asset opened")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.write_status(); self.assertEqual(tool.main([str(self.fx.trace), "--commitment-file",
                str(self.fx.commitment), "--commitment-key", str(self.fx.commitment_key), "--json"]), 0)

    def test_json_paths_are_bounded_and_secret_or_controls_are_redacted(self):
        self.write_status(); tool = load("bench_status_paths", TOOL)
        row = self.projection(); row["trace"]["path"] = "/tmp/token=hidden\npath"
        rendered = tool.render(row)
        self.assertNotIn("hidden", rendered); self.assertTrue(all(len(line) <= 100 for line in rendered.splitlines()))
        self.assertLessEqual(max(len(value) for value in row["trace"].values() if isinstance(value, str)), 4096)

    def test_invalid_sidecars_fail_closed_without_leaking_content(self):
        self.write_status()
        for name in ("validity.json", "status-events.jsonl", "upstream-completed.jsonl"):
            with self.subTest(name=name):
                (self.fx.trace / name).write_text('{"token":"hidden"}\n', encoding="utf-8")
                got = self.projection()
                self.assertNotIn("hidden", json.dumps(got))
                self.assertTrue(got["diagnostics"] or got["validity"]["state"] == "invalid")
                (self.fx.trace / name).unlink()

    def test_long_paths_are_preserved_in_phone_safe_wrapped_lines(self):
        self.write_status()
        very_long = self.fx.trace / ("x" * 180); very_long.mkdir()
        self.manifest["trace"]["path"] = str(very_long)
        # A resealed fake is still rejected; renderer is checked directly against a safe projection.
        tool = load("bench_status_renderer", TOOL)
        row = tool.project(str(self.fx.trace), json.loads((self.fx.trace / "run-manifest.json").read_text()), self.fx.commitment_key.read_bytes())
        row["trace"]["path"] = str(very_long)
        rendered = tool.render(row).splitlines()
        self.assertTrue(all(len(line) <= 100 for line in rendered))
        self.assertLessEqual(len(rendered), 7)
        self.assertGreaterEqual("".join(line for line in rendered if "x" in line).count("x"), 180)

    def test_renderer_is_phone_safe_and_does_not_mutate_trace(self):
        self.write_status(detail="x" * 500)
        self.write_validity(valid=False, reasons=["transport_failed"])
        before = {p.name: p.read_bytes() for p in self.fx.trace.iterdir()}
        result = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("transport_failed", result.stdout)
        self.assertGreaterEqual(len(result.stdout.splitlines()), 4)
        self.assertTrue(all(len(line) <= 100 for line in result.stdout.splitlines()))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.fx.trace.iterdir()})

    def test_target_probe_mode_accepts_only_the_canonical_nonce_bound_authority(self):
        root, trace = self.stage_target_probe_authority()
        result = subprocess.run([sys.executable, str(TOOL), str(trace), "--target-probe", "--json"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["selection"], "target-probe")
        self.assertTrue(row["authenticated"])
        self.assertEqual(row["authority"], "operator-approved-target-probe")
        self.assertEqual(row["health"], "not_applicable")
        self.assertEqual(row["validity"], "not_applicable")
        self.assertEqual(root / "probe-work" / "runs" / "target-contract-probe", trace)

    def test_target_probe_mode_binds_the_sealed_raw_prompt(self):
        _root, trace = self.stage_target_probe_authority()
        (trace / "probe" / "prompt.txt").write_bytes(b"replacement prompt\n")
        result = subprocess.run([sys.executable, str(TOOL), str(trace), "--target-probe", "--json"],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRACE_UNRESOLVED", result.stderr)


if __name__ == "__main__":
    unittest.main()
