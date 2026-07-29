#!/usr/bin/env python3
"""Tests for tools/citecheck.py — the content-comparing citation checker.

The load-bearing test is `test_the_real_misattribution`: on the corporate model a
run cited `Linux_2k.log:106` as evidence for a `session opened for user test`
claim. Line 106 is an authentication-failure line; the real occurrences are at
92, 585, 586, 587. Real file, real line, WRONG CONTENT — the shape a range-check
cannot catch. If that case ever stops being caught, this suite goes red.

    python3 tools/tests/test_citecheck.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
CITECHECK = os.path.join(TOOLS, "citecheck.py")
REAL_LINUX = os.path.expanduser(
    "~/hack/logalyzer-real-world-testset/real-logs/Linux/Linux_2k.log")


def run(report_text, corpus=FIX, args=()):
    """Run citecheck over a report string; return (rc, parsed json)."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8",
                                     delete=False) as fh:
        fh.write(report_text)
        path = fh.name
    try:
        p = subprocess.run(
            [sys.executable, CITECHECK, path, "--corpus", corpus, "--json", *args],
            capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout), p.stderr
    finally:
        os.unlink(path)


def verdicts(data):
    return [c["verdict"] for c in data["citations"]]


class TheRealMisattribution(unittest.TestCase):
    """The finding this tool exists for."""

    CLAIM = ("Атакующий получил успешный вход в систему: "
             "session opened for user test by (uid=509) — "
             "%s:%d")

    def test_wrong_content_is_caught(self):
        rc, d, _ = run(self.CLAIM % ("linux_syslog_excerpt.log", 106))
        self.assertEqual(verdicts(d), ["wrong-content"])
        self.assertEqual(rc, 1, "a bad citation must fail the exit code")
        self.assertEqual(d["summary"]["wrong-content"], 1)

    def test_correct_line_passes(self):
        rc, d, _ = run(self.CLAIM % ("linux_syslog_excerpt.log", 92))
        self.assertEqual(verdicts(d), ["ok"])
        self.assertEqual(rc, 0)

    @unittest.skipUnless(os.path.exists(REAL_LINUX),
                         "real loghub testset not present on this box")
    def test_against_the_real_file(self):
        """Same assertion, against the actual 2,000-line log the run cited."""
        corpus = os.path.dirname(REAL_LINUX)
        rc, d, _ = run(self.CLAIM % ("Linux_2k.log", 106), corpus=corpus)
        self.assertEqual(verdicts(d), ["wrong-content"])
        for good in (92, 585, 586, 587):
            _rc, d, _ = run(self.CLAIM % ("Linux_2k.log", good), corpus=corpus)
            self.assertEqual(verdicts(d), ["ok"], "line %d is a real occurrence" % good)


class Verdicts(unittest.TestCase):

    def test_out_of_range(self):
        rc, d, _ = run("session opened for user test — linux_syslog_excerpt.log:99999")
        self.assertEqual(verdicts(d), ["out-of-range"])
        self.assertEqual(rc, 1)

    def test_missing_file(self):
        rc, d, _ = run("что-то произошло, session opened — no_such_file.log:12")
        self.assertEqual(verdicts(d), ["missing-file"])
        self.assertEqual(rc, 1)

    def test_verbatim_quote_passes(self):
        rc, d, _ = run('linux_syslog_excerpt.log:106 — "authentication failure; '
                       'logname= uid=0 euid=0"')
        self.assertEqual(verdicts(d), ["ok"])

    def test_fabricated_quote_is_caught(self):
        rc, d, _ = run('linux_syslog_excerpt.log:106 — '
                       '"Accepted password for root from 10.0.0.9 port 22 ssh2"')
        self.assertEqual(verdicts(d), ["wrong-content"])

    def test_russian_only_claim_is_unverifiable_not_wrong(self):
        """Cross-language claims must not be called fabrications.

        Line 106 IS an authentication failure, so «неудачная попытка входа» is a
        true claim about it — there is simply nothing token-comparable. Honest
        answer: unverifiable. Calling it wrong-content would train the model to
        delete good evidence."""
        rc, d, _ = run("Зафиксирована неудачная попытка входа — "
                       "linux_syslog_excerpt.log:106")
        self.assertEqual(verdicts(d), ["unverifiable"])
        self.assertEqual(rc, 0, "unverifiable is not a failure")

    def test_table_style_citation(self):
        report = ("| файл | строка | цитата |\n"
                  "|---|---|---|\n"
                  "| linux_syslog_excerpt.log | 92 | session opened for user test |\n"
                  "| linux_syslog_excerpt.log | 106 | session opened for user test |\n")
        rc, d, _ = run(report)
        self.assertEqual(verdicts(d), ["ok", "wrong-content"])

    def test_line_range_citation(self):
        rc, d, _ = run("session opened for user test — linux_syslog_excerpt.log:92-94")
        self.assertEqual(verdicts(d), ["ok"])


