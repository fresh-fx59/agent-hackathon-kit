#!/usr/bin/env python3
"""REGRESSION LOCK — a target blocked inside a tool call must be named as
that, not reported as a target that stopped.

WHAT THE WRONG WORDS COST. Free acceptance run 20260902T193433Z-v44 ended
`STAGE_STALLED — no upstream call for 603 s at stage repair after 3
nudge(s) — the target stopped and will not restart`. The screen at that
moment said:

    x Shell {"command":"python3 /opt/sherlock-arm/log-rca/tools/stopcheck.py
      --work .../work --report .../work/report.md","timeout":600000}
    ⠋ Communing with the machine spirit... (6m 21s · ↑ 11k tokens · esc to cancel)

The target had not stopped. It was BUSY, waiting on the arm's own gate tool,
which was blocking forever on stdin. The terminal was the right call; the
diagnosis pointed at the wrong component, and finding the real culprit took
a hand read of a 190 MB transcript.

THE DISTINCTION IS FREE TO MAKE. The driver already has both signals: the
ledger says the target has not talked to the provider (quiet), and the
footer says whether the screen is busy. Quiet AND idle means stopped. Quiet
AND busy means blocked in a tool call — a different component, a different
fix, and worth its own terminal (rc 12) rather than sharing rc 7.

IT ALSO STOPS A POINTLESS NUDGE. Typing «продолжай» into a busy qwen-code
0.22.0 does not reach the model — it queues (the whole reason v44 waits for
idle everywhere else) — so the three nudges that preceded that verdict could
never have helped, and the run spent 603 s proving it.

`FAKE_MODE=tool_hang` prints the shell-tool banner and then repaints the
busy footer forever, swallowing typed input, with a ledger that never grows.

RUN THIS ON fe75b7d (before the fix): the driver nudges the busy target,
then reports rc 7 STAGE_STALLED and «the target stopped», naming nothing.
RUN THIS ON HEAD: rc 12, a TOOL_CALL_BLOCKED event that quotes the command
on screen, and no nudge typed into a busy screen.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.normpath(os.path.join(HERE, ".."))
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")
SHERLOCK = os.path.normpath(os.path.join(MEASURE, ".."))
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v44", "tools", "checkpoint.py")
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


root = tempfile.mkdtemp(prefix="toolblock-")
work = os.path.join(root, "work")
os.makedirs(work)
open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
    HEADER + "".join(ROW % i for i in range(1, 4)))
subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
               capture_output=True)
# A ledger with one completed call that then never grows: the target talked
# to the provider once and has been silent since, which is the real run's
# shape (its last three rows were healthy `status 200 tool_calls`).
led = os.path.join(root, "led.jsonl")
open(led, "w").write(json.dumps(
    {"kind": "call", "ts_ms": int(time.time() * 1000), "status": 200,
     "finish_reason": "tool_calls", "usage": {"prompt_tokens": 40000},
     "messages_count": 12, "session_id": "s-1"}) + "\n")

env = dict(os.environ)
env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
            "FAKE_MODE": "tool_hang", "PYTHONUNBUFFERED": "1"})
transcript = os.path.join(root, "transcript.log")
events = os.path.join(root, "events.jsonl")
argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
        "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
        "--events", events, "--stage-budget-s", "180",
        "--settle-s", "0.6", "--handoff-grace-s", "0",
        "--ledger", led, "--idle-nudge-s", "5", "--max-nudges", "2",
        "--", sys.executable, "-u", FAKE]
try:
    p = subprocess.run(argv, capture_output=True, text=True, env=env,
                       timeout=240)
    rc, err = p.returncode, p.stderr[-500:]
except subprocess.TimeoutExpired:
    rc, err = None, "TIMEOUT"

ev = []
if os.path.exists(events):
    for line in open(events, "rb"):
        try:
            ev.append(json.loads(line.decode("utf-8", "replace")))
        except ValueError:
            pass
names = [e.get("event") for e in ev]
blocked = [e for e in ev if e.get("event") == "TOOL_CALL_BLOCKED"]
detail = "rc=%r err=%s events=%s" % (rc, err, names)

check("a target blocked in a tool call gets its own terminal (rc 12), not "
      "the rc 7 that means the target stopped",
      rc == 12, detail)

check("the event says TOOL_CALL_BLOCKED", bool(blocked), detail)

check("and it QUOTES the command on screen, so the artifact names the "
      "culprit without a transcript read",
      bool(blocked) and "stopcheck.py" in (blocked[0].get("detail") or ""),
      blocked[0].get("detail") if blocked else detail)

check("no nudge was typed into a busy screen — typing into a busy "
      "qwen-code queues, so a nudge there can never help",
      "nudged" not in names, detail)

check("it did not reach a terminal that means something else",
      rc not in (7, 8, 9, 10, 11), detail)

if FAILED:
    print("FAIL test_tool_blocked_v44: " + "; ".join(FAILED))
    sys.exit(1)
print("OK")
