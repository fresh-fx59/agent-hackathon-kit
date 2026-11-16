#!/usr/bin/env python3
"""Provider-free v69 boundary tests."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "skills" / os.environ.get("SHERLOCK_PKG", "v69") / "tools"
WORKLIST = TOOLS / "worklist.py"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v69-contract-"))
        self.work, self.corpus = self.tmp / "work", self.tmp / "corpus"
        self.work.mkdir(); self.corpus.mkdir()
        (self.corpus / "Security.jsonl").write_text(
            '{"EventID":4625,"TargetUserName":"alice"}\n'
            '{"EventID":4624,"Message":"Unique failure Alpha"}\n', encoding="utf-8")
        (self.work / "worklist.tsv").write_text(
            "# sherlock-host\tfixture\n"
            "g001\t?\tN\tSecurity.jsonl:1\t1\t4625\n"
            "g002\t?\tN\tSecurity.jsonl:2\t1\t4624\n", encoding="utf-8")

    def invoke(self, *args, input):
        return subprocess.run([sys.executable, *map(str, args)], input=input,
                              text=True, capture_output=True, timeout=30)


class TestV67Contract(Fixture):
    def test_mixed_batch_rejects_atomically_and_preserves_provenance(self):
        ledger = (self.work / "worklist.tsv").read_bytes()
        payload = ('g001\tN Security.jsonl:1 "EventID":4625\n'
                   'g002\tN Security.jsonl:2 "4624"\n')
        r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                        "--corpus", self.corpus, "--from-stdin", input=payload)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.work / "worklist.tsv").read_bytes(), ledger)
        self.assertFalse((self.work / "worklist.provenance.jsonl").exists())

    def test_rule_and_finding_forms_are_accepted(self):
        for cell in ('N #R1', 'Н-1', 'D Н-1', 'H-1', 'Н 1', 'Н—1'):
            r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                            "--corpus", self.corpus, "--id", "g001",
                            "--cell", cell, input="")
            self.assertEqual(r.returncode, 0, cell + ": " + r.stderr)

    def test_bad_range_and_invalid_id_after_valid_citation_leave_no_genesis(self):
        for payload in ('missing\tN Security.jsonl:1 «{"EventID":4625,"TargetUserName":"alice"}»\n',):
            r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                            "--corpus", self.corpus, "--from-stdin", input=payload)
            self.assertNotEqual(r.returncode, 0, payload)
            self.assertFalse((self.work / "worklist.provenance.jsonl").exists())

    def test_invalid_cell_with_strong_valid_citation_leaves_no_genesis(self):
        r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                        "--corpus", self.corpus, "--from-stdin",
                        input='g001\tBOGUS Security.jsonl:1 «{"EventID":4625,"TargetUserName":"alice"}»\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.work / "worklist.provenance.jsonl").exists())

    def test_closed_cells_without_supported_reference_are_rejected(self):
        for cell in ("N nonsense 1", "X arbitrary reason"):
            r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                            "--corpus", self.corpus, "--id", "g001",
                            "--cell", cell, input="")
            self.assertNotEqual(r.returncode, 0, cell)
            self.assertFalse((self.work / "worklist.provenance.jsonl").exists())

    def test_range_quote_on_second_line_is_accepted(self):
        r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                        "--corpus", self.corpus, "--id", "g001", "--cell",
                        'N Security.jsonl:1-2 «{"EventID":4624,"Message":"Unique failure Alpha"}»', input="")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_corpus_is_required_and_must_be_external_existing_directory(self):
        r = self.invoke(WORKLIST, "verdict", "--work", self.work, "--id", "g001",
                        "--cell", 'N Security.jsonl:1 "EventID":4625', input="")
        self.assertNotEqual(r.returncode, 0)
        r = self.invoke(WORKLIST, "verdict", "--work", self.work,
                        "--corpus", self.tmp / "missing", "--id", "g001",
                        "--cell", 'N Security.jsonl:1 "EventID":4625', input="")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
