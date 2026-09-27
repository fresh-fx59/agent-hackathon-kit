#!/usr/bin/env python3
"""Harness hostgate (measure/citebench/harness/v1/hostgate.py):
- a stage printing 50 kB keeps every byte in its file, digest matches (Part B);
- a receipt printed in a retried attempt's stream.a1.jsonl is seen (spec item 5);
- RUN/control/trusted/seed/manifest.json entries reach journalcheck as pinned
  --prior-stream/--prior-sha256 pairs (Part A);
- `blocking` carries finalize's parsed_blocking total, so qwen-run's no_progress rule
  sees report defects, not only journal defects (Part C input).
Fake finalize/judge; real v62 journalcheck/checkpoint + harness journalcheck. Offline."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[2]
HG = HERE.parent / "harness" / "v1" / "hostgate.py"
sys.path.insert(0, str(HERE))
from test_journalcheck_prior_stream import R1, R2, S1, S2, journal, stream  # noqa: E402

BIG = 50 * 1024
FINALIZE = ("import json,sys\nsys.stdout.write('F' * %d + '\\n')\n"
            "print(json.dumps({'verdict': 'blocking', 'gates': {'citecheck': {'parsed_blocking': 4},"
            " 'reportcheck': {'parsed_blocking': 1}}}))\nsys.exit(2)\n" % BIG)


class HostgateFullOutput(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="hgfull-"))
        self.pkg = self.d / "trusted" / "v62"
        shutil.copytree(SHERLOCK / "skills" / "v62" / "tools", self.pkg / "tools")
        (self.pkg / "tools" / "finalize.py").write_text(FINALIZE)
        self.run = self.d / "run"
        self.work = self.run / "work" / "work"
        (self.run / "out" / "draft").mkdir(parents=True)
        (self.run / "out" / "repair").mkdir(parents=True)

    def gate(self):
        r = subprocess.run([sys.executable, str(HG), "--work", str(self.work), "--corpus",
                            str(self.d), "--package", str(self.pkg), "--run-dir", str(self.run),
                            "--phase", "repair"], capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout.splitlines()[-1])

    def seed(self, sha=None):
        sd = self.run / "control" / "trusted" / "seed"
        sd.mkdir(parents=True)
        stream(sd / "stream.jsonl", [(R1, S1, 1)])
        real = hashlib.sha256((sd / "stream.jsonl").read_bytes()).hexdigest()
        (sd / "manifest.json").write_text(json.dumps([{"file": "stream.jsonl", "sha256": sha or real}]))
        return real

    def test_50kb_stage_output_kept_whole(self):
        journal(self.work, [(R2, S2, 1)])
        stream(self.run / "out" / "draft" / "stream.jsonl", [(R2, S2, 1)])
        rc, out = self.gate()
        st = out["stages"]["finalize"]
        data = (self.run / st["stdout_file"]).read_bytes()
        self.assertGreater(len(data), BIG)
        self.assertEqual(st["stdout_bytes"], len(data))
        self.assertEqual(hashlib.sha256(data).hexdigest(), st["stdout_sha256"])
        self.assertEqual(out["blocking"]["finalize"], 5)
        self.assertEqual(rc, 1)

    def test_retried_attempt_stream_is_globbed(self):
        journal(self.work, [(R2, S2, 1)])
        stream(self.run / "out" / "draft" / "stream.a1.jsonl", [(R2, S2, 1)])
        (self.run / "out" / "draft" / "stream.jsonl").write_text("")
        _, out = self.gate()
        self.assertEqual(out["stages"]["journalcheck"]["result"]["violations"], [])

    def test_seed_manifest_pins_prior_stream(self):
        real = self.seed()
        journal(self.work, [(R1, S1, 1), (R2, S2, 2)])
        stream(self.run / "out" / "repair" / "stream.jsonl", [(R2, S2, 2)])
        _, out = self.gate()
        res = out["stages"]["journalcheck"]["result"]
        self.assertEqual(res["violations"], [])
        self.assertEqual(res["prior_streams"][0]["sha256"], real)
        self.assertEqual(out["blocking"]["journalcheck"], 0)

    def test_seed_manifest_digest_mismatch_blocks(self):
        self.seed(sha="0" * 64)
        journal(self.work, [(R1, S1, 1), (R2, S2, 2)])
        stream(self.run / "out" / "repair" / "stream.jsonl", [(R2, S2, 2)])
        rc, out = self.gate()
        jc = out["stages"]["journalcheck"]
        self.assertEqual(jc["exit"], 2)
        self.assertEqual(jc["result"]["error"], "prior_digest_mismatch")
        self.assertGreaterEqual(out["blocking"]["journalcheck"], 1)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
