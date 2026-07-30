#!/usr/bin/env python3
"""Tests for report-case.py — the ONLY thing that writes the measurement artifact.

test_gate.py stubs this file out entirely (it is testing gate.sh's orchestration),
so until now nothing exercised the row that actually gets quoted. That is how a live
row came to report files_opened=3 for a case whose corpus held one log file.

No network and no metered judge: SHERLOCK_JUDGE_STUB feeds report-case.py the judge's
JSON from a file, and every row it produces is stamped judge_stub:true so a stubbed
number can never be mistaken for a measured one.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
REPORTER = os.path.join(MEASURE, "report-case.py")
FIXTURE = os.path.join(HERE, "fixtures", "real-stream-excerpt.jsonl")

REPORT_BODY = ("## Что произошло\nNPE в PromoCodeResolver, checkout-api.log:3-6.\n"
               "## Корневая причина\nPromoCode.normalized() возвращает null.\n") * 20


class Harness(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="report-case-test-")

    def build(self, report=REPORT_BODY, kind="capability_micro", stream=None,
              judge='{"found": true, "why": "identifies the NPE"}'):
        case_dir = os.path.join(self.d, "cases", "cap-multiline-stitching")
        os.makedirs(os.path.join(case_dir, "corpus"), exist_ok=True)
        with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "cap-multiline-stitching", "kind": kind,
                       "title": "NPE hidden in a stack trace", "root_cause": "null promo",
                       "requires": "multiline stitching", "files": ["checkout-api.log"],
                       "proof_locations": [{"file": "checkout-api.log", "line_start": 3,
                                            "line_end": 6, "note": "the NPE"}]}, fh)
        run_dir = os.path.join(self.d, "runs", "r1")
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(report)
        with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "cap-multiline-stitching", "arm": "v6",
                       "duration_s": 78, "exit_code": 0}, fh)
        # The stream is the REAL captured one unless a test supplies its own.
        body = stream if stream is not None else open(FIXTURE, encoding="utf-8").read()
        with open(os.path.join(run_dir, "stream.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(body)
        judge_path = os.path.join(self.d, "judge.json")
        with open(judge_path, "w", encoding="utf-8") as fh:
            fh.write(judge)
        return case_dir, run_dir, judge_path

    def run_reporter(self, case_dir, run_dir, judge_path, tier="0"):
        results = os.path.join(self.d, "results.jsonl")
        env = dict(os.environ)
        env["SHERLOCK_JUDGE_STUB"] = judge_path
        # No JUDGE_API_KEY: a real call would raise, so reaching the network fails loudly.
        env.pop("JUDGE_API_KEY", None)
        p = subprocess.run([sys.executable, REPORTER, "--case", case_dir, "--run", run_dir,
                            "--tier", tier, "--results", results],
                           capture_output=True, text=True, env=env, timeout=60)
        rows = []
        if os.path.exists(results):
            with open(results, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        return p, rows


class EveryFieldOfTheEmittedRow(Harness):
    def test_the_row_is_exactly_what_a_real_run_should_produce(self):
        p, rows = self.run_reporter(*self.build())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["case_id"], "cap-multiline-stitching")
        self.assertEqual(row["arm"], "v6")
        self.assertEqual(row["tier"], "0")
        self.assertEqual(row["diagnosis"], "ok")
        self.assertIs(row["judge_found"], True)
        self.assertEqual(row["why"], "identifies the NPE")
        self.assertEqual(row["requires"], "multiline stitching")
        # Important-8: the real stream touches a directory, case.json and ONE log file.
        self.assertEqual(row["files_opened"], 1)
        self.assertEqual(row["proofs_reached"], 1)
        self.assertEqual(row["reach_verdict"], "reached")
        self.assertEqual(row["proofs_unknown"], [])
        self.assertIsNone(row["collapse_reason"])
        self.assertEqual(row["report_chars"], len(REPORT_BODY))
        # 4 tool_use blocks in the excerpt: list_directory, read_file(case.json),
        # run_shell_command(wc -l), read_file(checkout-api.log).
        self.assertEqual(row["tool_calls"], 4)
        self.assertTrue(row["run_dir"].endswith("runs/r1"))
        self.assertIs(row["judge_stub"], True)
        self.assertIn("JUDGE STUB ACTIVE", p.stdout)

    def test_a_judge_miss_on_a_read_proof_is_a_reasoning_row(self):
        p, rows = self.run_reporter(*self.build(judge='{"found": false, "why": "no"}'))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "reasoning")
        self.assertEqual(rows[0]["proofs_reached"], 1)

    def test_a_dir_scan_only_run_is_inconclusive_never_coverage(self):
        only_dir = "\n".join(l for l in open(FIXTURE, encoding="utf-8").read().splitlines()
                             if "list_directory" in l)
        p, rows = self.run_reporter(*self.build(stream=only_dir + "\n",
                                                judge='{"found": false, "why": "no"}'))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["reach_verdict"], "unknown")
        self.assertEqual(rows[0]["diagnosis"], "inconclusive")
        self.assertEqual(rows[0]["files_opened"], 0)


class TheJudgeIsSkippedWhenThereIsNothingToJudge(Harness):
    """Important-10: the deterministic collapse check is free, the judge is metered."""

    def test_a_collapsed_report_is_diagnosed_without_calling_the_judge(self):
        # The stub file is deliberately NOT valid judge JSON: if the judge is called
        # at all, parse_verdict raises and the run fails loudly.
        case_dir, run_dir, judge = self.build(
            report="Отчёт выше уже содержит все находки.", judge="THIS IS NOT JSON")
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "collapse")
        self.assertIs(rows[0]["judge_found"], False)
        self.assertIn("judge skipped", rows[0]["why"])
        self.assertIn("banned phrase", rows[0]["collapse_reason"])

    def test_a_short_micro_report_still_reaches_the_judge(self):
        """The other half of Important-7, at the artifact layer: 700 chars is under
        the full-corpus floor of 2000 but over the micro floor, so it must be judged,
        not written off as a collapse."""
        case_dir, run_dir, judge = self.build(report="а" * 700, kind="capability_micro")
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "ok")
        self.assertIsNone(rows[0]["collapse_reason"])

    def test_the_same_report_on_a_defect_slice_is_a_collapse(self):
        case_dir, run_dir, judge = self.build(report="а" * 700, kind="defect_slice",
                                              judge="THIS IS NOT JSON")
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "collapse")


if __name__ == "__main__":
    unittest.main(verbosity=2)
