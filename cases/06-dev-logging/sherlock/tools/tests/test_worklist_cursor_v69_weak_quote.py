"""Provider-free v69 red test for weak citation writes at the cursor boundary."""

import pathlib
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKLIST = ROOT / "skills" / os.environ.get("SHERLOCK_PKG", "v69") / "tools" / "worklist.py"


def make_work(root):
    work = root / "work"
    corpus = work / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "sample.jsonl").write_text(
        '{"EventID":4625,"status":"failed"}\n'
        '{"EventID":4624,"status":"success"}\n',
        encoding="utf-8",
    )
    (work / "worklist.tsv").write_text(
        "# id\tverdict\taxis\tpath\tfrequency\n"
        "N\t\tauth\tsample.jsonl\t1\n",
        encoding="utf-8",
    )
    return work


def verdict(work, cell):
    return subprocess.run(
        [sys.executable, str(WORKLIST), "verdict", "--work", str(work),
         "--id", "N", "--cell", cell],
        text=True,
        capture_output=True,
    )


class CursorCitationBoundaryV67Test(unittest.TestCase):
    def test_weak_quote_is_rejected_atomically_and_strong_quote_is_written(self):
        with tempfile.TemporaryDirectory() as td:
            weak_work = make_work(pathlib.Path(td) / "weak")
            ledger = weak_work / "worklist.tsv"
            before = ledger.read_bytes()

            weak = verdict(weak_work, 'N sample.jsonl:1 "EventID":4625')

            # v69 must reject the weak quote before changing either witness file.
            self.assertNotEqual(weak.returncode, 0, weak.stdout + weak.stderr)
            self.assertEqual(ledger.read_bytes(), before)
            self.assertFalse((weak_work / "worklist.provenance.jsonl").exists())

            strong_work = make_work(pathlib.Path(td) / "strong")
            strong = verdict(strong_work, 'N sample.jsonl:1 "EventID":4625,"status":"failed"')
            self.assertEqual(strong.returncode, 0, strong.stdout + strong.stderr)
            self.assertIn('"written": 1', strong.stdout)
            self.assertIn('EventID":4625,"status":"failed"',
                          (strong_work / "worklist.tsv").read_text())
            self.assertTrue((strong_work / "worklist.provenance.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
