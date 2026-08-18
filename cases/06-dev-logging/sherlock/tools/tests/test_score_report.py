#!/usr/bin/env python3
"""Tests for eval/bench/score-report.py — scoring the REPORT, not the worklist.

Everything measured before this file scored a worklist: Step 1's 250-row attention
budget, which is an upper bound on what the skill can find and says nothing about
what it told the reader. The deliverable is a report. This scores one.

The design each test defends:

* THE PRIMARY FINDINGS COLUMN COSTS NOTHING AND IS NAMED HONESTLY. Every real
  defect in the key carries `proof_locations`. Whether the report cited one of
  them is string-and-path arithmetic against the corpus index — no judge, no
  token, no money — so it can be run over every historical trajectory
  retroactively. It is `anchored`: the report put that evidence in front of the
  reader. It is NOT `understood`, and the two must never share a column, because
  a report can cite the right line and draw the wrong conclusion. `asserted` is
  the judged column and it is `None` — never 0 — when no judge ran. Recording an
  unasked question as a negative answer is the same class of lie as an unmeasured
  cost recorded as 0.

* AN AMBIGUOUS CITATION IS NOT AN ANCHOR. v16 made ambiguity fail closed inside
  citecheck for one reason: a corpus that ships `auth.log` on ten hosts turns a
  bare `auth.log:8977` into a confident verdict about a machine the report never
  named. The same rule has to hold here or the anchoring number launders exactly
  what citecheck refuses. A citation that cannot be pinned to ONE file anchors
  nothing and is counted and printed as ambiguous.

* RED HERRINGS NEVER TOUCH THE NUMERATOR — ON EITHER AXIS. score-bench.py already
  encodes this for the judged column; anchoring adds a second door to the same
  room. Citing a decoy's proof line is not recall, and the denominator stays real
  defects only. `decoys_anchored` and `decoys_asserted` are their own numbers,
  because the field's own benchmarks measure false-positive rate directly and it
  is the number that separates an investigator from an alarm.

* A DECOY IS ASKED THE OPPOSITE QUESTION. Reusing the defect prompt on an entry
  titled «RED HERRING: …» inverts the score: `false` reads as "refused" when the
  report actually presented the decoy as a root cause. `build_herring_prompt` in
  score-bench.py exists for this; this scorer imports it rather than owning a
  second copy.

* FAIL LOUD. A missing corpus, an unreadable report and a judge transport failure
  all RAISE. A transport failure recorded as "not found" is indistinguishable
  from a real miss.

* NO SILENT CAPS. citecheck caps a cited span at MAX_RANGE lines and the judge
  prompt truncates the report at 120,000 chars. Both are real and both must be
  printed when they bite.

    python3 tools/tests/test_score_report.py
"""
import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
SCORER = os.path.join(SHERLOCK, "eval", "bench", "score-report.py")

_spec = importlib.util.spec_from_file_location("score_report", SCORER)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


# --------------------------------------------------------------------------
# fixtures: a corpus small enough to read by eye, shaped like the real ones
# --------------------------------------------------------------------------
def build_corpus(tmp, files):
    """files: {relpath: [line, line, ...]} -> writes them under tmp."""
    for rel, lines in files.items():
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return tmp


def numbered(n, text="filler line"):
    return ["%s %d" % (text, i) for i in range(1, n + 1)]


def key_with(defects, dataset="test", verdict=None):
    k = {"dataset": dataset, "defects": defects}
    if verdict:
        k["verdict"] = verdict
    return k


def defect(cid, title, locs, root_cause="because"):
    return {"id": cid, "title": title, "root_cause": root_cause,
            "proof_locations": locs}


def loc(f, a, b=None):
    return {"file": f, "line_start": a, "line_end": b if b is not None else a}


class Tripwire(object):
    """Anything that must not be reached. Raises loudly if it ever is."""

    def __init__(self, what):
        self.what = what

    def __call__(self, *a, **kw):
        raise AssertionError("the judge-free path reached %s" % self.what)


class StubJudge(object):
    """Answers from a table keyed by the defect id found in the prompt."""

    def __init__(self, answers, default=False):
        self.answers = answers
        self.default = default
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        for cid, val in self.answers.items():
            if cid in prompt:
                return json.dumps({"found": bool(val), "why": "stub"})
        return json.dumps({"found": bool(self.default), "why": "stub"})


# --------------------------------------------------------------------------
# A. the judge-free anchoring column
# --------------------------------------------------------------------------
class TestAnchoringIsFree(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {
            "app/checkout.log": numbered(500),
            "db/postgres.log": numbered(500),
        })

    def test_exact_proof_line_anchors_and_costs_nothing(self):
        key = key_with([defect("D01", "checkout blows up",
                               [loc("app/checkout.log", 178)])])
        report = "Finding 1: checkout fails. app/checkout.log:178 «filler line 178»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 1)
        self.assertEqual(rec["anchorable"], 1)
        self.assertIsNone(rec["asserted"],
                          "no judge ran, so `asserted` must be None, never 0")

    def test_the_free_path_touches_no_network_and_no_judge(self):
        """The tripwire, not an argument. `asserted` being None proves the column
        was not filled; this proves nothing was SENT. Both matter: the whole claim
        of the anchoring column is that it can be replayed over every historical
        trajectory for free."""
        key = key_with([defect("D01", "x", [loc("app/checkout.log", 178)])])
        saved = (S.score_case.http_call, S.score_bench.score)
        S.score_case.http_call = Tripwire("score_case.http_call")
        S.score_bench.score = Tripwire("score_bench.score")
        try:
            with redirect_stdout(io.StringIO()):
                rec = S.score(key, "app/checkout.log:178 «filler line 178»",
                              self.tmp, call=None)
        finally:
            S.score_case.http_call, S.score_bench.score = saved
        self.assertEqual(rec["anchored"], 1)
        self.assertFalse(rec["judged"])

    def test_line_outside_the_proof_span_does_not_anchor(self):
        key = key_with([defect("D01", "x", [loc("app/checkout.log", 178)])])
        report = "app/checkout.log:220 «filler line 220»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 0)

    def test_a_cited_range_anchors_every_proof_location_inside_it(self):
        """score-ait's lesson: a citation is an ADDRESS the reader is told to read
        around, so every proof location inside the span is in front of them."""
        key = key_with([defect("D01", "x", [loc("app/checkout.log", 200),
                                            loc("app/checkout.log", 210),
                                            loc("app/checkout.log", 480)])])
        report = "app/checkout.log:195-215 «filler line 200»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        row = rec["per_defect"][0]
        self.assertTrue(row["anchored"])
        self.assertEqual(row["anchor_hits"], 2,
                         "two of three proof locations fall inside 195-215")
        self.assertEqual(row["proof_locations"], 3)

    def test_overlapping_proof_span_anchors(self):
        key = key_with([defect("D01", "x", [loc("app/checkout.log", 100, 140)])])
        report = "app/checkout.log:135 «filler line 135»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 1)

    def test_right_line_wrong_file_does_not_anchor(self):
        key = key_with([defect("D01", "x", [loc("app/checkout.log", 178)])])
        report = "db/postgres.log:178 «filler line 178»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 0)

    def test_unambiguous_basename_still_anchors(self):
        """A single-host bundle must not be penalised for a short path."""
        key = key_with([defect("D01", "x", [loc("app/checkout.log", 178)])])
        report = "checkout.log:178 «filler line 178»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 1)


