#!/usr/bin/env python3
"""Tests for eval/bench/score-bench.py — the thing that produces the quotable number.

`run-bench.sh` has recorded answers since the project started and never scored one, so
every 649 MB figure quoted so far was read off a report by eye. This file exists so the
headline number is derived the same way a slice row is: same judge, same prompt, one
call per answer-key entry.

The property that carries the most weight is the red-herring axis. D12 and D13 are
planted to be REFUTED. If a `found: true` on a herring counted as a find — or if the
denominator were 13 — an arm that reports everything it sees would outscore one that
discriminates, which is the opposite of what the corpus is built to measure.

No network: the judge is stubbed through the same SHERLOCK_JUDGE_STUB path the slice
scorer uses, and every stubbed score is stamped judge_stub:true.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(MEASURE)
SCORER = os.path.join(SHERLOCK, "eval", "bench", "score-bench.py")

_spec = importlib.util.spec_from_file_location("score_bench", SCORER)
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)

KEY = {
    "D01": {"title": "checkout-api NPE", "root_cause": "null normalized()"},
    "D02": {"title": "payments-worker panic", "root_cause": "index out of range"},
    "D12": {"title": "RED HERRING: SYN flood + nf_conntrack table full",
            "root_cause": "unrelated to the incident"},
    "D13": {"title": "RED HERRING: cache-eviction WARN spam",
            "root_cause": "routine noise"},
}


class TheHerringsAreScoredOnTheOppositeAxis(unittest.TestCase):

    def test_a_herring_is_recognised_from_the_key_not_a_hardcoded_id(self):
        """Hardcoding D12/D13 would silently mis-score the day the key grows."""
        self.assertTrue(SB.is_herring({"title": "RED HERRING: whatever"}))
        self.assertTrue(SB.is_herring({"title": "  red herring: lowercase"}))
        self.assertFalse(SB.is_herring({"title": "checkout-api NPE"}))
        self.assertFalse(SB.is_herring({"title": "a report mentioning a red herring"}),
                         "the mark is a prefix on the KEY's title, not a substring "
                         "anywhere in it")

    def test_the_denominator_counts_real_defects_only(self):
        res = SB.score(KEY, "report", lambda _p: '{"found": true, "why": "x"}')
        self.assertEqual(res["total"], 2, "13 as a denominator rewards over-reporting")
        self.assertEqual(res["herrings"], 2)

    def test_finding_a_herring_is_a_false_positive_never_a_find(self):
        res = SB.score(KEY, "report", lambda _p: '{"found": true, "why": "x"}')
        self.assertEqual(res["found"], 2, "only the two real defects may count")
        self.assertEqual(res["false_positives"], 2)

    def test_refusing_a_herring_costs_nothing(self):
        def call(prompt):
            hit = "RED HERRING" not in prompt
            return json.dumps({"found": hit, "why": "x"})
        res = SB.score(KEY, "report", call)
        self.assertEqual((res["found"], res["total"]), (2, 2))
        self.assertEqual(res["false_positives"], 0)


class ItReadsTheREALAnswerKeyShape(unittest.TestCase):
    """Asserted against the shipped file, not a fixture of my own design.

    The first version of this scorer assumed `{id: entry}` and the real key is
    `{"defects": [ {"id": ...}, … ]}`, so it would have judged the top-level keys
    ("scenario", "seed", "files") and produced a confident, meaningless number. A
    fixture I write myself cannot catch that — only the real file can.
    """

    KEY = os.path.join(SHERLOCK, "eval", "bench", "answer-key.json")

    def test_the_shipped_key_yields_eleven_defects_and_two_herrings(self):
        entries = SB.load_key(json.load(open(self.KEY, encoding="utf-8")))
        self.assertEqual(len(entries), 13)
        herrings = [e for e in entries.values() if SB.is_herring(e)]
        self.assertEqual(len(herrings), 2, "D12 and D13 are the planted herrings")
        self.assertEqual(len(entries) - len(herrings), 11)

    def test_every_entry_carries_what_the_judge_prompt_needs(self):
        entries = SB.load_key(json.load(open(self.KEY, encoding="utf-8")))
        for cid, e in entries.items():
            self.assertTrue(e.get("title"), cid)
            self.assertTrue(e.get("root_cause"), cid)
            self.assertEqual(e.get("case_id", cid), cid)

    def test_a_dict_keyed_by_id_still_works(self):
        self.assertEqual(sorted(SB.load_key(KEY)), sorted(KEY))


class EveryEntryIsJudgedExactlyOnce(unittest.TestCase):

    def test_one_call_and_one_row_per_answer_key_entry(self):
        seen = []

        def call(prompt):
            seen.append(prompt)
            return '{"found": false, "why": "no"}'
        res = SB.score(KEY, "report", call)
        self.assertEqual(len(seen), len(KEY))
        self.assertEqual(len(res["rows"]), len(KEY))
        self.assertEqual(sorted(r["defect"] for r in res["rows"]), sorted(KEY))

    def test_the_report_reaches_the_judge_as_data(self):
        """The slice prompt wraps the report in a per-call random tag so a planted
        log line cannot forge a closing delimiter. Reusing build_prompt is what
        keeps that property here; reimplementing the prompt would lose it."""
        seen = []

        def call(prompt):
            seen.append(prompt)
            return '{"found": false, "why": "no"}'
        SB.score({"D01": KEY["D01"]}, "PAYLOAD-MARKER", call)
        self.assertIn("PAYLOAD-MARKER", seen[0])
        self.assertIn("<report-", seen[0], "the random-tag wrapper must survive")

    def test_a_judge_transport_failure_raises_and_is_not_a_miss(self):
        def call(_prompt):
            raise RuntimeError("broker down")
        with self.assertRaises(RuntimeError):
            SB.score(KEY, "report", call)


class TheScoredRowIsTraceable(unittest.TestCase):
    """A number with no provenance is the artifact this project keeps getting burned
    by: it must name the arm, the model under test, the judge, and the trajectory."""

    def run_it(self, answer="a report", found=True):
        d = tempfile.mkdtemp(prefix="score-bench-test-")
        ledger = os.path.join(d, "runs-bench.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"arm": "v11", "model": "[SP]deepseek-v4-flash",
                                 "dataset": "bench649", "trace_dir": "/runs/x",
                                 "turns": 48, "duration_s": 1850,
                                 "input_tokens": 6995082, "answer": answer}) + "\n")
        key = os.path.join(d, "key.json")
        json.dump(KEY, open(key, "w", encoding="utf-8"))
        stub = os.path.join(d, "judge.json")
        open(stub, "w", encoding="utf-8").write(
            json.dumps({"found": found, "why": "stubbed"}))
        out = os.path.join(d, "scores.jsonl")
        env = dict(os.environ, SHERLOCK_JUDGE_STUB=stub)
        env.pop("JUDGE_API_KEY", None)
        p = subprocess.run([sys.executable, SCORER, "--ledger", ledger, "--key", key,
                            "--out", out, "--arm", "v11"],
                           capture_output=True, text=True, env=env, timeout=60)
        rows = ([json.loads(l) for l in open(out, encoding="utf-8") if l.strip()]
                if os.path.exists(out) else [])
        return p, rows

    def test_the_row_names_arm_model_judge_and_trajectory(self):
        p, rows = self.run_it()
        self.assertEqual(p.returncode, 0, p.stderr)
        r = rows[0]
        self.assertEqual(r["arm"], "v11")
        self.assertEqual(r["model"], "[SP]deepseek-v4-flash")
        self.assertEqual(r["judge_model"], SB.score_case.JUDGE_MODEL)
        self.assertEqual(r["trace_dir"], "/runs/x")
        self.assertEqual(r["input_tokens"], 6995082, "cost rides with the score")

    def test_a_stubbed_score_can_never_be_mistaken_for_a_measurement(self):
        p, rows = self.run_it()
        self.assertIs(rows[0]["judge_stub"], True)
        self.assertIn("NOT a measurement", p.stdout)

    def test_the_per_defect_breakdown_is_kept(self):
        _p, rows = self.run_it()
        self.assertEqual(len(rows[0]["per_defect"]), len(KEY))
        self.assertEqual(rows[0]["found"], 2)
        self.assertEqual(rows[0]["false_positives"], 2)

    def test_an_empty_answer_is_refused_instead_of_scored_as_zero(self):
        """A collapsed run has no report. Scoring it 0/11 would put a delivery
        failure on the recall axis, which is the confusion the slice ledger spent
        two days separating."""
        p, rows = self.run_it(answer="   ")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(rows, [])
        self.assertIn("no answer", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
