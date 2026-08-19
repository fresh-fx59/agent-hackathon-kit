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



# --------------------------------------------------------------------------
# J. «reported as an incident» and «reported and refuted» are not one number
# --------------------------------------------------------------------------
# The field benchmark this project measures itself against (OpenSec: Sonnet 4.6
# at 100 % containment and 92.5 % FALSE-POSITIVE rate) counts one specific
# failure: reporting a benign thing AS AN INCIDENT. A decoy that a report names
# and refutes in the same breath is not that failure — it may be correct
# investigative behaviour, because a red herring that is silently ignored is
# indistinguishable from one that was never seen.
#
# `decoys_presented` could not tell those apart: it says WHERE a citation sits
# (inside «Находки»), not what the sentence around it claims. The split below
# goes one level deeper into structure the report format already mandates.
# `reference/report-format.md` is byte-identical in v16, v19 and v22 and it
# names the per-finding fields in a fixed order — «что сломано · корневая
# причина · улики · чем опровергал · что делать сейчас». It mandates NO outcome
# field, so there is nothing cheaper to read; what it does give is two fields
# with opposite polarity:
#
#     «Улики»          — the evidence FOR this finding
#     «Чем опровергал» — the check that would have KILLED it, and what it returned
#
# A decoy cited under «Улики» is asserted as an incident. A decoy cited only
# under «Чем опровергал» is presented with its refutation attached. That is
# parsing, one level below the section parse this file already defends, and it
# costs nothing.
#
# FAIL LOUD. When a report writes no mandated field labels at all, the split is
# None with a reason — never 0, which would read as «refuted nothing». And the
# split never replaces `decoys_presented`: axes in this project are reported
# beside each other, never summed.
FIELDED_REPORT = """# Отчёт

## 1. Находки

### Н-1 · Настоящий дефект

**Что сломано.** Ломается.

**Улики.**
* `app/a.log:10` → «filler line 10»

**Чем опровергал.** Проверял обратную версию: `app/a.log:30` → «filler line 30» —
не подтвердилась.

**Что делать сейчас.** Починить.

### Н-2 · Шум с соседней машины — успеха нет

**Что сломано.** Шумно.

**Улики.**
* `app/b.log:10` → «filler line 10»

**Чем опровергал.** Успеха нет: `app/b.log:40` → «filler line 40».

## 2. Отклонённые кандидаты

- **«Третье»** — нет. `app/b.log:20` «filler line 20» — это фон.

## 3. Покрытие

| путь | что искал | вердикт |
|---|---|---|
| app/c.log | всё | ничего относящегося |

## ВЕРДИКТ

Сбор улик шёл через `app/c.log:5` «filler line 5».

compromised
"""


def herring(cid, locs):
    """A decoy, in the shape `score_bench.is_herring` recognises."""
    return {"id": cid, "title": "RED HERRING: %s" % cid,
            "root_cause": "NOT A CAUSE", "proof_locations": locs}


