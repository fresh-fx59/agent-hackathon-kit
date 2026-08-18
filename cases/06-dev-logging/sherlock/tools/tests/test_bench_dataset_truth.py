#!/usr/bin/env python3
"""Tests for eval/bench — a run must be attributable to the corpus it ran on.

Three defects, all silent, all confirmed present on main on 2026-08-18:

  run-bench.sh:65   PROMPT hard-coded to «Продакшн деградировал…», a production
                    outage RCA. Pointed at an intrusion corpus it asks the model
                    the wrong question and then scores the answer.
  run-bench.sh:171  `"dataset": "bench649"` written as a LITERAL on every row, so
                    a run against any other corpus is filed under the dev corpus.
  score-bench.py    select_row() filtered on stub/arm/trace and NOT on dataset,
                    so it could not tell those rows apart afterwards.

Together they do not fail — they produce a confident number attributed to the
wrong evidence, which is the one outcome a measurement project cannot survive.
These tests exist because nothing else would have caught that.

    python3 tools/tests/test_bench_dataset_truth.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
BENCH = os.path.join(SHERLOCK, "eval", "bench")
RUNNER = os.path.join(BENCH, "run-bench.sh")
PROMPTS = os.path.join(BENCH, "prompts")

_spec = importlib.util.spec_from_file_location(
    "score_bench", os.path.join(BENCH, "score-bench.py"))
SB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SB)


def row(**kw):
    base = {"arm": "v14", "dataset": "bench649", "trace_dir": "/runs/x", "stub": False}
    base.update(kw)
    return base


class DatasetIsRecorded(unittest.TestCase):
    def test_runner_no_longer_hardcodes_the_dataset(self):
        src = open(RUNNER, encoding="utf-8").read()
        self.assertNotIn('"dataset": "bench649"', src,
                         "the literal is what filed every run under the dev corpus")
        self.assertIn('"dataset": dataset', src)

    def test_runner_records_the_corpus_directory_too(self):
        """A dataset id is a label; the path is the fact behind it."""
        self.assertIn('"corpus_dir": corpus', open(RUNNER, encoding="utf-8").read())

    def test_runner_passes_dataset_into_the_ledger_writer(self):
        src = open(RUNNER, encoding="utf-8").read()
        self.assertIn('"$DATASET"', src)
        self.assertIn("dataset = sys.argv[1:11]", src)

    def test_runner_is_valid_shell(self):
        self.assertEqual(subprocess.call(["bash", "-n", RUNNER]), 0)


class PromptBelongsToTheCorpus(unittest.TestCase):
    def test_every_security_dataset_has_a_prompt(self):
        for ds in ("bluesky", "ait-russellmitchell", "fleet-negative", "camlds-s1"):
            p = os.path.join(PROMPTS, ds + ".txt")
            self.assertTrue(os.path.exists(p), "%s has no prompt" % ds)
            self.assertGreater(len(open(p, encoding="utf-8").read().strip()), 200)

    def test_the_negative_control_is_asked_the_identical_question(self):
        """A clean corpus given a gentler prompt proves nothing. The four security
        prompts are symlinks to one file so that 'identical' is a property of the
        filesystem rather than a promise someone has to keep."""
        texts = {}
        for ds in ("bluesky", "ait-russellmitchell", "fleet-negative", "camlds-s1"):
            texts[ds] = open(os.path.join(PROMPTS, ds + ".txt"), encoding="utf-8").read()
        self.assertEqual(len(set(texts.values())), 1,
                         "the compromised corpora and the negative control must be "
                         "asked the same question, verbatim")

    def test_the_prompt_does_not_presuppose_an_attack(self):
        """The negative control's correct answer is 'attacked, not compromised'.
        A prompt that says 'find the attack' has answered its own question."""
        t = open(os.path.join(PROMPTS, "security.txt"), encoding="utf-8").read()
        self.assertIn("чисто", t, "'clean' must be an available verdict")
        self.assertIn("атаковали, но не доказано", t)
        for leading in ("взлом", "злоумышленник прон", "атака произошла"):
            self.assertNotIn(leading, t.lower())

    def test_prompt_carries_the_corpus_placeholder(self):
        t = open(os.path.join(PROMPTS, "security.txt"), encoding="utf-8").read()
        self.assertIn("{CORPUS}", t)

    def test_an_unknown_dataset_refuses_to_run(self):
        """Silently falling back to the outage prompt is the original defect."""
        src = open(RUNNER, encoding="utf-8").read()
        self.assertIn("has no prompt", src)
        m = re.search(r'elif \[ "\$DATASET" = "bench649" \]', src)
        self.assertTrue(m, "the historical prompt must be scoped to bench649 alone")


class ScorerCannotCrossCorpora(unittest.TestCase):
    def test_dataset_filter_selects(self):
        rows = [row(dataset="bench649", trace_dir="/a"),
                row(dataset="bluesky", trace_dir="/b")]
        self.assertEqual(SB.select_row(rows, None, None, "bluesky")["trace_dir"], "/b")

    def test_mixed_ledger_without_a_dataset_raises(self):
        """The whole point. Before the fix this returned rows[-1] and scored it."""
        rows = [row(dataset="bench649"), row(dataset="bluesky")]
        with self.assertRaises(SystemExit) as cm:
            SB.select_row(rows, None, None, None)
        self.assertIn("different corpora", str(cm.exception))

    def test_single_corpus_ledger_still_works_without_the_flag(self):
        """Backwards compatible: the five published bench649 runs re-score."""
        rows = [row(dataset="bench649", trace_dir="/a"),
                row(dataset="bench649", trace_dir="/b")]
        self.assertEqual(SB.select_row(rows, None, None, None)["trace_dir"], "/b")

    def test_stub_rows_are_still_excluded(self):
        rows = [row(dataset="bluesky", trace_dir="/real"),
                row(dataset="bluesky", trace_dir="/stub", stub=True)]
        self.assertEqual(SB.select_row(rows, None, None, "bluesky")["trace_dir"], "/real")

    def test_no_match_raises_and_names_the_dataset(self):
        with self.assertRaises(SystemExit) as cm:
            SB.select_row([row(dataset="bench649")], None, None, "bluesky")
        self.assertIn("bluesky", str(cm.exception))

    def test_key_and_run_must_agree(self):
        with self.assertRaises(SystemExit) as cm:
            SB.check_key_matches_dataset({"dataset": "bluesky"}, row(dataset="bench649"))
        self.assertIn("refusing", str(cm.exception))

    def test_key_without_a_dataset_is_permitted(self):
        """The shipped bench649 key predates the field; it must keep working."""
        SB.check_key_matches_dataset({"defects": []}, row(dataset="bench649"))

    def test_a_dataset_bearing_key_refuses_a_legacy_row(self):
        with self.assertRaises(SystemExit) as cm:
            SB.check_key_matches_dataset({"dataset": "bluesky"}, {"arm": "v14"})
        self.assertIn("predates", str(cm.exception))


class VerdictIsScoredWithoutAJudge(unittest.TestCase):
    """The only score here that is not a model grading a model."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "score_verdict", os.path.join(BENCH, "score-verdict.py"))
        self.SV = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.SV)

    def v(self, text):
        return self.SV.extract(text)[0]

    def test_three_verdicts_round_trip(self):
        self.assertEqual(self.v("## ВЕРДИКТ\nХост скомпрометирована."), "compromised")
        self.assertEqual(self.v("## ВЕРДИКТ\nАтаковали, но не доказано."),
                         "attacked-not-proven")
        self.assertEqual(self.v("## ВЕРДИКТ\nЧисто."), "clean")

    def test_english_wording_also_works(self):
        self.assertEqual(self.v("## VERDICT\nattacked-but-undecidable"),
                         "attacked-not-proven")

    def test_a_quoted_log_line_cannot_set_the_verdict(self):
        """Attacker tooling in the corpus literally contains the word. Reading the
        whole document instead of the verdict section would let the evidence grade
        the report."""
        report = ('Найден c2.ps1 со строкой "host compromised" (toolkit:12).\n'
                  '## ВЕРДИКТ\nЧисто.')
        self.assertEqual(self.v(report), "clean")

    def test_missing_section_is_absent_not_wrong(self):
        self.assertEqual(self.v("Отчёт без раздела вердикта."), "absent")
        self.assertEqual(self.v(""), "absent")

    def test_absent_is_reported_as_a_delivery_defect(self):
        _v, how = self.SV.extract("Отчёт без раздела вердикта.")
        self.assertIn("no verdict", how.lower())

    def test_ambiguity_is_surfaced_not_hidden(self):
        _v, how = self.SV.extract(
            "## ВЕРДИКТ\nСкомпрометирована. Впрочем, возможно и чисто.")
        self.assertIn("AMBIGUOUS", how)


