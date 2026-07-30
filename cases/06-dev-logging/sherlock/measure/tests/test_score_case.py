#!/usr/bin/env python3
"""Tests for score_case.py — the judge call, with the transport injected.

No network: `score()` takes a `call(prompt) -> str` so the HTTP layer can be stubbed.
The judge is gpt-5.5 on the cliproxyapi broker, chosen because it is neutral to BOTH
the model under test (deepseek) and the skill's author (Claude), and because it
reproduces the historical scores.jsonl column.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import score_case  # noqa: E402

CASE = {"case_id": "D04", "title": "un-indexed JSONB vendor_ref lookup",
        "root_cause": "catalog-svc 4.7.2 introduced a seq-scan with no expression index",
        "requires": "cross-format correlation", "proof_locations": []}


class Prompt(unittest.TestCase):
    def test_prompt_carries_the_root_cause_and_the_report(self):
        p = score_case.build_prompt(CASE, "мой отчёт про индекс")
        self.assertIn("catalog-svc 4.7.2", p)
        self.assertIn("мой отчёт про индекс", p)
        self.assertIn("D04", p)


class ParseVerdict(unittest.TestCase):
    def test_plain_json(self):
        v = score_case.parse_verdict('{"found": true, "why": "names the index"}')
        self.assertTrue(v["found"])

    def test_fenced_json_is_unwrapped(self):
        v = score_case.parse_verdict('```json\n{"found": false, "why": "no"}\n```')
        self.assertFalse(v["found"])

    def test_unparseable_output_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            score_case.parse_verdict("I think it probably found it, yes")


class Score(unittest.TestCase):
    def test_score_uses_the_injected_transport(self):
        seen = {}

        def fake(prompt):
            seen["prompt"] = prompt
            return '{"found": true, "why": "identifies the missing index"}'

        r = score_case.score(CASE, "отчёт", fake)
        self.assertTrue(r["found"])
        self.assertEqual(r["case_id"], "D04")
        self.assertIn("catalog-svc 4.7.2", seen["prompt"])

    def test_a_transport_error_is_not_a_not_found(self):
        def boom(prompt):
            raise RuntimeError("400 Upstream request failed")

        with self.assertRaises(RuntimeError):
            score_case.score(CASE, "отчёт", boom)


if __name__ == "__main__":
    unittest.main(verbosity=2)
