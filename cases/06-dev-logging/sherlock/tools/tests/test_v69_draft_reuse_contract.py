#!/usr/bin/env python3
"""Provider-free v69 contract: draft rules in SKILL.md + reportappend.py helper."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "skills" / "v69"
HELPER = PKG / "tools" / "reportappend.py"
MARKER = "СИНТЕЗ НЕ ЗАВЕРШЁН — удали эту строку последним действием синтеза."
STUB = "# Отчёт Sherlock\n\n%s\n\nСостояние: ready_for_synthesis — синтез не начат.\n" % MARKER


def run(*args, input=None):
    return subprocess.run([sys.executable, str(HELPER), *map(str, args)],
                          input=input, text=True, capture_output=True, timeout=60)


class SkillRules(unittest.TestCase):
    def setUp(self):
        self.s = (PKG / "SKILL.md").read_text(encoding="utf-8")

    def test_rules_present(self):
        for needle in ("DRAFT RULES (v68", "SYNTHESIS FROM CLOSED WORK ONLY",
                       "NO new corpus walking", "cite-shown.tsv",
                       "ONCE. Never re-read it", "WRITE ONLY `work/report.md`",
                       "`rp1.md`", "reportappend.py append",
                       "reportappend.py coverage", "reportappend.py rollover",
                       "reportappend.py finish"):
            self.assertIn(needle, self.s)

    def test_rules_before_report_format_read(self):
        self.assertLess(self.s.index("DRAFT RULES (v68"),
                        self.s.index("immediately before writing the draft"))

    def test_no_stale_v67_strings(self):
        for f in PKG.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".md") and "__pycache__" not in f.parts:
                t = f.read_text(encoding="utf-8")
                if f.name == "SKILL.md":
                    t = t.replace("measured v67", "")
                self.assertNotIn("sherlock-v67", t, f)


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v69-ra-"))
        self.work = self.tmp / "work"; self.work.mkdir()
        self.rep = self.work / "report.md"
        self.rep.write_text(STUB, encoding="utf-8")

    def test_first_write_replaces_stub_keeps_marker_last(self):
        r = run("append", "--work", self.work, input="## Находки\n\n### Н-1 · x\n- исход: успех\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.rep.read_text(encoding="utf-8")
        self.assertNotIn("Состояние: ready_for_synthesis", t)
        self.assertTrue(t.startswith("# Отчёт Sherlock\n\n## Находки"))
        self.assertEqual(t.rstrip().splitlines()[-1], MARKER)
        self.assertEqual(t.count("СИНТЕЗ НЕ ЗАВЕРШЁН"), 1)

    def test_append_order_and_finish(self):
        run("append", "--work", self.work, input="## Находки\n\n### Н-1 · a\n")
        run("append", "--work", self.work, input="### Н-2 · b\n")
        t = self.rep.read_text(encoding="utf-8")
        self.assertLess(t.index("Н-1"), t.index("Н-2"))
        self.assertEqual(t.rstrip().splitlines()[-1], MARKER)
        r = run("finish", "--work", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.rep.read_text(encoding="utf-8")
        self.assertNotIn("СИНТЕЗ НЕ ЗАВЕРШЁН", t)
        self.assertIn("Н-2", t)

    def test_refuses_oversize_empty_and_marker(self):
        self.assertNotEqual(run("append", "--work", self.work, input="x" * 13000).returncode, 0)
        self.assertNotEqual(run("append", "--work", self.work, input="\n").returncode, 0)
        self.assertNotEqual(run("append", "--work", self.work, input="a " + MARKER).returncode, 0)
        self.assertEqual(self.rep.read_text(encoding="utf-8"), STUB)

    def test_writes_only_report_md(self):
        run("append", "--work", self.work, input="## Находки\n")
        names = sorted(p.name for p in self.work.iterdir())
        self.assertEqual(names, ["report.md"])
        # no path argument exists: an arbitrary target is refused by argparse
        self.assertNotEqual(run("append", "--work", self.work, "--out", "rp1.md", input="x").returncode, 0)

    def test_coverage_and_rollover_sections(self):
        corpus = self.tmp / "corpus"; corpus.mkdir()
        (corpus / "Security.jsonl").write_text(
            '{"EventID":4625,"TargetUserName":"alice","EventRecordID":1}\n'
            '{"EventID":4624,"TargetUserName":"bob","EventRecordID":2}\n', encoding="utf-8")
        (self.work / "worklist.tsv").write_text("# sherlock-host\tfixture\n", encoding="utf-8")
        r = run("coverage", "--work", self.work, "--corpus", corpus)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("rollover", "--work", self.work, "--corpus", corpus, "--cite", "Security.jsonl")
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.rep.read_text(encoding="utf-8")
        self.assertIn("## Покрытие", t)
        self.assertIn("Security.jsonl", t)
        self.assertLess(t.index("## Покрытие"), t.index("# Окно записей"))
        self.assertEqual(t.rstrip().splitlines()[-1], MARKER)


if __name__ == "__main__":
    unittest.main()
