#!/usr/bin/env python3
"""Tests for measure.py — deterministic verdicts from a captured run.

The load-bearing case is `test_right_file_wrong_lines_is_not_reached`: the whole
diagnosis rests on telling "never opened the evidence" apart from "opened it and
failed to connect it". If that distinction breaks, every verdict downstream is noise.

The three-valued verdict matters too. A shell-based read (`sed -n`, `grep`) often
cannot be resolved to a line range. Calling that "not reached" would manufacture
coverage failures that never happened, so it is reported as `unknown`.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import measure  # noqa: E402

PROOFS = [
    {"file": "apps/api.log", "line_start": 178977, "line_end": 178996, "note": "the NPE"},
]


def stream(*records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def tool_use(name, inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def tool_result(text):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "content": text}]}}


class ReadEvents(unittest.TestCase):
    def test_read_file_offset_and_limit_become_a_line_range(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        ev = measure.read_events(p)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["range_known"])
        self.assertEqual(ev[0]["line_start"], 178971)
        self.assertEqual(ev[0]["line_end"], 179010)

    def test_tool_result_text_is_preferred_over_the_input(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 1, "limit": 2}),
                   tool_result("Read lines 2-3 of 4 from /c/apps/api.log"))
        ev = measure.read_events(p)
        self.assertEqual((ev[0]["line_start"], ev[0]["line_end"]), (2, 3))

    def test_shell_read_records_the_file_but_leaves_the_range_unknown(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "grep -n 'NullPointer' /c/apps/api.log"}))
        ev = measure.read_events(p)
        self.assertEqual(ev[0]["file"], "/c/apps/api.log")
        self.assertFalse(ev[0]["range_known"])


class ProofReach(unittest.TestCase):
    def test_reading_the_proof_lines_counts_as_reached(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "reached")

    def test_right_file_wrong_lines_is_not_reached(self):
        # THE load-bearing case: it opened the file, but nowhere near the evidence.
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 0, "limit": 200}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")
        self.assertIn("apps/api.log", r["files_opened"])

    def test_never_opening_the_file_is_not_reached(self):
        p = stream(tool_use("read_file", {"file_path": "/c/other.log",
                                          "offset": 0, "limit": 10}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")
        self.assertEqual(r["files_opened"], ["other.log"])

    def test_unresolvable_shell_read_of_the_proof_file_is_unknown_not_a_failure(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "sed -n '178977,178996p' /c/apps/api.log"}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "unknown",
                         "an unresolvable range must not be reported as a coverage failure")

    def test_empty_stream_is_not_reached(self):
        p = stream()
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")

    def test_files_opened_matches_files_with_proofs_on_a_deep_absolute_path(self):
        # Regression: a real captured run nests the proof file many directories
        # deep under a sandbox root, unlike the fixture's single-segment "/c/"
        # mount. files_opened must still land on the exact corpus-relative name
        # so it is directly comparable to files_with_proofs.
        proofs = [{"file": "apps/checkout-api/checkout-api.log",
                   "line_start": 10, "line_end": 20, "note": "the NPE"}]
        p = stream(tool_use("read_file", {
            "file_path": "/home/claude-developer/hack/agent-hackathon-kit/cases/06-dev-logging"
                         "/sherlock/measure/cases/D01/apps/checkout-api/checkout-api.log",
            "offset": 5, "limit": 20}))
        r = measure.proof_reach(measure.read_events(p), proofs)
        self.assertEqual(r["verdict"], "reached")
        self.assertEqual(r["files_opened"], r["files_with_proofs"])

    def test_same_basename_in_different_directories_is_not_the_same_file(self):
        # syslog/node-a/syslog and syslog/node-b/syslog are two different hosts'
        # logs that happen to share a basename. A basename-only fallback would
        # let reading node-a satisfy node-b's (red-herring) proof.
        proofs = [{"file": "syslog/node-b/syslog", "line_start": 5, "line_end": 10,
                   "note": "RED HERRING"}]
        p = stream(tool_use("read_file", {"file_path": "/c/syslog/node-a/syslog",
                                          "offset": 0, "limit": 20}))
        r = measure.proof_reach(measure.read_events(p), proofs)
        self.assertEqual(r["verdict"], "not_reached",
                         "node-a/syslog must not satisfy node-b/syslog's proof by basename alone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
