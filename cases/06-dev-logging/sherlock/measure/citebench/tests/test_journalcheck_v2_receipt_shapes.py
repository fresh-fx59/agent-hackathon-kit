#!/usr/bin/env python3
"""Harness v2 journalcheck accepts BOTH printed-receipt shapes.

Root cause (vault: references/sherlock-runs/2026-11-16-v68-journal-receipt-diagnosis):
the package's printed_receipts() returns (sha, seq) up to v64 and
{"receipt": (sha, seq), "meta": {...}} since v65. Harness v1 tuple-unpacked the dict
KEYS, so every printed receipt of a v65+ package was a `receipt_mismatch`.

(a) unit: receipt_pair() on tuple, list, dict, meta-only dict, junk
(b) unit: check() with a fake package returning each shape -> 0 blocking, in the live
    loop and in the prior loop
(c) regression: replay of the captured v68 small run `...20261116f` streams -> exactly 1
    blocking (`receipt_unprinted`, seq 2: the model piped handoff through `head -c 900`)
    and the v64 full run `sherlock-v64-full-dsv4flash-20260928b` -> 0 blocking.
    Fixtures hold only the handoff tool_use/tool_result events of each stream.
(d) v1 on the v68 fixture still shows the bug (3 receipt_mismatch), pinning the repro.
"""
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[2]
FIX = HERE / "fixtures" / "v68f-journal"


def load(version):
    spec = importlib.util.spec_from_file_location(
        "jc_harness_" + version, HERE.parent / "harness" / version / "journalcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(version, tools, case, streams):
    mod = load(version)
    argv = ["--tools", str(tools), "--work", str(FIX / case), "--json"]
    for s in streams:
        argv += ["--stream", str(FIX / case / (s + ".jsonl"))]
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main(argv)
    return json.loads(buf.getvalue())


class FakePkg:
    """Stands in for the package journalcheck; shape = what printed_receipts returns."""
    def __init__(self, shape, rid, sha, seq, rows):
        self.shape, self.rid, self.sha, self.seq, self._rows = shape, rid, sha, seq, rows

        class CK:
            @staticmethod
            def verify_journal(_work):
                return []
        self.CK = CK

    def read_stream(self, paths):
        return [], {}

    def printed_receipts(self, uses, results):
        pair = (self.sha, self.seq)
        entry = pair if self.shape == "tuple" else {"receipt": pair, "meta": {"x": 1}}
        return {self.rid: entry}

    def journal_rows(self, work):
        return self._rows

    def control_writes(self, uses):
        return []


class ReceiptPair(unittest.TestCase):
    def test_shapes(self):
        rp = load("v2").receipt_pair
        self.assertEqual(rp(("a", 1)), ("a", 1))
        self.assertEqual(rp(["a", 1]), ("a", 1))
        self.assertEqual(rp({"receipt": ("a", 1), "meta": {}}), ("a", 1))
        self.assertIsNone(rp({"meta": {"report_sha256": "x"}}))
        self.assertIsNone(rp({}))
        self.assertIsNone(rp(None))


class CheckBothShapes(unittest.TestCase):
    RID, SHA = "1" * 32, "a" * 64

    def rows(self, sha=None):
        return [{"handoff_receipt_id": self.RID, "handoff_sha256": sha or self.SHA, "boundary_seq": 1}]

    def test_live_and_prior_loops_accept_both(self):
        mod = load("v2")
        tmp = HERE / "fixtures" / "v68f-journal" / "v68f" / "investigate.jsonl"
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        for shape in ("tuple", "dict"):
            pkg = FakePkg(shape, self.RID, self.SHA, 1, self.rows())
            live = mod.check(pkg, "w", [], [])
            self.assertEqual(live["blocking"], 0, (shape, live["violations"]))
            prior = mod.check(pkg, "w", [], [(str(tmp), digest)])
            self.assertEqual(prior["blocking"], 0, (shape, prior["violations"]))
            self.assertEqual(prior["prior_streams"][0]["receipts"], 1)

    def test_real_mismatch_still_detected(self):
        mod = load("v2")
        for shape in ("tuple", "dict"):
            pkg = FakePkg(shape, self.RID, "b" * 64, 1, self.rows())
            res = mod.check(pkg, "w", [], [])
            self.assertEqual([v["code"] for v in res["violations"]], ["receipt_mismatch"], shape)


class Regression(unittest.TestCase):
    def test_v68_small_f_only_unprinted_seq2(self):
        res = run("v2", SHERLOCK / "skills" / "v68" / "tools", "v68f", ["investigate", "draft", "repair"])
        self.assertEqual(res["rows"], 4)
        self.assertEqual([v["code"] for v in res["violations"]], ["receipt_unprinted"])
        self.assertIn("boundary_seq=2", res["violations"][0]["detail"])
        self.assertEqual(res["blocking"], 1)

    def test_v64_full_clean(self):
        res = run("v2", SHERLOCK / "skills" / "v64" / "tools", "v64full", ["investigate", "draft"])
        self.assertEqual(res["rows"], 2)
        self.assertEqual(res["blocking"], 0, res["violations"])

    def test_v1_bug_reproduced(self):
        res = run("v1", SHERLOCK / "skills" / "v68" / "tools", "v68f", ["investigate", "draft", "repair"])
        self.assertEqual(sorted(v["code"] for v in res["violations"]),
                         ["receipt_mismatch"] * 3 + ["receipt_unprinted"])


if __name__ == "__main__":
    unittest.main()
