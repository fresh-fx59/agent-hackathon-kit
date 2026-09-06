#!/usr/bin/env python3
"""Actual-Qwen, loopback-only replay of the v51 workspace-root command form."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT / "tools/tests/qwen_post_tool_batch_capture.py"
QWEN = Path(os.environ.get("QWEN_BIN") or shutil.which("qwen") or
            Path.home() / ".local/bin/qwen")


@unittest.skipUnless(QWEN.is_file() and os.access(QWEN, os.X_OK), "Qwen CLI not installed")
class WorkspaceRootReplayTest(unittest.TestCase):
    def test_replay_preserves_hook_capture_when_given_a_relative_output_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            completed = subprocess.run(
                [sys.executable, str(REPLAY), "--qwen", str(QWEN), "--output", "evidence",
                 "--mode", "workspace-root-resume"],
                cwd=root, text=True, capture_output=True, timeout=60, check=False)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertTrue((root / "evidence/post-tool-batch.stdin.json").is_file())

    def test_absolute_tool_and_relative_work_run_from_default_workspace_without_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "evidence"
            completed = subprocess.run(
                [sys.executable, str(REPLAY), "--qwen", str(QWEN), "--output", str(out),
                 "--mode", "workspace-root-resume"],
                text=True, capture_output=True, timeout=60, check=False)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(0, summary["exit_code"])
            call = summary["tool_calls"][0]
            self.assertEqual("run_shell_command", call["tool_name"])
            self.assertNotIn("directory", call["tool_input"])
            self.assertTrue((out / "workspace/work/resume-receipt.json").is_file())
            receipt = json.loads((out / "workspace/work/resume-receipt.json").read_text())
            self.assertEqual(str((out / "workspace").resolve()), receipt["cwd"])


if __name__ == "__main__":
    unittest.main()
