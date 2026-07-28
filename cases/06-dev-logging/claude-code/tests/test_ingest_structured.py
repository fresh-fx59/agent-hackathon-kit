import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, unittest.mock, tempfile, json, zipfile, io, stat
from pathlib import Path
from logalyzer.masking import Masker
from logalyzer.ingest import read_all
import logalyzer.ingest as ingest_mod

KAFKA = """\
{"ts":"2026-07-15T11:22:03.410Z","topic":"payments.events.v1","partition":1,"offset":45123,"type":"PaymentAuthorized","payload":{"order_id":"ord-a12f5d7e","auth_id":"auth-51ac9d2e"}}
{"ts":"2026-07-15T11:22:05.435Z","topic":"orders.events.v1","partition":3,"offset":98421,"type":"OrderFailed","payload":{"order_id":"ord-a12f5d7e"}}
"""
K8S = """\
2026-07-15T11:20:11Z Warning Unhealthy pod/inventory-service-6f7d9c-x2v4q Readiness probe failed: HTTP probe failed with statuscode: 503
2026-07-15T11:21:02Z Normal ScalingReplicaSet deployment/inventory-service Scaled up replica set to 3
"""
# The real organizers' pack keys the span label "operation", not "name" —
# see petstore_input_pack.zip logs/trace_c-8f3a2b91.json.
TRACE = {"trace_id": "c-8f3a2b91-4d7c-11ee-b962-0242ac120002", "spans": [
    {"span_id": "s-06", "service": "inventory-service", "operation": "reserve",
     "start": "2026-07-15T11:22:03.425Z", "duration_ms": 2496, "status": "OK_LATE",
     "attrs": {"db.wait_ms": 2384, "client_disconnected": True}}]}

def make_tree(dirpath):
    (dirpath / "kafka_events.jsonl").write_text(KAFKA, encoding="utf-8")
    (dirpath / "k8s_events.log").write_text(K8S, encoding="utf-8")
    (dirpath / "trace_c-8f3a2b91.json").write_text(json.dumps(TRACE), encoding="utf-8")

