#!/usr/bin/env python3
"""A /clear that did not clear must end the run, loudly.

Run 20260831T214240Z-v43 typed /clear at 124 boundaries. The request bodies
show `messages` climbing 121 -> 633 and only 5 sessions ever starting fresh;
one context held 52 accumulated reseed messages. The driver's CLEAR_REFUSED
needle never matched — the failure was SILENT, not refused — so the run burned
2h19m measuring nothing. From 22:13:14 onward a clear reclaimed 0 tokens.
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
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v43", "tools", "checkpoint.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def run_case(mode, ledger_rows):
    """Drive the stand-in with a ledger we control, and return (rc, events)."""
    tmp = tempfile.mkdtemp(prefix="clearverify-%s-" % mode)
    work = os.path.join(tmp, "work")
    os.makedirs(work)
    with open(os.path.join(work, "checkpoint.json"), "w", encoding="utf-8") as fh:
        json.dump({"stage": "triage", "boundary_seq": 0, "schema": 2,
                   "resolved": 0, "total": 10, "unresolved": 10,
                   "report_bytes": 0, "report_sections_written": 0,
                   "report_sections_required": 5}, fh)
    # checkpoint.py handoff for stage=triage requires a worklist on disk —
    # without it the fake's real (non-mode-specific) handoff call fails with
    # "no worklist*.tsv in checkpoint" and the stage never advances, so the
    # driver never reaches /clear at all. Mirrors
    # measure/tests/test_interactive_drive.py's HEADER/ROW fixture.
    with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write(u"# id\tвердикт\n")
        for i in range(1, 4):
            fh.write(u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n" % i)
    ledger = os.path.join(tmp, "upstream.jsonl")
    with open(ledger, "w", encoding="utf-8") as fh:
        for row in ledger_rows:
            fh.write(json.dumps(row) + "\n")
    events = os.path.join(tmp, "events.jsonl")
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_MODE": mode,
                "FAKE_CHECKPOINT": CHECKPOINT})
    proc = subprocess.run(
        [sys.executable, DRIVE, "--work", work, "--cwd", tmp,
         "--prompt", "поехали", "--transcript", os.path.join(tmp, "t.log"),
         "--events", events, "--ledger", ledger, "--stage-budget-s", "40",
         "--threshold", "100", "--idle-nudge-s", "0",
         "--", sys.executable, FAKE],
        capture_output=True, text=True, env=env, timeout=180)
    rows = []
    if os.path.exists(events):
        rows = [json.loads(l) for l in open(events, encoding="utf-8") if l.strip()]
    return proc.returncode, rows, proc.stdout + proc.stderr


now_ms = int(time.time() * 1000)
# A ledger that is over threshold and whose post-reseed call is NOT fresh:
# messages_count 131, exactly the shape run C produced.
stale = [{"kind": "call", "ts_ms": now_ms + 10 * 1000 * i,
          "usage": {"prompt_tokens": 121000 + i}, "messages_count": 131}
         for i in range(1, 400)]
rc, events, output = run_case("clear_noop", stale)

kinds = [e.get("event") for e in events]
check("CLEAR_NOT_EFFECTIVE" in kinds,
      "a /clear that did not clear produced no CLEAR_NOT_EFFECTIVE event: %r"
      % kinds)
check(rc == 8, "expected rc 8 for an ineffective clear, got %r (%s)"
      % (rc, output[-400:]))
detail = " ".join(e.get("detail", "") for e in events
                  if e.get("event") == "CLEAR_NOT_EFFECTIVE")
check("131" in detail,
      "the event does not name the messages_count it saw: %r" % detail)


# Reproduce 20260902T014942Z-v44's shape: the first completed call after the
# reseed is the tail of the pre-clear conversation still draining (high
# messages_count, arriving within a couple of seconds), and the REAL fresh
# session's 2-message call shows up a little later in the same bounded
# window. The old first-row read treated this as CLEAR_NOT_EFFECTIVE and
# killed an otherwise healthy run at rc 8; the window-based check must
# instead find the later 2-message row and verify.
race = []
for i in range(1, 400):
    # In-flight tail of the pre-clear conversation, arriving first in each
    # slot — this is the row the OLD first-row check would have (wrongly)
    # trusted.
    race.append({"kind": "call", "ts_ms": now_ms + i * 10 * 1000,
                 "usage": {"prompt_tokens": 65365}, "messages_count": 29})
    # The real fresh session's first call, 2 seconds later in the same slot —
    # matches the actual v44 timing (reseed 01:54:58, race row 01:55:00).
    race.append({"kind": "call", "ts_ms": now_ms + i * 10 * 1000 + 2 * 1000,
                 "usage": {"prompt_tokens": 412}, "messages_count": 2})
rc2, events2, output2 = run_case("clear_verified", race)

kinds2 = [e.get("event") for e in events2]
check("clear_verified" in kinds2,
      "a /clear whose window contains a later 2-message call was not "
      "verified: %r (rc=%r, tail=%s)" % (kinds2, rc2, output2[-400:]))
check("CLEAR_NOT_EFFECTIVE" not in kinds2,
      "the in-flight tail of the pre-clear conversation falsely triggered "
      "CLEAR_NOT_EFFECTIVE: %r" % kinds2)
check(rc2 != 8, "the race case must not return rc 8, got %r (%s)"
      % (rc2, output2[-400:]))

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
