#!/usr/bin/env python3
"""Unit tests for app/checkout.py (demo shop of the smart-regression case).

Run standalone:  python3 cases/testing-regression/app/tests/test_checkout.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, CASE_DIR)  # for the `app` package

from app import auth, cart, checkout, notifications  # noqa: E402


class DiscountPercentTest(unittest.TestCase):
    """The pure discount calculation."""

    def test_no_discount_below_threshold(self):
        self.assertEqual(checkout.discount_percent(1000.0), 0)

    def test_promo_code_alone(self):
        self.assertEqual(checkout.discount_percent(1000.0, "WELCOME10"), 10)
        self.assertEqual(checkout.discount_percent(1000.0, "VIP5"), 5)

    def test_loyalty_alone(self):
        self.assertEqual(checkout.discount_percent(5000.0), 5)
        self.assertEqual(checkout.discount_percent(4999.99), 0)

    def test_promo_and_loyalty_stack(self):
        self.assertEqual(checkout.discount_percent(5000.0, "VIP5"), 10)

    def test_stacked_discount_hits_the_cap(self):
        self.assertEqual(checkout.discount_percent(999999.0, "WELCOME10"),
                         checkout.MAX_DISCOUNT_PERCENT)

    def test_unknown_promo_code_raises(self):
        with self.assertRaises(checkout.CheckoutError):
            checkout.discount_percent(1000.0, "HACKATHON")


class CheckoutTest(unittest.TestCase):
    """Order creation around the discount calculation."""

    def setUp(self):
        notifications.reset()
        self.user = auth.USERS["alice"]
        self.cart = cart.Cart()

    def test_empty_cart_rejected(self):
        with self.assertRaises(checkout.CheckoutError):
            checkout.checkout(self.cart, self.user)

    def test_order_without_promo(self):
        self.cart.add("SKU-2", 1)  # 990.0, below the loyalty threshold
        order = checkout.checkout(self.cart, self.user)
        self.assertTrue(order["order_id"].startswith("ORD-"))
        self.assertEqual(order["discount_percent"], 0)
        self.assertEqual(order["total"], 990.0)

    def test_order_with_promo_and_loyalty(self):
        self.cart.add("SKU-3", 1)  # 18990.0 -> loyalty applies
        order = checkout.checkout(self.cart, self.user, promo_code="WELCOME10")
        self.assertEqual(order["discount_percent"], 15)  # 10 + 5, at the cap
        self.assertEqual(order["discount"], 2848.5)
        self.assertEqual(order["total"], 16141.5)

    def test_order_sends_confirmation(self):
        self.cart.add("SKU-2", 2)
        order = checkout.checkout(self.cart, self.user)
        self.assertEqual(len(notifications.OUTBOX), 1)
        message = notifications.OUTBOX[0]
        self.assertEqual(message["to"], "alice@example.com")
        self.assertIn(order["order_id"], message["subject"])


if __name__ == "__main__":
    unittest.main()
