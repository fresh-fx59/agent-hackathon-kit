import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
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

JAVA_WITH_COMMENT = """\
package com.petstore.order.svc;
/**
 * OrderCheckoutService handles checkout operations.
 * Typical pattern: catch(ReservationTimeoutException) and mark order FAILED.
 * See handleReservationTimeout() for details.
 */
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

    def test_locate_ignores_catch_in_javadoc(self):
        """Javadoc comment containing catch(...) should not be cited as real catch block."""
        self.dir2 = tempfile.TemporaryDirectory()
        repo = Path(self.dir2.name) / "repo"
        src = repo / "services/order-service/src/main/java/com/petstore/order/svc"
        src.mkdir(parents=True)
        (repo / "pom.xml").write_text("<project/>")
        (src / "OrderCheckoutService.java").write_text(JAVA_WITH_COMMENT)

        ids = extract_identifiers(self._bundle())
        refs = locate(ids, [repo])
        top = refs[0]
        # Top ref must be the real catch block (method="checkout"), not the javadoc line
        self.assertEqual(top["method"], "checkout")
        self.assertGreater(top["line"], 5)  # Real catch is on line 13, javadoc comment is on line 3-5
        # Verify no kept ref points at the javadoc comment line
        kept, _ = gate(refs, [repo])
        for ref in kept:
            if ref["reason"].startswith("catch("):
                # Catch-derived refs must have a method (not methodless)
                self.assertNotEqual(ref["method"], "")

        self.dir2.cleanup()

    def test_gate_rejects_methodless_catch_ref(self):
        """gate() must reject catch-derived refs with empty method, even if file exists."""
        methodless_catch = {"file": "repo/services/order-service/src/main/java/com/petstore/order/svc/OrderCheckoutService.java",
                            "method": "", "line": 13, "reason": "catch(ReservationTimeoutException)",
                            "confidence": "high"}
        kept, rejected = gate([methodless_catch], [self.repo])
        self.assertEqual(kept, [])
        self.assertEqual(rejected, 1)

    def test_gate_accepts_methodless_logger_ref(self):
        """gate() must accept logger-derived refs with empty method when file exists."""
        logger_ref = {"file": "repo/services/order-service/src/main/java/com/petstore/order/svc/OrderCheckoutService.java",
                      "method": "", "line": 0, "reason": "logger c.p.o.svc.OrderCheckoutService",
                      "confidence": "medium"}
        kept, rejected = gate([logger_ref], [self.repo])
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected, 0)

if __name__ == "__main__":
    unittest.main()
