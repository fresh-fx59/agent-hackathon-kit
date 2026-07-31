#!/usr/bin/env python3
"""Tests for skills/v11/tools/logjoin.py — the arm-local fork.

One change: hits come back as RECORDS, not lines. A record is often several
physical lines (a stack trace, a goroutine dump, an 18-line journald block), and
a citation to its first line alone is not a citation to the evidence — the
checker will read that one line and find nothing that supports the claim.

Everything the tool did before must keep working, so the inherited behaviour
(canonical spellings, `absent_in`, refusing an invented edge) is asserted here
too, against the fork rather than against the original.

    python3 tools/tests/test_logjoin_v11.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
LJ = os.path.join(SHERLOCK, "skills", "v11", "tools", "logjoin.py")


def w(root, rel, lines):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def run(root, *ids, **kw):
    p = subprocess.run([sys.executable, LJ, *ids, "--corpus", root, "--json",
                        *kw.get("extra", ())], capture_output=True, text=True)
    return p, json.loads(p.stdout)


def entry(got, ident):
    return next(e for e in got["per_id"] if e["id"] == ident)


class HitsAreRecordsNotLines(unittest.TestCase):

    def test_a_stack_trace_comes_back_as_a_range(self):
        with tempfile.TemporaryDirectory() as d:
            lines = ["2026-07-28 09:%02d:00 ok work" % (i % 60) for i in range(300)]
            lines += ["2026-07-28 14:12:33.000 SEVERE unhandled for ORD-77421",
                      "\tat com.acme.Resolver.resolve(Resolver:41)",
                      "\tat com.acme.Controller.apply(Controller:88)"]
            w(d, "apps/app.log", lines)
            _p, got = run(d, "ORD-77421")
            f = entry(got, "ORD-77421")["files"][0]
            self.assertEqual(f["refs"], ["apps/app.log:301-303"],
                             "a multi-line record must cite as a range")

    def test_a_journald_block_comes_back_as_its_whole_block(self):
        with tempfile.TemporaryDirectory() as d:
            lines = []
            for i in range(60):
                lines += ["__CURSOR=s=abc;i=%d" % i,
                          "__REALTIME_TIMESTAMP=%d" % (1785229200000000 + i * 1000),
                          "_TRANSPORT=stdout",
                          "SYSLOG_IDENTIFIER=kubelet",
                          "MESSAGE=probe ok",
                          ""]
            lines += ["__CURSOR=s=abc;i=999",
                      "__REALTIME_TIMESTAMP=1785243600000000",
                      "_TRANSPORT=stdout",
                      "SYSLOG_IDENTIFIER=kubelet",
                      "MESSAGE=scale to 2 for ORD-77421",
                      ""]
            w(d, "systemd/journal.txt", lines)
            _p, got = run(d, "ORD-77421")
            f = entry(got, "ORD-77421")["files"][0]
            self.assertRegex(f["refs"][0], r"systemd/journal\.txt:\d+-\d+")

    def test_a_plain_line_log_still_cites_a_single_line(self):
        with tempfile.TemporaryDirectory() as d:
            w(d, "a.log", ["2026-07-28 09:00:00 x", "2026-07-28 09:00:01 ORD-1 paid"])
            _p, got = run(d, "ORD-1")
            self.assertEqual(entry(got, "ORD-1")["files"][0]["refs"], ["a.log:2"])

    def test_it_still_runs_when_logmap_is_not_next_to_it_and_SAYS_so(self):
        """A silent degrade is how this project loses defects, so the fallback
        announces itself."""
        with tempfile.TemporaryDirectory() as d:
            solo = os.path.join(d, "solo")
            os.makedirs(solo)
            import shutil
            shutil.copy(LJ, os.path.join(solo, "logjoin.py"))
            w(d, "logs/a.log", ["2026-07-28 09:00:01 ORD-1 paid"])
            p = subprocess.run([sys.executable, os.path.join(solo, "logjoin.py"),
                                "ORD-1", "--corpus", os.path.join(d, "logs")],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("logmap.py рядом не найден", p.stdout)
            self.assertIn("a.log:1", p.stdout)


class TheInheritedBehaviourSurvivesTheFork(unittest.TestCase):

    def build(self, d):
        w(d, "gw/access.log", ["2026-07-28 09:00:00 GET /x ord_77421 ok"])
        w(d, "app/app.log", ["2026-07-28 09:00:01 order ORD-77421 accepted"])
        w(d, "notify/n.log", ["2026-07-28 09:00:02 nothing to see"])

    def test_spellings_are_folded(self):
        with tempfile.TemporaryDirectory() as d:
            self.build(d)
            _p, got = run(d, "ORD-77421")
            paths = [f["path"] for f in entry(got, "ORD-77421")["files"]]
            self.assertEqual(sorted(paths), ["app/app.log", "gw/access.log"])

    def test_absence_is_reported_because_it_cannot_be_noticed(self):
        with tempfile.TemporaryDirectory() as d:
            self.build(d)
            _p, got = run(d, "ORD-77421")
            self.assertIn("notify/n.log", entry(got, "ORD-77421")["absent_in"])

    def test_an_invented_edge_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.build(d)
            _p, got = run(d, "ORD-77421", "10.42.12.31")
            self.assertEqual(got["cooccurrence"][0]["verdict"], "not-in-corpus")

    def test_a_real_edge_is_confirmed(self):
        with tempfile.TemporaryDirectory() as d:
            w(d, "a.log", ["2026-07-28 09:00:00 ORD-1 from 10.42.12.31 ok"])
            _p, got = run(d, "ORD-1", "10.42.12.31")
            self.assertEqual(got["cooccurrence"][0]["verdict"], "confirmed")

    def test_word_boundaries_hold(self):
        with tempfile.TemporaryDirectory() as d:
            w(d, "a.log", ["2026-07-28 09:00:00 ORD-77421 ok"])
            _p, got = run(d, "7742")
            self.assertEqual(entry(got, "7742")["total_hits"], 0)

    def test_an_id_absent_everywhere_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            self.build(d)
            p, _got = run(d, "ORD-00000")
            self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
