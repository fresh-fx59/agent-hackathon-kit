import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest
from logalyzer.records import NormalizedRecord
from logalyzer.correlate import related
from logalyzer.evidence import EvidenceBundle

CORR = "c-8f3a2b91-4d7c-11ee-b962-0242ac120002"

def rec(ts, service, level, body, corr="", order="", ref="f.log", line=1, **attrs):
    return NormalizedRecord(timestamp=ts, service=service, level=level, body=body,
                            correlation_id=corr,
                            domain_ids={"order_id": order} if order else {},
                            source_ref=ref, source_line=line, attrs=attrs)

RECORDS = [
    rec("2026-07-15T11:22:03.104Z", "order-service", "INFO", "checkout started", corr=CORR, order="ord-a12f5d7e", line=1),
    rec("2026-07-15T11:22:03.410Z", "kafka", "INFO", "PaymentAuthorized", order="ord-a12f5d7e", ref="kafka_events.jsonl", line=1, event_type="PaymentAuthorized"),
    rec("2026-07-15T11:22:05.425Z", "order-service", "ERROR", "reservation failed, marking order as FAILED", corr=CORR, order="ord-a12f5d7e", line=2, exception_type="ReservationTimeoutException"),
    rec("2026-07-15T11:22:09.000Z", "order-service", "INFO", "unrelated order", order="ord-zzz", line=3),
]

class TestCorrelate(unittest.TestCase):
    def test_expansion_pulls_kafka_by_order_id_and_sorts(self):
        tl = related(RECORDS, CORR)
        self.assertEqual(len(tl), 3)
        self.assertEqual([r.source_line for r in tl], [1, 1, 2])
        self.assertNotIn("ord-zzz", [r.domain_ids.get("order_id") for r in tl])

    def test_evidence_ids_stable_and_findable(self):
        b = EvidenceBundle.build(related(RECORDS, CORR))
        self.assertEqual(b.items[0]["id"], "EV-001")
        hits = b.find(service="order-service", level="ERROR", body_regex="FAILED")
        self.assertEqual(len(hits), 1)
        self.assertEqual(b.by_id(hits[0]["id"])["record"].attrs["exception_type"],
                         "ReservationTimeoutException")

    def test_empty_correlation_id_returns_empty(self):
        tl = related(RECORDS, "")
        self.assertEqual(len(tl), 0)
        tl_none = related(RECORDS, None)
        self.assertEqual(len(tl_none), 0)

if __name__ == "__main__":
    unittest.main()
