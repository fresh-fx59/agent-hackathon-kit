#!/usr/bin/env python3
"""covermap.py — the coverage table, generated instead of typed.

PROVENANCE. This tool is a generalisation of a script the MODEL wrote for itself
during the first gate-clean paid run (sherlock-winevtx-runs-v37-full-r1/
20260825T173021Z-v37, work/gen_cov.py, 73 lines). Given a gate that blocks on
any corpus file missing from the coverage table, it did not hunt for a cheap
path — it wrote a generator that imports citecheck.py in-process, reads the
logmap-flagged line for each file, and builds a quote the gate will accept.
Result: 143 rows for 143 files, 93 «наблюдение» with real quotes, 50 «пусто»
with байт=0, and ZERO «не смотрел».

Shipping it removes the one expensive part of that run: the model paid to write
it, debug it (work/cov_err.txt exists) and re-run it. It also removes the
temptation to type 143 rows by hand and get the grammar wrong 23 times, which is
exactly what the previous run did.

THE TENSION, stated rather than hidden: a generated table makes coverage
mechanical. That is acceptable because the coverage row's job is «this file was
answered, here is a checkable address», not «this file was understood» — the
findings and rejected-candidate sections carry the thinking, and the run that
generated this proved the model still does that work (5 findings, 9 candidates).
What must never be generated is a finding.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
V37 = os.path.join(SKILLS, "v37", "tools")
COVERMAP = os.path.join(V37, "covermap.py")
CITECHECK = os.path.join(V37, "citecheck.py")

LINE = ('{"Event":{"System":{"Provider":{"#attributes":{"Name":"Service Control '
        'Manager"}},"EventID":{"#text":7045}},"EventData":{"ServiceName":"3proxy '
        'tiny proxy server","AccountName":"LocalSystem"}}}')
NOISE = '{"Event":{"System":{"EventID":{"#text":4624}},"EventData":{"pad":"%s"}}}'


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.corpus = os.path.join(self.dir, "corpus")
        self.work = os.path.join(self.dir, "work")
        os.makedirs(os.path.join(self.corpus, "rendered"))
        os.makedirs(self.work)
        with open(os.path.join(self.corpus, "System.jsonl"), "w", encoding="utf-8") as fh:
            for i in range(1, 263):
                fh.write(NOISE % ("x" * 40) + "\n")
            fh.write(LINE + "\n")
        with open(os.path.join(self.corpus, "rendered", "Other.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write(NOISE % ("y" * 40) + "\n")
        open(os.path.join(self.corpus, "Empty.jsonl"), "w").close()
        with open(os.path.join(self.corpus, "Bin.jsonl"), "wb") as fh:
            fh.write(b"\x00\x01\x02binary\x00" * 40)
        # a worklist whose reference column flags the interesting line
        with open(os.path.join(self.work, "worklist.tsv"), "w", encoding="utf-8") as fh:
            fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
            # Column 4 is logmap's reason and column 5 the raw record —
            # copied in shape from a real row (work/worklist.tsv, g080).
            fh.write("g001\t?\todd\tSystem.jsonl:263\t"
                     "n=1 · json:ServiceName=3proxy при json:EventID=7045\t"
                     + LINE + "\n")

    def run_covermap(self, *extra):
        return subprocess.run([sys.executable, COVERMAP, "--corpus", self.corpus,
                               "--worklist", os.path.join(self.work, "worklist.tsv")]
                              + list(extra), capture_output=True, text=True)

    def rows(self, out):
        return [l for l in out.splitlines() if l.startswith("| ")]


class TestEveryFileGetsARow(Base):
    def test_tool_exists_and_runs(self):
        self.assertTrue(os.path.exists(COVERMAP), "v37 must ship tools/covermap.py")
        done = self.run_covermap()
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_one_row_per_corpus_file(self):
        rows = self.rows(self.run_covermap().stdout)
        files = sum(len(f) for _r, _d, f in os.walk(self.corpus))
        self.assertEqual(len(rows), files, rows)

    def test_empty_and_binary_are_classified_not_quoted(self):
        out = self.run_covermap().stdout
        self.assertRegex(out, r"\| Empty\.jsonl \| пусто \| байт=0 \|")
        self.assertRegex(out, r"\| Bin\.jsonl \| двоичный \|")

    def test_it_never_emits_ne_smotrel(self):
        """«не смотрел» does not discharge a file; generating it would be a lie."""
        self.assertNotIn("не смотрел", self.run_covermap().stdout)

    def test_it_prefers_the_logmap_flagged_line(self):
        """A quote from an arbitrary line is legal and worthless; citecheck's
        cov_unflagged_citation exists to say so."""
        out = self.run_covermap().stdout
        line = [l for l in self.rows(out) if l.startswith("| System.jsonl ")][0]
        self.assertIn("System.jsonl:263", line, line)
        self.assertIn("3proxy", line, line)

    def test_nested_paths_are_corpus_relative(self):
        out = self.run_covermap().stdout
        self.assertIn("| rendered/Other.jsonl |", out)


class TestTheOutputSatisfiesTheGate(Base):
    def test_generated_table_passes_citecheck_coverage(self):
        """The round trip, not a format guess: what covermap prints, the gate takes."""
        rows = self.rows(self.run_covermap().stdout)
        report = os.path.join(self.dir, "r.md")
        with open(report, "w", encoding="utf-8") as fh:
            fh.write("# Отчёт\n\n## Находки\n\n### Н-1 · Служба\n\n"
                     "улики: System.jsonl:263 «ServiceName»\n\n"
                     "атрибуция: установлена\n\nисход: успех\n\n"
                     "## Отклонённые кандидаты\n\n### К-1 · Ничего\n\n"
                     "улики: System.jsonl:263 «ServiceName»\n\nисход: норма\n\n"
                     "## Покрытие\n\n| путь | статус | улики |\n| --- | --- | --- |\n")
            fh.write("\n".join(rows) + "\n\n## ВЕРДИКТ\n\nскомпрометирована\n")
        out = subprocess.run([sys.executable, CITECHECK, report, "--corpus",
                              self.corpus, "--require-quote"],
                             capture_output=True, text=True).stdout
        self.assertNotIn("НЕ ПОКРЫТО", out, out[-1500:])
        self.assertNotIn("НЕ СМОТРЕЛ", out, out[-1500:])


class TestFrozenArms(unittest.TestCase):
    def test_v36_does_not_gain_the_tool(self):
        self.assertFalse(os.path.exists(os.path.join(SKILLS, "v36", "tools",
                                                     "covermap.py")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
