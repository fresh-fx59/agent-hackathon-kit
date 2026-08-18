#!/usr/bin/env python3
"""Tests for answer-key-ait-russellmitchell.json — the first MECHANICALLY DERIVED key.

The other three keys in eval/bench were written by hand: somebody read the corpus
and decided what the defects were. A reader is entitled to ask whether such a key
was bent to fit the arm being scored. This one cannot have been — `build-answer-key-ait.py`
groups AIT-LDS v2.1's own per-line labels and nothing else — but "cannot have been"
is a claim, and these tests are what makes it checkable without the 6.6 GB corpus.

The property that matters most is the DENOMINATOR. AIT's needle-to-haystack ratio
spans three orders of magnitude (dnsmasq.log 54,035 labelled of 275,900 versus
auth.log 8 of 272). A key that pooled those would let a report which found only
the loud DNS exfiltration score ~86 %. Because a defect here is one
(labelled file × attack phase), that same report scores 1 of 11 — and
`test_finding_only_the_loud_thing_scores_near_zero` runs exactly that arithmetic
through `score-report.py`'s own `proof_spans`/`anchor_hits`, with no corpus and no
judge.

    python3 tools/tests/test_answer_key_ait.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
BENCH = os.path.join(SHERLOCK, "eval", "bench")
KEY = os.path.join(BENCH, "answer-key-ait-russellmitchell.json")
BUILDER = os.path.join(BENCH, "build-answer-key-ait.py")

# The extracted AIT-LDS tree is a 6.6 GB local artifact, not a repo fixture. Every
# test that needs it skips when it is absent, and every other test runs anywhere.
AIT_ROOT = os.environ.get(
    "AIT_ROOT", os.path.expanduser("~/hack/sherlock-corpora/ait-lds-v2/extracted"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SR = _load("score_report", os.path.join(BENCH, "score-report.py"))
SB = _load("score_bench", os.path.join(BENCH, "score-bench.py"))
BUILD = _load("build_ait", BUILDER)


class TheKeyExistsAndDeclaresItself(unittest.TestCase):
    def setUp(self):
        self.k = json.load(open(KEY, encoding="utf-8"))

    def test_it_names_its_dataset(self):
        self.assertEqual(self.k["dataset"], "ait-russellmitchell")

    def test_the_verdict_is_compromised(self):
        """Access was obtained and used: a web shell upload, 32 web shell commands,
        then root on the same host in two independent logs."""
        self.assertEqual(self.k["verdict"], "compromised")
        self.assertIn("web shell", self.k["verdict_rationale"].lower())

    def test_it_says_out_loud_that_it_was_generated(self):
        d = self.k["derivation"]
        self.assertEqual(d["tool"], "build-answer-key-ait.py")
        self.assertEqual(d["labelled_lines"], 61862)
        self.assertEqual(d["label_files"], 8)
        self.assertEqual(d["distinct_label_names"], 22)


class ItHasNoDecoysAndSaysSo(unittest.TestCase):
    """The one column this key cannot fill, filled with a statement instead."""

    def setUp(self):
        self.k = json.load(open(KEY, encoding="utf-8"))

    def test_zero_red_herrings(self):
        self.assertEqual(self.k["totals"]["red_herrings"], 0)

    def test_no_defect_is_a_herring_by_score_benchs_own_test(self):
        key = SB.load_key(self.k)
        self.assertEqual([c for c in key if SB.is_herring(key[c])], [])

    def test_the_key_warns_that_decoys_are_not_measurable_here(self):
        """0/0 must not be read as a clean false-positive bill. An authored decoy
        inside a derived key is the one thing this file must not contain."""
        self.assertIn("NOT MEASURABLE", self.k["notes"])
        self.assertIn("answer-key-fleet-negative.json", self.k["notes"])


class TheDenominatorIsDefectsNotLines(unittest.TestCase):
    def setUp(self):
        self.k = json.load(open(KEY, encoding="utf-8"))
        self.defects = self.k["defects"]

    def test_eleven_defects_over_the_eight_labelled_files(self):
        self.assertEqual(len(self.defects), 11)
        self.assertEqual(len({d["file"] for d in self.defects}), 8)

    def test_the_loudest_and_the_quietest_are_separate_defects_of_equal_weight(self):
        """53,054 labelled lines and 2 labelled lines each count once."""
        sizes = sorted(d["labelled_lines"] for d in self.defects)
        self.assertEqual(sizes[0], 2)
        self.assertEqual(sizes[-1], 53054)

    def test_the_quiet_privilege_escalation_is_its_own_defect(self):
        """8 lines of 272 in auth.log — the intrusion, not the noise. If this ever
        merges into a louder defect the key stops measuring what it exists for."""
        quiet = [d for d in self.defects
                 if d["file"] == "intranet_server/logs/auth.log"]
        self.assertEqual(len(quiet), 1)
        self.assertEqual(quiet[0]["labelled_lines"], 8)
        self.assertEqual(quiet[0]["proof_locations"],
                         [{"file": "intranet_server/logs/auth.log",
                           "line_start": 145, "line_end": 152}])

    def test_finding_only_the_loud_thing_scores_near_zero(self):
        """The whole argument for this key shape, run as arithmetic.

        A report that cites three lines of dnsmasq's DNS exfiltration and nothing
        else. Under a lines-pooled key it would score 53,054/61,862 = 86 %.
        """
        spans = [("inet-firewall/logs/dnsmasq.log", n, n)
                 for n in (1, 1000, 150000)]
        key = SB.load_key(self.k)
        hit = sum(1 for cid in key
                  if SR.anchor_hits(spans, SR.proof_spans(key[cid])) > 0)
        self.assertEqual(hit, 1, "citing only the loud exfiltration must anchor "
                                 "exactly one of the eleven defects")

    def test_citing_every_defects_first_proof_line_anchors_all_eleven(self):
        key = SB.load_key(self.k)
        spans = []
        for cid in key:
            pl = key[cid]["proof_locations"][0]
            spans.append((pl["file"], pl["line_start"], pl["line_start"]))
        hit = sum(1 for cid in key
                  if SR.anchor_hits(spans, SR.proof_spans(key[cid])) > 0)
        self.assertEqual(hit, 11)


class EveryProofLocationIsUsable(unittest.TestCase):
    def setUp(self):
        self.k = json.load(open(KEY, encoding="utf-8"))

    def test_every_defect_is_anchorable(self):
        """A defect with no proof locations silently leaves the denominator —
        score-report.py prints that as a warning, and a key should not need one."""
        for d in self.k["defects"]:
            self.assertTrue(d["proof_locations"], "%s has no proof" % d["id"])

    def test_spans_are_well_formed_and_inside_the_file(self):
        for d in self.k["defects"]:
            for pl in d["proof_locations"]:
                self.assertEqual(pl["file"], d["file"])
                self.assertGreaterEqual(pl["line_start"], 1)
                self.assertLessEqual(pl["line_start"], pl["line_end"])
                self.assertLessEqual(pl["line_end"], d["file_total_lines"])

    def test_runs_are_maximal_and_disjoint(self):
        """Contiguous runs, not wide ranges: every line inside a proof span has to
        really be labelled, or the key credits the gaps between the needles."""
        for d in self.k["defects"]:
            prev = None
            for pl in d["proof_locations"]:
                if prev is not None:
                    self.assertGreater(pl["line_start"], prev + 1,
                                       "%s: runs ending %d and starting %d are "
                                       "adjacent or overlap — they should have "
                                       "been one run"
                                       % (d["id"], prev, pl["line_start"]))
                prev = pl["line_end"]

    def test_proof_lines_sum_to_the_labelled_count(self):
        for d in self.k["defects"]:
            n = sum(pl["line_end"] - pl["line_start"] + 1
                    for pl in d["proof_locations"])
            self.assertEqual(n, d["labelled_lines"], d["id"])


class MergesAreRecordedNotHidden(unittest.TestCase):
    def setUp(self):
        self.k = json.load(open(KEY, encoding="utf-8"))

    def test_the_three_merges_are_in_the_key(self):
        m = {(x["file"].rsplit("/", 1)[-1], x["absorbed"], x["into"])
             for x in self.k["derivation"]["merges"]}
        self.assertIn(("dnsmasq.log", "escalate", "webshell"), m)
        self.assertIn(("2022-01-24-system.cpu.log", "cracking", "escalate"), m)

    def test_a_merged_defect_keeps_both_phase_names_in_its_title(self):
        """`crack_passwords` and `escalate` label the identical 49 lines of the
        monitoring CPU log. Titling that 'privilege escalation' alone would
        describe the evidence wrongly inside the key itself."""
        cpu = [d for d in self.k["defects"] if d["file"].endswith("system.cpu.log")]
        self.assertEqual(len(cpu), 1)
        self.assertIn("cracking", cpu[0]["phases"])
        self.assertIn("password", cpu[0]["title"].lower())


class ContextLabelsAssignNoPhase(unittest.TestCase):
    """`attacker`, `attacker_http` and `foothold` co-occur with every stage. If any
    of them ever entered the phase map, all 61,862 lines would land in one bucket
    and the key would stop discriminating — the exact failure it was built to avoid."""

    def test_they_are_not_in_the_phase_map(self):
        for l in ("attacker", "attacker_http", "foothold"):
            self.assertNotIn(l, BUILD.PHASE)
            self.assertIn(l, BUILD.CONTEXT)

    def test_the_phase_map_covers_every_other_label_name(self):
        k = json.load(open(KEY, encoding="utf-8"))
        seen = set()
        for d in k["defects"]:
            seen |= set(d["labels"])
        self.assertEqual(sorted(seen - BUILD.CONTEXT - set(BUILD.PHASE)), [],
                         "a label reached the key with no phase")


class TheBuilderIsReproducible(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(os.path.join(AIT_ROOT, "labels")),
                         "AIT-LDS extracted tree not present (set AIT_ROOT)")
    def test_regenerating_reproduces_the_committed_key(self):
        """A derived key that cannot be re-derived is an authored key with a story."""
        committed = json.load(open(KEY, encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "k.json")
            subprocess.run([sys.executable, BUILDER, "--root", AIT_ROOT,
                            "--corpus", committed["corpus_root"],
                            "--dataset", committed["dataset"], "--out", out],
                           check=True, capture_output=True)
            again = json.load(open(out, encoding="utf-8"))
        self.assertEqual(committed, again)


if __name__ == "__main__":
    unittest.main(verbosity=2)
