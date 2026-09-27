#!/usr/bin/env python3
"""Part B (docs/specs/2026-09-27-replay-journalcheck-and-repair-loop-spec.md):
the gate must call hostgate.py from the separate, versioned harness location
(measure/citebench/harness/v1/), never from the tested skills/vNN tree — those
trees must not be edited in place. --package still points at the staged,
untouched skill package (finalize.py/journalcheck.py/judge.py stay v62/v63
originals). build_spec() must add a "harness" trusted entry, and stage() must
copy harness/v1/ into it.

RED before the fix: build_spec's gate argv still calls
$QWR_TRUSTED_DIR/<pkg>/tools/hostgate.py (no "harness" trusted entry, no
redirect), so a hostgate.py change would require editing the tested v63 tree.
"""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
BASE = HERE / "base-run-spec-v60.json"
sys.path.insert(0, str(BENCH))
import citebench  # noqa: E402


def _gate_argv_strings(spec):
    out = []
    for p in spec["phases"]:
        gate = p.get("gate")
        if gate:
            out.extend(gate["argv"])
    return out


class HostgateHarnessRedirect(unittest.TestCase):
    def test_gate_calls_hostgate_from_the_harness_trusted_dir_not_the_pkg_tree(self):
        base = citebench.load_base_spec(str(BASE))
        spec = citebench.build_spec(base, mode="replay", pkg="v63", run_id="t5",
                                    stage_dir="/tmp/citebench-t5", base_pkg="v60")
        names = {t["name"] for t in spec["task"]["trusted"]}
        self.assertIn("harness", names, "build_spec must stage a separate harness trusted dir")
        for s in _gate_argv_strings(spec):
            if "hostgate.py" not in s:
                continue
            self.assertIn("$QWR_TRUSTED_DIR/harness/hostgate.py", s)
            self.assertNotIn("$QWR_TRUSTED_DIR/v63/tools/hostgate.py", s)
            # --package still resolves to the real, untouched staged skill package
            self.assertIn("--package $QWR_TRUSTED_DIR/v63", s)

    def test_stage_copies_the_harness_dir_into_trusted_harness(self):
        import json
        import shutil
        import tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = d / "run1"
        corpus = HERE / "fixtures" / "v60-smalltest" / "corpus"
        if not corpus.is_dir():
            self.skipTest("no v60-smalltest corpus fixture available")
        import argparse
        args = argparse.Namespace(
            mode="micro", pkg="v63", run_id="t6", out=str(out), base_spec=str(BASE),
            base_pkg="v60", kit=str(BENCH.parents[1]), corpus=str(corpus),
            seed_work=None, tools_dir=None)
        citebench.stage(args)
        self.assertTrue((out / "trusted-harness" / "hostgate.py").is_file())
        spec = json.loads((out / "run-spec.json").read_text())
        names = {t["name"]: t["src"] for t in spec["task"]["trusted"]}
        self.assertEqual(names["harness"], str(out / "trusted-harness"))


if __name__ == "__main__":
    unittest.main()
