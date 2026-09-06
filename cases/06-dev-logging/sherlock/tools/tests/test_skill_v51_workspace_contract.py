#!/usr/bin/env python3
"""Regression for the Qwen project-workspace contract in runtime v51."""
import importlib.util
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
REGISTRY = ROOT / "eval/bench/skill-version-registry.json"


def load_gate():
    spec = importlib.util.spec_from_file_location("workspace_contract_version_gate",
                                                  ROOT / "eval/bench/version-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkspaceContractV51Test(unittest.TestCase):
    def test_v50_remains_registered_at_its_existing_digest(self):
        gate = load_gate()
        versions = json.loads(REGISTRY.read_text(encoding="utf-8"))["versions"]
        self.assertEqual("0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7",
                         versions["v50"])
        self.assertEqual(versions["v50"], gate.tree_digest(SKILLS / "v50"))

    def test_v51_binds_relative_work_to_the_default_registered_workspace(self):
        gate = load_gate()
        versions = json.loads(REGISTRY.read_text(encoding="utf-8"))["versions"]
        text = (SKILLS / "v51" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"Qwen's default\s+registered project workspace")
        self.assertIn("omit `directory`", text)
        self.assertRegex(text, r"Never infer a command directory from\s+`<SKILL_BASE_DIR>`")
        self.assertRegex(text, r"`\./work`\s+means `<workspace>/work`")
        self.assertIn("v51", versions)
        self.assertEqual(versions["v51"], gate.tree_digest(SKILLS / "v51"))
        verified = gate.verify_version("v51", SKILLS)
        self.assertEqual("v51", verified.version)
        self.assertEqual(versions["v51"], verified.digest)


if __name__ == "__main__":
    unittest.main()
