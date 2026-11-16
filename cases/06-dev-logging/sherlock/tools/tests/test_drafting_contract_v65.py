import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "skills" / "v65" / "tools" / "citecheck.py"
spec = importlib.util.spec_from_file_location("citecheck_v65", HERE)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
dc_spec = importlib.util.spec_from_file_location("draftcheck_v65", HERE.parent / "draftcheck.py")
dc = importlib.util.module_from_spec(dc_spec)
dc_spec.loader.exec_module(dc)


class DraftingContractV65Test(unittest.TestCase):
    def test_separator_is_required_and_diagnostic_is_explicit(self):
        report = "### Н-1 без разделителя\n- исход: успех\n### Н-2 · canonical\n- исход: норма\n"
        bad = cc.malformed_finding_headers(report)
        self.assertEqual([x["report_line"] for x in bad], [1])
        self.assertEqual([x[0] for x in cc.finding_blocks(report)], ["2"])

    def test_canonical_heading_is_recognized(self):
        report = "### Н-1 · событие\n- исход: успех\n### К-1 · кандидат\n- исход: норма\n"
        self.assertEqual(len(cc.finding_blocks(report)), 1)
        self.assertEqual(cc.malformed_finding_headers(report), [])

    def test_json_check_exposes_structure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "logs"
            root.mkdir()
            (root / "a.log").write_text("ok\n", encoding="utf-8")
            report = "### Н-1 без разделителя\n- исход: успех\n"
            result = cc.check(report, root)
            self.assertEqual(result["summary"]["malformed_headings"], 1)
            self.assertEqual(result["structure"]["malformed_headings"][0]["error"],
                             "missing_heading_separator")

    def test_draftcheck_rejects_missing_fields(self):
        result = dc.check("### Н-1 · event\n- исход: успех\n")
        self.assertFalse(result["ok"])
        self.assertTrue(any(x["code"] == "field_count" for x in result["errors"]))


if __name__ == "__main__":
    unittest.main()
