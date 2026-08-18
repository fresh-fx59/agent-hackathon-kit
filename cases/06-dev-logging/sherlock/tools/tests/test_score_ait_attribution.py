#!/usr/bin/env python3
"""Tests for eval/bench/score-ait.py — what the number counts, and whose file it is.

Every assertion here is a defect that was MEASURED on the real AIT-LDS v2.1 run
`_runs/ait-all-v15-x/worklist.tsv` on 2026-08-18, before it was fixed.

What broke, and why each matters:

* THE HEADLINE NUMBER WAS NOT RECALL. The scorer appended one entry per worklist
  ROW that landed on a labelled line, then printed `len(...)` in a column read as
  recall. Two rows citing the same labelled line counted twice. Live proof: the
  v15 run printed

      intranet_server/logs/auth.log       8      272    2.9%  **9**

  — nine, in the column next to `labelled=8`. A number larger than its own
  denominator had been sitting there being quoted as coverage. Rows-on-attack is
  real information (it says how much of the budget landed on the intrusion), it
  is simply a different quantity from "how much of the intrusion we covered", and
  the two must never share a column.

* CITATIONS WERE MATCHED BY BASENAME, ACROSS HOSTS. The match clause was

      if path.endswith(rel) or rel.endswith(path.split("/")[-1]):

  The second half is a bare basename test. On this testbed `gather/` ships
  `auth.log` on 10 hosts, `audit.log` on 7 and `error.log.2` on 3, so a row citing
  one host's file was scored as a hit on ANOTHER host's labels. Measured: the 9th
  row on `intranet_server/logs/auth.log` was really `mail/logs/auth.log:146`, and
  all 5 credits on `intranet.smith.russellmitchell.com-error.log.2` came from
  `intranet_server/logs/apache2/error.log.2` and `cloud_share/.../error.log.2` —
  three separate real files. A confident number attributed to the wrong evidence
  is the worst failure this harness can have, so the fallback is deleted rather
  than ranked: a citation that cannot be pinned to exactly ONE label file is
  counted as unattributed and printed, never guessed.

* THE SUFFIX MATCH WAS UNANCHORED. `path.endswith(rel)` with no `/` boundary is
  how `...com-error.log.2`.endswith(`error.log.2`) let `cloud_share` in, and would
  equally match `.../notauth.log` against a label file `auth.log`. Any suffix
  match must land on a path separator.

* ALL LABELLED LINES IN A CITED SPAN COUNT, NOT THE FIRST. The old walk did
  `break` at the first labelled line in a range, so a row citing a 40-line span
  covering 6 labelled lines was credited with 1. A citation is an ADDRESS the
  model is handed and told to read around — every labelled line inside it is in
  front of the model. Line coverage counts them all; rows-on-attack still counts
  the row once, which is what keeps the two columns honest.

    python3 tools/tests/test_score_ait_attribution.py
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
SCORER = os.path.join(SHERLOCK, "eval", "bench", "score-ait.py")

_spec = importlib.util.spec_from_file_location("score_ait", SCORER)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


# --------------------------------------------------------------------------
# a synthetic AIT-LDS-shaped corpus: two hosts that both ship `auth.log`
# --------------------------------------------------------------------------
def build_corpus(tmp, logs, labels):
    """logs: {rel: n_lines} · labels: {rel: {line: [label]}} — both under gather/."""
    for rel, n in logs.items():
        p = os.path.join(tmp, "gather", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            for i in range(1, n + 1):
                fh.write("line %d\n" % i)
    for rel, marks in labels.items():
        p = os.path.join(tmp, "labels", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            for line, ls in sorted(marks.items()):
                fh.write(json.dumps({"line": line, "labels": ls}) + "\n")
    return tmp


def worklist(tmp, refs, name="worklist.tsv"):
    """refs: ["host/logs/auth.log:145", ...] -> a Step-1-shaped TSV."""
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        for i, ref in enumerate(refs, 1):
            fh.write("g%d\t?\tcat\t%s\tn=1\tsome log text\n" % (i, ref))
    return p


def run_scorer(root, wl, detail=False):
    argv = ["--root", root, "--worklist", wl, "--label", "test"]
    if detail:
        argv.append("--detail")
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["score-ait.py"] + argv
    try:
        with redirect_stdout(buf):
            S.main()
    finally:
        sys.argv = old
    return buf.getvalue()


def cell(out, rel):
    """The scorer's row for a label file -> its whitespace-split fields."""
    for line in out.splitlines():
        st = line.strip()
        if st.startswith(rel[:52]):
            return st.split()
    raise AssertionError("no row for %r in:\n%s" % (rel, out))


