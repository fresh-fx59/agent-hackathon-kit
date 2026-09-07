#!/usr/bin/env python3
"""Regression for cursor provenance with stopcheck's composite ledger."""
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


SHERLOCK = Path(__file__).resolve().parents[2]
V52 = SHERLOCK / "skills" / "v52"


def load_stopcheck():
    spec = importlib.util.spec_from_file_location(
        "stopcheck_v52_under_test", V52 / "tools" / "stopcheck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_worklist():
    spec = importlib.util.spec_from_file_location(
        "worklist_v52_under_test", V52 / "tools" / "worklist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompositeLedgerTriage(unittest.TestCase):
    def test_composite_is_unwitnessed_while_source_witness_verifies(self):
        stopcheck = load_stopcheck()
        worklist_api = load_worklist()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            work = root / "work"
            work.mkdir()
            source = work / "worklist.tsv"
            source.write_text("# id\tвердикт\ng001\tD Н-1\n", encoding="utf-8")
            (work / "worklist.manifest.json").write_text("{}\n", encoding="utf-8")
            worklist_api.append_entry(str(source), "genesis",
                                      rows=worklist_api.verdict_snapshot(str(source)))
            ledger, temporary, stage = stopcheck.compose_stopcheck_worklists(
                [str(source)], str(work))
            try:
                self.assertEqual("ok", worklist_api.audit(str(source))["state"])
                self.assertEqual("unwitnessed", worklist_api.audit(ledger)["state"])
            finally:
                Path(temporary).unlink()
                Path(stage).rmdir()

    def test_triage_keeps_global_ledger_and_verifies_source_witness(self):
        stopcheck = load_stopcheck()
        calls = []

        class Good:
            returncode = 0
            stdout = ""
            stderr = ""

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            work = root / "work"
            corpus = root / "corpus"
            work.mkdir()
            corpus.mkdir()
            worklist = work / "worklist.tsv"
            worklist.write_text("# id\tвердикт\nrow\tD Н-1\n", encoding="utf-8")
            (work / "rules.tsv").write_text("", encoding="utf-8")
            report = work / "report.md"
            report.write_text("report\n", encoding="utf-8")

            prior = stopcheck.run_child
            stopcheck.run_child = lambda argv, deadline: calls.append(argv) or Good()
            try:
                reason = stopcheck.check_children(
                    str(corpus), str(work), str(report), [str(worklist)],
                    str(V52), str(root), time.monotonic() + 20)
            finally:
                stopcheck.run_child = prior

        self.assertIsNone(reason)
        self.assertEqual(4, len(calls))
        witness, triage, cite, reportcheck = calls
        self.assertIn("worklist.py", witness[1])
        self.assertEqual(str(work), witness[witness.index("--work") + 1])
        self.assertEqual(worklist.name, witness[witness.index("--ledger") + 1])
        triage_ledger = triage[triage.index("--worklist") + 1]
        self.assertIn(".stopcheck-ledger-", triage_ledger)
        self.assertNotEqual(str(worklist), triage_ledger)
        self.assertIn(".stopcheck-ledger-", cite[cite.index("--ledger") + 1])
        self.assertEqual(triage_ledger, cite[cite.index("--ledger") + 1])


if __name__ == "__main__":
    unittest.main()
