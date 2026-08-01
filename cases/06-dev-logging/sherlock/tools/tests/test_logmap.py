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
import re
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


class ARepeatedGroupCarriesItsSpread(unittest.TestCase):
    """`n=7` is not an observation. Measured on a real corpus: one shape occurred
    seven times inside 284 seconds of a ten-hour capture and, because the capture
    straddled a rotation, rendered as `n=4` in one file and `n=3` in another —
    which reads as routine background and is the exact opposite of the truth."""

    def _corpus(self, d):
        """One file, two shapes of EQUAL count: one packed into five minutes, one
        spread across the whole capture. A count alone cannot tell them apart."""
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        lines = ["2026-07-28 %02d:%02d:00 INFO steady thing" % (9 + i // 240, i % 60)
                 for i in range(1680)]
        for k in range(4):                       # packed: 13:20:00 .. 13:23:00
            lines.append("2026-07-28 13:2%d:00 INFO the packed shape" % k)
        for k in range(4):                       # spread: 09:00 .. 15:00
            lines.append("2026-07-28 %02d:05:00 INFO the spread shape" % (9 + 2 * k))
        write(corpus, "a.log", lines)
        run(corpus, out)
        return [(r[5], r[4]) for r in rows(out)]

    def test_a_burst_is_told_apart_from_a_trickle_of_the_same_count(self):
        with tempfile.TemporaryDirectory() as d:
            freq = self._corpus(d)
            packed = next(v for k, v in freq if "packed shape" in k)
            spread = next(v for k, v in freq if "spread shape" in k)
            self.assertIn("n=4", packed)
            self.assertIn("n=4", spread)
            self.assertIn("ВСПЛЕСК", packed,
                          "four records inside three minutes of a seven-hour "
                          "capture must be marked a burst: %s" % packed)
            self.assertNotIn("ВСПЛЕСК", spread,
                             "four records spread across the capture are not a "
                             "burst: %s" % spread)

    def test_the_spread_carries_first_seen_last_seen_and_a_share(self):
        with tempfile.TemporaryDirectory() as d:
            freq = self._corpus(d)
            packed = next(v for k, v in freq if "packed shape" in k)
            self.assertIn("13:20:00→13:23:00", packed)
            self.assertIn("% окна", packed, "the span must be given as a share of "
                                            "the capture window: %s" % packed)

    def test_a_single_occurrence_gets_no_spread(self):
        """A window of one point is not a window, and printing one would be a
        measurement that is not there."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "a.log",
                  ["2026-07-28 09:%02d:00 INFO steady" % (i % 60) for i in range(300)]
                  + ["2026-07-28 15:00:00 INFO seen exactly once"])
            run(corpus, out)
            hit = [r for r in rows(out) if "exactly once" in r[5]]
            self.assertTrue(hit)
            self.assertEqual(hit[0][4], "n=1")

    def test_a_file_with_no_time_axis_gets_a_count_and_no_invented_spread(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "boot/dmesg",
                  ["[276537.000000] link becomes ready %d" % (i % 7)
                   for i in range(300)]
                  + ["[276999.000000] the odd one"] * 3)
            run(corpus, out)
            for r in rows(out):
                self.assertNotIn("окна", r[4],
                                 "a file with no clock cannot report a window: %s"
                                 % r[4])

    def test_spread_note_is_arithmetic_not_prose(self):
        self.assertEqual(L.spread_note(1, 0.0, 10.0, 0.0, 100.0), "")
        self.assertEqual(L.spread_note(3, 0.0, 100.0, 0.0, 100.0),
                         "00:00:00→00:01:40 100с=100.0% окна")
        self.assertIn("ВСПЛЕСК", L.spread_note(3, 0.0, 10.0, 0.0, 36000.0))


class ARotatedFamilyIsOneStream(unittest.TestCase):
    """`x.log` and `x.log.1.gz` are one stream cut by logrotate. Counted apart,
    every count is halved and a before/after lands on opposite sides of the cut,
    so the comparison measures the rotation instead of the incident."""

    @staticmethod
    def _slice(hours, marker_at=(), stamp="2026-07-28 %02d:%02d:%02d"):
        out = []
        for h in hours:
            for i in range(400):
                out.append((stamp % (h, i % 60, i % 60)) + " INFO steady work")
            if h in marker_at:
                out.append((stamp % (h, 30, 0)) + " INFO the rare shape")
                out.append((stamp % (h, 30, 20)) + " INFO the rare shape")
        return out

    def test_a_rotated_pair_is_counted_and_cited_as_one_stream(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/app.log.1.gz", self._slice([6, 7, 8], marker_at=[8]),
                  gz=True)
            write(corpus, "svc/app.log", self._slice([9, 10, 11], marker_at=[9]))
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("ПОТОК «svc/app.log»", body,
                          "the rotated pair was not stitched:\n%s" % body[:1200])
            hit = [r for r in rows(out) if "the rare shape" in r[5]]
            self.assertEqual(len(hit), 1,
                             "the stream must contribute ONE row, not one per "
                             "slice: %s" % [(r[3], r[4]) for r in hit])
            self.assertIn("n=4", hit[0][4],
                          "2 + 2 across the rotation is 4: %s" % hit[0][4])
            self.assertTrue(hit[0][3].startswith("svc/app.log.1.gz:"),
                            "the row must cite the slice the first occurrence "
                            "physically lives in: %s" % hit[0][3])

    def test_the_before_after_spans_the_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/app.log.1.gz", self._slice([6, 7, 8]), gz=True)
            write(corpus, "svc/app.log", self._slice([9, 10, 11]))
            run(corpus, out)
            body = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertIn("06h", body,
                          "the earliest comparable hour lives in the ROTATED file; "
                          "without stitching it is invisible:\n%s" % body)
            self.assertIn("11h", body)

    def test_an_overlapping_family_is_NOT_stitched(self):
        """The negative case that matters. Two files whose windows overlap are not
        consecutive slices of one stream, and a wrong stitch inverts every
        before/after in the table — so the tool keeps its hands off."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/app.log.1", self._slice([9, 10, 11]))
            write(corpus, "svc/app.log", self._slice([9, 10, 11]))
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertNotIn("ПОТОК «", body,
                             "two files covering the SAME hours cannot be ordered "
                             "and must not be stitched:\n%s" % body[:1200])

    def test_a_family_with_no_dated_clock_is_NOT_stitched(self):
        """Two files stamped `13:05:01` with no date have unrelated origins.
        Lining them up would be a guess wearing a measurement's clothes."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/app.log.1",
                  self._slice([6, 7, 8], stamp="%02d:%02d:%02d"))
            write(corpus, "svc/app.log",
                  self._slice([9, 10, 11], stamp="%02d:%02d:%02d"))
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("время: clock", body)
            self.assertNotIn("ПОТОК «", body,
                             "a date-less clock cannot order a family:\n%s"
                             % body[:1200])

    def test_the_family_is_found_by_pattern_and_no_base_name_is_assumed(self):
        f = L.ROTATION_SUFFIX_RE
        self.assertEqual(f.match("access.log.1.gz").group("base"), "access.log")
        self.assertEqual(f.match("bespoke-thing.out.14").group("base"),
                         "bespoke-thing.out")
        self.assertEqual(f.match("messages.20260728.gz").group("base"), "messages")
        self.assertIsNone(f.match("access.log"))
        self.assertIsNone(f.match("access.log.old"))
        self.assertIsNone(f.match("app.9f2a1c.js"))

    def test_files_that_merely_share_a_directory_are_not_a_family(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/a.log", self._slice([6, 7, 8]))
            write(corpus, "svc/b.log.1", self._slice([9, 10, 11]))
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertNotIn("ПОТОК «", body,
                             "different base names are different streams:\n%s"
                             % body[:1200])


def _clf(hour, minute, path, code, ua="kube-probe/1.29"):
    return ('10.42.0.1 - - [28/Jul/2026:%02d:%02d:11 +0000] "GET %s HTTP/1.1" '
            '%d 28 "-" "%s" "-" rt=0.003 uct="-" uht="-" urt="0.002" rid=-'
            % (hour, minute, path, code, ua))


class TheOutcomeAxisIsFoundByShape(unittest.TestCase):
    """Measured: on a 134 MB access log the level axis came out as the request
    method, the path and the user-agent — never the status code, because `200`
    scores badly precisely BECAUSE it is a number. Meanwhile a plain count of
    codes >= 500 per ten minutes went from 0 to over a thousand and landed on the
    incident minute."""

    def test_a_result_code_column_is_found_and_counted_per_bucket(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for h in (9, 10, 11, 12):            # four healthy hours FIRST, so the
                for i in range(600):             # probe window sees only `200`
                    lines.append(_clf(h, i % 60, "/-/healthy", 200))
            for i in range(600):
                lines.append(_clf(13, i % 60, "/-/healthy",
                                  502 if 20 <= i % 60 < 40 else 200))
            write(corpus, "gw/a.log", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("ось исхода «ws:8»", body,
                          "the status column was not found:\n%s" % body[:1400])
            self.assertIn("класс 5xx", body)
            ax = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertIn("\tout\t", ax, "no per-bucket outcome row:\n%s" % ax)
            self.assertIn("исход 5xx", ax)
            self.assertIn("13:", ax, "the bucket must name the time it started:\n%s"
                                     % ax)

    def test_a_constant_three_digit_port_yields_no_outcome_row(self):
        """The negative case: a column that looks like a status and is not. It may
        be accepted as the axis — a constant column is indistinguishable — but it
        must never produce a per-bucket comparison, because there is nothing to
        compare."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/a.log",
                  ["2026-07-28 %02d:%02d:00 INFO peer=10.0.0.%d port 443 open %s"
                   % (9 + i // 400, i % 60, i % 200, word(i % 5))
                   for i in range(1200)])
            run(corpus, out)
            ax = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertNotIn("\tout\t", ax,
                             "a constant column produced an outcome row:\n%s" % ax)

    def test_a_duration_in_milliseconds_is_not_mistaken_for_a_status(self):
        """Its values leave the 100..599 range and change width, which is exactly
        what a code never does. Only a handful of distinct values here, and one of
        them dominant, so cardinality and dominance both say `status` — the width
        of the token is the only thing that does not."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/a.log",
                  ["2026-07-28 %02d:%02d:00 INFO handled in %d ms"
                   % (9 + i // 400, i % 60, (204, 204, 204, 61, 1740, 9)[i % 6])
                   for i in range(1200)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertNotIn("ось исхода «", body,
                             "a millisecond column was read as a result code:\n%s"
                             % body[:1200])

    def test_a_code_column_survives_a_few_dashes(self):
        """Negative control for the width rule: a real code column does carry a
        `-` where the connection died, and throwing the column away over it costs
        the finding."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for h in (9, 10, 11, 12):
                for i in range(600):
                    lines.append(_clf(h, i % 60, "/-/healthy",
                                      200).replace('" 200 ', '" - ', 1)
                                 if i % 50 == 0 else _clf(h, i % 60, "/-/healthy", 200))
            for i in range(600):
                lines.append(_clf(13, i % 60, "/-/healthy",
                                  502 if 20 <= i % 60 < 40 else 200))
            write(corpus, "gw/a.log", lines)
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("ось исхода «ws:8»", body,
                          "2 %% of dashes threw the code column away:\n%s"
                          % body[:1400])

    def test_a_high_cardinality_three_digit_column_is_not_a_status(self):
        """Always three digits, always in range, and one value on more than half
        the records — so presence, width and dominance all say `status`. Four
        hundred distinct values say otherwise, and they are right: a code column
        has a handful."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/a.log",
                  ["2026-07-28 %02d:%02d:00 INFO bucket %d filled"
                   % (9 + i // 400, i % 60,
                      200 if i % 3 else 100 + (i * 7) % 400) for i in range(1200)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertNotIn("ось исхода «", body,
                             "hundreds of distinct values is a measurement, not a "
                             "code:\n%s" % body[:1200])

    def test_a_column_with_no_dominant_value_is_not_a_status(self):
        """Even a handful of values is not a code column when they are handed out
        evenly: a service answers one thing most of the time."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            write(corpus, "svc/a.log",
                  ["2026-07-28 %02d:%02d:00 INFO shard %d picked"
                   % (9 + i // 400, i % 60, 100 + i % 10) for i in range(1200)])
            run(corpus, out)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertNotIn("ось исхода «", body,
                             "ten values at 10%% each is a round-robin, not a "
                             "code:\n%s" % body[:1200])

    def test_the_tool_carries_no_corpus_vocabulary(self):
        """The disqualification check for this whole change. A tool that knows the
        name of the file it was tuned on measures the tuning, not the log."""
        import re
        body = open(LOGMAP, encoding="utf-8").read()
        hits = re.findall(r"(?i)\b(nginx|istio|envoy|kube|catalog|checkout|"
                          r"healthz|livez|acme|SKU-\d|apache|journald)\b", body)
        self.assertEqual(hits, [], "corpus vocabulary leaked into the tool: %s"
                                   % sorted(set(hits)))


class ANumericSlotMustBeAMeasurement(unittest.TestCase):
    """The rate axis was reporting "background did not shift" rows built on things
    that measure nothing — the digits of an item code, and the `1.1` out of
    `HTTP/1.1` given as a latency of 1.100 s for an endpoint whose real response
    time is 0.002 s. A confident wrong number is worse than a missing one, because
    it is what a hypothesis gets refuted with."""

    def test_digits_inside_a_larger_token_are_not_a_measurement(self):
        self.assertTrue(L.glued("sku=ITEM-40044", len("sku=ITEM-")))
        self.assertTrue(L.glued('"GET / HTTP/1.1"', len('"GET / HTTP/')))
        self.assertTrue(L.glued("[pool-nio-8080-exec-14]", len("[pool-nio-")))
        self.assertTrue(L.glued("/api/v1/x", len("/api/v")))
        self.assertFalse(L.glued("rt=0.003", 3))
        self.assertFalse(L.glued('urt="0.002"', 5))
        self.assertFalse(L.glued("took 1490ms", 5))
        self.assertFalse(L.glued('"duration":8', 11))
        self.assertFalse(L.glued("204", 0))

    def test_masking_is_unchanged_by_the_judgement(self):
        """Leaving an identifier unmasked would give every item its own template
        and take the rarity axis apart. The judgement belongs to the rate axis."""
        slots, glue = [], []
        got = L.mask("item=ITEM-40044 rt=0.310", slots, glue)
        self.assertEqual(got, "item=ITEM-# rt=#")
        self.assertEqual(slots, [40044.0, 0.310])
        self.assertEqual(glue, [True, False])

    def test_the_four_disqualifiers(self):
        def stat(values, glued=False):
            s = L.SlotStat()
            for v in values:
                s.add(float(v), glued)
            return s
        self.assertFalse(stat([1.1] * 200, glued=True).is_metric()[0])
        self.assertFalse(stat([1.1] * 200).is_metric()[0])           # constant
        self.assertFalse(stat([0, 1] * 100).is_metric()[0])          # a fixed pair
        self.assertFalse(stat(range(1000, 1400)).is_metric()[0])     # a counter
        self.assertFalse(stat([7] * 199 + [9]).is_metric()[0])       # ~constant
        self.assertTrue(stat([(i * 37) % 900 for i in range(400)]).is_metric()[0])

    def test_a_doubtful_slot_is_dropped_and_the_drop_is_declared(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for h in (9, 15):
                for i in range(900):
                    lines.append(
                        '10.42.0.1 - - [28/Jul/2026:%02d:%02d:11 +0000] '
                        '"GET /probe HTTP/1.1" 200 28 item=ITEM-%05d '
                        'rt=%.3f' % (h, i % 60, (i * 7919) % 99999,
                                     0.002 + (i % 50) / 1000.0))
            write(corpus, "gw/a.log", lines)
            run(corpus, out)
            ax = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertNotIn("p50 1.100", ax,
                             "the protocol version came back as a latency:\n%s" % ax)
            self.assertNotIn("p50 200", ax, "a constant slot was reported:\n%s" % ax)
            self.assertFalse(re.search(r"p50 \d{5}", ax),
                             "an item code was reported as a metric:\n%s" % ax)
            body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
            self.assertIn("отклонённые как НЕ измерения", body,
                          "a dropped slot must be declared, not silently gone:\n%s"
                          % body[:1500])

    def test_a_real_metric_beside_a_rejected_one_still_gets_through(self):
        """Negative control for the guard: it must not eat the real thing. The
        record below carries a protocol version, a constant, an item code AND a
        response time; only the last is a measurement."""
        with tempfile.TemporaryDirectory() as d:
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            lines = []
            for h in (9, 15):
                for i in range(900):
                    rt = 0.010 if i % 100 else (0.120 if h == 9 else 1.300)
                    lines.append(
                        '10.42.0.1 - - [28/Jul/2026:%02d:%02d:11 +0000] '
                        '"GET /probe HTTP/1.1" 200 28 item=ITEM-%05d '
                        'rt=%.3f' % (h, i % 60, (i * 7919) % 99999, rt))
            write(corpus, "gw/a.log", lines)
            run(corpus, out)
            ax = open(os.path.join(out, "axis3.tsv"), encoding="utf-8").read()
            self.assertIn("p50 0.010→0.010", ax,
                          "the one real metric was thrown out with the rest:\n%s"
                          % ax)
            self.assertIn("p99 0.120→1.300", ax)

    def test_a_reported_slot_carries_the_text_in_front_of_it(self):
        """`слот#4` on its own is exactly how a byte count gets read as a latency."""
        self.assertEqual(L.slot_label('rt=# uct="#"', 0), "rt=")
        self.assertEqual(L.slot_label('rt=# uct="#"', 1), 'rt=# uct="')
        self.assertEqual(L.slot_label("no placeholder here", 0), "")


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
