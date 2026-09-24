#!/usr/bin/env python3
"""Stage handoff -> the harness continues with the exact /sherlock line (fixture: 2026-09-24 claude small test 2)."""
import importlib.util, json, os, shutil, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("runner_stage", HERE / "run-v52-gpt55-comparison.py")
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)
HANDOFF = HERE / "fixtures/claude-smalltest2-20260924-handoff.txt"
HOOK = HERE / "fixtures/claude-smalltest2-20260924-stop-hook.jsonl"


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="stage-")); (self.d / "work").mkdir()
        self.since = time.time() - 60
        shutil.copy(HANDOFF, self.d / "work/handoff.txt")
        row = json.loads(HOOK.read_text().splitlines()[-1])
        row["ts"] = R.datetime.datetime.now(R.datetime.timezone.utc).isoformat()
        (self.d / "hook.jsonl").write_text(json.dumps(row) + "\n")
    def tearDown(self):
        shutil.rmtree(self.d)

    def test_real_handoff_yields_exact_continuation_line(self):
        got = R.continuation_prompt(self.d / "work", self.d / "hook.jsonl", self.since)
        self.assertTrue(got.startswith("/sherlock ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ "))
        self.assertTrue(got.endswith("— СТУПЕНЬ draft"))
        self.assertIn(got, HANDOFF.read_text())

    def test_stale_handoff_file_is_not_continued(self):
        old = self.since - 3600; os.utime(self.d / "work/handoff.txt", (old, old))
        self.assertIsNone(R.continuation_prompt(self.d / "work", self.d / "hook.jsonl", self.since))

    def test_hook_decision_must_be_this_sessions_accepted_handoff(self):
        for row in ({"decision": "allow", "reason": "Sherlock inactive"},
                    {"decision": "block", "reason": "Sherlock stage handoff accepted"},
                    {"decision": "allow", "reason": "Sherlock stage handoff accepted", "ts": "2000-01-01T00:00:00+00:00"}):
            row.setdefault("ts", R.datetime.datetime.now(R.datetime.timezone.utc).isoformat())
            (self.d / "hook.jsonl").write_text(json.dumps(row) + "\n")
            self.assertIsNone(R.continuation_prompt(self.d / "work", self.d / "hook.jsonl", self.since), row)

    def test_no_hook_log_or_no_line_means_no_continuation(self):
        self.assertIsNone(R.continuation_prompt(self.d / "work", self.d / "missing.jsonl", self.since))
        (self.d / "work/handoff.txt").write_text("СТУПЕНЬ ЗАВЕРШЕНА: triage\n")
        self.assertIsNone(R.continuation_prompt(self.d / "work", self.d / "hook.jsonl", self.since))

    def test_diagnostics_count_skill_calls_across_sessions(self):
        c = self.d / "control"; c.mkdir()
        call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "skill", "input": {"skill": "sherlock"}}]}}
        (c / "qwen-output.json").write_text(json.dumps([call])); (c / "qwen-output-s2.json").write_text(json.dumps([call]))
        self.assertEqual(R.finalizer_diagnostics(self.d / "work", c)["skill_invocations"], 2)


if __name__ == "__main__":
    unittest.main()
