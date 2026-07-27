#!/usr/bin/env python3
"""Unit tests for app/catalog.py (demo shop of the smart-regression case).

Run standalone:  python3 cases/testing-regression/app/tests/test_catalog.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, CASE_DIR)  # for the `app` package

from app import catalog  # noqa: E402


class CatalogTest(unittest.TestCase):

    def test_get_product(self):
        product = catalog.get_product("SKU-1")
        self.assertEqual(product.name, "Keyboard")
        self.assertEqual(product.price, 2490.0)

    def test_get_unknown_product_raises(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.get_product("SKU-404")

    def test_list_products_sorted_by_id(self):
        ids = [p.product_id for p in catalog.list_products()]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), 6)

    def test_search_is_case_insensitive(self):
        hits = catalog.search("mo")
        names = [p.name for p in hits]
        self.assertIn("Mouse", names)
        self.assertIn("Monitor", names)

    def test_search_empty_query_finds_nothing(self):
        self.assertEqual(catalog.search("   "), [])

    def test_in_stock(self):
        self.assertTrue(catalog.in_stock("SKU-3", 5))
        self.assertFalse(catalog.in_stock("SKU-3", 6))
        self.assertFalse(catalog.in_stock("SKU-6", 1))  # out of stock


if __name__ == "__main__":
    unittest.main()
