#!/usr/bin/env python3
"""Regression: a valid corpus outside the state-change catalogue is N/A."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SHERLOCK = Path(__file__).resolve().parents[2]
STATECHECK = SHERLOCK / "skills" / "v52" / "tools" / "statecheck.py"


def event(provider, event_id):
    return {"Event": {"System": {
        "Provider": {"#attributes": {"Name": provider}},
        "EventID": {"#text": event_id},
        "TimeCreated": {"#attributes": {"SystemTime": "2024-01-01T00:00:00Z"}},
    }}}


class StatecheckNotApplicable(unittest.TestCase):
    def invoke(self, lines):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "events.jsonl").write_text(lines, encoding="utf-8")
            report = root / "report.md"
            report.write_text("# report\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(STATECHECK), "--corpus", str(corpus),
                 "--report", str(report), "--json"], text=True,
                capture_output=True, check=False)
        return result, json.loads(result.stdout)

    def test_valid_non_catalogue_event_is_explicitly_not_applicable(self):
        result, payload = self.invoke(json.dumps(event("PowerShell", 600)) + "\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(payload["not_applicable"])
        self.assertEqual(1, payload["valid_records"])
        self.assertEqual([], payload["groups"])

    def test_empty_or_malformed_corpus_stays_blocking(self):
        cases = (("", 0), ("not json\n", 0),
                 (json.dumps(event("PowerShell", 600)) + "\n{truncated\n", 1),
                 (json.dumps({"Event": {"System": {}}}) + "\n", 0),
                 (json.dumps({"Event": {"System": {"Provider": {"Name": ""},
                                                     "EventID": {"#text": 600}}}}) + "\n", 0),
                 (json.dumps({"Event": {"System": {"Provider": {"Name": "   "},
                                                     "EventID": {"#text": 600}}}}) + "\n", 0),
                 (json.dumps({"Event": {"System": {"Provider": {"Name": 7},
                                                     "EventID": {"#text": 600}}}}) + "\n", 0),
                 (json.dumps({"Event": {"System": {"Provider": {"Name": "PowerShell"},
                                                     "EventID": {"#text": True}}}}) + "\n", 0))
        for lines, expected_valid in cases:
            with self.subTest(lines=repr(lines)):
                result, payload = self.invoke(lines)
                self.assertEqual(3, result.returncode, result.stderr)
                self.assertTrue(payload["empty_census"])
                self.assertFalse(payload["not_applicable"])
                self.assertEqual(expected_valid, payload["valid_records"])
                if lines:
                    self.assertGreater(payload["invalid_records"], 0)

    def test_catalogue_match_is_not_not_applicable(self):
        result, payload = self.invoke(json.dumps(event("Service Control Manager", 7045)) + "\n")
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertFalse(payload["not_applicable"])
        self.assertEqual(1, payload["valid_records"])
        self.assertEqual(1, len(payload["groups"]))


if __name__ == "__main__":
    unittest.main()
