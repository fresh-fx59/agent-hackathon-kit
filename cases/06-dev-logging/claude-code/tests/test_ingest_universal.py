import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, unittest.mock, tempfile, json, gzip, io, os as _os, zipfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import redirect_stdout
from logalyzer.masking import Masker
from logalyzer.ingest import (read_all, read_all_with_stats, read_source,
                              _generic_parse, discover_domain_ids)
from logalyzer.rules_engine import _match
from logalyzer.__main__ import main

FLINK_LINE_1 = ("2026-07-28 15:08:38.903 [main] INFO  "
                "org.apache.flink.runtime.io.network.netty.NettyServer  "
                "- Transport type 'auto': using EPOLL.")
FLINK_LINE_2 = ("2026-07-28 15:08:39.011 [flink-akka.actor.default-dispatcher-4] INFO  "
                "org.apache.flink.runtime.taskexecutor.TaskExecutor  "
                "- Connecting to ResourceManager akka.tcp://flink@fgrfr-appname0001.esrt.domain.ru"
                ":50010/user/rpc/resourcemanager_0(bc719ec79434ad2bd22e6e640a0c423f).")
FLINK_STACKTRACE = (
    "java.lang.RuntimeException: boom\n"
    "\tat com.example.Foo.bar(Foo.java:42)\n"
    "\tat com.example.Foo.main(Foo.java:10)")


class TestSingleFileAndDirectoryWalk(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.masker = Masker()

    def tearDown(self):
        self.dir.cleanup()

    def test_single_file_of_any_name_is_accepted(self):
        p = self.root / "weird_name_no_convention"
        p.write_text('{"ts":"2026-07-28T10:00:00.000Z","service":"svc","level":"INFO","msg":"hi"}\n',
                     encoding="utf-8")
        recs = read_all(p, self.masker)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].level, "INFO")

    def test_directory_walk_includes_extensionless_and_out_files(self):
        (self.root / "taskmanager.out").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - out file message\n", encoding="utf-8")
        (self.root / "LOG").write_text(
            "2026-07-28T10:00:01.000Z INFO svc.Logger - extensionless file message\n", encoding="utf-8")
        (self.root / "app.log.1").write_text(
            "2026-07-28T10:00:02.000Z INFO svc.Logger - rotated file message\n", encoding="utf-8")
        recs = read_all(self.root, self.masker)
        refs = {r.source_ref for r in recs}
        self.assertIn("taskmanager.out", refs)
        self.assertIn("LOG", refs)
        self.assertIn("app.log.1", refs)
        self.assertEqual(len(recs), 3)

    def test_gz_file_read_transparently(self):
        content = '{"ts":"2026-07-28T10:00:00.000Z","service":"svc","level":"ERROR","msg":"boom"}\n'
        with gzip.open(self.root / "app.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(content)
        recs = read_all(self.root, self.masker)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].level, "ERROR")
        self.assertEqual(recs[0].body, "boom")

    def test_binary_file_skipped_with_visible_reason(self):
        (self.root / "core.dump").write_bytes(b"\x7fELF\x00\x00binary\x00garbage")
        (self.root / "normal.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - fine\n", encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, self.masker)
        refs = {r.source_ref for r in recs}
        self.assertNotIn("core.dump", refs)
        self.assertIn("normal.log", refs)
        reasons = {s["file"]: s["reason"] for s in stats["skipped"]}
        self.assertIn("core.dump", reasons)
        self.assertIn("binary", reasons["core.dump"].lower())

    def test_nested_zip_in_directory_skipped_with_reason(self):
        nested = self.root / "inner.zip"
        with zipfile.ZipFile(nested, "w") as z:
            z.writestr("whatever.log", "2026-07-28T10:00:00.000Z INFO x - y\n")
        (self.root / "normal.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - fine\n", encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, self.masker)
        refs = {r.source_ref for r in recs}
        self.assertNotIn("inner.zip", refs)
        self.assertIn("normal.log", refs)
        reasons = {s["file"]: s["reason"] for s in stats["skipped"]}
        self.assertIn("inner.zip", reasons)
        self.assertIn("zip", reasons["inner.zip"].lower())

    def test_oversized_file_skipped_with_reason(self):
        import logalyzer.ingest as ingest_mod
        big = self.root / "huge.log"
        big.touch()
        _os.truncate(str(big), ingest_mod._MAX_FILE_BYTES + 1)
        recs, stats = read_all_with_stats(self.root, self.masker)
        refs = {r.source_ref for r in recs}
        self.assertNotIn("huge.log", refs)
        reasons = {s["file"]: s["reason"] for s in stats["skipped"]}
        self.assertIn("huge.log", reasons)

    def test_hidden_path_components_skipped_from_walk(self):
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
        (self.root / "real.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - fine\n", encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, self.masker)
        refs = {r.source_ref for r in recs}
        self.assertEqual(refs, {"real.log"})
        # not ingested AND not listed per-file (not even as a visible skip --
        # hidden paths are not "logs of an unknown format", just not logs).
        self.assertNotIn("HEAD", stats["files"])
        self.assertNotIn("config", stats["files"])
        self.assertFalse(any(s["file"] in ("HEAD", "config") for s in stats["skipped"]))

    def test_gz_decompressed_size_cap_skips_with_reason(self):
        import logalyzer.ingest as ingest_mod
        # Highly repetitive payload: compresses to a tiny on-disk size but
        # decompresses to well over a small patched cap -- proves the check
        # is on decompressed bytes, not the compressed file size.
        payload = "x" * 20000 + "\n"
        gz_path = self.root / "bomb.jsonl.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write(payload)
        self.assertLess(gz_path.stat().st_size, 1000)  # sanity: passes the on-disk pre-check
        with unittest.mock.patch.object(ingest_mod, "_MAX_FILE_BYTES", 1000):
            recs, stats = read_all_with_stats(self.root, self.masker)
        refs = {r.source_ref for r in recs}
        self.assertNotIn("bomb.jsonl.gz", refs)
        reasons = {s["file"]: s["reason"] for s in stats["skipped"]}
        self.assertIn("bomb.jsonl.gz", reasons)
        self.assertIn("decompressed", reasons["bomb.jsonl.gz"].lower())

    def test_stats_dict_shape(self):
        (self.root / "a.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - fine\n", encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, self.masker)
        self.assertIn("files", stats)
        self.assertIn("skipped", stats)
        self.assertIn("a.log", stats["files"])
        entry = stats["files"]["a.log"]
        for key in ("format", "ok", "partial", "unparsed"):
            self.assertIn(key, entry)
        self.assertIsInstance(stats["skipped"], list)


