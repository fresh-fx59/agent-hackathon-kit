#!/usr/bin/env python3
"""Tests for scripts/new-case.sh -- the case scaffolder.

For each mode (rubric / findings / selection) it scaffolds a case into a
temp directory and asserts the scaffold is green from second zero:
benchmark.py --self-test exits 0 and --score-only prints a parseable number.
Also checks the guard rails: refusing an existing target and a bad slug.

Stdlib only, Python >= 3.9.  Runs standalone:  python3 test_new_case.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # repo root
NEW_CASE_SH = os.path.join(ROOT, "scripts", "new-case.sh")

MODES = ("rubric", "findings", "selection")

# Per-mode gold input for the --score-only check: (argument file, perfect score).
GOLD_INPUT = {
    "rubric": ("expected-output.md", 100.0),
    "findings": ("expected-findings.json", 1.0),
    "selection": (None, 1.0),  # selection.json is built from must_run in the test
}


def run(cmd, **kwargs):
    """Run a command, capture output; never raises on non-zero exit."""
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, **kwargs)


def scaffold(slug, mode, dest):
    return run(["bash", NEW_CASE_SH, slug, "--mode", mode, "--dest", dest])


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestNewCase(unittest.TestCase):

    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="new-case-test-")
        self.addCleanup(shutil.rmtree, self.dest, True)

    # ------------------------------------------------------------ happy path
    def test_scaffold_each_mode_is_green(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                slug = "demo-%s-case" % mode
                proc = scaffold(slug, mode, self.dest)
                self.assertEqual(proc.returncode, 0,
                                 "scaffold failed:\n%s\n%s"
                                 % (proc.stdout, proc.stderr))
                target = os.path.join(self.dest, slug)
                self.assertTrue(os.path.isdir(target))

                # README got the slug substituted, no token left anywhere.
                readme = read_text(os.path.join(target, "README.md"))
                self.assertIn(slug, readme)
                for name in os.listdir(target):
                    body = read_text(os.path.join(target, name))
                    self.assertNotIn("__CASE_SLUG__", body,
                                     "%s still holds the slug token" % name)

                # The fresh scaffold must pass its own benchmark self-test.
                bench = os.path.join(target, "benchmark.py")
                proc = run([sys.executable, bench, "--self-test"])
                self.assertEqual(proc.returncode, 0,
                                 "--self-test failed for %s:\n%s\n%s"
                                 % (mode, proc.stdout, proc.stderr))

                # --score-only prints exactly one parseable number.
                gold, perfect = GOLD_INPUT[mode]
                if gold is None:  # selection: replay must_run as the selection
                    expected = json.loads(read_text(
                        os.path.join(target, "expected-selection.json")))
                    gold = os.path.join(self.dest, "selection-%s.json" % slug)
                    with open(gold, "w", encoding="utf-8") as fh:
                        json.dump({"selected": expected["must_run"],
                                   "strategy": "test replay"}, fh)
                else:
                    gold = os.path.join(target, gold)
                proc = run([sys.executable, bench, gold, "--score-only"])
                self.assertEqual(proc.returncode, 0,
                                 "--score-only failed for %s:\n%s\n%s"
                                 % (mode, proc.stdout, proc.stderr))
                lines = proc.stdout.strip().splitlines()
                self.assertEqual(len(lines), 1,
                                 "--score-only must print exactly one line, "
                                 "got: %r" % proc.stdout)
                self.assertAlmostEqual(float(lines[0]), perfect, places=2)

    # ------------------------------------------------------------ guard rails
    def test_refuses_existing_target(self):
        slug = "demo-existing"
        self.assertEqual(scaffold(slug, "rubric", self.dest).returncode, 0)
        proc = scaffold(slug, "rubric", self.dest)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("exists", proc.stderr.lower())

    def test_rejects_bad_slugs(self):
        for bad in ("Bad_Slug", "UPPER", "has space", "double--dash",
                    "-lead", "trail-", "dot.case", ""):
            with self.subTest(slug=bad):
                proc = scaffold(bad, "rubric", self.dest)
                self.assertNotEqual(proc.returncode, 0,
                                    "slug %r must be rejected" % bad)
                self.assertFalse(
                    os.path.exists(os.path.join(self.dest, bad)) if bad else
                    False, "rejected slug %r left a directory behind" % bad)

    def test_rejects_bad_mode(self):
        proc = run(["bash", NEW_CASE_SH, "demo-mode", "--mode", "nope",
                    "--dest", self.dest])
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "demo-mode")))


if __name__ == "__main__":
    unittest.main()