class TestAmbiguityIsNotAnAnchor(unittest.TestCase):
    """Ten hosts, one filename. This is the AIT-LDS shape and the v16 rule."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {
            "host-a/auth.log": numbered(300),
            "host-b/auth.log": numbered(300),
        })
        self.key = key_with([defect("D01", "intrusion on host-a",
                                    [loc("host-a/auth.log", 100)])])

    def test_bare_basename_across_hosts_anchors_nothing(self):
        report = "auth.log:100 «filler line 100»"
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(self.key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 0,
                         "a citation that could mean two hosts anchors neither")
        self.assertEqual(rec["ambiguous_citations"], 1)
        self.assertIn("ambiguous", buf.getvalue().lower())

    def test_host_qualified_citation_anchors(self):
        report = "host-a/auth.log:100 «filler line 100»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 1)

    def test_the_other_host_does_not_anchor(self):
        report = "host-b/auth.log:100 «filler line 100»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 0)


# --------------------------------------------------------------------------
# B. decoys
# --------------------------------------------------------------------------
class TestDecoys(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(300),
                                "app/b.log": numbered(300)})
        self.key = key_with([
            defect("D01", "the real thing", [loc("app/a.log", 10)]),
            defect("D02", "RED HERRING: the loud thing", [loc("app/b.log", 20)]),
        ])

    def test_anchoring_a_decoy_never_enters_the_numerator(self):
        report = "app/b.log:20 «filler line 20»"
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, report, self.tmp, call=None)
        self.assertEqual(rec["anchored"], 0)
        self.assertEqual(rec["anchorable"], 1, "denominator is real defects only")
        self.assertEqual(rec["decoys_anchored"], 1)
        self.assertEqual(rec["decoys"], 1)

    def test_asserting_a_decoy_is_a_false_positive_not_recall(self):
        report = "app/a.log:10 and app/b.log:20 both matter"
        judge = StubJudge({"D01": True, "D02": True})
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, report, self.tmp, call=judge)
        self.assertEqual(rec["asserted"], 1)
        self.assertEqual(rec["total_real"], 1)
        self.assertEqual(rec["decoys_asserted"], 1)

    def test_a_decoy_is_asked_the_opposite_question(self):
        report = "app/a.log:10"
        judge = StubJudge({}, default=False)
        with redirect_stdout(io.StringIO()):
            S.score(self.key, report, self.tmp, call=judge)
        herring_prompts = [p for p in judge.prompts if "D02" in p]
        self.assertEqual(len(herring_prompts), 1)
        self.assertIn("DECOY", herring_prompts[0],
                      "a red herring must get build_herring_prompt, not the "
                      "defect prompt — the score inverts otherwise")

    def test_no_judge_leaves_both_judged_columns_none(self):
        report = "app/b.log:20"
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, report, self.tmp, call=None)
        self.assertIsNone(rec["asserted"])
        self.assertIsNone(rec["decoys_asserted"])
        self.assertEqual(rec["decoys_anchored"], 1,
                         "the free decoy column still works with no judge")


# --------------------------------------------------------------------------
# C. verdict — delegated, never reimplemented
# --------------------------------------------------------------------------
class TestVerdict(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50)})

    def test_verdict_matches_score_verdict_exactly(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)])],
                       verdict="attacked-not-proven")
        report = "body\n\n## ВЕРДИКТ\nАтаковали, но компрометация не доказана."
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        want, how = S.score_verdict.extract(report)
        self.assertEqual(rec["verdict"]["reported"], want)
        self.assertEqual(rec["verdict"]["read_from"], how)
        self.assertEqual(rec["verdict"]["truth"], "attacked-not-proven")
        self.assertTrue(rec["verdict"]["correct"])

    def test_missing_verdict_section_is_absent_and_a_delivery_defect(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)])],
                       verdict="compromised")
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, "just some findings, app/a.log:5", self.tmp,
                          call=None)
        self.assertEqual(rec["verdict"]["reported"], "absent")
        self.assertFalse(rec["verdict"]["correct"])

    def test_a_key_with_no_verdict_records_null_not_a_crash(self):
        """answer-key.json (bench649) declares no verdict. Scoring it must still
        produce every other column instead of exiting."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, "app/a.log:5 «filler line 5»", self.tmp,
                          call=None)
        self.assertIsNone(rec["verdict"]["truth"])
        self.assertIsNone(rec["verdict"]["correct"])
        self.assertEqual(rec["anchored"], 1)


