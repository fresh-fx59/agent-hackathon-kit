#!/usr/bin/env python3
"""Provider-free contracts for the minimum resumable Sherlock v30 cut."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
SKILL = SHERLOCK / "skills" / "v30"


class SkillV30Contract(unittest.TestCase):
    def test_staging_moves_unsafe_names_without_duplicating_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            corpus = Path(raw) / "corpus"
            corpus.mkdir()
            fixtures = {
                "Microsoft-Windows-PowerShell%4Operational.jsonl": "one\n",
                "Windows PowerShell.jsonl": "two\n",
                "журнал.jsonl": "three\n",
            }
            for name, body in fixtures.items():
                (corpus / name).write_text(body, encoding="utf-8")
            path_map = Path(raw) / "work" / "path-map.tsv"

            run = subprocess.run(
                [sys.executable, str(SKILL / "tools" / "stage-corpus.py"), str(corpus),
                 "--map", str(path_map)],
                text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            rows = json.loads(run.stdout)
            self.assertEqual(rows["files"], 3)
            self.assertEqual(rows["moved"], 2)
            self.assertFalse((corpus / "Microsoft-Windows-PowerShell%4Operational.jsonl").exists())
            self.assertEqual(
                (corpus / "rendered" / "Microsoft-Windows-PowerShell-4Operational.jsonl").read_text(),
                "one\n")
            self.assertEqual(
                (corpus / "rendered" / "Windows-PowerShell.jsonl").read_text(), "two\n")
            self.assertEqual((corpus / "журнал.jsonl").read_text(), "three\n")
            self.assertEqual(sum(p.is_file() for p in corpus.rglob("*")), 3)
            self.assertIn("source_relpath\tsafe_relpath\tsha256", path_map.read_text())

    def test_checkpoint_accepts_resolved_seed_and_writes_report_skeleton(self):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw) / "work"
            work.mkdir()
            (work / "worklist.tsv").write_text(
                "# id\tverdict\nA001\tD problem\nA002\tN normal\n", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(SKILL / "tools" / "checkpoint.py"), "init",
                 "--work", str(work)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            checkpoint = json.loads((work / "checkpoint.json").read_text())
            self.assertEqual(checkpoint["resolved"], 2)
            self.assertEqual(checkpoint["unresolved"], 0)
            self.assertEqual(checkpoint["state"], "ready_for_synthesis")
            report = (work / "report.md").read_text()
            self.assertIn("Состояние: частичный отчёт", report)
            self.assertIn("2 из 2", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
