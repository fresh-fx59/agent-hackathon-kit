#!/usr/bin/env python3
"""The model under test is bound in the manifest and labelled; unknown ids are refused."""
import importlib.util, json, shutil, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
def load():
    spec = importlib.util.spec_from_file_location("runner_model", HERE / "run-v52-gpt55-comparison.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
BASE = importlib.util.spec_from_file_location("tbase", HERE / "test_run_v52_gpt55_comparison.py")


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.R = load()
        self.root = Path(tempfile.mkdtemp(prefix="model-test-"))
        t = importlib.util.module_from_spec(BASE); BASE.loader.exec_module(t)
        t.RUNNER = self.R
        helper = t.TerminalClassificationTest("test_complete_exact_identity_ledger_is_accepted")
        helper.root = self.root
        self.prepared = helper.prepared_root()
        for name in ("proxy.py", "lane_guard.py", "qwen"):
            (self.root / name).write_text("# stub")
    def tearDown(self):
        shutil.rmtree(self.root)

    def prep(self, model):
        args = SimpleNamespace(prepared_root=str(self.prepared), control_root=str(self.root / ("c-" + model)),
                               qwen=str(self.root / "qwen"), proxy=str(self.root / "proxy.py"),
                               upstream_base="http://127.0.0.1:8317/v1", authorization="ok", model=model)
        self.R.prepare(args)
        return json.loads((Path(args.control_root) / "manifest.json").read_text()), Path(args.control_root)

    def test_claude_run_is_labelled_not_comparable_and_binds_identity(self):
        m, control = self.prep("claude-opus-5")
        self.assertEqual((m["model"], m["expected_returned_identity"]), ("claude-opus-5", "claude-opus-5"))
        self.assertEqual(m["model_under_test"], {"model": "claude-opus-5", "family": "anthropic-claude",
                                                 "comparable_to_gpt55_r4": False})
        self.assertIsNone(self.R.validate_manifest(control, m))
        rows = [{"status": 200, "returned_model": "claude-opus-5", "stream": True, "stream_complete": True}]
        (self.root / "t").mkdir(); (self.root / "t/upstream.jsonl").write_text(json.dumps(rows[0]) + "\n")
        self.assertEqual(len(self.R.validate_terminal_ledger(self.root / "t")), 1)
        (self.root / "t/upstream.jsonl").write_text(json.dumps(dict(rows[0], returned_model="gpt-5.5")) + "\n")
        with self.assertRaisesRegex(self.R.TerminalFailure, "identity"):
            self.R.validate_terminal_ledger(self.root / "t")

    def test_default_stays_gpt55_and_unknown_model_refused(self):
        m, _ = self.prep("gpt-5.5")
        self.assertTrue(m["model_under_test"]["comparable_to_gpt55_r4"])
        with self.assertRaisesRegex(self.R.Refusal, "not admitted"):
            self.prep("claude-opus-5-5")

    def test_tampered_label_is_refused(self):
        m, control = self.prep("claude-opus-5")
        m["model_under_test"]["comparable_to_gpt55_r4"] = True
        with self.assertRaisesRegex(self.R.Refusal, "strict target identity"):
            self.R.validate_manifest(control, m)


if __name__ == "__main__":
    unittest.main()
