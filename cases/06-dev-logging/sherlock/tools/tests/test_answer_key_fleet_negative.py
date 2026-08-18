#!/usr/bin/env python3
"""Tests for answer-key-fleet-negative.json — the negative control, now with a
REAL-FINDINGS half.

Until 2026-08-18 this key held eight decoys and zero real defects, so
`score-report.py` printed `anchored 0/0` on it: it could score what a report should
refuse and not one thing a report should observe. The AIT key is the mirror image —
eleven real defects, no decoys. Neither corpus could measure both halves.

This corpus ships NO ground truth, so unlike `answer-key-ait-russellmitchell.json`
the ten new findings cannot point at a label file and say nobody authored them. What
replaces that defence is checkable here, without the 225 MB corpus:

  * every real finding is `provenance: counted` and carries the shell one-liner that
    produced its number — and the BUILDER runs it, so a wrong number cannot be
    committed;
  * no real finding shares a proof line, or a whole-file claim, with a decoy, which
    is what stops one citation scoring as a finding and a false positive at once;
  * no proof location lives in a file `citecheck.looks_binary` rejects, because v16
    refuses those citations and an unanchorable proof is a free point nobody can win;
  * the verdict is still `attacked-not-proven` and no real finding claims a
    compromise.

`test_regenerating_reproduces_the_committed_key` needs the corpus and skips without
it; everything else runs anywhere.

    python3 tools/tests/test_answer_key_fleet_negative.py
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
KEY = os.path.join(BENCH, "answer-key-fleet-negative.json")
BUILDER = os.path.join(BENCH, "build-answer-key-fleet-negative.py")

# The corpus is 225 MB of production log slices living in the operator's vault; it
# is gitignored and only its MANIFEST/SHA256SUMS are committed. Tests that need it
# skip when it is absent.
CORPUS = os.environ.get(
    "FLEET_NEGATIVE_CORPUS",
    os.path.expanduser("~/Documents/projects/personal-os/projects/active/"
                       "attachments/sherlock-cyber-fleet/corpus"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SR = _load("score_report", os.path.join(BENCH, "score-report.py"))
SB = _load("score_bench", os.path.join(BENCH, "score-bench.py"))
CC = _load("citecheck_v16", os.path.join(SHERLOCK, "skills", "v16", "tools",
                                         "citecheck.py"))
K = json.load(open(KEY, encoding="utf-8"))
REAL = [d for d in K["defects"] if d["provenance"] == "counted"]
DECOY = [d for d in K["defects"] if d["provenance"] == "authored"]


class TheNegativeControlIsStillNegative(unittest.TestCase):
    def test_the_verdict_did_not_move(self):
        self.assertEqual(K["verdict"], "attacked-not-proven")

    def test_the_decoys_are_all_still_there(self):
        self.assertEqual(sorted(d["id"] for d in DECOY),
                         ["D0%d" % i for i in range(1, 9)])

    def test_every_decoy_is_a_herring_to_the_scorer(self):
        """`is_herring` reads the TITLE. A decoy whose title lost its marker would
        silently move into the findings denominator."""
        for d in DECOY:
            self.assertTrue(SB.is_herring(d), d["id"])

    def test_no_real_finding_is_read_as_a_decoy(self):
        for d in REAL:
            self.assertFalse(SB.is_herring(d), d["id"])

    def test_the_evidence_collector_is_not_a_finding(self):
        """D03 is the root shell that produced this corpus. Promoting it — or the
        Tailscale login that opened it — would turn the negative control into an
        intrusion corpus."""
        blob = json.dumps(REAL, ensure_ascii=False)
        self.assertNotIn("mon/auth/auth.log\"", blob)
        for d in REAL:
            for pl in d["proof_locations"]:
                self.assertNotEqual(pl["file"], "mon/auth/auth.log", d["id"])

    def test_no_real_finding_TITLES_a_compromise(self):
        """The title is the assertion; `root_cause` is allowed to say «this is a
        finding and not a breach», and a keyword scan cannot tell an assertion from
        its negation. So the scan runs on the titles, where there is nothing to
        negate."""
        bad = ("compromised", "breach", "backdoor", "exfiltrat", "attacker gained",
               "post-exploitation", "intrusion")
        for d in REAL:
            t = d["title"].lower()
            for w in bad:
                self.assertNotIn(w, t, "%s titles %r" % (d["id"], w))

    def test_the_key_says_out_loud_that_ten_of_ten_is_not_compromised(self):
        """The one sentence a future reader most needs: anchoring every real finding
        here and still answering `compromised` is WRONG, and the key must say so
        rather than leave it to be inferred from the verdict field."""
        self.assertIn("still answers", K["notes"])
        self.assertIn("'compromised' is wrong", K["notes"])


class TheRealFindingsAreCounted(unittest.TestCase):
    def test_there_are_ten_of_them_and_the_totals_agree(self):
        self.assertEqual(len(REAL), 10)
        self.assertEqual(K["totals"]["real_defects"], 10)
        self.assertEqual(K["totals"]["red_herrings"], 8)
        self.assertEqual(K["totals"]["counted"], 10)
        self.assertEqual(K["totals"]["authored"], 8)

    def test_provenance_is_stated_on_every_single_entry(self):
        """«a reader must be able to tell the two apart at a glance»."""
        for d in K["defects"]:
            self.assertIn(d["provenance"], ("counted", "authored"), d["id"])

    def test_every_counted_finding_carries_the_command_and_its_output(self):
        for d in REAL:
            self.assertTrue(d["command"].strip(), d["id"])
            self.assertTrue(d["command_output"].strip(), d["id"])
            self.assertIs(d["command_verified"], True, d["id"])

    def test_every_finding_carries_a_half_of_the_verdict(self):
        halves = {"attacked", "not-succeeded", "not-proven"}
        for d in REAL:
            self.assertIn(d["verdict_half"], halves, d["id"])

    def test_all_three_halves_are_actually_represented(self):
        """A key with only `attacked` findings would measure the same half twice
        and leave `not-proven` — the reason this verdict is not `clean` — unscored."""
        self.assertEqual(set(d["verdict_half"] for d in REAL),
                         {"attacked", "not-succeeded", "not-proven"})


class TheProofLocationsCanActuallyBeAnchored(unittest.TestCase):
    def test_every_real_finding_has_proof_locations(self):
        """`score-report.py` drops a proof-less defect out of the denominator
        entirely, so an unanchorable finding is worse than no finding."""
        for d in REAL:
            self.assertTrue(d["proof_locations"], d["id"])

    def test_proof_spans_parse_through_the_scorers_own_reader(self):
        for d in REAL:
            spans = SR.proof_spans(d)
            self.assertEqual(len(spans), len(d["proof_locations"]), d["id"])
            for (f, lo, hi) in spans:
                self.assertTrue(f and lo and hi <= 10 ** 9, (d["id"], f))
                self.assertLessEqual(lo, hi, (d["id"], f, lo, hi))

    def test_no_real_finding_shares_a_line_with_another(self):
        owner = {}
        for d in REAL:
            for (f, lo, hi) in SR.proof_spans(d):
                for n in range(lo, hi + 1):
                    self.assertNotIn((f, n), owner,
                                     "%s and %s both claim %s:%d"
                                     % (owner.get((f, n)), d["id"], f, n))
                    owner[(f, n)] = d["id"]

    def test_no_real_finding_stands_on_a_decoy(self):
        """One citation must never score as a finding AND a false positive."""
        dec = {}
        for d in DECOY:
            for (f, lo, hi) in SR.proof_spans(d):
                if lo is None:
                    dec.setdefault(f, None)          # whole-file claim
                else:
                    dec.setdefault(f, set())
                    if dec[f] is not None:
                        dec[f].update(range(lo, hi + 1))
        for d in REAL:
            for (f, lo, hi) in SR.proof_spans(d):
                if f not in dec:
                    continue
                self.assertIsNotNone(dec[f],
                                     "%s anchors in %s, claimed whole by a decoy"
                                     % (d["id"], f))
                self.assertFalse(set(range(lo, hi + 1)) & dec[f],
                                 "%s overlaps a decoy in %s" % (d["id"], f))

    def test_D05_no_longer_anchors_inside_a_binary(self):
        """THE TRAP THIS KEY USED TO CARRY. `citecheck.extract` only recognises
        `path:line`, so anchoring `mon/utmp/btmp` demanded a citation into a binary —
        which v13's guard exists to refuse. It cost every arm 1 of 8 decoys, forever."""
        d = [x for x in DECOY if x["id"] == "D05"][0]
        self.assertTrue(d["proof_locations"])
        for pl in d["proof_locations"]:
            self.assertNotIn("utmp", pl["file"])
        self.assertEqual(d["proof_locations"][0]["file"], "MANIFEST.tsv")

    def test_D04_anchors_both_records_it_names(self):
        """The prose named :35058 AND :35657; the anchor named only the first."""
        d = [x for x in DECOY if x["id"] == "D04"][0]
        lines = sorted(pl["line_start"] for pl in d["proof_locations"])
        self.assertEqual(lines, [35058, 35657])


