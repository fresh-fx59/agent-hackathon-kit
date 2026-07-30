#!/usr/bin/env python3
"""The shipped skill bundle must carry byte-identical copies of the tools it references.

Nothing in this repo enforced that until now. `tools/citecheck.py` and
`skills/v5/tools/citecheck.py` are identical today (md5 124d71eacecef944948502ed7947a99e)
purely by discipline, and v6 doubles the exposure by adding a second duplicated file.
The failure mode is silent and expensive: a fix lands in `tools/fetch-logs.sh`, every
test here goes green because the tests run the top-level copy — and the SHIPPED skill,
the one an operator copies into `~/.qwen/skills/log-rca/`, keeps the bug.

The bundle deliberately carries only the tools it actually references, so this suite
asserts equality per pair rather than mirroring the whole directory.

    python3 tools/tests/test_bundle_copy.py
"""
import filecmp
import os
import stat
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
BUNDLE = os.path.join(SHERLOCK, "skills", "v6", "tools")

PAIRS = ["citecheck.py", "fetch-logs.sh", "fetch-logs.conf.example"]
EXECUTABLE = {"citecheck.py", "fetch-logs.sh"}


class BundleCopiesAreByteIdentical(unittest.TestCase):
    """A drifted copy ships the bug the top-level fix already removed."""

    def test_every_referenced_tool_is_present_in_the_bundle(self):
        for name in PAIRS:
            self.assertTrue(os.path.exists(os.path.join(TOOLS, name)),
                            "missing tools/%s" % name)
            self.assertTrue(os.path.exists(os.path.join(BUNDLE, name)),
                            "the v6 bundle is missing its copy of %s" % name)

    def test_contents_match_byte_for_byte(self):
        for name in PAIRS:
            a, b = os.path.join(TOOLS, name), os.path.join(BUNDLE, name)
            self.assertTrue(filecmp.cmp(a, b, shallow=False),
                            "%s has drifted from its bundle copy — re-copy it" % name)

    def test_the_exec_bit_survives_the_copy(self):
        for name in sorted(EXECUTABLE):
            b = os.path.join(BUNDLE, name)
            self.assertTrue(os.stat(b).st_mode & stat.S_IXUSR,
                            "the bundle copy of %s lost its exec bit" % name)


class TheFrozenArmsAreUntouched(unittest.TestCase):
    """v1-v5 are frozen A/B arms — every measurement in the ledgers is relative to
    them. v6 is additive; it may not reach back into an older bundle."""

    def test_no_transport_leaked_into_v1_to_v5(self):
        for ver in ("v1", "v2", "v3", "v4", "v4.1", "v5"):
            d = os.path.join(SHERLOCK, "skills", ver, "tools")
            if not os.path.isdir(d):
                continue
            self.assertNotIn("fetch-logs.sh", os.listdir(d),
                             "%s is a frozen arm and must not gain the transport" % ver)

    def test_v6_exists_and_carries_a_skill(self):
        self.assertTrue(os.path.exists(os.path.join(SHERLOCK, "skills", "v6", "SKILL.md")),
                        "skills/v6/SKILL.md is what makes v6 loadable as an eval arm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
