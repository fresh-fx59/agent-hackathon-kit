#!/usr/bin/env python3
"""Tests for eval/score.py — the scorer that produces every number this project quotes.

It had no tests at all, which is exactly backwards: `measure/` is thoroughly tested
and produces diagnoses, while THIS file produces the recall percentages that end up
in the deck and the README. A silent bug here is a wrong number nobody can trace.

The two behaviours that matter:

  * `--all-rows` must score EVERY row. The default keeps only the latest row per
    (dataset, arm), which silently discards repetitions — and repetitions are the
    only defence against judge noise, measured at +/-1 defect on this corpus.
  * every emitted score must carry `rep`, `ledger_line` and `judge_model`, so a
    quoted number traces back to the exact run and judge that produced it, and rows
    judged by different judges can never be silently compared.

No network and no metered judge: SHERLOCK_SCORE_STUB feeds score.py the judge's JSON
from a file, and every row it produces carries judge_stub:true so a stubbed number can
never be mistaken for a measured one.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)
SCORER = os.path.join(EVAL, "score.py")

# Shaped like the REAL key (eval/bench/answer-key.json): herrings carry no
# `red_herring` field at all — they are marked by a "RED HERRING:" prefix on the
# TITLE. A fixture that marked one via root_cause instead silently landed it in the
# denominator and turned 1-of-2 into 33.3%.
KEY = {"defects": [
    {"id": "D01", "title": "NPE in PromoCodeResolver", "root_cause": "null promo"},
    {"id": "D02", "title": "connection pool exhausted", "root_cause": "leak"},
    {"id": "D12", "title": "RED HERRING: noisy healthcheck",
     "description": "Looks like the cause of the 503 storm. It is not."},
]}


class Harness(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="score-test-")
        self.key = os.path.join(self.d, "key.json")
        with open(self.key, "w", encoding="utf-8") as fh:
            json.dump(KEY, fh)
        self.stub = os.path.join(self.d, "stub.json")
        with open(self.stub, "w", encoding="utf-8") as fh:
            fh.write('{"found_ids": ["D01"], "missed_ids": ["D02"], '
                     '"false_positives": 1, "notes": "found the NPE"}')

    def ledger(self, *rows):
        p = os.path.join(self.d, "runs.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p

    def run_scorer(self, ledger, *extra):
        env = dict(os.environ)
        env["SHERLOCK_SCORE_STUB"] = self.stub
        env.pop("JUDGE_API_KEY", None)   # a real call must fail loudly, not silently
        env["SHERLOCK_SCORES_OUT"] = os.path.join(self.d, "scores.jsonl")
        p = subprocess.run(
            [sys.executable, SCORER, "--key", self.key, "--ledger", ledger] + list(extra),
            capture_output=True, text=True, env=env, timeout=60)
        rows = []
        out = env["SHERLOCK_SCORES_OUT"]
        if os.path.exists(out):
            with open(out, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        return p, rows


class AllRowsScoresEveryRepetition(Harness):
    def test_default_keeps_only_the_latest_row_per_arm(self):
        """The pre-existing behaviour, pinned so a change to it is deliberate."""
        led = self.ledger(
            {"dataset": "bench649", "arm": "v7", "answer": "run one", "turns": 10},
            {"dataset": "bench649", "arm": "v7", "answer": "run two", "turns": 11},
            {"dataset": "bench649", "arm": "v7", "answer": "run three", "turns": 12})
        p, rows = self.run_scorer(led)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 1, "default must collapse to the latest row")
        self.assertEqual(rows[0]["turns"], 12)

    def test_all_rows_scores_each_repetition_separately(self):
        led = self.ledger(
            {"dataset": "bench649", "arm": "v7", "answer": "run one", "turns": 10},
            {"dataset": "bench649", "arm": "v7", "answer": "run two", "turns": 11},
            {"dataset": "bench649", "arm": "v7", "answer": "run three", "turns": 12})
        p, rows = self.run_scorer(led, "--all-rows")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 3, "every repetition must be scored")
        self.assertEqual([r["rep"] for r in rows], [1, 2, 3])
        self.assertEqual([r["turns"] for r in rows], [10, 11, 12])

    def test_rep_counts_per_arm_not_globally(self):
        led = self.ledger(
            {"dataset": "bench649", "arm": "v5", "answer": "a", "turns": 1},
            {"dataset": "bench649", "arm": "v7", "answer": "b", "turns": 2},
            {"dataset": "bench649", "arm": "v5", "answer": "c", "turns": 3})
        p, rows = self.run_scorer(led, "--all-rows")
        self.assertEqual(p.returncode, 0, p.stderr)
        got = [(r["arm"], r["rep"]) for r in rows]
        self.assertEqual(got, [("v5", 1), ("v7", 1), ("v5", 2)], got)


class EveryScoreTracesBackToItsSource(Harness):
    def test_each_score_names_its_ledger_line_and_judge(self):
        led = self.ledger(
            {"dataset": "bench649", "arm": "v7", "answer": "run one", "turns": 10},
            {"dataset": "bench649", "arm": "v7", "answer": "run two", "turns": 11})
        p, rows = self.run_scorer(led, "--all-rows")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([r["ledger_line"] for r in rows], [1, 2],
                         "a quoted number must point at the raw row that produced it")
        self.assertEqual([r["ledger"] for r in rows], ["runs.jsonl"] * 2)
        for r in rows:
            self.assertTrue(r["judge_model"],
                            "a score with no judge recorded cannot be compared to anything")

    def test_a_stubbed_score_is_marked_so_it_is_never_mistaken_for_measured(self):
        led = self.ledger({"dataset": "bench649", "arm": "v7", "answer": "x", "turns": 1})
        p, rows = self.run_scorer(led, "--all-rows")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIs(rows[0]["judge_stub"], True)


class RedHerringsStayOutOfTheDenominator(Harness):
    def test_recall_is_over_real_defects_only(self):
        """D12 is a planted red herring. Missing it is CORRECT behaviour, so it must
        never inflate the denominator — 1 of 2 real defects is 50%, not 33%."""
        led = self.ledger({"dataset": "bench649", "arm": "v7", "answer": "x", "turns": 1})
        p, rows = self.run_scorer(led, "--all-rows")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["recall_pct"], 50.0, rows[0])
        self.assertIn("denominator = 2", p.stdout)

    def test_the_explicit_red_herring_flag_also_works(self):
        """The real key marks herrings only by a title prefix, which is a magic string
        — one herring written without it silently joins the denominator and deflates
        every recall number. The code also supports an explicit boolean; pin it so
        that escape hatch stays available."""
        self.key = os.path.join(self.d, "key2.json")
        with open(self.key, "w", encoding="utf-8") as fh:
            json.dump({"defects": [
                {"id": "D01", "title": "NPE in PromoCodeResolver"},
                {"id": "D02", "title": "connection pool exhausted"},
                {"id": "D12", "title": "noisy healthcheck", "red_herring": True},
            ]}, fh)
        led = self.ledger({"dataset": "bench649", "arm": "v7", "answer": "x", "turns": 1})
        p, rows = self.run_scorer(led, "--all-rows")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("denominator = 2", p.stdout)
        self.assertEqual(rows[0]["recall_pct"], 50.0, rows[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