class TheNegativeControlKey(unittest.TestCase):
    """The corpus whose whole job is to catch an arm crying wolf."""

    def setUp(self):
        self.k = json.load(open(os.path.join(BENCH, "answer-key-fleet-negative.json"),
                                encoding="utf-8"))

    def test_it_has_decoys_AND_real_findings_now(self):
        """This used to assert `real == []`, and that was right until 2026-08-18:
        the key was decoys only, so `score-report.py` printed `anchored 0/0` and
        could score what a report should REFUSE and nothing it should OBSERVE.
        A negative control is not «a corpus with no findings» — it is a corpus with
        no COMPROMISE. The ten real findings are true observations about a fleet
        that was attacked and held, every one of them `provenance: counted`, and
        `test_no_real_finding_TITLES_a_compromise` in
        test_answer_key_fleet_negative.py is what keeps that distinction honest."""
        real = [d for d in self.k["defects"]
                if not d["title"].upper().startswith("RED HERRING")]
        decoys = [d for d in self.k["defects"]
                  if d["title"].upper().startswith("RED HERRING")]
        self.assertEqual(len(decoys), 8)
        self.assertEqual(len(real), 10)
        for d in real:
            self.assertEqual(d["provenance"], "counted", d["id"])
        self.assertEqual(self.k["totals"]["real_defects"], len(real))
        self.assertEqual(self.k["totals"]["red_herrings"], len(decoys))

    def test_the_truth_is_the_middle_verdict(self):
        """Not 'clean': the attacks are real and visible. Not 'compromised': none
        of them worked."""
        self.assertEqual(self.k["verdict"], "attacked-not-proven")

    def test_the_responder_artifact_is_encoded(self):
        """The only successful login in the corpus is the evidence collection
        itself. A report that cannot separate the responder's footprint from the
        intruder's is dangerous, and this is the cheapest test of that."""
        joined = json.dumps(self.k, ensure_ascii=False)
        self.assertIn("100.122.174.119", joined)
        self.assertIn("EVIDENCE COLLECTION ITSELF", joined.upper())

    def test_every_defect_names_where_to_look(self):
        """A decoy points with `anchor`, a counted finding with `proof_locations`.
        Both are read by `score-report.py`'s `proof_spans`; what must never happen
        is an entry that names neither, because that one silently leaves the
        denominator and costs nothing to miss."""
        for d in self.k["defects"]:
            self.assertTrue(d.get("anchor") or d.get("proof_locations"),
                            "%s says nowhere to look" % d["id"])
            self.assertTrue(d.get("root_cause"), "%s has no rationale" % d["id"])

    def test_zero_denominator_is_survivable(self):
        """score-bench divides found/total. A decoy-only key makes total 0, and the
        crash would take out the one corpus that measures false positives."""
        src = open(os.path.join(BENCH, "score-bench.py"), encoding="utf-8").read()
        self.assertIn('if res["total"] else', src)


