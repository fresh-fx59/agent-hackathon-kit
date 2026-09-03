import fcntl
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import threading
import unittest

MEASURE = pathlib.Path(__file__).resolve().parents[1].parent / "measure"
sys.path.insert(0, str(MEASURE))
import run_state  # noqa: E402


class RunStateTests(unittest.TestCase):
    def test_write_status_replaces_complete_json(self):
        with self.subTest("atomic snapshot"):
            import tempfile
            with tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "status.json"
                row = run_state.write_status(str(path), run_tag="r1", phase="QWEN_RUNNING")
                self.assertEqual(json.loads(path.read_text())["phase"], "QWEN_RUNNING")
                self.assertEqual(set(row), set(run_state.STATUS_FIELDS))
                self.assertFalse(list(pathlib.Path(directory).glob(".status.json.*")))

    def test_append_event_never_rewrites_prior_rows(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status-events.jsonl"
            first = run_state.append_event(str(path), "RUN_STARTED", run_tag="r1")
            before = path.read_bytes()
            run_state.append_event(str(path), "ATTEMPT_STARTED", run_tag="r1", attempt=0)
            self.assertTrue(path.read_bytes().startswith(before))
            self.assertEqual(first["event"], "RUN_STARTED")

    def test_concurrent_readers_only_see_complete_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            import threading
            errors = []
            def writer():
                for i in range(30): run_state.write_status(str(path), run_tag="r1", phase=str(i))
            def reader():
                for _ in range(100):
                    if path.exists():
                        try:
                            data = json.loads(path.read_text())
                            if set(data) != set(run_state.STATUS_FIELDS): errors.append(data)
                        except (json.JSONDecodeError, OSError): errors.append("partial")
            threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(errors, [])

    def test_secret_shaped_detail_is_rejected_without_echo(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            with self.assertRaises(ValueError) as caught:
                run_state.write_status(str(path), run_tag="r1", detail="Bearer super-secret-token")
            self.assertNotIn("super-secret-token", str(caught.exception))
            self.assertFalse(path.exists())

    def test_unknown_fields_and_non_scalar_values_are_rejected(self):
        with self.assertRaises(ValueError):
            run_state.write_status("/tmp/never-written-status.json", response="model output")
        with self.assertRaises(ValueError):
            run_state.write_status("/tmp/never-written-status.json", detail={"nested": "value"})

    def test_secret_key_value_forms_are_rejected_without_echo(self):
        for value in ("password: hidden-value", "api_key: hidden-value", "token=hidden-value"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    run_state.write_status("/tmp/never-written-status.json", detail=value)
            self.assertNotIn("hidden-value", str(caught.exception))

    def test_cli_set_and_event(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            status = pathlib.Path(directory) / "status.json"
            events = pathlib.Path(directory) / "events.jsonl"
            command = [sys.executable, str(MEASURE / "run_state.py")]
            subprocess.run(command + ["set", str(status), "--run-tag", "r1", "--phase", "DONE"], check=True)
            self.assertEqual(json.loads(status.read_text())["phase"], "DONE")
            subprocess.run(command + ["event", str(events), "RUN_FINISHED", "--run-tag", "r1"], check=True)
            self.assertEqual(json.loads(events.read_text())["event"], "RUN_FINISHED")

    def test_cli_accepts_causal_runner_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            events = pathlib.Path(directory) / "events.jsonl"
            command = [sys.executable, str(MEASURE / "run_state.py"), "event", str(events), "ATTEMPT_FINISHED",
                       "--run-tag", "r1", "--session-id", "session-1", "--reason", "broken_stream",
                       "--exit-code", "0", "--duration-s", "3", "--upstream-log", "upstream.jsonl",
                       "--inflight-path", "upstream-inflight.json"]
            subprocess.run(command, check=True)
            row = json.loads(events.read_text())
            self.assertEqual(row["session_id"], "session-1")
            self.assertEqual(row["exit_code"], "0")

    def test_status_preserves_the_runner_process_proof(self):
        """Replacing the runner facts with the state-writer PID breaks PID-reuse proof."""
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            row = run_state.write_status(
                str(path), run_tag="r1", phase="QWEN_RUNNING", pid=4123,
                process_start_ticks=987654, pgid=4123,
                boot_id_sha256="1" * 64, command_sha256="2" * 64)
            self.assertEqual(
                {name: row[name] for name in (
                    "pid", "process_start_ticks", "pgid", "boot_id_sha256",
                    "command_sha256")},
                {"pid": 4123, "process_start_ticks": 987654, "pgid": 4123,
                 "boot_id_sha256": "1" * 64, "command_sha256": "2" * 64})

    def test_cli_accepts_the_complete_runner_process_proof(self):
        """Dropping any proof field makes a paid child impossible to attribute."""
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            status = pathlib.Path(directory) / "status.json"
            command = [
                sys.executable, str(MEASURE / "run_state.py"), "set", str(status),
                "--run-tag", "r1", "--phase", "QWEN_RUNNING", "--pid", "4123",
                "--process-start-ticks", "987654", "--pgid", "4123",
                "--boot-id-sha256", "1" * 64, "--command-sha256", "2" * 64,
            ]
            subprocess.run(command, check=True)
            row = json.loads(status.read_text())
            self.assertEqual(row["process_start_ticks"], 987654)
            self.assertEqual(row["pgid"], 4123)
            self.assertEqual(row["boot_id_sha256"], "1" * 64)
            self.assertEqual(row["command_sha256"], "2" * 64)

    def test_first_terminal_failure_is_not_replaced(self):
        """A later wrapper observation must retain the original root failure."""
        first = run_state.state_event(
            "RUN_FAILED", exit_code=9, reason="NO_PROGRESS"
        )
        later = run_state.state_event(
            "RUN_FAILED", exit_code=2, reason="WRAPPER_NONZERO", previous=first
        )

        self.assertEqual(later["primary_failure"], "NO_PROGRESS")
        self.assertEqual(later["wrapper_exit_code"], 2)

    def test_terminal_status_persists_the_first_failure_across_later_writes(self):
        """The durable snapshot, not only an in-memory helper, retains first failure."""
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            run_state.write_status(
                str(path), run_tag="r1", phase="RUN_FAILED", exit_code=9,
                reason="NO_PROGRESS",
            )
            later = run_state.write_status(
                str(path), run_tag="r1", phase="RUN_FAILED", exit_code=2,
                reason="WRAPPER_NONZERO",
            )

            self.assertEqual(later["primary_failure"], "NO_PROGRESS")
            self.assertEqual(json.loads(path.read_text())["primary_failure"], "NO_PROGRESS")

    def test_first_terminal_failure_survives_every_later_phase(self):
        """A later non-terminal observer must not erase the original terminal cause."""
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            run_state.write_status(
                str(path), run_tag="r1", phase="RUN_FAILED", exit_code=9,
                reason="NO_PROGRESS",
            )
            later = run_state.write_status(
                str(path), run_tag="r1", phase="VERIFYING", reason="ordinary detail",
            )

            self.assertEqual(later["primary_failure"], "NO_PROGRESS")
            self.assertEqual(json.loads(path.read_text())["primary_failure"], "NO_PROGRESS")

    def test_status_transaction_holds_a_stable_sidecar_lock(self):
        """A second writer cannot observe a half-computed status transaction."""
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            entered = threading.Event()
            release = threading.Event()
            original = run_state._normalize

            def block_normalize(*args, **kwargs):
                row = original(*args, **kwargs)
                entered.set()
                self.assertTrue(release.wait(5), "writer was not released")
                return row

            from unittest import mock
            with mock.patch.object(run_state, "_normalize", side_effect=block_normalize):
                worker = threading.Thread(
                    target=run_state.write_status,
                    args=(str(path),), kwargs={"run_tag": "r1", "phase": "RUN_FAILED",
                                                "reason": "NO_PROGRESS"},
                )
                worker.start()
                self.assertTrue(entered.wait(5), "writer did not enter transaction")
                lock_path = str(path) + ".lock"
                with open(lock_path, "a+", encoding="utf-8") as lock:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        held = True
                    else:
                        held = False
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())

            self.assertTrue(held)

    def test_unrecognized_reason_cannot_become_primary_failure(self):
        """Caller detail is not an open-ended machine-readable failure vocabulary."""
        row = run_state.state_event(
            "RUN_FAILED", exit_code=9, reason="BILLED_999_RUB"
        )

        self.assertIsNone(row["primary_failure"])

    def test_primary_failure_vocabulary_matches_every_current_producer(self):
        """Lane and admission producers cannot silently drift from their consumers."""
        sherlock = MEASURE.parent
        sources = [
            sherlock / "measure" / "lane-audit.py",
            sherlock / "measure" / "lane_guard.py",
        ]
        produced = set().union(*[
            set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', source.read_text()))
            for source in sources
        ])
        expected_lane_codes = {
            "CACHE_TERMS_INCOMPLETE", "COMPACTION_OUTPUT_CLIPPED",
            "EXPECTED_IDENTITY_UNKNOWN", "GENERATION_WINDOW_EXCEEDED",
            "LANE_ABORT_UNREADABLE", "LANE_ACCOUNTING_INCOMPLETE", "LEDGER_EMPTY",
            "LEDGER_MALFORMED", "LEDGER_MISSING", "OUTPUT_BUDGET_EXHAUSTED_BY_REASONING",
            "PER_REQUEST_TOKEN_GATE_BREACHED", "PER_REQUEST_TOKEN_GATE_UNMEASURED",
            "PROMPT_CACHE_COLLAPSE", "REASONING_CONTENT_NOT_RELAYED",
            "RETURNED_MODEL_FAMILY_MISMATCH", "RETURNED_MODEL_UNKNOWN",
            "ROUTE_ADVANCE_COUNTERS_INCONSISTENT", "ROUTE_ADVANCE_HISTORY_UNREADABLE",
            "ROUTE_ADVANCE_UNRECORDED", "ROUTE_IDENTITY_UNREADABLE", "USAGE_UNREADABLE",
        }
        historical_and_spec_codes = {
            "ATTRIBUTION_UNAVAILABLE", "LANE_AUDIT_FAILED", "NO_PROGRESS",
            "CLEAR_NOT_EFFECTIVE", "TARGET_REFUSED", "STAGE_STALLED", "WRAPPER_NONZERO",
            "DRIVER_EXIT", "HARNESS_QUALIFICATION_MISSING", "TARGET_PROBE_NOT_AUTHORIZED",
            "TARGET_PROBE_BUDGET", "TARGET_CONTRACT_FAILED", "TARGET_IDENTITY_MISMATCH",
            "TARGET_IDENTITY_UNVERIFIABLE", "TARGET_RECEIPT_EXPIRED", "TARGET_RECEIPT_USED",
            "APPROVAL_REPLAYED", "FULL_RUN_NOT_AUTHORIZED", "INPUTS_INCOMPARABLE",
            "BILLING_UNKNOWN",
        }
        budget_and_rate_codes = {
            "BUDGET_EXCEEDED", "RATE_SNAPSHOT_INVALID", "RATE_SNAPSHOT_CHANGED",
            "ACTION_BUDGET_INVALID", "MAX_PROVIDER_CALLS", "MAX_PROMPT_TOKENS",
            "MAX_COMPLETION_TOKENS", "MAX_WALL_TIME_S", "MAX_ESTIMATED_COST_RUB",
        }
        self.assertEqual(produced, expected_lane_codes)
        canonical = run_state.PRIMARY_FAILURE_CODES
        self.assertEqual(canonical, produced | historical_and_spec_codes | budget_and_rate_codes)
        self.assertIsNone(run_state.state_event("RUN_FAILED", reason="BILLED_999_RUB")["primary_failure"])
        for code in produced | historical_and_spec_codes:
            with self.subTest(code=code):
                self.assertEqual(run_state.state_event("RUN_FAILED", reason=code)["primary_failure"], code)
        for module_name, path in (
            ("bench_status_vocabulary", sherlock / "eval" / "bench" / "bench-status.py"),
            ("run_verdict_vocabulary", sherlock / "eval" / "bench" / "run-verdict.py"),
        ):
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.PRIMARY_FAILURE_CODES, canonical)

    def test_terminal_state_handles_missing_or_malformed_previous_failure(self):
        """Optional predecessor data cannot make the failure projection crash or lie."""
        missing = run_state.state_event("RUN_FAILED", exit_code=7, reason="DRIVER_EXIT")
        malformed = run_state.state_event(
            "RUN_FAILED", exit_code=2, reason="WRAPPER_NONZERO",
            previous={"primary_failure": ["not", "a", "code"]},
        )

        self.assertEqual(missing["primary_failure"], "DRIVER_EXIT")
        self.assertEqual(malformed["primary_failure"], "WRAPPER_NONZERO")


if __name__ == "__main__":
    unittest.main()
