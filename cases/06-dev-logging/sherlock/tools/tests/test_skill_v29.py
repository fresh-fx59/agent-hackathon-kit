#!/usr/bin/env python3
"""Provider-free version contract for the Sherlock v29 skill copy."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
SKILL = SHERLOCK / "skills" / "v29"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillV29VersionContract(unittest.TestCase):
    def test_logmap_records_version_29(self):
        self.assertTrue(SKILL.is_dir(), "skills/v29 is missing")
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            corpus = workspace / "corpus"
            corpus.mkdir()
            (corpus / "app.log").write_text(
                "2026-08-21T00:00:00Z ERROR sample\n", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(SKILL / "tools" / "logmap.py"),
                 str(corpus), "--out", "work", "--jobs", "1"],
                cwd=workspace, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            marker = json.loads(
                (workspace / ".sherlock" / "active.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["version"], 29)
            self.assertEqual(marker["skill_root"], str(SKILL.resolve()))

    def test_stopcheck_accepts_version_29_marker(self):
        self.assertTrue(SKILL.is_dir(), "skills/v29 is missing")
        stopcheck = load_module(SKILL / "tools" / "stopcheck.py", "stopcheck_v29")
        with tempfile.TemporaryDirectory() as raw:
            workspace = str(Path(raw).resolve())
            marker = {
                "version": 29,
                "active": True,
                "workspace": workspace,
                "skill_root": str(SKILL.resolve()),
                "corpus": str(Path(raw, "corpus")),
                "out": str(Path(raw, "work")),
                "mode": "single",
                "worklists": ["worklist.tsv"],
            }
            self.assertTrue(stopcheck.validate_active_marker(marker, workspace))


if __name__ == "__main__":
    unittest.main(verbosity=2)
