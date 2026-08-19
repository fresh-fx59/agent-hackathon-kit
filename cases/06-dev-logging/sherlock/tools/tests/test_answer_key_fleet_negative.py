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

THE 2026-08-19 THIRD DERIVATION — THE SECOND RENDERING, COMPUTED
----------------------------------------------------------------
The corpus deliberately ships three renderings of one journald stream. The key used
to reach the other two through `JOURNAL_MIRRORS` — an ASSERTED list of two filenames
re-scanned with a hand-written regex, hard-coded to `contabo/journal/` and applied to
three findings. It was wrong in every direction it could be: it never covered `mon/`
at all, so R09's proof lived only in `mon/syslog/syslog.tail` and the v22 arm that
found R09 correctly while citing `mon/journal/journal.short-iso:23` scored a miss; its
regex under-counted R03 (284 of 334 records) and double-counted R05 (64 physical lines
for 32 events) because `journal.export` writes ONE event as MANY lines.

It is replaced by `rendering_alternates_for` from `build-answer-key-ait.py`, which
COMPUTES the equivalence: the key is (second, host, `ident[pid]: message`), the one
normalisation is timestamp precision, and the loosening is paid for by an
equal-multiplicity gate that refuses rather than guesses. `journal.export` is out of
scope by construction — a citation addresses one physical line and an export block
is not one — so it leaves the key rather than being argued out of it.

An alternate is a CANDIDATE, never an entitlement: it is admitted only if it passes
the same three gates a primary passes, and every rejection is counted in the key.
The gate the decoys need is new and is asserted below — D02's `Accepted publickey`
line HAS a second rendering in `mon/journal/`, two files R09's alternates also land
in, so the decoys' rendered lines are claimed territory that no finding may take.

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


