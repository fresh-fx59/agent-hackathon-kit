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

    def test_it_uses_v16_not_the_stale_working_copy(self):
        """tools/citecheck.py in the working tree is a v5-v10 snapshot: it has no
        `ambiguous` verdict at all. Loading it here would silently drop the one
        column this scorer promises to keep visible."""
        self.assertIn("ambiguous", S.citecheck.VERDICTS)
        self.assertNotIn("ambiguous", S.citecheck.RANK,
                         "v16 keeps `ambiguous` out of RANK on purpose")
        self.assertIn(os.path.join("skills", "v16"), S.CITECHECK_PATH)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
