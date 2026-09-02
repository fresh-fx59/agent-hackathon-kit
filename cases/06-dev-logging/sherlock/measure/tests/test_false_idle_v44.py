#!/usr/bin/env python3
"""REGRESSION LOCK — a busy target whose spinner line stops repainting, while
the queued-input indicator keeps repainting, must still read as BUSY.

Run 20260902T053801Z-v44 failed at CLEAR_NOT_EFFECTIVE with NO
`clear_typed_while_busy` note and NO ESC note in the events log: `wait_before`
decided the target was idle and typed straight through, even though the
terminal was showing an in-flight spinner (`esc to cancel`) and a queued
`/clear`/`/sherlock` (`queued` appears 261 times in that transcript's tail).

ROOT CAUSE (confirmed by reading `Session.wait_idle`/`BUSY_MARKER` on
df994a0): the detector watched ONLY the literal `esc to cancel` — a model
that is "thinking" (Compiling the 1s and 0s...) does not keep re-printing
that exact spinner line every tick (the real transcript's own gap between
consecutive `esc to cancel` repaints ran as high as 1.7 MB — a long silent
stretch on that one signal), so `RecencyWatch.idle_for()` ages past
`clear_idle_settle_s` and `wait_idle` returns True — false idle — while the
SAME transcript is still clearly busy by its OTHER signal, `queued`, which
the pre-fix code explicitly rejected as a needle (conflating it with the
always-present `Ctrl+Q to queue` hint).

THIS FIXTURE reproduces that shape deterministically: `sparse_busy_after_
boundary` prints the spinner (`esc to cancel`) exactly ONCE per busy window,
then repaints only the queued-input indicator (`⏳ N queued`) every
`FAKE_REPAINT_S` seconds (0.3s by default) for the rest of it — genuinely
busy (every typed line is swallowed, same as `busy_after_boundary`), but the
`esc to cancel` needle alone goes stale well under a second in.

RUN THIS ON HEAD (with the fix, `BUSY_MARKERS = (BUSY_MARKER,
QUEUED_MARKER)`): the driver's bounded `wait_idle` correctly reads `queued`
and waits the real busy window out — `/clear` never gets typed into it, and
the run finishes cleanly.
RUN THIS ON df994a0 (before this fix, in a detached worktree): `wait_idle`
goes false-idle inside its first bounded wait (its needle only ever sees the
spinner's one-time print), types `/clear` and the `/sherlock` retype
straight into the swallow window, loses both, and the run reaches
`STAGE_TIMEOUT` — it never even gets far enough to see `CLEAR_NOT_EFFECTIVE`,
because there is no ledger wired into this fixture to judge that against;
the STAGE_TIMEOUT is the observable proof of the same false-idle root cause.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.normpath(os.path.join(HERE, ".."))
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")
SHERLOCK = os.path.normpath(os.path.join(MEASURE, ".."))
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v44", "tools", "checkpoint.py")
CLEAR_BANNER = "Starting a new session, resetting chat, and clearing terminal."
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(busy_s, repaint_s, stage_budget_s=10, clear_idle_wait_s=8.0,
        clear_idle_settle_s=1.0):
    """Drive the sparsely-repainting busy stand-in; return (rc, events, text)."""
    root = tempfile.mkdtemp(prefix="falseidle-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True)
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
                "FAKE_MODE": "sparse_busy_after_boundary",
                "FAKE_BUSY_S": str(busy_s), "FAKE_REPAINT_S": str(repaint_s),
                "PYTHONUNBUFFERED": "1"})
    transcript = os.path.join(root, "transcript.log")
    events_path = os.path.join(root, "events.jsonl")
    argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
            "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
            "--events", events_path, "--stage-budget-s", str(stage_budget_s),
            "--settle-s", "0.6", "--handoff-grace-s", "0",
            "--clear-idle-wait-s", str(clear_idle_wait_s),
            "--clear-idle-settle-s", str(clear_idle_settle_s)]
    argv += ["--", sys.executable, "-u", FAKE]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env,
                           timeout=max(90, stage_budget_s * 6))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = None
    events = []
    if os.path.exists(events_path):
        for row in open(events_path, encoding="utf-8"):
            row = row.strip()
            if row:
                try:
                    events.append(json.loads(row))
                except ValueError:
                    pass
    text = (open(transcript, "rb").read().decode("utf-8", "replace")
            if os.path.exists(transcript) else "")
    return rc, events, text


# BUSY_S=6.0, comfortably inside clear_idle_wait_s=8.0's bound once the
# fixture's own ~0.6s pre-arm delay is accounted for — a driver that reads
# CURRENT SCREEN CONTENT correctly waits this out on the FIRST bounded
# `wait_idle` and never needs ESC at all (`esc_sent` legitimately absent on
# HEAD — see the docstring above). A driver that only ever watches
# `esc to cancel` goes false-idle the instant that one line stops repainting
# (well under 1s in), types `/clear` straight into the swallow window, loses
# it, and — because the retyped `/sherlock` is lost the same way one
# boundary later — never recovers: the fixture's `STAGE_TIMEOUT` on df994a0
# is the proof, not a coincidence of budget.
rc, events, text = run(busy_s=6.0, repaint_s=1.5, stage_budget_s=15,
                       clear_idle_wait_s=8.0, clear_idle_settle_s=1.0)
kinds = [e.get("event") for e in events]
detail = "kinds=%r rc=%r transcript_tail=%s" % (kinds, rc, text[-500:])

check("no clear_typed_while_busy — the /clear was never typed into a busy "
      "screen the driver had wrongly judged idle",
      "clear_typed_while_busy" not in kinds, detail)

check("the /clear eventually reaches the target — the banner appears in "
      "the transcript",
      CLEAR_BANNER in text, detail)

check("no STAGE_TIMEOUT — a false idle here loses /clear AND the /sherlock "
      "retype to the fixture's busy swallow, and the run never recovers",
      "STAGE_TIMEOUT" not in kinds, detail)

check("CLEAR_NOT_EFFECTIVE was not reached — rc must not be 8",
      rc != 8, detail)

check("the run finishes cleanly (rc 0)", rc == 0, detail)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