class TheSecondRenderingIsComputedNotAsserted(unittest.TestCase):
    """The 2026-08-19 third derivation. None of this needs the corpus: the key
    records which files each finding reaches and by what rule."""

    SRC = open(BUILDER, encoding="utf-8").read()
    # `.get` so a key that predates this derivation fails these tests one by
    # one instead of aborting the module and hiding the other 31.
    RE = K["derivation"].get("rendering_equivalence", {})

    def test_the_key_carries_a_rendering_equivalence_block_at_all(self):
        self.assertTrue(self.RE, "derivation.rendering_equivalence is absent")

    def test_the_asserted_mirror_regex_is_gone_from_the_builder(self):
        """A hard-coded list of two filenames plus a hand-written re-scan is an
        assertion about the corpus. Deleting it is the point of this derivation, so
        it is asserted rather than remembered."""
        self.assertNotIn("JOURNAL_MIRRORS = ", self.SRC)   # the list
        self.assertNotIn('"mirror":', self.SRC)             # the three uses
        # The NAME must survive, in the docstring: a deletion nobody can read the
        # reason for is how the next derivation reinvents it.
        self.assertIn("JOURNAL_MIRRORS", self.SRC)

    def test_the_builder_calls_the_computed_rule_rather_than_copying_it(self):
        """Copied code is a second implementation that drifts. The rule has ONE
        home and this builder imports it."""
        self.assertIn("build-answer-key-ait.py", self.SRC)
        self.assertIn("rendering_alternates_for", self.SRC)

    def test_the_key_says_which_rule_produced_the_alternates(self):
        for f in ("rule", "source", "key", "normalisation", "multiplicity_gate",
                  "limitations", "replaced"):
            self.assertTrue(str(self.RE[f]).strip(), f)
        self.assertIn("build-answer-key-ait.py", self.RE["source"])
        self.assertIn("JOURNAL_MIRRORS", self.RE["replaced"])

    def test_the_export_rendering_left_the_key_and_the_key_says_why(self):
        """`journalctl -o export` writes one event as many physical lines, so a
        citation cannot address the event. The asserted mirror anchored there
        anyway — R05 got 64 physical lines for 32 records."""
        for d in K["defects"]:
            for pl in (d.get("proof_locations") or []):
                self.assertNotIn("journal.export", pl["file"], d["id"])
        self.assertTrue([x for x in self.RE["limitations"] if "export" in x])

    def test_R09_now_reaches_both_journald_renderings(self):
        """The defect this derivation exists for: the v22 arm found R09 and cited
        `mon/journal/journal.short-iso`, a render of the same stream, against a key
        whose only proof was the syslog render."""
        r09 = [d for d in REAL if d["id"] == "R09"][0]
        self.assertEqual({pl["file"] for pl in r09["proof_locations"]},
                         {"mon/syslog/syslog.tail",
                          "mon/journal/journal.short-iso",
                          "mon/journal/journal.json"})
        self.assertEqual(r09["rendering_files"],
                         ["mon/journal/journal.json",
                          "mon/journal/journal.short-iso"])

    def test_the_contabo_findings_kept_their_json_rendering(self):
        """R02/R03/R05 reached `journal.json` under the asserted rule too. The
        computed rule must not lose them — it is replacing a mirror, not removing
        one."""
        for cid in ("R02", "R03", "R05"):
            d = [x for x in REAL if x["id"] == cid][0]
            self.assertEqual(d["rendering_files"],
                             ["contabo/journal/journal.json"], cid)

    def test_every_finding_declares_its_renderings_and_nothing_else_claims_any(self):
        """`rendering_files` must be exactly the files a finding reaches that its
        own scan does not — otherwise the field is decoration."""
        for d in REAL:
            files = {pl["file"] for pl in d["proof_locations"]}
            self.assertEqual(sorted(d["rendering_files"]),
                             d["rendering_files"], d["id"])
            for rel in d["rendering_files"]:
                self.assertIn(rel, files, d["id"])

    def test_the_findings_that_have_no_second_rendering_say_so_as_a_number(self):
        """Eight of the twelve gain nothing. An empty list is a measurement here,
        not a missing field, so every finding carries one."""
        got = {d["id"]: len(d["rendering_files"]) for d in REAL}
        self.assertEqual(len(got), 12)
        self.assertEqual(sorted(k for k, v in got.items() if v),
                         ["R02", "R03", "R05", "R09"])

    def test_an_alternate_is_a_candidate_that_must_pass_the_same_gates(self):
        """The one way this change could WEAKEN the instrument is by letting a
        rendering smuggle in a line a primary would have been refused."""
        self.assertTrue(self.RE["admission"].strip())
        self.assertIn("rejected", self.RE)
        for k in ("binary", "decoy", "another_finding"):
            self.assertIn(k, self.RE["rejected"], k)

    def test_the_decoys_rendered_lines_are_recorded(self):
        """Computed, not assumed: the key stores what each decoy looks like in a
        second rendering so the next derivation can see it without re-deriving."""
        dr = {x["id"]: x for x in self.RE["decoy_renderings"]}
        self.assertEqual(sorted(dr), ["D0%d" % i for i in range(1, 9)])
        for x in dr.values():
            self.assertTrue(x["treatment"].strip(), x["id"])

    def test_the_operator_login_HAS_a_second_rendering_and_no_finding_took_it(self):
        """THE LOUD ONE. D02 is the corpus's single successful authentication and
        the most tempting false positive it offers. It is written into
        `mon/journal/` as well as into `mon/auth/auth.log`, and those are two of the
        three files R09's alternates land in. Nothing collided — but only because
        the gate ran, so the gate is asserted here rather than trusted."""
        d02 = [x for x in self.RE["decoy_renderings"] if x["id"] == "D02"][0]
        self.assertEqual(d02["rendered_at"],
                         ["mon/journal/journal.json:18603",
                          "mon/journal/journal.short-iso:18552"])
        taken = set()
        for d in REAL:
            for (f, lo, hi) in SR.proof_spans(d):
                taken |= {"%s:%d" % (f, n) for n in range(lo, hi + 1)}
        for ref in d02["rendered_at"]:
            self.assertNotIn(ref, taken,
                             "a finding took D02's rendered line %s" % ref)

    def test_the_evidence_collector_has_no_second_address_at_all(self):
        """D03 is the root shell that produced this corpus. It renders nowhere, so
        there is no second way to reach it — the property the negative control most
        needs, stated as a fact rather than hoped for."""
        d03 = [x for x in self.RE["decoy_renderings"] if x["id"] == "D03"][0]
        self.assertEqual(d03["rendered_at"], [])
        blob = json.dumps(REAL, ensure_ascii=False)
        self.assertNotIn("mon/auth/auth.log\"", blob)

    def test_a_whole_file_decoy_claim_cannot_be_rendered_and_the_key_says_so(self):
        """D01 claims `mon/auth/auth.log` whole. There is no line to map, so the
        rule has nothing to say and the whole-file half of gate 2 is what protects
        it. Saying that out loud is the point."""
        d01 = [x for x in self.RE["decoy_renderings"] if x["id"] == "D01"][0]
        self.assertEqual(d01["rendered_at"], [])
        self.assertIn("whole", d01["treatment"].lower())

    def test_the_decoys_did_not_gain_anchors_from_this_change(self):
        """A rendering makes a decoy EASIER to cite. Adding it to the decoy's own
        proof_locations would move the false-positive axis in the same commit that
        moves the findings axis, and two moved numbers cannot be told apart."""
        d02 = [x for x in DECOY if x["id"] == "D02"][0]
        self.assertIsNone(d02.get("proof_locations"))
        d04 = [x for x in DECOY if x["id"] == "D04"][0]
        self.assertEqual({pl["file"] for pl in d04["proof_locations"]},
                         {"contabo/audit/audit.log"})

    def test_there_are_still_exactly_four_gates(self):
        """The gate list is the key's own summary of what it enforces. Renderings
        widened what gate 2 covers; they did not add a fifth rule."""
        self.assertEqual(len(K["derivation"]["gates"]), 4)
        self.assertIn("rendering", " ".join(K["derivation"]["gates"]).lower())

    def test_the_proof_rule_no_longer_advertises_the_asserted_mirror(self):
        self.assertNotIn("journal.export", K["derivation"]["proof_rule"])
        self.assertIn("rendering", K["derivation"]["proof_rule"])


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
