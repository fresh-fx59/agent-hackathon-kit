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
        for name in ("target-profile.json", "probe-budget.json", "fixture-manifest.json",
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

    def test_cli_run_uses_only_sealed_local_stub_and_disables_retries(self):
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
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(marker.read_text())["SHERLOCK_MAX_RETRIES"], "0")

    def _accepted_trace(self, trace):
        trace.mkdir()
        for name in ("target-profile.json", "probe-budget.json", "fixture-manifest.json",
                     "input-package.json", "probe-manifest.json"):
            shutil.copy2(self.root / name, trace / name)
        shutil.copytree(self.root / "fixture", trace / "fixture")
        shutil.copy2(ROOT / "tools" / "tests" / "fixtures" / "target-contract-reports" / "canonical.md",
                     trace / "final-report.md")
        fixture_test = ROOT / "tools" / "tests" / "test_target_contract_fixture.py"
        spec = importlib.util.spec_from_file_location("fixture_test_helpers", fixture_test)
        helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
        (trace / "work").mkdir()
        helpers.minimum_ledger(trace / "work", trace / "fixture")
        (trace / "ledger.json").write_text(json.dumps({"provider_calls_observed": 1,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "returned_identities": ["deepseek-v4-20260901"]}))
        (trace / "request-bodies").mkdir(); (trace / "response-bodies").mkdir()
        (trace / "request-bodies" / "one.json").write_text("{}")
        (trace / "response-bodies" / "one.json").write_text("{}")
        gates = {name: {"returncode": 0, "blocking": 0} for name in self.probe.GATES}
        (trace / "probe-gates.json").write_text(json.dumps(gates))
        (trace / "probe-oracle.json").write_text(json.dumps({"accepted": True}))
        (trace / "exit-layers.json").write_text(json.dumps({"attempt_exit_code": 0,
            "driver_exit_code": 0, "wrapper_exit_code": 0,
            "status_exit_code": 0, "gate_exit_codes": {name: 0 for name in self.probe.GATES},
            "primary_failure": None, "terminal_observation": "RUN_SUCCEEDED"}))
        (trace / "upstream-budget-state.json").write_text(json.dumps({"schema": 2,
            "run_tag": "probe", "updated_at": "2026-09-04T00:00:00Z",
            "limits": {"max_provider_calls": 10, "max_prompt_tokens": 400000,
                       "max_completion_tokens": 20000, "max_wall_time_s": 600,
                       "max_estimated_cost_rub": 15.0},
            "budget_assurance": "client_pre_dispatch", "projected": {"provider_calls": 1,
            "prompt_tokens": 1, "completion_tokens": 1, "wall_time_s": 1.0, "estimated_cost_rub": 0.1},
            "observed": {"provider_calls": 1, "prompt_tokens": 1, "completion_tokens": 1},
            "completed_overshoot": {"provider_calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
            "observed_usage_unknown": 0, "completed_attempt_ids": ["0" * 32 + ".a1"],
            "verdict": "WITHIN", "reason": None,
            "rate_snapshot": {"effective_at": "2026-09-04T00:00:00Z", "source": "test",
            "sha256": "0" * 64}}))

    @property
    def trips(self):
        return getattr(self, "_trips", [])

    def _tripwire(self, *args, **kwargs):
        self._trips = self.trips + [args]
        raise AssertionError("contact tripwire invoked")

    @staticmethod
    def _sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
