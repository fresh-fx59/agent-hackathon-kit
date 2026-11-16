"""Executable provider-free stage continuation contract for v65."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path


SHERLOCK = Path(__file__).resolve().parents[2]
PACKAGE = SHERLOCK / "skills" / "v65"
CHECK = PACKAGE / "tools" / "check.py"
_SPEC = importlib.util.spec_from_file_location("check_v65_cap", CHECK)
CHECK_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECK_MOD)


class V65FreshSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / "work"
        self.corpus = self.root / "corpus"
        self.work.mkdir()
        self.corpus.mkdir()
        (self.work / "worklist.tsv").write_text("# id\tverdict\n", encoding="utf-8")
        marker = self.root / ".sherlock" / "active.json"
        marker.parent.mkdir()
        marker.write_text(json.dumps({"version": 36, "active": True,
            "workspace": str(self.root), "skill_root": str(PACKAGE.resolve()),
            "corpus": str(self.corpus), "out": str(self.work), "mode": "single",
            "worklists": ["worklist.tsv"]}) + "\n", encoding="utf-8")
        self.run_checkpoint("init")
        self.run_checkpoint("handoff", "triage")

    def tearDown(self):
        self.tmp.cleanup()

    def run_checkpoint(self, command, done=None, partial=False):
        args = [sys.executable, str(PACKAGE / "tools" / "checkpoint.py"),
                command, "--work", str(self.work)]
        if done:
            args += ["--done", done]
        if partial:
            args.append("--partial")
        result = subprocess.run(args, check=False, capture_output=True, text=True, cwd=self.root)
        if result.returncode:
            raise AssertionError(result.stderr or result.stdout)
        return result

    def stop(self, session):
        return subprocess.run([sys.executable, str(PACKAGE / "tools" / "stopcheck.py")],
            input=json.dumps({"cwd": str(self.root), "hook_event_name": "Stop",
                              "session_id": session, "last_assistant_message": "handoff"}),
            cwd=self.root, env={**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)},
            text=True, capture_output=True, check=False)

    def test_draft_and_each_repair_round_use_fresh_sessions_and_keep_artifacts(self):
        sessions = ("triage-session", "draft-session", "repair-1-session", "repair-2-session")
        self.assertEqual(len(sessions), len(set(sessions)))
        self.assertEqual(json.loads(self.stop(sessions[0]).stdout)["decision"], "allow")
        self.run_checkpoint("init")
        (self.work / "report.md").write_text("""# Инвентарь
## Пропущенные данные
## Покрытие
## Окно записей
| путь | канал | окно | записи | нет |
| a.log | x | окно: 1–2 | записей: 2 | нет: 0 |
## Вердикт
| rare | new | peak | volume | sequence |
### Н-1 · событие
- исход: успех
- атрибуция: установлена
### К-1 · кандидат
- исход: норма
""", encoding="utf-8")
        self.run_checkpoint("handoff", "draft")
        self.assertEqual(json.loads(self.stop(sessions[1]).stdout)["decision"], "allow")
        self.run_checkpoint("init")
        (self.work / "validation").mkdir()
        (self.work / "validation" / "receipt.json").write_text("{}\n", encoding="utf-8")
        self.run_checkpoint("handoff", "repair", partial=True)
        self.assertEqual(json.loads(self.stop(sessions[2]).stdout)["decision"], "allow")
        self.assertTrue((self.work / "report.md").is_file())
        self.assertTrue((self.work / "validation" / "receipt.json").is_file())
        self.run_checkpoint("init")
        self.run_checkpoint("handoff", "repair", partial=True)
        self.assertEqual(json.loads(self.stop(sessions[3]).stdout)["decision"], "allow")
        self.assertTrue((self.work / "report.md").is_file())
        self.assertTrue((self.work / "validation" / "receipt.json").is_file())

    def test_repair_phase_allows_two_checks_and_rejects_third(self):
        self.assertEqual(CHECK_MOD.record_repair_check(self.work, "repair-1"), 1)
        self.assertEqual(CHECK_MOD.record_repair_check(self.work, "repair-1"), 2)
        with self.assertRaisesRegex(RuntimeError, "two-check cap"):
            CHECK_MOD.record_repair_check(self.work, "repair-1")
        self.assertEqual(CHECK_MOD.record_repair_check(self.work, "repair-2"), 1)

    def test_normal_check_derives_repair_phase_from_checkpoint(self):
        (self.work / "checkpoint.json").write_text(
            json.dumps({"stage": "repair", "boundary_seq": 9}) + "\n",
            encoding="utf-8")
        for _ in range(2):
            subprocess.run([sys.executable, str(CHECK), "--work", str(self.work),
                            "--corpus", str(self.corpus)], cwd=self.root,
                           capture_output=True, text=True, check=False)
        blocked = subprocess.run([sys.executable, str(CHECK), "--work", str(self.work),
                                  "--corpus", str(self.corpus)], cwd=self.root,
                                 capture_output=True, text=True, check=False)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("two-check cap", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
