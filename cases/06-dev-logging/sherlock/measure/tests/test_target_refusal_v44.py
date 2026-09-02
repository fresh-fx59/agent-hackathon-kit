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

FIX ROUND 1 added a second scenario: this repo IS the corpus for the case the
banner came from, so an honest RCA agent narrating its own investigation, or
`command grep "maximum number of turns" ...`-ing a transcript, could put that
bare fragment on screen. `test_a_bare_fragment_is_not_a_refusal` proves that
does NOT trip TARGET_REFUSED — the fix requires the CLI's own distinguishing
tail ("this limit in your setting.json") to ALSO be on screen before
the latch fires.
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


def run_drive(mode, idle_nudge_s=5, stage_budget_s=90, timeout=300):
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
    env.update({"FAKE_WORK": work, "FAKE_MODE": mode,
                "FAKE_CHECKPOINT": CHECKPOINT})
    proc = subprocess.run(
        [sys.executable, DRIVE, "--work", work, "--cwd", tmp,
         "--prompt", "поехали", "--transcript", os.path.join(tmp, "t.log"),
         "--events", events, "--ledger", ledger,
         "--stage-budget-s", str(stage_budget_s),
         "--idle-nudge-s", str(idle_nudge_s), "--max-nudges", "3",
         "--", sys.executable, FAKE],
        capture_output=True, text=True, env=env, timeout=timeout)
    rows = []
    if os.path.exists(events):
        rows = [json.loads(l) for l in open(events, encoding="utf-8")
                if l.strip()]
    return proc, rows


# --- Scenario 1: the real banner, the CLI genuinely refusing everything ---
proc, rows = run_drive("turns_exhausted")
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

# --- Scenario 2: the false positive this fix closes ---
# The target only ever echoes the bare fragment, exactly as an honest RCA
# agent's own narration or `command grep "maximum number of turns" ...`
# could do while investigating THIS case's own corpus. It never advances any
# stage and the ledger stays quiet, so the run's honest terminal is
# STAGE_STALLED — and it must reach that, not TARGET_REFUSED, or the latch is
# still killing runs it shouldn't.
proc2, rows2 = run_drive("grep_echo_only", idle_nudge_s=3, stage_budget_s=30)
kinds2 = [r.get("event") for r in rows2]

check("TARGET_REFUSED" not in kinds2,
      "a bare fragment an honest agent could echo was misread as a refusal: "
      "%r" % kinds2)
check(proc2.returncode == 7,
      "expected rc 7 (STAGE_STALLED, the honest terminal for a target that "
      "never advances) but got %r with events %r"
      % (proc2.returncode, kinds2))
check("STAGE_STALLED" in kinds2,
      "the false-positive run did not reach its normal stall terminal: %r"
      % kinds2)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
