import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, re
from logalyzer.records import NormalizedRecord
from logalyzer.evidence import EvidenceBundle
from logalyzer.report import build, render_ru

def bundle():
    return EvidenceBundle.build([
        NormalizedRecord(timestamp="2026-07-15T11:22:03.410Z", service="kafka",
                         level="INFO", body="PaymentAuthorized"),
        NormalizedRecord(timestamp="2026-07-15T11:22:05.425Z", service="order-service",
                         level="ERROR", body="marking order as FAILED"),
    ])

MATCH = {"rule_id": "R-ORD-001", "name": "Оплата авторизована, но заказ FAILED",
         "severity": "critical", "hypothesis": "Таймаут обработан как терминальный.",
         "invariant_ref": "И-1", "suggested_fix_ref": "FIX-ORD-001",
         "evidence_ids": ["EV-001", "EV-002"], "rubric_sha": "abc123def456"}

CODEREF = {"file": "repo/services/order-service/src/main/java/com/petstore/order/svc/OrderCheckoutService.java",
           "method": "checkout", "line": 87, "reason": "catch(ReservationTimeoutException)",
           "confidence": "high"}

class TestReport(unittest.TestCase):
    def test_dev_mode_has_code_ops_mode_does_not(self):
        dev = build([MATCH], bundle(), [CODEREF], "dev", {"correlation_id": "c-8f3a2b91"})
        self.assertTrue(dev["root_cause"]["file"].endswith("OrderCheckoutService.java"))
        ops = build([MATCH], bundle(), [], "ops", {"correlation_id": "c-8f3a2b91"})
        self.assertIsNone(ops["root_cause"]["file"])
        self.assertTrue(any("код" in l.lower() for l in ops["limitations"]))

    def test_render_ru_is_claim_free(self):
        rep = build([MATCH], bundle(), [CODEREF], "dev", {"correlation_id": "c-8f3a2b91"})
        md = render_ru(rep)
        for ev in re.findall(r"EV-\d{3}", md):
            self.assertIn(ev, [e["id"] for e in rep["evidence"]])
        for path in re.findall(r"[\w/.-]+\.java", md):
            self.assertIn(path, [rep["root_cause"]["file"]] +
                          [c.get("file") for c in rep["code_recommendations"]])
        self.assertIn("И-1", md)

if __name__ == "__main__":
    unittest.main()
