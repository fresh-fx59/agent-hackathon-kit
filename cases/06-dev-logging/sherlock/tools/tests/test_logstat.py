#!/usr/bin/env python3
"""Tests for tools/logstat.py — the cheap per-file triage the skill calls FIRST.

Coverage is the binding constraint (100 % → 73 % → 18 % recall on one corpus and
one model, decided purely by which files were opened). logstat exists so that
choosing a file costs one cheap call instead of reading it.

    python3 tools/tests/test_logstat.py
"""
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
CORPUS = os.path.join(FIX, "corpus")
LOGSTAT = os.path.join(TOOLS, "logstat.py")


def run(*args):
    p = subprocess.run([sys.executable, LOGSTAT, *args, "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def one(*args):
    return run(*args)["files"][0]


class SingleFile(unittest.TestCase):

    def test_line_count_and_bytes(self):
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertEqual(f["lines"], 15)
        self.assertEqual(f["bytes"],
                         os.path.getsize(os.path.join(CORPUS, "api", "app.log")))
        self.assertFalse(f["sampled"])

    def test_iso_time_range(self):
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertEqual(f["first_ts"], "2026-07-28T10:00:01.114Z")
        self.assertEqual(f["last_ts"], "2026-07-28T10:00:21.400Z")
        self.assertEqual(f["time_format"], "iso")

    def test_bsd_syslog_time_range(self):
        f = one(os.path.join(CORPUS, "payments", "payments.log"))
        self.assertEqual(f["first_ts"], "Jul 28 10:00:00")
        self.assertEqual(f["last_ts"], "Jul 28 10:00:25")
        self.assertEqual(f["time_format"], "bsd-syslog")

    def test_clf_time_range(self):
        f = one(os.path.join(CORPUS, "gateway", "access.log"))
        self.assertEqual(f["first_ts"], "28/Jul/2026:10:00:01")
        self.assertEqual(f["time_format"], "clf")

    def test_no_timestamp_is_null_not_a_guess(self):
        """Year-1900 sentinels are how the old pipeline died. Say null instead."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plain.txt")
            with open(p, "w") as fh:
                fh.write("hello\nworld\n")
            f = one(p)
            self.assertIsNone(f["first_ts"])
            self.assertIsNone(f["time_format"])


class Severity(unittest.TestCase):

    def test_known_levels_are_counted(self):
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertEqual(f["severity"],
                         {"ERROR": 3, "WARN": 1, "INFO": 5, "DEBUG": 4})

    def test_invented_vocabulary_is_discovered_without_a_dictionary(self):
        """R2: a team's bespoke severity words must still surface."""
        f = one(os.path.join(CORPUS, "api", "app.log"))
        vocab = dict(f["vocabulary"])
        self.assertEqual(vocab.get("ALARM"), 1)
        self.assertEqual(vocab.get("FATALITY"), 1)

    def test_identifier_prefixes_are_not_vocabulary(self):
        """ORD-77421 must not register ORD as a severity word."""
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertNotIn("ORD", dict(f["vocabulary"]))


class Shapes(unittest.TestCase):

    def test_repeated_shape_collapses(self):
        f = one(os.path.join(CORPUS, "api", "app.log"), "--top", "3")
        top = f["top_shapes"][0]
        self.assertEqual(top["count"], 5)
        self.assertIn("accepted from customer", top["shape"])
        self.assertNotIn("77421", top["shape"], "digits must be masked")

    def test_top_n_is_respected(self):
        f = one(os.path.join(CORPUS, "api", "app.log"), "--top", "2")
        self.assertEqual(len(f["top_shapes"]), 2)

    def test_rare_shapes_surface_the_one_off_line(self):
        f = one(os.path.join(CORPUS, "api", "app.log"), "--top", "2")
        rare = " ".join(s["shape"] for s in f["rare_shapes"])
        self.assertIn("circuit breaker open", rare)
        self.assertTrue(all(s["count"] == 1 for s in f["rare_shapes"]))

    def test_rare_never_relists_a_top_shape(self):
        """Bottom-N on a 3-shape file would otherwise print the commonest line
        under the heading "rare" — a small lie that costs the model a step."""
        f = one(os.path.join(CORPUS, "gateway", "access.log"), "--top", "2")
        top = {s["shape"] for s in f["top_shapes"]}
        self.assertEqual(len(f["rare_shapes"]), 1)
        self.assertFalse(top & {s["shape"] for s in f["rare_shapes"]})

    def test_rare_is_empty_when_top_already_shows_everything(self):
        f = one(os.path.join(CORPUS, "gateway", "access.log"), "--top", "9")
        self.assertEqual(f["rare_shapes"], [])

    def test_distinct_shape_count(self):
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertEqual(f["distinct_shapes"], 6)


class Sampling(unittest.TestCase):
    """R3: a multi-GB file must not cost a multi-GB analysis pass. But a sampled
    count that is presented as a total is a measurement artifact — so the line
    count stays exact, the sample is flagged, and nothing is silently scaled."""

    def big(self, d, n=5000):
        p = os.path.join(d, "big.log")
        with open(p, "w") as fh:
            for i in range(1, n + 1):
                fh.write("2026-07-28T10:00:%02dZ INFO worker item %d done\n"
                         % (i % 60, i))
        return p

    def test_full_scan_reports_sample_rate_one(self):
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertEqual(f["sample_rate"], 1.0)
        self.assertEqual(f["analysed_lines"], 15)

    def test_sampling_is_flagged_and_counts_stay_raw(self):
        with tempfile.TemporaryDirectory() as d:
            f = one(self.big(d), "--max-lines", "500")
            self.assertEqual(f["lines"], 5000, "line count must stay exact")
            self.assertTrue(f["sampled"])
            self.assertLess(f["analysed_lines"], 5000)
            self.assertLess(f["sample_rate"], 1.0)
            self.assertEqual(f["severity"]["INFO"], f["analysed_lines"],
                             "counts are raw over the sample, never extrapolated")
            self.assertEqual(f["first_ts"], "2026-07-28T10:00:01Z")
            self.assertEqual(f["last_ts"], "2026-07-28T10:00:20Z",
                             "the tail is always analysed, so last_ts is real")


class Binary(unittest.TestCase):

    def test_binary_file_is_flagged_not_quoted(self):
        """SKILL.md step 1b wants every file closed explicitly. A file it cannot
        read must come back as 'unreadable', not as mojibake shapes."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "blob.bin")
            with open(p, "wb") as fh:
                fh.write(b"\x00\x01\x02\xff" * 256)
            f = one(p)
            self.assertTrue(f["binary"])
            self.assertEqual(f["top_shapes"], [])
            self.assertEqual(f["vocabulary"], [])

    def test_text_file_is_not_binary(self):
        f = one(os.path.join(CORPUS, "api", "app.log"))
        self.assertFalse(f["binary"])


class Corpus(unittest.TestCase):

    def test_directory_recursion(self):
        d = run(CORPUS)
        paths = sorted(f["path"] for f in d["files"])
        self.assertEqual(paths, ["api/app.log", "gateway/access.log",
                                 "payments/payments.log"])
        self.assertEqual(d["totals"]["files"], 3)
        self.assertEqual(d["totals"]["lines"], 15 + 7 + 6)

    def test_gzip_is_read_not_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(CORPUS, "api", "app.log")
            dst = os.path.join(t, "app.log.gz")
            with open(src, "rb") as i, gzip.open(dst, "wb") as o:
                shutil.copyfileobj(i, o)
            f = one(dst)
            self.assertEqual(f["lines"], 15)
            self.assertTrue(f["compressed"])
            self.assertEqual(f["first_ts"], "2026-07-28T10:00:01.114Z")


class TextOutput(unittest.TestCase):

    def test_human_table(self):
        p = subprocess.run([sys.executable, LOGSTAT, CORPUS],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("api/app.log", p.stdout)
        self.assertIn("ERROR", p.stdout)
        self.assertIn("FATALITY", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
