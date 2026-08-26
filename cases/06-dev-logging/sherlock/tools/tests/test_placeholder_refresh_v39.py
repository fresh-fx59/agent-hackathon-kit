#!/usr/bin/env python3
"""fix 5b — the report placeholder was a write-once lie.

MEASURED, v38 paid run 20260826T132832Z-v38. `checkpoint.py::init()` wrote the
stub only `if not report.exists() or not report.read_text().strip()`. The FIRST
init ran at 13 of 262 rows resolved and froze «Разобрано строк рабочего списка:
13 из 262» into `report.md`; every later init left it alone. End state on disk:
`checkpoint.json` = `{"state": "ready_for_synthesis", "resolved": 262,
"total": 262}` at 15:35:05Z, beside a 192-byte `report.md` still claiming 13 of
262. The run's own progress signal read 5 % complete when it was 100 %.

An artifact that contradicts the state file next to it is worse than no artifact.
So `init` regenerates the placeholder EVERY time — but only when what is on disk
is byte-identical to a placeholder this tool could have generated. Never a fuzzy
match, and real content is never overwritten: this tool must not be able to
destroy the report it exists to protect.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
V39 = os.path.join(SKILLS, "v39", "tools")
CHECKPOINT = os.path.join(V39, "checkpoint.py")

HEADER = "# id\tverdict\taxis\tref\tfrequency\trecord\n"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def worklist(total, resolved):
    rows = []
    for i in range(1, total + 1):
        verdict = "D" if i <= resolved else "?"
        rows.append("g%03d\t%s\trare\thost/app.log:1\tn=1\tevent\n" % (i, verdict))
    return HEADER + "".join(rows)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ckpt-v39-")
        self.work = os.path.join(self.dir, "work")
        os.makedirs(self.work)
        self.report = os.path.join(self.work, "report.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_worklist(self, total, resolved):
        with open(os.path.join(self.work, "worklist.tsv"), "w", encoding="utf-8") as fh:
            fh.write(worklist(total, resolved))

    def init(self):
        p = subprocess.run([sys.executable, CHECKPOINT, "init", "--work", self.work],
                           capture_output=True, text=True, cwd=self.dir)
        self.assertEqual(0, p.returncode, p.stderr)
        return json.loads(p.stdout)

    def report_text(self):
        with open(self.report, encoding="utf-8") as fh:
            return fh.read()


class PlaceholderTracksTheState(Base):
    def test_13_of_262_then_262_of_262(self):
        """The exact sequence the paid run walked, and its exact contradiction."""
        self.write_worklist(262, 13)
        row = self.init()
        self.assertEqual("resume_triage", row["state"])
        first = self.report_text()
        self.assertIn("13 из 262", first)
        self.assertEqual("created", row["report"])

        self.write_worklist(262, 262)
        row = self.init()
        self.assertEqual("ready_for_synthesis", row["state"])
        self.assertEqual("regenerated", row["report"])
        second = self.report_text()
        self.assertNotIn("13 из 262", second)
        self.assertIn("262 из 262", second)
        # the state file's own words, in the artifact beside it
        self.assertIn("ready_for_synthesis", second)
        # and the next action, named
        self.assertIn("СЛЕДУЮЩЕЕ ДЕЙСТВИЕ", second)
        self.assertIn("### Н-n", second)

    def test_partial_placeholder_names_the_remaining_work(self):
        self.write_worklist(100, 40)
        self.init()
        text = self.report_text()
        self.assertIn("40 из 100", text)
        self.assertIn("СЛЕДУЮЩЕЕ ДЕЙСТВИЕ", text)
        self.assertIn("60", text)

    def test_placeholder_carries_the_incomplete_marker(self):
        self.write_worklist(10, 10)
        self.init()
        cp = load(CHECKPOINT, "v39_checkpoint")
        self.assertIn(cp.PLACEHOLDER_MARKER, self.report_text())


class RealContentIsNeverOverwritten(Base):
    REAL = ("# Отчёт Sherlock\n\n## Находки\n\n### Н-1 · 3proxy установлен\n\n"
            "что сломано: служба 3proxy\n\nулики: System.jsonl:263 «3proxy»\n\n"
            "исход: успех\n")

    def test_a_real_report_survives_init(self):
        self.write_worklist(262, 262)
        with open(self.report, "w", encoding="utf-8") as fh:
            fh.write(self.REAL)
        row = self.init()
        self.assertEqual("preserved", row["report"])
        self.assertEqual(self.REAL, self.report_text())

    def test_a_placeholder_with_one_real_line_appended_survives(self):
        """Incremental synthesis (fix 5c) appends INTO this file. The first real
        line makes it content, and content is never regenerated — otherwise this
        tool would delete the very partial report 5c exists to preserve."""
        self.write_worklist(262, 13)
        self.init()
        with open(self.report, "a", encoding="utf-8") as fh:
            fh.write("\n## Находки\n\n### Н-1 · нечто\n")
        before = self.report_text()
        self.write_worklist(262, 262)
        row = self.init()
        self.assertEqual("preserved", row["report"])
        self.assertEqual(before, self.report_text())

    def test_an_almost_placeholder_is_not_a_placeholder(self):
        """Never a fuzzy match: one edited character makes it the arm's text."""
        self.write_worklist(262, 13)
        self.init()
        edited = self.report_text().replace("Отчёт Sherlock", "Отчёт Sherlock (мой)")
        with open(self.report, "w", encoding="utf-8") as fh:
            fh.write(edited)
        self.write_worklist(262, 262)
        row = self.init()
        self.assertEqual("preserved", row["report"])
        self.assertEqual(edited, self.report_text())

    def test_the_v38_stub_shape_is_recognised_and_refreshed(self):
        """A run that started under v38 and finished under v39 must not keep the
        frozen count: the legacy shape is one this tool could have generated."""
        self.write_worklist(262, 262)
        with open(self.report, "w", encoding="utf-8") as fh:
            fh.write("# Отчёт Sherlock\n\n"
                     "Состояние: частичный отчёт; синтез ещё не завершён.\n\n"
                     "Разобрано строк рабочего списка: 13 из 262.\n")
        row = self.init()
        self.assertEqual("regenerated", row["report"])
        self.assertIn("262 из 262", self.report_text())


class ShapesAreExact(unittest.TestCase):
    def setUp(self):
        self.cp = load(CHECKPOINT, "v39_checkpoint_shapes")

    def test_every_generated_placeholder_is_recognised_as_one(self):
        for state, resolved, total in (("resume_triage", 13, 262),
                                       ("ready_for_synthesis", 262, 262),
                                       ("resume_triage", 0, 1)):
            row = {"state": state, "resolved": resolved, "total": total,
                   "unresolved": total - resolved}
            text = self.cp.render_placeholder(row)
            self.assertTrue(self.cp.is_placeholder(text),
                            "%s not recognised:\n%s" % (state, text))

    def test_nothing_else_is(self):
        for text in ("", "# Отчёт Sherlock\n", "## Находки\n### Н-1 · x\n",
                     "# Отчёт Sherlock\n\nСостояние: частичный отчёт.\n"):
            self.assertFalse(self.cp.is_placeholder(text), repr(text))


class FrozenArmsUntouched(unittest.TestCase):
    def test_v38_keeps_the_write_once_stub(self):
        with open(os.path.join(SKILLS, "v38", "tools", "checkpoint.py"),
                  encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("if not report.exists() or not report.read_text(", body)
        self.assertNotIn("is_placeholder", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
