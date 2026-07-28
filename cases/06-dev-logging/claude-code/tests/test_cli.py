import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, tempfile, json, io
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

    def test_auto_adopt_refuses_git_only_cwd(self):
        """Finding 4: a cwd carrying ONLY a .git dir must not be silently
        auto-adopted as the repo — it should still ask for clarification,
        same as any ordinary git clone with no code checked out yet."""
        git_only = self.root / "gitonly"
        (git_only / ".git").mkdir(parents=True)
        prev_cwd = Path.cwd()
        os.chdir(git_only)
        try:
            code, out = self._run(["investigate", "--logs", str(self.logs),
                                   "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
                                   "--out", str(self.out_json), "--case-dir", str(self.root),
                                   "--suggest-from", str(self.root)])
        finally:
            os.chdir(prev_cwd)
        self.assertEqual(code, 3)
        clar = json.loads(out)
        self.assertIn("question", clar)

    def test_auto_adopt_with_src_marker_proceeds_dev_mode(self):
        """Finding 4: a cwd with a genuine code marker (src/) auto-adopts
        and announces it on stdout before proceeding."""
        coderepo = self.root / "codehere"
        (coderepo / "src").mkdir(parents=True)
        prev_cwd = Path.cwd()
        os.chdir(coderepo)
        try:
            code, out = self._run(["investigate", "--logs", str(self.logs),
                                   "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
                                   "--out", str(self.out_json), "--case-dir", str(self.root),
                                   "--suggest-from", str(self.root)])
        finally:
            os.chdir(prev_cwd)
        self.assertEqual(code, 0)
        self.assertIn("auto-adopted repo:", out)
        rep = json.loads(self.out_json.read_text(encoding="utf-8"))
        self.assertEqual(rep["mode"], "dev")

if __name__ == "__main__":
    unittest.main()