# --------------------------------------------------------------------------
class TwoHostsOneBasename(unittest.TestCase):
    """The AIT-LDS shape that broke it: two hosts, same filename, one labelled."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="score-ait-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build_corpus(
            self.tmp,
            logs={"intranet_server/logs/auth.log": 272,
                  "mail/logs/auth.log": 272,
                  "internal_share/logs/audit/audit.log": 732,
                  "intranet_server/logs/audit/audit.log": 2316},
            labels={"intranet_server/logs/auth.log":
                    {n: ["escalate"] for n in range(145, 153)},        # 8 lines
                    "internal_share/logs/audit/audit.log":
                    {667: ["exfiltration-service"], 668: ["exfiltration-service"]},
                    "intranet_server/logs/audit/audit.log":
                    {n: ["attacker_change_user"] for n in range(1861, 1870)}},
        )

    def test_line_coverage_never_exceeds_its_own_denominator(self):
        """8 real lines + one cross-host row must not print 9 of 8."""
        wl = worklist(self.tmp, ["intranet_server/logs/auth.log:%d" % n
                                 for n in range(145, 153)]
                      + ["mail/logs/auth.log:146"])
        out = run_scorer(self.tmp, wl)
        c = cell(out, "intranet_server/logs/auth.log")
        covered, total = c[-3].strip("*"), c[-2]
        self.assertEqual((covered, total), ("8", "8"),
                         "line coverage must be distinct labelled lines / labelled "
                         "lines, capped by the denominator:\n" + out)

    def test_duplicate_rows_on_one_line_are_one_line_but_two_rows(self):
        """Three rows all citing line 145: coverage 1, rows-on-attack 3."""
        wl = worklist(self.tmp, ["intranet_server/logs/auth.log:145"] * 3)
        out = run_scorer(self.tmp, wl)
        c = cell(out, "intranet_server/logs/auth.log")
        self.assertEqual(c[-3].strip("*"), "1", "3 rows on ONE line cover 1 line:\n" + out)
        self.assertEqual(c[-1].strip("*"), "3", "…and are still 3 rows of budget:\n" + out)

    def test_other_host_basename_is_never_credited(self):
        """mail/logs/auth.log is unlabelled — citing it must score nothing."""
        wl = worklist(self.tmp, ["mail/logs/auth.log:%d" % n for n in range(145, 153)])
        out = run_scorer(self.tmp, wl)
        c = cell(out, "intranet_server/logs/auth.log")
        self.assertEqual(c[-3].strip("*"), "0",
                         "a citation to another host must not touch these labels:\n" + out)
        self.assertIn("FILES TOUCHED: 0", out)

    def test_the_two_audit_logs_do_not_cross_credit(self):
        """internal_share and intranet_server both ship logs/audit/audit.log."""
        wl = worklist(self.tmp, ["internal_share/logs/audit/audit.log:667",
                                 "internal_share/logs/audit/audit.log:668"])
        out = run_scorer(self.tmp, wl)
        self.assertEqual(cell(out, "internal_share/logs/audit/audit.log")[-3].strip("*"), "2")
        self.assertEqual(cell(out, "intranet_server/logs/audit/audit.log")[-3].strip("*"), "0",
                         "the OTHER host's audit.log must stay at zero:\n" + out)

    def test_host_qualified_suffix_still_matches(self):
        """A run pointed at one host cites `logs/auth.log:145` with no host prefix."""
        wl = worklist(self.tmp, ["logs/auth.log:145"])
        out = run_scorer(self.tmp, wl)
        self.assertEqual(cell(out, "intranet_server/logs/auth.log")[-3].strip("*"), "1",
                         "an unambiguous suffix must resolve:\n" + out)


class UnanchoredSuffix(unittest.TestCase):
    """`...com-error.log.2`.endswith(`error.log.2`) is not a path match."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="score-ait-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build_corpus(
            self.tmp,
            logs={"intranet_server/logs/apache2/"
                  "intranet.smith.russellmitchell.com-error.log.2": 36,
                  "intranet_server/logs/apache2/error.log.2": 500,
                  "cloud_share/logs/apache2/error.log.2": 500},
            labels={"intranet_server/logs/apache2/"
                    "intranet.smith.russellmitchell.com-error.log.2":
                    {n: ["dirb"] for n in range(1, 37)}},
        )

    def test_sibling_file_sharing_a_name_tail_is_not_a_hit(self):
        """The measured case: 5 credits that all came from other files."""
        wl = worklist(self.tmp, ["intranet_server/logs/apache2/error.log.2:1",
                                 "intranet_server/logs/apache2/error.log.2:2",
                                 "intranet_server/logs/apache2/error.log.2:3",
                                 "cloud_share/logs/apache2/error.log.2:1",
                                 "cloud_share/logs/apache2/error.log.2:2"])
        out = run_scorer(self.tmp, wl)
        rel = "intranet_server/logs/apache2/intranet.smith.russellmitchell.com-error.log.2"
        self.assertEqual(cell(out, rel)[-3].strip("*"), "0",
                         "a suffix match must land on a '/' boundary:\n" + out)
        self.assertIn("FILES TOUCHED: 0", out)

    def test_notauth_does_not_match_auth(self):
        """The generic form of the same bug."""
        tmp = tempfile.mkdtemp(prefix="score-ait-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build_corpus(tmp, logs={"h/logs/auth.log": 10, "h/logs/notauth.log": 10},
                     labels={"h/logs/auth.log": {5: ["escalate"]}})
        wl = worklist(tmp, ["h/logs/notauth.log:5"])
        out = run_scorer(tmp, wl)
        self.assertEqual(cell(out, "h/logs/auth.log")[-3].strip("*"), "0",
                         "notauth.log is not auth.log:\n" + out)