# --------------------------------------------------------------------------
# D. citation integrity, folded in from v16 citecheck
# --------------------------------------------------------------------------
class TestCitecheckFold(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {
            "host-a/auth.log": ["Accepted password for root from 10.0.0.1"] * 3,
            "host-b/auth.log": numbered(3),
            "app/only.log": numbered(50),
        })

    def test_it_uses_a_current_skill_version_not_the_stale_working_copy(self):
        """tools/citecheck.py in the working tree is a v5-v10 snapshot: it has no
        `ambiguous` verdict at all. Loading it here would silently drop the one
        column this scorer promises to keep visible.

        The PIN used to be the whole guard, and a pin is a guard that expires: it
        named v16, v19 taught citecheck to read `.gz`, and the negative-control key
        moved its biggest finding into `auth.log.2.gz`. So the version is RESOLVED
        (highest on disk, or $SHERLOCK_CITECHECK_VERSION) and the guard is now a
        behavioural probe instead of a string."""
        self.assertIn("ambiguous", S.citecheck.VERDICTS)
        self.assertNotIn("ambiguous", S.citecheck.RANK,
                         "`ambiguous` stays out of RANK on purpose")
        self.assertIn(os.path.join("skills", S.CITECHECK_VERSION),
                      S.CITECHECK_PATH)
        self.assertRegex(S.CITECHECK_VERSION, r"^v[0-9][0-9.]*$")

    def test_the_resolved_version_is_the_highest_one_on_disk(self):
        """A pin that names a number goes stale the day the next arm ships. This
        one is derived, so v21 becomes current by existing."""
        name, path = S.resolve_citecheck(S.SHERLOCK)
        self.assertEqual(name, S.CITECHECK_VERSION)
        self.assertTrue(os.path.isfile(path))
        avail = [v for v, _n, _p in S._skill_citecheckers(S.SHERLOCK)]
        self.assertEqual(S._version_tuple(name), max(avail))

    def test_an_explicit_version_can_still_be_pinned(self):
        """Re-checking an old score needs the old checker. The receipt in the
        record names a version AND a sha; both have to be reachable again."""
        name, path = S.resolve_citecheck(S.SHERLOCK, want="v16")
        self.assertEqual(name, "v16")
        self.assertIn(os.path.join("skills", "v16"), path)

    def test_a_checker_that_cannot_PRODUCE_ambiguous_is_refused(self):
        """The guard the old comment described, made real. `tools/citecheck.py` is
        the v5-v10 snapshot that is actually on disk in this repo — loading it must
        RAISE, not quietly score with one fewer column."""
        stale = os.path.join(TOOLS, "citecheck.py")
        self.assertTrue(os.path.isfile(stale), "the stale snapshot is the fixture")
        mod = S._load("citecheck_stale", stale)
        self.assertNotIn("ambiguous", getattr(mod, "VERDICTS", ()))
        with self.assertRaises(Exception):
            S.assert_ambiguity_capable(mod, "stale", stale)

    def test_the_checker_in_use_really_produces_ambiguous_on_a_two_host_corpus(self):
        """Not `"ambiguous" in VERDICTS` — the verdict actually coming out."""
        S.assert_ambiguity_capable(S.citecheck, S.CITECHECK_VERSION,
                                   S.CITECHECK_PATH)

    def test_a_gz_citation_is_not_scored_binary_file(self):
        """v16 called every `.gz` a binary because it sniffed the RAW bytes, and a
        gzip stream is full of NULs. v19 reads through `gzip.open`. The negative
        control's biggest finding now lives in `mon/auth/auth.log.2.gz` (R01 went
        6,650 -> 16,501), so a scorer still on v16 would score a CORRECT citation
        of it as `binary-file`. No arm has cited a `.gz` yet; this is the test that
        fires before one does."""
        gz = os.path.join(self.tmp, "mon", "auth", "auth.log.2.gz")
        os.makedirs(os.path.dirname(gz), exist_ok=True)
        with gzip.open(gz, "wt", encoding="utf-8") as fh:
            fh.write("Aug 02 00:00:01 mon sshd[1]: Invalid user admin from 1.2.3.4\n"
                     * 3)
        key = key_with([defect("D01", "ssh sweep",
                               [{"file": "mon/auth/auth.log.2.gz",
                                 "line_start": 2, "line_end": 2}])])
        report = ("mon/auth/auth.log.2.gz:2 "
                  "«Aug 02 00:00:01 mon sshd[1]: Invalid user admin from 1.2.3.4»")
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        self.assertEqual(rec["citecheck"]["binary-file"], 0,
                         "a gzipped TEXT log is citable since v19")
        self.assertEqual(rec["citecheck"]["ok"], 1)
        self.assertEqual(rec["anchored"], 1,
                         "and a `.gz` proof location must anchor")

    def test_the_record_carries_a_re_checkable_receipt(self):
        key = key_with([defect("D01", "x", [loc("app/only.log", 5)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, "app/only.log:5 «filler line 5»", self.tmp,
                          call=None)
        self.assertEqual(rec["citecheck_version"], S.CITECHECK_VERSION)
        self.assertEqual(rec["citecheck_sha"],
                         hashlib.sha1(open(S.CITECHECK_PATH, "rb").read()).hexdigest())
        self.assertEqual(rec["citecheck_path"], S.CITECHECK_PATH)

    def test_summary_is_folded_in_with_ambiguous_as_its_own_count(self):
        key = key_with([defect("D01", "x", [loc("app/only.log", 5)])])
        report = ("app/only.log:5 «filler line 5»\n"
                  "auth.log:1 «Accepted password for root from 10.0.0.1»\n")
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        cc = rec["citecheck"]
        self.assertEqual(cc["ambiguous"], 1)
        self.assertGreaterEqual(cc["ok"], 1)
        self.assertEqual(cc["total"], 2)
        self.assertIsNotNone(cc["verified_pct"])
        for v in ("ok", "wrong-content", "out-of-range", "missing-file",
                  "no-quote", "ambiguous", "binary-file", "unverifiable"):
            self.assertIn(v, cc, "%s must stay visible as its own count" % v)

    def test_ambiguous_is_never_absorbed_into_not_ok(self):
        key = key_with([defect("D01", "x", [loc("app/only.log", 5)])])
        report = "auth.log:1 «Accepted password for root from 10.0.0.1»\n"
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, report, self.tmp, call=None)
        cc = rec["citecheck"]
        self.assertEqual(cc["ambiguous"], 1)
        self.assertEqual(cc["wrong-content"], 0)
        self.assertEqual(cc["missing-file"], 0)


# --------------------------------------------------------------------------
# E. fail loud, and never cap in silence
# --------------------------------------------------------------------------
class TestFailsLoud(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50)})
        self.key = key_with([defect("D01", "x", [loc("app/a.log", 5)])])

    def test_missing_corpus_raises(self):
        with self.assertRaises(Exception):
            S.score(self.key, "app/a.log:5", os.path.join(self.tmp, "nope"),
                    call=None)

    def test_empty_report_raises_rather_than_scoring_zero(self):
        with self.assertRaises(Exception):
            S.score(self.key, "   ", self.tmp, call=None)

    def test_judge_transport_failure_raises_not_recorded_as_a_miss(self):
        def boom(_prompt):
            raise RuntimeError("connection reset by peer")
        with self.assertRaises(Exception):
            with redirect_stdout(io.StringIO()):
                S.score(self.key, "app/a.log:5 «filler line 5»", self.tmp,
                        call=boom)

    def test_a_capped_citation_span_is_printed(self):
        """citecheck reads at most MAX_RANGE lines of a cited span. A report that
        cites :5-400 has NOT put lines 46..400 in front of anyone, and the cap
        must say so out loud."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            S.score(key, "app/a.log:5-400 «filler line 5»", self.tmp,
                    call=None)
        out = buf.getvalue()
        self.assertIn("40", out)
        self.assertRegex(out.lower(), r"cap|truncat|обрез")

    def test_an_oversized_report_says_what_the_judge_did_not_see(self):
        big = ("app/a.log:5 «filler line 5»\n" + ("x" * 200) + "\n") * 800
        self.assertGreater(len(big), S.JUDGE_PROMPT_LIMIT)
        buf = io.StringIO()
        with redirect_stdout(buf):
            S.score(self.key, big, self.tmp, call=StubJudge({}))
        out = buf.getvalue()
        self.assertRegex(out.lower(), r"truncat|dropped|обрез")
        self.assertIn(str(len(big) - S.JUDGE_PROMPT_LIMIT), out.replace(",", ""))

    def test_a_timestamp_is_not_reported_as_a_lost_citation(self):
        """`11:00` and `10.42.12.20:8080` match the citation regex and resolve to
        nothing. Counting them made the unresolved warning say 15 where the real
        number was 6 — the same gate citecheck uses for `не-ссылка` settles it."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, "outage from 11:00 to 15:59 on 10.42.12.20:8080; "
                               "app/a.log:5 «filler line 5»\n"
                               "and ghost/missing.log:9 says otherwise",
                          self.tmp, call=None)
        self.assertEqual(rec["unresolved_citations"], 1,
                         "only ghost/missing.log:9 is a lost citation")

    def test_a_real_defect_with_no_proof_locations_is_printed_not_dropped(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)]),
                        {"id": "D02", "title": "no proof recorded",
                         "root_cause": "y"}])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, "app/a.log:5 «filler line 5»", self.tmp,
                          call=None)
        self.assertEqual(rec["anchorable"], 1,
                         "a defect with no proof locations cannot be anchored, "
                         "so it must leave the denominator")
        self.assertEqual(rec["unanchorable"], ["D02"])
        self.assertIn("D02", buf.getvalue())


