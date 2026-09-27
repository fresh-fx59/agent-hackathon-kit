#!/usr/bin/env python3
"""Part A (vault spec 2026-09-27-replay-journalcheck-and-repair-loop, Decision A2):
the harness journalcheck accepts a pinned prior stream (the seed run's investigate
transcript) for the journal PREFIX only.

Fixture: journal row 1's receipt is printed only in a prior stream (the seed run);
row 2's receipt is printed in the live stream. Both streams reuse tool_use id "t1"
on purpose (ids are per-session; prior and live must not be merged into one map).
(a) no --prior-stream -> receipt_unprinted (reproduces replay-v62-r3)
(b) pinned prior -> clean, prior_streams recorded
(c) wrong digest -> exit 2, prior_digest_mismatch
(d) a later row reusing the prior receipt -> receipt_prior_out_of_prefix
(e) a fabricated row whose receipt is printed nowhere -> still receipt_unprinted
(f) the prior row deleted from the journal -> receipt_unjournaled
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[2]
JC = HERE.parent / "harness" / "v1" / "journalcheck.py"
TOOLS = SHERLOCK / "skills" / "v62" / "tools"
sys.path.insert(0, str(TOOLS))
import checkpoint as CK  # noqa: E402

R1, R2, R3 = "1" * 32, "2" * 32, "3" * 32
S1, S2, S3 = "a" * 64, "b" * 64, "c" * 64


def stream(path, receipts):
    with open(path, "w") as fh:
        for rid, sha, seq in receipts:
            fh.write(json.dumps({"message": {"content": [{"type": "tool_use", "id": "t1",
                     "name": "run_shell_command",
                     "input": {"command": "python3 tools/checkpoint.py handoff --done x"}}]}}) + "\n")
            fh.write(json.dumps({"message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                     "content": "RECEIPT id=%s sha256=%s seq=%d" % (rid, sha, seq)}]}}) + "\n")


def journal(work, rows):
    os.makedirs(work, exist_ok=True)
    prev, lines = None, []
    for rid, sha, seq in rows:
        e = {"boundary_seq": seq, "handoff_receipt_id": rid, "handoff_sha256": sha,
             "worklist_authority": {}, "prev_sha256": CK._row_sha(prev) if prev else None}
        prev = json.dumps(e, sort_keys=True)
        lines.append(prev)
    Path(work, "checkpoint.jsonl").write_text("".join(l + "\n" for l in lines))
    Path(work, "checkpoint.json").write_text(json.dumps({"boundary_seq": rows[-1][2] if rows else 0}))


class PriorStream(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="jcprior-"))
        self.work = self.d / "work"
        self.prior = self.d / "prior.jsonl"
        self.live = self.d / "live.jsonl"
        stream(self.prior, [(R1, S1, 1)])
        stream(self.live, [(R2, S2, 2)])
        self.sha = hashlib.sha256(self.prior.read_bytes()).hexdigest()

    def jc(self, prior=True, sha=None):
        argv = [sys.executable, str(JC), "--tools", str(TOOLS), "--work", str(self.work),
                "--json", "--stream", str(self.live)]
        if prior:
            argv += ["--prior-stream", str(self.prior), "--prior-sha256", sha or self.sha]
        r = subprocess.run(argv, capture_output=True, text=True)
        try:
            return r.returncode, json.loads(r.stdout)
        except ValueError:
            self.fail("no JSON: rc=%s out=%r err=%r" % (r.returncode, r.stdout, r.stderr))

    def codes(self, res):
        return [v["code"] for v in res["violations"]]

    def test_a_without_prior_is_unprinted(self):
        journal(self.work, [(R1, S1, 1), (R2, S2, 2)])
        rc, res = self.jc(prior=False)
        self.assertEqual(rc, 1)
        self.assertEqual(self.codes(res), ["receipt_unprinted"])

    def test_b_pinned_prior_is_clean(self):
        journal(self.work, [(R1, S1, 1), (R2, S2, 2)])
        rc, res = self.jc()
        self.assertEqual((rc, res["violations"]), (0, []))
        self.assertEqual(res["prior_streams"],
                         [{"path": os.path.abspath(self.prior), "sha256": self.sha, "receipts": 1}])

    def test_c_wrong_digest_exit_2(self):
        journal(self.work, [(R1, S1, 1), (R2, S2, 2)])
        rc, res = self.jc(sha="0" * 64)
        self.assertEqual(rc, 2)
        self.assertEqual(res["error"], "prior_digest_mismatch")

    def test_d_later_row_reusing_prior_receipt_is_out_of_prefix(self):
        journal(self.work, [(R1, S1, 1), (R2, S2, 2), (R1, S1, 3)])
        rc, res = self.jc()
        self.assertEqual(rc, 1)
        self.assertIn("receipt_prior_out_of_prefix", self.codes(res))

    def test_e_fabricated_row_still_unprinted(self):
        journal(self.work, [(R1, S1, 1), (R2, S2, 2), (R3, S3, 3)])
        rc, res = self.jc()
        self.assertEqual(rc, 1)
        self.assertEqual(self.codes(res), ["receipt_unprinted"])
        self.assertIn(R3, res["violations"][0]["detail"])

    def test_f_prior_row_deleted_is_unjournaled(self):
        journal(self.work, [(R2, S2, 2)])
        rc, res = self.jc()
        self.assertEqual(rc, 1)
        self.assertIn("receipt_unjournaled", self.codes(res))
        self.assertTrue(any(R1 in v["detail"] for v in res["violations"]
                            if v["code"] == "receipt_unjournaled"))


if __name__ == "__main__":
    unittest.main()
