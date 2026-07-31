#!/usr/bin/env python3
"""Tests for skills/v11/tools/logmap.py — the map + worklist + rate table.

logmap replaces logstat for the v11 arm. It is an ARM-LOCAL tool: the frozen arms
v8-v10 keep logstat.py, and their numbers are already quoted, so this file lives
next to the arm rather than in the shared tools/ directory.

What is asserted here is exactly what the measurements said breaks silently:

* an hour extractor that only knows `HH:MM:SS` returns NOTHING on epoch-ms,
  epoch-float, combined-log-format and bespoke pipe stamps — with no error;
* a severity dictionary is blind to at least one real corpus per corpus;
* sampling and rare-event detection contradict each other;
* a worklist with no cap blows the context it was supposed to save.

    python3 tools/tests/test_logmap.py
"""
import gzip
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
LOGMAP = os.path.join(SHERLOCK, "skills", "v11", "tools", "logmap.py")

_spec = importlib.util.spec_from_file_location("logmap_v11", LOGMAP)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def word(i):
    """A distinct WORD per index. Digits are masked away by design, so a fixture
    that varies only a number produces exactly one template — which is correct
    behaviour and a useless test."""
    a = "abcdefghijklmnopqrstuvwxyz"
    return a[i % 26] + a[(i // 26) % 26] + a[(i // 676) % 26]


def write(root, rel, lines, gz=False):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    op = gzip.open if gz else open
    with op(p, "wt", encoding="utf-8") as fh:
        for l in lines:
            fh.write(l + "\n")
    return p


def run(corpus, out, *extra):
    p = subprocess.run([sys.executable, LOGMAP, corpus, "--out", out, *extra],
                       capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr
    return p


def rows(out):
    """-> [(id, verdict, axis, cite, n, text)] from worklist.tsv"""
    got = []
    with open(os.path.join(out, "worklist.tsv"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            got.append(line.rstrip("\n").split("\t"))
    return got


class TimeAxisIsSevenShapesNotOne(unittest.TestCase):
    """The correction that matters most: a naive HH:MM:SS reader is silent, not
    wrong, on four of these — and silence is indistinguishable from 'nothing
    happened here'."""

    def test_iso(self):
        self.assertEqual(L.hour_of("2026-07-28 13:05:01 ok", "iso")[0], 13)

    def test_day_first_with_dots(self):
        self.assertEqual(L.hour_of("28.07.2026 17:40:11,000 ОШИБКА", "dmy")[0], 17)

    def test_bsd_syslog_without_a_year(self):
        self.assertEqual(L.hour_of("Jul 28 13:31:02 node-a sudo:", "bsd")[0], 13)

    def test_combined_log_format(self):
        """The hour sits behind a colon, which the bare-clock guard rejects."""
        line = '10.42.3.7 - - [28/Jul/2026:14:01:57 +0000] "POST /x HTTP/1.1" 200'
        self.assertEqual(L.hour_of(line, "clf")[0], 14)
        self.assertIsNone(L.hour_of(line, "clock")[0])

    def test_bespoke_pipe_stamp(self):
        self.assertEqual(L.hour_of("20260728|173351.000|+0300|x|y", "compact")[0], 17)

    def test_epoch_is_key_agnostic_and_float_aware(self):
        """`time` in ms, `ts` in seconds-as-float, `__REALTIME_TIMESTAMP` in µs —
        three different keys, and one of them is not even a JSON field."""
        self.assertEqual(L.hour_of('{"level":50,"time":1785229451002}', "epoch")[0],
                         L.hour_of('{"ts":1785229451.002000}', "epoch")[0])
        self.assertEqual(L.hour_of('{"ts":1785229200.000000}', "epoch")[0], 9)
        self.assertEqual(L.hour_of('{"time":1785229200000}', "epoch")[0], 9)
        self.assertEqual(L.hour_of("__REALTIME_TIMESTAMP=1785229200000000",
                                   "epoch")[0], 9)

    def test_a_number_that_is_not_a_time_is_rejected(self):
        self.assertIsNone(L.epoch_hour("1234567890123456789"))
        self.assertIsNone(L.epoch_hour("0000000000"))
        self.assertIsNone(L.epoch_hour("9999999999999"))

    def test_a_column_of_long_identifiers_is_not_a_clock(self):
        """Found by running the tool on a corpus it had never seen: 16-digit block
        identifiers divide into a perfectly plausible microsecond epoch, so every
        record got an hour and the file was handed a time axis made of random
        numbers — silently. A clock advances and covers ONE capture."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            import random
            rnd = random.Random(7)
            lines = ["081109 %06d %d ok block blk_%d terminating"
                     % (203615 + i, 100 + i % 40, rnd.randrange(10 ** 15, 10 ** 16))
                     for i in range(600)]
            write(corpus, "hdfs.log", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("время: НЕТ", body,
                          "identifiers were accepted as a clock:\n%s" % body[:900])

    def test_a_real_epoch_column_is_still_accepted(self):
        """Negative control for the guard above — it must not eat the real thing."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc.json",
                  ['{"ts":%.6f,"msg":"captured","order":"ORD-%d"}'
                   % (1785229200.0 + i * 1.5, i) for i in range(600)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("время: epoch", body)

    def test_a_file_with_no_usable_time_axis_says_so_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "boot/dmesg", ["[276537.000000] link becomes ready %d" % i
                                         for i in range(300)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("время: НЕТ", body)
            self.assertIn("ВНИМАНИЕ", body,
                          "a file dropped from the rate pass must be announced, "
                          "not quietly skipped")


class SeverityIsDiscoveredNotLookedUp(unittest.TestCase):
    """No dictionary can carry every team's invented vocabulary, so the tool
    carries none. The disqualification check greps this file for level words."""

    def test_the_tool_contains_no_severity_vocabulary(self):
        import re
        body = open(LOGMAP, encoding="utf-8").read()
        hits = re.findall(r"\b(ERROR|WARN|WARNING|FATAL|CRITICAL|INFO|DEBUG|"
                          r"TRACE|PANIC)\b", body)
        self.assertEqual(hits, [], "a severity word leaked into the tool: %s" % hits)

    def test_a_pipe_column_is_found_as_the_level_axis(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = ["20260728|12%04d.000|+0300|svc|node-b|CHATTER|EVAL|order=ORD-%d"
                     % (i, i) for i in range(400)]
            lines.append("20260728|173351.000|+0300|svc|node-b|FATALITY|POST|"
                         "order=ORD-9|final_minor=-486212")
            write(corpus, "inhouse/bespoke.plog", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("pipe:5", body)
            self.assertIn("FATALITY", body)

    def test_a_cyrillic_kv_field_is_found_as_the_level_axis(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = ["28.07.2026 12:00:%02d,000 [адаптер] УРОВЕНЬ=ИНФО поток=%d | "
                     "проводка создана | заказ=ORD-%d" % (i % 60, i % 4, i)
                     for i in range(400)]
            lines.append("28.07.2026 17:40:11,000 [адаптер] УРОВЕНЬ=ОШИБКА поток=4 | "
                         "курс валют устарел | заказ=ORD-1")
            write(corpus, "inhouse/ru.log", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("kv:УРОВЕНЬ", body)
            self.assertIn("ОШИБКА", body)

    def test_a_numeric_json_level_is_found_even_next_to_a_wordy_field(self):
        """A structural ranking prefers word-like values, so `channel` outranks
        `level`. Carrying every qualifying axis removes the guess."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            chans = ["sms", "email", "push"]
            lines = ['{"level":30,"time":%d,"channel":"%s","msg":"queued"}'
                     % (1785229200000 + i * 100, chans[i % 3]) for i in range(400)]
            lines.append('{"level":50,"time":1785229451002,"channel":"email",'
                         '"msg":"handshake failed"}')
            write(corpus, "apps/svc.json", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("json:level", body)


class FramingMakesOneRecordOutOfManyLines(unittest.TestCase):

    def test_a_stack_trace_is_one_record_and_cites_as_a_range(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for i in range(300):
                lines.append("2026-07-28 09:%02d:00.000 INFO ok order=ORD-%d"
                             % (i % 60, i))
            lines += ["2026-07-28 14:12:33.000 SEVERE unhandled while applying x",
                      "\tat com.acme.PromoCodeResolver.resolve(PromoCodeResolver:41)",
                      "\tat com.acme.CheckoutController.apply(CheckoutController:88)",
                      "Caused by: java.lang.NullPointerException"]
            write(corpus, "apps/app.log", lines)
            run(corpus, out)
            cites = [r[3] for r in rows(out)]
            self.assertTrue(any(c.startswith("apps/app.log:301-") for c in cites),
                            "the trace must be ONE record: %s" % cites[:6])

    def test_a_journald_export_block_is_one_record(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for i in range(200):
                lines += ["__CURSOR=s=abc;i=%d" % i,
                          "__REALTIME_TIMESTAMP=%d" % (1785229200000000 + i * 1000),
                          "_TRANSPORT=stdout",
                          "SYSLOG_IDENTIFIER=kubelet",
                          "_HOSTNAME=node-a",
                          "MESSAGE=probe succeeded pod=svc-%d" % (i % 5),
                          ""]
            lines += ["__CURSOR=s=abc;i=999",
                      "__REALTIME_TIMESTAMP=1785243600000000",
                      "_TRANSPORT=stdout",
                      "SYSLOG_IDENTIFIER=kubelet",
                      "_HOSTNAME=node-a",
                      "MESSAGE=replica set scaled down to 2 from 6",
                      ""]
            write(corpus, "systemd/journal.txt", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("кадрирование block", body)
            hit = [r for r in rows(out) if "scaled down" in r[5]]
            self.assertTrue(hit, "the one-off journald record never reached the "
                                 "worklist")
            self.assertRegex(hit[0][3], r"systemd/journal\.txt:\d+-\d+",
                             "a block record must cite as a RANGE")

    def test_a_cri_wrapped_payload_is_stitched(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for i in range(200):
                lines.append("2026-07-28T09:00:%02d.000000000Z stdout F line %d"
                             % (i % 60, i))
            lines += ["2026-07-28T13:40:00.000000000Z stdout P first half of a ",
                      "2026-07-28T13:40:00.000000000Z stdout F very long payload"]
            write(corpus, "k8s/pods/p.log", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("кадрирование cri", body)
            hit = [r for r in rows(out) if "very long payload" in r[5]]
            self.assertTrue(hit, "the P/F fragments were not joined")
            self.assertIn("first half", hit[0][5])


class TheWorklistIsBoundedAndHonest(unittest.TestCase):

    def test_every_row_starts_unadjudicated(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = ["2026-07-28 09:00:%02d INFO steady thing" % (i % 60)
                     for i in range(300)]
            lines.append("2026-07-28 09:59:00 INFO a shape seen exactly once")
            write(corpus, "a.log", lines)
            run(corpus, out)
            self.assertTrue(rows(out))
            self.assertTrue(all(r[1] == "?" for r in rows(out)))

    def test_the_cap_is_obeyed_and_the_remainder_is_declared(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            for f in range(4):
                lines = ["2026-07-28 09:%02d:00 INFO steady work" % (i % 60)
                         for i in range(1000)]
                lines += ["2026-07-28 09:%02d:00 INFO odd %s %s"
                          % (i % 60, word(f), word(i)) for i in range(60)]
                write(corpus, "svc%d/app.log" % f, lines)
            run(corpus, out, "--worklist-cap", "40", "--per-file-cap", "5")
            got = rows(out)
            self.assertLessEqual(len(got), 40)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("TRUNC=", body, "a truncated file must say how much was "
                                          "left out")

    def test_every_file_gets_a_turn_before_any_file_gets_seconds(self):
        """Pure global rarity ranking lets one chatty file eat the budget; the
        single most expensive failure measured on this corpus was a run that never
        opened 12 of 28 files."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "loud.log", ["2026-07-28 09:00:00 INFO one-off %s" % word(i)
                                       for i in range(600)])
            write(corpus, "quiet.log",
                  ["2026-07-28 09:00:00 INFO steady"] * 200
                  + ["2026-07-28 15:00:00 INFO the single interesting line"])
            run(corpus, out, "--worklist-cap", "12")
            files = {r[3].split(":")[0] for r in rows(out)}
            self.assertIn("quiet.log", files,
                          "the quiet file was crowded out: %s" % files)

    def test_a_file_that_is_not_a_log_is_gated_by_arithmetic_not_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "prose.txt", ["%s %s %s wholly different sentence"
                                        % (word(i), word(i + 7), word(i + 300))
                                        for i in range(500)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("ОСЬ РЕДКОСТИ ОТКЛЮЧЕНА", body)

    def test_a_small_file_is_quoted_whole(self):
        """`replicas: 6` is what turns `--replicas=2` into a defect."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "notes.md", ["# deploy notes", "- svc replicas: 6",
                                       "- limits: memory 2Gi"])
            write(corpus, "a.log", ["2026-07-28 09:00:00 INFO x %d" % i
                                    for i in range(50)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("replicas: 6", body)
            self.assertIn("memory 2Gi", body)

    def test_a_gz_file_is_read_and_numbered_in_the_decompressed_stream(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = ["2026-07-28 09:00:00 INFO steady %d" % (i % 3)
                     for i in range(500)]
            lines[401] = "2026-07-28 09:00:00 INFO the needle in the rotated file"
            write(corpus, "old.log.gz", lines, gz=True)
            run(corpus, out)
            hit = [r for r in rows(out) if "needle" in r[5]]
            self.assertTrue(hit, "the compressed file was skipped")
            self.assertEqual(hit[0][3], "old.log.gz:402")

    def test_colour_codes_do_not_become_part_of_the_template(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = ["\x1b[2m2026-07-28 09:00:%02d.000\x1b[0m \x1b[33mWARN\x1b[0m "
                     "pass took %dms" % (i % 60, 1400 + i) for i in range(300)]
            write(corpus, "ansi.log", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("форм 1 ", body.replace("форм 1(", "форм 1 "))

    def test_nothing_is_written_next_to_the_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "a.log", ["2026-07-28 09:00:00 INFO x %d" % i
                                    for i in range(50)])
            before = sorted(os.listdir(corpus))
            run(corpus, out)
            self.assertEqual(sorted(os.listdir(corpus)), before)

    def test_stdout_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            for f in range(30):
                write(corpus, "svc%02d/app.log" % f,
                      ["2026-07-28 09:%02d:00 INFO u-%d-%d" % (i % 60, f, i)
                       for i in range(200)])
            p = run(corpus, out)
            self.assertLessEqual(len(p.stdout.splitlines()),
                                 L.STDOUT_MAX_LINES + 5)


class TheRateAxisIsFormatAgnostic(unittest.TestCase):

    def test_a_ramp_in_share_is_found_without_knowing_any_field_name(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            base = 1785229200000                      # 09:00 UTC
            for hour in range(7):
                bad = 2 if hour == 0 else 2 + hour * 40
                for i in range(600):
                    t = base + hour * 3600000 + i * 1000
                    lvl = 50 if i < bad else 30
                    lines.append('{"level":%d,"time":%d,"msg":"send","attempt":1}'
                                 % (lvl, t))
            write(corpus, "svc.json", lines)
            run(corpus, out)
            body = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertIn('"level":50', body,
                          "the ramp never reached the rate table:\n%s" % body)

    def test_a_p99_shift_is_found_while_the_median_stays_flat(self):
        """The shape a mean destroys: p50 flat, p99 x10."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for hour in (9, 15):
                for i in range(800):
                    dur = 10 if i % 100 else (120 if hour == 9 else 1300)
                    lines.append('{"start":"2026-07-28T%02d:%02d:00Z",'
                                 '"path":"/api/v1/search","code":200,"duration":%d}'
                                 % (hour, i % 60, dur))
            write(corpus, "gw.json", lines)
            run(corpus, out)
            body = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertIn("слот#", body)
            self.assertIn("p50 10→10", body,
                          "the median must be reported flat next to the p99 "
                          "shift:\n%s" % body)

    def test_the_background_list_is_a_written_measurement(self):
        """A refutation nobody was asked for never gets written down."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            for name, text in (("evict.log", "eviction pass took 1490ms"),
                               ("cache.log", "cache miss")):
                write(corpus, name,
                      ["2026-07-28 %02d:%02d:00 WARN %s" % (hour, i % 60, text)
                       for hour in (9, 15) for i in range(1000)])
            run(corpus, out)
            body = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertIn("\tbg\t", body)
            self.assertIn("eviction", body)


class ItIsDeterministic(unittest.TestCase):

    def test_parallel_and_serial_produce_identical_files(self):
        """Percentiles come from a bounded reservoir; if the seeding depended on
        scheduling, two runs of the same corpus would disagree."""
        with tempfile.TemporaryDirectory() as d:
            corpus = os.path.join(d, "c")
            for f in range(3):
                write(corpus, "svc%d.log" % f,
                      ["2026-07-28 %02d:%02d:00 INFO v=%d"
                       % (9 + (i // 400), i % 60, i % 97) for i in range(1200)])
            a, b = os.path.join(d, "a"), os.path.join(d, "b")
            run(corpus, a, "--jobs", "1")
            run(corpus, b, "--jobs", "4")
            for name in ("map.txt", "worklist.tsv", "axis3.tsv"):
                self.assertEqual(open(os.path.join(a, name), encoding="utf-8").read(),
                                 open(os.path.join(b, name), encoding="utf-8").read(),
                                 "%s differs between --jobs 1 and --jobs 4" % name)


class ItSurvivesBadInput(unittest.TestCase):

    def test_a_binary_file_is_flagged_not_quoted(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            os.makedirs(corpus)
            with open(os.path.join(corpus, "blob.bin"), "wb") as fh:
                fh.write(b"\x00\x01\x02\xff" * 512)
            write(corpus, "a.log", ["2026-07-28 09:00:00 INFO x %d" % i
                                    for i in range(50)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("двоичный", body)

    def test_an_empty_corpus_still_writes_all_three_files(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            os.makedirs(corpus)
            run(corpus, out)
            for name in ("map.txt", "worklist.tsv", "axis3.tsv"):
                self.assertTrue(os.path.exists(os.path.join(out, name)), name)

    def test_a_missing_corpus_fails_loudly(self):
        p = subprocess.run([sys.executable, LOGMAP, "/nope/nothing/here",
                            "--out", "/tmp/x"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
