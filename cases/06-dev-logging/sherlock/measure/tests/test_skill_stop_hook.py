#!/usr/bin/env python3
"""The run settings must install Stop outside Qwen's slash-skill path."""
import json
import os
import subprocess
import sys
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.normpath(os.path.join(HERE, "..", "corporate-settings.py"))
STOP_COMMAND = 'python3 "$QWEN_SKILL_ROOT/tools/stopcheck.py"'


def emit_run(*extra):
    result = subprocess.run(
        [sys.executable, SETTINGS, "emit-run", "--window", "262000",
         "--max-tokens", "20000", "--session-token-limit", "230000",
         "--max-retries", "0", *extra],
        text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class SkillStopHookTests(unittest.TestCase):
    def test_skill_directory_emits_global_stopcheck_command(self):
        row = emit_run("--skill-directory", "/opt/sherlock-arm")

        self.assertIn("hooks", row)
        self.assertEqual(row["hooks"].get("Stop"), [{"hooks": [{
            "type": "command", "command": STOP_COMMAND}]}])

    def test_no_skill_directory_emits_no_stop_hook(self):
        row = emit_run()

        self.assertNotIn("hooks", row)


if __name__ == "__main__":
    unittest.main()
