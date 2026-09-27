#!/usr/bin/env python3
"""Part C (docs/specs/2026-09-27-replay-journalcheck-and-repair-loop-spec.md, vault):
a failed repair gate must start the next repair phase, not end the run. The replay spec
declares 6 repair phases, each 900 s, each gate `on_fail: continue`; run wall 7200 s
(draft 1800 + 6 x 900)."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import citebench  # noqa: E402

BASE = HERE / "base-run-spec-v60.json"


class RepairContinueSpec(unittest.TestCase):
    def setUp(self):
        self.spec = citebench.build_spec(citebench.load_base_spec(BASE), mode="replay", pkg="v62",
                                         run_id="t", stage_dir="/s")

    def test_six_repair_phases_on_fail_continue(self):
        rep = [p for p in self.spec["phases"] if p["id"].startswith("repair")]
        self.assertEqual([p["id"] for p in rep],
                         ["repair"] + ["repair-%d" % i for i in range(2, 7)])
        for p in rep:
            self.assertEqual(p["gate"]["on_fail"], "continue", p["id"])
            self.assertEqual(p["limits"]["max_wall_time_s"], 900, p["id"])

    def test_run_wall_7200(self):
        self.assertEqual(self.spec["limits"]["run_wall_s"], 7200)


if __name__ == "__main__":
    unittest.main()