class TestGenericParser(unittest.TestCase):
    def test_iso_without_brackets_ok_quality(self):
        line = "2026-07-28T10:00:00.123Z INFO svc.Logger - hello world"
        g = _generic_parse(line)
        self.assertIsNotNone(g)
        self.assertEqual(g["quality"], "ok")
        self.assertEqual(g["level"], "INFO")
        self.assertEqual(g["logger"], "svc.Logger")
        self.assertEqual(g["msg"], "hello world")
        self.assertEqual(g["timestamp"], "2026-07-28T10:00:00.123Z")

    def test_iso_with_offset_timezone(self):
        line = "2026-07-28T10:00:00.123+02:00 WARN svc - drifted"
        g = _generic_parse(line)
        self.assertIsNotNone(g)
        self.assertEqual(g["level"], "WARN")
        self.assertTrue(g["timestamp"].endswith("+02:00"))

    def test_epoch_millis_normalized(self):
        epoch_ms = 1700000000000
        line = "%d ERROR something failed" % epoch_ms
        g = _generic_parse(line)
        self.assertIsNotNone(g)
        self.assertEqual(g["quality"], "ok")
        self.assertEqual(g["level"], "ERROR")
        self.assertEqual(g["msg"], "something failed")
        expected = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        self.assertEqual(g["timestamp"], expected)

    def test_epoch_seconds_normalized(self):
        epoch_s = 1700000000
        line = "%d INFO all good" % epoch_s
        g = _generic_parse(line)
        self.assertIsNotNone(g)
        self.assertEqual(g["quality"], "ok")
        expected = datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        self.assertEqual(g["timestamp"], expected)

    def test_syslog_forced_partial_even_with_level_but_windowless_visible(self):
        line = "Jul 28 15:08:38 host1 myapp[9]: ERROR something broke"
        g = _generic_parse(line)
        self.assertIsNotNone(g)
        self.assertEqual(g["level"], "ERROR")
        # Deliberate design choice (documented in ingest.py): syslog lines
        # carry no year, so they always come back "partial" even when a
        # level token was found -- this keeps windowed rules from doing
        # elapsed_ms math against the 1900 placeholder year.
        self.assertEqual(g["quality"], "partial")
        self.assertTrue(g["timestamp"].startswith("1900-07-28T15:08:38"))
        # But a windowless (all_of) rule matcher still sees it fine --
        # rules_engine._match only looks at service/level/attr/body_regex,
        # never parse_quality or timestamp.
        fake_item = {"record": type("R", (), {
            "service": "svc", "level": "ERROR", "attrs": {}, "body": g["msg"]})()}
        self.assertTrue(_match({"level": "ERROR"}, fake_item))

    def test_no_recognized_timestamp_returns_none(self):
        self.assertIsNone(_generic_parse("just some free text, no timestamp here"))
        self.assertIsNone(_generic_parse("\tat com.example.Foo.bar(Foo.java:42)"))


