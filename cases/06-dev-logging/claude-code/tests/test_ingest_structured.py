import unittest, tempfile, json, zipfile, io
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

if __name__ == "__main__":
    unittest.main()
