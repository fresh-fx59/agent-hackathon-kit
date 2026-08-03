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

    def test_a_decoy_is_asked_the_OPPOSITE_question(self):
        """The bug this test exists for cost a published headline.

        The defect prompt asks "did the report identify THIS defect?". Asked of a
        decoy titled «RED HERRING: …», `false` means "did not call it a red
        herring" — which is exactly what a report that presents the decoy as a
        ROOT CAUSE returns. Scored as a defect, that read as "refused", and the
        649 MB rep 1 — whose findings list contains «Н-7 · SYN flooding» — was
        reported as 0 false positives.

        So a decoy must be asked whether the report presented it as REAL.
        """
        seen = {}

        def call(prompt):
            seen[("herring" if "PLANTED\nDECOY" in prompt or "DECOY" in prompt
                  else "defect")] = prompt
            return '{"found": false, "why": "x"}'
        SB.score(KEY, "report", call)
        self.assertIn("herring", seen, "a decoy went through the defect prompt")
        self.assertIn("present this decoy as a REAL", seen["herring"])
        self.assertNotIn("Did the report identify THIS defect?", seen["herring"])

    def test_presenting_the_decoy_as_a_cause_is_the_false_positive(self):
        def call(prompt):
            # the model reported everything it saw, decoys included
            return '{"found": true, "why": "listed among the findings"}'
        res = SB.score(KEY, "report", call)
        self.assertEqual(res["false_positives"], 2)

    def test_setting_the_decoy_aside_is_clean(self):
        def call(prompt):
            return '{"found": false, "why": "explicitly set aside as noise"}'
        res = SB.score(KEY, "report", call)
        self.assertEqual(res["false_positives"], 0)

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

    def run_it(self, answer="a report", found=True, artifact=None):
        d = tempfile.mkdtemp(prefix="score-bench-test-")
        ledger = os.path.join(d, "runs-bench.jsonl")
        row = {"arm": "v11", "model": "[SP]deepseek-v4-flash",
               "dataset": "bench649", "trace_dir": "/runs/x",
               "turns": 48, "duration_s": 1850,
               "input_tokens": 6995082, "answer": answer}
        if artifact is not None:
            row["artifact"] = artifact
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
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
        """A run with NO report at all — neither channel. Scoring it 0/11 would
        put a delivery failure on the recall axis, which is the confusion the
        slice ledger spent two days separating."""
        p, rows = self.run_it(answer="   ")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(rows, [])
        self.assertIn("nothing to score", p.stderr)

    def test_the_scored_row_names_the_channel_the_report_arrived_on(self):
        """Recall and delivery stay separately visible. A row that scores 8 of 11
        off a 101-char final message is a real finding AND a real defect."""
        _p, rows = self.run_it(answer="Отчёт готов.", artifact="x" * 5000)
        self.assertEqual(rows[0]["delivered_in"], "file")
        self.assertEqual(rows[0]["answer_chars"], len("Отчёт готов."))
        self.assertEqual(rows[0]["artifact_chars"], 5000)

    def test_a_run_that_only_wrote_a_report_file_is_scored_not_refused(self):
        p, rows = self.run_it(answer="", artifact="# Отчёт\napps/api.log:1 x")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 1, "a paid-for run was thrown away again")
        self.assertEqual(rows[0]["delivered_in"], "file")


class ItScoresTheDeliverableNotOnlyTheFinalMessage(unittest.TestCase):
    """The 18.76 M-token row this exists for.

    `20260802T221034Z-v11` answered «Отчёт готов…» in 101 chars beside a complete
    19,991-char `work/report.md` that had just passed `citecheck` 45/45. Judging
    the final message alone scores a finished investigation 0 of 11 — a delivery
    failure recorded on the recall axis, which is the exact confusion this rig
    was built to separate.
    """

    def row(self, **kw):
        r = {"arm": "v11", "model": "[SP]deepseek-v4-flash", "dataset": "bench649",
             "trace_dir": "/runs/x", "turns": 90, "answer": ""}
        r.update(kw)
        return r

    def test_the_report_file_reaches_the_judge(self):
        got = []
        r = self.row(answer="Отчёт готов.", artifact="PAYLOAD-MARKER api.log:1")
        SB.score({"D01": KEY["D01"]}, SB.deliverable.of_row(r),
                 lambda p: got.append(p) or '{"found": true, "why": "x"}')
        self.assertIn("PAYLOAD-MARKER", got[0])
        self.assertIn("Отчёт готов.", got[0], "the message is evidence too")

    def test_a_message_only_row_reaches_the_judge_byte_identical(self):
        """Twelve published rows are message-only. If composition perturbed them,
        the 0-of-11 baseline would stop being comparable to its own number."""
        self.assertEqual(SB.deliverable.of_row(self.row(answer="just this")),
                         "just this")


class TheRowUnderTestIsChosenExplicitly(unittest.TestCase):
    """`rows[-1]` can reach exactly one run, and four stub rows sit in the real
    bench ledger (`input_tokens: 11`, from a runner smoke test) where it would
    happily score one of them as a measurement."""

    ROWS = [{"arm": "v11", "trace_dir": "/runs/WANTED", "answer": "a"},
            {"arm": "none", "trace_dir": "/runs/base", "answer": "b"},
            {"arm": "v11", "trace_dir": "/runs/stub", "answer": "c", "stub": True},
            {"arm": "v11", "trace_dir": "/runs/later", "answer": "d"}]

    def test_the_default_is_still_the_last_real_row(self):
        self.assertEqual(SB.select_row(self.ROWS, None, None)["trace_dir"],
                         "/runs/later")

    def test_a_stub_row_is_never_selected(self):
        rows = self.ROWS[:3]
        self.assertEqual(SB.select_row(rows, "v11", None)["trace_dir"],
                         "/runs/WANTED", "a stub row was scored as a measurement")

    def test_a_named_trajectory_is_selected_by_substring(self):
        self.assertEqual(SB.select_row(self.ROWS, None, "WANTED")["trace_dir"],
                         "/runs/WANTED")

    def test_an_arm_and_a_trajectory_compose(self):
        self.assertEqual(SB.select_row(self.ROWS, "none", None)["trace_dir"],
                         "/runs/base")

    def test_a_trajectory_that_matches_nothing_raises_instead_of_scoring_another(self):
        """Silently falling back to the last row is how a re-score gets published
        against a run nobody asked for."""
        with self.assertRaises(SystemExit):
            SB.select_row(self.ROWS, None, "no-such-run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