class SpanCoverage(unittest.TestCase):
    """A cited range puts EVERY labelled line inside it in front of the model."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="score-ait-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build_corpus(self.tmp, logs={"h/logs/auth.log": 300},
                     labels={"h/logs/auth.log":
                             {n: ["escalate"] for n in range(145, 153)}})

    def test_a_span_covers_every_labelled_line_it_contains(self):
        wl = worklist(self.tmp, ["h/logs/auth.log:140-160"])
        out = run_scorer(self.tmp, wl)
        c = cell(out, "h/logs/auth.log")
        self.assertEqual(c[-3].strip("*"), "8",
                         "one row spanning all 8 labelled lines covers 8, not 1:\n" + out)
        self.assertEqual(c[-1].strip("*"), "1", "…and it is still ONE row:\n" + out)

    def test_span_clamp_is_documented_and_wide_enough_for_real_rows(self):
        """The widest citation in any published AIT run spans 80 lines."""
        self.assertGreaterEqual(S.MAX_SPAN, 80,
                                "the clamp must not silently truncate a real row")


class Unattributed(unittest.TestCase):
    """Never guess. Count it and print it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="score-ait-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build_corpus(
            self.tmp,
            logs={"intranet_server/logs/auth.log": 272, "mail/logs/auth.log": 272},
            labels={"intranet_server/logs/auth.log": {145: ["escalate"]},
                    "mail/logs/auth.log": {145: ["escalate"]}},
        )

    def test_an_ambiguous_bare_suffix_is_unattributed_not_guessed(self):
        """`auth.log:145` fits two label files. Credit neither; say so."""
        wl = worklist(self.tmp, ["auth.log:145"])
        out = run_scorer(self.tmp, wl)
        self.assertEqual(cell(out, "intranet_server/logs/auth.log")[-3].strip("*"), "0", out)
        self.assertEqual(cell(out, "mail/logs/auth.log")[-3].strip("*"), "0", out)
        self.assertIn("unattributed", out.lower())
        self.assertRegex(out, r"unattributed[^\n]*\b1\b")

    def test_unambiguous_citation_is_not_counted_unattributed(self):
        wl = worklist(self.tmp, ["mail/logs/auth.log:145"])
        out = run_scorer(self.tmp, wl)
        self.assertEqual(cell(out, "mail/logs/auth.log")[-3].strip("*"), "1", out)
        self.assertRegex(out, r"unattributed[^\n]*\b0\b")


class ColumnsAreDistinct(unittest.TestCase):
    """Rows-on-attack survives as its own named column — it is real information."""

    def test_header_names_both_quantities(self):
        tmp = tempfile.mkdtemp(prefix="score-ait-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build_corpus(tmp, logs={"h/logs/auth.log": 10},
                     labels={"h/logs/auth.log": {5: ["escalate"]}})
        out = run_scorer(tmp, worklist(tmp, ["h/logs/auth.log:5"]))
        head = [l for l in out.splitlines() if "labelled file" in l]
        self.assertTrue(head, out)
        self.assertIn("covered", head[0], "the recall column must say what it counts")
        self.assertIn("rows", head[0], "rows-on-attack must keep its own column")


if __name__ == "__main__":
    unittest.main(verbosity=2)