# --------------------------------------------------------------------------
# F. the negative control's key shape
# --------------------------------------------------------------------------
class TestNegativeControlShape(unittest.TestCase):
    """answer-key-fleet-negative.json carries no `proof_locations` at all — each
    decoy has a single `anchor` string, sometimes `path:line`, sometimes a bare
    path. It is the only corpus whose correct answer is the middle verdict, so a
    scorer that cannot read its shape cannot score the one run that matters."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"mon/auth/auth.log": numbered(9000),
                                "contabo/nginx/access.log.1": numbered(100)})

    def test_anchor_with_a_line_number_behaves_like_a_proof_location(self):
        key = key_with([{"id": "D02", "title": "RED HERRING: one good login",
                         "root_cause": "z", "anchor": "mon/auth/auth.log:8977"}],
                       verdict="attacked-not-proven")
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, "mon/auth/auth.log:8977 «filler line 8977»",
                          self.tmp, call=None)
        self.assertEqual(rec["decoys_anchored"], 1)
        self.assertEqual(rec["anchorable"], 0)

    def test_a_bare_path_anchor_matches_any_line_of_that_file(self):
        key = key_with([{"id": "D07", "title": "RED HERRING: a scanner UA",
                         "root_cause": "z",
                         "anchor": "contabo/nginx/access.log.1"}],
                       verdict="attacked-not-proven")
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, "contabo/nginx/access.log.1:42 «filler line 42»",
                          self.tmp, call=None)
        self.assertEqual(rec["decoys_anchored"], 1)

    def test_zero_real_defects_does_not_divide_by_zero(self):
        key = key_with([{"id": "D01", "title": "RED HERRING: noise",
                         "root_cause": "z", "anchor": "mon/auth/auth.log"}],
                       verdict="attacked-not-proven")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, "mon/auth/auth.log:1 «filler line 1»\n"
                               "## ВЕРДИКТ\nАтаковали, но не доказано.",
                          self.tmp, call=None)
        self.assertEqual(rec["anchorable"], 0)
        self.assertIsNone(rec["anchored_pct"])
        self.assertTrue(rec["verdict"]["correct"])


# --------------------------------------------------------------------------
# G. what lands on disk
# --------------------------------------------------------------------------
class TestLedger(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50)})
        self.key = key_with([defect("D01", "x", [loc("app/a.log", 5)])],
                            verdict="compromised")

    def test_the_record_is_json_serialisable_and_names_its_provenance(self):
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, "app/a.log:5 «filler line 5»\n"
                                    "## VERDICT\ncompromised", self.tmp,
                          call=None)
        json.dumps(rec, ensure_ascii=False)      # must not raise
        self.assertEqual(rec["dataset"], "test")
        self.assertFalse(rec["judged"])
        self.assertIsNone(rec["judge_model"])
        self.assertIn("citecheck_version", rec)

    def test_judged_run_records_the_judge_model(self):
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, "app/a.log:5 «filler line 5»", self.tmp,
                          call=StubJudge({"D01": True}))
        self.assertTrue(rec["judged"])
        self.assertEqual(rec["judge_model"], S.score_case.JUDGE_MODEL)

    def test_human_table_prints_both_columns_separately(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            S.score(self.key, "app/a.log:5 «filler line 5»", self.tmp,
                    call=None)
        out = buf.getvalue()
        self.assertIn("anchored", out.lower())
        self.assertIn("asserted", out.lower())


# --------------------------------------------------------------------------
# H. the structural assertion axis — free, and NOT the same claim as `anchored`
# --------------------------------------------------------------------------
# `anchored` says the report put the proof line in front of the reader. It does
# NOT say the report concluded anything, and two measured cases prove the gap:
# BlueSky D01 was anchored and then argued away as pre-compromise, and BlueSky
# D09's frame was cited under a DIFFERENT finding. The only column that could
# catch that was `asserted`, which needs the judge — and the judge is the
# unstable axis: one identical report scored 0/2, 2/2, 2/2 on decoys across three
# runs. These reports have a structure, so the claim can be READ instead of
# judged: a citation inside the findings section is an assertion, the same
# citation inside «Отклонённые кандидаты» is a mention. Parsing, not judgement.
FINDINGS_REPORT = """# Отчёт

