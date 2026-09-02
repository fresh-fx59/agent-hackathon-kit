#!/usr/bin/env python3
"""A busy target must not swallow /clear, /sherlock, or the reseed line —
REGRESSION LOCK for v44's root cause and its follow-on.

Run 20260902T021751Z-v44's transcript showed qwen-code 0.22.0 QUEUING typed
input while a turn was in flight («⏳ 2 queued»), instead of executing it. The
driver used to type all three reseed-sequence lines — /clear, the /sherlock
retype, the reseed line — back to back with no regard for whether the target
was still generating, so any of the three could be queued and never
delivered. Losing /clear alone is bad (a stale conversation survives);
losing the /sherlock retype is WORSE and silent: the reseed line then lands
in a session with no skill loaded — no arm, no procedure — and nothing on
disk looks wrong.

fixtures/fake_qwen.py's `busy_after_boundary` mode reproduces both swallow
windows: FAKE_BUSY_S busy after the stage boundary lands (can eat /clear),
and — because a real qwen-code session does not go idle just because /clear
landed — FAKE_BUSY_S busy again immediately after /clear is genuinely
processed (can eat the /sherlock retype instead). A driver that waits for
idle before EACH of the three typed inputs (Session.wait_idle, called via
one shared `wait_before` in interactive-drive.py) gets all three through;
one that waits only before /clear loses the retype to the second window.

The observable that actually proves "skill loaded", not merely "reseed
typed": the fixture only ever prints "Base directory for this skill" in
response to a /clear it genuinely received, and only ever prints "I have no
skill loaded" when a message arrives while `loaded` is still False. A
transcript with the skill banner reappearing AFTER the clear banner, and
with the "no skill loaded" line never appearing, is the only shape that is
consistent with the retyped /sherlock having actually reached the target —
a reseed line that landed in a bare session cannot produce it, because
`loaded` would still read False when the reseed arrives.
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
CLEAR_BANNER = "Starting a new session, resetting chat, and clearing terminal."
SKILL_BANNER = "Base directory for this skill"
NO_SKILL = "I have no skill loaded"
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(busy_s, stage_budget_s=10, clear_idle_wait_s=None):
    """Drive the busy stand-in and return (rc, transcript_text, stderr_tail)."""
    root = tempfile.mkdtemp(prefix="idlewait-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True)
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
                "FAKE_MODE": "busy_after_boundary", "FAKE_BUSY_S": str(busy_s),
                "PYTHONUNBUFFERED": "1"})
    transcript = os.path.join(root, "transcript.log")
    events = os.path.join(root, "events.jsonl")
    argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
            "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
            "--events", events, "--stage-budget-s", str(stage_budget_s),
            "--settle-s", "0.6", "--handoff-grace-s", "0"]
    if clear_idle_wait_s is not None:
        argv += ["--clear-idle-wait-s", str(clear_idle_wait_s),
                 "--clear-idle-settle-s", "1.0"]
    argv += ["--", sys.executable, "-u", FAKE]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env,
                           timeout=max(90, stage_budget_s * 6))
        rc = p.returncode
        stderr_tail = p.stderr[-800:]
    except subprocess.TimeoutExpired as exc:
        rc = None
        stderr_tail = (exc.stderr or b"")[-800:] if isinstance(
            exc.stderr, bytes) else (exc.stderr or "")[-800:]
    text = (open(transcript, "rb").read().decode("utf-8", "replace")
            if os.path.exists(transcript) else "")
    return rc, text, stderr_tail


# THE REGRESSION LOCK. FAKE_BUSY_S=6, comfortably longer than the
# disk-polling driver's own detection lag (it only re-checks
# checkpoint.json once every pump(3.0) cycle, so up to ~3s of that is
# unavoidable in EITHER version) plus the near-instant typing of one line.
# The fixture opens a SECOND busy window immediately after a genuine /clear,
# so both swallow points are exercised in one run.
rc, text, stderr_tail = run(busy_s=6.0, stage_budget_s=15)
detail = "rc=%r stderr=%s transcript_tail=%s" % (rc, stderr_tail, text[-700:])

check("a /clear typed after the target goes idle actually reaches it — the "
      "first busy-swallow window must not eat it",
      CLEAR_BANNER in text, detail)

clear_at = text.find(CLEAR_BANNER)
retype_at = text.find(SKILL_BANNER, clear_at + 1) if clear_at >= 0 else -1
check("the /sherlock retype after /clear also reaches the target — the "
      "SECOND busy-swallow window (opened the instant /clear lands) must "
      "not eat it either",
      clear_at >= 0 and retype_at > clear_at, detail)

check("the reseeded session actually has the skill loaded — it never "
      "answers as a bare, skill-less session",
      NO_SKILL not in text, detail)



# THIRD SWALLOW WINDOW, one keystroke type further still: `handoff --partial`
# at a threshold crossing. Unlike /clear (retried at every later boundary
# regardless) this command had NO retry at all before this fix: the
# `awaiting_boundary is None` latch fires once per crossing and only a REAL
# boundary advance ever clears it, so a queued/swallowed `handoff --partial`
# used to leave the latch stuck for the rest of the stage — no boundary ever
# comes, and the run reaches STAGE_TIMEOUT looking like the arm stalled.
#
# fixtures/fake_qwen.py's `handoff_drop_once` mode swallows the FIRST
# `handoff --partial ...` it ever receives, deterministically — no real-time
# race against a busy footer, just a guaranteed lost first attempt exactly
# like a keystroke that queued and was never delivered — and processes every
# one after that normally. Only a driver that RETRIES a stuck latch (not
# merely waits once before typing) can still reach the boundary.
def run_handoff_retry(retry_window_s, stage_budget_s=25):
    root = tempfile.mkdtemp(prefix="handoffretry-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True)
    led = os.path.join(root, "upstream.jsonl")
    now_ms = int(time.time() * 1000)
    with open(led, "w", encoding="utf-8") as fh:
        for i in range(1, 200):
            fh.write(json.dumps({"kind": "call", "ts_ms": now_ms + i * 1000,
                                 "usage": {"prompt_tokens": 300000},
                                 "messages_count": 400}) + "\n")
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
                "FAKE_MODE": "handoff_drop_once", "PYTHONUNBUFFERED": "1"})
    transcript = os.path.join(root, "transcript.log")
    events = os.path.join(root, "events.jsonl")
    argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
            "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
            "--events", events, "--stage-budget-s", str(stage_budget_s),
            "--settle-s", "0.5", "--handoff-grace-s", "0",
            "--ledger", led, "--threshold", "1000",
            "--handoff-retry-window-s", str(retry_window_s),
            "--handoff-max-retries", "3",
            "--", sys.executable, "-u", FAKE]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env,
                           timeout=max(90, stage_budget_s * 4))
        rc = p.returncode
        stderr_tail = p.stderr[-800:]
    except subprocess.TimeoutExpired as exc:
        rc = None
        stderr_tail = (exc.stderr or "")[-800:]
    rows = ([json.loads(l) for l in open(events, encoding="utf-8")
             if l.strip()] if os.path.exists(events) else [])
    return rc, rows, stderr_tail


# retry_window_s=3.0: short enough to keep the test fast, long enough that
# the driver never mistakes "the target is still working on it" for "it was
# swallowed" inside one normal poll cycle.
rc3, rows3, stderr3 = run_handoff_retry(retry_window_s=3.0)
kinds3 = [r.get("event") for r in rows3]
detail3 = "rc=%r kinds=%r stderr=%s" % (rc3, kinds3, stderr3)

check("a swallowed handoff --partial is RETRIED, not lost — the driver "
      "notices the latch never cleared and retypes it",
      "handoff_retry" in kinds3, detail3)
check("the retried handoff --partial reaches the target and the boundary "
      "actually advances — no STAGE_TIMEOUT from a lost keystroke",
      ("batch_boundary" in kinds3 or "stage_advanced" in kinds3)
      and "STAGE_TIMEOUT" not in kinds3, detail3)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
