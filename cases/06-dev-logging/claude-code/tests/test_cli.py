import unittest, tempfile, json, sys, io
from pathlib import Path
from contextlib import redirect_stdout
from logalyzer.__main__ import main
import tests.test_ingest_lines as fixtures

class TestCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.logs = self.root / "logs"; self.logs.mkdir()
        (self.logs / "order-service.log").write_text(fixtures.ORDER_JSONL, encoding="utf-8")
        (self.logs / "payment-service.log").write_text(fixtures.PAYMENT_PLAIN, encoding="utf-8")
        self.out_json = self.root / "report.json"

    def tearDown(self):
        self.dir.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_investigate_ops_mode_writes_report(self):
        code, _ = self._run(["investigate", "--logs", str(self.logs),
                             "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
                             "--mode", "ops", "--out", str(self.out_json),
                             "--case-dir", str(self.root)])
        self.assertEqual(code, 0)
        rep = json.loads(self.out_json.read_text(encoding="utf-8"))
        self.assertEqual(rep["mode"], "ops")
        self.assertIn("R-ORD-001", [rep["classification"]["type"]])
        self.assertIsNone(rep["root_cause"]["file"])
        runs = (self.root / "docs" / "runs.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(runs), 1)
        self.assertIn("R-ORD-001", runs[0])

    def test_investigate_auto_mode_without_code_asks(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
                               "--out", str(self.out_json), "--case-dir", str(self.root),
                               "--suggest-from", str(self.root)])
        self.assertEqual(code, 3)
        clar = json.loads(out)
        self.assertIn("question", clar)
        self.assertIn("без кода", clar["question"])

if __name__ == "__main__":
    unittest.main()
