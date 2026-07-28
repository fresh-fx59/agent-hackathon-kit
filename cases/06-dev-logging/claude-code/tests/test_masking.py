import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest
from logalyzer.masking import Masker

class TestMasker(unittest.TestCase):
    def setUp(self):
        self.m = Masker()

    def test_email_phone_card_masked(self):
        s, applied = self.m.mask_with_flag(
            "user ivan.petrov@example.com phone +7 916 123-45-67 card 4276 8381 2345 1231")
        self.assertTrue(applied)
        self.assertNotIn("ivan.petrov@example.com", s)
        self.assertNotIn("123-45-67", s)
        self.assertNotIn("4276 8381 2345 1231", s)
        self.assertIn("<EMAIL:", s)  # pseudonym form <EMAIL:u-01>

    def test_pseudonyms_stable_within_run(self):
        a, _ = self.m.mask_with_flag("mail a@b.com again a@b.com")
        first = a.split("again")[0]; second = a.split("again")[1]
        self.assertEqual(first.strip().split()[-1], second.strip().split()[-1])

    def test_luhn_guard_leaves_non_card_digits(self):
        s, applied = self.m.mask_with_flag("offset 4276838123451111")  # fails Luhn
        self.assertIn("4276838123451111", s)

    def test_technical_ids_untouched(self):
        s, applied = self.m.mask_with_flag(
            "correlation_id=c-8f3a2b91-4d7c-11ee-b962-0242ac120002 order ord-a12f5d7e")
        self.assertIn("c-8f3a2b91-4d7c-11ee-b962-0242ac120002", s)
        self.assertIn("ord-a12f5d7e", s)
        self.assertFalse(applied)

if __name__ == "__main__":
    unittest.main()
