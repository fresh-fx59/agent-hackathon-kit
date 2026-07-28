import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, tempfile, json, zipfile, io, stat
from pathlib import Path
from logalyzer.masking import Masker
from logalyzer.ingest import read_all

KAFKA = """\
{"ts":"2026-07-15T11:22:03.410Z","topic":"payments.events.v1","partition":1,"offset":45123,"type":"PaymentAuthorized","payload":{"order_id":"ord-a12f5d7e","auth_id":"auth-51ac9d2e"}}
{"ts":"2026-07-15T11:22:05.435Z","topic":"orders.events.v1","partition":3,"offset":98421,"type":"OrderFailed","payload":{"order_id":"ord-a12f5d7e"}}
"""
K8S = """\
2026-07-15T11:20:11Z Warning Unhealthy pod/inventory-service-6f7d9c-x2v4q Readiness probe failed: HTTP probe failed with statuscode: 503
2026-07-15T11:21:02Z Normal ScalingReplicaSet deployment/inventory-service Scaled up replica set to 3
"""
TRACE = {"trace_id": "c-8f3a2b91-4d7c-11ee-b962-0242ac120002", "spans": [
    {"span_id": "s-06", "service": "inventory-service", "name": "reserve",
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
