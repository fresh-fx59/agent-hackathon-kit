#!/usr/bin/env python3
"""A busy target must not swallow /clear — REGRESSION LOCK for v44's root cause.

Run 20260902T021751Z-v44's transcript showed qwen-code 0.22.0 QUEUING typed
input while a turn was in flight («⏳ 2 queued»), instead of executing it. The
driver used to type `/clear` the instant `boundary_seq` advanced on disk, with
no regard for whether the target was still generating — so the keystroke
landed in the TUI's own input queue, was never delivered, and the
conversation was never actually reset. `messages_count` climbed at every
later boundary in stage `repair` (23, 25, 27, 33, 37, 41) because every clear
in that stage was silently swallowed.

fixtures/fake_qwen.py's `busy_after_boundary` mode reproduces exactly this
shape: for FAKE_BUSY_S seconds after every stage boundary it (a) keeps
repainting the real TUI's in-flight footer ("esc to cancel") and (b) SWALLOWS
every typed line instead of acting on it. A driver that types `/clear` right
away always loses it here; one that waits for the busy marker to go quiet
first (Session.wait_idle) types after the window closes and gets through —
proven by the "Starting a new session..." banner the target only ever prints
for a /clear it actually received.
"""
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


def run(busy_s, stage_budget_s=10, clear_idle_wait_s=None):
    """Drive the busy stand-in and return (rc, transcript_text, events_rows)."""
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


# THE REGRESSION LOCK. FAKE_BUSY_S=6: the target stays busy for 6 seconds
# after the triage boundary lands — comfortably longer than the disk-polling
# driver's own detection lag (it only re-checks checkpoint.json once every
# pump(3.0) cycle, so up to ~3s of that is unavoidable in EITHER version) plus
# the near-instant typing of /clear itself. A driver that waits for idle
# before typing types AFTER the busy window closes and the /clear reaches
# the target for real; one that does not always loses it to the swallow, and
# the banner the target only prints for a /clear it actually received never
# appears.
rc, text, stderr_tail = run(busy_s=6.0, stage_budget_s=15)
check("a /clear typed after the target goes idle actually reaches it — the "
      "busy-swallow window must not eat it",
      CLEAR_BANNER in text,
      "rc=%r stderr=%s transcript_tail=%s"
      % (rc, stderr_tail, text[-500:]))

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
