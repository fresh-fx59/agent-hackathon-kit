import importlib.util
import json
import tempfile
import unittest
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "skills" / "v65" / "tools"

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

checkpoint = load("checkpoint")
journalcheck = load("journalcheck")


class TrustBoundaryV65Test(unittest.TestCase):
    def test_package_digest_ignores_generated_cache(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td)
            (package / "tools").mkdir()
            (package / "tools" / "x.py").write_text("x = 1\n")
            before = checkpoint._package_sha256(package)
            (package / "tools" / "__pycache__").mkdir()
            (package / "tools" / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"generated")
            self.assertEqual(before, checkpoint._package_sha256(package))

    def test_receipt_metadata_is_parsed_from_captured_tool_output(self):
        use = {"id": "u1", "input": {"command": "checkpoint.py handoff --done draft"}}
        text = ("RECEIPT id=" + "a" * 32 + " sha256=" + "b" * 64 + " seq=1\n" +
                "RECEIPT_META id=" + "a" * 32 + " report_sha256=" + "c" * 64 +
                " package_sha256=" + "d" * 64 + " work_sha256=" + "e" * 64 +
                " structure_sha256=" + "f" * 64)
        got = journalcheck.printed_receipts([("stream", 1, use)], {"u1": text})
        self.assertEqual(got["a" * 32]["receipt"], ("b" * 64, 1))
        self.assertEqual(got["a" * 32]["meta"]["report_sha256"], "c" * 64)

    def test_forged_metadata_is_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "report.md").write_text("report\n", encoding="utf-8")
            rid = "a" * 32
            row = {"boundary_seq": 1, "handoff_receipt_id": rid,
                   "handoff_sha256": "b" * 64, "report_sha256": "c" * 64,
                   "package_sha256": "d" * 64, "work_sha256": "e" * 64,
                   "structure_sha256": "f" * 64}
            (work / "checkpoint.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            stream = work / "stream.jsonl"
            stream.write_text(json.dumps({"message": {"content": [
                {"type": "tool_use", "id": "u1", "input": {"command": "checkpoint.py handoff"}},
                {"type": "tool_result", "tool_use_id": "u1", "content":
                 "RECEIPT id=%s sha256=%s seq=1\nRECEIPT_META id=%s report_sha256=%s package_sha256=%s work_sha256=%s structure_sha256=%s"
                 % (rid, "b" * 64, rid, "c" * 64, "d" * 64, "e" * 64, "f" * 64)}]}}) + "\n")
            result = journalcheck.check(str(work), [str(stream)], work / "report.md", ROOT.parent)
            self.assertTrue(result["blocking"])
            self.assertTrue(any(v["code"] == "journal_no_authority" for v in result["violations"]))

    def test_altered_structure_is_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            report = work / "report.md"
            report.write_text("report\n", encoding="utf-8")
            rid = "a" * 32
            row = {"boundary_seq": 1, "handoff_receipt_id": rid,
                   "handoff_sha256": "b" * 64, "report_sha256": checkpoint._file_sha256(report),
                   "package_sha256": checkpoint._package_sha256(ROOT.parent),
                   "work_sha256": __import__("hashlib").sha256(str(work.resolve()).encode()).hexdigest(),
                   "structure_sha256": "f" * 64, "worklist_authority": {}}
            (work / "checkpoint.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            stream = work / "stream.jsonl"
            meta = "RECEIPT_META id=%s report_sha256=%s package_sha256=%s work_sha256=%s structure_sha256=%s" % (rid, row["report_sha256"], row["package_sha256"], row["work_sha256"], row["structure_sha256"])
            stream.write_text(json.dumps({"message": {"content": [{"type": "tool_use", "id": "u", "input": {"command": "checkpoint.py handoff"}}, {"type": "tool_result", "tool_use_id": "u", "content": "RECEIPT id=%s sha256=%s seq=1\n%s" % (rid, row["handoff_sha256"], meta)}]}}) + "\n")
            result = journalcheck.check(str(work), [str(stream)], report, ROOT.parent)
            self.assertTrue(any(v["code"] == "structure_digest" for v in result["violations"]))

    def test_replayed_boundary_sequence_is_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            rows = [{"boundary_seq": 1, "handoff_receipt_id": "a" * 32, "handoff_sha256": "b" * 64, "prev_sha256": None, "worklist_authority": {}},
                    {"boundary_seq": 1, "handoff_receipt_id": "c" * 32, "handoff_sha256": "d" * 64, "prev_sha256": None, "worklist_authority": {}}]
            (work / "checkpoint.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n")
            self.assertTrue(any(code == "journal_seq" for code, _ in checkpoint.verify_journal(work)))


if __name__ == "__main__":
    unittest.main()