## 0. Короткий ответ

Что-то произошло. app/a.log:5 «filler line 5»

## 1. Находки

### Н-1 · Первое

Вот доказательство: app/a.log:10 «filler line 10»

## 2. Отклонённые кандидаты

- **«Второе»** — нет. app/a.log:20 «filler line 20» — это фон.
- **«Третье»** — ничего относящегося.

## 3. Покрытие

| путь | что искал | вердикт |
|---|---|---|
| app/b.log | всё | ничего относящегося |

## ВЕРДИКТ

compromised
"""


class TestStructuralAssertionAxis(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})

    def score(self, key, report=FINDINGS_REPORT):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, report, self.tmp, call=None)
        return rec, buf.getvalue()

    def test_a_citation_in_the_findings_section_is_an_assertion(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["anchored"], 1)
        self.assertEqual(rec["presented"], 1)
        self.assertEqual(rec["presentable"], 1)

    def test_the_same_citation_in_the_rejected_section_is_only_a_mention(self):
        """BlueSky D01: anchored, then argued away as pre-compromise. `anchored`
        cannot see the difference; this axis can, and for free."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 20)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["anchored"], 1, "it IS cited — that stays true")
        self.assertEqual(rec["presented"], 0)
        self.assertEqual(rec["dismissed"], 1)
        self.assertEqual(rec["per_defect"][0]["anchored_zones"], ["rejected"])

    def test_a_citation_in_neither_section_anchors_but_asserts_nothing(self):
        """The executive summary, the coverage table and the inventory are all
        real places to cite from, and none of them is a finding."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 5)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["anchored"], 1)
        self.assertEqual(rec["presented"], 0)
        self.assertEqual(rec["dismissed"], 0)
        self.assertEqual(rec["anchored_outside_findings"], 1)
        self.assertEqual(rec["per_defect"][0]["anchored_zones"], ["other"])

    def test_the_two_axes_are_reported_side_by_side_and_never_merged(self):
        """The project's rule: axes are never summed. `anchored` also STAYS,
        because it is what makes retroactive scoring of old trajectories possible."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 20)])])
        rec, out = self.score(key)
        self.assertIn("anchored", rec)
        self.assertIn("presented", rec)
        self.assertNotEqual(rec["anchored"], rec["presented"])
        self.assertIn("anchored", out.lower())
        self.assertIn("presented", out.lower())

    def test_it_records_WHICH_heading_the_anchor_sits_under(self):
        """BlueSky D09: the analyst cited a frame of the payload's callback and
        filed it under the port scan. No free axis can call that wrong, but the
        heading it landed under is free to record — so a reader can see it."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["per_defect"][0]["anchored_headings"],
                         ["Н-1 · Первое"])

    def test_an_unparseable_report_returns_none_and_says_why(self):
        """Never a silent 0. `asserted nothing` and `could not be read` are
        different facts, and one of them is about the report."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        rec, out = self.score(key, "no headings at all. app/a.log:10 «filler line 10»")
        self.assertEqual(rec["anchored"], 1, "anchoring is unaffected")
        self.assertIsNone(rec["presented"])
        self.assertIsNone(rec["dismissed"])
        self.assertIsNone(rec["decoys_presented"])
        self.assertIsNone(rec["per_defect"][0]["presented"])
        self.assertFalse(rec["structure"]["parsed"])
        self.assertTrue(rec["structure"]["why"])
        self.assertIn("находк", rec["structure"]["why"].lower())
        self.assertRegex(out, r"NOT MEASURED|НЕ ИЗМЕР|could not")

    def test_a_report_with_findings_but_no_rejected_section_still_scores(self):
        """One missing half must not delete the other. `presented` is computable;
        `dismissed` is not, so it is None — never 0."""
        rep = "## 1. Находки\n\n### Н-1\n\napp/a.log:10 «filler line 10»\n"
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        rec, _ = self.score(key, rep)
        self.assertEqual(rec["presented"], 1)
        self.assertIsNone(rec["dismissed"])
        self.assertTrue(rec["structure"]["parsed"])
        self.assertIsNone(rec["structure"]["rejections"])

    def test_the_axis_sends_nothing_anywhere(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        saved = (S.score_case.http_call, S.score_bench.score)
        S.score_case.http_call = Tripwire("score_case.http_call")
        S.score_bench.score = Tripwire("score_bench.score")
        try:
            rec, _ = self.score(key)
        finally:
            S.score_case.http_call, S.score_bench.score = saved
        self.assertEqual(rec["presented"], 1)
        self.assertFalse(rec["judged"])

    def test_presented_never_fills_the_judged_column(self):
        """`asserted` is the JUDGED column. A free number is not allowed to
        impersonate it — that is the same lie as recording an unasked question as
        a negative answer."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        self.assertIsNone(rec["asserted"])
        self.assertIsNone(rec["per_defect"][0]["asserted"])
        self.assertEqual(rec["presented"], 1)

    def test_headings_inside_a_code_fence_are_not_sections(self):
        rep = ("## 1. Находки\n\n```\n## 2. Отклонённые кандидаты\n```\n"
               "app/a.log:10 «filler line 10»\n")
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        rec, _ = self.score(key, rep)
        self.assertEqual(rec["presented"], 1,
                         "a fenced `##` line is sample text, not a section")

    def test_a_nested_rejected_section_does_not_double_its_rows(self):
        """A section owns its subsections, so a nested «Отклонённые кандидаты»
        hands the same report line to `items_in` twice. Counted by POSITION, so
        the line is one row — and the count is of the report, not of the parse."""
        rep = ("# Отчёт\n\n## 1. Находки\n\napp/a.log:10 «filler line 10»\n\n"
               "## 2. Отклонённые кандидаты\n\n"
               "- **«A»** — нет. app/a.log:20 «filler line 20»\n\n"
               "### 2.1 Отклонённые кандидаты, продолжение\n\n"
               "- **«B»** — ничего относящегося.\n")
        rec, _ = self.score(key_with([defect("D01", "x", [loc("app/a.log", 10)])]),
                            rep)
        self.assertEqual(rec["structure"]["rejections"], 2,
                         "two bullets, counted once each despite nested spans")

    def test_a_row_the_report_really_wrote_twice_is_counted_twice(self):
        """The old text-level de-duplication existed only because the deliverable
        was a concatenation. It also silently merged two rows a report genuinely
        repeated — a claim about the report that the report never made."""
        rep = ("# Отчёт\n\n## 1. Находки\n\napp/a.log:10 «filler line 10»\n\n"
               "## 2. Отклонённые кандидаты\n\n"
               "- **«A»** — ничего относящегося.\n"
               "- **«A»** — ничего относящегося.\n")
        rec, _ = self.score(key_with([defect("D01", "x", [loc("app/a.log", 10)])]),
                            rep)
        self.assertEqual(rec["structure"]["rejections"], 2)
        self.assertEqual(rec["structure"]["rejections_uncited"], 2)

    def test_a_report_delivered_in_both_channels_parses_as_ONE_structure(self):
        """One report, handed over on both channels, is ONE document — not two
        glued together. Built through `deliverable.compose` on purpose: this is
        the rule under test, not a hand-typed guess at what it produces."""
        both = S.deliverable.compose(FINDINGS_REPORT, FINDINGS_REPORT)
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)]),
                        defect("D02", "y", [loc("app/a.log", 20)])])
        rec, _ = self.score(key, both)
        self.assertEqual(rec["presented"], 1)
        self.assertEqual(rec["dismissed"], 1)
        self.assertEqual(len(rec["structure"]["findings_sections"]), 1,
                         "one findings section — the deliverable is a union")
        self.assertEqual(rec["structure"]["rejections"], 2,
                         "two rejections in the report, and two in the composed "
                         "deliverable")


class TestDecoysGetTheFreeAssertionColumn(unittest.TestCase):
    """`decoys_asserted` needed the judge and therefore was never filled on a free
    run. The negative-control arm presented decoy D06 as its Н-10 and nothing free
    could record it. Now something can."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})

    def test_a_decoy_presented_as_a_finding_is_a_free_false_positive(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)]),
                        defect("D02", "RED HERRING: loud", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, FINDINGS_REPORT, self.tmp, call=None)
        self.assertEqual(rec["decoys_presented"], 1)
        self.assertIsNone(rec["decoys_asserted"],
                          "the JUDGED decoy column stays None — no judge ran")

    def test_a_decoy_only_in_the_rejected_section_is_a_refusal(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)]),
                        defect("D02", "RED HERRING: loud", [loc("app/a.log", 20)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, FINDINGS_REPORT, self.tmp, call=None)
        self.assertEqual(rec["decoys_anchored"], 1, "it is cited")
        self.assertEqual(rec["decoys_presented"], 0, "but it is refused")
        self.assertEqual(rec["decoys_dismissed"], 1)

    def test_a_decoy_presented_never_enters_the_findings_numerator(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 5)]),
                        defect("D02", "RED HERRING: loud", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, FINDINGS_REPORT, self.tmp, call=None)
        self.assertEqual(rec["presented"], 0)
        self.assertEqual(rec["presentable"], 1)
        self.assertEqual(rec["decoys_presented"], 1)


