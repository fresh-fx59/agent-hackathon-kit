import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
GATE = HERE / "triagegate.py"


class TriageGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.pkg = self.root / "package"
        self.run = self.root / "run"
        self.work.mkdir()
        (self.pkg / "tools").mkdir(parents=True)
        (self.run / "out" / "investigate").mkdir(parents=True)
        (self.work / "worklist.tsv").write_text("# id\tverdict\n1\tN\n")
        (self.work / "rules.tsv").write_text("# rules\n")
        (self.root / "corpus").mkdir()
        self.triage = self.pkg / "tools" / "triagecheck.py"
        self.triage.write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.exit(0 if '--fail' not in open('" +
            str(self.root / "mode") + "').read() else 1)\n")
        self.triage.chmod(0o755)
        checkpoint = self.pkg / "tools" / "checkpoint.py"
        checkpoint.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        checkpoint.chmod(0o755)
        (self.root / "mode").write_text("ok")

    def tearDown(self):
        self.tmp.cleanup()

    def checkpoint(self, valid=True, consumed=False):
        receipt = {"state": "pending", "from_stage": "triage",
                   "to_stage": "draft"} if valid else {
                       "state": "pending", "from_stage": "draft", "to_stage": "repair"}
        if consumed and valid:
            receipt.update(state="consumed", stop_event_sha256="a" * 64,
                           stop_session_id="session-1")
        (self.work / "checkpoint.json").write_text(json.dumps(
            {"stage": "draft" if valid else "triage", "pending_handoff": receipt}))

    def stream(self, agent=True):
        text = {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "name": "agent" if agent else "read_file",
            "id": "agent-call-1",
            "input": {"subagent_type": "sherlock-triage",
                      "run_in_background": False}}]}}
        result = {"type": "user", "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "agent-call-1",
            "is_error": False}]}}
        (self.run / "out" / "investigate" / "stream.jsonl").write_text(
            json.dumps(text) + "\n" + json.dumps(result) + "\n")

    def failed_agent_stream(self):
        self.stream()
        path = self.run / "out" / "investigate" / "stream.jsonl"
        lines = path.read_text().splitlines()
        lines[-1] = json.dumps({"type": "user", "message": {"role": "user",
                                "content": [{"type": "tool_result",
                                             "tool_use_id": "agent-call-1",
                                             "is_error": True}]}})
        path.write_text("\n".join(lines) + "\n")

    def invoke(self):
        return subprocess.run(
            [sys.executable, str(GATE), "--work", str(self.work),
             "--corpus", str(self.root / "corpus"), "--package", str(self.pkg),
             "--run-dir", str(self.run)], capture_output=True, text=True)

    def test_valid_handoff_and_agent_pass(self):
        self.checkpoint()
        self.stream()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["pass"])

    def test_consumed_handoff_after_stop_hook_passes(self):
        self.checkpoint(consumed=True)
        self.stream()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_agent_fails_closed(self):
        self.checkpoint()
        self.stream(agent=False)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["pass"])

    def test_failed_agent_fails_closed(self):
        self.checkpoint()
        self.failed_agent_stream()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["pass"])

    def test_invalid_handoff_fails_closed(self):
        self.checkpoint(valid=False)
        self.stream()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["pass"])

    def test_failed_triage_fails_closed(self):
        self.checkpoint()
        self.stream()
        (self.root / "mode").write_text("--fail")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["pass"])


if __name__ == "__main__":
    unittest.main()
