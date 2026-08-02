#!/usr/bin/env python3
"""Every command SKILL.md tells the model to run must actually run.

This is the free, deterministic guard for the defect that cost four metered slice
cells on 2026-07-31: `logstat.py`, `logjoin.py` and `citecheck.py` were shipped in the
bundle AND named in SKILL.md — so every existing test was green — yet were invoked
ZERO times in every run ever recorded. The documented form was
`python3 tools/logstat.py`, but `tools/` lives inside the SKILL directory while the
model's cwd is the WORKING directory. The path could never resolve. citecheck.py had
shipped that way since v5 and nobody noticed for months.

Grepping for the tool NAME cannot catch this (test_bundle_copy does that, and it
passed throughout). Asserting the path SHAPE is better but still guesses at runtime
behaviour. The only honest check is to build the real layout and EXECUTE the command.

No LLM, no network, no metered call — this runs in under a second and would have
caught the bug before a single cell was spent.

    python3 tools/tests/test_documented_commands_run.py
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)

# Arms required to have runnable documented commands. v6/v7 are frozen with the broken
# form; v7 is deliberately preserved as "the arm that shipped tools it could never run".
ARMS = ["v8", "v9", "v10", "v11"]

# The two layouts that exist in reality:
#   project-local  — what measure/run-case.sh builds ($W/.qwen/skills/log-rca)
#   home-installed — what skills/README.md tells an operator to do (~/.qwen/skills/...)
LAYOUTS = ["project", "home"]


def documented_commands(ver):
    """Lines of SKILL.md that invoke a bundled asset, as runnable shell."""
    body = open(os.path.join(SHERLOCK, "skills", ver, "SKILL.md"), encoding="utf-8").read()
    out = []
    for raw in body.splitlines():
        line = raw.strip().strip("`")
        if not re.match(r"^(python3|bash|sh)\s", line):
            continue
        if ".py" not in line and ".sh" not in line:
            continue
        out.append(line)
    return out


class EveryDocumentedCommandResolves(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="doccmd-")
        self.work = os.path.join(self.root, "work")
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.work, "logs"), exist_ok=True)
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.work, "logs", "app.log"), "w",
                  encoding="utf-8") as fh:
            # Big enough to BE a log: a two-line file is mostly-unique by
            # construction, and a residue tool correctly refuses to call that a
            # stream of records. The corpus has to be plausible for the
            # end-to-end proof to mean anything.
            for i in range(200):
                fh.write("2026-07-28 09:%02d:00 INFO ok order=ORD-1\n" % (i % 60))
            fh.write("2026-07-28 09:59:59 ERROR boom order=ORD-1\n")
            fh.write("2026-07-28 09:59:59 ALARM a shape seen exactly once\n")
        with open(os.path.join(self.work, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("## Улики\nlogs/app.log:1 — ERROR boom\n")

    def _sub(self, cmd):
        """Substitute the corpus placeholder exactly as the model is told to.

        v11 documents `<КАТАЛОГ_ЛОГОВ>` instead of `./logs` because BOTH harnesses
        cd into a fresh temp dir and pass the corpus by ABSOLUTE path — `./logs`
        never exists there, and SKILL.md routed that failure into the no-tool
        fallback, i.e. a path slip read as "the tool is absent" and skipped the
        whole mechanism. The previous version of this suite manufactured a `logs/`
        dir in the work dir, which made the broken command pass."""
        return cmd.replace("<КАТАЛОГ_ЛОГОВ>",
                           shlex.quote(os.path.join(self.work, "logs")))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _install(self, ver, layout):
        base = self.work if layout == "project" else self.home
        dest = os.path.join(base, ".qwen", "skills", "log-rca")
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(os.path.join(SHERLOCK, "skills", ver), dest)

    def test_the_documented_commands_resolve_to_a_real_file(self):
        """Resolution only — not full execution, since several commands take
        placeholder arguments (`<каталог>`, `<id>`) that are not valid input. What
        must hold is that the path expansion finds the script."""
        for ver in ARMS:
            cmds = documented_commands(ver)
            self.assertTrue(cmds, "%s/SKILL.md documents no runnable command" % ver)
            for layout in LAYOUTS:
                self._install(ver, layout)
                env = dict(os.environ, HOME=self.home)
                for cmd in cmds:
                    m = re.search(r'"\$\((ls [^)]+)\)"', cmd)
                    self.assertIsNotNone(
                        m, "%s/%s: command does not self-resolve its path: %s"
                           % (ver, layout, cmd))
                    p = subprocess.run(["bash", "-c", m.group(1)], cwd=self.work,
                                       env=env, capture_output=True, text=True)
                    found = (p.stdout or "").strip().splitlines()
                    self.assertTrue(
                        found,
                        "%s/%s: path expansion found NOTHING for: %s\n"
                        "This is the exact bug that made the tools unrunnable."
                        % (ver, layout, cmd))
                    # `ls` printed the path relative to the WORKING dir (or under the
                    # fake HOME); resolve it the same way the shell would before
                    # asserting the file is really there.
                    hit = found[0].replace("~", self.home, 1)
                    if not os.path.isabs(hit):
                        hit = os.path.join(self.work, hit)
                    self.assertTrue(
                        os.path.exists(hit),
                        "%s/%s: resolved to a non-existent file: %s"
                        % (ver, layout, found[0]))

    # The arm's mapping tool: v8-v10 ship logstat.py, v11 replaces it with
    # logmap.py. Hard-coding one name here would have silently skipped the only
    # end-to-end proof the new arm has.
    MAP_TOOL = {"v8": "logstat.py", "v9": "logstat.py", "v10": "logstat.py",
                "v11": "logmap.py"}

    def test_the_map_tool_actually_executes_and_produces_output(self):
        """The full end-to-end proof for the one command with real arguments."""
        for ver in ARMS:
            for layout in LAYOUTS:
                self._install(ver, layout)
                cmd = self._sub(next(c for c in documented_commands(ver)
                                     if self.MAP_TOOL[ver] in c and "--json" not in c))
                p = subprocess.run(["bash", "-c", cmd], cwd=self.work,
                                   env=dict(os.environ, HOME=self.home),
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0,
                                 "%s/%s: documented map command failed: %s\n%s"
                                 % (ver, layout, cmd, p.stderr[:400]))
                self.assertIn("app.log", p.stdout,
                              "%s/%s: the map tool ran but reported nothing about "
                              "the corpus" % (ver, layout))

    def test_the_forked_arm_writes_a_worklist_that_starts_unadjudicated(self):
        """v11's whole mechanism is a worklist the model must write back. If the
        first command does not produce one, nothing downstream can run."""
        for layout in LAYOUTS:
            self._install("v11", layout)
            cmd = self._sub(next(c for c in documented_commands("v11")
                                 if "logmap.py" in c))
            p = subprocess.run(["bash", "-c", cmd], cwd=self.work,
                               env=dict(os.environ, HOME=self.home),
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stderr[:400])
            wl = os.path.join(self.work, "work", "worklist.tsv")
            self.assertTrue(os.path.exists(wl), "no worklist at %s" % wl)
            rows = [l.split("\t") for l in open(wl, encoding="utf-8")
                    if not l.startswith("#") and l.strip()]
            self.assertTrue(rows, "the worklist is empty")
            self.assertTrue(all(r[1] == "?" for r in rows),
                            "every row must start unadjudicated")

    def test_the_forked_arm_ledger_refuses_an_unadjudicated_worklist(self):
        """Free proof of the stopping condition: exit code, not self-assessment."""
        self._install("v11", "project")
        cmd = self._sub(next(c for c in documented_commands("v11")
                             if "logmap.py" in c))
        subprocess.run(["bash", "-c", cmd], cwd=self.work,
                       env=dict(os.environ, HOME=self.home),
                       capture_output=True, text=True, timeout=300)
        cc = self._sub(next(c for c in documented_commands("v11")
                            if "citecheck.py" in c))
        p = subprocess.run(["bash", "-c", cc], cwd=self.work,
                           env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 1,
                         "a worklist full of `?` must block the report:\n%s"
                         % p.stdout[-1500:])
        self.assertIn("неразобранных строк", p.stdout)

    def test_every_reference_file_pointed_to_actually_exists(self):
        """Progressive disclosure adds a new way to be silently wrong.

        Moving heavy sections into `reference/*.md` keeps SKILL.md near the size of
        Qwen's own reference skill (239 lines), which matters because smaller models
        tolerate far less distance between an instruction and its use. But a pointer
        to a file that is not there is worse than the inline text it replaced: the
        model follows the pointer, finds nothing, and silently loses that guidance —
        the same class of failure as a tool path that never resolved."""
        for ver in ARMS:
            skill_dir = os.path.join(SHERLOCK, "skills", ver)
            body = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
            refs = set(re.findall(r"`(reference/[A-Za-z0-9_./-]+\.md)`", body))
            for ref in sorted(refs):
                self.assertTrue(
                    os.path.exists(os.path.join(skill_dir, ref)),
                    "%s/SKILL.md points at %s, which does not exist — the model will "
                    "follow it and silently lose that guidance" % (ver, ref))

    def test_no_reference_file_is_orphaned(self):
        """The other direction: a reference file nothing points at is dead weight the
        model will never load."""
        for ver in ARMS:
            skill_dir = os.path.join(SHERLOCK, "skills", ver)
            ref_dir = os.path.join(skill_dir, "reference")
            if not os.path.isdir(ref_dir):
                continue
            body = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
            for name in sorted(os.listdir(ref_dir)):
                self.assertIn(name, body,
                              "%s/reference/%s is never referenced from SKILL.md, so "
                              "nothing will ever load it" % (ver, name))

    def test_a_cwd_relative_command_would_have_failed_this_test(self):
        """Negative control: prove the test can actually catch the original bug,
        rather than passing because the assertion is toothless."""
        self._install(ARMS[0], "project")
        p = subprocess.run(["bash", "-c", "python3 tools/logstat.py ./logs"],
                           cwd=self.work, env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0,
                            "the original broken form somehow succeeded — this test "
                            "would not have caught the bug it exists for")


class TheForkedArmNeverHardcodesTheCorpusPath(unittest.TestCase):
    """REGRESSION for the path trap. Neither harness creates ./logs — both cd into
    a fresh temp dir and name the corpus by absolute path — so a documented command
    that passes ./logs as the corpus argument fails at runtime, and SKILL.md sent
    exactly that failure into the no-tool fallback."""

    def test_no_v11_command_passes_a_bare_dot_logs_as_corpus(self):
        offenders = [c for c in documented_commands("v11")
                     if re.search(r"(?:^|\s)\./logs(?:\s|$)", c)]
        self.assertEqual(offenders, [],
                         "v11 must use the <КАТАЛОГ_ЛОГОВ> placeholder: %s"
                         % offenders)


if __name__ == "__main__":
    unittest.main(verbosity=2)
