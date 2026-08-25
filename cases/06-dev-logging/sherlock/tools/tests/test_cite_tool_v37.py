#!/usr/bin/env python3
"""v37: a tool that hands back a finished citation, so none is typed by hand.

MEASURED on the full winevtx run (sherlock-winevtx-runs-v36-full-r1/
20260825T061049Z-v36, exit 0, gates.json verdict=blocking). citecheck resolved
41 references in that report: 18 ok, 17 no-quote, 6 wrong-content. The 23 bad
ones are NOT bad forensics — `System.jsonl:263` really is the 3proxy install
(Service Control Manager, EventID 7045), and the report named 3proxy 16 times
and reached the correct verdict. They are bad GRAMMAR:

  * no-quote (17)     — a `path:line` with no verbatim fragment beside it.
  * wrong-content (6) — a fragment that was the model own prose, not the log.
                        On System.jsonl:263 it offered «входящего доступа,
                        установленная от имени пользователя root. улики:» and
                        matched 1 of 7 words (14 %).

More prose in SKILL.md is the fix that already failed: citecheck quote_example
docstring records D07 spending 40 turns and 11.15M tokens, and D04 spending 123
turns, reverse-engineering this checker rather than adding a pair of quotes. So
v37 adds cite.py, whose whole job is to print a citation that citecheck accepts.

The contract asserted here, and it is a round-trip rather than a format guess:
whatever cite.py prints must be accepted by citecheck as `ok`. If the two ever
drift apart, this file goes red.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
V37 = os.path.join(SKILLS, "v37", "tools")
CITE = os.path.join(V37, "cite.py")
CITECHECK = os.path.join(V37, "citecheck.py")

# A real winevtx record, trimmed. This is the line the full run cited six times
# and got wrong every time.
LINE_263 = json.dumps({"Event": {"System": {
    "Provider": {"#attributes": {"Name": "Service Control Manager"}},
    "EventID": {"#text": 7045}},
    "EventData": {"ServiceName": "3proxy", "ImagePath": "C:\\\\3proxy\\\\3proxy.exe",
                  "AccountName": "LocalSystem", "StartType": "auto start"}}},
    ensure_ascii=False)
NOISE = json.dumps({"Event": {"System": {"EventID": {"#text": 4624}}}})


class CorpusCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.corpus = os.path.join(self.dir, "corpus")
        os.makedirs(self.corpus)
        with open(os.path.join(self.corpus, "System.jsonl"), "w",
                  encoding="utf-8") as fh:
            for i in range(1, 263):
                fh.write(NOISE + "\n")
            fh.write(LINE_263 + "\n")

    def cite(self, *args):
        return subprocess.run([sys.executable, CITE, "--corpus", self.corpus]
                              + list(args), capture_output=True, text=True)

    def citecheck(self, report_text):
        path = os.path.join(self.dir, "report.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report_text)
        return subprocess.run(
            [sys.executable, CITECHECK, path, "--corpus", self.corpus,
             "--require-quote"], capture_output=True, text=True)

    def counters(self, out):
        """Read citecheck own tally line, not a substring of the whole report.

        The naive `assertNotIn("wrong-content", out)` is a false red: the
        summary prints `wrong-content 0`, so the string is present exactly when
        the count is zero.
        """
        m = re.search(r"итого: (\d+) ссылок — ok (\d+), wrong-content (\d+), "
                      r"out-of-range (\d+), missing-file (\d+), unverifiable "
                      r"(\d+), без цитаты (\d+)", out)
        self.assertIsNotNone(m, "no citecheck tally line in:\\n" + out)
        keys = ("total", "ok", "wrong-content", "out-of-range",
                "missing-file", "unverifiable", "no-quote")
        return dict(zip(keys, (int(g) for g in m.groups())))


class TestCiteExists(CorpusCase):
    def test_tool_is_present_and_runs(self):
        self.assertTrue(os.path.exists(CITE), "v37 must ship tools/cite.py")
        done = self.cite("System.jsonl:263")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.strip(), "cite.py printed nothing")


class TestRoundTrip(CorpusCase):
    def test_output_is_accepted_by_citecheck_as_ok(self):
        """The whole point: paste what cite.py printed, and the gate says ok."""
        cited = self.cite("System.jsonl:263").stdout.strip()
        self.assertIn("System.jsonl:263", cited)
        c = self.counters(self.citecheck(
            "## Наблюдения\n\n- служба 3proxy установлена: %s\n" % cited).stdout)
        self.assertEqual(c["ok"], 1, c)
        self.assertEqual(c["wrong-content"], 0, c)
        self.assertEqual(c["no-quote"], 0, c)

    def test_contains_centres_the_quote_on_the_evidence(self):
        """A citation whose quote is boilerplate is legal and useless."""
        cited = self.cite("System.jsonl:263", "--contains", "3proxy").stdout
        self.assertIn("3proxy", cited)
        c = self.counters(self.citecheck("- улика: %s\n" % cited.strip()).stdout)
        self.assertEqual(c["ok"], 1, c)
        self.assertEqual(c["wrong-content"], 0, c)
        self.assertEqual(c["no-quote"], 0, c)


class TestRefusals(CorpusCase):
    def test_out_of_range_line_is_refused_not_invented(self):
        done = self.cite("System.jsonl:99999")
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("«", done.stdout)

    def test_missing_file_is_refused(self):
        done = self.cite("Nope.jsonl:1")
        self.assertNotEqual(done.returncode, 0)

    def test_contains_that_does_not_appear_is_refused(self):
        """Refusing beats quoting the wrong half of the right line."""
        done = self.cite("System.jsonl:263", "--contains", "mimikatz")
        self.assertNotEqual(done.returncode, 0)


class TestDriftGuard(CorpusCase):
    def test_cite_reuses_citechecks_own_builder(self):
        """cite.py must not re-implement the quote rules citecheck enforces."""
        with open(CITE, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("quote_example", src,
                      "cite.py must call citecheck.quote_example, not "
                      "re-derive the delimiter and length rules")


class TestFrozenArms(unittest.TestCase):
    def test_v36_does_not_gain_the_tool(self):
        """v36 has a paid result attached; the repair lands one version later."""
        self.assertFalse(
            os.path.exists(os.path.join(SKILLS, "v36", "tools", "cite.py")),
            "v36 is frozen — cite.py belongs to v37")


if __name__ == "__main__":
    unittest.main(verbosity=2)
