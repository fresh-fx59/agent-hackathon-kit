#!/usr/bin/env python3
"""v63 — the citebench replay harness must not be single-shot.

RED against pre-v63 citebench.py: `--mode replay` built a spec with ONLY the
`draft` phase — no repair phase at all, so even a model that correctly calls
`checkpoint.py handoff --done draft` gets no fresh process for repair; the
harness never launches one. This proves that gap, then (module 2) proves the
launcher-side trusted-stage recompute never trusts a forged checkpoint.json.
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
SHERLOCK = BENCH.parents[1]
BASE = HERE / "base-run-spec-v60.json"
sys.path.insert(0, str(BENCH))
import citebench  # noqa: E402


class ReplayRepairPhases(unittest.TestCase):
    def test_replay_spec_declares_repeating_repair_phases(self):
        base = citebench.load_base_spec(str(BASE))
        spec = citebench.build_spec(base, mode="replay", pkg="v63", run_id="t1",
                                    stage_dir="/tmp/citebench-t1", base_pkg="v60")
        ids = [p.get("id") for p in spec["phases"]]
        self.assertEqual(ids.count("draft"), 1, ids)
        repair_ids = [i for i in ids if i and i.startswith("repair")]
        self.assertGreaterEqual(len(repair_ids), 2,
                                "replay must declare at least 2 repair phases, got %r" % ids)

    def test_each_repair_phase_carries_the_trusted_hostgate_gate(self):
        base = citebench.load_base_spec(str(BASE))
        spec = citebench.build_spec(base, mode="replay", pkg="v63", run_id="t2",
                                    stage_dir="/tmp/citebench-t2", base_pkg="v60")
        for p in spec["phases"]:
            if (p.get("id") or "").startswith("repair"):
                gate = p.get("gate")
                self.assertIsNotNone(gate, "repair phase %s has no trusted gate" % p.get("id"))
                self.assertIn("hostgate.py", " ".join(gate["argv"]))


if __name__ == "__main__":
    unittest.main()
