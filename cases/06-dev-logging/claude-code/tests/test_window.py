import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, tempfile, json, zipfile, io
from pathlib import Path
from contextlib import redirect_stdout
from logalyzer.records import NormalizedRecord
from logalyzer.correlate import related_window
from logalyzer.__main__ import main
import tests.test_ingest_lines as fixtures

CASE = Path(__file__).resolve().parents[1]
PACK = CASE.parent / "petstore_input_pack.zip"


def rec(ts, service, level="INFO", body="event", ref="f.log", line=1, **attrs):
    return NormalizedRecord(timestamp=ts, service=service, level=level, body=body,
                            source_ref=ref, source_line=line, attrs=attrs)


RECORDS = [
    rec("2026-07-15T11:20:00.000Z", "order-service", body="before window", ref="a.log", line=1),
    rec("2026-07-15T11:22:00.000Z", "order-service", body="at since boundary", ref="a.log", line=2),
    rec("2026-07-15T11:23:00.000Z", "payment-service", body="mid window", ref="b.log", line=1),
    rec("2026-07-15T11:24:00.000Z", "order-service", body="at until boundary", ref="a.log", line=3),
    rec("2026-07-15T11:25:00.000Z", "order-service", body="after window", ref="a.log", line=4),
    rec("", "order-service", body="no timestamp at all", ref="a.log", line=5),
    rec("not-a-timestamp", "payment-service", body="garbage timestamp", ref="b.log", line=2),
]

SINCE = "2026-07-15T11:22:00.000Z"
UNTIL = "2026-07-15T11:24:00.000Z"


class TestRelatedWindow(unittest.TestCase):
    def test_inclusive_bounds(self):
        matched, excluded = related_window(RECORDS, SINCE, UNTIL)
        bodies = [r.body for r in matched]
        self.assertIn("at since boundary", bodies)
        self.assertIn("mid window", bodies)
        self.assertIn("at until boundary", bodies)
        self.assertNotIn("before window", bodies)
        self.assertNotIn("after window", bodies)

    def test_no_ts_records_excluded_but_counted(self):
        matched, excluded = related_window(RECORDS, SINCE, UNTIL)
        bodies = [r.body for r in matched]
        self.assertNotIn("no timestamp at all", bodies)
        self.assertNotIn("garbage timestamp", bodies)
        # both no-ts records fall in [since, until] time-wise (well, would,
        # if they had a parseable timestamp) and are NOT service-filtered
        # out here, so both count towards excluded_no_ts.
        self.assertEqual(excluded, 2)

    def test_returns_tuple_of_list_and_int(self):
        result = related_window(RECORDS, SINCE, UNTIL)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        matched, excluded = result
        self.assertIsInstance(matched, list)
        self.assertIsInstance(excluded, int)

    def test_service_filter(self):
        matched, excluded = related_window(RECORDS, SINCE, UNTIL, service="payment-service")
        self.assertEqual([r.body for r in matched], ["mid window"])
        # the order-service no-ts record is filtered out by service BEFORE
        # the no-ts check, so it must not inflate this count; only the
        # payment-service garbage-timestamp record does.
        self.assertEqual(excluded, 1)

    def test_service_filter_no_match_returns_empty(self):
        matched, excluded = related_window(RECORDS, SINCE, UNTIL, service="nonexistent-service")
        self.assertEqual(matched, [])

    def test_narrow_window_excludes_everything_with_ts(self):
        matched, excluded = related_window(
            RECORDS, "2026-07-15T11:22:00.500Z", "2026-07-15T11:22:59.999Z")
        self.assertEqual(matched, [])

    def test_sort_order_matches_related_key(self):
        # same tie-break as correlate.related(): timestamp, then source_ref,
        # then source_line -- verified here via two same-timestamp records
        # from different files.
        same_ts = [
            rec("2026-07-15T11:23:00.000Z", "svc", ref="z.log", line=1),
            rec("2026-07-15T11:23:00.000Z", "svc", ref="a.log", line=1),
        ]
        matched, _ = related_window(same_ts, SINCE, UNTIL)
        self.assertEqual([r.source_ref for r in matched], ["a.log", "z.log"])

    def test_empty_since_until_bounds_open_that_side(self):
        # since=None only lower-bounds nothing -> everything up to until with
        # a parseable timestamp qualifies.
        matched, _ = related_window(RECORDS, None, UNTIL)
        bodies = [r.body for r in matched]
        self.assertIn("before window", bodies)
        self.assertIn("at until boundary", bodies)
        self.assertNotIn("after window", bodies)