class CalibratedAgainstRealTranscripts(unittest.TestCase):
    """Each of these was a FALSE POSITIVE found by running the checker over the
    18 saved transcripts in knowledge/measure/**/raw/. They are the reason the
    quote path has a token floor and numbers do not count as words."""

    def test_short_quoted_search_term_is_not_a_claimed_quote(self):
        """«всегда искать "Accepted password"» is a procedure, not evidence."""
        rc, d, _ = run('всегда искать "Accepted password" в окне атаки — '
                       'linux_syslog_excerpt.log:106')
        self.assertEqual(verdicts(d), ["unverifiable"])
        self.assertEqual(rc, 0)

    def test_a_list_of_line_numbers_is_not_a_content_claim(self):
        rc, d, _ = run("- Все 43 строки: linux_syslog_excerpt.log:16, 75, 80, "
                       "139, 147, 164, 190")
        self.assertEqual(verdicts(d), ["unverifiable"])

    def test_backticked_citation_does_not_unbalance_the_quote_matcher(self):
        """Stripping `file:line` used to leave orphaned backticks, after which
        the quote matcher paired the wrong delimiters and read prose as a quote."""
        rc, d, _ = run("2. `linux_syslog_excerpt.log:92` — session opened for "
                       "user test, `uid=509`")
        self.assertEqual(verdicts(d), ["ok"])


class NotCitations(unittest.TestCase):
    """Measurement artifact #6: `line_refs` counted any `:\\d+`, so timestamps
    inflated it — an OpenSSH baseline scored 114 "refs" with zero citations."""

    def test_timestamps_are_not_citations(self):
        rc, d, _ = run("Инцидент начался в 20:29:26 и закончился 2026-07-28 10:00:15, "
                       "пик пришёлся на 01:30:59.")
        self.assertEqual(d["citations"], [])
        self.assertEqual(rc, 0)

    def test_bare_numbers_and_urls_are_not_citations(self):
        rc, d, _ = run("Подключение к http://127.0.0.1:8317/v1 заняло 3000 ms, "
                       "порт 22:22 закрыт.")
        self.assertEqual(d["citations"], [])


class Summary(unittest.TestCase):

    def test_counts_and_percentage(self):
        report = ("session opened for user test — linux_syslog_excerpt.log:92\n"
                  "session opened for user test — linux_syslog_excerpt.log:106\n"
                  "session opened for user test — no_such_file.log:4\n")
        rc, d, _ = run(report)
        s = d["summary"]
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["ok"], 1)
        self.assertEqual(s["wrong-content"], 1)
        self.assertEqual(s["missing-file"], 1)
        self.assertEqual(s["verified_pct"], 33.3)

    def test_text_output_is_human_readable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8",
                                         delete=False) as fh:
            fh.write("session opened for user test — linux_syslog_excerpt.log:106")
            path = fh.name
        try:
            p = subprocess.run([sys.executable, CITECHECK, path, "--corpus", FIX],
                               capture_output=True, text=True)
        finally:
            os.unlink(path)
        self.assertIn("wrong-content", p.stdout)
        self.assertIn("106", p.stdout)
        self.assertIn("authentication failure", p.stdout,
                      "must show what the line ACTUALLY says")

    def test_reads_stdin(self):
        p = subprocess.run([sys.executable, CITECHECK, "-", "--corpus", FIX, "--json"],
                           input="session opened for user test — "
                                 "linux_syslog_excerpt.log:92",
                           capture_output=True, text=True)
        self.assertEqual(json.loads(p.stdout)["summary"]["ok"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
