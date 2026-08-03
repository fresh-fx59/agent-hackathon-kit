#!/usr/bin/env python3
"""Tests for eval/bench/backfill-bench-delivery.py — the one-off that repairs
the bench ledger after `work/report.md` became a delivery channel.

Same stakes as backfill-row-fields.py: every row in `runs-bench.jsonl` cost
metered money and one of them cost 18.76 M input tokens. What must hold is that
it never invents a number, never overwrites a recorded one, never leaves the
file half-written, and can be run twice.

Three jobs, because all three are the same defect — the ledger cannot see what
the run produced:

1. **artifact** — the report file each recorded run left behind.
2. **stub** — four rows in the real ledger came from a stub `qwen`
   (`input_tokens: 11`, answer «apps/api.log:1 something broke»). `rows[-1]`
   would score one as a measurement.
3. **orphan** — `20260802T151710Z-v11` has a 0-byte `out.json` and a complete
   24,233-byte (19,115-char) `work/report.md`. The runner exited before recording anything, so
   a paid-for run left NO row. Its detection is answerable; its cost is not.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(MEASURE)
TOOL = os.path.join(SHERLOCK, "eval", "bench", "backfill-bench-delivery.py")


class Rig(unittest.TestCase):

    def build(self, rows, dirs):
        d = tempfile.mkdtemp(prefix="backfill-bench-")
        runs = os.path.join(d, "runs")
        for name, report in dirs.items():
            w = os.path.join(runs, name, "work")
            os.makedirs(w)
            if report is not None:
                with open(os.path.join(w, "report.md"), "w",
                          encoding="utf-8") as fh:
                    fh.write(report)
        ledger = os.path.join(d, "runs-bench.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            for r in rows:
                if "trace_dir" in r and not os.path.isabs(r["trace_dir"]):
                    r = dict(r, trace_dir=os.path.join(runs, r["trace_dir"]))
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return d, ledger, runs

    def run_tool(self, rows, dirs, extra=()):
        d, ledger, runs = self.build(rows, dirs)
        p = subprocess.run([sys.executable, TOOL, "--ledger", ledger,
                            "--runs", runs, *extra],
                           capture_output=True, text=True, timeout=60)
        with open(ledger, encoding="utf-8") as fh:
            out = [json.loads(l) for l in fh if l.strip()]
        return p, out, ledger


class TheArtifactChannelIsFilledIn(Rig):

    ROWS = [{"arm": "v11", "trace_dir": "run-a", "answer": "Отчёт готов.",
             "input_tokens": 18758431, "turns": 90}]

    def test_the_report_file_lands_on_the_row(self):
        # the real proportions of 20260802T221034Z-v11: 101 chars beside 19,991
        report = "# Отчёт\napi.log:1 x\n" + "деталь\n" * 2800
        _p, out, _l = self.run_tool(self.ROWS, {"run-a": report})
        self.assertEqual(out[0]["artifact"], report)
        self.assertEqual(out[0]["artifact_chars"], len(report))
        self.assertEqual(out[0]["delivered_in"], "file")

    def test_a_run_dir_with_no_report_records_an_empty_artifact_not_a_guess(self):
        _p, out, _l = self.run_tool(self.ROWS, {"run-a": None})
        self.assertEqual(out[0]["artifact"], "")
        self.assertEqual(out[0]["delivered_in"], "message")

    def test_a_missing_run_dir_leaves_the_row_message_only(self):
        _p, out, _l = self.run_tool(
            [{"arm": "v11", "trace_dir": "/gone/for/good", "answer": "a report"}],
            {})
        self.assertEqual(out[0]["artifact"], "")
        self.assertEqual(out[0]["delivered_in"], "message")

    def test_a_row_predating_trace_dir_is_left_alone(self):
        """Rows 0–4 have no `trace_dir` at all. Their run dirs are gone, so the
        honest record is message-only, and inventing one would be worse."""
        _p, out, _l = self.run_tool([{"arm": "v2", "answer": "old row"}], {})
        self.assertEqual(out[0]["artifact"], "")
        self.assertEqual(out[0]["answer"], "old row")

    def test_an_already_filled_artifact_is_never_overwritten(self):
        rows = [dict(self.ROWS[0], artifact="RECORDED AT RUN TIME")]
        _p, out, _l = self.run_tool(rows, {"run-a": "a different body"})
        self.assertEqual(out[0]["artifact"], "RECORDED AT RUN TIME")

    def test_running_it_twice_changes_nothing(self):
        d, ledger, runs = self.build(list(self.ROWS), {"run-a": "# Отчёт\nx"})
        for _ in range(2):
            subprocess.run([sys.executable, TOOL, "--ledger", ledger,
                            "--runs", runs], capture_output=True, timeout=60)
            with open(ledger, encoding="utf-8") as fh:
                once = fh.read()
        subprocess.run([sys.executable, TOOL, "--ledger", ledger, "--runs", runs],
                       capture_output=True, timeout=60)
        with open(ledger, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), once)

    def test_dry_run_touches_nothing(self):
        d, ledger, runs = self.build(list(self.ROWS), {"run-a": "# Отчёт\nx"})
        before = open(ledger, encoding="utf-8").read()
        p = subprocess.run([sys.executable, TOOL, "--ledger", ledger,
                            "--runs", runs, "--dry-run"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(open(ledger, encoding="utf-8").read(), before)
        self.assertIn("artifact", p.stdout)


class TheStubRowsAreMarkedNotDeleted(Rig):
    """Marked, never deleted: a row that was really produced stays in the file,
    and the mark is what keeps it out of a measurement."""

    STUB = {"arm": "none", "trace_dir": "s1", "turns": 2, "input_tokens": 11,
            "answer": "apps/api.log:1 something broke"}

    def test_the_stub_answer_and_cost_together_mark_a_row(self):
        _p, out, _l = self.run_tool([self.STUB], {"s1": None})
        self.assertIs(out[0]["stub"], True)
        self.assertEqual(len(out), 1, "the row must survive, marked")

    def test_a_real_row_is_never_marked(self):
        row = {"arm": "v11", "trace_dir": "r", "turns": 66,
               "input_tokens": 11083002, "answer": "a real 25kb report"}
        _p, out, _l = self.run_tool([row], {"r": None})
        self.assertFalse(out[0].get("stub"))

    def test_a_real_row_that_merely_quotes_the_stub_line_is_not_marked(self):
        """The stub's answer is a plausible sentence. Cost is what makes it a
        stub: 11 input tokens cannot buy a 649 MB investigation."""
        row = {"arm": "v11", "trace_dir": "r", "turns": 48,
               "input_tokens": 6995082,
               "answer": "apps/api.log:1 something broke"}
        _p, out, _l = self.run_tool([row], {"r": None})
        self.assertFalse(out[0].get("stub"))


class TheOrphanRunsAreRecovered(Rig):

    def test_a_run_dir_with_a_report_and_no_row_gets_an_artifact_only_row(self):
        p, out, _l = self.run_tool(
            [{"arm": "v11", "trace_dir": "kept", "answer": "a"}],
            {"kept": "# kept", "20260802T151710Z-v11": "# recovered\napi.log:1"})
        orphans = [r for r in out if r.get("artifact_only")]
        self.assertEqual(len(orphans), 1, p.stdout + p.stderr)
        o = orphans[0]
        self.assertEqual(o["artifact"], "# recovered\napi.log:1")
        self.assertEqual(o["arm"], "v11", "the arm is read off the run-dir name")
        self.assertEqual(o["delivered_in"], "file")

    def test_the_recovered_row_carries_NULL_cost_never_zero(self):
        """Its out.json is 0 bytes: the tokens were spent and are unrecoverable.
        Zero would make the arm look free. → [[eval-must-measure-cost-not-just-quality]]"""
        _p, out, _l = self.run_tool([], {"20260802T151710Z-v11": "# recovered"})
        o = out[0]
        for k in ("input_tokens", "output_tokens", "turns", "duration_s"):
            self.assertIsNone(o[k], k)

    def test_a_run_dir_with_no_report_is_not_invented_into_a_row(self):
        _p, out, _l = self.run_tool([], {"empty-run": None})
        self.assertEqual(out, [])

    def test_recovery_runs_once_not_once_per_invocation(self):
        d, ledger, runs = self.build([], {"20260802T151710Z-v11": "# r"})
        for _ in range(3):
            subprocess.run([sys.executable, TOOL, "--ledger", ledger,
                            "--runs", runs], capture_output=True, timeout=60)
        with open(ledger, encoding="utf-8") as fh:
            self.assertEqual(len([l for l in fh if l.strip()]), 1)


class TheRewriteIsAtomic(Rig):

    def test_a_backup_of_the_original_is_left_beside_it(self):
        """A --ledger pointed at an untracked file has no `git checkout`."""
        _p, _out, ledger = self.run_tool(
            [{"arm": "v11", "trace_dir": "a", "answer": "x"}], {"a": "# r"})
        self.assertTrue(os.path.exists(ledger + ".bak"),
                        "no backup of a gitignored, metered ledger")

    def test_every_original_field_survives_the_rewrite(self):
        row = {"arm": "v11", "trace_dir": "a", "answer": "x", "model": "[SP]d",
               "cited_files": ["a.log"], "files_cited": 1, "line_refs": 3}
        _p, out, _l = self.run_tool([row], {"a": "# r"})
        for k, v in row.items():
            if k == "trace_dir":
                continue
            self.assertEqual(out[0][k], v, k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
