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


# THE ISOLATION MUST NOT SILENTLY DISABLE THE SKILL. Moving the arm out of $W
# only works if the target is TOLD where it went. qwen 0.22.0 discovers skills
# from `settings.skills.directories` (chunk-6QSA4JHL.js:37943 -> customSkillDirs)
# and registers each entry at the **`user`** level (chunk-T6XLJRQY.js:96211).
# QWEN_SKILL_ROOT feeds hooks and NOTHING else. So two things must hold together
# or the arm vanishes with no error at all: run-bench.sh must write
# skills.directories, and `user` must never be in disabledLevels while it does.
check('skills-json' in src,
      "run-bench.sh never asks corporate-settings.py for the skills block, so "
      "nothing writes skills.directories and the relocated arm is undiscoverable")
check('SKILLS_JSON' in src and '"skills\\": $SKILLS_BLOCK' in src,
      "run-bench.sh does not splice a skills block into the settings it writes")
check('dirname "$ARM_HOME"' in src,
      "run-bench.sh must pass the directory CONTAINING the arm, not ARM_HOME "
      "itself — qwen scans a custom skill dir for skill FOLDERS")

sys.path.insert(0, os.path.join(SHERLOCK, "measure"))
import importlib.util
spec = importlib.util.spec_from_file_location("corporate_settings", SETTINGS)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

block = cs.skills_settings("/opt/sherlock-arm")
check(block.get("directories") == ["/opt/sherlock-arm"],
      "skills_settings drops the directory: %r" % block)
check("user" not in block.get("disabledLevels", []),
      "skills_settings emits a directory while `user` is disabled: %r" % block)

# And the guard itself must bite, not just happen to be satisfied today.
saved = list(cs.DISABLED_SKILL_LEVELS)
try:
    cs.DISABLED_SKILL_LEVELS = saved + ["user"]
    try:
        cs.skills_settings("/opt/sherlock-arm")
        check(False, "skills_settings accepted a directory with `user` disabled "
                     "— the arm would silently never load")
    except ValueError:
        pass
finally:
    cs.DISABLED_SKILL_LEVELS = saved

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