class TestMultilineFolding(unittest.TestCase):
    def setUp(self):
        self.masker = Masker()

    def test_stacktrace_folds_into_previous_record_logback_path(self):
        content = (
            "2026-07-15 11:22:03.402 [http-nio-1] INFO  c.p.p.svc.PaymentService - starting\n"
            "java.lang.RuntimeException: boom\n"
            "\tat com.example.Foo.bar(Foo.java:42)\n"
            "\tat com.example.Foo.main(Foo.java:10)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "payment-service.log"
            p.write_text(content, encoding="utf-8")
            recs = read_source(p, self.masker)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].parse_quality, "ok")
        self.assertIn("java.lang.RuntimeException: boom", recs[0].body)
        self.assertIn("at com.example.Foo.bar(Foo.java:42)", recs[0].body)
        self.assertIn("at com.example.Foo.main(Foo.java:10)", recs[0].body)

    def test_stacktrace_with_coincidental_epoch_and_date_phrase_folds_to_one_record(self):
        """IMPORTANT 6: the timestamp-anywhere-in-line upgrade must not
        shred a stack trace just because a continuation line happens to
        contain something bank-shaped -- an embedded 13-digit "error code"
        (epoch_ms-shaped) and a "May 12 10:00:00" phrase (syslog-shaped)
        buried mid-line must both fold, not become their own bogus
        records, when they don't conform to the file's dominant
        (kind, position) timestamp pattern."""
        content = (
            "2026-07-28 15:08:38.903 [main] INFO  com.example.Service - starting operation\n"
            "java.lang.RuntimeException: something failed with code 1700000000123\n"
            "\tat com.example.Foo.bar(Foo.java:42)\n"
            "\tat com.example.Foo.baz(Foo.java:99) on May 12 10:00:00 last time\n"
            "\tat com.example.Foo.main(Foo.java:10)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "service.log"
            p.write_text(content, encoding="utf-8")
            recs = read_source(p, self.masker)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].parse_quality, "ok")
        self.assertIn("1700000000123", recs[0].body)
        self.assertIn("May 12 10:00:00", recs[0].body)
        self.assertIn("at com.example.Foo.main(Foo.java:10)", recs[0].body)

    def test_leading_lines_with_no_previous_record_become_standalone_partial(self):
        content = "some preamble line with no timestamp\nanother preamble line\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "weird.log"
            p.write_text(content, encoding="utf-8")
            recs = read_source(p, self.masker)
        # No previous record exists for either line -> each becomes its own
        # standalone partial/unparsed record (first one has no prev; the
        # second folds into the first, since the first IS now a "previous
        # record"). Only the very first line has no predecessor.
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].parse_quality, "partial")
        self.assertIn("some preamble line with no timestamp", recs[0].body)
        self.assertIn("another preamble line", recs[0].body)

    def test_fold_cap_at_20_lines_with_truncation_marker(self):
        lines = ["2026-07-28T10:00:00.000Z INFO svc.Logger - start"]
        for n in range(25):
            lines.append("continuation line %d" % n)
        content = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "capped.log"
            p.write_text(content, encoding="utf-8")
            recs = read_source(p, self.masker)
        self.assertEqual(len(recs), 1)
        body = recs[0].body
        self.assertIn("continuation line 0", body)
        self.assertIn("continuation line 19", body)
        self.assertNotIn("continuation line 20", body)
        self.assertIn("[truncated]", body)
        self.assertEqual(body.count("[truncated]"), 1)


class TestFlinkFixture(unittest.TestCase):
    def test_flink_log4j_format_parses_ok_with_stacktrace_fold(self):
        content = FLINK_LINE_1 + "\n" + FLINK_LINE_2 + "\n" + FLINK_STACKTRACE + "\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "flink-taskexecutor-1.log"
            p.write_text(content, encoding="utf-8")
            recs = read_source(p, Masker())
        self.assertEqual(len(recs), 2)
        r1, r2 = recs
        self.assertEqual(r1.parse_quality, "ok")
        self.assertEqual(r1.level, "INFO")
        self.assertEqual(r1.attrs.get("logger"),
                          "org.apache.flink.runtime.io.network.netty.NettyServer")
        self.assertEqual(r1.body, "Transport type 'auto': using EPOLL.")
        self.assertEqual(r2.parse_quality, "ok")
        self.assertEqual(r2.level, "INFO")
        self.assertEqual(r2.attrs.get("logger"),
                          "org.apache.flink.runtime.taskexecutor.TaskExecutor")
        self.assertTrue(r2.body.startswith("Connecting to ResourceManager"))
        # The 3-line fabricated stack trace has no leading timestamp -> it
        # folds into r2 (the record immediately preceding it), proving
        # multi-line folding on real-world Flink output.
        self.assertIn("java.lang.RuntimeException: boom", r2.body)
        self.assertIn("at com.example.Foo.bar(Foo.java:42)", r2.body)
        self.assertIn("at com.example.Foo.main(Foo.java:10)", r2.body)


