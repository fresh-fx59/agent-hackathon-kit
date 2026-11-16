"""Provider-free regression for the v65 repair receipt crash and silent omissions."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

CHECK = pathlib.Path(__file__).resolve().parents[2] / "skills/v66/tools/check.py"
spec = importlib.util.spec_from_file_location("check_v66", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ReceiptTest(unittest.TestCase):
    def test_numeric_ledger_and_every_blocking_family(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            attempt = root / "work" / "validation" / "attempt"
            attempt.mkdir(parents=True)
            (root / "work" / "worklist.tsv").write_text("id\tstatus\n1\tclosed\n")
            (root / "work" / "report.md").write_text("report\n")
            (attempt / "metadata.json").write_text(json.dumps({"gates": {
                "citecheck": {"parsed_blocking": 6}, "reportcheck": {"parsed_blocking": 1},
                "statecheck": {"parsed_blocking": 0}, "triagecheck": {"parsed_blocking": 0}}}))
            cite = {
                "citations": [{"verdict": "ok"}, {"verdict": "typed-quote", "report_line": 3}],
                "aggregates": {"items": [{"verdict": "ok"}, {"verdict": "malformed", "report_line": 4}]},
                "report_evidence": {"rejected": {"invalid_citation": ["К-2"]},
                                    "ownership": {"missing_rows": ["user1"]},
                                    "rollover": {"summary_missing": True}},
                "ledger": {"rows": 250, "unresolved_total": 6, "removed_rows": ["row-2"]}}
            (attempt / "citecheck.stdout").write_text(json.dumps(cite))
            (attempt / "reportcheck.stdout").write_text(json.dumps({"defects": [
                {"defect": "absolute_uncited", "line": 5, "detail": "claim"}]}))
            receipt = check.diagnostics(attempt)
            self.assertEqual(receipt["defect_total"], 7)
            self.assertEqual(receipt["ledger"], {"unresolved_total": 6, "open_worklist_rows": 0})
            self.assertEqual([x["kind"] for x in receipt["defects"]], [
                "defects", "citations", "aggregates", "ledger.removed_rows",
                "report_evidence.rejected.invalid_citation", "report_evidence.ownership.missing_rows",
                "report_evidence.rollover.summary_missing"])
            self.assertTrue(all(x["source"].startswith("/") and x["detail"] for x in receipt["defects"]))
            self.assertEqual(receipt, check.diagnostics(attempt))
            self.assertEqual(sum(len(receipt["defects"][i:i+2]) for i in range(0, 7, 2)), 7)


if __name__ == "__main__":
    unittest.main()
