#!/usr/bin/env python3
"""v64: several addresses in ONE cite.py call.

Measured reason: a past replay made 57 separate cite.py calls, one address
each, ~7 s and ~130k prompt tokens per call. cite.py always accepted several
addresses (`address` nargs="*"), but its output was not labelled per address,
and SKILL.md only showed one-address examples.

Contract (SHERLOCK_PKG selects the package, default v64):
  * one address -> output byte-identical to v63;
  * several -> each block starts with a label naming its address;
  * one bad address -> labelled error, the others still print, exit 1;
  * cite-shown.tsv gets a row for every address shown;
  * --help shows single and multi-address examples.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
PKG = os.environ.get("SHERLOCK_PKG", "v64")
CITE = os.path.join(SKILLS, PKG, "tools", "cite.py")
V63 = os.path.join(SKILLS, "v63", "tools", "cite.py")
CORPUS = os.path.join(HERE, "fixtures", "v60-smalltest", "corpus")


def run(cite, *args, work=None):
    argv = [sys.executable, cite, "--corpus", CORPUS]
    if work:
        argv += ["--work", work]
    p = subprocess.run(argv + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Multi(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_single_address_identical_to_v63(self):
        for addr in ("Security.jsonl:15#TargetUserName", "Security.jsonl:12",
                     "System.jsonl:8", "Security.jsonl@417272", "Nope.jsonl:1",
                     "Security.jsonl:999", "Security.jsonl:15#NoSuchField"):
            self.assertEqual(run(CITE, addr), run(V63, addr), addr)
        self.assertEqual(run(CITE, "Security.jsonl:15", "--full"),
                         run(V63, "Security.jsonl:15", "--full"))

    def test_multi_labelled(self):
        rc, out, err = run(CITE, "Security.jsonl:15#TargetUserName",
                           "Security.jsonl:12", "System.jsonl:8")
        self.assertEqual(rc, 0, err)
        for a in ("Security.jsonl:15#TargetUserName", "Security.jsonl:12",
                  "System.jsonl:8"):
            self.assertIn("=== %s ===" % a, out)
        # each block's first line after the label is that address's reference
        blocks = out.split("=== ")[1:]
        self.assertEqual(len(blocks), 3)
        _one_rc, one_out, _ = run(CITE, "Security.jsonl:12")
        self.assertIn(one_out, blocks[1])

    def test_partial_failure(self):
        rc, out, err = run(CITE, "Security.jsonl:15", "Missing.jsonl:3",
                           "Security.jsonl:999", "Security.jsonl:15#NoSuchField",
                           "System.jsonl:8")
        self.assertEqual(rc, 1)
        self.assertIn("=== Security.jsonl:15 ===", out)
        self.assertIn("=== System.jsonl:8 ===", out)
        for bad in ("Missing.jsonl:3", "Security.jsonl:999",
                    "Security.jsonl:15#NoSuchField"):
            self.assertIn("=== %s ===" % bad, out)
            self.assertIn(bad, err)
        self.assertIn("3 из 5", err)
        self.assertIn("System.jsonl:8", out.split("=== System.jsonl:8 ===")[1])

    def test_shown_rows(self):
        run(CITE, "Security.jsonl:15", "Missing.jsonl:3", "Security.jsonl:12",
            "System.jsonl:8", work=self.work)
        with open(os.path.join(self.work, "cite-shown.tsv"), encoding="utf-8") as fh:
            rows = [l.split("\t")[:2] for l in fh.read().splitlines()]
        self.assertEqual(rows, [["Security.jsonl", "15"], ["Security.jsonl", "12"],
                                ["System.jsonl", "8"]])

    def test_help_examples(self):
        out = subprocess.run([sys.executable, CITE, "--help"], capture_output=True,
                             text=True).stdout
        self.assertIn("cite.py Security.jsonl:15#TargetUserName", out)
        self.assertIn("cite.py Security.jsonl:15#TargetUserName Security.jsonl:12 "
                      "System.jsonl:8", out)


if __name__ == "__main__":
    unittest.main()
