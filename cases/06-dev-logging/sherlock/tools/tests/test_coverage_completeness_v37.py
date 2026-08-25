#!/usr/bin/env python3
"""Defect 1 of the 2026-08-25 v36 audit: deleting coverage rows IMPROVED the score.

MEASURED on the paid run sherlock-winevtx-runs-v36-full-r1/20260825T061049Z-v36.
A subagent wrote a 33,326-byte report; the parent replaced it with an 18,052-byte
one. Both were scored with that run's own gate-tools/citecheck.py:

    version            refs  ok        coverage rows  192.99.186.31  ИТОГ
    discarded 33,326B    76  65 (86%)            128             4x  осталось 80
    delivered 18,052B    41  18 (44%)             16             0x  осталось 32

The BETTER report scores WORSE. The corpus holds 143 files; the delivered report
showed 16 coverage rows and citecheck said nothing about the 127 it never saw.
Deleting 112 rows deleted 112 chances to fail. That is not the model cheating —
it is the model correctly optimising the metric we handed it.

Root cause: `evidence()` scores the coverage rows PRESENT and never compares them
against the corpus it already indexed (`index_corpus` gives `by_rel`, the full
set, three lines above the loop). So the fix is not a cap or a warning: every
corpus file absent from the coverage table must be a BLOCKING defect, so that
removing a row can only ever make the score worse.

The truncation corollary, per AGENTS.md ("no silent caps"): `index_corpus` stops
at MAX_INDEX_FILES. If it truncated, the uncovered set is unknowable, so the
check must say so loudly rather than score against a partial list.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
V37 = os.path.join(SKILLS, "v37", "tools", "citecheck.py")
V36 = os.path.join(SKILLS, "v36", "tools", "citecheck.py")

LINE = '{"Event":{"System":{"EventID":{"#text":7045}},' \
       '"EventData":{"ServiceName":"3proxy"}}}'

HEAD = "# Отчёт\n\n## Находки\n\n### Н-1 · Служба\n\nулики: a.jsonl:1 «%s»\n\n" \
       "атрибуция: установлена\n\nисход: успех\n\n" \
       "## Отклонённые кандидаты\n\n### К-1 · Ничего\n\n" \
       "улики: a.jsonl:1 «%s»\n\nисход: норма\n\n" % ("ServiceName", "ServiceName")


def report(rows):
    body = HEAD + "## Покрытие\n\n| путь | статус | улики |\n| --- | --- | --- |\n"
    for name in rows:
        body += "| %s | наблюдение | %s:1 «ServiceName» |\n" % (name, name)
    return body + "\n## ВЕРДИКТ\n\nчисто\n"


class Base(unittest.TestCase):
    FILES = ("a.jsonl", "b.jsonl", "c.jsonl")

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.corpus = os.path.join(self.dir, "corpus")
        os.makedirs(self.corpus)
        for name in self.FILES:
            with open(os.path.join(self.corpus, name), "w", encoding="utf-8") as fh:
                fh.write(LINE + "\n")

    def run_gate(self, tool, rows):
        path = os.path.join(self.dir, "r.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report(rows))
        return subprocess.run([sys.executable, tool, path, "--corpus", self.corpus,
                               "--require-quote"], capture_output=True, text=True)


class TestDeletingRowsMustNotHelp(Base):
    def test_full_coverage_has_no_uncovered_defect(self):
        out = self.run_gate(V37, self.FILES).stdout
        self.assertNotIn("НЕ ПОКРЫТО", out, "a complete table must not be flagged")

    def test_missing_file_is_blocking(self):
        """The whole point: a corpus file with no coverage row must FAIL."""
        out = self.run_gate(V37, ("a.jsonl",)).stdout
        self.assertIn("НЕ ПОКРЫТО", out)
        self.assertIn("b.jsonl", out)
        self.assertIn("c.jsonl", out)

    def test_deleting_a_row_can_only_make_the_score_worse(self):
        """The v36 report improved 80 -> 32 by deleting rows. Never again."""
        full = self.count(self.run_gate(V37, self.FILES).stdout)
        cut = self.count(self.run_gate(V37, ("a.jsonl",)).stdout)
        self.assertGreater(cut, full,
                           "deleting 2 of 3 coverage rows must RAISE the "
                           "defect count, not lower it (full=%s cut=%s)"
                           % (full, cut))

    @staticmethod
    def count(out):
        """citecheck's own blocking tally — the number the run stops on."""
        import re
        m = re.search(r"ОТЧЁТНЫЕ НАБЛЮДЕНИЯ v26: (\d+) блокирующих", out)
        assert m, "no blocking tally in:\n" + out
        return int(m.group(1))


class TestV36StillCarriesTheDefect(Base):
    """v36 has a paid result attached; it is frozen and must stay broken."""

    def test_v36_says_nothing_about_uncovered_files(self):
        out = self.run_gate(V36, ("a.jsonl",)).stdout
        self.assertNotIn("НЕ ПОКРЫТО", out)

    def test_v36_rewards_deletion(self):
        full = TestDeletingRowsMustNotHelp.count(self.run_gate(V36, self.FILES).stdout)
        cut = TestDeletingRowsMustNotHelp.count(self.run_gate(V36, ("a.jsonl",)).stdout)
        self.assertLessEqual(cut, full,
                             "this is the defect being fixed: in v36 deleting "
                             "rows does not raise the count")


if __name__ == "__main__":
    unittest.main(verbosity=2)
