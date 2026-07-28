import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, tempfile, json, zipfile, io
from pathlib import Path
from contextlib import redirect_stdout
from logalyzer.__main__ import main

CASE = Path(__file__).resolve().parents[1]
PACK = CASE.parent / "petstore_input_pack.zip"
CORR = "c-8f3a2b91-4d7c-11ee-b962-0242ac120002"

@unittest.skipUnless(PACK.is_file(), "pack zip not present")
class TestE2EPack(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        with zipfile.ZipFile(PACK) as z:
            z.extractall(self.root)
        self.pack = next(self.root.glob("**/logs")).parent
        # Rules always load from the tool's own rules/rules.json (_CASE_DIR);
        # --case-dir only controls run-log artifact placement (Task 10 decision).
        # This copy is therefore redundant for correctness, kept harmless anyway
        # so the temp case dir is self-contained if that ever changes.
        case = self.root / "case"; (case / "rules").mkdir(parents=True)
        (case / "rules" / "rules.json").write_bytes(
            (CASE / "rules" / "rules.json").read_bytes())

    def tearDown(self):
        self.dir.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_dev_mode_finds_checkout_catch_block(self):
        out = self.root / "report.json"
        code, _ = self._run(["investigate", "--logs", str(self.pack / "logs"),
                             "--correlation-id", CORR,
                             "--repo", str(self.pack / "repo"),
                             "--out", str(out), "--case-dir", str(self.root / "case")])
        self.assertEqual(code, 0)
        rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rep["mode"], "dev")
        self.assertIn("И-1", rep["invariant_violations"])
        self.assertTrue(rep["root_cause"]["file"] and
                        rep["root_cause"]["file"].endswith(".java"))
        self.assertNotEqual(rep["root_cause"]["method"], "handleReservationTimeout")
        self.assertGreater(len(rep["evidence"]), 5)

    def test_ops_mode_no_code_claims(self):
        out = self.root / "report-ops.json"
        code, _ = self._run(["investigate", "--logs", str(self.pack / "logs"),
                             "--correlation-id", CORR, "--mode", "ops",
                             "--out", str(out), "--case-dir", str(self.root / "case")])
        self.assertEqual(code, 0)
        rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(rep["root_cause"]["file"])
        self.assertEqual(rep["code_recommendations"], [])

if __name__ == "__main__":
    unittest.main()
