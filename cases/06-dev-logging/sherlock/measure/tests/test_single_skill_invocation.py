#!/usr/bin/env python3
"""The interactive driver sends one skill invocation per fresh session."""
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "single_invocation_drive", ROOT / "measure" / "interactive-drive.py")
DRIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVE)


class SingleSkillInvocationTest(unittest.TestCase):
    def test_composes_unprefixed_arguments_without_rewriting_multiline_text(self):
        arguments = "Investigate fresh corpus\n\nretain this spacing\n"
        self.assertEqual(
            DRIVE.skill_invocation("/sherlock", arguments),
            "/sherlock " + arguments)

    def test_preserves_exact_existing_prefix_without_duplicate_command(self):
        invocation = "/sherlock\n\nInvestigate fresh corpus\n"
        self.assertEqual(DRIVE.skill_invocation("/sherlock", invocation), invocation)

    def test_requires_nonempty_arguments_and_only_recognizes_exact_prefix(self):
        for arguments in ("", "   ", "/sherlock", "/sherlock   "):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    DRIVE.skill_invocation("/sherlock", arguments)
        self.assertEqual(
            DRIVE.skill_invocation("/sherlock", "/sherlock-other task"),
            "/sherlock /sherlock-other task")

    def test_rejects_cr_and_other_unsupported_control_input(self):
        for arguments in ("/sherlock\r\nTask", "task\x00", "task\x1b",
                          "task\rmore"):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    DRIVE.skill_invocation("/sherlock", arguments)

    def test_reads_skill_body_after_frontmatter_and_binds_its_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = "# Sherlock\n\nFollow the durable stage protocol.\n"
            (root / "SKILL.md").write_text(
                "---\nname: sherlock\n---\n" + body, encoding="utf-8")
            actual_body, body_sha = DRIVE.skill_body(root)
        self.assertEqual(actual_body, body)
        self.assertEqual(body_sha, hashlib.sha256(body.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
