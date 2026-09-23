#!/usr/bin/env python3
"""Quota 429s end as quota_exhausted (named provider/plan/reset), fixture = 2026-09-23 small-test ledger."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("runner_quota", HERE / "run-v52-gpt55-comparison.py")
RUNNER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RUNNER)
FIXTURE = HERE / "fixtures" / "smalltest-20260923-quota-upstream.jsonl"


class QuotaTest(unittest.TestCase):
    def setUp(self):
        self.trace = Path(tempfile.mkdtemp(prefix="quota-")); 
    def tearDown(self):
        shutil.rmtree(self.trace)

    def test_saved_small_test_ledger_is_quota_exhausted_not_transport(self):
        shutil.copy(FIXTURE, self.trace / "upstream.jsonl")
        with self.assertRaises(RUNNER.TerminalFailure) as raised:
            RUNNER.validate_terminal_ledger(self.trace, strict_buffer_enabled=True)
        exc = raised.exception
        self.assertEqual(exc.status, "quota_exhausted")
        d = exc.details
        self.assertEqual((d["provider"], d["plan_type"], d["quota_rows"], d["first_kind"]),
                         ("codex", "free", 11, "usage_limit_reached"))
        self.assertEqual(d["resets_at_iso"][:10], "2026-10-19")
        for needle in ("provider=codex", "plan_type=free", "resets_at=2026-10-19"):
            self.assertIn(needle, str(exc))

    def test_watch_fires_on_first_quota_row_even_if_appended_in_pieces(self):
        rows = FIXTURE.read_bytes().split(b"\n")
        ok, first_quota = b"\n".join(rows[:44]) + b"\n", rows[44] + b"\n"
        led = self.trace / "upstream.jsonl"
        led.write_bytes(ok)
        watch = RUNNER.QuotaWatch(self.trace)
        self.assertIsNone(watch.poll())
        with open(led, "ab") as fh: fh.write(first_quota[:50])
        self.assertIsNone(watch.poll())          # half a line is not a signal
        with open(led, "ab") as fh: fh.write(first_quota[50:])
        exc = watch.poll()
        self.assertEqual(exc.status, "quota_exhausted")
        self.assertEqual((exc.details["quota_rows"], exc.details["plan_type"]), (1, "free"))

    def test_non_quota_429_and_healthy_rows_are_not_quota(self):
        self.assertIsNone(RUNNER.quota_signal({"status": 429, "upstream_error": '{"error":{"type":"rate_limit"}}'}))
        self.assertIsNone(RUNNER.quota_signal({"status": 200, "upstream_error": None}))
        q = RUNNER.quota_signal({"status": 429, "ts_ms": 1000000,
            "upstream_error": '{"error":{"code":"model_cooldown","provider":"codex","reset_seconds":60}}'})
        self.assertEqual((q["kind"], q["provider"], q["resets_at"]), ("model_cooldown", "codex", 1060))

    def test_clipped_error_json_is_salvaged(self):
        q = RUNNER.quota_signal({"status": 429, "upstream_error":
            '{"error":{"type":"usage_limit_reached","plan_type":"free","resets_at":1792419864,"eligible_promo":{"mess'})
        self.assertEqual((q["plan_type"], q["resets_at_iso"]), ("free", "2026-10-19T14:24:24Z"))

    def test_healthy_prefix_of_fixture_still_passes(self):
        rows = FIXTURE.read_bytes().split(b"\n")[:44]
        (self.trace / "upstream.jsonl").write_bytes(b"\n".join(rows) + b"\n")
        self.assertEqual(len(RUNNER.validate_terminal_ledger(self.trace, strict_buffer_enabled=True)), 44)


if __name__ == "__main__":
    unittest.main()