class TheKeyDeclaresWhatItCannotDo(unittest.TestCase):
    def test_it_names_its_own_authored_component(self):
        self.assertIn("authored", K["derivation"]["authored_means"])
        self.assertIn("SELECTION", K["derivation"]["authored_means"])

    def test_it_records_the_two_findings_whose_patterns_are_narrow(self):
        """Both were found by comparing against the 2026-08-18 report and both were
        deliberately NOT widened, because widening a key after reading which lines
        its report cited is how a measurement gets fitted to its answer."""
        lim = {x["id"]: x for x in K["derivation"]["known_limitations"]}
        self.assertEqual(sorted(lim), ["R01", "R10"])
        for x in lim.values():
            self.assertTrue(x["not_fixed_because"].strip())

    def test_it_records_the_operator_exclusion(self):
        self.assertIn("100.64.0.0/10", K["derivation"]["operator_exclusion"])


class TheCorpusStillBacksIt(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(CORPUS), "fleet-negative corpus not present")
    def test_no_proof_location_is_in_a_file_v16_refuses_to_read(self):
        by_rel, _ = CC.index_corpus(CORPUS)
        for d in K["defects"]:
            for (f, _lo, _hi) in SR.proof_spans(d):
                if f not in by_rel:          # a whole-file anchor to a real path
                    self.fail("%s names %s, absent from the corpus" % (d["id"], f))
                if d["provenance"] == "authored" and not d.get("proof_locations"):
                    continue
                if d["provenance"] == "counted" or d.get("proof_locations"):
                    self.assertFalse(CC.looks_binary(by_rel[f]),
                                     "%s anchors in binary %s" % (d["id"], f))

    @unittest.skipUnless(os.path.isdir(CORPUS), "fleet-negative corpus not present")
    def test_regenerating_reproduces_the_committed_key_byte_for_byte(self):
        """A derived key that cannot be re-derived is an authored key with a story."""
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "k.json")
            r = subprocess.run([sys.executable, BUILDER, "--corpus", CORPUS,
                                "--corpus-root", K["corpus_root"],
                                "--dataset", K["dataset"], "--out", out],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(open(out, "rb").read(), open(KEY, "rb").read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
