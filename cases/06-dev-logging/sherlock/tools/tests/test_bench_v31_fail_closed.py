#!/usr/bin/env python3
"""v31 fail-closed contracts: a healthy transport is not a result."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
TOOL = SHERLOCK / "eval" / "bench" / "validate-run.py"


def load():
    spec = importlib.util.spec_from_file_location("validate_run_v31", str(TOOL))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def final_row(skill_calls, main_requests, tool_calls=12):
    return {
        "type": "result",
        "stats": {
            "skills": {"totalCalls": skill_calls},
            "tools": {"totalCalls": tool_calls},
            "models": {"m": {"bySource": {"main": {"api": {"totalRequests": main_requests}}}}},
        },
    }


class SkillReceiptTest(unittest.TestCase):
    def test_zero_skill_calls_is_rejected(self):
        module = load()
        self.assertIn("no_skill_load", module.skill_receipt(final_row(0, 1))["reasons"])

    def test_single_main_request_is_rejected_as_no_investigation(self):
        module = load()
        self.assertIn("no_investigation", module.skill_receipt(final_row(1, 1))["reasons"])

    def test_loaded_skill_with_real_investigation_passes(self):
        module = load()
        receipt = module.skill_receipt(final_row(1, 40))
        self.assertEqual(receipt["reasons"], [])
        self.assertEqual(receipt["skill_calls"], 1)
        self.assertEqual(receipt["main_requests"], 40)

    def test_missing_stats_is_rejected_not_ignored(self):
        module = load()
        self.assertIn("no_skill_load", module.skill_receipt({"type": "result"})["reasons"])


class TerminalExitTest(unittest.TestCase):
    def test_unknown_exit_is_never_zero(self):
        module = load()
        self.assertEqual(module.terminal_exit(None), "unknown")

    def test_real_exit_is_preserved(self):
        module = load()
        self.assertEqual(module.terminal_exit(0), 0)
        self.assertEqual(module.terminal_exit(124), 124)


class TerminalReceiptTest(unittest.TestCase):
    """The real terminal comes from the runner's own attempt receipt."""

    def receipt(self, body):
        import tempfile, os
        module = load()
        directory = tempfile.mkdtemp(prefix="v31-terminal-")
        with open(os.path.join(directory, "attempts.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(body)
        fd = os.open(directory, os.O_RDONLY)
        try:
            return module.terminal_receipt(fd)
        finally:
            os.close(fd)

    def test_last_attempt_wins(self):
        row = self.receipt('{"attempt":0,"exit_code":124,"duration_s":10}\n'
                           '{"attempt":1,"exit_code":0,"duration_s":42}\n')
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["duration_s"], 42)
        self.assertEqual(row["reasons"], [])

    def test_nonzero_exit_is_a_reason(self):
        row = self.receipt('{"attempt":0,"exit_code":124,"duration_s":10}\n')
        self.assertEqual(row["exit_code"], 124)
        self.assertIn("exit_nonzero", row["reasons"])

    def test_absent_receipt_is_unknown_not_zero(self):
        row = self.receipt("")
        self.assertEqual(row["exit_code"], "unknown")
        self.assertIn("exit_unknown", row["reasons"])

    def test_unparseable_receipt_is_unknown(self):
        row = self.receipt("not json\n")
        self.assertEqual(row["exit_code"], "unknown")
        self.assertIn("exit_unknown", row["reasons"])


class RealR4RegressionTest(unittest.TestCase):
    """The 2026-08-21 direct r4 trace must be rejected by the skill gate."""

    R4 = Path("/home/claude-developer/hack/sherlock-winevtx-runs-v30-direct-r4/"
              "20260821T101424Z-v30/out.json")

    def test_r4_final_row_is_rejected(self):
        if not self.R4.exists():
            self.skipTest("r4 trace not present on this host")
        import json
        module = load()
        final = json.loads(self.R4.read_text())[-1]
        receipt = module.skill_receipt(final)
        self.assertEqual(receipt["skill_calls"], 0)
        self.assertEqual(receipt["main_requests"], 1)
        self.assertIn("no_skill_load", receipt["reasons"])
        self.assertIn("no_investigation", receipt["reasons"])


class UngroundedMessageTest(unittest.TestCase):
    """The existing citation authority must reject the r4 fabricated answer."""

    R4 = Path("/home/claude-developer/hack/sherlock-winevtx-runs-v30-direct-r4/"
              "20260821T101424Z-v30/out.json")
    CORPUS = Path("/home/claude-developer/hack/sherlock-winevtx-corpus-v30-normalized-20260821")

    def test_r4_message_is_blocking(self):
        if not (self.R4.exists() and self.CORPUS.is_dir()):
            self.skipTest("r4 trace or corpus not present on this host")
        import json, subprocess, sys, tempfile, os
        checker = SHERLOCK / "skills" / "v30" / "tools" / "citecheck.py"
        message = json.loads(self.R4.read_text())[-1]["result"]
        with tempfile.TemporaryDirectory(prefix="v31-ungrounded-") as directory:
            delivered = os.path.join(directory, "delivered.md")
            with open(delivered, "w", encoding="utf-8") as handle:
                handle.write(message)
            done = subprocess.run([sys.executable, str(checker), delivered,
                                   "--corpus", str(self.CORPUS), "--require-quote", "--json"],
                                  capture_output=True, text=True, timeout=600)
        row = json.loads(done.stdout)
        self.assertEqual(row["summary"]["total"], 0)
        self.assertGreater(row["report_evidence"]["blocking"], 0)


class SkillVersionTest(unittest.TestCase):
    """v31's own tools must agree that the arm is version 31, not 30."""

    def test_marker_version_is_31(self):
        logmap = (SHERLOCK / "skills" / "v31" / "tools" / "logmap.py").read_text()
        self.assertIn('"version": 31,', logmap)
        self.assertNotIn('"version": 30,', logmap)

    def test_stopcheck_requires_version_31(self):
        stop = (SHERLOCK / "skills" / "v31" / "tools" / "stopcheck.py").read_text()
        self.assertIn("if version != 31:", stop)

    def test_v30_is_untouched(self):
        logmap = (SHERLOCK / "skills" / "v30" / "tools" / "logmap.py").read_text()
        stop = (SHERLOCK / "skills" / "v30" / "tools" / "stopcheck.py").read_text()
        self.assertIn('"version": 30,', logmap)
        self.assertIn("if version != 30:", stop)


if __name__ == "__main__":
    unittest.main()
