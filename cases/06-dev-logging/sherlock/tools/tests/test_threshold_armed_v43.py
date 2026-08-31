#!/usr/bin/env python3
"""The measured handoff threshold must reach the driver that acts on it.

The first free v43 acceptance run (20260831T201639Z-v43) wrote a correct
handoff-threshold-proof.txt — auto=222700, TURN_GROWTH_HEADROOM=53369,
handoff_threshold=169331 — and then launched interactive-drive.py with no
`--threshold` at all. That argument defaults to 0, and the driver's branch is
guarded by `ledger_path and threshold and ...`, so 0 disables it silently. The
run crossed 169,331 at call 39 and peaked at 200,161 prompt tokens without ever
clearing; both reseeds it did perform were STAGE boundaries, not threshold ones.

A number that is recorded but not read is a claim, not a mechanism. This test
asserts the wiring, and that the value comes from the proof the launcher itself
wrote rather than from a second constant that could drift away from it.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
BENCH = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
DRIVER = os.path.join(SHERLOCK, "measure", "interactive-drive.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


src = open(BENCH, encoding="utf-8").read()
drv = open(DRIVER, encoding="utf-8").read()

check("--threshold" in drv, "interactive-drive.py has no --threshold argument")
check('--threshold "$HANDOFF_THRESHOLD"' in src,
      "run-bench.sh launches interactive-drive.py without --threshold, so the "
      "driver runs with the threshold disabled (default 0)")

# The value must be DERIVED from the proof, not written out a second time.
check("HANDOFF_THRESHOLD_PROOF" in src and
      re.search(r"HANDOFF_THRESHOLD=.*HANDOFF_THRESHOLD_PROOF", src, re.S),
      "the threshold passed to the driver is not parsed out of the proof the "
      "launcher wrote — a second constant can drift from the recorded one")

# And an unparseable proof must abort, never fall through to a disabled driver.
parts = src.split("HANDOFF_THRESHOLD=")
check(len(parts) > 1 and "refusing to launch" in parts[1][:800],
      "a missing or non-numeric handoff_threshold does not abort the launch")

# The ledger branch really is gated on a truthy threshold, which is why 0 is
# not a harmless default but a silent off switch.
check(re.search(r"ledger_path and threshold and", drv) is not None,
      "interactive-drive.py no longer gates the handoff on `threshold`; this "
      "test's premise needs rechecking")

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