class TestStructured(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        make_tree(self.root)

    def tearDown(self):
        self.dir.cleanup()

    def test_directory_walk_all_formats(self):
        recs = read_all(self.root, Masker())
        kafka = [r for r in recs if r.service == "kafka"]
        self.assertEqual(len(kafka), 2)
        self.assertEqual(kafka[1].attrs["event_type"], "OrderFailed")
        self.assertEqual(kafka[1].domain_ids["order_id"], "ord-a12f5d7e")
        k8s = [r for r in recs if r.service == "k8s"]
        self.assertEqual(k8s[0].level, "WARN")
        spans = [r for r in recs if r.attrs.get("span_id")]
        self.assertEqual(spans[0].attrs["duration_ms"], 2496)
        self.assertEqual(spans[0].trace_id, TRACE["trace_id"])
        self.assertTrue(spans[0].attrs.get("client_disconnected"))
        # Finding 1: span key is "operation" in the real pack; span_name must
        # not be silently dropped, and the body must not double-space.
        self.assertEqual(spans[0].attrs["span_name"], "reserve")
        self.assertEqual(spans[0].body, "span reserve OK_LATE")
        self.assertNotIn("  ", spans[0].body)

    def test_zip_walk_and_traversal_guard(self):
        zpath = self.root / "pack.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("logs/kafka_events.jsonl", KAFKA)
            z.writestr("../evil.txt", "x")
        recs = read_all(zpath, Masker())
        self.assertEqual(len([r for r in recs if r.service == "kafka"]), 2)
        self.assertFalse((self.root.parent / "evil.txt").exists())

    def test_kafka_with_scalar_json_line(self):
        """Kafka file with bare scalar line should not crash batch."""
        kafka_mixed = """\
{"ts":"2026-07-15T11:22:03.410Z","topic":"payments.events.v1","partition":1,"offset":45123,"type":"PaymentAuthorized","payload":{"order_id":"ord-a12f5d7e"}}
42
{"ts":"2026-07-15T11:22:05.435Z","topic":"orders.events.v1","partition":3,"offset":98421,"type":"OrderFailed","payload":{"order_id":"ord-a12f5d7e"}}
"""
        (self.root / "kafka_mixed.jsonl").write_text(kafka_mixed, encoding="utf-8")
        recs = read_all(self.root, Masker())
        kafka = [r for r in recs if r.service == "kafka" and r.source_ref == "kafka_mixed.jsonl"]
        # kafka_mixed.jsonl should have 2 valid + 1 unparsed = 3 total
        self.assertEqual(len(kafka), 3)
        unparsed = [r for r in kafka if r.parse_quality == "unparsed"]
        self.assertEqual(len(unparsed), 1)
        valid = [r for r in kafka if r.parse_quality != "unparsed"]
        self.assertEqual(len(valid), 2)

    def test_trace_with_invalid_json(self):
        """Malformed trace JSON should produce one unparsed record, not crash."""
        (self.root / "trace_bad.json").write_text("{bad json}", encoding="utf-8")
        recs = read_all(self.root, Masker())
        trace_bad = [r for r in recs if r.source_ref == "trace_bad.json"]
        self.assertEqual(len(trace_bad), 1)
        self.assertEqual(trace_bad[0].parse_quality, "unparsed")
        self.assertEqual(trace_bad[0].service, "trace")

    def test_trace_as_json_list(self):
        """Trace file that is a JSON list should produce one unparsed record, not crash."""
        (self.root / "trace_list.json").write_text('[{"span_id": "s-1"}]', encoding="utf-8")
        recs = read_all(self.root, Masker())
        trace_list = [r for r in recs if r.source_ref == "trace_list.json"]
        self.assertEqual(len(trace_list), 1)
        self.assertEqual(trace_list[0].parse_quality, "unparsed")

    def test_hostile_file_does_not_prevent_others(self):
        """A hostile file should not prevent other files from being processed."""
        (self.root / "kafka_mixed.jsonl").write_text("42\ninvalid\n[1,2,3]", encoding="utf-8")
        (self.root / "kafka_valid.jsonl").write_text(KAFKA, encoding="utf-8")
        recs = read_all(self.root, Masker())
        kafka = [r for r in recs if r.service == "kafka"]
        # Should have 2 from kafka_valid.jsonl
        self.assertGreaterEqual(len(kafka), 2)

    def test_kafka_with_non_dict_payload(self):
        """Finding 3: kafka line with a list payload must not raise
        AttributeError on payload.items(); it parses as a normal event
        record with empty domain_ids."""
        kafka_list_payload = ('{"ts":"2026-07-15T11:22:03.410Z","topic":"orders.events.v1",'
                              '"type":"WeirdEvent","payload":[1,2]}\n')
        (self.root / "kafka_weird.jsonl").write_text(kafka_list_payload, encoding="utf-8")
        recs = read_all(self.root, Masker())
        weird = [r for r in recs if r.source_ref == "kafka_weird.jsonl"]
        self.assertEqual(len(weird), 1)
        self.assertEqual(weird[0].service, "kafka")
        self.assertEqual(weird[0].parse_quality, "ok")
        self.assertEqual(weird[0].domain_ids, {})
        self.assertEqual(weird[0].attrs["event_type"], "WeirdEvent")

    def test_kafka_payload_email_is_masked_and_flagged(self):
        """Finding 5: structured (kafka) records must set redaction_applied
        via mask_with_flag, not silently mask without flagging."""
        kafka_email = ('{"ts":"2026-07-15T11:22:03.410Z","topic":"orders.events.v1",'
                       '"type":"CustomerContacted",'
                       '"payload":{"order_id":"ord-a12f5d7e","email":"ivan@example.com"}}\n')
        (self.root / "kafka_pii.jsonl").write_text(kafka_email, encoding="utf-8")
        recs = read_all(self.root, Masker())
        pii = [r for r in recs if r.source_ref == "kafka_pii.jsonl"]
        self.assertEqual(len(pii), 1)
        self.assertTrue(pii[0].redaction_applied)
        self.assertNotIn("ivan@example.com", pii[0].body)

    def test_unreadable_file_yields_visible_unparsed_record(self):
        """Finding 2: a file that fails to ingest must not vanish silently —
        it surfaces as exactly one unparsed record naming the failure, and
        other files in the directory still get processed.

        Patches `_parse_lines` (not `read_source`): Normalization v2 made
        `_ingest_one_file` read a file's lines once and call `_parse_lines`
        directly for both the gz and non-gz paths (previously non-gz files
        went through `read_source`, which re-read the file a second time
        internally) -- `_parse_lines` is the shared seam post-refactor."""
        (self.root / "broken.log").write_text("irrelevant", encoding="utf-8")
        real_parse_lines = ingest_mod._parse_lines

        def flaky(lines, hint, ref, masker, warnings=None):
            if ref == "broken.log":
                raise OSError("simulated unreadable file")
            return real_parse_lines(lines, hint, ref, masker, warnings=warnings)

        with unittest.mock.patch.object(ingest_mod, "_parse_lines", side_effect=flaky):
            recs = ingest_mod.read_all(self.root, Masker())
        broken = [r for r in recs if r.source_ref == "broken.log"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0].parse_quality, "unparsed")
        self.assertEqual(broken[0].level, "UNKNOWN")
        self.assertEqual(broken[0].service, "broken")
        self.assertIn("OSError", broken[0].body)
        # other files (kafka/k8s/trace fixtures from setUp) still load
        kafka = [r for r in recs if r.service == "kafka"]
        self.assertEqual(len(kafka), 2)

    def test_zip_with_symlink_entry_skipped(self):
        """ZIP with symlink entry (suffix-bearing name) should not be extracted."""
        from logalyzer.ingest import _safe_extract

        zpath = self.root / "pack_symlink.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("logs/normal.jsonl", KAFKA)
            # Create a ZipInfo with symlink mode bit and suffix-bearing name
            symlink_info = zipfile.ZipInfo("evil-link.log")
            symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(symlink_info, "target")

        # Extract to a temp directory and verify filesystem state
        extract_dir = tempfile.TemporaryDirectory()
        try:
            _safe_extract(zpath, extract_dir.name)
            # Symlink should NOT be extracted
            self.assertFalse((Path(extract_dir.name) / "evil-link.log").exists(),
                           "Symlink entry should not be extracted")
            # Normal file SHOULD be extracted
            self.assertTrue((Path(extract_dir.name) / "logs" / "normal.jsonl").exists(),
                          "Normal file should be extracted")
        finally:
            extract_dir.cleanup()

if __name__ == "__main__":
    unittest.main()
