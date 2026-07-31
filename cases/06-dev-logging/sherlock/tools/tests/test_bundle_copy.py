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
import re
import stat
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)

# Each bundle carries ONLY the tools its own SKILL.md references, so this is declared
# per arm rather than mirroring the whole directory.
BUNDLES = {
    "v6": ["citecheck.py", "fetch-logs.sh", "fetch-logs.conf.example"],
    "v7": ["citecheck.py", "fetch-logs.sh", "fetch-logs.conf.example",
           "logstat.py", "logjoin.py"],
}
EXECUTABLE = {"citecheck.py", "fetch-logs.sh", "logstat.py", "logjoin.py"}

# Not invoked by the model — it accompanies fetch-logs.sh as a config template, so
# SKILL.md has no reason to name it by path.
COMPANIONS = {"fetch-logs.conf.example"}


def bundle_dir(ver):
    return os.path.join(SHERLOCK, "skills", ver, "tools")


def referenced_tools(ver):
    """The `tools/<x>` paths the arm's SKILL.md actually tells the model to run."""
    body = open(os.path.join(SHERLOCK, "skills", ver, "SKILL.md"),
                encoding="utf-8").read()
    return set(re.findall(r"tools/([A-Za-z0-9_.-]+)", body))


class BundleCopiesAreByteIdentical(unittest.TestCase):
    """A drifted copy ships the bug the top-level fix already removed."""

    def test_every_referenced_tool_is_present_in_the_bundle(self):
        for ver, names in BUNDLES.items():
            for name in names:
                self.assertTrue(os.path.exists(os.path.join(TOOLS, name)),
                                "missing tools/%s" % name)
                self.assertTrue(os.path.exists(os.path.join(bundle_dir(ver), name)),
                                "the %s bundle is missing its copy of %s" % (ver, name))

    def test_contents_match_byte_for_byte(self):
        for ver, names in BUNDLES.items():
            for name in names:
                a, b = os.path.join(TOOLS, name), os.path.join(bundle_dir(ver), name)
                self.assertTrue(filecmp.cmp(a, b, shallow=False),
                                "%s/%s has drifted from tools/%s — re-copy it"
                                % (ver, name, name))

    def test_the_exec_bit_survives_the_copy(self):
        for ver, names in BUNDLES.items():
            for name in sorted(set(names) & EXECUTABLE):
                b = os.path.join(bundle_dir(ver), name)
                self.assertTrue(os.stat(b).st_mode & stat.S_IXUSR,
                                "%s's copy of %s lost its exec bit" % (ver, name))


class ShippedAndDocumentedAgreeBothWays(unittest.TestCase):
    """The expensive silent failure this whole file exists for.

    `logstat.py` and `logjoin.py` were written with 35 tests and docstrings naming
    the two measured gaps by name — and then shipped NOWHERE: every bundle carried
    only `citecheck.py`, and SKILL.md mentioned them zero times. A tool the model is
    never told about is worth exactly nothing, and nothing failed to say so.

    So assert BOTH directions: nothing documented is missing from the bundle, and
    nothing shipped goes unmentioned."""

    def test_every_tool_named_in_skill_md_is_actually_shipped(self):
        for ver in BUNDLES:
            shipped = set(os.listdir(bundle_dir(ver)))
            for name in sorted(referenced_tools(ver)):
                self.assertIn(name, shipped,
                              "%s/SKILL.md tells the model to run tools/%s, which the "
                              "bundle does not carry" % (ver, name))

    def test_every_shipped_tool_is_named_in_skill_md(self):
        for ver in BUNDLES:
            named = referenced_tools(ver)
            for name in sorted(set(os.listdir(bundle_dir(ver))) - COMPANIONS):
                self.assertIn(name, named,
                              "%s ships tools/%s but its SKILL.md never mentions it — "
                              "the model will never run it" % (ver, name))


class TheFrozenArmsAreUntouched(unittest.TestCase):
    """v1-v6 are frozen A/B arms — every measurement in the ledgers is relative to
    them. A new arm is additive; it may not reach back into an older bundle."""

    def test_no_transport_leaked_into_v1_to_v5(self):
        for ver in ("v1", "v2", "v3", "v4", "v4.1", "v5"):
            d = bundle_dir(ver)
            if not os.path.isdir(d):
                continue
            self.assertNotIn("fetch-logs.sh", os.listdir(d),
                             "%s is a frozen arm and must not gain the transport" % ver)

    def test_no_analysis_tool_leaked_into_v1_to_v6(self):
        """v6 is on main and its number is quotable; adding tools to it in place would
        silently redefine what "v6" meant in every earlier row."""
        for ver in ("v1", "v2", "v3", "v4", "v4.1", "v5", "v6"):
            d = bundle_dir(ver)
            if not os.path.isdir(d):
                continue
            present = set(os.listdir(d))
            for tool in ("logstat.py", "logjoin.py"):
                self.assertNotIn(tool, present,
                                 "%s is a frozen arm and must not gain %s" % (ver, tool))

    def test_each_arm_carries_a_skill(self):
        for ver in BUNDLES:
            self.assertTrue(
                os.path.exists(os.path.join(SHERLOCK, "skills", ver, "SKILL.md")),
                "skills/%s/SKILL.md is what makes %s loadable as an eval arm"
                % (ver, ver))


if __name__ == "__main__":
    unittest.main(verbosity=2)
