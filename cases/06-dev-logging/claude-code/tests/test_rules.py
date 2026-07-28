import unittest
from pathlib import Path
from logalyzer.records import NormalizedRecord
from logalyzer.evidence import EvidenceBundle
from logalyzer.rules_engine import load_rules, evaluate

RULES_PATH = Path(__file__).resolve().parents[1] / "rules" / "rules.json"

def rec(ts, service, level, body, **attrs):
    return NormalizedRecord(timestamp=ts, service=service, level=level, body=body, attrs=attrs)

class TestRules(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules(RULES_PATH)

    def test_rubric_sha_present_and_stable(self):
        self.assertEqual(len(self.catalog["rubric_sha"]), 12)
        self.assertEqual(self.catalog["rubric_sha"], load_rules(RULES_PATH)["rubric_sha"])

    def test_r_ord_001_fires_on_sequence(self):
        b = EvidenceBundle.build([
            rec("2026-07-15T11:22:03.410Z", "kafka", "INFO", "PaymentAuthorized {...}"),
            rec("2026-07-15T11:22:05.425Z", "order-service", "ERROR",
                "reservation failed, marking order as FAILED"),
        ])
        matches = evaluate(self.catalog, b)
        ids = [m["rule_id"] for m in matches]
        self.assertIn("R-ORD-001", ids)
        m = [x for x in matches if x["rule_id"] == "R-ORD-001"][0]
        self.assertEqual(len(m["evidence_ids"]), 2)
        self.assertEqual(m["invariant_ref"], "И-1")

    def test_sequence_respects_order_and_window(self):
        b = EvidenceBundle.build([
            rec("2026-07-15T11:22:05.425Z", "order-service", "ERROR", "marking order as FAILED"),
            rec("2026-07-15T11:23:59.000Z", "kafka", "INFO", "PaymentAuthorized"),
        ])
        self.assertNotIn("R-ORD-001", [m["rule_id"] for m in evaluate(self.catalog, b)])

    def test_quarantined_rule_never_fires(self):
        b = EvidenceBundle.build([
            rec("2026-07-16T09:14:00.000Z", "notification-service", "ERROR",
                "SMTP timeout, delivery failed"),
        ])
        self.assertNotIn("R-NOTIF-001", [m["rule_id"] for m in evaluate(self.catalog, b)])

    def test_windowed_rule_rejects_timestamps_beyond_window(self):
        """Well-formed timestamps 40s apart exceed 30s window; R-ORD-001 must NOT fire."""
        b = EvidenceBundle.build([
            rec("2026-07-15T11:22:03.410Z", "kafka", "INFO", "PaymentAuthorized {...}"),
            rec("2026-07-15T11:22:43.410Z", "order-service", "ERROR",
                "reservation failed, marking order as FAILED"),
        ])
        self.assertNotIn("R-ORD-001", [m["rule_id"] for m in evaluate(self.catalog, b)])

    def test_windowed_rule_rejects_unparseable_timestamp_in_sequence(self):
        """Second element with empty/unparseable timestamp in windowed rule; R-ORD-001 must NOT fire."""
        b = EvidenceBundle.build([
            rec("2026-07-15T11:22:03.410Z", "kafka", "INFO", "PaymentAuthorized {...}"),
            rec("", "order-service", "ERROR", "marking order as FAILED"),
        ])
        self.assertNotIn("R-ORD-001", [m["rule_id"] for m in evaluate(self.catalog, b)])

    def test_unwindowed_rule_accepts_empty_timestamps(self):
        """all_of rule (no window) with empty-timestamp items; R-INV-001 must fire."""
        b = EvidenceBundle.build([
            rec("", "inventory-service", "INFO", "processing", client_disconnected=True),
        ])
        ids = [m["rule_id"] for m in evaluate(self.catalog, b)]
        self.assertIn("R-INV-001", ids)

if __name__ == "__main__":
    unittest.main()
