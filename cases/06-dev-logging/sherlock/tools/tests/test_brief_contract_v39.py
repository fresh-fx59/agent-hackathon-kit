#!/usr/bin/env python3
"""Defects 4 and 5 of the 2026-08-25 v36 audit. One file, one shared root cause.

DEFECT 4 — children re-import the parent's system prompt as tool output.
MEASURED: reads of the gate source, SKILL.md and reference/*.md across the run
cost ~5,070,000 amplified prompt tokens, 27% of the whole thing. citecheck.py
alone was read 25 times. The briefs are a CORRECT contract — absolute paths,
verbatim gate commands, a pre-verified facts block — but they also say

    полная инструкция навыка: {skill_md} — прочитай раздел «Шаг 2 …» целиком

and hand over the tools directory, and every child obeyed. sherlock-triage's
second thought, verbatim: "Now let me read the SKILL.md section «Шаг 2. Разбор
рабочего списка» completely". The content is already in the parent's system
prompt; reading it in a child pays for it again, uncached, resident for the
child's whole run.

DEFECT 5 — all three children failed and returned nothing, and the parent could
not see the work they had left on disk.
  sherlock-triage    failed · MAX_TURNS (30) · returned ""
  sherlock-draft     failed · MAX_TURNS (30) · returned "" · ZERO write_file calls
  general-purpose    failed · "Concurrency limit exceeded"
The children burned 1,972,355 fresh tokens (33%) for nothing retained. Worse:
general-purpose HAD written a 33,326-byte report at 07:27:03 before dying. The
parent read its FAILURE STATUS, concluded "мне нужно написать отчёт самому", and
spent 29 minutes re-deriving a report that was already on disk beside it.

The report phase is not delegated any more. The measured reason is not "children
are bad" — it is that the parent wrote the report itself BOTH before and after
spawning draft children, so the delegation bought a second author for a
single-author job. Triage stays delegated: it is genuine bulk work and its disk
side-effects were the one thing that survived.

Raising maxTurns is deliberately NOT the fix — that moves the cliff to the next
wider corpus. The class fix is that a child's deliverable is the FILE, and the
parent verifies files rather than trusting a return value.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))


def brief_text(version, phase):
    root = os.path.join(SKILLS, version)
    tmp = tempfile.mkdtemp()
    work = os.path.join(tmp, "work")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(work); os.makedirs(corpus)
    subprocess.run([sys.executable, os.path.join(root, "tools", "brief.py"),
                    "--work", work, "--corpus", corpus, "--skill-root", root],
                   capture_output=True, text=True, check=True)
    path = os.path.join(work, "brief-%s.md" % phase)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else None


class TestBriefsDoNotSendChildrenToTheSource(unittest.TestCase):
    def test_triage_brief_does_not_point_at_skill_md(self):
        """Naming SKILL.md inside the PROHIBITION is right; listing it as an
        input is the defect. So this checks the input bullets, not the page."""
        text = brief_text("v39", "triage")
        inputs = [l for l in text.splitlines() if l.strip().startswith("* ")]
        offenders = [l for l in inputs if "SKILL.md" in l]
        self.assertEqual(offenders, [],
                         "the brief IS the contract; listing SKILL.md as an "
                         "input pays for the parent's system prompt again")
        self.assertIn("НЕ ЧИТАЙ SKILL.md", text,
                      "and it must say so, not merely omit the invitation")

    def test_triage_brief_forbids_reading_tool_source(self):
        text = brief_text("v39", "triage")
        self.assertIn("НЕ ЧИТАЙ", text.upper(),
                      "the brief must forbid reading the tools, not just omit "
                      "the invitation — citecheck.py was read 25 times")

    def test_gate_commands_survive(self):
        """Forbidding the SOURCE must not forbid RUNNING the gates."""
        text = brief_text("v39", "triage")
        self.assertIn("triagecheck.py", text)

    def test_brief_does_not_name_files_that_do_not_exist_yet(self):
        """The draft child burned a failed read plus a whole find(1) round
        hunting for a checkpoint.json the brief listed as an input."""
        text = brief_text("v39", "triage")
        for line in text.splitlines():
            if line.strip().startswith("* ") and "checkpoint.json" in line:
                self.fail("checkpoint.json listed as an input: %r" % line)


class TestDraftIsNotDelegated(unittest.TestCase):
    def test_no_draft_agent_is_installed(self):
        agents = tempfile.mkdtemp()
        root = os.path.join(SKILLS, "v39")
        subprocess.run([sys.executable, os.path.join(root, "tools", "brief.py"),
                        "--install-agents", agents], capture_output=True,
                       text=True, check=True)
        installed = sorted(os.listdir(agents))
        self.assertNotIn("sherlock-draft.md", installed, installed)
        self.assertIn("sherlock-triage.md", installed, installed)

    def test_no_draft_brief_is_written(self):
        self.assertIsNone(brief_text("v39", "draft"),
                          "the parent writes the report; it did so both before "
                          "and after spawning draft children")


class TestChildDeliverableIsTheFile(unittest.TestCase):
    def test_brief_says_the_file_is_the_deliverable(self):
        text = brief_text("v39", "triage")
        self.assertIn("ФАЙЛ", text.upper(),
                      "a child that runs out of turns returns nothing; its work "
                      "must survive on disk and the parent must look there")


class TestV36StillCarriesTheDefect(unittest.TestCase):
    """v36 has a paid result attached and stays frozen."""

    def test_v36_still_points_children_at_skill_md(self):
        self.assertIn("SKILL.md", brief_text("v36", "triage"))

    def test_v36_still_delegates_the_draft(self):
        self.assertIsNotNone(brief_text("v36", "draft"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