class TestCliTimeWindow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.logs = self.root / "logs"; self.logs.mkdir()
        (self.logs / "order-service.log").write_text(fixtures.ORDER_JSONL, encoding="utf-8")
        (self.logs / "payment-service.log").write_text(fixtures.PAYMENT_PLAIN, encoding="utf-8")
        self.out_json = self.root / "report.json"

    def tearDown(self):
        self.dir.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_since_until_without_id_investigates_ok(self):
        code, _ = self._run(["investigate", "--logs", str(self.logs),
                             "--since", "2026-07-15T11:22:00Z",
                             "--until", "2026-07-15T11:22:10Z",
                             "--mode", "ops", "--out", str(self.out_json),
                             "--case-dir", str(self.root)])
        self.assertEqual(code, 0)
        rep = json.loads(self.out_json.read_text(encoding="utf-8"))
        basis = rep["meta"]["correlation_basis"]
        self.assertEqual(basis["kind"], "time_window")
        self.assertEqual(basis["since"], "2026-07-15T11:22:00.000Z")
        self.assertEqual(basis["until"], "2026-07-15T11:22:10.000Z")
        # the ORDER_JSONL fixture's 3rd line is deliberately not valid JSON
        # (empty timestamp) -- it must be excluded and counted, not silently
        # dropped.
        self.assertEqual(basis["excluded_no_ts"], 1)
        # order-service checkout+FAILED (both inside the window) plus
        # payment's "payment AUTHORIZED" line (also inside) is enough
        # evidence for R-ORD-001 to fire on the windowed bundle too.
        self.assertEqual(rep["classification"]["type"], "R-ORD-001")
        self.assertTrue(any("временн" in l.lower() for l in rep["limitations"]))

    def test_md_header_states_time_window_basis(self):
        md_path = self.root / "report.ru.md"
        code, _ = self._run(["investigate", "--logs", str(self.logs),
                             "--since", "2026-07-15T11:22:00Z",
                             "--until", "2026-07-15T11:22:10Z",
                             "--mode", "ops", "--out", str(self.out_json),
                             "--md", str(md_path), "--case-dir", str(self.root)])
        self.assertEqual(code, 0)
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("временн", md.lower())
        self.assertIn("2026-07-15T11:22:00", md)
        self.assertIn("2026-07-15T11:22:10", md)

    def test_around_default_window_is_5m(self):
        # payment's second line (11:22:35.782Z) is 32.78s away from the
        # order-service anchor line (11:22:03.104Z) -- well inside a
        # default 5-minute window centered on the anchor -- so a bare
        # --around with no --window must still pick it up.
        code, _ = self._run(["investigate", "--logs", str(self.logs),
                             "--around", "2026-07-15T11:22:03Z",
                             "--mode", "ops", "--out", str(self.out_json),
                             "--case-dir", str(self.root)])
        self.assertEqual(code, 0)
        rep = json.loads(self.out_json.read_text(encoding="utf-8"))
        self.assertEqual(rep["meta"]["correlation_basis"]["kind"], "time_window")
        evidence_bodies = " ".join(e.get("body", "") for e in rep["evidence"])
        self.assertIn("AUTHORIZED", evidence_bodies)

    def test_service_filter_narrows_window_evidence(self):
        code, _ = self._run(["investigate", "--logs", str(self.logs),
                             "--since", "2026-07-15T11:22:00Z",
                             "--until", "2026-07-15T11:22:10Z",
                             "--service", "payment-service",
                             "--mode", "ops", "--out", str(self.out_json),
                             "--case-dir", str(self.root)])
        self.assertEqual(code, 0)
        rep = json.loads(self.out_json.read_text(encoding="utf-8"))
        self.assertEqual(rep["meta"]["correlation_basis"]["service"], "payment-service")
        for e in rep["evidence"]:
            self.assertEqual(e["service"], "payment-service")

    def test_both_id_and_window_given_is_usage_error(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
                               "--since", "2026-07-15T11:22:00Z",
                               "--until", "2026-07-15T11:22:10Z",
                               "--out", str(self.out_json), "--case-dir", str(self.root)])
        self.assertEqual(code, 2)

    def test_id_and_around_given_is_usage_error(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--correlation-id", "c-8f3a2b91-4d7c-11ee-b962-0242ac120002",
                               "--around", "2026-07-15T11:22:00Z",
                               "--out", str(self.out_json), "--case-dir", str(self.root)])
        self.assertEqual(code, 2)

    def test_neither_id_nor_window_given_is_usage_error(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--out", str(self.out_json), "--case-dir", str(self.root)])
        self.assertEqual(code, 2)

    def test_since_without_until_is_usage_error(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--since", "2026-07-15T11:22:00Z",
                               "--out", str(self.out_json), "--case-dir", str(self.root)])
        self.assertEqual(code, 2)

    def test_window_without_around_is_usage_error(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--window", "5m",
                               "--out", str(self.out_json), "--case-dir", str(self.root)])
        self.assertEqual(code, 2)

    def test_since_and_around_together_is_usage_error(self):
        code, out = self._run(["investigate", "--logs", str(self.logs),
                               "--since", "2026-07-15T11:22:00Z",
                               "--until", "2026-07-15T11:22:10Z",
                               "--around", "2026-07-15T11:22:00Z",
                               "--out", str(self.out_json), "--case-dir", str(self.root)])
        self.assertEqual(code, 2)


@unittest.skipUnless(PACK.is_file(), "pack zip not present")
class TestCliTimeWindowOnPack(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        with zipfile.ZipFile(PACK) as z:
            z.extractall(self.root)
        self.pack = next(self.root.glob("**/logs")).parent

    def tearDown(self):
        self.dir.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_around_window_captures_incident_and_rule_fires(self):
        out = self.root / "report.json"
        code, _ = self._run(["investigate", "--logs", str(self.pack / "logs"),
                             "--around", "2026-07-15T11:22:00Z", "--window", "4m",
                             "--mode", "ops",
                             "--out", str(out), "--case-dir", str(self.root / "case")])
        self.assertEqual(code, 0)
        rep = json.loads(out.read_text(encoding="utf-8"))
        basis = rep["meta"]["correlation_basis"]
        self.assertEqual(basis["kind"], "time_window")
        self.assertEqual(basis["since"], "2026-07-15T11:20:00.000Z")
        self.assertEqual(basis["until"], "2026-07-15T11:24:00.000Z")
        self.assertEqual(rep["classification"]["type"], "R-ORD-001")
        self.assertIn("И-1", rep["invariant_violations"])
        self.assertGreater(len(rep["evidence"]), 5)
        # the second incident (2026-07-16) must not leak into a window
        # scoped to 2026-07-15 11:20-11:24.
        for e in rep["evidence"]:
            self.assertTrue(e["timestamp"].startswith("2026-07-15"))


if __name__ == "__main__":
    unittest.main()
