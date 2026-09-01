#!/usr/bin/env python3
"""What the launcher writes must be what corporate-settings.py proves.

run-bench.sh hand-built its settings JSON. Diffing the proven profile against
20260901T002401Z-v43's sealed qwen-settings-pre.json showed five keys that no
run ever used: context.autoCompactThreshold,
model.chatCompression.maxRecentFilesToRetain, model.skipStartupContext,
tools.core and mcp.excluded. So qwen auto-compacted at its default while our
driver was also clearing, and the 68-tool schema stayed at 114,582 chars —
about 17,000 tokens, 45% of the measured 38,403-token reseed floor.

verify-bundle already proves every profile key is one the target reads. This
test proves the launcher does not quietly ship a subset of it.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.normpath(os.path.join(HERE, ".."))
SHERLOCK = os.path.normpath(os.path.join(MEASURE, ".."))
SETTINGS = os.path.join(MEASURE, "corporate-settings.py")
BENCH = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def keys(row, prefix=""):
    out = set()
    for key, value in row.items():
        out.add(prefix + key)
        if isinstance(value, dict):
            out |= keys(value, prefix + key + ".")
    return out


proc = subprocess.run(
    [sys.executable, SETTINGS, "emit-run", "--window", "262000",
     "--max-tokens", "20000", "--session-token-limit", "230000",
     "--timeout", "900000", "--max-retries", "0",
     "--skill-directory", "/opt/sherlock-arm"],
    capture_output=True, text=True)
check(proc.returncode == 0,
      "emit-run failed: %s" % (proc.stderr[-400:] or proc.stdout[-400:]))

emitted = {}
if proc.returncode == 0:
    try:
        emitted = json.loads(proc.stdout)
    except ValueError as exc:
        FAILED.append("emit-run did not print JSON: %s" % exc)

profile = json.loads(subprocess.run(
    [sys.executable, SETTINGS, "emit", "--window", "262000",
     "--max-tokens", "20000"], capture_output=True, text=True).stdout)

missing = sorted(keys(profile) - keys(emitted))
check(not missing,
      "emit-run drops proven profile keys: %s" % missing)

for key in ("context.autoCompactThreshold", "model.skipStartupContext",
            "model.chatCompression.maxRecentFilesToRetain", "tools.core",
            "mcp.excluded"):
    check(key in keys(emitted), "emit-run is missing %s" % key)

check(emitted.get("skills", {}).get("directories") == ["/opt/sherlock-arm"],
      "emit-run lost the skill directory")

# --max-tokens 0 must omit samplingParams entirely, the same contract as
# --session-token-limit 0 for sessionTokenLimit. Emitting `max_tokens: 0`
# would ask the provider for a zero-token completion — worse than the old
# hand-built launcher's SAMPLING_JSON='' branch it replaces.
zero_out = json.loads(subprocess.run(
    [sys.executable, SETTINGS, "emit-run", "--window", "262000",
     "--max-tokens", "0", "--session-token-limit", "0",
     "--timeout", "900000", "--max-retries", "0",
     "--skill-directory", "/opt/sherlock-arm"],
    capture_output=True, text=True).stdout or "{}")
check("samplingParams" not in zero_out.get("model", {}).get(
          "generationConfig", {}),
      "emit-run --max-tokens 0 still emits samplingParams: %r" % zero_out)
check("samplingParams" in emitted.get("model", {}).get("generationConfig", {}),
      "emit-run drops samplingParams even for a normal --max-tokens")

src = open(BENCH, encoding="utf-8").read()
check("emit-run" in src,
      "run-bench.sh still hand-builds its settings instead of calling "
      "corporate-settings.py emit-run")
check('printf \'{ "model": { "generationConfig"' not in src,
      "run-bench.sh still has a hand-built settings printf")

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