class TestDecoyDispositionSplit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50),
                                "app/c.log": numbered(50)})

    def score(self, key, report=FIELDED_REPORT):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, report, self.tmp, call=None)
        return rec, buf.getvalue()

    def row(self, rec, cid):
        return [r for r in rec["per_defect"] if r["defect"] == cid][0]

    # --- the two halves --------------------------------------------------
    def test_a_decoy_under_ulики_is_asserted_as_an_incident(self):
        """`app/b.log:10` sits under «Улики» of Н-2 — the evidence-FOR field.
        That is the OpenSec failure: a benign thing entered in the findings
        register as evidence for a numbered finding."""
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_presented"], 1)
        self.assertEqual(rec["decoys_asserted_as_incident"], 1)
        self.assertEqual(rec["decoys_presented_refuted"], 0)
        self.assertEqual(self.row(rec, "D01")["disposition"], "asserted")

    def test_a_decoy_under_chem_oproverghal_is_presented_with_its_refutation(self):
        """`app/b.log:40` sits under «Чем опровергал» of Н-2 — the mandated
        refutation field. The report put the red herring in front of the reader
        and said what killed it. Not a false positive."""
        key = key_with([herring("D01", [loc("app/b.log", 40)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_presented"], 1)
        self.assertEqual(rec["decoys_asserted_as_incident"], 0)
        self.assertEqual(rec["decoys_presented_refuted"], 1)
        self.assertEqual(self.row(rec, "D01")["disposition"], "refuted")

    def test_the_two_halves_partition_decoys_presented_and_never_replace_it(self):
        key = key_with([herring("D01", [loc("app/b.log", 10)]),
                        herring("D02", [loc("app/b.log", 40)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_presented"], 2)
        self.assertEqual(rec["decoys_asserted_as_incident"]
                         + rec["decoys_presented_refuted"],
                         rec["decoys_presented"],
                         "the split is a partition of the existing column, and "
                         "the existing column stays")

    def test_evidence_wins_when_a_decoy_is_cited_in_both_fields(self):
        """A decoy whose proof is under «Улики» AND under «Чем опровергал» was
        still used as evidence for a finding. The stricter reading is the honest
        one: a false positive that also gets argued about is a false positive."""
        key = key_with([herring("D01", [loc("app/b.log", 10),
                                        loc("app/b.log", 40)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_asserted_as_incident"], 1)
        self.assertEqual(rec["decoys_presented_refuted"], 0)

    # --- the third outcome: anchored, claimed nowhere ---------------------
    def test_a_decoy_anchored_only_in_the_verdict_is_neither_half(self):
        """The negative control's D03 shape: the report anchors the decoy in its
        verdict and names it correctly, and never files it as a finding. That is
        the GOOD outcome and it must be visible as its own number, not hidden in
        the gap between `decoys_anchored` and `decoys_presented`."""
        key = key_with([herring("D01", [loc("app/c.log", 5)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_anchored"], 1)
        self.assertEqual(rec["decoys_presented"], 0)
        self.assertEqual(rec["decoys_asserted_as_incident"], 0)
        self.assertEqual(rec["decoys_presented_refuted"], 0)
        self.assertEqual(rec["decoys_anchored_elsewhere"], 1)
        self.assertEqual(self.row(rec, "D01")["disposition"], "elsewhere")

    def test_a_decoy_only_in_the_rejected_section_is_dismissed_not_split(self):
        """«Отклонённые кандидаты» already has its own column. The split is OF
        `decoys_presented`, so a decoy that never entered the findings section
        does not appear in either half."""
        key = key_with([herring("D01", [loc("app/b.log", 20)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_presented"], 0)
        self.assertEqual(rec["decoys_dismissed"], 1)
        self.assertEqual(rec["decoys_asserted_as_incident"], 0)
        self.assertEqual(rec["decoys_presented_refuted"], 0)
        self.assertEqual(self.row(rec, "D01")["disposition"], "dismissed")

    def test_a_decoy_nobody_cited_has_no_disposition(self):
        key = key_with([herring("D01", [loc("app/a.log", 44)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_anchored"], 0)
        self.assertIsNone(self.row(rec, "D01")["disposition"])
        self.assertEqual(rec["decoys_anchored_elsewhere"], 0)

    # --- the same reading, free, for real defects -------------------------
    def test_real_defects_get_the_same_disposition_for_free(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)]),
                        defect("D02", "real", [loc("app/a.log", 30)])])
        rec, _ = self.score(key)
        self.assertEqual(self.row(rec, "D01")["disposition"], "asserted")
        self.assertEqual(self.row(rec, "D02")["disposition"], "refuted",
                         "a REAL defect whose only citation sits in the "
                         "refutation field was argued away, and that is worth "
                         "seeing too")
        self.assertEqual(rec["anchored"], 2, "the split changes no denominator")
        self.assertEqual(rec["presented"], 2)

    # --- fail loud --------------------------------------------------------
    def test_a_report_with_no_mandated_field_labels_scores_none_not_zero(self):
        """FINDINGS_REPORT has a findings section and no «Улики»/«Чем опровергал»
        labels anywhere. Reporting 0 refutations would be a claim about the
        report; the truth is that the question cannot be read off it."""
        key = key_with([herring("D01", [loc("app/a.log", 10)])])
        rec, out = self.score(key, FINDINGS_REPORT)
        self.assertEqual(rec["decoys_presented"], 1,
                         "the coarser column is still readable")
        self.assertIsNone(rec["decoys_asserted_as_incident"])
        self.assertIsNone(rec["decoys_presented_refuted"])
        self.assertIsNone(rec["structure"]["fields_parsed"] or None)
        self.assertFalse(rec["structure"]["fields_parsed"])
        self.assertIn("чем опровергал", rec["structure"]["why_fields"].lower())
        self.assertIn("NOT MEASURED", out)

    def test_the_refusal_names_the_field_it_looked_for(self):
        key = key_with([herring("D01", [loc("app/a.log", 10)])])
        rec, _ = self.score(key, FINDINGS_REPORT)
        why = rec["structure"]["why_fields"]
        self.assertIn("«чем опровергал»", why.lower())
        self.assertIn("None, NOT 0", why,
                      "the refusal has to say out loud that this is an unread "
                      "question and not a measured zero")

    def test_no_findings_section_leaves_the_split_none_as_well(self):
        key = key_with([herring("D01", [loc("app/a.log", 10)])])
        rec, _ = self.score(key, "app/a.log:10 «filler line 10»")
        self.assertIsNone(rec["decoys_presented"])
        self.assertIsNone(rec["decoys_asserted_as_incident"])
        self.assertIsNone(rec["decoys_presented_refuted"])

    # --- the parse itself -------------------------------------------------
    def test_a_field_label_does_not_leak_past_its_own_heading(self):
        """The bug this parse had to be corrected out of: «Что делать сейчас» is
        the last label of Н-1, and without scoping it claimed every citation in
        every later section of the report — including the verdict."""
        key = key_with([herring("D01", [loc("app/c.log", 5)])])
        rec, _ = self.score(key)
        self.assertEqual(self.row(rec, "D01")["disposition"], "elsewhere",
                         "app/c.log:5 is in the VERDICT, four headings after "
                         "the last field label of Н-1")

    def test_bare_labels_count_as_well_as_bold_ones(self):
        """v19's BlueSky report writes `Улики:` and `Чем опровергал: …` with no
        bold. Same mandated field, same parse."""
        rep = FIELDED_REPORT.replace("**Улики.**", "Улики:").replace(
            "**Чем опровергал.**", "Чем опровергал:")
        key = key_with([herring("D01", [loc("app/b.log", 40)])])
        rec, _ = self.score(key, rep)
        self.assertTrue(rec["structure"]["fields_parsed"])
        self.assertEqual(rec["decoys_presented_refuted"], 1)

    def test_a_fenced_field_label_is_sample_text_not_structure(self):
        rep = FIELDED_REPORT.replace(
            "## 1. Находки",
            "```\n**Улики.** app/b.log:40\n```\n\n## 1. Находки")
        key = key_with([herring("D01", [loc("app/b.log", 40)])])
        rec, _ = self.score(key, rep)
        self.assertEqual(rec["decoys_presented_refuted"], 1)

    def test_the_split_is_printed_beside_the_decoy_line_not_instead_of_it(self):
        key = key_with([herring("D01", [loc("app/b.log", 10)]),
                        herring("D02", [loc("app/b.log", 40)])])
        _rec, out = self.score(key)
        self.assertIn("decoys    :", out)
        self.assertIn("asserted as incident", out)
        self.assertIn("with refutation", out)

    def test_the_split_costs_nothing(self):
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        saved = (S.score_case.http_call, S.score_bench.score)
        S.score_case.http_call = Tripwire("score_case.http_call")
        S.score_bench.score = Tripwire("score_bench.score")
        try:
            with redirect_stdout(io.StringIO()):
                rec = S.score(key, FIELDED_REPORT, self.tmp, call=None)
        finally:
            S.score_case.http_call, S.score_bench.score = saved
        self.assertEqual(rec["decoys_asserted_as_incident"], 1)


# --------------------------------------------------------------------------
# K. the uncited-disposal numbers are headlines, not fields you go looking for
# --------------------------------------------------------------------------
# `rejections_uncited` is rising as the skill closes more rows by rule — AIT v16
# 0 of 7, v19 2 of 11, v22 6 of 10 — and the v22 Linux arm's sharpest single loss
# happened in exactly that gap: a whole exfiltration disposed of in an UNCITED
# coverage row while the tool's own receipts had quoted the labelled proof. A
# number that behaves like that belongs in the summary, next to `anchored`.
class TestUncitedDisposalsAreFirstClass(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50),
                                "app/c.log": numbered(50)})

    def score(self, report=FIELDED_REPORT, key=None):
        key = key or key_with([defect("D01", "x", [loc("app/a.log", 10)])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, report, self.tmp, call=None)
        return rec, buf.getvalue()

    def test_they_are_top_level_record_fields(self):
        rec, _ = self.score()
        self.assertEqual(rec["rejections"], 1)
        self.assertEqual(rec["rejections_uncited"], 0)
        self.assertEqual(rec["coverage_rows"], 1)
        self.assertEqual(rec["coverage_rows_uncited"], 1)

    def test_they_agree_with_the_structure_block_they_were_hiding_in(self):
        rec, _ = self.score()
        for k in ("rejections", "rejections_uncited", "coverage_rows",
                  "coverage_rows_uncited"):
            self.assertEqual(rec[k], rec["structure"][k],
                             "%s must not be able to disagree with itself" % k)

    def test_the_summary_prints_them_on_their_own_headline_line(self):
        _rec, out = self.score()
        line = [l for l in out.splitlines() if l.startswith("uncited")]
        self.assertTrue(line, "expected an `uncited   :` headline line, got:\n%s"
                        % out)
        self.assertIn("rejection", line[0])
        self.assertIn("coverage", line[0])

    def test_the_percentages_are_printed_because_the_ratio_is_the_signal(self):
        _rec, out = self.score()
        line = [l for l in out.splitlines() if l.startswith("uncited")][0]
        self.assertIn("%", line)

    def test_a_report_with_no_rejected_section_reports_none_not_zero(self):
        rep = FIELDED_REPORT.replace("## 2. Отклонённые кандидаты",
                                     "## 2. Прочее")
        rec, out = self.score(rep)
        self.assertIsNone(rec["rejections"])
        self.assertIsNone(rec["rejections_uncited"])
        self.assertIn("uncited", out)
        self.assertIn("NOT MEASURED", out)

    def test_a_report_with_no_coverage_section_reports_none_not_zero(self):
        rep = FIELDED_REPORT.replace("## 3. Покрытие", "## 3. Прочее")
        rec, _ = self.score(rep)
        self.assertIsNone(rec["coverage_rows"])
        self.assertIsNone(rec["coverage_rows_uncited"])

    def test_the_two_counts_are_never_summed(self):
        """Same rule as every other axis here: they are printed beside each
        other. A single «uncited disposals» number would hide that one of them
        is a rejected candidate and the other is a whole file."""
        _rec, out = self.score()
        line = [l for l in out.splitlines() if l.startswith("uncited")][0]
        self.assertNotIn("total", line.lower())


# --------------------------------------------------------------------------
# L. the split, on every report this project has on disk
# --------------------------------------------------------------------------
class TestTheSplitOnTheRealReports(unittest.TestCase):
    """Gitignored trajectories: skip where absent, run where the evidence is."""

    BS22 = "20260818T212500Z-v22-claude-bluesky"

    @unittest.skipUnless(_run_report(BS22) and os.path.isdir(BLUESKY_CORPUS),
                         "BlueSky v22 trajectory or corpus not on this machine")
    def test_bluesky_v22_presents_both_decoys_as_evidence_for_a_finding(self):
        """The claim this job existed to test: the arm's reading is that D12 and
        D13 carry «успеха нет» and are therefore refutations. The report's own
        structure says otherwise — both are cited under «Улики» of Н-12, the
        evidence-FOR field. The heading carries the negative; the mandated field
        that would have recorded it does not."""
        key = json.load(open(os.path.join(BENCH, "answer-key-bluesky.json"),
                             encoding="utf-8"))
        rep = open(_run_report(self.BS22), encoding="utf-8").read()
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, BLUESKY_CORPUS, call=None)
        self.assertTrue(rec["structure"]["fields_parsed"])
        self.assertEqual(rec["decoys_presented"], 2)
        self.assertEqual(rec["decoys_asserted_as_incident"], 2)
        self.assertEqual(rec["decoys_presented_refuted"], 0)
        for cid in ("D12", "D13"):
            row = [r for r in rec["per_defect"] if r["defect"] == cid][0]
            self.assertEqual(row["disposition"], "asserted")


# --------------------------------------------------------------------------
# M. DELIVERY INTEGRITY IS AN AXIS, NOT A PRINTED WARNING
# --------------------------------------------------------------------------
# The v22 negative control is the first arm whose citation integrity fell below
# 100 %, and the cause was not the investigation. Scored per channel:
# `work/report.md` alone is 110 / 110 ok; the final message alone is 74 / 95
# with 21 `wrong-content`, all inside a condensed inventory the run hand-wrote
# AFTER checking the draft. `CHANNELS DIVERGE` fired — 34 shared blocks out of
# 71 and 151 — and printed a warning nobody could put in a table.
#
# The composed number (198 citations, 89.4 %) is an average that describes
# NEITHER document. So the channels are scored separately as well as together,
# and the divergence itself becomes a record field: how many blocks are shared,
# how many are unique to each side, and whether each side's citations verify.
#
# WHAT DOES NOT WORK, measured before this was written. «The delivered citations
# must be a subset of the verified ones» catches 1 of the 21 failures, because 20
# of them were citations already in the verified set, RE-TYPED under a new
# sentence. The mechanism that works is re-checking the delivered text against
# the corpus — the same check, the same corpus, the same citecheck the scorer
# already loads and receipts. Both nets are `citecheck`'s (`check` and
# `not_in_checked`); this file grades, it does not re-decide.
#
# THE COMPOSED NUMBER STAYS. Axes are never summed and never silently replaced:
# a ledger row written before today must still mean what it said.
# The draft the run checked: two findings, and every citation reads correctly.
CHECKED_DRAFT = """# Отчёт

## 0. Короткий ответ

Что-то произошло. app/a.log:5 «filler line 5»

## 1. Находки

### Н-1 · Первое

Вот доказательство: app/a.log:10 «filler line 10»

### Н-2 · Второе

И ещё одно: app/b.log:30 «filler line 30»

## 2. Отклонённые кандидаты

- **«Третье»** — нет. app/a.log:20 «filler line 20» — это фон.

## ВЕРДИКТ

compromised
"""

# The measured shape: a condensed inventory written AFTER the check. Both of its
# citations are re-typed — same file, same line, already in the draft's verified
# set — under quotes the line does not support. And the whole of
# Н-2 is gone, which the composed number cannot see because the draft still
# carries it.
HANDOVER_RETYPED = """# Итог

Кратко — что нашли, одной таблицей.

## 1. Находки

### Н-1 · Первое

Сводка: app/a.log:10 «unexpected privilege escalation detected»

Ещё строка: app/a.log:20 «outbound connection to unknown host»

## ВЕРДИКТ

compromised
"""

# A hand-over whose every citation reads correctly — and one of them was never
# part of what the run actually checked.
HANDOVER_UNCHECKED = """# Итог

## 1. Находки

### Н-1 · Первое

Вот доказательство: app/a.log:10 «filler line 10»

Ещё одно, впервые: app/b.log:31 «filler line 31»

## ВЕРДИКТ

compromised
"""


class TestDeliveryIntegrityIsScored(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})
        self.key = key_with([defect("D01", "x", [loc("app/a.log", 10)]),
                             defect("D02", "y", [loc("app/b.log", 30)])])

    def score(self, answer, artifact):
        text = S.deliverable.compose(answer, artifact)
        with redirect_stdout(io.StringIO()):
            return S.score(self.key, text, self.tmp, call=None,
                           answer=answer, artifact=artifact)

    def out_of(self, answer, artifact):
        text = S.deliverable.compose(answer, artifact)
        buf = io.StringIO()
        with redirect_stdout(buf):
            S.score(self.key, text, self.tmp, call=None,
                    answer=answer, artifact=artifact)
        return buf.getvalue()

    # -- the two channels are scored separately --------------------------
    def test_each_channel_gets_its_own_citation_score(self):
        """THE measurement. 110/110 and 74/95 are two facts about two documents,
        and the arm published one number that is neither."""
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        ch = rec["delivery"]["channels"]
        self.assertEqual(ch["file"]["citations"]["ok"],
                         ch["file"]["citations"]["total"],
                         "the draft verifies whole — that is the investigation")
        self.assertGreater(ch["message"]["citations"]["wrong-content"], 0,
                           "the hand-over does not — that is the delivery")
        self.assertTrue(ch["file"]["verified"])
        self.assertFalse(ch["message"]["verified"])

    def test_the_investigation_and_the_handover_get_separate_findings_columns(self):
        """«The investigation was sound, the hand-over was not» has to be a
        statement the NUMBERS can make, not only the prose around them."""
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        ch = rec["delivery"]["channels"]
        self.assertEqual(ch["file"]["anchored"], 2)
        self.assertEqual(ch["message"]["anchored"], 1,
                         "the condensed hand-over dropped Н-2 entirely — and the "
                         "composed number cannot see it, because the draft still "
                         "carries the proof")
        self.assertEqual(ch["file"]["anchorable"], ch["message"]["anchorable"],
                         "same key, same denominator — only the document differs")

    def test_the_composed_record_is_untouched_by_the_new_axis(self):
        """Axes are never summed and never silently replaced. A number that moves
        must be explainable to somebody reading an old ledger row, so the
        pre-existing fields must be bit-for-bit what they were."""
        text = S.deliverable.compose(HANDOVER_RETYPED, CHECKED_DRAFT)
        with redirect_stdout(io.StringIO()):
            plain = S.score(self.key, text, self.tmp, call=None)
        with_axis = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        for k in plain:
            if k == "delivery":
                continue
            self.assertEqual(with_axis[k], plain[k], k)
        self.assertIsNone(plain["delivery"]["channel"],
                          "no channels were handed in, so the axis says so")

    def test_the_composed_citation_total_is_still_the_union(self):
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        ch = rec["delivery"]["channels"]
        self.assertGreater(rec["citecheck"]["total"], ch["file"]["citations"]["total"])
        self.assertGreater(rec["citecheck"]["total"],
                           ch["message"]["citations"]["total"])

    # -- CHANNELS DIVERGE is a scored fact -------------------------------
    def test_divergence_is_a_record_field_with_the_block_arithmetic(self):
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertTrue(d["diverged"])
        self.assertEqual(d["relation"], "divergent")
        b = d["blocks"]
        for k in ("message", "file", "shared", "only_in_message", "only_in_file"):
            self.assertIsInstance(b[k], int, k)
        self.assertGreater(b["only_in_message"], 0)
        self.assertGreater(b["only_in_file"], 0)

    def test_the_block_arithmetic_is_deliverables_and_not_a_second_copy(self):
        """`duplication()` already computes this relation. A second copy in the
        scorer is a second scale."""
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        dup = S.deliverable.duplication(HANDOVER_RETYPED, CHECKED_DRAFT)
        b = rec["delivery"]["blocks"]
        self.assertEqual(b["shared"], dup["shared_blocks"])
        self.assertEqual(b["only_in_message"], dup["only_in_message"])
        self.assertEqual(b["only_in_file"], dup["only_in_file"])
        self.assertEqual(b["message"], dup["message_blocks"])
        self.assertEqual(b["file"], dup["file_blocks"])

    def test_whether_the_divergent_side_verifies_is_recorded(self):
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertEqual(d["divergent_sides"], ["message", "file"])
        self.assertFalse(d["divergent_side_verifies"],
                         "one of the two divergent documents does not verify")

    def test_two_divergent_channels_that_both_verify_say_so(self):
        """Divergence is not itself a citation defect: two documents can disagree
        in content and both be honest about the corpus."""
        clean = CHECKED_DRAFT.replace("app/a.log:5 «filler line 5»",
                                      "app/b.log:31 «filler line 31»")
        rec = self.score(clean, CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertTrue(d["diverged"])
        self.assertTrue(d["divergent_side_verifies"])

    def test_channels_that_agree_are_not_divergent_and_the_question_is_none(self):
        rec = self.score(CHECKED_DRAFT, CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertFalse(d["diverged"])
        self.assertEqual(d["relation"], "identical")
        self.assertEqual(d["divergent_sides"], [])
        self.assertIsNone(d["divergent_side_verifies"],
                          "nothing diverged, so the question was not asked — "
                          "None, never True")

    def test_a_file_that_only_re_wraps_the_message_is_not_divergence(self):
        wrapped = TestOneReportOnTwoChannelsIsCountedOnce.hard_wrap(CHECKED_DRAFT)
        rec = self.score(CHECKED_DRAFT, wrapped)
        self.assertFalse(rec["delivery"]["diverged"])
        self.assertIn(rec["delivery"]["relation"],
                      ("identical", "file-repeats-message"))

    # -- the hand-over gate is citecheck's own ---------------------------
    def test_a_retyped_citation_already_in_the_verified_set_still_fails(self):
        """The measured lesson, encoded so nobody rebuilds subset arithmetic:
        20 of the 21 failures were citations ALREADY in the verified set, re-typed
        under a new sentence. The subset net sees nothing; re-checking the
        delivered text against the corpus sees all of them."""
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertEqual(d["handover_not_in_checked"], 0,
                         "every re-typed citation IS in the verified set — the "
                         "subset test is blind to exactly this shape")
        self.assertEqual(d["channels"]["message"]["citations"]["wrong-content"], 2)
        self.assertTrue(d["handover_failed"])

    def test_a_delivered_citation_the_draft_never_checked_is_named(self):
        """The other net, and the 1 of 21 it catches: a line that exists and reads
        right was still never part of what the run checked."""
        rec = self.score(HANDOVER_UNCHECKED, CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertTrue(d["channels"]["message"]["verified"],
                        "every delivered citation reads correctly")
        self.assertEqual(d["handover_not_in_checked"], 1)
        self.assertIn("app/b.log:31",
                      d["handover_not_in_checked_examples"][0]["citation"])
        self.assertTrue(d["handover_failed"])

    def test_the_pass_fail_predicate_is_citechecks_and_not_a_second_copy(self):
        """`citecheck.delivery_failed` is what the skill exits non-zero on. The
        scorer must agree with the tool by construction, not by coincidence."""
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        d = rec["delivery"]
        dd = {"summary": d["channels"]["message"]["citations"],
              "not_in_checked": [1] * d["handover_not_in_checked"]}
        self.assertEqual(d["handover_failed"], S.citecheck.delivery_failed(dd))

    def test_a_clean_handover_does_not_fail(self):
        rec = self.score(CHECKED_DRAFT, CHECKED_DRAFT)
        self.assertFalse(rec["delivery"]["handover_failed"])
        self.assertTrue(rec["delivery"]["channels"]["message"]["verified"])

    def test_the_handover_is_the_message_and_the_checked_artefact_is_the_file(self):
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        self.assertEqual(rec["delivery"]["handover"], "message")
        self.assertEqual(rec["delivery"]["checked"], "file")

    # -- one channel, no channel, and the receipt ------------------------
    def test_a_message_only_run_has_one_channel_and_nothing_to_diverge_from(self):
        rec = self.score(CHECKED_DRAFT, "")
        d = rec["delivery"]
        self.assertEqual(d["channel"], "message")
        self.assertEqual(sorted(d["channels"]), ["message"])
        self.assertFalse(d["diverged"])
        self.assertIsNone(d["handover_not_in_checked"],
                          "there is no second document to have checked — None, "
                          "never 0")
        self.assertEqual(d["checked"], None)

    def test_a_file_only_run_names_the_file_as_the_handover(self):
        rec = self.score("", CHECKED_DRAFT)
        d = rec["delivery"]
        self.assertEqual(d["channel"], "file")
        self.assertEqual(d["handover"], "file")

    def test_scoring_a_bare_document_says_the_axis_was_not_measurable(self):
        with redirect_stdout(io.StringIO()):
            rec = S.score(self.key, CHECKED_DRAFT, self.tmp, call=None)
        d = rec["delivery"]
        self.assertFalse(d["measured"])
        self.assertIsNone(d["diverged"])
        self.assertIsNone(d["handover_failed"])
        self.assertTrue(d["why"])

    def test_the_axis_carries_the_same_citecheck_receipt_as_the_record(self):
        rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        self.assertEqual(rec["delivery"]["citecheck_version"],
                         rec["citecheck_version"])
        self.assertEqual(rec["delivery"]["citecheck_sha"], rec["citecheck_sha"])

    def test_the_axis_sends_nothing_anywhere(self):
        saved = (S.score_case.http_call, S.score_bench.score)
        S.score_case.http_call = Tripwire("score_case.http_call")
        S.score_bench.score = Tripwire("score_bench.score")
        try:
            rec = self.score(HANDOVER_RETYPED, CHECKED_DRAFT)
        finally:
            S.score_case.http_call, S.score_bench.score = saved
        self.assertTrue(rec["delivery"]["measured"])

    def test_a_report_that_is_not_the_union_of_the_channels_RAISES(self):
        """Fail loud. A delivery block describing two channels beside a composed
        score taken from some third document is a record that lies quietly."""
        with self.assertRaises(RuntimeError) as cm:
            with redirect_stdout(io.StringIO()):
                S.score(self.key, CHECKED_DRAFT, self.tmp, call=None,
                        answer=HANDOVER_RETYPED, artifact=CHECKED_DRAFT)
        self.assertIn("union", str(cm.exception).lower())

    # -- it is printed too, beside being scored --------------------------
    def test_the_summary_prints_a_delivery_headline_line(self):
        out = self.out_of(HANDOVER_RETYPED, CHECKED_DRAFT)
        line = [l for l in out.splitlines() if l.startswith("delivery")]
        self.assertTrue(line, "expected a `delivery  :` headline line, got:\n%s"
                        % out)
        self.assertIn("CHANNELS DIVERGE", out)

    def test_the_headline_carries_both_channels_verified_rates(self):
        out = self.out_of(HANDOVER_RETYPED, CHECKED_DRAFT)
        block = [l for l in out.splitlines()
                 if l.startswith("delivery") or l.startswith("          ")]
        joined = "\n".join(block)
        self.assertIn("100.0 %", joined)
        self.assertRegex(joined, r"hand-over.*\d+ / \d+")

    def test_a_single_channel_run_prints_no_divergence(self):
        out = self.out_of(CHECKED_DRAFT, "")
        self.assertNotIn("CHANNELS DIVERGE", out)
        self.assertTrue([l for l in out.splitlines() if l.startswith("delivery")])


class TestTheDeliveryAxisThroughTheCLI(unittest.TestCase):
    """Both doors into the axis: a run ledger, which carries the two channels
    already, and `--report` + `--delivered`, which is the same two roles
    `citecheck.py --delivered` names."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50)})
        self.dir = tempfile.mkdtemp()
        self.keyfile = os.path.join(self.dir, "key.json")
        with open(self.keyfile, "w", encoding="utf-8") as fh:
            json.dump(key_with([defect("D01", "x", [loc("app/a.log", 10)]),
                                defect("D02", "y", [loc("app/b.log", 30)])],
                               dataset="unit"), fh)
        self.out = os.path.join(self.dir, "scores.jsonl")

    def _run(self, argv):
        saved = sys.argv
        sys.argv = ["score-report.py"] + argv
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                S.main()
        finally:
            sys.argv = saved
        rec = [json.loads(l) for l in open(self.out, encoding="utf-8")
               if l.strip()][-1]
        return rec, buf.getvalue()

    def _file(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_a_ledger_row_fills_the_axis_from_its_two_channels(self):
        ledger = self._file("runs.jsonl", json.dumps(
            {"dataset": "unit", "arm": "vX", "trace_dir": "/tmp/t1",
             "answer": HANDOVER_RETYPED, "artifact": CHECKED_DRAFT},
            ensure_ascii=False) + "\n")
        rec, out = self._run(["--key", self.keyfile, "--corpus", self.tmp,
                              "--ledger", ledger, "--dataset", "unit",
                              "--out", self.out])
        d = rec["delivery"]
        self.assertTrue(d["measured"])
        self.assertTrue(d["diverged"])
        self.assertTrue(d["handover_failed"])
        self.assertEqual(d["channels"]["file"]["citations"]["ok"], 4)
        self.assertIn("CHANNELS DIVERGE", out)
        self.assertIn("delivery  :", out)

    def test_the_record_stays_json_serialisable_with_the_axis_on_it(self):
        ledger = self._file("runs.jsonl", json.dumps(
            {"dataset": "unit", "arm": "vX", "trace_dir": "/tmp/t1",
             "answer": HANDOVER_RETYPED, "artifact": CHECKED_DRAFT},
            ensure_ascii=False) + "\n")
        rec, _ = self._run(["--key", self.keyfile, "--corpus", self.tmp,
                            "--ledger", ledger, "--dataset", "unit",
                            "--out", self.out])
        json.dumps(rec, ensure_ascii=False)
        self.assertEqual(rec["delivery"]["citecheck_version"],
                         rec["citecheck_version"])

    def test_report_plus_delivered_scores_the_union_and_both_channels(self):
        draft = self._file("report.md", CHECKED_DRAFT)
        hand = self._file("handover.md", HANDOVER_RETYPED)
        rec, out = self._run(["--key", self.keyfile, "--corpus", self.tmp,
                              "--report", draft, "--delivered", hand,
                              "--out", self.out])
        d = rec["delivery"]
        self.assertTrue(d["measured"])
        self.assertEqual(d["handover"], "message")
        self.assertEqual(d["checked"], "file")
        self.assertTrue(d["handover_failed"])
        self.assertEqual(rec["report_chars"],
                         len(S.deliverable.compose(HANDOVER_RETYPED,
                                                   CHECKED_DRAFT)),
                         "what is scored stays the UNION of the two channels")
        self.assertEqual(rec["delivered_in"], "both")

    def test_report_alone_says_the_axis_was_not_measurable(self):
        draft = self._file("report.md", CHECKED_DRAFT)
        rec, out = self._run(["--key", self.keyfile, "--corpus", self.tmp,
                              "--report", draft, "--out", self.out])
        self.assertFalse(rec["delivery"]["measured"])
        self.assertIn("NOT MEASURED", out)
        self.assertIsNone(rec["duplication"])

    def test_delivered_without_report_is_refused(self):
        hand = self._file("handover.md", HANDOVER_RETYPED)
        saved = sys.argv
        sys.argv = ["score-report.py", "--key", self.keyfile, "--corpus",
                    self.tmp, "--delivered", hand, "--out", self.out]
        try:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    S.main()
        finally:
            sys.argv = saved


# --------------------------------------------------------------------------
# N. the delivery axis on the run that forced it
# --------------------------------------------------------------------------
NEG_KEY = os.path.join(BENCH, "answer-key-fleet-negative.json")
FLEET_CORPUS = os.path.join(
    os.path.expanduser("~"), "Documents", "projects", "personal-os", "projects",
    "active", "attachments", "sherlock-cyber-fleet", "corpus")
LEDGER = os.path.join(BENCH, "runs-bench.jsonl")


def _ledger_row(trace):
    if not os.path.isfile(LEDGER):
        return None
    for line in open(LEDGER, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if trace in (r.get("trace_dir") or ""):
            return r
    return None


class TestDeliveryOnTheNegativeControl(unittest.TestCase):
    """The ledger is committed; the 225 MB corpus is not. Skip where it is
    absent, run where the evidence is."""

    TRACE = "20260818T212438Z-v22-claude-fleetneg"

    @unittest.skipUnless(_ledger_row(TRACE) and os.path.isdir(FLEET_CORPUS),
                         "fleet-negative run row or corpus not on this machine")
    def test_the_arm_that_shipped_21_bad_citations_splits_110_110_and_74_95(self):
        row = _ledger_row(self.TRACE)
        key = json.load(open(NEG_KEY, encoding="utf-8"))
        parts = S.deliverable.channels_of_row(row)
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, S.deliverable.of_row(row), FLEET_CORPUS, call=None,
                          answer=parts.get("message"), artifact=parts.get("file"))
        d = rec["delivery"]
        self.assertTrue(d["diverged"])
        self.assertEqual(d["blocks"]["shared"], 34)
        self.assertEqual(d["blocks"]["message"], 71)
        self.assertEqual(d["blocks"]["file"], 151)
        f = d["channels"]["file"]["citations"]
        m = d["channels"]["message"]["citations"]
        self.assertEqual((f["ok"], f["total"]), (110, 110))
        self.assertEqual((m["ok"], m["total"]), (74, 95))
        self.assertEqual(m["wrong-content"], 21)
        self.assertEqual(d["handover_not_in_checked"], 1)
        self.assertTrue(d["handover_failed"])
        self.assertFalse(d["divergent_side_verifies"])
        # and the composed number the ledger already published is unmoved
        self.assertEqual(rec["citecheck"]["total"], 198)
        self.assertEqual(rec["citecheck"]["ok"], 177)

    AIT22 = "20260818T212406Z-v22-claude-ait"
    AIT_CORPUS = os.path.join(os.path.expanduser("~"), "hack", "sherlock-corpora",
                              "_blind", "incident-alpha")

    @unittest.skipUnless(_ledger_row(AIT22) and os.path.isdir(AIT_CORPUS),
                         "AIT v22 run row or corpus not on this machine")
    def test_ait_v22_is_the_arm_where_ONE_TYPO_doubled_a_33_citation_table(self):
        """FOUND BY THIS AXIS, 2026-08-19, and left unfixed on purpose.

        AIT v22 composes to 207 citations and BOTH channels score 174. The two
        channels are 105 blocks each and differ in exactly one: an 8,806-char
        timeline table that reads «То же скачивание» in the message and «Тот же
        скачивание» in the file — one word, 0.999943 similar. Block identity is
        whitespace-insensitive and nothing else, so the union kept both copies
        and counted that table's 33 citations twice.

        NOT FIXED HERE. The unit is the block precisely because the alternative
        is a similarity threshold, and `deliverable.py` refuses one by name: a
        threshold is a number nobody can defend at the edge. Changing the union
        would move every published composed total, which is a different job from
        this one. What this axis changes is that the residual is now VISIBLE —
        174 beside 207 — instead of hiding inside a 100 %-verified headline."""
        row = _ledger_row(self.AIT22)
        key = json.load(open(os.path.join(BENCH,
                                          "answer-key-ait-russellmitchell.json"),
                             encoding="utf-8"))
        parts = S.deliverable.channels_of_row(row)
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, S.deliverable.of_row(row), self.AIT_CORPUS,
                          call=None, answer=parts.get("message"),
                          artifact=parts.get("file"))
        d = rec["delivery"]
        self.assertEqual(d["relation"], "divergent")
        self.assertEqual((d["blocks"]["only_in_message"],
                          d["blocks"]["only_in_file"]), (1, 1))
        self.assertEqual(d["blocks"]["message"], d["blocks"]["file"])
        for name in ("message", "file"):
            c = d["channels"][name]["citations"]
            self.assertEqual((c["ok"], c["total"]), (174, 174))
        self.assertTrue(d["divergent_side_verifies"],
                        "both documents are honest about the corpus — the "
                        "divergence is a typo, not a citation defect")
        self.assertFalse(d["handover_failed"])
        self.assertEqual(rec["citecheck"]["total"], 207,
                         "the composed total is 33 higher than either channel: "
                         "the near-identical table counted twice")


# --------------------------------------------------------------------------
# N. THE OUTCOME AXIS — the field that turns a citation into a false positive
# --------------------------------------------------------------------------
# `presented` says a decoy's proof was written inside «Находки». The «Улики» /
# «Чем опровергал» split above says which mandated FIELD it sat under. Neither
# can say what the report CONCLUDED about the thing, because until v24 no field
# carried that: a block reading «I checked this and it was nothing» is written
# in exactly the same fields as one reading «I found an intrusion», and the
# difference lived in prose. Measured across all nine arms on disk: 12 planted
# non-defects presented, 0 refuted — every one of them under «Улики», because
# «Улики» was the only field that could hold them.
#
# `skills/v24` makes every finding carry a closed-vocabulary outcome:
#
#     исход: успех     — действие достигло цели
#     исход: попытка   — действие видно, и видно, что цели оно НЕ достигло
#     исход: норма     — проверено и объяснено штатным поведением
#
# So the join is one line of arithmetic this scorer could not do before:
#
#     a decoy cited inside a finding marked успех/попытка   = A FALSE POSITIVE
#     the same decoy cited inside a finding marked норма    = A REFUTATION
#     a REAL defect cited inside a finding marked норма     = a miss dressed
#                                                             as diligence
#
# THE PARSE IS NOT REIMPLEMENTED. `citecheck.finding_blocks`,
# `finding_outcomes` and `implied_verdict` own the grammar — including the rule
# that a trailing qualifier («успех, но не доказан») is refused SEPARATELY from
# a missing line, because those are different failures. This file joins their
# output to the anchoring arithmetic and grades; it does not re-decide.
#
# FAIL LOUD ON A REPORT THAT PREDATES THE FIELD. Every report on disk was
# written before v24 existed, so every finding block is `missing`. The axis has
# to read as «this question did not exist yet» and NEVER as «this report
# refuted nothing» — the second is a claim about the report, and it would
# silently rewrite eight historical scores into zeros.
OUTCOME_REPORT = """# Отчёт

## 1. Находки

### Н-1 · Настоящий дефект

**Улики.**
* `app/a.log:10` → «filler line 10»

**Исход:** успех

**Чем опровергал.** Обратная версия: `app/a.log:30` → «filler line 30».

### Н-2 · Шум с соседней машины

**Улики.**
* `app/b.log:10` → «filler line 10»

исход: норма

### Н-3 · Видно, но цели не достигло

**Улики.**
* `app/b.log:40` → «filler line 40»

исход: попытка

### Н-4 · Блок без строки исхода

**Улики.**
* `app/c.log:20` → «filler line 20»

## 2. Отклонённые кандидаты

- **«Третье»** — нет. `app/b.log:20` «filler line 20» — это фон.

## 3. Покрытие

| путь | что искал | вердикт |
|---|---|---|
| app/c.log | всё | ничего относящегося |

## ВЕРДИКТ

Сбор улик шёл через `app/c.log:5` «filler line 5».

compromised
"""


class TestTheOutcomeAxis(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50),
                                "app/c.log": numbered(50)})

    def score(self, key, report=OUTCOME_REPORT):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rec = S.score(key, report, self.tmp, call=None)
        return rec, buf.getvalue()

    def row(self, rec, cid):
        return [r for r in rec["per_defect"] if r["defect"] == cid][0]

    # --- the join, on decoys ---------------------------------------------
    def test_a_decoy_inside_a_uspeh_finding_is_a_false_positive(self):
        """`app/a.log:10` sits in Н-1, whose outcome is «успех». A planted
        non-defect written up as a thing that reached its goal is the failure
        the field benchmarks count."""
        key = key_with([herring("D01", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertTrue(o["measured"])
        self.assertEqual(o["decoys_false_positive"], 1)
        self.assertEqual(o["decoys_refutation"], 0)
        self.assertEqual(self.row(rec, "D01")["outcome"], "успех")
        self.assertEqual(self.row(rec, "D01")["outcome_zone"], "incident")

    def test_a_decoy_inside_a_popytka_finding_is_also_a_false_positive(self):
        """«попытка» is «visible, and shown to have failed» — still a claim that
        something happened. `норма` is the only outcome that is not."""
        key = key_with([herring("D01", [loc("app/b.log", 40)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["outcomes"]["decoys_false_positive"], 1)
        self.assertEqual(rec["outcomes"]["decoys_refutation"], 0)
        self.assertEqual(self.row(rec, "D01")["outcome"], "попытка")

    def test_a_decoy_inside_a_norma_finding_is_a_refutation(self):
        """THE POINT OF THE WHOLE AXIS. The same citation, in the same section,
        under the same «Улики» field — and the report says it is ordinary
        behaviour. That is an investigation, not a false positive."""
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(o["decoys_refutation"], 1)
        self.assertEqual(o["decoys_false_positive"], 0)
        self.assertEqual(self.row(rec, "D01")["outcome"], "норма")
        self.assertEqual(self.row(rec, "D01")["outcome_zone"], "normal")

    def test_the_field_split_and_the_outcome_split_disagree_and_both_stay(self):
        """`app/b.log:10` is under «Улики» of Н-2 — `decoys_asserted_as_incident`
        counts it, because that column reads the FIELD. Н-2's outcome is «норма»,
        so the outcome axis calls it a refutation. Both readings are true about
        different questions and neither may overwrite the other."""
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["decoys_presented"], 1)
        self.assertEqual(rec["decoys_asserted_as_incident"], 1)
        self.assertEqual(rec["decoys_presented_refuted"], 0)
        self.assertEqual(rec["outcomes"]["decoys_refutation"], 1)

    def test_a_decoy_only_in_the_rejected_section_is_dismissed(self):
        key = key_with([herring("D01", [loc("app/b.log", 20)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(o["decoys_dismissed"], 1)
        self.assertEqual(o["decoys_false_positive"], 0)
        self.assertEqual(o["decoys_refutation"], 0)
        self.assertEqual(self.row(rec, "D01")["outcome_zone"], "dismissed")

    def test_a_decoy_anchored_in_the_verdict_is_elsewhere(self):
        key = key_with([herring("D01", [loc("app/c.log", 5)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["outcomes"]["decoys_elsewhere"], 1)
        self.assertEqual(self.row(rec, "D01")["outcome_zone"], "elsewhere")

    def test_a_decoy_in_a_finding_that_states_no_outcome_is_unlabelled(self):
        """Н-4 writes no `исход:` line. The decoy is presented, and what the
        report concluded about it is UNREADABLE — not a refutation, and not
        provably a false positive either."""
        key = key_with([herring("D01", [loc("app/c.log", 20)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(o["decoys_unlabelled"], 1)
        self.assertEqual(o["decoys_false_positive"], 0)
        self.assertEqual(o["decoys_refutation"], 0)
        self.assertIsNone(self.row(rec, "D01")["outcome"])
        self.assertEqual(self.row(rec, "D01")["outcome_zone"], "unlabelled")

    def test_a_decoy_nobody_cited_is_never_anchored_and_never_a_bucket(self):
        key = key_with([herring("D01", [loc("app/a.log", 44)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(o["decoys_not_anchored"], 1)
        self.assertIsNone(self.row(rec, "D01")["outcome_zone"])

    def test_the_five_buckets_partition_every_decoy(self):
        """A split that does not add up to its denominator is a third number
        nobody can check. Every decoy in the key lands in exactly one bucket."""
        key = key_with([herring("D01", [loc("app/a.log", 10)]),
                        herring("D02", [loc("app/b.log", 10)]),
                        herring("D03", [loc("app/b.log", 20)]),
                        herring("D04", [loc("app/c.log", 5)]),
                        herring("D05", [loc("app/c.log", 20)]),
                        herring("D06", [loc("app/a.log", 44)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual((o["decoys_false_positive"], o["decoys_refutation"],
                          o["decoys_dismissed"], o["decoys_elsewhere"],
                          o["decoys_unlabelled"], o["decoys_not_anchored"]),
                         (1, 1, 1, 1, 1, 1))
        self.assertEqual(o["decoys_false_positive"] + o["decoys_refutation"]
                         + o["decoys_dismissed"] + o["decoys_elsewhere"]
                         + o["decoys_unlabelled"] + o["decoys_not_anchored"],
                         o["decoys"])
        self.assertEqual(o["decoys"], rec["decoys"],
                         "the same denominator as every other decoy column")

    def test_the_strongest_outcome_wins_when_one_decoy_is_cited_twice(self):
        """A decoy cited both under a «норма» finding and under a «успех» one
        was still entered as evidence for something that happened. The strict
        reading is the honest one — the other direction lets an unlabelled or
        refuted block launder a false positive."""
        key = key_with([herring("D01", [loc("app/b.log", 10),
                                        loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["outcomes"]["decoys_false_positive"], 1)
        self.assertEqual(rec["outcomes"]["decoys_refutation"], 0)
        self.assertEqual(self.row(rec, "D01")["outcome"], "успех")

    # --- the same join, on real defects -----------------------------------
    def test_a_real_defect_marked_norma_is_a_miss_dressed_as_diligence(self):
        """The report found the right line, filed it as a finding, and concluded
        it was ordinary behaviour. `anchored` and `presented` both score it, and
        neither can see that the reader was told nothing happened."""
        key = key_with([defect("D01", "real", [loc("app/b.log", 10)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(rec["anchored"], 1)
        self.assertEqual(rec["presented"], 1)
        self.assertEqual(o["real_marked_normal"], 1)
        self.assertEqual(o["real_asserted_as_incident"], 0)
        self.assertEqual(self.row(rec, "D01")["outcome_zone"], "normal")

    def test_a_real_defect_marked_uspeh_is_asserted_as_an_incident(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["outcomes"]["real_asserted_as_incident"], 1)
        self.assertEqual(rec["outcomes"]["real_marked_normal"], 0)

    def test_the_real_buckets_partition_the_anchorable_denominator(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)]),
                        defect("D02", "real", [loc("app/b.log", 10)]),
                        defect("D03", "real", [loc("app/b.log", 20)]),
                        defect("D04", "real", [loc("app/c.log", 5)]),
                        defect("D05", "real", [loc("app/c.log", 20)]),
                        defect("D06", "real", [loc("app/a.log", 44)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual((o["real_asserted_as_incident"], o["real_marked_normal"],
                          o["real_dismissed"], o["real_elsewhere"],
                          o["real_unlabelled"], o["real_not_anchored"]),
                         (1, 1, 1, 1, 1, 1))
        self.assertEqual(o["real_anchorable"], rec["anchorable"])
        self.assertEqual(sum((o["real_asserted_as_incident"],
                              o["real_marked_normal"], o["real_dismissed"],
                              o["real_elsewhere"], o["real_unlabelled"],
                              o["real_not_anchored"])), o["real_anchorable"])

    # --- the outcome-line health, as delivery facts ------------------------
    def test_the_health_block_counts_blocks_missing_and_invalid(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(o["finding_blocks"], 4)
        self.assertEqual(o["outcomes_stated"], 3)
        self.assertEqual(o["outcome_missing"], 1)
        self.assertEqual(o["outcome_missing_findings"], ["4"])
        self.assertEqual(o["outcome_invalid"], 0)

    def test_a_trailing_qualifier_is_invalid_not_missing(self):
        """v24 refuses «успех, но не доказан» SEPARATELY from a forgotten line:
        one is a forgotten field, the other is an argument with the vocabulary,
        and a scorer that folds them together cannot tell a skill which to fix."""
        rep = OUTCOME_REPORT.replace("**Исход:** успех",
                                     "**Исход:** успех, но не доказан")
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        rec, _ = self.score(key, rep)
        o = rec["outcomes"]
        self.assertEqual(o["outcome_invalid"], 1)
        self.assertEqual(o["outcome_missing"], 1, "Н-4 is still the only "
                                                  "block that forgot the line")
        self.assertEqual(o["outcome_invalid_findings"][0]["finding"], "1")
        self.assertIn("но не доказан", o["outcome_invalid_findings"][0]["text"])

    def test_the_implied_verdict_is_read_beside_the_stated_one(self):
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        o = rec["outcomes"]
        self.assertEqual(o["implied_verdict"], "compromised")
        self.assertEqual(o["stated_verdict"], "compromised")
        self.assertFalse(o["contradiction"])

    def test_a_register_that_does_not_add_up_to_the_verdict_is_a_contradiction(self):
        """Every finding says «норма» and the report ends «compromised». One of
        the two is wrong, and a delivery that contradicts itself is a defect
        exactly as a missing verdict section is."""
        rep = (OUTCOME_REPORT.replace("**Исход:** успех", "исход: норма")
                             .replace("исход: попытка", "исход: норма"))
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        rec, out = self.score(key, rep)
        o = rec["outcomes"]
        self.assertEqual(o["implied_verdict"], "clean")
        self.assertEqual(o["stated_verdict"], "compromised")
        self.assertTrue(o["contradiction"])
        self.assertIn("ПРОТИВОРЕЧИЕ", out)

    def test_the_health_block_is_read_even_when_no_defect_is_anchored(self):
        """The outcome-line count is a fact about the DOCUMENT. It does not need
        a key, a corpus hit or a single anchored defect to be true."""
        key = key_with([defect("D01", "real", [loc("app/a.log", 44)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["outcomes"]["finding_blocks"], 4)
        self.assertEqual(rec["outcomes"]["outcomes_stated"], 3)

    # --- FAIL LOUD on a report that predates the field ---------------------
    def test_a_pre_v24_report_scores_none_on_every_join_column_not_zero(self):
        """FIELDED_REPORT has Н-1 and Н-2, «Улики», «Чем опровергал» — and no
        `исход:` line anywhere, because it was written before the field existed.
        Zero refutations here would read as «this report refuted nothing»,
        which is a claim about the report instead of about the field."""
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, out = self.score(key, FIELDED_REPORT)
        o = rec["outcomes"]
        self.assertFalse(o["measured"])
        for k in ("decoys_false_positive", "decoys_refutation",
                  "decoys_dismissed", "decoys_elsewhere", "decoys_unlabelled",
                  "real_asserted_as_incident", "real_marked_normal"):
            self.assertIsNone(o[k], "%s must be None, not 0, on a pre-v24 "
                                    "report" % k)
        self.assertIsNone(self.row(rec, "D01")["outcome_zone"])
        self.assertIn("NOT MEASURED", out)

    def test_the_refusal_says_the_field_did_not_exist_yet(self):
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, _ = self.score(key, FIELDED_REPORT)
        why = rec["outcomes"]["why"]
        self.assertIn("исход", why.lower())
        self.assertIn("None, NOT 0", why)
        self.assertIn("v24", why)

    def test_the_health_block_still_reads_on_a_pre_v24_report(self):
        """«This axis did not exist yet» is itself measured, not assumed: the
        blocks are counted and every one of them is reported as missing."""
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, _ = self.score(key, FIELDED_REPORT)
        o = rec["outcomes"]
        self.assertEqual(o["finding_blocks"], 2)
        self.assertEqual(o["outcomes_stated"], 0)
        self.assertEqual(o["outcome_missing"], 2)
        self.assertIsNone(o["implied_verdict"])
        self.assertEqual(o["stated_verdict"], "compromised")
        self.assertIsNone(o["contradiction"],
                          "a verdict cannot contradict a register that states "
                          "nothing — that is None, not False")

    def test_a_report_with_no_finding_blocks_at_all_reads_none_everywhere(self):
        key = key_with([herring("D01", [loc("app/a.log", 10)])])
        rec, _ = self.score(key, "app/a.log:10 «filler line 10»")
        o = rec["outcomes"]
        self.assertFalse(o["measured"])
        self.assertIsNone(o["finding_blocks"])
        self.assertIsNone(o["outcome_missing"])
        self.assertIn("Н-n", o["why"])

    def test_one_labelled_block_is_enough_to_open_the_axis(self):
        """The gate is «did anybody state an outcome», not «did everybody». A
        report that labels three of four blocks supports the join for those
        three, and the fourth is visible as `unlabelled` rather than silently
        scored as a refutation."""
        key = key_with([herring("D01", [loc("app/c.log", 20)])])
        rec, _ = self.score(key)
        self.assertTrue(rec["outcomes"]["measured"])
        self.assertEqual(rec["outcomes"]["decoys_unlabelled"], 1)

    # --- the record everything else already published stays put ------------
    def test_no_pre_existing_column_moves(self):
        """The project's rule: axes are never summed and never silently
        replaced. This one is additive — a new nested block, and every column an
        old ledger row published reads exactly as before."""
        key = key_with([herring("D01", [loc("app/b.log", 10)]),
                        defect("D02", "real", [loc("app/a.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["anchored"], 1)
        self.assertEqual(rec["anchorable"], 1)
        self.assertEqual(rec["presented"], 1)
        self.assertEqual(rec["decoys"], 1)
        self.assertEqual(rec["decoys_anchored"], 1)
        self.assertEqual(rec["decoys_presented"], 1)
        self.assertEqual(rec["decoys_asserted_as_incident"], 1)
        self.assertEqual(rec["decoys_presented_refuted"], 0)

    def test_the_axis_is_printed_beside_the_others(self):
        key = key_with([herring("D01", [loc("app/b.log", 10)]),
                        defect("D02", "real", [loc("app/a.log", 10)])])
        _rec, out = self.score(key)
        self.assertIn("outcomes  :", out)
        self.assertIn("false positive", out)
        self.assertIn("refutation", out)

    def test_the_axis_costs_nothing(self):
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        saved = (S.score_case.http_call, S.score_bench.score)
        S.score_case.http_call = Tripwire("score_case.http_call")
        S.score_bench.score = Tripwire("score_bench.score")
        try:
            with redirect_stdout(io.StringIO()):
                rec = S.score(key, OUTCOME_REPORT, self.tmp, call=None)
        finally:
            S.score_case.http_call, S.score_bench.score = saved
        self.assertEqual(rec["outcomes"]["decoys_refutation"], 1)

    def test_the_parse_is_citechecks_and_is_receipted(self):
        """A second copy of the outcome grammar is a second, incomparable scale.
        The tokens come from citecheck's own vocabulary, and the record already
        receipts which citecheck was loaded."""
        self.assertEqual(tuple(S.citecheck.OUTCOME_ORDER),
                         ("норма", "попытка", "успех"))
        key = key_with([herring("D01", [loc("app/b.log", 10)])])
        rec, _ = self.score(key)
        self.assertEqual(rec["outcomes"]["vocabulary"],
                         list(S.citecheck.OUTCOME_ORDER))
        self.assertEqual(rec["outcomes"]["citecheck_version"],
                         rec["citecheck_version"])


class TestOutcomeHealthIsADeliveryFact(unittest.TestCase):
    """A missing outcome line is a delivery defect exactly as a missing verdict
    section is — and the two channels can disagree about it, so each is read on
    its own beside the union."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50),
                                "app/c.log": numbered(50)})

    def test_each_channel_carries_its_own_outcome_health(self):
        handover = OUTCOME_REPORT.replace("**Исход:** успех", "").replace(
            "исход: норма", "").replace("исход: попытка", "")
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, S.deliverable.compose(handover, OUTCOME_REPORT),
                          self.tmp, call=None,
                          answer=handover, artifact=OUTCOME_REPORT)
        ch = rec["delivery"]["channels"]
        self.assertEqual(ch["file"]["outcomes"]["outcomes_stated"], 3)
        self.assertEqual(ch["message"]["outcomes"]["outcomes_stated"], 0)
        self.assertEqual(ch["message"]["outcomes"]["outcome_missing"], 4,
                         "the hand-over dropped every outcome line the draft "
                         "carried, and the composed record cannot see that")

    def test_the_per_channel_health_is_printed_not_only_recorded(self):
        handover = OUTCOME_REPORT.replace("**Исход:** успех", "")
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            S.score(key, S.deliverable.compose(handover, OUTCOME_REPORT),
                    self.tmp, call=None,
                    answer=handover, artifact=OUTCOME_REPORT)
        self.assertIn("outcomes 2 of 4 finding block(s)", buf.getvalue())
        self.assertIn("outcomes 3 of 4 finding block(s)", buf.getvalue())

    def test_the_composed_record_is_not_replaced_by_the_per_channel_one(self):
        handover = OUTCOME_REPORT.replace("**Исход:** успех", "")
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, S.deliverable.compose(handover, OUTCOME_REPORT),
                          self.tmp, call=None,
                          answer=handover, artifact=OUTCOME_REPORT)
        self.assertIsNotNone(rec["outcomes"]["finding_blocks"])
        self.assertTrue(rec["outcomes"]["measured"])


class TestTheBlockParseIsCitechecksWarts(unittest.TestCase):
    """A CHARACTERISATION, not an endorsement — and deliberately not fixed here.

    `citecheck.FINDING_HEAD_RE` matches any line that STARTS with `Н-n`, with no
    heading marker required. `work/report.md` is hard-wrapped and the final
    message is not, so a wrapped cross-reference — «… событие —\nН-3: `ppid=1`,
    …» — starts a line and reads as a thirteenth finding block. Measured on the
    fleet v16 arm: the message parses 12 blocks and the file 13, and the extra
    one is that sentence.

    It matters here because a phantom block has no outcome line and therefore
    inflates `outcome_missing`. It is pinned rather than worked around: the parse
    lives in `skills/**`, one place decides where a finding starts, and a second
    copy of that rule inside the scorer would be a second, incomparable scale.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_corpus(self.tmp, {"app/a.log": numbered(50),
                                "app/b.log": numbered(50),
                                "app/c.log": numbered(50)})

    def test_a_wrapped_cross_reference_reads_as_a_finding_block(self):
        rep = OUTCOME_REPORT.replace(
            "## 2. Отклонённые кандидаты",
            "Тот же процесс описан выше, событие —\n"
            "Н-3: `ppid=1`, соседние остановки юнитов.\n\n"
            "## 2. Отклонённые кандидаты")
        key = key_with([defect("D01", "real", [loc("app/a.log", 10)])])
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, self.tmp, call=None)
        o = rec["outcomes"]
        self.assertEqual(o["finding_blocks"], 5,
                         "four real blocks plus the wrapped reference")
        self.assertEqual(o["outcome_missing"], 2,
                         "Н-4 forgot its line; the phantom never had one")
        self.assertTrue(o["measured"],
                        "the join still opens — one phantom block does not "
                        "delete an axis three real blocks support")


class TestTheOutcomeAxisOnTheRealReports(unittest.TestCase):
    """Gitignored trajectories: skip where absent, run where the evidence is.

    Every arm on disk predates v24. The axis must say so."""

    BS22 = "20260818T212500Z-v22-claude-bluesky"

    @unittest.skipUnless(_run_report(BS22) and os.path.isdir(BLUESKY_CORPUS),
                         "BlueSky v22 trajectory or corpus not on this machine")
    def test_bluesky_v22_reads_as_unavailable_not_as_zero_refutations(self):
        key = json.load(open(os.path.join(BENCH, "answer-key-bluesky.json"),
                             encoding="utf-8"))
        rep = open(_run_report(self.BS22), encoding="utf-8").read()
        with redirect_stdout(io.StringIO()):
            rec = S.score(key, rep, BLUESKY_CORPUS, call=None)
        o = rec["outcomes"]
        self.assertEqual(rec["decoys_presented"], 2,
                         "the pre-existing column is unmoved")
        self.assertFalse(o["measured"])
        self.assertIsNone(o["decoys_refutation"])
        self.assertIsNone(o["decoys_false_positive"])
        self.assertEqual(o["outcome_missing"], o["finding_blocks"],
                         "every finding block on this arm is missing the line")
        self.assertIsNone(o["implied_verdict"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