class TestCliAndStats(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_single_file_logs_path_e2e_through_investigate(self):
        single = self.root / "onefile.jsonl"
        single.write_text(
            '{"ts":"2026-07-15T11:22:03.104Z","service":"order-service","level":"INFO",'
            '"correlation_id":"c-abc123","order_id":"ord-1","msg":"checkout started"}\n'
            '{"ts":"2026-07-15T11:22:05.425Z","service":"order-service","level":"ERROR",'
            '"correlation_id":"c-abc123","order_id":"ord-1","msg":"failed"}\n',
            encoding="utf-8")
        out = self.root / "report.json"
        code, _ = self._run(["investigate", "--logs", str(single),
                             "--correlation-id", "c-abc123", "--mode", "ops",
                             "--out", str(out), "--case-dir", str(self.root)])
        self.assertEqual(code, 0)
        rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rep["mode"], "ops")

    def test_cmd_stats_json_has_files_and_skipped_and_legacy_keys(self):
        (self.root / "a.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - fine\n", encoding="utf-8")
        (self.root / "bin.dat").write_bytes(b"\x00\x01binary")
        code, out = self._run(["stats", "--logs", str(self.root)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        for key in ("records_total", "unparsed", "by_service", "files", "skipped"):
            self.assertIn(key, payload)
        self.assertIn("a.log", payload["files"])
        self.assertTrue(any(s["file"] == "bin.dat" for s in payload["skipped"]))

    def test_cmd_stats_surfaces_ts_and_level_hit_rates_for_plaintext_files(self):
        """MINOR 9: per-file extractor hit rates (ts/level %) must be
        visible in stats JSON for plaintext/learned files, not just the
        ok/partial/unparsed counts -- diagnosing WHY a file is borderline
        (missing timestamps vs missing levels) shouldn't require re-running
        with a debugger."""
        (self.root / "mixed.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - fine\n"
            "2026-07-28T10:00:01.000Z svc.Logger - no level token here\n",
            encoding="utf-8")
        code, out = self._run(["stats", "--logs", str(self.root)])
        self.assertEqual(code, 0)
        entry = json.loads(out)["files"]["mixed.log"]
        self.assertIn("ts_hit_rate", entry)
        self.assertIn("level_hit_rate", entry)
        self.assertEqual(entry["ts_hit_rate"], 1.0)
        self.assertEqual(entry["level_hit_rate"], 0.5)


# ---------------------------------------------------------------------------
# Normalization v2: FormatStore learned-cache ingest path + the exit-4
# inference handshake (CLI level, via __main__.main). "Alien" dialect =
# same mid-line-timestamp shape as the design doc's example
# (`level=W ts=[28/Jul/2026 15:08:38.903] svc=inventory :: message text`),
# unrecognizable by the built-in heuristic timestamp bank.
# ---------------------------------------------------------------------------
ALIEN_DESCRIPTOR = {
    "line_regex": (r"^level=(?P<level>\w+)\s+ts=\[(?P<ts>[^\]]+)\]\s+svc=(?P<service>\S+)"
                   r"\s+::\s+(?P<msg>.*)$"),
    "ts_format": "%d/%b/%Y %H:%M:%S.%f",
    "notes": "mid-line-timestamp custom dialect (design-doc example)",
}
_ALIEN_LEVELS = ["INFO", "WARN", "ERROR", "INFO"]
_ALIEN_MSGS = ["reservation queue backing up", "reservation completed successfully",
              "reservation timed out", "compensating reserve released"]

def _alien_lines(n=24):
    out = []
    for i in range(n):
        lvl = _ALIEN_LEVELS[i % len(_ALIEN_LEVELS)]
        msg = _ALIEN_MSGS[i % len(_ALIEN_MSGS)]
        out.append("level=%s ts=[28/Jul/2026 15:%02d:%02d.%03d] svc=inventory :: %s" %
                   (lvl, 8 + (i // 60), i % 60, (i * 7) % 1000, msg))
    return out


# CRITICAL 1 fixture: a valid, fully-matching but LEVEL-LESS dialect
# (nginx-access-log-style: ts + service + message, no level field at all).
# apply_descriptor can only ever mark such a file "ok" if BOTH ts and level
# are present -- with no level group in the regex, every matched record is
# "partial" forever, which must NOT be confused with "file still doesn't
# parse" (the needs_inference deadloop this reproduces).
NOLEVEL_DESCRIPTOR = {
    "line_regex": r"^ts=\[(?P<ts>[^\]]+)\]\s+svc=(?P<service>\S+)\s+::\s+(?P<msg>.*)$",
    "ts_format": "%d/%b/%Y %H:%M:%S.%f",
    "notes": "ts-only dialect, no level field (nginx-style access log)",
}
_NOLEVEL_MSGS = ["GET /api/inventory 200", "GET /api/reserve 201",
                 "POST /api/reserve 409", "GET /api/health 200"]

def _nolevel_lines(n=24):
    out = []
    for i in range(n):
        msg = _NOLEVEL_MSGS[i % len(_NOLEVEL_MSGS)]
        out.append("ts=[28/Jul/2026 15:%02d:%02d.%03d] svc=inventory :: %s" %
                   (8 + (i // 60), i % 60, (i * 7) % 1000, msg))
    return out


class TestLearnedFormatCache(unittest.TestCase):
    """Ingest cache-hit path: a pre-learned descriptor makes a file of that
    dialect parse via apply_descriptor (dialect label "learned:<fp>"),
    never touching needs_inference."""

    def setUp(self):
        self.store_dir = tempfile.TemporaryDirectory()
        self.env_patcher = unittest.mock.patch.dict(
            os.environ, {"LOGALYZER_FORMATS_DIR": self.store_dir.name})
        self.env_patcher.start()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.env_patcher.stop()
        self.store_dir.cleanup()
        self.dir.cleanup()

    def test_cache_hit_parses_via_learned_descriptor_no_needs_inference(self):
        from logalyzer.formats import fingerprint, FormatStore, validate_descriptor
        lines = _alien_lines(24)
        fp = fingerprint(lines[:50])
        ok, hit_rates, reason = validate_descriptor(ALIEN_DESCRIPTOR, lines)
        self.assertTrue(ok, reason)
        FormatStore().save(fp, ALIEN_DESCRIPTOR, hit_rates, [])
        (self.root / "weird.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, Masker())
        self.assertEqual(stats["files"]["weird.log"]["format"], "learned:%s" % fp)
        self.assertNotIn("needs_inference", stats["files"]["weird.log"])
        self.assertEqual(stats.get("needs_inference", []), [])

    def test_apply_time_budget_breach_falls_back_to_heuristic_with_warning(self):
        """CRITICAL 3 (apply-time secondary budget): wired into the actual
        ingest path, not just unit-tested in isolation -- a learned
        descriptor whose application breaches the budget (or crashes) must
        not hang read_all_with_stats; it must fall back to the heuristic
        waterfall and leave a visible warning. Mocks
        formats.apply_descriptor_with_budget's return value directly
        (rather than waiting out a real timeout) to keep this test fast;
        the timeout mechanism itself is proven separately in
        tests/test_formats.py's TestReDoSContainment."""
        import logalyzer.formats as formats_mod
        lines = _alien_lines(24)
        fp = formats_mod.fingerprint(lines[:50])
        formats_mod.FormatStore().save(fp, ALIEN_DESCRIPTOR, {"ts": 1.0}, [])
        (self.root / "weird.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

        with unittest.mock.patch.object(
                formats_mod, "apply_descriptor_with_budget",
                return_value=(None, "descriptor application exceeded the 10.0s "
                                    "time budget -- falling back")):
            recs, stats = read_all_with_stats(self.root, Masker())
        entry = stats["files"]["weird.log"]
        self.assertIn(entry["format"], ("heuristic", "logback"))
        self.assertNotEqual(entry["format"], "learned:%s" % fp)
        self.assertTrue(stats.get("warnings"))
        warning = stats["warnings"][0]
        self.assertEqual(warning["file"], "weird.log")
        self.assertIn("time budget", warning["reason"].lower())
        self.assertGreater(len(recs), 0)


class TestExitFourHandshake(unittest.TestCase):
    """CLI-level exit-4 inference handshake + register-format round trip."""

    def setUp(self):
        self.store_dir = tempfile.TemporaryDirectory()
        self.env_patcher = unittest.mock.patch.dict(
            os.environ, {"LOGALYZER_FORMATS_DIR": self.store_dir.name})
        self.env_patcher.start()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.env_patcher.stop()
        self.store_dir.cleanup()
        self.dir.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_alien_only_dir_exits_4_then_register_format_then_proceeds(self):
        logs = self.root / "logs"; logs.mkdir()
        lines = _alien_lines(24)
        (logs / "alien.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = self.root / "report.json"

        code, stdout = self._run(["investigate", "--logs", str(logs),
                                  "--correlation-id", "c-does-not-exist",
                                  "--mode", "ops", "--out", str(out),
                                  "--case-dir", str(self.root)])
        self.assertEqual(code, 4)
        payload = json.loads(stdout)
        self.assertEqual(payload["action"], "format_inference_needed")
        self.assertIn("instructions", payload)
        self.assertIn("register-format", payload["instructions"])
        # MINOR 8: instructions must explain the --sample file explicitly,
        # state the acceptance thresholds, and mention exit codes -- not
        # just name the command.
        instr = payload["instructions"].lower()
        self.assertIn("sample_lines", instr)
        self.assertIn("one line per line", instr)
        self.assertIn("90%", instr)
        self.assertIn("50%", instr)
        self.assertIn("2000-2100", instr)
        self.assertIn("366", instr)
        self.assertIn("exit codes", instr)
        self.assertEqual(len(payload["files"]), 1)
        entry = payload["files"][0]
        self.assertEqual(entry["file"], "alien.log")
        self.assertIn("fingerprint", entry)
        self.assertGreater(len(entry["sample_lines"]), 0)
        self.assertLessEqual(len(entry["sample_lines"]), 20)

        descriptor_path = self.root / "descriptor.json"
        descriptor_path.write_text(json.dumps(ALIEN_DESCRIPTOR), encoding="utf-8")
        sample_path = self.root / "sample.txt"
        sample_path.write_text("\n".join(entry["sample_lines"]) + "\n", encoding="utf-8")

        code2, stdout2 = self._run(["register-format", str(descriptor_path),
                                    "--fingerprint", entry["fingerprint"],
                                    "--sample", str(sample_path)])
        self.assertEqual(code2, 0, stdout2)
        result = json.loads(stdout2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fingerprint"], entry["fingerprint"])
        self.assertIn("hit_rates", result)
        # CRITICAL 3: measured validation wall time is persisted alongside
        # the learned descriptor (operator visibility into anything that
        # took suspiciously long even while staying under budget).
        from logalyzer.formats import FormatStore
        stored = FormatStore().get(entry["fingerprint"])
        self.assertIn("validate_seconds", stored)
        self.assertIsInstance(stored["validate_seconds"], float)
        self.assertGreaterEqual(stored["validate_seconds"], 0)

        code3, stdout3 = self._run(["investigate", "--logs", str(logs),
                                    "--correlation-id", "c-does-not-exist",
                                    "--mode", "ops", "--out", str(out),
                                    "--case-dir", str(self.root)])
        self.assertEqual(code3, 0, stdout3)

    def test_level_less_dialect_no_deadloop_after_registration(self):
        """CRITICAL 1: a valid ts-only (no level group) descriptor must not
        deadloop exit-4 forever. apply_descriptor's "ok" quality requires
        BOTH ts and level, so a level-less dialect's records are always
        "partial" -- the needs_inference gate for a learned: dialect must
        key on descriptor MATCH rate (did the regex find ts at all), not
        ok-rate, or investigate keeps demanding re-registration of an
        already-solved format forever."""
        logs = self.root / "logs"; logs.mkdir()
        lines = _nolevel_lines(24)
        (logs / "access.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = self.root / "report.json"

        code, stdout = self._run(["investigate", "--logs", str(logs),
                                  "--correlation-id", "c-does-not-exist",
                                  "--mode", "ops", "--out", str(out),
                                  "--case-dir", str(self.root)])
        self.assertEqual(code, 4, stdout)
        entry = json.loads(stdout)["files"][0]

        descriptor_path = self.root / "nolevel_descriptor.json"
        descriptor_path.write_text(json.dumps(NOLEVEL_DESCRIPTOR), encoding="utf-8")
        sample_path = self.root / "nolevel_sample.txt"
        sample_path.write_text("\n".join(entry["sample_lines"]) + "\n", encoding="utf-8")
        code2, stdout2 = self._run(["register-format", str(descriptor_path),
                                    "--fingerprint", entry["fingerprint"],
                                    "--sample", str(sample_path)])
        self.assertEqual(code2, 0, stdout2)

        # The bug: before the fix, this second investigate call also exits
        # 4 (ok-rate stuck at 0% forever for a level-less descriptor), even
        # though the dialect is now fully registered and every line matches.
        code3, stdout3 = self._run(["investigate", "--logs", str(logs),
                                    "--correlation-id", "c-does-not-exist",
                                    "--mode", "ops", "--out", str(out),
                                    "--case-dir", str(self.root)])
        self.assertEqual(code3, 0, stdout3)
        # And a THIRD run (simulating the agent naively re-registering the
        # exact same already-solved dialect) must also proceed cleanly --
        # a registered fingerprint must never be re-offered for inference.
        code4, stdout4 = self._run(["investigate", "--logs", str(logs),
                                    "--correlation-id", "c-does-not-exist",
                                    "--mode", "ops", "--out", str(out),
                                    "--case-dir", str(self.root)])
        self.assertEqual(code4, 0, stdout4)

    def test_register_format_bad_descriptor_exits_1(self):
        lines = _alien_lines(24)
        descriptor_path = self.root / "bad.json"
        descriptor_path.write_text(json.dumps({"line_regex": r"^(?P<lvl>\w+)$",
                                                "ts_format": "iso"}), encoding="utf-8")
        sample_path = self.root / "sample.txt"
        sample_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, stdout = self._run(["register-format", str(descriptor_path),
                                  "--fingerprint", "deadbeef0000",
                                  "--sample", str(sample_path)])
        self.assertEqual(code, 1)
        result = json.loads(stdout)
        self.assertFalse(result["ok"])
        self.assertIn("ts", result["reason"].lower())

    def test_alien_plus_known_good_fixtures_exits_0_with_limitations_note(self):
        import tests.test_ingest_lines as fixtures
        logs = self.root / "logs"; logs.mkdir()
        lines = _alien_lines(24)
        (logs / "alien.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (logs / "order-service.log").write_text(fixtures.ORDER_JSONL, encoding="utf-8")
        (logs / "payment-service.log").write_text(fixtures.PAYMENT_PLAIN, encoding="utf-8")
        out = self.root / "report.json"
        code, stdout = self._run(
            ["investigate", "--logs", str(logs),
             "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
             "--mode", "ops", "--out", str(out), "--case-dir", str(self.root)])
        self.assertEqual(code, 0, stdout)
        rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(rep["evidence"])
        joined = " ".join(rep["limitations"])
        self.assertIn("alien.log", joined)


class TestStatsDialectLabel(unittest.TestCase):
    def setUp(self):
        self.store_dir = tempfile.TemporaryDirectory()
        self.env_patcher = unittest.mock.patch.dict(
            os.environ, {"LOGALYZER_FORMATS_DIR": self.store_dir.name})
        self.env_patcher.start()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.env_patcher.stop()
        self.store_dir.cleanup()
        self.dir.cleanup()

    def test_logback_heuristic_learned_and_structured_labels(self):
        from logalyzer.formats import fingerprint, FormatStore, validate_descriptor
        (self.root / "payment-service.log").write_text(
            "2026-07-15 11:22:03.402 [http-nio-1] INFO  c.p.p.svc.PaymentService "
            "- payment AUTHORIZED\n", encoding="utf-8")
        (self.root / "plain.log").write_text(
            "2026-07-28T10:00:00.000Z INFO svc.Logger - heuristic path only\n",
            encoding="utf-8")
        (self.root / "kafka_events.jsonl").write_text(
            '{"ts":"2026-07-28T10:00:00Z","type":"X","topic":"t","partition":0,'
            '"offset":1,"payload":{}}\n', encoding="utf-8")

        learned_lines = _alien_lines(24)
        fp = fingerprint(learned_lines[:50])
        ok, hit_rates, _r = validate_descriptor(ALIEN_DESCRIPTOR, learned_lines)
        self.assertTrue(ok)
        FormatStore().save(fp, ALIEN_DESCRIPTOR, hit_rates, [])
        (self.root / "learned.log").write_text("\n".join(learned_lines) + "\n",
                                                encoding="utf-8")

        _recs, stats = read_all_with_stats(self.root, Masker())
        self.assertEqual(stats["files"]["payment-service.log"]["format"], "logback")
        self.assertEqual(stats["files"]["plain.log"]["format"], "heuristic")
        self.assertEqual(stats["files"]["learned.log"]["format"], "learned:%s" % fp)
        self.assertEqual(stats["files"]["kafka_events.jsonl"]["format"], "kafka")


# ---------------------------------------------------------------------------
# CRITICAL 2: content-based container detection. Filename substrings used
# to be authoritative -- a plain logback file merely NAMED kafka-server.log
# got forced through the kafka JSON reader (every line fails json.loads)
# and came back 100% unparsed, which also made it INELIGIBLE for
# needs_inference recovery (that gate only fires for plaintext dialects).
# Content must decide; filename is only a tie-breaker for genuinely
# ambiguous content.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# IMPORTANT 5: generic ID discovery false positives. `\b\w*_?[Ii]d[=:]` (the
# original literal regex) matches ANY word ending in the letters "id" --
# "valid", "paid", "invalid" all qualify -- turning ordinary sentence
# fragments into bogus domain_ids that could join unrelated records via a
# shared "value". Fix requires genuine id morphology (exact "id", `*_id`
# snake_case, `*Id` camelCase) plus a plausible value (not a trivial
# true/false/yes/no/null/none token, length >= 4, digit or hex/uuid shaped).
# ---------------------------------------------------------------------------
class TestIdDiscoveryFalsePositives(unittest.TestCase):
    def test_prose_words_ending_in_id_yield_no_domain_ids(self):
        raw = "status valid: true, paid: confirming, invalid: false, ok: yes"
        self.assertEqual(discover_domain_ids(raw), {})

    def test_snake_case_and_camel_case_ids_still_captured(self):
        raw = "order_id=ord-123 userId=abc123 authId: auth-51ac9d2e"
        ids = discover_domain_ids(raw)
        self.assertEqual(ids.get("order_id"), "ord-123")
        self.assertEqual(ids.get("userId"), "abc123")
        self.assertEqual(ids.get("authId"), "auth-51ac9d2e")

    def test_exact_id_key_still_captured(self):
        ids = discover_domain_ids("id=req-88291")
        self.assertEqual(ids.get("id"), "req-88291")

    def test_short_or_trivial_values_dropped_even_with_id_morphology(self):
        ids = discover_domain_ids("session_id=yes retry_id=no")
        self.assertEqual(ids, {})

    def test_hex_run_without_digits_still_captured_as_hex(self):
        ids = discover_domain_ids("commit deadbeefcafefeed applied")
        self.assertEqual(ids.get("hex"), "deadbeefcafefeed")


class TestContentBasedSniffing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_logback_content_in_kafka_named_file_parses_as_logback(self):
        logback_content = (
            "2026-07-15 11:22:03.402 [http-nio-1] INFO  c.p.p.svc.PaymentService "
            "- payment AUTHORIZED auth_id=auth-51ac9d2e\n"
            "2026-07-15 11:22:35.782 [pool-2] WARN  c.p.p.svc.ReconciliationJob "
            "- payment in AUTHORIZED but order in FAILED\n")
        (self.root / "kafka-server.log").write_text(logback_content, encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, Masker())
        entry = stats["files"]["kafka-server.log"]
        self.assertEqual(entry["format"], "logback")
        self.assertEqual(entry["ok"], 2)
        self.assertEqual(entry["unparsed"], 0)
        self.assertNotIn("needs_inference", entry)
        levels = {r.level for r in recs}
        self.assertEqual(levels, {"INFO", "WARN"})

    def test_real_kafka_jsonl_still_enriched_regardless_of_filename(self):
        kafka_content = (
            '{"ts":"2026-07-15T11:22:03.410Z","topic":"payments.events.v1",'
            '"partition":1,"offset":45123,"type":"PaymentAuthorized",'
            '"payload":{"order_id":"ord-a12f5d7e","auth_id":"auth-51ac9d2e"}}\n'
            '{"ts":"2026-07-15T11:22:05.435Z","topic":"orders.events.v1",'
            '"partition":3,"offset":98421,"type":"OrderFailed",'
            '"payload":{"order_id":"ord-a12f5d7e"}}\n')
        # deliberately NOT named anything kafka-ish -- content alone must decide
        (self.root / "events_stream.log").write_text(kafka_content, encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, Masker())
        self.assertEqual(stats["files"]["events_stream.log"]["format"], "kafka")
        kafka_recs = [r for r in recs if r.service == "kafka"]
        self.assertEqual(len(kafka_recs), 2)
        self.assertEqual(kafka_recs[1].attrs["event_type"], "OrderFailed")
        self.assertEqual(kafka_recs[1].domain_ids["order_id"], "ord-a12f5d7e")

    def test_k8s_content_detected_without_filename_hint(self):
        k8s_content = (
            "2026-07-15T11:20:11Z Warning Unhealthy pod/inventory-service-x2jkl "
            "Readiness probe failed: HTTP 503\n"
            "2026-07-15T11:21:02Z Normal Scaling hpa/inventory-service "
            "New size: 5\n")
        (self.root / "events.log").write_text(k8s_content, encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, Masker())
        self.assertEqual(stats["files"]["events.log"]["format"], "k8s")
        self.assertEqual({r.service for r in recs}, {"k8s"})

    def test_metrics_content_detected_without_filename_hint(self):
        metrics_content = (
            "# HELP http_server_requests_seconds Duration\n"
            'http_server_requests_seconds{service="inventory-service"} 1.912\n'
            'http_server_requests_seconds{service="payment-service"} 0.198\n')
        (self.root / "snapshot.txt").write_text(metrics_content, encoding="utf-8")
        recs, stats = read_all_with_stats(self.root, Masker())
        self.assertEqual(stats["files"]["snapshot.txt"]["format"], "metrics")

    def test_mostly_unparsed_structured_guess_falls_back_to_plaintext(self):
        """Defense in depth: even if content sniffing somehow still picks a
        structured reader that turns out to be wrong for most of the file
        (>=90% unparsed, on a large-enough sample), fall through to the
        plaintext waterfall instead of returning a wall of garbage -- and
        that file becomes needs_inference-eligible again."""
        import logalyzer.ingest as ingest_mod
        with unittest.mock.patch.object(ingest_mod, "_content_classify",
                                        return_value="kafka"):
            lines = ["logback-style line number %d with no json at all" % i
                     for i in range(24)]
            (self.root / "forced.log").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
            recs, stats = read_all_with_stats(self.root, Masker())
        entry = stats["files"]["forced.log"]
        self.assertNotEqual(entry["format"], "kafka")
        self.assertIn(entry["format"], ("heuristic", "logback"))


if __name__ == "__main__":
    unittest.main()
