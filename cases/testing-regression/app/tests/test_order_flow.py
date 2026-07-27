#!/usr/bin/env python3
"""End-to-end flow test: login -> cart -> checkout -> confirmation.

This is the executable twin of the e2e cases in testcase-map.json
(TC-20/TC-21).  Run standalone:
    python3 cases/testing-regression/app/tests/test_order_flow.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, CASE_DIR)  # for the `app` package

from app import auth, cart, checkout, notifications  # noqa: E402


class OrderFlowTest(unittest.TestCase):

    def setUp(self):
        notifications.reset()

    def test_full_purchase_flow(self):
        user = auth.authenticate("alice", "wonderland")
        basket = cart.Cart()
        basket.add("SKU-1", 1)   # 2490.0
        basket.add("SKU-2", 2)   # 1980.0
        self.assertEqual(basket.subtotal(), 4470.0)

        order = checkout.checkout(basket, user)
        self.assertEqual(order["discount_percent"], 0)  # below loyalty
        self.assertEqual(order["total"], 4470.0)
        self.assertEqual(len(notifications.OUTBOX), 1)
        self.assertEqual(notifications.OUTBOX[0]["to"], user["email"])

    def test_promo_purchase_flow(self):
        user = auth.authenticate("bob", "builder")
        basket = cart.Cart()
        basket.add("SKU-5", 2)   # 9980.0 -> loyalty applies
        order = checkout.checkout(basket, user, promo_code="VIP5")
        self.assertEqual(order["discount_percent"], 10)  # 5 promo + 5 loyalty
        self.assertEqual(order["total"], 8982.0)
        self.assertEqual(notifications.OUTBOX[0]["to"], "bob@example.com")


if __name__ == "__main__":
    unittest.main()
