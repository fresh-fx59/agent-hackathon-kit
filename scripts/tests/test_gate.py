#!/usr/bin/env python3
"""Tests for ci/gate.sh -- the universal eval gate.

Drives the real script via subprocess (bash), the same way a developer, a
git hook, or the Jenkins Gate stage would.  Uses cases/analytics-meeting as
the live fixture: its expected-br.md self-scores >= 95, so min 90 passes and
min 101 (above the 0-100 scale) must fail.

No network, no fixed ports, no stray files (artifacts go to a TemporaryDirectory).

Run standalone:  python3 scripts/tests/test_gate.py
"""

import os
import subprocess
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(TESTS_DIR))
GATE = os.path.join(REPO_DIR, "ci", "gate.sh")
CASE_DIR = os.path.join(REPO_DIR, "cases", "analytics-meeting")
EXPECTED_BR = os.path.join(CASE_DIR, "expected-br.md")


def run_gate(args, cwd=REPO_DIR):
    """Run gate.sh with args; return (returncode, combined stdout, stderr)."""
    proc = subprocess.run(
        ["bash", GATE] + list(args),
        cwd=cwd, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


class HarnessOnlyModeTest(unittest.TestCase):
    """No artifact -> the gate runs benchmark.py --self-test only."""

    def test_harness_only_passes_and_says_so(self):
        rc, out, err = run_gate([CASE_DIR, "80"])
        self.assertEqual(rc, 0, "stdout:\n%s\nstderr:\n%s" % (out, err))
        self.assertIn("HARNESS ONLY", out)
        self.assertIn("not applied", out)   # min-score explicitly not applied
        self.assertIn("gate: PASS", out)
        # No artifact was scored, so the stable score line must NOT appear.
        self.assertNotIn("benchmark score:", out)

    def test_harness_only_with_relative_paths_from_repo_root(self):
        rc, out, err = run_gate(["cases/analytics-meeting", "80"], cwd=REPO_DIR)
        self.assertEqual(rc, 0, "stdout:\n%s\nstderr:\n%s" % (out, err))
        self.assertIn("gate: PASS", out)


class ArtifactModeTest(unittest.TestCase):
    """With an artifact -> score via --score-only and gate on min-score."""

    def test_passing_artifact(self):
        rc, out, err = run_gate([CASE_DIR, "90", EXPECTED_BR])
        self.assertEqual(rc, 0, "stdout:\n%s\nstderr:\n%s" % (out, err))
        self.assertIn("benchmark score:", out)
        self.assertIn("(min 90)", out)
        self.assertIn("gate: PASS", out)

    def test_failing_artifact(self):
        # 101 is above the 0-100 rubric scale: guaranteed red.
        rc, out, err = run_gate([CASE_DIR, "101", EXPECTED_BR])
        self.assertEqual(rc, 1, "stdout:\n%s\nstderr:\n%s" % (out, err))
        self.assertIn("benchmark score:", out)
        self.assertIn("(min 101)", out)
        self.assertIn("gate: FAIL", err)

    def test_score_line_is_stable_and_parseable(self):
        rc, out, _ = run_gate([CASE_DIR, "90", EXPECTED_BR])
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if l.startswith("benchmark score: ")]
        self.assertEqual(len(lines), 1)
        # "benchmark score: <N> (min <MIN>)" -- N must parse as a float.
        score_str = lines[0].split()[2]
        score = float(score_str)
        self.assertGreaterEqual(score, 90.0)
        self.assertLessEqual(score, 100.0)

    def test_works_from_any_cwd_with_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = run_gate([CASE_DIR, "90", EXPECTED_BR], cwd=tmp)
            self.assertEqual(rc, 0, "stdout:\n%s\nstderr:\n%s" % (out, err))
            self.assertIn("gate: PASS", out)


class ErrorPathsTest(unittest.TestCase):
    """Usage and setup errors exit 2 with a clear message."""

    def test_no_args_prints_usage(self):
        rc, _, err = run_gate([])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", err)

    def test_one_arg_prints_usage(self):
        rc, _, err = run_gate([CASE_DIR])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", err)

    def test_missing_case_dir(self):
        rc, _, err = run_gate(["cases/no-such-case", "80"])
        self.assertEqual(rc, 2)
        self.assertIn("case dir not found", err)

    def test_case_dir_without_benchmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = run_gate([tmp, "80"])
            self.assertEqual(rc, 2)
            self.assertIn("no benchmark.py", err)

    def test_non_numeric_min_score(self):
        rc, _, err = run_gate([CASE_DIR, "eighty"])
        self.assertEqual(rc, 2)
        self.assertIn("not a number", err)

    def test_missing_artifact(self):
        rc, _, err = run_gate([CASE_DIR, "80", "out/no-such-artifact.md"])
        self.assertEqual(rc, 2)
        self.assertIn("artifact not found", err)


if __name__ == "__main__":
    unittest.main()
