import unittest
from logalyzer.records import NormalizedRecord

class TestNormalizedRecord(unittest.TestCase):
    def test_defaults_and_to_dict(self):
        r = NormalizedRecord(timestamp="2026-07-15T11:22:05.425Z",
                             service="order-service", level="ERROR",
                             body="reservation failed")
        self.assertEqual(r.observed_timestamp, "2026-07-15T11:22:05.425Z")
        self.assertEqual(r.parse_quality, "ok")
        self.assertFalse(r.redaction_applied)
        d = r.to_dict()
        self.assertEqual(d["service"], "order-service")
        self.assertEqual(d["domain_ids"], {})
        self.assertEqual(d["source_line"], 0)

    def test_level_normalized(self):
        r = NormalizedRecord(timestamp="t", service="s", level="warning", body="b")
        self.assertEqual(r.level, "WARN")
        r2 = NormalizedRecord(timestamp="t", service="s", level="weird", body="b")
        self.assertEqual(r2.level, "UNKNOWN")

if __name__ == "__main__":
    unittest.main()
