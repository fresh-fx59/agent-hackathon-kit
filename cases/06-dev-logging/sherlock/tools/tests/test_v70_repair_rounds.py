#!/usr/bin/env python3
"""Provider-free v70 tests: repair rounds count only after an edit; last-check.txt."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
PKG = SHERLOCK / "skills" / os.environ.get("SHERLOCK_PKG", "v70")
_SPEC = importlib.util.spec_from_file_location("check_v70", PKG / "tools" / "check.py")
CHECK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECK)


class FakeProc:
    returncode = 1
    stderr = ""
    stdout = json.dumps({"attempt": "a1", "verdict": "BLOCKED"})


class RepairRounds(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v70-rounds-"))
        self.work = self.tmp / "work"
        (self.work / "validation").mkdir(parents=True)
        (self.work / "report.md").write_text("# r\n", encoding="utf-8")
        (self.work / "checkpoint.json").write_text(
            json.dumps({"stage": "repair", "boundary_seq": 2}), encoding="utf-8")
        (self.tmp / "corpus").mkdir()
        self._orig = (CHECK.subprocess.run, CHECK.diagnostics, CHECK.summarize)
        CHECK.subprocess.run = lambda *a, **k: FakeProc()
        CHECK.diagnostics = lambda *a, **k: {}
        CHECK.summarize = lambda *a, **k: "  ✗ стр.1: blocker\n  ✗ стр.2: blocker"
        self.addCleanup(self._restore)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _restore(self):
        CHECK.subprocess.run, CHECK.diagnostics, CHECK.summarize = self._orig

    def run_check(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CHECK.main(["--work", str(self.work), "--corpus", str(self.tmp / "corpus")])
        return rc, out.getvalue(), err.getvalue()

    def counts(self):
        return json.loads((self.work / "validation" / "repair-check-counts.json")
                          .read_text(encoding="utf-8"))

    def edit(self, text):
        (self.work / "report.md").write_text(text, encoding="utf-8")

    def test_first_run_counts(self):
        self.run_check()
        self.assertEqual(self.counts(), {"repair-2": 1})

    def test_no_edit_rerun_not_counted(self):
        self.run_check()
        for _ in range(4):
            rc, out, _ = self.run_check()
            self.assertEqual(rc, 1)
        self.assertEqual(self.counts(), {"repair-2": 1})
        self.assertIn("no edit since last check — not counted; fix the listed items, "
                      "then re-check", out)

    def test_edit_then_rerun_counted(self):
        self.run_check()
        self.edit("# r\nedit\n")
        _, out, _ = self.run_check()
        self.assertEqual(self.counts(), {"repair-2": 2})
        self.assertNotIn("not counted", out)

    def test_cap_enforced_after_two_edited_rounds(self):
        self.run_check()
        self.edit("# r\n1\n")
        self.run_check()
        self.edit("# r\n2\n")
        rc, out, err = self.run_check()
        self.assertEqual(rc, 2)
        self.assertIn("two-check cap", err)
        self.assertEqual(self.counts(), {"repair-2": 2})

    def test_no_edit_rerun_allowed_at_cap(self):
        self.run_check()
        self.edit("# r\n1\n")
        self.run_check()
        rc, out, err = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("not counted", out)
        self.assertEqual(self.counts(), {"repair-2": 2})

    def test_report_rewritten_by_check_is_not_an_edit(self):
        def rewriting(*a, **k):
            self.edit("# r\nrendered by check\n")
            return FakeProc()
        CHECK.subprocess.run = rewriting
        self.run_check()
        self.run_check()
        self.assertEqual(self.counts(), {"repair-2": 1})

    def test_record_repair_check_api_without_report_still_caps(self):
        (self.work / "report.md").unlink()
        self.assertEqual(CHECK.record_repair_check(self.work, "repair-1"), 1)
        self.assertEqual(CHECK.record_repair_check(self.work, "repair-1"), 2)
        with self.assertRaises(RuntimeError):
            CHECK.record_repair_check(self.work, "repair-1")
        self.assertEqual(CHECK.record_repair_check(self.work, "repair-2"), 1)

    def test_last_check_written_and_first_last_lines(self):
        rc, out, _ = self.run_check()
        f = self.work / "validation" / "last-check.txt"
        self.assertTrue(f.is_file())
        self.assertEqual(f.read_text(encoding="utf-8"), out)
        lines = out.splitlines()
        self.assertEqual(lines[0], lines[-1])
        self.assertIn("ИТОГ: BLOCKED", lines[0])
        self.assertIn("full output: " + str(f), lines[0])
        self.assertTrue(lines[0].endswith("validation/last-check.txt"))
        self.assertIn("blocker", out)

    def test_skill_rule_text(self):
        s = " ".join((PKG / "SKILL.md").read_text(encoding="utf-8").split())
        for needle in ("Never pipe `check.py`", "work/validation/last-check.txt",
                       "вставь строкой ниже", "cite.py <file>:<line>", "`not-shown`"):
            self.assertIn(needle, s)


if __name__ == "__main__":
    unittest.main()
