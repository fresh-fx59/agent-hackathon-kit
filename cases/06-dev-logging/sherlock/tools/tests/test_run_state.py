import json
import pathlib
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
