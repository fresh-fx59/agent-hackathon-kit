import unittest, tempfile, os
from pathlib import Path
from logalyzer.masking import Masker
from logalyzer.ingest import read_source

ORDER_JSONL = """\
{"ts":"2026-07-15T11:22:03.104Z","service":"order-service","level":"INFO","correlation_id":"c-8f3a2b91-4d7c-11ee-b962-0242ac120002","order_id":"ord-a12f5d7e","msg":"checkout started"}
{"ts":"2026-07-15T11:22:05.425Z","service":"order-service","level":"ERROR","correlation_id":"c-8f3a2b91-4d7c-11ee-b962-0242ac120002","order_id":"ord-a12f5d7e","msg":"reservation failed, marking order as FAILED","exception_type":"ReservationTimeoutException"}
not json at all — русский комментарий в логе
"""

PAYMENT_PLAIN = """\
2026-07-15 11:22:03.402 [http-nio-1] INFO  c.p.p.svc.PaymentService - payment AUTHORIZED auth_id=auth-51ac9d2e correlation_id=c-8f3a2b91-4d7c-11ee-b962-0242ac120002 customer=ivan@example.com
2026-07-15 11:22:35.782 [pool-2] WARN  c.p.p.svc.ReconciliationJob - payment in AUTHORIZED but order in FAILED correlation_id=c-8f3a2b91-4d7c-11ee-b962-0242ac120002
"""

class TestLineReaders(unittest.TestCase):
    def _write(self, name, content):
        p = Path(self.dir.name) / name
        p.write_text(content, encoding="utf-8")
        return p

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.masker = Masker()

    def tearDown(self):
        self.dir.cleanup()

    def test_jsonl_parsed_and_unparsed_line_kept(self):
        recs = read_source(self._write("order-service.log", ORDER_JSONL), self.masker)
        self.assertEqual(len(recs), 3)
        self.assertEqual(recs[1].level, "ERROR")
        self.assertEqual(recs[1].correlation_id, "c-8f3a2b91-4d7c-11ee-b962-0242ac120002")
        self.assertEqual(recs[1].domain_ids.get("order_id"), "ord-a12f5d7e")
        self.assertEqual(recs[1].attrs.get("exception_type"), "ReservationTimeoutException")
        self.assertEqual(recs[1].source_line, 2)
        self.assertEqual(recs[2].parse_quality, "unparsed")
        self.assertEqual(recs[0].service, "order-service")

    def test_plaintext_logback_parsed_and_masked(self):
        recs = read_source(self._write("payment-service.log", PAYMENT_PLAIN), self.masker)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].level, "INFO")
        self.assertEqual(recs[0].attrs.get("logger"), "c.p.p.svc.PaymentService")
        self.assertIn("auth-51ac9d2e", recs[0].body)
        self.assertNotIn("ivan@example.com", recs[0].body)
        self.assertTrue(recs[0].redaction_applied)
        self.assertEqual(recs[0].correlation_id, "c-8f3a2b91-4d7c-11ee-b962-0242ac120002")
        self.assertEqual(recs[0].service, "payment-service")  # from filename

if __name__ == "__main__":
    unittest.main()
