#!/usr/bin/env python3
"""REGRESSION LOCK — the driver must not type `/sherlock` into a TUI that is
still initializing, and must never run a whole session with no skill loaded.

WHAT THIS REPRODUCES. Paid run 20260902T171049Z-v44 (neuraldeep,
deepseek-v4-flash) did nothing for 28 minutes and made ZERO upstream calls.
Its own transcript says why: `/sherlock` was typed 7 seconds after launch
while the TUI still showed «Initializing...», and the answer came back
`✕ Unknown command: /sherlock` — thirteen times. The task prompt then went
into a session with NO SKILL LOADED, no request was ever sent, and the run
sat silent until it was stopped by hand (exit 143, no ledger file, no money
spent).

WHY IT HAD NEVER FAILED BEFORE. `interactive-drive.py` opens with
`ses.pump(settle_s)  # let the UI come up` — a blind fixed wait written on
2026-08-27 (d1e2fbe), before any idleness detection existed. Every one of
v44's four idle-wait fixes was wired into the BOUNDARY path (`/clear`, the
`/sherlock` retype, `handoff --partial`, the reseed line); startup was never
given one. Free run 20260902T105204Z-v44 typed at the same +7s an hour
earlier and won the race, which is exactly why five prior runs proved
nothing about it.

WHY AN IDLE WAIT ALONE CANNOT FIX IT. While qwen-code is initializing, its
footer carries NO busy hint at all — no `esc to cancel`, no `Ctrl+Q to
queue`, no `queued`. Every busy marker the driver has reads IDLE there, and
the footer rule added in 053cc79 reads it as idle too. Readiness has to be a
POSITIVE signal (the ready footer line), not the absence of a busy one.

AND THE SECOND HALF OF THE BUG: nothing checked the outcome. The driver
logged `typed /sherlock`, which records that keystrokes were sent, not that
the command was accepted, and no code looked for `Unknown command`. So the
race had no way of being observed until it lost — with money on the table.

THE FIXTURE. `FAKE_MODE=slow_init` repaints «Initializing...» with an
idle-looking footer for `FAKE_INIT_S` seconds and answers any typed line
with `✕ Unknown command: <line>`, discarding it. After the window it behaves
like the happy stand-in.

RUN THIS ON 8287536 (before the fix): `/sherlock` is typed inside the
window, `✕ Unknown command: /sherlock` appears, the skill banner never
appears, and the run cannot finish — the pre-fix failure, reproduced.
RUN THIS ON HEAD: the driver waits for the ready footer, `/sherlock` is
accepted, the skill banner appears, and the run finishes rc 0.
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
SKILL_BANNER = "Base directory for this skill"
UNKNOWN = "Unknown command: /sherlock"
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(init_s, stage_budget_s=20):
    root = tempfile.mkdtemp(prefix="slowinit-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True)
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
                "FAKE_MODE": "slow_init", "FAKE_INIT_S": str(init_s),
                "PYTHONUNBUFFERED": "1"})
    transcript = os.path.join(root, "transcript.log")
    events = os.path.join(root, "events.jsonl")
    argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
            "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
            "--events", events, "--stage-budget-s", str(stage_budget_s),
            # settle 0.6s, exactly the shape of the real bug: far shorter
            # than the init window, so a blind wait CANNOT survive it.
            "--settle-s", "0.6", "--handoff-grace-s", "0",
            "--", sys.executable, "-u", FAKE]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env,
                           timeout=max(120, stage_budget_s * 6))
        rc, err = p.returncode, p.stderr[-600:]
    except subprocess.TimeoutExpired as exc:
        rc, err = None, "TIMEOUT"
    text = (open(transcript, "rb").read().decode("utf-8", "replace")
            if os.path.exists(transcript) else "")
    ev = (open(events, "rb").read().decode("utf-8", "replace")
          if os.path.exists(events) else "")
    return rc, text, ev, err


# 8 seconds of init against a 0.6s settle: more than an order of magnitude
# past the blind wait, and well inside a bounded readiness wait.
rc, text, ev, err = run(init_s=8.0)
detail = "rc=%r err=%s transcript_tail=%s" % (rc, err, text[-600:])

check("the skill actually loads — the /sherlock that reaches the target is "
      "the one typed AFTER initialization finished",
      SKILL_BANNER in text, detail)

check("no rejected /sherlock survives into the run — the driver either "
      "waited for readiness or retried until the command was accepted, "
      "and never proceeded on a rejection",
      SKILL_BANNER in text and text.rfind(SKILL_BANNER) > text.rfind(UNKNOWN),
      detail)

check("the run reaches a real terminal instead of hanging — the paid run "
      "sat 28 minutes with no upstream call and would have burned its "
      "whole 21,600s timeout",
      rc is not None, detail)

check("the run finishes cleanly (rc 0)", rc == 0, detail)

if FAILED:
    print("FAIL test_slow_init_v44: " + "; ".join(FAILED))
    sys.exit(1)
print("OK")