class KeysDeclareTheirCorpus(unittest.TestCase):
    def test_each_key_names_its_dataset(self):
        for f, ds in (("answer-key.json", "bench649"),
                      ("answer-key-bluesky.json", "bluesky"),
                      ("answer-key-fleet-negative.json", "fleet-negative"),
                      ("answer-key-ait-russellmitchell.json",
                       "ait-russellmitchell")):
            k = json.load(open(os.path.join(BENCH, f), encoding="utf-8"))
            self.assertEqual(k.get("dataset"), ds, f)

    def test_bench649_declares_no_verdict(self):
        """It is an outage corpus. A compromise verdict there is a category error."""
        k = json.load(open(os.path.join(BENCH, "answer-key.json"), encoding="utf-8"))
        self.assertIsNone(k.get("verdict"))

    def test_bluesky_is_compromised_but_not_because_of_impact(self):
        k = json.load(open(os.path.join(BENCH, "answer-key-bluesky.json"),
                           encoding="utf-8"))
        self.assertEqual(k.get("verdict"), "compromised")
        self.assertIn("never encrypted", k.get("verdict_rationale", ""))

    def test_every_key_with_a_dataset_has_a_prompt_file(self):
        """run-bench.sh refuses a dataset with no prompt, and the four security
        datasets are SYMLINKS to security.txt so the negative control is asked the
        identical question. A key whose prompt is missing cannot be run blind."""
        for f in os.listdir(BENCH):
            if not f.startswith("answer-key") or not f.endswith(".json"):
                continue
            ds = json.load(open(os.path.join(BENCH, f), encoding="utf-8")).get("dataset")
            if not ds or ds == "bench649":      # bench649 keeps its prompt inline
                continue
            self.assertTrue(os.path.isfile(os.path.join(PROMPTS, ds + ".txt")),
                            "%s declares dataset %r with no prompts/%s.txt"
                            % (f, ds, ds))


if __name__ == "__main__":
    unittest.main(verbosity=2)
