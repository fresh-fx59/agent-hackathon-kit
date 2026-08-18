#!/usr/bin/env python3
"""Tests for answer-key-fleet-negative.json — the negative control, now with a
REAL-FINDINGS half.

Until 2026-08-18 this key held eight decoys and zero real defects, so
`score-report.py` printed `anchored 0/0` on it: it could score what a report should
refuse and not one thing a report should observe. The AIT key is the mirror image —
eleven real defects, no decoys. Neither corpus could measure both halves.

This corpus ships NO ground truth, so unlike `answer-key-ait-russellmitchell.json`
the twelve real findings cannot point at a label file and say nobody authored them. What
replaces that defence is checkable here, without the 225 MB corpus:

  * every real finding is `provenance: counted` and carries the shell one-liner that
    produced its number — and the BUILDER runs it, so a wrong number cannot be
    committed;
  * no real finding shares a proof line, or a whole-file claim, with a decoy, which
    is what stops one citation scoring as a finding and a false positive at once;
  * no proof location lives in a file `citecheck.looks_binary` rejects, because the
    checker refuses those citations and an unanchorable proof is a free point nobody
    can win;
  * the verdict is still `attacked-not-proven` and no real finding claims a
    compromise.

THE 2026-08-18 SECOND DERIVATION — `.gz`
----------------------------------------
The first derivation loaded v16's citation checker, whose `looks_binary` read RAW
bytes; a gzip stream is full of NULs, so all seven `.gz` files in this corpus were
called binaries and the builder's own gate refused to anchor anything in them. That
excluded 109,708 lines of evidence, including `mon/auth/auth.log.2.gz` — the LARGEST
rotation of the SSH sweep, 9,851 invalid-user lines against the 6,650 R01 counted.
v19 fixed `looks_binary` to test the decompressed stream, v20 shipped it, and this
key was re-derived on top: R01 6,650 -> 16,501, R04 1,670 -> 4,891, R10's evidence
base 109 -> 729 lines of Mac syslog, plus R11 and R12, two findings the corpus always
held and the key could not previously see. The tests below therefore load v20 and
assert the `.gz` half explicitly, so nobody can quietly re-pin the builder to a
checker that cannot read half its evidence.

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
CC = _load("citecheck_v20", os.path.join(SHERLOCK, "skills", "v20", "tools",
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

    def test_the_key_says_out_loud_that_all_of_them_is_not_compromised(self):
        """The one sentence a future reader most needs: anchoring every real finding
        here and still answering `compromised` is WRONG, and the key must say so
        rather than leave it to be inferred from the verdict field."""
        self.assertIn("still answers", K["notes"])
        self.assertIn("twelve", K["notes"])
        self.assertIn("'compromised' is wrong", K["notes"])


class TheRealFindingsAreCounted(unittest.TestCase):
    def test_there_are_twelve_of_them_and_the_totals_agree(self):
        self.assertEqual(len(REAL), 12)
        self.assertEqual(K["totals"]["real_defects"], 12)
        self.assertEqual(K["totals"]["red_herrings"], 8)
        self.assertEqual(K["totals"]["counted"], 12)
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

    def test_the_two_narrow_patterns_were_repaired_and_the_repair_is_argued(self):
        """R01 and R10 were found narrow by the FIRST derivation, which refused to
        widen them because it was reading which lines one report happened to cite,
        and named the honest moment instead: «BEFORE the next arm runs, not after
        this one has been scored». The `.gz` re-derivation IS that moment, so both
        moved from `known_limitations` to `resolved_limitations` — and each has to
        carry the argument for why widening here is not fitting."""
        res = {x["id"]: x for x in K["derivation"]["resolved_limitations"]}
        self.assertEqual(sorted(res), ["R01", "R10"])
        for x in res.values():
            self.assertTrue(x["was"].strip())
            self.assertTrue(x["now"].strip())
            self.assertTrue(x["why_now_is_not_fitting"].strip())

    def test_it_still_records_what_it_declined_to_do(self):
        """A key that resolves its limitations and then reports none is a key that
        stopped looking. Every remaining entry must say what it did not do and why —
        including the two observables the corpus holds that the admission rule keeps
        out, so the next derivation decides rather than rediscovers."""
        lim = {x["id"]: x for x in K["derivation"]["known_limitations"]}
        self.assertTrue(lim)
        for x in lim.values():
            self.assertTrue(x["what"].strip())
            self.assertTrue(x["kept_because"].strip())
        self.assertIn("R11", lim)
        self.assertTrue([i for i in lim if i.startswith("not-admitted")])

    def test_it_records_the_operator_exclusion(self):
        self.assertIn("100.64.0.0/10", K["derivation"]["operator_exclusion"])


class TheGzRotationsAreInTheKey(unittest.TestCase):
    """The hole the second derivation closed, asserted rather than remembered.

    None of this needs the corpus: the key records the files it anchors in and the
    commands it ran, so a re-pin to a checker that cannot read a `.gz` shows up here
    as a failing test rather than as a silently smaller key."""

    GZ = ["mon/auth/auth.log.2.gz", "mon/nginx/access.log.10.gz",
          "mon/syslog/kern.log.3.gz", "contabo/nginx/access.log.2.gz",
          "mac/syslog/system.log.0.gz", "mac/syslog/system.log.1.gz"]

    def test_the_key_anchors_inside_gzipped_evidence(self):
        anchored = {pl["file"] for d in K["defects"]
                    for pl in (d.get("proof_locations") or [])
                    if pl["file"].endswith(".gz")}
        for rel in self.GZ:
            self.assertIn(rel, anchored, rel)

    def test_the_builder_is_pinned_to_a_checker_that_can_read_them(self):
        """`skills/v16` called every `.gz` a binary while its own `read_lines`
        gunzipped them, so the builder's binary gate excluded 109,708 lines of
        evidence. The key must say which checker it was derived against."""
        cc = K["derivation"]["citecheck"]
        self.assertEqual(cc["version"], "v20")
        self.assertEqual(cc["gz_files_admitted"], 7)
        self.assertEqual(cc["gz_lines_admitted"], 109708)
        self.assertLess(cc["binary_files"], cc["binary_files_under_v16"])
        src = open(BUILDER, encoding="utf-8").read()
        self.assertIn('"skills", "v20", "tools", "citecheck.py"', src)
        self.assertNotIn('"skills", "v16", "tools", "citecheck.py"', src)

    def test_the_sweep_finding_counts_the_biggest_rotation(self):
        """R01 is the finding the `.gz` guard broke hardest: `auth.log.2.gz` holds
        9,851 invalid-user lines against `auth.log.1`'s 6,650, so the key used to
        undercount the sweep it describes by 2.5x."""
        r01 = [d for d in REAL if d["id"] == "R01"][0]
        self.assertEqual(r01["command_output"], "16501")
        self.assertEqual(r01["counts"]["attempts_per_file"],
                         {"mon/auth/auth.log.2.gz": 9851,
                          "mon/auth/auth.log.1": 6650})
        self.assertIn("gzip -cd mon/auth/auth.log.2.gz", r01["command"])

    def test_the_probe_finding_counts_both_rotations(self):
        r04 = [d for d in REAL if d["id"] == "R04"][0]
        self.assertEqual(r04["command_output"], "4891")
        self.assertEqual(r04["counts"]["probes_per_file"]
                         ["mon/nginx/access.log.10.gz"], 3221)

    def test_the_two_findings_that_only_a_gz_reader_can_state(self):
        """R11 and R12 are not new facts about the fleet — both were in the corpus
        the day it was collected. They are new to the KEY, and each lives entirely
        inside a file the old gate refused."""
        ids = {d["id"] for d in REAL}
        self.assertIn("R11", ids)
        self.assertIn("R12", ids)
        for cid, rel, half in (("R11", "mon/syslog/kern.log.3.gz", "attacked"),
                               ("R12", "contabo/nginx/access.log.2.gz",
                                "not-proven")):
            d = [x for x in REAL if x["id"] == cid][0]
            self.assertEqual(d["verdict_half"], half, cid)
            self.assertEqual({pl["file"] for pl in d["proof_locations"]}, {rel},
                             cid)

    def test_R11_does_not_share_the_relay_finding_with_R09(self):
        """Same port, opposite halves of the verdict. Folding them together would
        put `attacked` and `not-proven` behind one citation."""
        r09 = [d for d in REAL if d["id"] == "R09"][0]
        r11 = [d for d in REAL if d["id"] == "R11"][0]
        self.assertEqual(r09["verdict_half"], "not-proven")
        self.assertEqual(r11["verdict_half"], "attacked")
        self.assertFalse({pl["file"] for pl in r09["proof_locations"]} &
                         {pl["file"] for pl in r11["proof_locations"]})

    def test_a_gz_of_a_binary_is_still_a_binary(self):
        """The gate was widened, not removed. `mon/utmp/btmp` is a real utmp record
        file and D05 must still anchor on the MANIFEST row rather than inside it."""
        d = [x for x in DECOY if x["id"] == "D05"][0]
        self.assertEqual(d["proof_locations"][0]["file"], "MANIFEST.tsv")
        self.assertIn("a .gz of text is text", " ".join(
            K["derivation"]["gates"]))


class TheCorpusStillBacksIt(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(CORPUS), "fleet-negative corpus not present")
    def test_no_proof_location_is_in_a_file_the_checker_refuses_to_read(self):
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
