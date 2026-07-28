import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, tempfile, json, gzip, io, os as _os, zipfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import redirect_stdout
from logalyzer.masking import Masker
from logalyzer.ingest import read_all, read_all_with_stats, read_source, _generic_parse
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


if __name__ == "__main__":
    unittest.main()
