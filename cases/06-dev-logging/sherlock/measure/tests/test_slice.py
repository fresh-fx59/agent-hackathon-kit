#!/usr/bin/env python3
"""Tests for slice.py — per-defect case construction.

The load-bearing property is that slicing keeps WHOLE FILES, so the answer key's
1-based physical proof line numbers remain valid inside the slice with no
renumbering. A slice that shifted line numbers would silently invalidate every
proof-reach check built on top of it.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import slice as slicer  # noqa: E402


def tiny_key():
    return {
        "defects": [
            {"id": "D01", "title": "NPE in promo", "description": "boom",
             "root_cause": "normalized() returns null", "requires": "single-format read",
             "proof_locations": [
                 {"file": "apps/api.log", "line_start": 3, "line_end": 3, "note": "the NPE"},
                 {"file": "inhouse/promo.plog", "line_start": 2, "line_end": 2, "note": "input"},
             ]},
            {"id": "D12", "title": "RED HERRING: SYN flood", "description": "not a defect",
             "root_cause": "n/a", "requires": "statistical/rate reasoning to REFUTE",
             "proof_locations": [{"file": "syslog/node-b", "line_start": 1, "line_end": 1,
                                  "note": "noise"}]},
        ]
    }


def make_corpus(root):
    files = {
        "apps/api.log": "one\ntwo\nNullPointerException here\nfour\n",
        "inhouse/promo.plog": "hdr\ncode=summer26 rejected\n",
        "syslog/node-b": "syn flood\n",
        "unrelated/other.log": "nothing to see\n",
    }
    for rel, body in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
    return root


class BuildCase(unittest.TestCase):
    def test_slice_contains_only_proof_files(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            case = slicer.build_case(tiny_key(), corpus, out, "D01")
            self.assertEqual(sorted(case["files"]),
                             ["apps/api.log", "inhouse/promo.plog"])
            self.assertFalse(os.path.exists(os.path.join(out, "D01", "corpus/unrelated/other.log")),
                             "a file with no proof must not be copied into the slice")

    def test_the_answer_is_not_inside_the_corpus_the_model_is_pointed_at(self):
        """CRITICAL-1: case.json carries the title, the root cause and every proof
        location. It sits at the case ROOT; corpus/ holds only log bytes. Sharing one
        directory is how the captured run 20260730T195412Z read the answer (record 12)
        before it read the log (record 15)."""
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            slicer.build_case(tiny_key(), corpus, out, "D01")
            self.assertTrue(os.path.isfile(os.path.join(out, "D01", "case.json")))
            self.assertFalse(os.path.exists(os.path.join(out, "D01", "corpus", "case.json")),
                             "case.json must never be reachable from the prompt directory")
            listed = os.listdir(os.path.join(out, "D01", "corpus"))
            self.assertEqual(sorted(listed), ["apps", "inhouse"],
                             "the corpus dir must hold nothing but log files")

    def test_whole_files_are_copied_so_line_numbers_still_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            slicer.build_case(tiny_key(), corpus, out, "D01")
            got = open(os.path.join(out, "D01", "corpus/apps/api.log"), encoding="utf-8").read()
            self.assertEqual(got, open(os.path.join(corpus, "apps/api.log"),
                                       encoding="utf-8").read())
            line3 = got.splitlines()[2]
            self.assertIn("NullPointerException", line3,
                          "proof at line 3 must still be at line 3 inside the slice")

    def test_case_json_carries_what_the_judge_needs(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            case = slicer.build_case(tiny_key(), corpus, out, "D01")
            self.assertEqual(case["case_id"], "D01")
            self.assertEqual(case["kind"], "defect_slice")
            self.assertEqual(case["root_cause"], "normalized() returns null")
            self.assertEqual(case["requires"], "single-format read")
            self.assertEqual(len(case["proof_locations"]), 2)
            on_disk = json.load(open(os.path.join(out, "D01", "case.json"), encoding="utf-8"))
            self.assertEqual(on_disk, case)

    def test_missing_source_file_is_a_hard_error(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            os.remove(os.path.join(corpus, "apps/api.log"))
            with self.assertRaises(FileNotFoundError):
                slicer.build_case(tiny_key(), corpus, os.path.join(d, "cases"), "D01")

    def test_proof_line_beyond_end_of_file_is_a_hard_error(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            key = tiny_key()
            key["defects"][0]["proof_locations"][0]["line_end"] = 9999
            with self.assertRaises(ValueError):
                slicer.build_case(key, corpus, os.path.join(d, "cases"), "D01")


class HerringDetection(unittest.TestCase):
    def test_red_herring_detected_from_title(self):
        self.assertTrue(slicer.is_herring(tiny_key()["defects"][1]))
        self.assertFalse(slicer.is_herring(tiny_key()["defects"][0]))

    def test_build_all_skips_herrings(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            ids = slicer.build_all(tiny_key(), corpus, out)
            self.assertEqual(ids, ["D01"])
            self.assertFalse(os.path.exists(os.path.join(out, "D12")),
                             "a red herring is not a defect and gets no slice")


if __name__ == "__main__":
    unittest.main(verbosity=2)
