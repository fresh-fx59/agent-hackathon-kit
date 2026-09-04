#!/usr/bin/env python3
"""Provider-free tests for the full paid-run admission boundary."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "eval" / "bench" / "paid-admission.py"
NOW = dt.datetime(2026, 9, 4, 12, 0, tzinfo=dt.timezone.utc)


def load_module():
    spec = importlib.util.spec_from_file_location("paid_admission", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.module = load_module()
        self.harness = self.root / "harness-acceptance.json"
        self.target = self.root / "target-contract-receipt.json"
        self.checksum = self.root / "target-contract-receipt.json.sha256"
        self.profile = self.root / "target-profile.json"
        self.settings = self.root / "corporate-settings.json"
        self.inputs = self.root / "full-input-package.json"
        self.budget = self.root / "full-run-budget.json"
        self.manifest = self.root / "paid-admission-manifest.json"
        self.nonce_root = self.root / "admission-nonces"
        self.module.DEFAULT_NONCE_ROOT = self.nonce_root

        self.write_json(self.settings, {"model": {"name": "fixture"}})
        gates = {name: (str(index) * 64) for index, name in enumerate(
            ("reportcheck", "citecheck", "statecheck", "triagecheck"), start=2)}
        bindings = {name: "1" * 64 for name in self.module.HARNESS_BINDINGS}
        bindings.update({
            "settings_sha256": sha256(self.settings),
            "tool_schema_sha256": "6" * 64,
            "skill_v44_sha256": "7" * 64,
            "report_gate_program_sha256": gates["reportcheck"],
            "citation_gate_program_sha256": gates["citecheck"],
            "state_gate_program_sha256": gates["statecheck"],
            "triage_gate_program_sha256": gates["triagecheck"],
        })
        self.write_json(self.harness, {
            "schema": 2,
            "accepted": True,
            "proof_scope": "harness_only",
            "matrix_sha256": "8" * 64,
            "qualification_manifest_sha256": "9" * 64,
            "trace": "run-fixture",
            "bindings": bindings,
            "free_run": {"id": "run-fixture", "input_manifest_sha256": "1" * 64,
                         "terminal_verdict": "ACCEPTED"},
            "free_model_observations": {"requested": "gpt-5.5", "sent": "gpt-5.5",
                                        "returned": ["gpt-5.5"]},
        })
        self.write_json(self.profile, {
            "schema": 1,
            "provider_base_url": "https://paid.invalid/v1",
            "route": "paid",
            "secret_ref": "SHERLOCK_API_KEY",
            "requested_model": "deepseek-v4-flash",
            "expected_returned_identity": "deepseek-v4-flash",
            "identity_mode": "provider_pinned_version",
            "temperature": 0,
            "top_p": 1,
            "max_output_tokens": 32000,
            "session_token_limit": 262000,
            "cache": {"enabled": True},
            "interactive": {"enabled": True},
            "qwen": {"cli": "/usr/bin/true"},
            "limits": {"requests": 512},
            "settings_sha256": sha256(self.settings),
            "system_prompt_sha256": "5" * 64,
            "skill_sha256": "7" * 64,
            "tool_schema_sha256": "6" * 64,
            "gate_sha256": gates,
            "lane_guard": {"enabled": True},
        })
        self.write_json(self.inputs, {
            "schema": 1,
            "comparison": {"status": "not_requested", "undeclared_differences": []},
        })
        self.write_json(self.budget, {
            "schema": 1,
            "max_upstream_attempts": 512,
            "max_request_bytes": 536870912,
            "max_wall_seconds": 4500,
            "max_consecutive_provider_failures": 3,
            "context_window": 262000,
            "max_output_tokens": 32000,
            "session_token_limit": 262000,
            "request_timeout_ms": 900000,
        })
        self.write_json(self.target, {
            "schema": 1,
            "accepted": True,
            "proof_scope": "representative_sample_only",
            "created_at": "2026-09-04T11:30:00Z",
            "expires_at": "2026-09-04T13:00:00Z",
            "nonce": "a" * 64,
            "target_profile_sha256": sha256(self.profile),
            "requested_model": "deepseek-v4-flash",
            "sent_model": "deepseek-v4-flash",
            "returned_identities": ["deepseek-v4-flash"],
            "identity_assurance": "provider_pinned_version",
            "authenticated": True,
            "authority": "operator-approved-target-probe",
        })
        self.refresh_checksum()
        self.write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_json(path: Path, value: object):
        path.write_bytes(canonical(value))

    def refresh_checksum(self):
        self.checksum.write_text(sha256(self.target) + "\n", encoding="ascii")

    def write_manifest(self, **changes):
        row = {
            "schema": 1,
            "action": "full_paid_run",
            "created_at": "2026-09-04T11:45:00Z",
            "expires_at": "2026-09-04T12:30:00Z",
            "nonce": "b" * 64,
            "harness_receipt_sha256": sha256(self.harness),
            "target_receipt_sha256": sha256(self.target),
            "target_receipt_checksum_sha256": sha256(self.checksum),
            "target_profile_sha256": sha256(self.profile),
            "full_input_package_sha256": sha256(self.inputs),
            "full_run_budget_sha256": sha256(self.budget),
            "accept_alias_identity_risk": False,
        }
        row.update(changes)
        self.write_json(self.manifest, row)

    def assert_failure(self, code: str, operation=None):
        operation = operation or (lambda: self.module.verify_admission(self.manifest, now=NOW))
        with self.assertRaises(self.module.AdmissionFailure) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def consume(self, approval=None):
        return self.module.consume_admission(
            self.manifest,
            now=NOW,
            operator_approved_full=approval or sha256(self.manifest),
        )

    def test_full_run_requires_current_matching_receipts_and_approval(self):
        mutations = {}

        def missing_harness():
            self.harness.unlink()

        mutations["missing_harness"] = (missing_harness, "HARNESS_QUALIFICATION_MISSING", False)

        def stale_harness():
            row = json.loads(self.harness.read_text())
            row["accepted"] = False
            self.write_json(self.harness, row)
            self.write_manifest()

        mutations["stale_harness"] = (stale_harness, "HARNESS_QUALIFICATION_MISSING", False)

        def expired_target():
            row = json.loads(self.target.read_text())
            row["expires_at"] = "2026-09-04T11:59:59Z"
            self.write_json(self.target, row)
            self.refresh_checksum()
            self.write_manifest()

        mutations["expired_target"] = (expired_target, "TARGET_RECEIPT_EXPIRED", False)

        def used_target_nonce():
            self.nonce_root.mkdir()
            (self.nonce_root / ("a" * 64 + ".target.used")).write_text("used\n")

        mutations["used_target_nonce"] = (used_target_nonce, "TARGET_RECEIPT_USED", True)

        def profile_mismatch():
            row = json.loads(self.target.read_text())
            row["target_profile_sha256"] = "f" * 64
            self.write_json(self.target, row)
            self.refresh_checksum()
            self.write_manifest()

        mutations["profile_mismatch"] = (profile_mismatch, "INPUTS_INCOMPARABLE", False)

        mutations["wrong_approval"] = (lambda: None, "FULL_RUN_NOT_AUTHORIZED", "wrong")

        def replayed_action_nonce():
            self.nonce_root.mkdir()
            (self.nonce_root / ("b" * 64 + ".action.used")).write_text("used\n")

        mutations["replayed_action_nonce"] = (replayed_action_nonce, "APPROVAL_REPLAYED", True)

        def input_incomparable():
            self.inputs.write_bytes(b"{}\n")

        mutations["input_incomparable"] = (input_incomparable, "INPUTS_INCOMPARABLE", False)

        for name, (mutate, code, consume) in mutations.items():
            with self.subTest(name=name):
                self.tearDown()
                self.setUp()
                mutate()
                if consume == "wrong":
                    operation = lambda: self.consume("0" * 64)
                elif consume:
                    operation = self.consume
                else:
                    operation = None
                self.assert_failure(code, operation)
                self.assertFalse((self.root / "contact-tripwire").exists())

    def test_alias_requires_explicit_risk_acceptance(self):
        target = json.loads(self.target.read_text())
        target["identity_assurance"] = "alias_unresolved"
        profile = json.loads(self.profile.read_text())
        profile["identity_mode"] = "alias_unresolved"
        self.write_json(self.profile, profile)
        target["target_profile_sha256"] = sha256(self.profile)
        self.write_json(self.target, target)
        self.refresh_checksum()
        self.write_manifest()
        self.assert_failure("FULL_RUN_NOT_AUTHORIZED")
        self.write_manifest(accept_alias_identity_risk=True)
        accepted = self.module.verify_admission(self.manifest, now=NOW)
        self.assertEqual(accepted["identity_assurance"], "alias_unresolved")

    def test_detached_checksum_covers_complete_receipt_bytes(self):
        self.checksum.write_text("0" * 64 + "\n", encoding="ascii")
        self.write_manifest()
        self.assert_failure("TARGET_PROBE_NOT_AUTHORIZED")

    def test_action_and_target_nonces_are_single_use_concurrently(self):
        approval = sha256(self.manifest)

        def attempt():
            try:
                self.module.consume_admission(
                    self.manifest, now=NOW,
                    operator_approved_full=approval)
                return "accepted"
            except self.module.AdmissionFailure as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(outcomes.count("accepted"), 1, outcomes)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(set(outcomes) <= {"accepted", "APPROVAL_REPLAYED", "TARGET_RECEIPT_USED"})

    def test_consume_persists_exact_manifest_and_accepted_record(self):
        original = self.manifest.read_bytes()
        result = self.consume()
        self.assertTrue(result["accepted"])
        self.assertEqual(self.manifest.read_bytes(), original)
        accepted_path = self.root / "paid-admission.json"
        self.assertTrue(accepted_path.is_file())
        accepted = json.loads(accepted_path.read_text())
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["manifest_sha256"], sha256(self.manifest))

    def test_prepare_produces_exact_verified_manifest_without_consuming_nonces(self):
        self.manifest.unlink()
        result = self.module.prepare_admission(self.root, now=NOW)
        self.assertEqual(Path(result["manifest"]), self.manifest)
        row = json.loads(self.manifest.read_text())
        self.assertEqual(set(row), self.module.ADMISSION_KEYS)
        self.assertEqual(row["harness_receipt_sha256"], sha256(self.harness))
        self.assertEqual(row["target_receipt_sha256"], sha256(self.target))
        self.assertEqual(row["target_receipt_checksum_sha256"], sha256(self.checksum))
        self.assertEqual(row["target_profile_sha256"], sha256(self.profile))
        self.assertEqual(row["full_input_package_sha256"], sha256(self.inputs))
        self.assertEqual(row["full_run_budget_sha256"], sha256(self.budget))
        self.assertEqual(self.module.verify_admission(self.manifest, now=NOW)["accepted"], True)
        self.assertFalse(self.nonce_root.exists())
        self.assertFalse((self.root / "paid-admission.json").exists())

    def test_each_consumed_nonce_and_accepted_record_is_directory_synced_in_order(self):
        events = []
        original_write = self.module._write_once_at
        original_sync = self.module._fsync_directory_fd

        def record_write(directory_fd, name, value):
            events.append(("write", name, os.fstat(directory_fd).st_ino))
            return original_write(directory_fd, name, value)

        def record_sync(directory_fd):
            events.append(("sync", None, os.fstat(directory_fd).st_ino))
            return original_sync(directory_fd)

        with mock.patch.object(self.module, "_write_once_at", side_effect=record_write), \
                mock.patch.object(self.module, "_fsync_directory_fd", side_effect=record_sync):
            self.consume()
        writes = [index for index, event in enumerate(events) if event[0] == "write"]
        self.assertEqual([events[index][1] for index in writes], [
            "a" * 64 + ".target.used", "b" * 64 + ".action.used", "paid-admission.json"])
        for left, right in zip(writes, writes[1:]):
            self.assertTrue(any(event[0] == "sync" for event in events[left + 1:right]), events)
        self.assertTrue(any(event[0] == "sync" for event in events[writes[-1] + 1:]), events)

    def test_default_nonce_authority_rejects_an_identical_copied_bundle(self):
        copied = self.root / "copied-bundle"
        copied.mkdir()
        for path in (self.harness, self.target, self.checksum, self.profile,
                     self.settings, self.inputs, self.budget, self.manifest):
            shutil.copy2(path, copied / path.name)
        authority = self.root / "machine-state" / "paid-admission-nonces"
        approval = sha256(self.manifest)
        with mock.patch.object(self.module, "DEFAULT_NONCE_ROOT", authority,
                               create=True):
            self.module.consume_admission(
                self.manifest, now=NOW, operator_approved_full=approval)
            self.assert_failure("TARGET_RECEIPT_USED", lambda:
                self.module.consume_admission(
                    copied / self.manifest.name, now=NOW,
                    operator_approved_full=approval))

    def test_new_nonce_authority_parent_is_synced_before_first_marker(self):
        authority = self.root / "machine-state" / "nested" / "paid-admission-nonces"
        events = []
        original_write = self.module._write_once_at
        original_sync = self.module._fsync_directory_fd

        def record_write(directory_fd, name, value):
            events.append(("write", name, os.fstat(directory_fd).st_ino))
            return original_write(directory_fd, name, value)

        def record_sync(directory_fd):
            events.append(("sync", None, os.fstat(directory_fd).st_ino))
            return original_sync(directory_fd)

        with mock.patch.object(self.module, "DEFAULT_NONCE_ROOT", authority), \
                mock.patch.object(self.module, "_write_once_at", side_effect=record_write), \
                mock.patch.object(self.module, "_fsync_directory_fd", side_effect=record_sync):
            self.module.consume_admission(
                self.manifest, now=NOW,
                operator_approved_full=sha256(self.manifest))
        first_marker = next(index for index, event in enumerate(events)
                            if event[0] == "write" and event[1].endswith(".target.used"))
        parent_inode = authority.parent.stat().st_ino
        self.assertIn(("sync", None, parent_inode), events[:first_marker], events)

    def test_cli_requires_exact_operator_approval(self):
        bad = subprocess.run(
            [sys.executable, str(MODULE), "consume", "--manifest", str(self.manifest),
             "--operator-approved-full", "0" * 64],
            capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("FULL_RUN_NOT_AUTHORIZED", bad.stderr)
        self.assertFalse((self.root / "paid-admission.json").exists())

    def test_cli_cannot_override_the_account_nonce_authority(self):
        result = subprocess.run(
            [sys.executable, str(MODULE), "consume", "--help"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--nonce-root", result.stdout)

    def test_symlinked_manifest_or_nonce_parent_is_rejected(self):
        alias = self.root.parent / (self.root.name + "-alias")
        alias.symlink_to(self.root, target_is_directory=True)
        try:
            self.assert_failure("FULL_RUN_NOT_AUTHORIZED", lambda:
                self.module.verify_admission(alias / self.manifest.name, now=NOW))
        finally:
            alias.unlink()
        nonce_parent = self.root / "nonce-parent"; nonce_parent.mkdir()
        nonce_alias = self.root / "nonce-alias"
        nonce_alias.symlink_to(nonce_parent, target_is_directory=True)
        try:
            with mock.patch.object(self.module, "DEFAULT_NONCE_ROOT", nonce_alias / "nonces"):
                self.assert_failure("FULL_RUN_NOT_AUTHORIZED", lambda:
                    self.module.consume_admission(
                        self.manifest, now=NOW,
                        operator_approved_full=sha256(self.manifest)))
        finally:
            nonce_alias.unlink()


if __name__ == "__main__":
    unittest.main()
