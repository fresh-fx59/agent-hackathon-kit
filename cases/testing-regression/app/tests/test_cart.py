#!/usr/bin/env python3
"""Unit tests for app/cart.py (demo shop of the smart-regression case).

Run standalone:  python3 cases/testing-regression/app/tests/test_cart.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, CASE_DIR)  # for the `app` package

from app import cart, catalog  # noqa: E402


class CartTest(unittest.TestCase):

    def setUp(self):
        self.cart = cart.Cart()

    def test_add_returns_line_quantity(self):
        self.assertEqual(self.cart.add("SKU-1", 2), 2)
        self.assertEqual(self.cart.items, {"SKU-1": 2})

    def test_add_merges_lines(self):
        self.cart.add("SKU-1", 2)
        self.assertEqual(self.cart.add("SKU-1", 3), 5)
        self.assertEqual(self.cart.items, {"SKU-1": 5})

    def test_add_rejects_zero_and_negative(self):
        for bad in (0, -1, -99):
            with self.assertRaises(cart.CartError):
                self.cart.add("SKU-1", bad)

    def test_add_rejects_non_integers(self):
        for bad in ("2", 1.5, None, True):
            with self.assertRaises(cart.CartError):
                self.cart.add("SKU-1", bad)

    def test_add_rejects_unknown_product(self):
        with self.assertRaises(catalog.CatalogError):
            self.cart.add("SKU-404", 1)

    def test_line_cap_single_add(self):
        with self.assertRaises(cart.CartError):
            self.cart.add("SKU-4", cart.MAX_QUANTITY_PER_LINE + 1)

    def test_line_cap_via_merge(self):
        self.cart.add("SKU-4", cart.MAX_QUANTITY_PER_LINE)
        with self.assertRaises(cart.CartError):
            self.cart.add("SKU-4", 1)

    def test_remove(self):
        self.cart.add("SKU-1", 1)
        self.cart.remove("SKU-1")
        self.assertEqual(self.cart.items, {})
        with self.assertRaises(cart.CartError):
            self.cart.remove("SKU-1")

    def test_totals(self):
        self.cart.add("SKU-1", 2)   # 2 * 2490.0
        self.cart.add("SKU-2", 1)   # 1 * 990.0
        self.assertEqual(self.cart.total_items(), 3)
        self.assertEqual(self.cart.subtotal(), 5970.0)


if __name__ == "__main__":
    unittest.main()
