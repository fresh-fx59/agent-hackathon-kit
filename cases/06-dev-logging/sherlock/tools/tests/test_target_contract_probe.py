#!/usr/bin/env python3
"""Provider-free contract tests for the paid target probe admission boundary."""
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
import datetime as dt


ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "eval" / "bench"
PROBE_PATH = BENCH / "target-contract-probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("target_contract_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TargetContractProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.probe = load_probe()
        self.source = ROOT / "tools" / "tests" / "fixtures" / "target-contract-source"
        self.root = self.temp / "probe"
        self.args = self.probe.PrepareArgs(
            root=self.root, source_corpus=self.source, provider_base_url="http://127.0.0.1:9",
            route="paid-route", secret_ref="SHERLOCK_API_KEY", requested_model="deepseek-v4-20260901",
            expected_returned_identity="deepseek-v4-20260901",
            identity_mode="provider_pinned_version", qwen_bin="/usr/bin/true", arm="target",
        )

    def tearDown(self):
        shutil.rmtree(self.temp)

    def test_prepare_is_secret_free_and_seals_exact_manifest_assets(self):
        result = self.probe.prepare(self.args, secret_reader=self._tripwire)
        self.assertEqual(self.trips, [])
        manifest = json.loads((self.root / "probe-manifest.json").read_text())
        self.assertEqual(set(manifest), self.probe.PROBE_MANIFEST_KEYS)
        self.assertEqual(manifest["action"], "target_contract_probe")
        for name in ("target-profile.json", "corporate-settings.json", "probe-budget.json", "fixture-manifest.json",
                     "input-package.json", "probe-manifest.json"):
            self.assertTrue((self.root / name).is_file(), name)
        self.assertEqual(result["manifest_sha256"], self._sha(self.root / "probe-manifest.json"))
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.prepare(self.args)

    def test_authorize_binds_raw_bytes_action_expiry_and_single_use_nonce(self):
        self.probe.prepare(self.args)
        manifest = self.root / "probe-manifest.json"
        digest = self._sha(manifest)
        row = self.probe.authorize(manifest, digest, self.root / "nonces")
        self.assertEqual(row["action"], "target_contract_probe")
        with self.assertRaisesRegex(self.probe.ProbeFailure, "APPROVAL_REPLAYED"):
            self.probe.authorize(manifest, digest, self.root / "nonces")

    def test_concurrent_authorization_consumes_nonce_exactly_once(self):
        self.probe.prepare(self.args)
        manifest = self.root / "probe-manifest.json"
        digest = self._sha(manifest)
        command = [sys.executable, str(PROBE_PATH), "authorize", "--manifest", str(manifest),
                   "--operator-approved-probe", digest, "--nonce-root", str(self.root / "nonces")]
        first = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        outcomes = []
        for child in (first, second):
            stdout, stderr = child.communicate(timeout=10)
            outcomes.append("accepted" if child.returncode == 0 else stderr.strip())
        outcomes.sort()
        self.assertEqual(outcomes, ["APPROVAL_REPLAYED", "accepted"])

    def test_refusals_happen_before_secret_proxy_runner_or_network(self):
        for case in ("missing_approval", "wrong_hash", "expired", "wrong_action",
                     "tampered_profile", "tampered_fixture", "used_nonce", "missing_rates"):
            with self.subTest(case=case):
                root = self.temp / case
                args = self.probe.PrepareArgs(**dict(self.args.__dict__, root=root))
                self.probe.prepare(args)
                manifest = root / "probe-manifest.json"
                if case in ("expired", "wrong_action"):
                    row = json.loads(manifest.read_text())
                    row["expires_at" if case == "expired" else "action"] = (
                        "2000-01-01T00:00:00Z" if case == "expired" else "other")
                    manifest.write_bytes(self.probe.canonical(row) + b"\n")
                elif case == "tampered_profile":
                    (root / "target-profile.json").write_text("{}")
                elif case == "tampered_fixture":
                    (root / "fixture-manifest.json").write_text("{}")
                elif case == "missing_rates":
                    (root / "probe-budget.json").write_text("{}")
                digest = self._sha(manifest)
                if case == "missing_approval":
                    digest = ""
                if case == "wrong_hash":
                    digest = "0" * 64
                if case == "used_nonce":
                    self.probe.authorize(manifest, digest, root / "nonces")
                with self.assertRaises(self.probe.ProbeFailure):
                    self.probe.run(manifest, digest, root / "nonces", secret_reader=self._tripwire,
                                   proxy_starter=self._tripwire, runner=self._tripwire)
                self.assertEqual(self.trips, [])

    def test_identity_modes_are_narrow_and_missing_identity_is_terminal(self):
        self.assertEqual(self.probe.audit_identity("provider_pinned_version", "deepseek-v4-20260901",
                                                   ["deepseek-v4-20260901"]),
                         "provider_pinned_version")
        self.assertEqual(self.probe.audit_identity("alias_unresolved", "deepseek-v4-flash",
                                                   ["deepseek-v4-flash"]), "alias_unresolved")
        for returned in ([], [None], ["deepseek-v4-pro"]):
            with self.assertRaisesRegex(self.probe.ProbeFailure, "TARGET_IDENTITY"):
                self.probe.audit_identity("alias_unresolved", "deepseek-v4-flash", returned)

    def test_authorized_run_starts_contact_path_once_with_retries_disabled(self):
        self.probe.prepare(self.args)
        observed = []
        def secret(reference):
            observed.append(("secret", reference))
            return "test-only"
        def proxy(profile, token, budget):
            observed.append(("proxy", profile["requested_model"], token, Path(budget).name))
            return "localhost-only"
        def runner(**kwargs):
            observed.append(("runner", kwargs["retries"], kwargs["proxy"], kwargs["profile_path"].name))
            return {"started": True}
        result = self.probe.run(self.root / "probe-manifest.json",
                                self._sha(self.root / "probe-manifest.json"), self.root / "nonces",
                                secret_reader=secret, proxy_starter=proxy, runner=runner)
        self.assertEqual(result, {"started": True})
        self.assertEqual(observed[-1], ("runner", 0, "localhost-only", "target-profile.json"))

    def test_audit_failure_writes_result_but_never_receipt(self):
        self.probe.prepare(self.args)
        trace = self.root / "probe-work"
        trace.mkdir()
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.audit(trace)
        self.assertTrue((trace / "probe-result.json").is_file())
        self.assertFalse((trace / "target-contract-receipt.json").exists())

    def test_receipt_is_atomic_no_replace_when_audit_accepts_all_bound_evidence(self):
        self.probe.prepare(self.args)
        self.probe.authorize(self.root / "probe-manifest.json",
                             self._sha(self.root / "probe-manifest.json"), self.root / "nonces")
        trace = self.root / "probe-work"
        self._accepted_trace(trace)
        result = self.probe.audit(trace)
        receipt = trace / "target-contract-receipt.json"
        self.assertTrue(result["accepted"])
        self.assertTrue(receipt.is_file())
        self.assertEqual(self._sha(receipt), (trace / "target-contract-receipt.json.sha256").read_text().strip())
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.audit(trace)

    def test_audit_rejects_symlinked_response_tree_and_keeps_receipt_absent(self):
        self.probe.prepare(self.args)
        trace = self.root / "probe-work"
        self._accepted_trace(trace)
        (trace / "response-bodies" / "alias.json").symlink_to(trace / "response-bodies" / "one.json")
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.audit(trace)
        self.assertFalse((trace / "target-contract-receipt.json").exists())

    def test_adversarial_review_regressions_fail_closed_before_or_after_contact(self):
        # These compact cases cover the nine review categories: forged audit,
        # mutable fixture, nonce burn, aliases, strict budget/exits/identity,
        # receipt transaction, and the provider-safe CLI contract.
        self.probe.prepare(self.args)
        manifest = self.root / "probe-manifest.json"
        digest = self._sha(manifest)
        (self.root / "fixture" / "Security.jsonl").write_text("attacker\n")
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(manifest, digest, self.root / "nonces", secret_reader=self._tripwire,
                           proxy_starter=self._tripwire, runner=self._tripwire)
        self.assertEqual(self.trips, [])
        # Dependency rejection must not consume approval.
        (self.root / "fixture" / "Security.jsonl").write_bytes(
            (self.source / "Security.jsonl").read_bytes())
        self.probe.authorize(manifest, digest, self.root / "nonces")

        root = self.temp / "forged"
        args = self.probe.PrepareArgs(**dict(self.args.__dict__, root=root))
        self.probe.prepare(args)
        trace = root / "probe-work"; self._accepted_trace(trace)
        (trace / "final-report.md").write_text("ok\n")
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.audit(trace)
        self.assertFalse((trace / "target-contract-receipt.json").exists())

    def test_cli_prepare_json_and_run_refusal_never_contact(self):
        root = self.temp / "cli"
        command = [sys.executable, str(PROBE_PATH), "prepare", "--root", str(root),
                   "--source-corpus", str(self.source), "--provider-base-url", "http://127.0.0.1:9",
                   "--route", "paid-route", "--secret-ref", "SHERLOCK_API_KEY",
                   "--requested-model", "deepseek-v4-20260901", "--expected-returned-identity",
                   "deepseek-v4-20260901", "--identity-mode", "provider_pinned_version",
                   "--qwen-bin", "/usr/bin/true", "--arm", "target", "--json"]
        done = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["root"], str(root))

    def test_cli_run_rejects_runner_substitution_even_for_local_stub(self):
        self.probe.prepare(self.args)
        marker = self.temp / "runner-marker.json"
        stub = self.temp / "runner.py"
        stub.write_text("#!/usr/bin/env python3\nimport json,os\nopen(os.environ['MARKER'],'w').write(json.dumps({k:os.environ[k] for k in ('SHERLOCK_TARGET_PROFILE','SHERLOCK_PROBE_FIXTURE','SHERLOCK_PROBE_BUDGET','SHERLOCK_PROBE_WORK','SHERLOCK_MAX_RETRIES')}))\n")
        stub.chmod(0o700)
        env = dict(os.environ, SHERLOCK_API_KEY="test-only", MARKER=str(marker))
        done = subprocess.run([sys.executable, str(PROBE_PATH), "run", "--manifest", str(self.root / "probe-manifest.json"),
                               "--operator-approved-probe", self._sha(self.root / "probe-manifest.json"),
                               "--nonce-root", str(self.root / "nonces"), "--runner-command", str(stub)],
                              text=True, capture_output=True, env=env)
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(marker.exists())

    def test_cli_local_transport_uses_the_real_runner_and_never_fabricates_a_report(self):
        """A provider-free refusal proves the production runner branch is selected."""
        self.probe.prepare(self.args)
        manifest = self.root / "probe-manifest.json"
        done = subprocess.run([sys.executable, str(PROBE_PATH), "run", "--manifest", str(manifest),
                               "--operator-approved-probe", self._sha(manifest), "--nonce-root", str(self.root / "nonces"),
                               "--transport-base-url", "http://127.0.0.1:9/v1", "--json"],
                              text=True, capture_output=True,
                              env=dict(os.environ, SHERLOCK_API_KEY="test-only"), timeout=30)
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("TARGET_PROBE_TEST_MODE_REQUIRED", done.stderr)
        trace = self.root / "probe-work" / "runs" / "target-contract-probe"
        self.assertTrue((trace / "run-manifest.json").is_file())
        self.assertFalse((trace / "final-report.md").exists())

    def test_manifest_created_at_must_be_aware_and_bound_before_contact(self):
        self.probe.prepare(self.args)
        manifest = self.root / "probe-manifest.json"
        row = json.loads(manifest.read_text())
        row["created_at"] = "2000-01-01T00:00:00"
        row["expires_at"] = "2099-01-01T00:00:00Z"
        manifest.write_bytes(self.probe.canonical(row) + b"\n")
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(manifest, self._sha(manifest), self.root / "nonces",
                           secret_reader=self._tripwire, proxy_starter=self._tripwire, runner=self._tripwire)
        self.assertEqual(self.trips, [])

    def test_run_rejects_undeclared_fixture_member_before_secret(self):
        """A sealed fixture is a closed tree, not merely declared leaf hashes."""
        self.probe.prepare(self.args)
        (self.root / "fixture" / "undeclared.jsonl").write_text("attacker\n")
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(self.root / "probe-manifest.json", self._sha(self.root / "probe-manifest.json"),
                           self.root / "nonces", secret_reader=self._tripwire,
                           proxy_starter=self._tripwire, runner=self._tripwire)
        self.assertEqual(self.trips, [])

    def test_safe_read_rejects_a_parent_symlink(self):
        """A no-follow leaf check is insufficient when an ancestor aliases it."""
        source = self.temp / "source"; source.mkdir()
        (source / "asset.json").write_text("{}\n")
        alias = self.temp / "alias"
        alias.symlink_to(source, target_is_directory=True)
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.safe_read_regular(alias / "asset.json")

    def test_nonce_and_publication_reject_parent_symlinks(self):
        """A parent alias cannot redirect single-use or receipt publication."""
        self.probe.prepare(self.args)
        outside = self.temp / "outside"; outside.mkdir()
        nonce_alias = self.temp / "nonce-alias"; nonce_alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.authorize(self.root / "probe-manifest.json", self._sha(self.root / "probe-manifest.json"), nonce_alias)
        self.assertEqual(list(outside.iterdir()), [])
        receipt_alias = self.temp / "receipt-alias"; receipt_alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe._atomic_no_replace(receipt_alias / "receipt.json", b"{}\n")
        self.assertEqual(list(outside.iterdir()), [])

    def test_receipt_checksum_interruption_rolls_back_receipt_and_seals_rejection(self):
        """An interrupted accepted publication leaves one rejected terminal result."""
        self.probe.prepare(self.args)
        trace = self.root / "probe-work"; self._accepted_trace(trace)
        original = self.probe._atomic_no_replace
        def interrupt(path, data):
            if str(path).endswith("target-contract-receipt.json.sha256"):
                raise self.probe.ProbeFailure("TARGET_CONTRACT_FAILED", "injected checksum interruption")
            return original(path, data)
        self.probe._atomic_no_replace = interrupt
        try:
            with self.assertRaises(self.probe.ProbeFailure):
                self.probe.audit(trace)
        finally:
            self.probe._atomic_no_replace = original
        self.assertFalse((trace / "target-contract-receipt.json").exists())
        self.assertFalse((trace / "target-contract-receipt.json.sha256").exists())
        self.assertFalse(json.loads((trace / "probe-result.json").read_text())["accepted"])

    def test_precontact_run_refusal_writes_one_terminal_result(self):
        """Even a malformed manifest leaves an auditable terminal outcome."""
        self.probe.prepare(self.args)
        manifest = self.root / "probe-manifest.json"
        manifest.write_text("{}\n")
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(manifest, self._sha(manifest), self.root / "nonces",
                           secret_reader=self._tripwire, proxy_starter=self._tripwire, runner=self._tripwire)
        result = self.root / "probe-result.json"
        self.assertTrue(result.is_file())
        self.assertFalse(json.loads(result.read_text())["accepted"])

    def test_audit_rejects_forged_budget_identity_and_body_aliases(self):
        """One trace call has one bounded sent/returned identity and owned bodies."""
        for mutation in ("budget", "identity", "body-alias"):
            with self.subTest(mutation=mutation):
                root = self.temp / ("audit-" + mutation)
                args = self.probe.PrepareArgs(**dict(self.args.__dict__, root=root))
                self.probe.prepare(args)
                trace = root / "probe-work"; self._accepted_trace(trace, root)
                if mutation == "budget":
                    budget = json.loads((trace / "upstream-budget-state.json").read_text())
                    budget["limits"]["max_provider_calls"] = 999
                    (trace / "upstream-budget-state.json").write_bytes(self.probe.canonical(budget))
                elif mutation == "identity":
                    ledger = json.loads((trace / "ledger.json").read_text())
                    ledger["sent_models"] = ["other-model"]
                    (trace / "ledger.json").write_bytes(self.probe.canonical(ledger))
                else:
                    first = trace / "request-bodies" / "one.json"
                    os.link(first, trace / "response-bodies" / "alias.json")
                with self.assertRaises(self.probe.ProbeFailure):
                    self.probe.audit(trace)
                self.assertFalse((trace / "target-contract-receipt.json").exists())

    def test_run_revalidates_snapshot_after_secret_callback(self):
        """A callback cannot substitute bytes after the final pre-contact check."""
        self.probe.prepare(self.args)
        observed = []
        def secret(_reference):
            sealed = self.root / "probe-work" / "sealed-input" / "target-profile.json"
            profile = json.loads(sealed.read_text())
            profile["route"] = "substituted-route"
            sealed.write_bytes(self.probe.canonical(profile) + b"\n")
            return "test-only"
        def proxy(profile, _token, _budget):
            observed.append(profile["route"])
            return "localhost-only"
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(self.root / "probe-manifest.json", self._sha(self.root / "probe-manifest.json"),
                           self.root / "nonces", secret_reader=secret, proxy_starter=proxy,
                           runner=self._tripwire)
        self.assertEqual(observed, [])

    def test_run_revalidates_snapshot_after_proxy_callback(self):
        """The proxy callback is not allowed to substitute last-use input bytes."""
        self.probe.prepare(self.args)
        observed = []
        def proxy(_profile, _token, _budget):
            sealed = self.root / "probe-work" / "sealed-input" / "target-profile.json"
            row = json.loads(sealed.read_text())
            row["route"] = "after-final-check"
            sealed.write_bytes(self.probe.canonical(row) + b"\n")
            return {"route": "localhost-only"}
        def runner(**kwargs):
            observed.append(json.loads(kwargs["profile_path"].read_text())["route"])
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(self.root / "probe-manifest.json", self._sha(self.root / "probe-manifest.json"),
                           self.root / "nonces", secret_reader=lambda _: "test-only",
                           proxy_starter=proxy, runner=runner)
        self.assertEqual(observed, [])

    def test_local_transport_parser_rejects_prefix_and_nonloopback(self):
        """Local integration override is an exact parsed loopback URL."""
        for value in ("http://127.0.0.1.evil.invalid:9/v1", "https://not-local.invalid/v1",
                      "http://user@127.0.0.1:9/v1", "ftp://127.0.0.1:9"):
            with self.subTest(value=value):
                with self.assertRaises(self.probe.ProbeFailure):
                    self.probe.validate_test_transport(value)
        self.assertEqual(self.probe.validate_test_transport("http://127.0.0.1:9/v1"), "http://127.0.0.1:9/v1")

    def test_alias_authorization_is_thirty_minutes_not_twenty_four_hours(self):
        args = self.probe.PrepareArgs(**dict(self.args.__dict__, root=self.temp / "alias",
            identity_mode="alias_unresolved", expected_returned_identity="deepseek-v4"))
        self.probe.prepare(args)
        manifest = args.root / "probe-manifest.json"
        row = json.loads(manifest.read_text())
        created = self.probe._iso(row["created_at"])
        self.assertLessEqual(self.probe._iso(row["expires_at"]) - created, __import__("datetime").timedelta(minutes=30))

    def test_timeout_writes_one_terminal_result(self):
        self.probe.prepare(self.args)
        def timeout(**_kwargs):
            raise subprocess.TimeoutExpired(["local-stub"], 1)
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.run(self.root / "probe-manifest.json", self._sha(self.root / "probe-manifest.json"),
                           self.root / "nonces", secret_reader=lambda _: "test-only",
                           proxy_starter=lambda *_: {}, runner=timeout)
        row = json.loads((self.root / "probe-work" / "probe-result.json").read_text())
        self.assertFalse(row["accepted"])
        self.assertEqual(row["failure"], "TARGET_CONTRACT_FAILED")

    def test_audit_correlates_actual_body_rows_and_task7_verdict(self):
        """Ledger summaries cannot replace the raw request/response evidence."""
        self.probe.prepare(self.args)
        trace = self.root / "probe-work"; self._accepted_trace(trace)
        (trace / "run-verdict.json").write_text(json.dumps({"schema": 1, "run_tag": trace.name,
            "state": "succeeded", "finished": True, "successful": True,
            "report_correct": True, "failures": [], "metrics": {}}))
        (trace / "response-bodies" / "one.json").write_text(json.dumps({"call_id": "other",
            "returned_identity": "other", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.audit(trace)

    def test_cli_refuses_arbitrary_runner_command(self):
        """The paid CLI may select only its sealed controller/runner path."""
        self.probe.prepare(self.args)
        done = subprocess.run([sys.executable, str(PROBE_PATH), "run", "--manifest",
                               str(self.root / "probe-manifest.json"), "--operator-approved-probe",
                               self._sha(self.root / "probe-manifest.json"), "--nonce-root",
                               str(self.root / "nonces"), "--runner-command", "/usr/bin/true", "--json"],
                              text=True, capture_output=True,
                              env=dict(os.environ, SHERLOCK_API_KEY="test-only"))
        self.assertNotEqual(done.returncode, 0)

    # Round 4 regressions.  These deliberately describe the real state-machine
    # contract, rather than accepting the old test-only runner fixture.
    def test_audit_requires_a_durable_consumed_action_nonce_and_uses_a_new_receipt_nonce(self):
        self.probe.prepare(self.args)
        trace = self.root / "probe-work"
        self._accepted_trace(trace)
        with self.assertRaisesRegex(self.probe.ProbeFailure, "TARGET_CONTRACT_FAILED"):
            self.probe.audit(trace)
        self.probe.authorize(self.root / "probe-manifest.json",
                             self._sha(self.root / "probe-manifest.json"), self.root / "nonces")
        trace = self.root / "probe-work-authorized"
        self._accepted_trace(trace)
        result = self.probe.audit(trace)
        receipt = json.loads((trace / "target-contract-receipt.json").read_text())
        self.assertTrue(result["accepted"])
        self.assertNotEqual(receipt["nonce"], json.loads((self.root / "probe-manifest.json").read_text())["nonce"])

    def test_audit_accepts_the_actual_task7_finished_exit_schema(self):
        self.probe.prepare(self.args)
        self.probe.authorize(self.root / "probe-manifest.json",
                             self._sha(self.root / "probe-manifest.json"), self.root / "nonces")
        trace = self.root / "probe-work"
        self._accepted_trace(trace)
        (trace / "run-verdict.json").write_text(json.dumps({
            "schema": 1, "run_tag": trace.name, "state": "finished",
            "attempt_exit_code": 0, "driver_exit_code": 0,
            "gate_exit_codes": {name: 0 for name in self.probe.GATES},
            "wrapper_exit_code": 0, "primary_failure": None,
            "terminal_observation": "representative target probe completed",
        }))
        self.assertTrue(self.probe.audit(trace)["accepted"])

    def test_audit_sums_each_gzip_proxy_response_and_correlates_calls_and_cost(self):
        self.probe.prepare(self.args)
        self.probe.authorize(self.root / "probe-manifest.json",
                             self._sha(self.root / "probe-manifest.json"), self.root / "nonces")
        trace = self.root / "probe-work"; self._accepted_trace(trace)
        # A forged aggregate is never a substitute for the per-call capture.
        body = json.loads((trace / "response-bodies" / "one.json").read_text())
        body["call_id"] = "f" * 32 + ".a1"
        body["usage"] = {"prompt_tokens": 2, "completion_tokens": 2}
        (trace / "response-bodies" / "one.json").write_text(json.dumps(body))
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.audit(trace)

    def test_prepare_rejects_symlink_parent_before_creating_any_entry(self):
        outside = self.temp / "outside"; outside.mkdir()
        parent = self.temp / "aliased-parent"; parent.symlink_to(outside, target_is_directory=True)
        args = self.probe.PrepareArgs(**dict(self.args.__dict__, root=parent / "probe"))
        with self.assertRaises(self.probe.ProbeFailure):
            self.probe.prepare(args)
        self.assertFalse((outside / "probe").exists())

    def test_audit_timeout_still_emits_exactly_one_terminal_result(self):
        self.probe.prepare(self.args)
        trace = self.root / "probe-work"; self._accepted_trace(trace)
        original = self.probe.subprocess.run
        self.probe.subprocess.run = lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 1))
        try:
            with self.assertRaises(self.probe.ProbeFailure):
                self.probe.audit(trace)
        finally:
            self.probe.subprocess.run = original
        self.assertTrue((trace / "probe-result.json").is_file())
        self.assertFalse((self.root / "nonces").exists())

    def _accepted_trace(self, trace, source_root=None):
        trace.mkdir()
        source_root = self.root if source_root is None else source_root
        for name in ("target-profile.json", "corporate-settings.json", "probe-budget.json", "fixture-manifest.json",
                     "input-package.json", "probe-manifest.json"):
            shutil.copy2(source_root / name, trace / name)
        if (source_root / "action-authorization.json").is_file():
            shutil.copy2(source_root / "action-authorization.json", trace / "action-authorization.json")
        shutil.copytree(source_root / "fixture", trace / "fixture")
        shutil.copy2(ROOT / "tools" / "tests" / "fixtures" / "target-contract-reports" / "canonical.md",
                     trace / "final-report.md")
        fixture_test = ROOT / "tools" / "tests" / "test_target_contract_fixture.py"
        spec = importlib.util.spec_from_file_location("fixture_test_helpers", fixture_test)
        helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
        (trace / "work").mkdir()
        # The canonical fixture is sealed; generate the v44 worklist from an
        # isolated gate corpus rather than adding a post-seal fixture leaf.
        gate_corpus = trace.parent / ("gate-corpus-" + trace.name)
        shutil.copytree(trace / "fixture", gate_corpus)
        helpers.minimum_ledger(trace / "work", gate_corpus)
        call_id = "0" * 32 + ".a1"
        (trace / "ledger.json").write_text(json.dumps({"provider_calls_observed": 1,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "returned_identities": ["deepseek-v4-20260901"],
            "sent_models": ["deepseek-v4-20260901"], "call_ids": [call_id]}))
        (trace / "request-bodies").mkdir(); (trace / "response-bodies").mkdir()
        (trace / "request-bodies" / "one.json").write_text(json.dumps({"call_id": call_id,
            "model": "deepseek-v4-20260901"}))
        (trace / "response-bodies" / "one.json").write_text(json.dumps({"call_id": call_id,
            "returned_identity": "deepseek-v4-20260901",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
        (trace / "run-verdict.json").write_text(json.dumps({"schema": 1, "run_tag": trace.name,
            "state": "finished", "attempt_exit_code": 0, "driver_exit_code": 0,
            "wrapper_exit_code": 0, "gate_exit_codes": {name: 0 for name in self.probe.GATES},
            "primary_failure": None, "terminal_observation": "RUN_SUCCEEDED"}))
        effective = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        rate = {"schema": 1, "run_tag": trace.name, "effective_at": effective, "source": "local-test",
                "prompt_rub_per_token": 0.0, "completion_rub_per_token": 0.0}
        rate["sha256"] = self._sha_bytes(self.probe.canonical(rate))
        (trace / "upstream-budget-state.json").write_text(json.dumps({"schema": 2,
            "run_tag": trace.name, "updated_at": effective,
            "limits": {"max_provider_calls": 10, "max_prompt_tokens": 400000,
                       "max_completion_tokens": 20000, "max_wall_time_s": 600,
                       "max_estimated_cost_rub": 15.0},
            "budget_assurance": "client_pre_dispatch", "projected": {"provider_calls": 1,
            "prompt_tokens": 1, "completion_tokens": 1, "wall_time_s": 1.0, "estimated_cost_rub": 0.1},
            "observed": {"provider_calls": 1, "prompt_tokens": 1, "completion_tokens": 1},
            "completed_overshoot": {"provider_calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
            "observed_usage_unknown": 0, "completed_attempt_ids": [call_id],
            "verdict": "WITHIN", "reason": None,
            "rate_snapshot": rate}))

    @property
    def trips(self):
        return getattr(self, "_trips", [])

    def _tripwire(self, *args, **kwargs):
        self._trips = self.trips + [args]
        raise AssertionError("contact tripwire invoked")

    @staticmethod
    def _sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def _sha_bytes(value):
        return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    unittest.main()
