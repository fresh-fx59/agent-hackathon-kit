#!/usr/bin/env python3
"""Provider-free contract tests for the Sherlock run verdict wrapper."""
import importlib.util
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
TOOL = SHERLOCK / "eval" / "bench" / "run-verdict.py"
MANIFEST_TEST = HERE / "test_run_manifest.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURES = load("run_verdict_manifest_fixture", MANIFEST_TEST)
VERDICT_TOOL = load("run_verdict_tool", TOOL)


class RunVerdictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = FIXTURES.Fixture(self.temp.name)
        self.fx.stage()
        self.manifest = self.fx.create()

    def write_status(self, phase="QWEN_RUNNING", **updates):
        row = {
            "schema": 1,
            "run_tag": "run-001",
            "phase": phase,
            "updated_at": "2026-08-30T19:00:00Z",
            "pid": None,
            "attempt": 0,
            "dataset": "fixture",
            "arm": "v28",
            "trace_dir": str(self.fx.trace),
            "detail": None,
            "session_id": None,
            "reason": None,
            "exit_code": None,
            "duration_s": None,
            "upstream_log": None,
            "inflight_path": None,
        }
        row.update(updates)
        (self.fx.trace / "status.json").write_text(json.dumps(row), encoding="utf-8")

    def verdict(self, *extra):
        command = [
            sys.executable,
            str(TOOL),
            str(self.fx.trace),
            "--commitment-file",
            str(self.fx.commitment),
            "--commitment-key",
            str(self.fx.commitment_key),
            "--json",
            *extra,
        ]
        return subprocess.run(command, text=True, capture_output=True)

    def verdict_text(self, *extra):
        command = [
            sys.executable,
            str(TOOL),
            str(self.fx.trace),
            "--commitment-file",
            str(self.fx.commitment),
            "--commitment-key",
            str(self.fx.commitment_key),
            *extra,
        ]
        return subprocess.run(command, text=True, capture_output=True)

    def verdict_auto(self, *extra):
        command = [
            sys.executable,
            str(TOOL),
            str(self.fx.trace),
            "--json",
            *extra,
        ]
        return subprocess.run(command, text=True, capture_output=True)

    def stage_target_probe_trace(self):
        root = Path(self.temp.name) / "target-root"
        trace = root / "probe-work" / "runs" / "target-contract-probe"
        trace.mkdir(parents=True); nonces = root / "nonces"; nonces.mkdir()
        raw = {}
        for name in ("target-profile.json", "probe-budget.json", "probe-rate-snapshot.json",
                     "fixture-manifest.json", "input-package.json", "probe/prompt.txt"):
            raw[name] = (json.dumps({"schema": 1, "name": name}, sort_keys=True,
                                    separators=(",", ":")).encode()
                         if name.endswith(".json") else b"sealed target probe prompt\n")
            destination = trace / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw[name])
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}
        manifest = {"schema": 1, "action": "target_contract_probe",
                    "created_at": "2026-09-04T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
                    "nonce": "b" * 64, "target_profile_sha256": hashes["target-profile.json"],
                    "probe_budget_sha256": hashes["probe-budget.json"],
                    "rate_snapshot_sha256": hashes["probe-rate-snapshot.json"],
                    "fixture_manifest_sha256": hashes["fixture-manifest.json"],
                    "input_package_sha256": hashes["input-package.json"],
                    "prompt_sha256": hashes["probe/prompt.txt"]}
        raw_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        (trace / "probe-manifest.json").write_bytes(raw_manifest)
        nonce = (json.dumps({"nonce": manifest["nonce"], "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest()},
                            sort_keys=True, separators=(",", ":")) + "\n").encode()
        nonce_path = nonces / (manifest["nonce"] + ".json"); nonce_path.write_bytes(nonce)
        auth = {"schema": 1, "action_nonce": manifest["nonce"],
                "manifest_raw_sha256": hashlib.sha256(raw_manifest).hexdigest(),
                "nonce_record_path": os.path.realpath(nonce_path),
                "nonce_record_sha256": hashlib.sha256(nonce).hexdigest(),
                "nonce_root": os.path.realpath(nonces), "probe_root": os.path.realpath(root),
                "trace_path": os.path.realpath(trace),
                "bench_status_sha256": hashlib.sha256((SHERLOCK / "eval" / "bench" / "bench-status.py").read_bytes()).hexdigest(),
                "run_verdict_sha256": hashlib.sha256(TOOL.read_bytes()).hexdigest()}
        (trace / "action-authorization.json").write_text(json.dumps(auth, sort_keys=True, separators=(",", ":")))
        status = {"schema": 1, "run_tag": "target-contract-probe", "phase": "ACCEPTED",
                  "updated_at": "2026-09-04T00:00:00Z", "pid": None, "attempt": 1,
                  "dataset": "fixture", "arm": "v44", "trace_dir": str(trace), "detail": None,
                  "session_id": None, "reason": None, "exit_code": 0, "duration_s": 1,
                  "upstream_log": None, "inflight_path": None}
        (trace / "status.json").write_text(json.dumps(status))
        (trace / "candidate.json").write_text("{}")
        (trace / "gates.json").write_text(json.dumps({"schema": 1, "verdict": "clean", "arm_intact": True,
            "gates": {name: {"exit_code": 0, "blocking": 0} for name in VERDICT_TOOL.REQUIRED_GATES}}))
        (trace / "lane-integrity.json").write_text(json.dumps({"schema": 1, "verdict": "clean", "reason": None, "detail": None}))
        work = trace / "work"; work.mkdir(); (work / "report.md").write_text("# report\n")
        replay = trace / "replay.sh"; replay.write_text("#!/usr/bin/env bash\nexit 0\n"); replay.chmod(0o755)
        (trace / "attempts.jsonl").write_text(json.dumps({"attempt": 1, "exit_code": 0}) + "\n")
        (trace / "driver-result.json").write_text(json.dumps({"exit_code": 0}))
        (trace / "upstream-completed.jsonl").write_text(json.dumps({"status": 200, "request_max_tokens": 1,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}) + "\n")
        return trace

    def copied_tool_tree(self):
        """Copy only the local verifier tree for dependency-boundary attacks."""
        copied = Path(self.temp.name) / "copied-sherlock"
        for relative in (
            "eval/bench/run-verdict.py", "eval/bench/bench-status.py",
            "eval/bench/run-manifest.py", "eval/bench/validate-run.py",
            "measure/run_state.py", "measure/deliverable.py",
        ):
            source = SHERLOCK / relative
            destination = copied / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return copied

    def write_validity(self, valid=True, reasons=()):
        row = {
            "schema": 1,
            "valid": valid,
            "reasons": list(reasons),
            "run_tag": "run-001",
            "manifest_sha256": self.manifest["manifest_sha256"],
            "candidate_sha256": "0" * 64,
        }
        validity = load(
            "run_verdict_validity_writer",
            SHERLOCK / "eval" / "bench" / "validate-run.py",
        )
        row["hmac_sha256"] = validity.sign(row, self.fx.commitment_key.read_bytes())
        (self.fx.trace / "validity.json").write_text(json.dumps(row), encoding="utf-8")

    def write_clean_report_artifacts(self):
        work = self.fx.trace / "work"
        work.mkdir(exist_ok=True)
        (work / "report.md").write_text("# Verified report\n", encoding="utf-8")
        gates = {
            "schema": 1,
            "verdict": "clean",
            "arm_intact": True,
            "gates": {
                name: {"exit_code": 0, "blocking": 0}
                for name in ("citecheck", "triagecheck", "statecheck", "reportcheck")
            },
        }
        (self.fx.trace / "gates.json").write_text(json.dumps(gates), encoding="utf-8")
        (self.fx.trace / "lane-integrity.json").write_text(
            json.dumps({"schema": 1, "verdict": "clean", "reason": None, "detail": None}),
            encoding="utf-8",
        )
        replay = self.fx.trace / "replay.sh"
        replay.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        replay.chmod(0o755)

    def write_usage_rows(self, usage_calls=1, include_usage=True, estimate=None):
        rows = []
        for _ in range(usage_calls):
            row = {"status": 200, "request_max_tokens": 40}
            if include_usage:
                row["usage"] = {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 10},
                }
            if estimate is not None:
                row["estimated_cost_rub"] = estimate
            rows.append(row)
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def write_billing_receipt(self, value):
        (self.fx.trace / "provider-billing-receipt.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def write_budget_estimate(self, cost, effective_at=None):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        snapshot = {
            "schema": 1, "run_tag": "run-001",
            "effective_at": effective_at or now.isoformat().replace("+00:00", "Z"),
            "source": "fixture", "prompt_rub_per_token": 0.01,
            "completion_rub_per_token": 0.02,
        }
        snapshot["sha256"] = hashlib.sha256(json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        (self.fx.trace / "upstream-budget-state.json").write_text(json.dumps({
            "schema": 2, "run_tag": "run-001",
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "limits": {"max_provider_calls": 10, "max_prompt_tokens": 400000,
                       "max_completion_tokens": 20000, "max_wall_time_s": 600,
                       "max_estimated_cost_rub": 15.0},
            "rate_snapshot": snapshot, "budget_assurance": "client_pre_dispatch",
            "projected": {"provider_calls": 1, "prompt_tokens": 100,
                          "completion_tokens": 20, "wall_time_s": 1.0,
                          "estimated_cost_rub": cost},
            "observed": {"provider_calls": 0, "prompt_tokens": 0,
                         "completion_tokens": 0},
            "observed_usage_unknown": 0, "completed_attempt_ids": [],
            "completed_overshoot": {"provider_calls": 0, "prompt_tokens": 0,
                                    "completion_tokens": 0},
            "verdict": "WITHIN", "reason": None,
        }), encoding="utf-8")

    def test_usage_rows_do_not_imply_provider_billing(self):
        """Provider usage is observation, never a claim that the provider billed it."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_usage_rows(usage_calls=3)

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["metrics"]["provider_calls_observed"], 3)
        self.assertEqual(row["metrics"]["usage_bearing_calls"], 3)
        self.assertIsNone(row["metrics"]["estimated_cost"])
        self.assertIsNone(row["metrics"]["provider_billed_calls"])
        self.assertIsNone(row["metrics"]["provider_billed_cost"])
        self.assertNotIn("billed", VERDICT_TOOL.render_summary(row).lower())

    def test_terminal_exit_layers_keep_first_failure_when_wrapper_finishes_later(self):
        """A wrapper exit cannot rewrite the driver failure that started the terminal state."""
        self.write_status("RUN_FAILED", exit_code=2, reason="WRAPPER_NONZERO",
                          primary_failure="NO_PROGRESS")
        self.write_validity(False, ("driver_failed",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "attempts.jsonl").write_text(
            json.dumps({"attempt": 0, "exit_code": 9}) + "\n", encoding="utf-8"
        )
        (self.fx.trace / "driver-result.json").write_text(
            json.dumps({"exit_code": 9}), encoding="utf-8"
        )

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["attempt_exit_code"], 9)
        self.assertEqual(row["driver_exit_code"], 9)
        self.assertEqual(row["wrapper_exit_code"], 2)
        self.assertEqual(row["primary_failure"], "NO_PROGRESS")
        self.assertEqual(row["terminal_observation"], "RUN_FAILED")
        self.assertEqual(set(row["gate_exit_codes"]), set(VERDICT_TOOL.REQUIRED_GATES))

    def test_authenticated_primary_failure_is_not_controlled_by_copied_run_state(self):
        """An unbound helper edit/deletion cannot alter or crash verifier semantics."""
        self.write_status("RUN_FAILED", exit_code=2, reason="ordinary detail",
                          primary_failure="BILLED_999_RUB")
        self.write_validity(False, ("driver_failed",))
        self.write_clean_report_artifacts()
        copied = self.copied_tool_tree()
        copied_state = copied / "measure" / "run_state.py"
        source = copied_state.read_text(encoding="utf-8")
        self.assertIn('"BILLING_UNKNOWN",', source)
        copied_state.write_text(source.replace(
            '"BILLING_UNKNOWN",', '"BILLING_UNKNOWN", "BILLED_999_RUB",'
        ), encoding="utf-8")
        copied_status = copied / "eval" / "bench" / "bench-status.py"
        copied_verdict = copied / "eval" / "bench" / "run-verdict.py"
        authority = ["--commitment-file", str(self.fx.commitment),
                     "--commitment-key", str(self.fx.commitment_key)]

        status = subprocess.run(
            [sys.executable, str(copied_status), str(self.fx.trace), *authority, "--json"],
            text=True, capture_output=True, cwd=self.temp.name,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        projected = json.loads(status.stdout)
        self.assertIsNone(projected["primary_failure"])
        self.assertIn("STATUS_INVALID", projected["diagnostics"])

        verdict = subprocess.run(
            [sys.executable, str(copied_verdict), str(self.fx.trace), *authority, "--json"],
            text=True, capture_output=True, cwd=self.temp.name,
        )
        self.assertIn(verdict.returncode, (1, 2), verdict.stderr)
        row = json.loads(verdict.stdout)
        self.assertTrue(row["authenticated"])
        self.assertIsNone(row.get("primary_failure"))
        self.assertNotIn("Traceback", verdict.stderr)

        copied_validity = load("copied_validate_run", copied / "eval" / "bench" / "validate-run.py")
        copied_validity.state(str(self.fx.trace), "VERIFYING", "run-001", self.manifest)
        self.assertEqual(json.loads((self.fx.trace / "status.json").read_text())["phase"], "VERIFYING")
        copied_state.unlink()
        with self.assertRaises(copied_validity.ValidityError) as missing_state:
            copied_validity.state(str(self.fx.trace), "VERIFYING", "run-001", self.manifest)
        self.assertEqual(missing_state.exception.code, "authority_import_failed")

        deleted_status = subprocess.run(
            [sys.executable, str(copied_status), str(self.fx.trace), *authority, "--json"],
            text=True, capture_output=True, cwd=self.temp.name,
        )
        self.assertEqual(deleted_status.returncode, 0, deleted_status.stderr)
        self.assertNotIn("Traceback", deleted_status.stderr)
        for tool in (copied_status, copied_verdict):
            probe = subprocess.run([sys.executable, str(tool), "--help"],
                                   text=True, capture_output=True, cwd=self.temp.name)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertNotIn("Traceback", probe.stderr)

    def test_missing_driver_receipt_does_not_collapse_into_attempt_layer(self):
        """A final attempt is observed evidence, not an invented driver receipt."""
        self.write_status("RUN_FAILED", exit_code=2, reason="WRAPPER_NONZERO",
                          primary_failure="NO_PROGRESS")
        self.write_validity(False, ("driver_failed",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "attempts.jsonl").write_text(
            json.dumps({"attempt": 0, "exit_code": 9}) + "\n", encoding="utf-8"
        )

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["attempt_exit_code"], 9)
        self.assertIsNone(row["driver_exit_code"])

    def test_local_billing_receipt_never_manufactures_provider_billing(self):
        """Until a provider verifier exists, local JSON remains non-provider evidence."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_usage_rows(usage_calls=2)
        self.write_budget_estimate(2.5)
        self.write_billing_receipt({"calls": 2, "cost": 7.5})

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(result.stdout)["metrics"]
        self.assertEqual(metrics["estimated_cost"], 2.5)
        self.assertIsNone(metrics["provider_billed_calls"])
        self.assertIsNone(metrics["provider_billed_cost"])

    def test_estimate_survives_a_call_without_provider_usage_counters(self):
        """A known reservation estimate does not depend on a provider usage response."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_usage_rows(usage_calls=1, include_usage=False)
        self.write_budget_estimate(1.25)

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(result.stdout)["metrics"]
        self.assertEqual(metrics["provider_calls_observed"], 1)
        self.assertEqual(metrics["usage_bearing_calls"], 0)
        self.assertEqual(metrics["estimated_cost"], 1.25)

    def test_budget_estimate_requires_a_bound_rate_snapshot(self):
        """A terminal estimate is valid only with the budget's checked rate evidence."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_usage_rows(usage_calls=1, include_usage=False)
        self.write_budget_estimate(3.25)

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["metrics"]["estimated_cost"], 3.25)

    def test_stale_rate_snapshot_cannot_support_an_estimate(self):
        """A self-hash is insufficient once the configured rate is stale."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_budget_estimate(3.25, effective_at="2000-01-01T00:00:00Z")

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(result.stdout)["metrics"]
        self.assertIsNone(metrics["estimated_cost"])
        self.assertIsNone(metrics["rate_snapshot"])

    def test_future_or_inconsistent_budget_state_cannot_support_an_estimate(self):
        """Every schema-2 invariant remains required at terminal projection time."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        for mutation in ("future-rate", "inconsistent-observed", "malformed-reason"):
            with self.subTest(mutation=mutation):
                self.write_budget_estimate(3.25)
                path = self.fx.trace / "upstream-budget-state.json"
                budget = json.loads(path.read_text(encoding="utf-8"))
                if mutation == "future-rate":
                    snapshot = budget["rate_snapshot"]
                    snapshot["effective_at"] = "2999-01-01T00:00:00Z"
                    unsigned = {name: value for name, value in snapshot.items() if name != "sha256"}
                    snapshot["sha256"] = hashlib.sha256(json.dumps(
                        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")).hexdigest()
                elif mutation == "inconsistent-observed":
                    budget["observed"]["provider_calls"] = 1
                else:
                    budget["reason"] = "ordinary detail"
                path.write_text(json.dumps(budget), encoding="utf-8")

                result = self.verdict()

                self.assertEqual(result.returncode, 0, result.stderr)
                metrics = json.loads(result.stdout)["metrics"]
                self.assertIsNone(metrics["estimated_cost"])
                self.assertIsNone(metrics["rate_snapshot"])

    def test_malformed_usage_is_not_exact_zero_observation(self):
        """An empty provider usage object is invalid, not a zero-token counter set."""
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            json.dumps({"status": 200, "usage": {}}) + "\n", encoding="utf-8"
        )

        with self.assertRaises(ValueError):
            VERDICT_TOOL.upstream_metrics(self.fx.trace)

    def test_every_malformed_usage_shape_returns_a_normal_invalid_ledger_verdict(self):
        """Bad provider counters cannot escape the fail-closed terminal renderer."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        cases = {
            "string": "bad", "list": [], "null": None, "bool": False,
            "bad-prompt": {"prompt_tokens": True, "completion_tokens": 1},
            "bad-completion": {"prompt_tokens": 1, "completion_tokens": -1},
            "bad-cache": {"prompt_tokens": 1, "completion_tokens": 1,
                          "prompt_tokens_details": {"cached_tokens": "bad"}},
            "bad-reasoning": {"prompt_tokens": 1, "completion_tokens": 1,
                              "completion_tokens_details": {"reasoning_tokens": True}},
        }
        for name, usage in cases.items():
            with self.subTest(name=name):
                (self.fx.trace / "upstream-completed.jsonl").write_text(
                    json.dumps({"status": 200, "usage": usage}) + "\n", encoding="utf-8"
                )
                result = self.verdict()
                self.assertEqual(result.returncode, 1, result.stderr)
                row = json.loads(result.stdout)
                self.assertIn("UPSTREAM_LEDGER_INVALID", row["failures"])
                self.assertEqual(row["metrics"].get("usage_bearing_calls"), None)

    def test_configured_estimate_carries_unverified_rate_assurance(self):
        """A fresh self-hashed rate is a configured estimate, never provider billing."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_budget_estimate(2.5)

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["metrics"]["rate_assurance"], "configured_unverified")
        summary = VERDICT_TOOL.render_summary(row)
        self.assertIn("configured_estimated_cost=2.5", summary)
        self.assertIn("rate_assurance=configured_unverified", summary)
        self.assertNotIn("provider-authenticated", summary)

    def test_missing_estimate_has_no_rate_assurance_or_cost_summary(self):
        """No price evidence must not acquire an assurance label or cost prose."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertIsNone(row["metrics"]["rate_assurance"])
        summary = VERDICT_TOOL.render_summary(row)
        self.assertNotIn("configured_estimated_cost", summary)
        self.assertNotIn("rate_assurance", summary)

    def test_malformed_optional_cost_evidence_is_null_not_a_crash(self):
        """Optional receipt and estimate corruption cannot manufacture a cost claim."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        self.write_usage_rows(usage_calls=1)
        self.write_billing_receipt({"calls": True, "cost": "not-a-number"})

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(result.stdout)["metrics"]
        self.assertIsNone(metrics["estimated_cost"])
        self.assertIsNone(metrics["provider_billed_calls"])
        self.assertIsNone(metrics["provider_billed_cost"])

    def test_running_trace_is_unfinished(self):
        """Catches treating a healthy live process as a completed result."""
        self.write_status()

        result = self.verdict()

        self.assertTrue(TOOL.exists(), result.stderr)
        self.assertEqual(result.returncode, 2, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["state"], "running")
        self.assertFalse(row["finished"])
        self.assertIsNone(row["successful"])
        self.assertIsNone(row["report_correct"])
        self.assertEqual(row["phase"], "QWEN_RUNNING")
        self.assertEqual(row["failures"], [])
        self.assertEqual(row["improvements"], [])

    def test_accepted_trace_with_clean_replay_is_correct(self):
        """Catches reporting success without all four gates and reproducible evidence."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()

        result = self.verdict()

        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertTrue(row["finished"])
        self.assertTrue(row["successful"])
        self.assertTrue(row["report_correct"])
        self.assertEqual(row["report_correctness_scope"], "sealed-contract-gates")
        self.assertEqual(row["failures"], [])
        self.assertEqual(
            row["metrics"]["gate_exits"],
            {"citecheck": 0, "reportcheck": 0, "statecheck": 0, "triagecheck": 0},
        )
        self.assertEqual(row["improvements"], [])

    @mock.patch.object(
        VERDICT_TOOL.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0),
    )
    def test_replay_resolves_bash_from_path_on_nixos(self, run):
        """Catches hard-coding /bin/bash, which does not exist on NixOS."""
        self.write_clean_report_artifacts()
        nixos_bash = "/run/current-system/sw/bin/bash"

        with mock.patch.object(VERDICT_TOOL.shutil, "which", return_value=nixos_bash):
            result = VERDICT_TOOL.replay(self.fx.trace)

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(command[0], nixos_bash)
        self.assertIn("/run/current-system/sw/bin", environment["PATH"].split(":"))

    def test_trace_only_discovers_and_authenticates_manifest_authority(self):
        """Catches forcing the operator to recover authority paths already in the manifest."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        controller_key = self.fx.commitment_key.with_name("controller.key")
        self.fx.commitment_key.replace(controller_key)
        self.fx.commitment_key = controller_key

        result = self.verdict_auto()

        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertTrue(row["successful"])
        self.assertTrue(row["report_correct"])
        self.assertTrue(row["authenticated"])

    def test_direct_paid_trace_is_verifiable_but_never_successful(self):
        """Catches an uncontrolled trace being promoted to authoritative success."""
        (self.fx.trace / "run-manifest.json").unlink()
        self.write_status("ACCEPTED", run_tag=self.fx.trace.name)
        self.write_clean_report_artifacts()
        (self.fx.trace / "candidate.json").write_text("{}\n", encoding="utf-8")

        result = self.verdict_auto()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertTrue(row["finished"])
        self.assertFalse(row["successful"])
        self.assertFalse(row["report_correct"])
        self.assertFalse(row["authenticated"])
        self.assertEqual(row["authority"], "uncontrolled-local")
        self.assertIn("AUTHORITY_UNCONTROLLED", row["failures"])
        self.assertIn(
            "USE_AUTHENTICATED_CONTROLLER_NEXT_RUN",
            {item["code"] for item in row["improvements"]},
        )

    def test_direct_accepted_status_without_candidate_is_not_success(self):
        """Catches trusting an uncontrolled status after its candidate disappeared."""
        (self.fx.trace / "run-manifest.json").unlink()
        self.write_status("ACCEPTED", run_tag=self.fx.trace.name)
        self.write_clean_report_artifacts()

        result = self.verdict_auto()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(row["successful"])
        self.assertIn("CANDIDATE_MISSING", row["failures"])
        self.assertIn(
            "PRESERVE_CANDIDATE_ARTIFACT",
            {item["code"] for item in row["improvements"]},
        )

    def test_manifest_entry_cannot_downgrade_to_uncontrolled_on_auth_failure(self):
        """Catches a broken manifest link silently selecting the weaker direct mode."""
        manifest = self.fx.trace / "run-manifest.json"
        manifest.unlink()
        manifest.symlink_to(self.fx.trace / "missing-manifest.json")
        self.write_status("ACCEPTED", run_tag=self.fx.trace.name)
        self.write_clean_report_artifacts()

        result = self.verdict_auto()

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("TRACE_AUTHORITY_UNRESOLVED", result.stderr)

    def test_rejected_citations_name_the_report_improvement(self):
        """Catches a blocking citation gate that produces no actionable diagnosis."""
        self.write_status("REJECTED", exit_code=4)
        self.write_validity(False, ("citation_failed",))
        self.write_clean_report_artifacts()
        gates_path = self.fx.trace / "gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        gates["verdict"] = "blocking"
        gates["gates"]["citecheck"].update({"exit_code": 1, "blocking": 3})
        gates_path.write_text(json.dumps(gates), encoding="utf-8")

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertTrue(row["finished"])
        self.assertFalse(row["successful"])
        self.assertFalse(row["report_correct"])
        self.assertIn("GATES_BLOCKING", row["failures"])
        self.assertIn("gate_blocking", row["metrics"])
        self.assertEqual(row["metrics"]["gate_blocking"]["citecheck"], 3)
        self.assertIn(
            "FIX_CITATION_DEFECTS",
            {item["code"] for item in row["improvements"]},
        )

    def test_wait_returns_the_terminal_verdict(self):
        """Catches --wait returning the first non-terminal projection."""
        self.write_status()
        self.write_validity()
        self.write_clean_report_artifacts()
        timer = threading.Timer(0.15, lambda: self.write_status("ACCEPTED"))
        timer.start()
        self.addCleanup(timer.cancel)

        result = self.verdict(
            "--wait", "--poll-seconds", "0.02", "--timeout-seconds", "2"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["phase"], "ACCEPTED")
        self.assertTrue(row["finished"])

    def test_compaction_clip_names_the_lane_fix(self):
        """Catches recommending a settings tweak for a provider generation clock."""
        self.write_status(
            "RUN_FAILED", exit_code=5, reason="COMPACTION_OUTPUT_CLIPPED"
        )
        self.write_validity(False, ("transport_failed",))
        self.write_clean_report_artifacts()
        lane_path = self.fx.trace / "lane-integrity.json"
        lane_path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "verdict": "breach",
                    "reason": "COMPACTION_OUTPUT_CLIPPED",
                    "detail": "2 memory calls ended with finish_reason=length",
                }
            ),
            encoding="utf-8",
        )

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("LANE_INTEGRITY_BREACH", row["failures"])
        improvement = {item["code"]: item for item in row["improvements"]}
        self.assertIn("USE_UNWINDOWED_OR_198S_LANE", improvement)
        self.assertEqual(
            improvement["USE_UNWINDOWED_OR_198S_LANE"]["evidence"]["reason"],
            "COMPACTION_OUTPUT_CLIPPED",
        )

    def test_upstream_ledger_exposes_clipped_memory_and_token_metrics(self):
        """Catches a false green that ignores a clipped compaction ledger row."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        rows = [
            {
                "status": 200,
                "finish_reason": "stop",
                "request_max_tokens": 500,
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 200,
                    "prompt_tokens_details": {"cached_tokens": 700},
                },
            },
            {
                "status": 200,
                "finish_reason": "length",
                "clipped_request_class": "compaction",
                "request_max_tokens": 20000,
                "usage": {
                    "prompt_tokens": 2000,
                    "completion_tokens": 20000,
                    "prompt_tokens_details": {"cached_tokens": 800},
                },
            },
        ]
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("COMPACTION_OUTPUT_CLIPPED", row["failures"])
        self.assertEqual(row["metrics"]["upstream_calls"], 2)
        self.assertEqual(row["metrics"]["prompt_tokens"], 3000)
        self.assertEqual(row["metrics"]["output_tokens"], 20200)
        self.assertEqual(row["metrics"]["cached_prompt_tokens"], 1500)
        self.assertEqual(row["metrics"]["cache_hit_percent"], 50.0)
        self.assertEqual(row["metrics"]["length_stops"], 1)
        self.assertEqual(row["metrics"]["peak_prompt_plus_max_tokens"], 22000)

    def test_reasoning_snapshot_clip_names_the_actual_budget_fix(self):
        """Catches treating an unwindowed reasoning-token clip as a provider clock."""
        self.write_status(
            "RUN_FAILED", exit_code=5, reason="COMPACTION_OUTPUT_CLIPPED"
        )
        self.write_validity(False, ("transport_failed",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "lane-integrity.json").write_text(
            json.dumps({"schema": 1, "verdict": "breach",
                        "reason": "COMPACTION_OUTPUT_CLIPPED", "detail": "snapshot clipped"}),
            encoding="utf-8",
        )
        (self.fx.trace / "run-inputs.json").write_text(
            json.dumps({"generation_window": {"generation_window_seconds": -1.0}}),
            encoding="utf-8",
        )
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            json.dumps({
                "status": 200,
                "finish_reason": "length",
                "clipped_request_class": "state_snapshot",
                "request_max_tokens": 20000,
                "usage": {
                    "prompt_tokens": 128993,
                    "completion_tokens": 20000,
                    "completion_tokens_details": {"reasoning_tokens": 13427},
                },
            }) + "\n",
            encoding="utf-8",
        )

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("COMPACTION_OUTPUT_CLIPPED", row["failures"])
        self.assertEqual(row["metrics"]["reasoning_tokens"], 13427)
        self.assertEqual(row["metrics"]["snapshot_visible_tokens"], 6573)
        repairs = {item["code"] for item in row["improvements"]}
        self.assertIn("SEPARATE_REASONING_FROM_SNAPSHOT_BUDGET", repairs)
        self.assertNotIn("USE_UNWINDOWED_OR_198S_LANE", repairs)

    def test_reasoning_compaction_clip_uses_the_same_memory_budget_fix(self):
        """Catches reasoning-clipped compaction falling back to the old clock advice."""
        self.write_status(
            "RUN_FAILED", exit_code=5, reason="COMPACTION_OUTPUT_CLIPPED"
        )
        self.write_validity(False, ("transport_failed",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "lane-integrity.json").write_text(
            json.dumps({"schema": 1, "verdict": "breach",
                        "reason": "COMPACTION_OUTPUT_CLIPPED", "detail": "compaction clipped"}),
            encoding="utf-8",
        )
        (self.fx.trace / "run-inputs.json").write_text(
            json.dumps({"generation_window": {"generation_window_seconds": -1.0}}),
            encoding="utf-8",
        )
        (self.fx.trace / "upstream-completed.jsonl").write_text(
            json.dumps({
                "status": 200,
                "finish_reason": "length",
                "clipped_request_class": "compaction",
                "request_max_tokens": 20000,
                "usage": {
                    "prompt_tokens": 120000,
                    "completion_tokens": 20000,
                    "completion_tokens_details": {"reasoning_tokens": 12000},
                },
            }) + "\n",
            encoding="utf-8",
        )

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(row["metrics"]["memory_reasoning_tokens"], 12000)
        self.assertEqual(row["metrics"]["memory_visible_tokens"], 8000)
        repairs = {item["code"] for item in row["improvements"]}
        self.assertIn("SEPARATE_REASONING_FROM_SNAPSHOT_BUDGET", repairs)
        self.assertNotIn("USE_UNWINDOWED_OR_198S_LANE", repairs)

    def test_each_report_gate_names_its_own_repair(self):
        """Catches collapsing distinct report defects into one generic recommendation."""
        expected = {
            "triagecheck": "CLOSE_WORKLIST_GAPS",
            "statecheck": "ADD_MISSING_STATE_EVIDENCE",
            "reportcheck": "REPAIR_REPORT_CONTRACT",
        }
        self.write_status("REJECTED", exit_code=4)
        self.write_validity(False, ("gate_failed",))
        for gate, code in expected.items():
            with self.subTest(gate=gate):
                self.write_clean_report_artifacts()
                gates_path = self.fx.trace / "gates.json"
                gates = json.loads(gates_path.read_text(encoding="utf-8"))
                gates["verdict"] = "blocking"
                gates["gates"][gate].update({"exit_code": 1, "blocking": 2})
                gates_path.write_text(json.dumps(gates), encoding="utf-8")

                result = self.verdict()

                self.assertEqual(result.returncode, 1, result.stderr)
                row = json.loads(result.stdout)
                self.assertIn(code, {item["code"] for item in row["improvements"]})

    def test_live_gate_shapes_name_enum_and_label_parser_fixes(self):
        """Catches describing validator defects as report citation or content repairs."""
        self.write_status("RUN_FAILED", exit_code=5)
        self.write_validity(False, ("gate_failed",))
        self.write_clean_report_artifacts()
        gates_path = self.fx.trace / "gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        gates["verdict"] = "blocking"
        gates["gates"]["citecheck"] = {
            "exit_code": 1,
            "blocking": 1,
            "json": {"report_evidence": {"enum_decode": {
                "blocking": 1,
                "items": [{"kind": "unknown_value", "field": "status",
                           "value": 3221549076, "line": 63}],
            }}},
        }
        gates["gates"]["reportcheck"] = {
            "exit_code": 1,
            "blocking": 2,
            "json": {"defects": [
                {"defect": "label_unknown", "detail": "ADMINI"},
                {"defect": "label_unknown", "detail": "IPSERVER"},
            ]},
        }
        gates_path.write_text(json.dumps(gates), encoding="utf-8")

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        repairs = {item["code"] for item in json.loads(result.stdout)["improvements"]}
        self.assertIn("FIX_UNKNOWN_ENUM_DECODE", repairs)
        self.assertIn("FIX_LABEL_BOUNDARY_PARSER", repairs)
        self.assertNotIn("FIX_CITATION_DEFECTS", repairs)
        self.assertNotIn("REPAIR_REPORT_CONTRACT", repairs)

    def test_changed_frozen_arm_is_an_explicit_failure(self):
        """Catches hiding a measurement-input mutation inside a generic gate failure."""
        self.write_status("RUN_FAILED", exit_code=5)
        self.write_validity(False, ("gate_failed",))
        self.write_clean_report_artifacts()
        gates_path = self.fx.trace / "gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        gates["verdict"] = "blocking"
        gates["arm_intact"] = False
        gates_path.write_text(json.dumps(gates), encoding="utf-8")
        (self.fx.trace / "arm-integrity.json").write_text(
            json.dumps({"schema": 1, "intact": False, "changed": [
                {"path": "reference/report-contract.corporate.json"},
                {"path": "reference/enum-tables.tsv"},
            ]}),
            encoding="utf-8",
        )

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("ARM_MUTATED", row["failures"])
        repair = next(item for item in row["improvements"]
                      if item["code"] == "PREVENT_ARM_MUTATION")
        self.assertEqual(repair["evidence"]["changed"], [
            "reference/report-contract.corporate.json",
            "reference/enum-tables.tsv",
        ])

    def test_replay_divergence_blocks_success_and_names_the_fix(self):
        """Catches trusting recorded clean gates when the sealed replay diverges."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        replay = self.fx.trace / "replay.sh"
        replay.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("REPLAY_FAILED", row["failures"])
        self.assertFalse(row["report_correct"])
        self.assertIn(
            "REPAIR_REPLAY_DIVERGENCE",
            {item["code"] for item in row["improvements"]},
        )

    def test_plain_output_answers_the_operator_questions(self):
        """Catches making a human decode JSON to learn the final verdict."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()

        result = self.verdict_text()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FINISHED run-001 ACCEPTED", result.stdout)
        self.assertIn("successful=yes report_correct=yes", result.stdout)
        self.assertIn("report_scope=sealed-contract-gates", result.stdout)
        self.assertIn("failures=none", result.stdout)
        self.assertIn("improvements=none", result.stdout)

    def test_missing_gate_blocking_count_is_unknown_not_clean(self):
        """Catches exit zero being trusted when a gate emitted no machine verdict."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        gates_path = self.fx.trace / "gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        gates["gates"]["reportcheck"]["blocking"] = None
        gates_path.write_text(json.dumps(gates), encoding="utf-8")

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertFalse(row["report_correct"])
        self.assertIn("GATE_RESULT_UNKNOWN", row["failures"])
        self.assertIn(
            "RECORD_MACHINE_GATE_RESULTS",
            {item["code"] for item in row["improvements"]},
        )

    def test_malformed_gate_signals_are_unknown_not_blocking_counts(self):
        """Catches booleans or negative counts being accepted as integer gate evidence."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        gates_path = self.fx.trace / "gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        gates["gates"]["reportcheck"].update({"exit_code": False, "blocking": -1})
        gates_path.write_text(json.dumps(gates), encoding="utf-8")

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("GATE_RESULT_UNKNOWN", row["failures"])
        repair = next(item for item in row["improvements"]
                      if item["code"] == "RECORD_MACHINE_GATE_RESULTS")
        self.assertIn("reportcheck", repair["evidence"]["gates"])

    def test_malformed_gate_row_returns_a_verdict_instead_of_crashing(self):
        """Catches a null gate row escaping the wrapper's failure report."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        gates_path = self.fx.trace / "gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        gates["gates"]["citecheck"] = None
        gates_path.write_text(json.dumps(gates), encoding="utf-8")

        result = self.verdict()

        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)
        self.assertFalse(row["report_correct"])
        self.assertIn("GATE_RESULT_UNKNOWN", row["failures"])

    def test_missing_report_names_the_delivery_fix(self):
        """Catches a terminal trace with no report and no repair action."""
        self.write_status("RUN_FAILED", exit_code=3)
        self.write_validity(False, ("missing_deliverable",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "work" / "report.md").unlink()

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("REPORT_MISSING", row["failures"])
        self.assertIn("WRITE_REPORT_ARTIFACT", {item["code"] for item in row["improvements"]})

    def test_wrong_model_lane_names_the_provider_fix(self):
        """Catches a substituted model being described as a report repair."""
        self.write_status("RUN_FAILED", exit_code=5, reason="RETURNED_MODEL_MISMATCH")
        self.write_validity(False, ("identity_mismatch",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "lane-integrity.json").write_text(
            json.dumps({"schema": 1, "verdict": "breach",
                        "reason": "RETURNED_MODEL_MISMATCH", "detail": "wrong family"}),
            encoding="utf-8",
        )

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("USE_EXACT_MODEL_LANE", {item["code"] for item in row["improvements"]})

    def test_empty_lane_integrity_is_invalid_not_clean(self):
        """Catches an empty lane artifact bypassing the exact-model requirement."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        (self.fx.trace / "lane-integrity.json").write_text("{}\n", encoding="utf-8")

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(row["successful"])
        self.assertFalse(row["report_correct"])
        self.assertIn("LANE_INTEGRITY_INVALID", row["failures"])
        self.assertIn(
            "REPAIR_LANE_INTEGRITY_EVIDENCE",
            {item["code"] for item in row["improvements"]},
        )

    def test_unsealed_trace_names_the_evidence_fix(self):
        """Catches an unreplayable trace producing no evidence-preservation action."""
        self.write_status("RUN_FAILED", exit_code=6)
        self.write_validity(False, ("trace_unsealed",))
        self.write_clean_report_artifacts()
        (self.fx.trace / "seal-failure.json").write_text(
            json.dumps({"schema": 1, "stage": "audit"}), encoding="utf-8"
        )

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(row["report_correct"])
        self.assertIn("REBUILD_SELF_CONTAINED_TRACE",
                      {item["code"] for item in row["improvements"]})

    def test_malformed_upstream_ledger_names_the_observability_fix(self):
        """Catches unreadable paid-call evidence being silently ignored."""
        self.write_status("ACCEPTED")
        self.write_validity()
        self.write_clean_report_artifacts()
        (self.fx.trace / "upstream-completed.jsonl").write_text("{broken\n", encoding="utf-8")

        result = self.verdict()

        row = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("UPSTREAM_LEDGER_INVALID", row["failures"])
        self.assertIn("REPAIR_UPSTREAM_LEDGER", {item["code"] for item in row["improvements"]})

    def test_target_probe_authority_accepts_the_real_terminal_contract_without_validity(self):
        trace = self.stage_target_probe_trace()
        result = subprocess.run([sys.executable, str(TOOL), str(trace), "--target-probe", "--json"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertTrue(row["authenticated"])
        self.assertEqual(row["authority"], "operator-approved-target-probe")
        self.assertTrue(row["successful"])
        self.assertEqual(row["attempt_exit_code"], 0)
        self.assertEqual(row["driver_exit_code"], 0)
        self.assertEqual(row["wrapper_exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
