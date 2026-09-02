#!/usr/bin/env python3
"""REGRESSION LOCK — a skill the target NEVER accepts must end the run with
its own terminal (rc 11), not a skill-less session that burns the budget.

TWO REAL RUNS SIT BEHIND THIS. On 2026-08-31 the v43 task-10 attempt found
`Unknown command: /sherlock` because Task 6 moved the arm outside `$HOME`
and `settings.skills.directories` was not being written — caught only by
reading a transcript by hand. On 2026-09-02 paid run 20260902T171049Z-v44
hit the same rejection for a different reason (a startup race) and ran for
28 minutes with no skill and no request at all.

The startup race is fixed by waiting for readiness and retyping
(test_slow_init_v44.py). This test covers the OTHER case, where waiting
cannot help because the skill genuinely is not registered: the driver must
NAME it. Silence is the failure mode that cost the most here — a run that
looks alive, produces nothing, and is only understood after the fact.

`FAKE_MODE=no_skill` rejects the command for the life of the process.

RUN THIS ON 8287536 (before the fix): the driver types once, logs
`typed /sherlock` as though it had worked, never looks at the answer, and
the run does not reach rc 11.
RUN THIS ON HEAD: three bounded attempts, then rc 11 with a
`SKILL_NOT_LOADED` event naming the target's own words.
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
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


root = tempfile.mkdtemp(prefix="noskill-")
work = os.path.join(root, "work")
os.makedirs(work)
open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
    HEADER + "".join(ROW % i for i in range(1, 4)))
subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
               capture_output=True)
env = dict(os.environ)
env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
            "FAKE_MODE": "no_skill", "PYTHONUNBUFFERED": "1"})
transcript = os.path.join(root, "transcript.log")
events = os.path.join(root, "events.jsonl")
argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
        "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
        "--events", events, "--stage-budget-s", "20",
        "--settle-s", "0.6", "--handoff-grace-s", "0",
        "--", sys.executable, "-u", FAKE]
try:
    p = subprocess.run(argv, capture_output=True, text=True, env=env,
                       timeout=180)
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
detail = "rc=%r err=%s events=%s" % (rc, err, names)

check("the run ends with the SKILL_NOT_LOADED terminal, rc 11 — never a "
      "skill-less session left to burn the stage budget",
      rc == 11, detail)

check("it says so in the event log, in the target's own words",
      "SKILL_NOT_LOADED" in names, detail)

check("it retried before giving up — a late-registering skill must not be "
      "failed on the first rejection",
      names.count("skill_command_rejected") >= 2, detail)

check("it did NOT reach a terminal that means something else — rc 8/9/10 "
      "each name a different failure and must stay disjoint from this one",
      rc not in (8, 9, 10), detail)

if FAILED:
    print("FAIL test_skill_not_loaded_v44: " + "; ".join(FAILED))
    sys.exit(1)
print("OK")
