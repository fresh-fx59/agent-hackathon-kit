#!/usr/bin/env python3
"""Tests for tools/logjoin.py — the multi-hop primitive.

Two things it must do that grep does not:
  1. an id is respelled between services (`ORD-77421` / `ord_77421`);
  2. **absence is evidence** — the file where the id should have appeared and
     did not is the finding, and a model cannot notice a thing that is not there.

Plus the corpus-wide co-occurrence check, which is the deterministic answer to the
most common fabrication: an invented RELATIONSHIP between two real entities.

    python3 tools/tests/test_logjoin.py
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
CORPUS = os.path.join(HERE, "fixtures", "corpus")
LOGJOIN = os.path.join(TOOLS, "logjoin.py")


def run(*args):
    p = subprocess.run([sys.executable, LOGJOIN, *args, "--corpus", CORPUS, "--json"],
                       capture_output=True, text=True)
    assert p.returncode in (0, 1), p.stderr
    return json.loads(p.stdout)


def by_path(entry):
    return {f["path"]: f for f in entry["files"]}


class CrossFileCorrelation(unittest.TestCase):

    def test_line_numbers_and_timestamps(self):
        d = run("ORD-77421")
        e = d["per_id"][0]
        api = by_path(e)["api/app.log"]
        self.assertEqual(api["lines"], [1, 7, 8])
        self.assertEqual(api["hits"], 3)
        self.assertEqual(api["first_ts"], "2026-07-28T10:00:01.114Z")
        self.assertEqual(api["last_ts"], "2026-07-28T10:00:08.010Z")
        self.assertIn("PaymentTimeout", "".join(api["sample"]))

    def test_respelled_id_is_found(self):
        gw = by_path(run("ORD-77421")["per_id"][0])["gateway/access.log"]
        self.assertEqual(gw["lines"], [1, 4])
        self.assertEqual(gw["first_ts"], "28/Jul/2026:10:00:01")

    def test_no_canon_only_matches_the_literal(self):
        d = run("ORD-77421", "--no-canon")
        self.assertNotIn("gateway/access.log", by_path(d["per_id"][0]))

    def test_total_hits(self):
        self.assertEqual(run("ORD-77421")["per_id"][0]["total_hits"], 5)


class AbsenceIsEvidence(unittest.TestCase):

    def test_absent_file_is_named(self):
        e = run("ORD-77421")["per_id"][0]
        self.assertEqual(e["absent_in"], ["payments/payments.log"])

    def test_present_id_has_no_absence(self):
        e = run("ORD-77422")["per_id"][0]
        self.assertEqual(e["absent_in"], [])

    def test_id_absent_everywhere(self):
        e = run("ORD-99999")["per_id"][0]
        self.assertEqual(e["total_hits"], 0)
        self.assertEqual(len(e["absent_in"]), 3)


class Boundaries(unittest.TestCase):

    def test_substring_is_not_a_hit(self):
        """`7742` sits inside ORD-77421/77422/77423 and must not match."""
        self.assertEqual(run("7742")["per_id"][0]["total_hits"], 0)

    def test_substring_mode_can_be_asked_for(self):
        self.assertGreater(run("7742", "--substring")["per_id"][0]["total_hits"], 0)


class CoOccurrence(unittest.TestCase):
    """The invented-edge guard: two real entities joined by an edge nobody logged."""

    def test_real_edge_is_confirmed(self):
        d = run("ORD-77421", "10.42.12.31")
        co = d["cooccurrence"][0]
        self.assertEqual(co["hits"], 2)
        self.assertEqual(co["files"][0]["path"], "gateway/access.log")
        self.assertEqual(co["files"][0]["lines"], [1, 4])

    def test_invented_edge_is_refused(self):
        d = run("ORD-77421", "10.42.12.33")
        co = d["cooccurrence"][0]
        self.assertEqual(co["hits"], 0)
        self.assertEqual(co["verdict"], "not-in-corpus")

    def test_single_id_has_no_cooccurrence_section(self):
        self.assertEqual(run("ORD-77421")["cooccurrence"], [])


class Capping(unittest.TestCase):

    def test_max_hits_caps_lines_but_not_the_count(self):
        d = run("ORD-77421", "--max-hits", "1")
        api = by_path(d["per_id"][0])["api/app.log"]
        self.assertEqual(api["lines"], [1])
        self.assertEqual(api["hits"], 3)
        self.assertTrue(api["truncated"])


class TextOutput(unittest.TestCase):

    def test_human_output_names_the_absence(self):
        p = subprocess.run([sys.executable, LOGJOIN, "ORD-77421", "--corpus", CORPUS],
                           capture_output=True, text=True)
        self.assertIn("payments/payments.log", p.stdout)
        self.assertIn("api/app.log:1", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