class TestARejectionWithNoCitationIsNotAJudgement(unittest.TestCase):
    """The Linux arm disposed of 2,473 of 2,560 worklist rows with one invented
    regex, and its disposal rows say «ничего относящегося» with no line reference
    at all. A rejection nobody can check is not a judgement, and it belongs in the
    score as its own visible count."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})

    def test_it_counts_the_rejections_and_the_uncited_ones_separately(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, FINDINGS_REPORT, self.tmp, call=None)
        st = rec["structure"]
        self.assertEqual(st["rejections"], 2)
        self.assertEqual(st["rejections_uncited"], 1)
        self.assertIn("Третье", st["uncited_rejections"][0])

    def test_an_uncited_rejection_is_printed(self):
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            S.score(key, FINDINGS_REPORT, self.tmp, call=None)
        out = buf.getvalue()
        self.assertRegex(out, r"без ссылк|no citation|uncited")

    def test_coverage_rows_are_counted_the_same_way_and_kept_separate(self):
        """«ничего относящегося» lives in the coverage table on the real arms, not
        in the rejected section. Same rule, its own count — never merged."""
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, FINDINGS_REPORT, self.tmp, call=None)
        st = rec["structure"]
        self.assertEqual(st["coverage_rows"], 1)
        self.assertEqual(st["coverage_rows_uncited"], 1)

    def test_a_rejection_that_cites_a_line_is_not_counted_as_uncited(self):
        rep = ("## 1. Находки\n\napp/a.log:10 «filler line 10»\n\n"
               "## 2. Отклонённые кандидаты\n\n"
               "- **«A»** — нет, app/b.log:3 «filler line 3» это фон.\n")
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, self.tmp, call=None)
        self.assertEqual(rec["structure"]["rejections"], 1)
        self.assertEqual(rec["structure"]["rejections_uncited"], 0)

    def test_a_rejection_that_points_at_an_existing_finding_is_backed(self):
        """«см. измерение в Н-9» is a reference to a heading that exists in this
        report's own findings section. Following it is parsing, not judgement, and
        a rejection that hands the reader a checkable place to look is not the
        shape being counted."""
        rep = ("## 1. Находки\n\n### Н-1 · Первое\n\napp/a.log:10 «filler line 10»\n\n"
               "## 2. Отклонённые кандидаты\n\n"
               "- **«A»** — нет, см. измерение в Н-1.\n"
               "- **«B»** — нет, см. измерение в Н-7.\n")
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, self.tmp, call=None)
        self.assertEqual(rec["structure"]["rejections"], 2)
        self.assertEqual(rec["structure"]["rejections_uncited"], 1,
                         "Н-1 exists as a heading; Н-7 does not")
        self.assertIn("«B»", rec["structure"]["uncited_rejections"][0])

    def test_a_coverage_row_that_names_a_finding_is_not_counted_as_uncited(self):
        """The real coverage tables say «найдено: … (Н-5, Н-6)» on the rows that
        found something and «ничего относящегося» on the rows that did not. Only
        the second shape is a disposal nobody can check."""
        rep = ("## 1. Находки\n\n### Н-1 · Первое\n\napp/a.log:10 «filler line 10»\n\n"
               "## 3. Покрытие\n\n"
               "| путь | что искал | вердикт |\n|---|---|---|\n"
               "| app/a.log | всё | найдено (Н-1) |\n"
               "| app/b.log | всё | ничего относящегося |\n")
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, self.tmp, call=None)
        self.assertEqual(rec["structure"]["coverage_rows"], 2)
        self.assertEqual(rec["structure"]["coverage_rows_uncited"], 1)

    def test_a_rejection_naming_a_bare_path_with_no_line_is_still_uncited(self):
        """«ничего относящегося» next to a path family is exactly the shape being
        counted: a file name is not a line reference."""
        rep = ("## 1. Находки\n\napp/a.log:10 «filler line 10»\n\n"
               "## 2. Отклонённые кандидаты\n\n"
               "- **«A»** — смотрел `app/b.log`, ничего относящегося.\n")
        key = key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, self.tmp, call=None)
        self.assertEqual(rec["structure"]["rejections_uncited"], 1)


# --------------------------------------------------------------------------
# I. the axis, run against every report this project has on disk
# --------------------------------------------------------------------------
RUNS = os.environ.get(
    "SHERLOCK_RUNS",
    os.path.join(SHERLOCK, "eval", "bench", "runs"))
BENCH = os.path.join(SHERLOCK, "eval", "bench")
BLUESKY_CORPUS = os.path.join(
    os.path.expanduser("~"), "Documents", "projects", "personal-os", "projects",
    "active", "attachments", "sherlock-cyber-bench", "corpus")


def _run_report(name):
    p = os.path.join(RUNS, name, "report.md")
    return p if os.path.isfile(p) else None


class TestTheAxisOnTheRealReports(unittest.TestCase):
    """The trajectories are a local artifact (`eval/bench/runs/` is gitignored), so
    these skip where they are absent and run where the evidence is."""

    BS19 = "20260818T174121Z-v19-claude-bluesky"

    @unittest.skipUnless(_run_report(BS19) and os.path.isdir(BLUESKY_CORPUS),
                         "BlueSky v19 trajectory or corpus not on this machine")
    def test_bluesky_v19_D01_is_anchored_but_not_presented(self):
        """The measured case the axis was built for: the report cites
        `evtx/incident/BlueSkyRansomware.jsonl:425` — D01's own proof — inside
        «Отклонённые кандидаты», concluding «До компрометации»."""
        key = json.load(open(os.path.join(BENCH, "answer-key-bluesky.json"),
                             encoding="utf-8"))
        rep = open(_run_report(self.BS19), encoding="utf-8").read()
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, BLUESKY_CORPUS, call=None)
        row = [r for r in rec["per_defect"] if r["defect"] == "D01"][0]
        self.assertTrue(row["anchored"])
        self.assertFalse(row["presented"])
        self.assertIn("rejected", row["anchored_zones"])
        self.assertEqual(rec["anchored"], 10)
        self.assertEqual(rec["presented"], 9,
                         "judged rather than anchored this arm is nearer 9 of 11")



# --------------------------------------------------------------------------
# L. ONE REPORT ON TWO CHANNELS IS ONE REPORT
# --------------------------------------------------------------------------
# Measured 2026-08-18 on all six arms on disk. `measure/deliverable.py` composed
# `answer` + `artifact`, both channels carried the same report, and every
# published citation total was that report's citations counted twice:
# 294 / 268 / 212 / 314 / 316 / 396 against file-only 147 / 141 / 106 / 157 /
# 158 / 198. The RATE never moved (316/316 and 158/158 are both 100 %), which is
# exactly why it survived four scoring reviews.
#
# The doubling did not stop at citations. `rejections` and the coverage-row count
# doubled too on the one arm whose two channels are worded differently — the
# text-level de-duplication inside `items_in` caught the byte-alike arms and
# missed that one. `anchored`, `presented`, `dismissed` and both decoy columns are
# set membership over defects and never moved on any arm.
#
# The channels are NOT byte-identical on any arm: the file is hard-wrapped, the
# message is not, and three arms opened with a preamble. So the fix is in the
# unit, not in an equality test — see `measure/deliverable.py`.
class TestOneReportOnTwoChannelsIsCountedOnce(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})
        self.key = key_with([defect("D01", "x", [loc("app/a.log", 10)]),
                             defect("D02", "y", [loc("app/a.log", 20)])])

    def score(self, report):
        with redirect_stdout(io.StringIO()):
            return S.score(self.key, report, self.tmp, call=None)

    @staticmethod
    def hard_wrap(text, width=40):
        """What `work/report.md` is and the final message is not."""
        out = []
        for para in text.split("\n\n"):
            words, line, lines = para.split(), "", []
            for w in words:
                if line and len(line) + 1 + len(w) > width:
                    lines.append(line)
                    line = w
                else:
                    line = (line + " " + w).strip()
            if line:
                lines.append(line)
            out.append("\n".join(lines))
        return "\n\n".join(out)

    def test_the_same_report_on_both_channels_does_not_double_the_citations(self):
        """THE defect. Six arms, every published total, exactly this."""
        one = self.score(FINDINGS_REPORT)
        both = self.score(S.deliverable.compose(FINDINGS_REPORT, FINDINGS_REPORT))
        self.assertEqual(both["citecheck"]["total"], one["citecheck"]["total"],
                         "a report handed over on two channels is one report")
        self.assertEqual(both["citecheck"]["ok"], one["citecheck"]["ok"])
        self.assertEqual(both["report_chars"], one["report_chars"])

    def test_a_hard_wrapped_file_beside_the_same_message_does_not_double_either(self):
        """The measured shape. Byte equality would have caught 0 of 6 arms."""
        wrapped = self.hard_wrap(FINDINGS_REPORT)
        self.assertNotEqual(wrapped, FINDINGS_REPORT)
        one = self.score(FINDINGS_REPORT)
        both = self.score(S.deliverable.compose(FINDINGS_REPORT, wrapped))
        self.assertEqual(both["citecheck"]["total"], one["citecheck"]["total"])

    def test_the_disposal_counts_do_not_double_either(self):
        """`rejections` and the coverage rows went 7 -> 13 and 24 -> 33 on the AIT
        v16-contaminated arm, because its two channels are worded differently.
        The count is of the REPORT's disposals, not of the delivery."""
        one = self.score(FINDINGS_REPORT)
        both = self.score(S.deliverable.compose(FINDINGS_REPORT,
                                                self.hard_wrap(FINDINGS_REPORT)))
        self.assertEqual(both["structure"]["rejections"],
                         one["structure"]["rejections"])
        self.assertEqual(both["structure"]["coverage_rows"],
                         one["structure"]["coverage_rows"])
        self.assertEqual(both["structure"]["rejections_uncited"],
                         one["structure"]["rejections_uncited"])

    def test_the_findings_columns_are_unmoved_either_way(self):
        """They never doubled — they are set membership over defects. Asserted
        here so a future "fix" cannot quietly move them while chasing the total."""
        one = self.score(FINDINGS_REPORT)
        both = self.score(S.deliverable.compose(FINDINGS_REPORT, FINDINGS_REPORT))
        for col in ("anchored", "anchorable", "presented", "dismissed",
                    "decoys_anchored", "decoys_presented"):
            self.assertEqual(both[col], one[col], col)

    def test_channels_that_genuinely_DIFFER_are_both_scored(self):
        """The whole reason the union exists: the message can carry a finding the
        file does not. De-duplicating must not cost that finding."""
        msg = FINDINGS_REPORT
        fil = FINDINGS_REPORT.replace("app/a.log:10 «filler line 10»",
                                      "app/b.log:30 «filler line 30»")
        rec = self.score(S.deliverable.compose(msg, fil))
        self.assertGreater(rec["citecheck"]["total"],
                           self.score(msg)["citecheck"]["total"],
                           "a block only the file wrote must still be counted")


