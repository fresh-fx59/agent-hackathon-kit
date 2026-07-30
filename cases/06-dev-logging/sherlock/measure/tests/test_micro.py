#!/usr/bin/env python3
"""Tests for micro.py — hand-written capability corpora.

Two things must hold or a micro-corpus is worse than useless:

1. Its case.json must be shape-identical to a defect slice's, or the runner and the
   scorer need special cases and the "one interface" promise dies.
2. Every declared proof line must ACTUALLY contain the evidence. A micro-corpus with
   a proof pointing at the wrong line would report a coverage failure forever and
   send us chasing a bug that does not exist.
"""
import gzip
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import micro  # noqa: E402

SLICE_KEYS = {"case_id", "kind", "defect_id", "title", "root_cause", "requires",
              "files", "proof_locations"}


class Shape(unittest.TestCase):
    def test_every_capability_builds(self):
        with tempfile.TemporaryDirectory() as d:
            ids = micro.build_all_micro(d)
            self.assertEqual(len(ids), len(micro.MICRO))
            for cid in ids:
                self.assertTrue(os.path.isfile(os.path.join(d, cid, "case.json")))

    def test_case_json_is_shape_compatible_with_a_defect_slice(self):
        with tempfile.TemporaryDirectory() as d:
            case = micro.build_micro(d, "cap-multiline-stitching")
            self.assertTrue(SLICE_KEYS.issubset(set(case)),
                            "missing %r" % (SLICE_KEYS - set(case)))
            self.assertEqual(case["kind"], "capability_micro")
            self.assertIn("capability", case)

    def test_capability_is_a_real_requires_value(self):
        known = {
            "cross-format correlation", "rare-event needle", "single-format read",
            "multiline stitching", "statistical/rate reasoning", "JSON unescaping",
            "gz decompression", "single-format read of an unknown format",
            "single-format read (Russian)",
        }
        for cid, spec in micro.MICRO.items():
            self.assertIn(spec["capability"], known,
                          "%s uses a capability not in the answer key taxonomy" % cid)


class ProofsAreReal(unittest.TestCase):
    def test_every_proof_line_contains_its_expected_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            for cid in micro.build_all_micro(d):
                case = json.load(open(os.path.join(d, cid, "case.json"), encoding="utf-8"))
                for pr in case["proof_locations"]:
                    path = os.path.join(d, cid, pr["file"])
                    if path.endswith(".gz"):
                        with gzip.open(path, "rt", encoding="utf-8") as fh:
                            lines = fh.read().splitlines()
                    else:
                        lines = open(path, encoding="utf-8").read().splitlines()
                    self.assertLessEqual(pr["line_end"], len(lines),
                                         "%s: proof past EOF" % cid)
                    window = "\n".join(lines[pr["line_start"] - 1:pr["line_end"]])
                    self.assertIn(pr["expect"], window,
                                  "%s: %s:%d-%d does not contain %r"
                                  % (cid, pr["file"], pr["line_start"],
                                     pr["line_end"], pr["expect"]))

    def test_gz_capability_really_ships_a_gzip_file(self):
        with tempfile.TemporaryDirectory() as d:
            case = micro.build_micro(d, "cap-gz-decompression")
            gz = [f for f in case["files"] if f.endswith(".gz")]
            self.assertEqual(len(gz), 1)
            with gzip.open(os.path.join(d, case["case_id"], gz[0]), "rt",
                           encoding="utf-8") as fh:
                self.assertIn("cbr.ru", fh.read())

    def test_ru_capability_uses_no_english_severity_words(self):
        # The point of this corpus is that grepping ERROR/FATAL finds nothing.
        with tempfile.TemporaryDirectory() as d:
            case = micro.build_micro(d, "cap-ru-severity")
            body = open(os.path.join(d, case["case_id"], case["files"][0]),
                        encoding="utf-8").read()
            for word in ("ERROR", "FATAL", "WARN", "CRITICAL"):
                self.assertNotIn(word, body,
                                 "%s would be findable by an English severity grep" % word)


class MeasureCompatibility(unittest.TestCase):
    def test_verdict_runs_on_a_micro_case(self):
        import measure
        with tempfile.TemporaryDirectory() as d:
            case = micro.build_micro(d, "cap-rare-event-needle")
            stream = os.path.join(d, "s.jsonl")
            with open(stream, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "read_file",
                     "input": {"file_path": "/x/" + case["files"][0],
                               "offset": 0, "limit": 500}}]}}) + "\n")
            v = measure.verdict(case, stream, "x" * 3000, judge_found=False)
            self.assertIn(v["diagnosis"], {"reasoning", "coverage", "inconclusive"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
