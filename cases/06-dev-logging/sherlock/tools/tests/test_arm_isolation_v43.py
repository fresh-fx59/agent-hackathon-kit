#!/usr/bin/env python3
"""The arm must not live inside the model's writable root.

Two of five v42 runs ended `arm_intact: false`: the model edited stopcheck.py on
20260828T170343Z-v42 and appended 15 labels to report-contract.corporate.json on
20260830T190815Z-v42. run-bench.sh created that opportunity by copying the arm to
$W/.qwen/skills/log-rca — $W being the model's own cwd. Corporate never had this
hole: writes outside the launch directory are a hard block in the customer's
harness (operator, 2026-08-31). This test asserts our lane matches.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
BENCH = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
SETTINGS = os.path.join(SHERLOCK, "measure", "corporate-settings.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


src = open(BENCH, encoding="utf-8").read()
check('cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca"' not in src,
      "run-bench.sh still copies the arm into the model's writable root $W")
check(re.search(r'ARM_HOME=', src),
      "run-bench.sh defines no ARM_HOME")
for var in ("ARM_SNAPSHOT", "ARM_TOOLS", "QWEN_SKILL_ROOT"):
    for line in src.splitlines():
        if line.strip().startswith(var + "=") or ("export %s=" % var) in line:
            check('"$W"' not in line and "$W/" not in line,
                  "%s is still derived from $W: %s" % (var, line.strip()))

cfg = open(SETTINGS, encoding="utf-8").read()
m = re.search(r"DISABLED_SKILL_LEVELS\s*=\s*\[([^\]]*)\]", cfg)
check(m is not None, "DISABLED_SKILL_LEVELS not found")
if m:
    levels = [t.strip().strip('"\'') for t in m.group(1).split(",") if t.strip()]
    check("project" in levels,
          "project skill level is still enabled — that is where the writable "
          "copy lived: %r" % levels)
    check("user" not in levels,
          "user skill level is still disabled, but the arm now lives there: %r"
          % levels)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