class TestDivergentChannelsAreFlaggedNotCollapsed(unittest.TestCase):
    """A silent pick between two channels that disagree is the same class of
    error as counting them twice: a fact about delivery, printed as a fact about
    the analysis. The record says which one happened."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})
        self.dir = tempfile.mkdtemp()
        self.keyfile = os.path.join(self.dir, "key.json")
        with open(self.keyfile, "w", encoding="utf-8") as fh:
            json.dump(key_with([defect("D01", "x", [loc("app/a.log", 10)])],
                               dataset="unit"), fh)
        self.out = os.path.join(self.dir, "scores.jsonl")

    def run_main(self, answer, artifact):
        ledger = os.path.join(self.dir, "runs.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"dataset": "unit", "arm": "vX",
                                 "trace_dir": "/tmp/t1", "answer": answer,
                                 "artifact": artifact}, ensure_ascii=False) + "\n")
        argv = sys.argv
        sys.argv = ["score-report.py", "--key", self.keyfile, "--corpus", self.tmp,
                    "--ledger", ledger, "--dataset", "unit", "--out", self.out]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                S.main()
        finally:
            sys.argv = argv
        rec = [json.loads(l) for l in open(self.out, encoding="utf-8") if l.strip()][-1]
        return rec, buf.getvalue()

    def test_the_same_report_on_both_channels_records_no_divergence(self):
        rec, out = self.run_main(FINDINGS_REPORT, FINDINGS_REPORT)
        self.assertEqual(rec["duplication"]["relation"], "identical")
        self.assertIsNone(rec["duplication"]["warning"])
        self.assertNotIn("CHANNELS DIVERGE", out)

    def test_channels_that_differ_are_recorded_AND_printed(self):
        fil = FINDINGS_REPORT.replace("app/a.log:10 «filler line 10»",
                                      "app/b.log:30 «filler line 30»")
        rec, out = self.run_main(FINDINGS_REPORT, fil)
        d = rec["duplication"]
        self.assertEqual(d["relation"], "divergent")
        self.assertGreaterEqual(d["only_in_message"], 1)
        self.assertGreaterEqual(d["only_in_file"], 1)
        self.assertIsNotNone(d["warning"])
        self.assertIn("CHANNELS DIVERGE", out,
                      "a silent pick between disagreeing channels is the defect")

    def test_a_message_only_run_records_the_channel_and_no_warning(self):
        rec, out = self.run_main(FINDINGS_REPORT, "")
        self.assertEqual(rec["duplication"]["relation"], "message-only")
        self.assertIsNone(rec["duplication"]["warning"])
        self.assertNotIn("CHANNELS DIVERGE", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
