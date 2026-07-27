#!/usr/bin/env python3
"""Unit tests for app/auth.py (demo shop of the smart-regression case).

Run standalone:  python3 cases/testing-regression/app/tests/test_auth.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, CASE_DIR)  # for the `app` package

from app import auth  # noqa: E402


class AuthTest(unittest.TestCase):

    def test_authenticate_ok(self):
        user = auth.authenticate("alice", "wonderland")
        self.assertEqual(user["email"], "alice@example.com")

    def test_wrong_password_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.authenticate("alice", "not-the-password")

    def test_unknown_user_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.authenticate("mallory", "whatever")

    def test_require_user_passes_known_user(self):
        user = auth.USERS["bob"]
        self.assertIs(auth.require_user(user), user)

    def test_require_user_rejects_anonymous(self):
        for bad in (None, {}, {"login": "mallory"}, "alice"):
            with self.assertRaises(auth.AuthError):
                auth.require_user(bad)


if __name__ == "__main__":
    unittest.main()
