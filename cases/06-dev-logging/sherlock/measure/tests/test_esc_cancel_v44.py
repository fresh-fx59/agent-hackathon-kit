#!/usr/bin/env python3
"""REGRESSION LOCK — a target that never goes idle must be cancelled with
ESC before /clear, not waited out.

Run 20260902T045011Z-v44's real transcript proved a bounded idle-wait alone
cannot fix `CLEAR_NOT_EFFECTIVE`: the target was 4m21s into a single turn and
never went idle in the window (busy footer 54 times, "queued" 230 times over
the sampled window). The fix is the TUI's own escape hatch — the busy footer
names it: "esc to cancel". Before typing /clear, if the target is still
busy after the existing bounded wait_idle, send a bare ESC (never followed by
Enter) to cancel the in-flight turn, then re-check idle, bounded
(ESCAPE_ATTEMPTS in interactive-drive.py) — never an unbounded loop.

fixtures/fake_qwen.py's `busy_until_esc` mode is a target that goes busy
FOREVER once it has done its one piece of real work (a `handoff --partial`
that makes boundary_seq durable on disk — the fact the whole ESC-safety
argument rests on: cancelling AFTER that point only discards trailing
narration the next session would redo from the checkpoint anyway) and comes
back to idle ONLY when it receives the byte 0x1b. No bounded wait_idle can
ever reach idle against this fixture — proving the regression the same way
the real run failed. It also appends a live ledger row every ~1.5s, carrying
a big stale messages_count until a REAL /clear lands (never, without ESC)
and messages_count == 2 afterward (only once ESC unlocks a genuine /clear) —
the same signal `interactive-drive.py` already uses against a real target.

RUN THIS ON HEAD (with the ESC-cancel fix): the clear must be verified.
RUN THIS ON 2863491 (before the fix, in a detached worktree): the fixture
never goes idle inside the bounded wait, /clear is typed anyway and
swallowed, the ledger stays stale forever, and the run must reach
CLEAR_NOT_EFFECTIVE (rc 8).
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
if not os.path.exists(CHECKPOINT):
    # A worktree checked out at a commit before v44's tools/ existed (e.g.
    # 2863491's ancestor state) may not have skills/v44 populated — fall
    # back to whatever the newest skills/vNN/tools/checkpoint.py is, the
    # same contract checkpoint.py has kept across v41-v44.
    for cand in sorted(os.listdir(os.path.join(SHERLOCK, "skills")), reverse=True):
        p = os.path.join(SHERLOCK, "skills", cand, "tools", "checkpoint.py")
        if os.path.exists(p):
            CHECKPOINT = p
            break
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(clear_idle_wait_s=5.0, stage_budget_s=60):
    root = tempfile.mkdtemp(prefix="esccancel-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True)
    ledger = os.path.join(root, "upstream.jsonl")
    open(ledger, "w", encoding="utf-8").close()
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
                "FAKE_MODE": "busy_until_esc", "FAKE_LEDGER": ledger,
                "PYTHONUNBUFFERED": "1"})
    transcript = os.path.join(root, "transcript.log")
    events = os.path.join(root, "events.jsonl")
    argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
            "--prompt", "поехали", "--transcript", transcript,
            "--events", events, "--ledger", ledger,
            "--stage-budget-s", str(stage_budget_s),
            "--settle-s", "0.6", "--handoff-grace-s", "0",
            "--clear-idle-wait-s", str(clear_idle_wait_s),
            "--clear-idle-settle-s", "0.5",
            "--", sys.executable, "-u", FAKE]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env,
                           timeout=max(120, stage_budget_s * 3))
        rc = p.returncode
        stderr_tail = p.stderr[-1200:]
    except subprocess.TimeoutExpired as exc:
        rc = None
        stderr_tail = ((exc.stderr or "")[-1200:]
                       if isinstance(exc.stderr, str)
                       else (exc.stderr or b"")[-1200:].decode(
                           "utf-8", "replace"))
    rows = ([json.loads(l) for l in open(events, encoding="utf-8")
             if l.strip()] if os.path.exists(events) else [])
    return rc, rows, stderr_tail


rc, events, stderr_tail = run()
kinds = [e.get("event") for e in events]
detail = "rc=%r kinds=%r stderr=%s" % (rc, kinds, stderr_tail)

# THE PROOF THIS FIXTURE ADDS OVER test_idle_wait_v44.py: that one's target
# recovers on its own after a bounded busy window, so a wait-only driver
# still eventually gets through. This one's target recovers ONLY on ESC —
# so whichever of the two outcomes below fires tells the two implementations
# apart cleanly: the pre-fix driver (bounded wait, then type-anyway, no ESC)
# reaches CLEAR_NOT_EFFECTIVE against this fixture every time; the fixed
# driver reaches clear_verified.
check("the clear gets verified — a fresh (messages_count==2) call appears "
      "after a /clear that actually reached the target, which the fixture "
      "only allows once it received ESC",
      "clear_verified" in kinds, detail)
check("no CLEAR_NOT_EFFECTIVE — an ESC-cancelled turn must not be "
      "mistaken for a clear that never landed",
      "CLEAR_NOT_EFFECTIVE" not in kinds, detail)
check("the driver actually sent ESC before /clear landed — otherwise "
      "clear_verified above would be true by luck, not by the fix",
      any(e.get("event") == "esc_sent" for e in events), detail)
check("rc is not 8", rc != 8, detail)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
