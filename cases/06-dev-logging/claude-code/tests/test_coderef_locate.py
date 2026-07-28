import unittest, tempfile
from pathlib import Path
from logalyzer.records import NormalizedRecord
from logalyzer.evidence import EvidenceBundle
from logalyzer.coderef import extract_identifiers, locate, gate

JAVA = """\
package com.petstore.order.svc;
public class OrderCheckoutService {
    public CheckoutResult checkout(CheckoutRequest req) {
        try {
            inventoryClient.reserve(req);
        } catch (ReservationTimeoutException e) {
            order.setStatus(OrderStatus.FAILED);
        }
        return result;
    }
}
"""

class TestLocate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        repo = Path(self.dir.name) / "repo"
        src = repo / "services/order-service/src/main/java/com/petstore/order/svc"
        src.mkdir(parents=True)
        (repo / "pom.xml").write_text("<project/>")
        (src / "OrderCheckoutService.java").write_text(JAVA)
        self.repo = repo

    def tearDown(self):
        self.dir.cleanup()

    def _bundle(self):
        return EvidenceBundle.build([NormalizedRecord(
            timestamp="t", service="order-service", level="ERROR",
            body="reservation failed, marking order as FAILED",
            attrs={"exception_type": "ReservationTimeoutException",
                   "logger": "c.p.o.svc.OrderCheckoutService"})])

    def test_extract_and_locate_catch_block(self):
        ids = extract_identifiers(self._bundle())
        self.assertIn("ReservationTimeoutException", ids["exceptions"])
        refs = locate(ids, [self.repo])
        top = refs[0]
        self.assertTrue(top["file"].endswith("OrderCheckoutService.java"))
        self.assertEqual(top["method"], "checkout")
        self.assertEqual(top["confidence"], "high")
        self.assertGreater(top["line"], 0)

    def test_gate_rejects_nonexistent(self):
        fake = {"file": "no/such/File.java", "method": "handleReservationTimeout",
                "line": 1, "reason": "from docs", "confidence": "high"}
        kept, rejected = gate([fake], [self.repo])
        self.assertEqual(kept, [])
        self.assertEqual(rejected, 1)

if __name__ == "__main__":
    unittest.main()
