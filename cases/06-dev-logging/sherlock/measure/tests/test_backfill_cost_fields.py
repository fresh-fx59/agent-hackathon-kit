#!/usr/bin/env python3
"""Tests for backfill-cost-fields.py — the one-off that repairs an existing ledger.

A migration that runs once, over rows that cost real money to produce, gets exactly
one chance to be right: results.jsonl is gitignored, so there is no `git checkout` to
undo a bad rewrite. What must hold is that it never invents a number (an unrecoverable
cost is null, never 0), never overwrites one that is already there, and never leaves
the file half-written.

No network, no model, no meta.json outside a temp dir.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
BACKFILL = os.path.join(MEASURE, "backfill-cost-fields.py")
COST = ("duration_s", "input_tokens", "output_tokens", "turns")


class Harness(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="backfill-test-")

    def run_dir(self, name, meta):
        """A run dir whose meta.json is `meta`: a dict is written as JSON, a str
        verbatim (malformed), None leaves the directory with no meta.json at all."""
        p = os.path.join(self.d, name)
        os.makedirs(p, exist_ok=True)
        if meta is not None:
            with open(os.path.join(p, "meta.json"), "w", encoding="utf-8") as fh:
                fh.write(meta if isinstance(meta, str)
                         else json.dumps(meta, ensure_ascii=False))
        return p

    def ledger(self, rows, mode=0o600):
        p = os.path.join(self.d, "results.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(p, mode)
        return p

    def backfill(self, results, *extra):
        p = subprocess.run([sys.executable, BACKFILL, "--results", results] + list(extra),
                           capture_output=True, text=True, timeout=60)
        rows = []
        if os.path.exists(results):
            with open(results, encoding="utf-8") as fh:
                for l in fh:
                    # Lenient on purpose: one test deliberately corrupts a line, and
                    # the assertion there is about the FILE being untouched. A strict
                    # reader here would fail in the harness before the test could look.
                    try:
                        rows.append(json.loads(l))
                    except ValueError:
                        pass
        return p, rows

    def row(self, name, meta, **kw):
        return dict({"case_id": "D11", "arm": "v5", "diagnosis": "ok",
                     "run_dir": self.run_dir(name, meta)}, **kw)


FULL = {"case_id": "D11", "arm": "v5", "duration_s": 1187, "input_tokens": 6042442,
        "output_tokens": 58974, "answer_chars": 530, "turns": 34}


class TheNumbersComeFromTheRunsOwnMeta(Harness):
    def test_a_good_meta_fills_all_four_fields(self):
        p, rows = self.backfill(self.ledger([self.row("r1", FULL)]))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([rows[0][k] for k in COST], [1187, 6042442, 58974, 34])
        self.assertEqual(rows[0]["diagnosis"], "ok", "existing fields must survive")

    def test_a_missing_meta_gives_explicit_nulls(self):
        p, rows = self.backfill(self.ledger([self.row("r1", None)]))
        self.assertEqual(p.returncode, 0, p.stderr)
        for k in COST:
            self.assertIn(k, rows[0], "the key must exist so the gap is visible")
            self.assertIsNone(rows[0][k], k)
        self.assertIn("unreadable", p.stdout)

    def test_a_malformed_meta_gives_explicit_nulls(self):
        p, rows = self.backfill(self.ledger([self.row("r1", "{not json")]))
        self.assertEqual(p.returncode, 0, p.stderr)
        for k in COST:
            self.assertIsNone(rows[0][k], k)
        self.assertIn("malformed", p.stdout)

    def test_a_null_token_stays_null_beside_the_fields_that_were_measured(self):
        meta = dict(FULL, input_tokens=None, turns=None)
        p, rows = self.backfill(self.ledger([self.row("r1", meta)]))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(rows[0]["input_tokens"])
        self.assertIsNone(rows[0]["turns"])
        self.assertEqual(rows[0]["duration_s"], 1187)
        self.assertEqual(rows[0]["output_tokens"], 58974)

    def test_a_measured_zero_is_kept_as_zero(self):
        """0 and null are the whole point of this column: 0 output tokens is a run
        that answered with nothing, null is a run whose cost was never recorded."""
        p, rows = self.backfill(self.ledger([self.row("r1", dict(FULL, output_tokens=0))]))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["output_tokens"], 0)

    def test_a_row_with_no_run_dir_gives_nulls_not_a_crash(self):
        p, rows = self.backfill(self.ledger([{"case_id": "D11", "arm": "v5"}]))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(all(rows[0][k] is None for k in COST))
        self.assertIn("no run_dir", p.stdout)


class RunningItTwiceChangesNothing(Harness):
    def test_the_second_run_is_a_no_op(self):
        results = self.ledger([self.row("r1", FULL)])
        self.backfill(results)
        first = open(results, encoding="utf-8").read()
        p, _ = self.backfill(results)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(open(results, encoding="utf-8").read(), first)
        self.assertIn("nothing to do", p.stdout)

    def test_a_value_already_on_the_row_is_never_overwritten(self):
        """A row scored by the new report-case.py already carries its own numbers.
        Re-deriving them from meta.json would be harmless today and wrong the moment
        the two disagree — the row is the artifact, meta.json is only its source."""
        row = self.row("r1", FULL, duration_s=99, input_tokens=1,
                       output_tokens=2, turns=3)
        p, rows = self.backfill(self.ledger([row]))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([rows[0][k] for k in COST], [99, 1, 2, 3])
        self.assertIn("already had the fields", p.stdout)

    def test_dry_run_touches_nothing(self):
        results = self.ledger([self.row("r1", FULL)])
        before = open(results, encoding="utf-8").read()
        p, rows = self.backfill(results, "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(open(results, encoding="utf-8").read(), before)
        self.assertNotIn("duration_s", rows[0])


class TheLedgerIsNeverDamaged(Harness):
    def test_an_unparseable_line_aborts_before_anything_is_written(self):
        """Rewriting the whole file means a line this script cannot read is a line it
        would silently delete. Refuse instead: these rows are metered measurements."""
        results = self.ledger([self.row("r1", FULL)])
        with open(results, "a", encoding="utf-8") as fh:
            fh.write("{truncated half-row\n")
        before = open(results, encoding="utf-8").read()
        p, _ = self.backfill(results)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(open(results, encoding="utf-8").read(), before)
        self.assertIn("REFUSING", p.stdout)

    def test_the_private_mode_of_the_ledger_survives_the_rewrite(self):
        """results.jsonl is 0600 because it is built from a corpus that must not leave
        this box; a temp file created under the umask would land 0644."""
        results = self.ledger([self.row("r1", FULL)], mode=0o600)
        p, _ = self.backfill(results)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.stat(results).st_mode & 0o777, 0o600)

    def test_no_temp_file_is_left_behind(self):
        results = self.ledger([self.row("r1", FULL)])
        self.backfill(results)
        self.assertFalse(os.path.exists(results + ".backfill.tmp"))

    def test_a_missing_ledger_fails_loudly(self):
        p, _ = self.backfill(os.path.join(self.d, "nope.jsonl"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no results file", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
