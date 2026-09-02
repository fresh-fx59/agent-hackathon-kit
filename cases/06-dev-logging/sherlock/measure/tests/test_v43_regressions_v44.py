#!/usr/bin/env python3
"""The regression lock: every v43 failure now has its OWN named terminal.

Seven tasks added terminals to the driver so that each historical failure
stops the run with a distinct exit code. This test does not re-prove any one
terminal fires correctly — test_clear_verified_v44.py, test_progress_gate_v44.py
and test_target_refusal_v44.py already do that in detail. Its distinct job is
DISJOINTNESS: a future edit that silently collapses two terminals into one
(e.g. NO_PROGRESS firing where CLEAR_NOT_EFFECTIVE should have) would still
leave those three files green, because none of them checks what did NOT
happen in another fixture's run. Here, each case asserts both its own event
AND the absence of every other terminal's event.

The four real failures pinned:
  - clear_noop         -> rc 8  CLEAR_NOT_EFFECTIVE  (run 20260831T214240Z-v43,
    124 silent /clear no-ops, messages climbed to 633)
  - barren_boundaries  -> rc 9  NO_PROGRESS           (run 20260901T002401Z-v43,
    12 boundaries, report_sections_written stuck at 0)
  - turns_exhausted    -> rc 10 TARGET_REFUSED        (the same run hitting
    --max-session-turns 600, refusing every later input)
  - slow_but_honest    -> rc 0  (an honest run must still finish)

run_case's shape is Task 5's (test_progress_gate_v44.py) verbatim: same
work/checkpoint.json init via checkpoint.py init, same worklist fixture
convention (# header, empty verdict = open row, "X closed" = resolved,
matching tools/tests/test_boundary_history_v44.py and
skills/v44/tools/checkpoint.py:55's inspect_worklists), same ledger row shape
(kind: "call", ts_ms, messages_count: 2 so Task 2's clear-verification wait
does not stall on an unrelated concern), same subprocess invocation of
interactive-drive.py against fixtures/fake_qwen.py. It takes an optional
ledger_rows override (clear_noop needs a STALE ledger --  messages_count 131,
over threshold, post-reseed -- that is the whole point of the fixture) and
optional drive kwargs (turns_exhausted needs shorter idle_nudge_s/stage_budget_s
to reach its terminal quickly rather than the progress-gate defaults).

Fixtures kept minimal on purpose (controller ruling: runtime is a real
constraint). Each worklist is 4 rows, one boundary is enough per case to
reach its terminal, and slow_but_honest's stage budget/row count is the same
small shape test_progress_gate_v44.py already proved converges quickly.
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

ALL_TERMINALS = ("CLEAR_NOT_EFFECTIVE", "NO_PROGRESS", "TARGET_REFUSED",
                  "STAGE_STALLED")


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def run_case(mode, seconds=120, ledger_rows=None, **drive_kwargs):
    """Drive the stand-in with a ledger we control, and return (rc, events, output).

    Shape reused verbatim from test_progress_gate_v44.py's run_case: small
    worklist, checkpoint.py init, a ledger we build ourselves, one subprocess
    call to interactive-drive.py against fake_qwen.py.
    """
    tmp = tempfile.mkdtemp(prefix="v43regress-%s-" % mode)
    work = os.path.join(tmp, "work")
    os.makedirs(work)
    with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# id\tverdict\n")
        for n in range(4):
            fh.write("g%03d\t\n" % n)
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True, text=True)
    ledger = os.path.join(tmp, "upstream.jsonl")
    now_ms = int(time.time() * 1000)
    if ledger_rows is None:
        ledger_rows = [{"kind": "call", "ts_ms": now_ms + i * 1000,
                         "usage": {"prompt_tokens": 150000 + i},
                         "messages_count": 2} for i in range(1, 500)]
    with open(ledger, "w", encoding="utf-8") as fh:
        for row in ledger_rows:
            fh.write(json.dumps(row) + "\n")
    events = os.path.join(tmp, "events.jsonl")
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_MODE": mode,
                "FAKE_CHECKPOINT": CHECKPOINT})
    args = [sys.executable, DRIVE, "--work", work, "--cwd", tmp,
            "--prompt", "поехали", "--transcript", os.path.join(tmp, "t.log"),
            "--events", events, "--ledger", ledger,
            "--stage-budget-s", str(drive_kwargs.get("stage_budget_s", 60)),
            "--threshold", str(drive_kwargs.get("threshold", 100)),
            "--idle-nudge-s", str(drive_kwargs.get("idle_nudge_s", 0)),
            "--max-nudges", str(drive_kwargs.get("max_nudges", 3)),
            "--", sys.executable, FAKE]
    proc = subprocess.run(args, capture_output=True, text=True, env=env,
                          timeout=seconds + 120)
    rows = []
    if os.path.exists(events):
        rows = [json.loads(l) for l in open(events, encoding="utf-8") if l.strip()]
    return proc.returncode, rows, proc.stdout + proc.stderr


def check_disjoint(case_name, kinds, required_event, expected_rc, rc):
    """Assert the required terminal fired, the rc matches, and no OTHER
    terminal's event leaked into this run's events."""
    check(required_event in kinds,
          "%s: expected %s in events, got %r" % (case_name, required_event, kinds))
    check(rc == expected_rc,
          "%s: expected rc %d, got %r (events %r)"
          % (case_name, expected_rc, rc, kinds))
    for other in ALL_TERMINALS:
        if other == required_event:
            continue
        check(other not in kinds,
              "%s: unrelated terminal %s leaked into this run's events "
              "(disjointness broken): %r" % (case_name, other, kinds))


# --- Case 1: clear_noop -> rc 8 CLEAR_NOT_EFFECTIVE, and nothing else ---
# A stale ledger over threshold whose post-reseed call is NOT fresh
# (messages_count 131) -- the shape run 20260831T214240Z-v43 actually
# produced. This is deliberately NOT the messages_count:2 shape (rule 4) --
# clear_noop's whole point is that the post-/clear call is NOT a fresh
# session, so it must carry a messages_count that fails the freshness check.
now_ms = int(time.time() * 1000)
stale = [{"kind": "call", "ts_ms": now_ms + 10 * 1000 * i,
          "usage": {"prompt_tokens": 121000 + i}, "messages_count": 131}
         for i in range(1, 400)]
rc1, events1, out1 = run_case("clear_noop", ledger_rows=stale, threshold=100,
                               idle_nudge_s=0)
check_disjoint("clear_noop", [e.get("event") for e in events1],
               "CLEAR_NOT_EFFECTIVE", 8, rc1)

# --- Case 2: barren_boundaries -> rc 9 NO_PROGRESS, and nothing else ---
rc2, events2, out2 = run_case("barren_boundaries")
check_disjoint("barren_boundaries", [e.get("event") for e in events2],
               "NO_PROGRESS", 9, rc2)

# --- Case 3: turns_exhausted -> rc 10 TARGET_REFUSED, and nothing else ---
rc3, events3, out3 = run_case("turns_exhausted", idle_nudge_s=5,
                               stage_budget_s=90, seconds=300)
check_disjoint("turns_exhausted", [e.get("event") for e in events3],
               "TARGET_REFUSED", 10, rc3)

# --- Case 4: slow_but_honest -> rc 0, and no terminal at all ---
rc4, events4, out4 = run_case("slow_but_honest")
kinds4 = [e.get("event") for e in events4]
check(rc4 == 0, "the honest run did not finish cleanly: rc %r (%s)"
      % (rc4, out4[-400:]))
for terminal in ALL_TERMINALS:
    check(terminal not in kinds4,
          "the honest run tripped a failure terminal it should never reach: "
          "%s in %r" % (terminal, kinds4))

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
