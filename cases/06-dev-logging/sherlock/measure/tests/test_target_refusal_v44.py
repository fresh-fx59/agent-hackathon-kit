#!/usr/bin/env python3
"""A target that refuses every keystroke is not a slow target.

Paid run 20260901T002401Z-v43 did not hang. At 03:45:30 it hit
`--max-session-turns 600` — a PROCESS-lifetime counter that /clear does not
reset — and the CLI refused every later input, including all three nudges:

    The session has reached the maximum number of turns: 600. Please update
    this limit in your setting.json file.

65 occurrences in the transcript tail. The driver called it STAGE_STALLED
after 1,204 s of silence, err.txt was 0 bytes, and the only record of the real
cause was rendered ANSI text inside a 190 MB log.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.normpath(os.path.join(HERE, ".."))
DRIVE = os.path.join(MEASURE, "interactive-drive.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")
SHERLOCK = os.path.normpath(os.path.join(MEASURE, ".."))
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v44", "tools", "checkpoint.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


tmp = tempfile.mkdtemp(prefix="refusal-")
work = os.path.join(tmp, "work")
os.makedirs(work)
with open(os.path.join(work, "checkpoint.json"), "w", encoding="utf-8") as fh:
    json.dump({"stage": "triage", "boundary_seq": 0, "schema": 2,
               "resolved": 0, "total": 5, "unresolved": 5,
               "report_bytes": 0, "report_sections_written": 0,
               "report_sections_required": 5}, fh)
ledger = os.path.join(tmp, "upstream.jsonl")
now_ms = int(time.time() * 1000)
with open(ledger, "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"kind": "call", "ts_ms": now_ms,
                         "usage": {"prompt_tokens": 1000},
                         "messages_count": 2}) + "\n")
events = os.path.join(tmp, "events.jsonl")
env = dict(os.environ)
env.update({"FAKE_WORK": work, "FAKE_MODE": "turns_exhausted",
            "FAKE_CHECKPOINT": CHECKPOINT})
proc = subprocess.run(
    [sys.executable, DRIVE, "--work", work, "--cwd", tmp,
     "--prompt", "поехали", "--transcript", os.path.join(tmp, "t.log"),
     "--events", events, "--ledger", ledger, "--stage-budget-s", "90",
     "--idle-nudge-s", "5", "--max-nudges", "3",
     "--", sys.executable, FAKE],
    capture_output=True, text=True, env=env, timeout=300)

rows = []
if os.path.exists(events):
    rows = [json.loads(l) for l in open(events, encoding="utf-8") if l.strip()]
kinds = [r.get("event") for r in rows]

check("TARGET_REFUSED" in kinds,
      "a target refusing every input was not detected: %r" % kinds)
check(proc.returncode == 10,
      "expected rc 10 for TARGET_REFUSED, got %r" % proc.returncode)
detail = " ".join(r.get("detail", "") for r in rows
                  if r.get("event") == "TARGET_REFUSED")
check("maximum number of turns" in detail,
      "the event does not carry the banner text: %r" % detail)
check("STAGE_STALLED" not in kinds,
      "a refusing target was still misreported as a stall: %r" % kinds)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
