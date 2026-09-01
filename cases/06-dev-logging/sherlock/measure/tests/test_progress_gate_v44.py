#!/usr/bin/env python3
"""Boundaries without deliverables must stop the run — and honest work must not.

The gate exists because 20260901T002401Z-v43 took 12 boundaries with
report_sections_written 0, and 20260831T214240Z-v43 did 643 tool calls in
`repair` with zero writes to report.md.

The control matters as much as the gate. That paid run's final 4 minutes were
its ONLY productive work: probe.md -> probe4.md tested against reportcheck.py
and citecheck.py so the model could learn what the gate would accept. None of
that moves report_sections_written, report_bytes or a worklist seal, so a naive
gate would have killed the run exactly when it started to succeed.

Three cases: the gate (barren_boundaries, must trip at boundary 2), the
control (slow_but_honest, must survive because it always makes real
progress), and the escape's own control (gate_tool_near_miss, must survive
because two/three consecutive boundaries ran a gate tool and moved nothing
else — proving the gate-tool escape in boundary_advanced is load-bearing, not
dead code).

Worklist fixture rows use an empty verdict column for "still open" and
"X closed" for resolved, header `#`-prefixed — `inspect_worklists`
(skills/v44/tools/checkpoint.py:55) counts a row RESOLVED when column 2 is
non-empty and does not start with `?`, so a non-empty placeholder like
"D open" or an unprefixed header line would silently inflate `resolved`.
Matches the convention already fixed in tools/tests/test_boundary_history_v44.py.
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


def run_case(mode, seconds=120):
    tmp = tempfile.mkdtemp(prefix="progress-%s-" % mode)
    work = os.path.join(tmp, "work")
    os.makedirs(work)
    # Small on purpose: the honest-run control resolves one row per boundary
    # and each boundary costs a real /clear + clear-verification wait
    # (Task 2: max(settle_s * 4, 60.0) is the FLOOR only when settle_s is
    # large; the fixture's short settle keeps each cycle to a few seconds,
    # but the row count still multiplies it, and the subprocess has a fixed
    # timeout).
    with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# id\tverdict\n")
        for n in range(4):
            fh.write("g%03d\t\n" % n)
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True, text=True)
    # Timestamps spread a second apart out to ~8 minutes ahead — not "+i ms",
    # which packs all 500 rows into half a second and leaves nothing "fresh"
    # once real wall-clock time has moved past it. Task 2's clear-verification
    # wait (max(settle_s * 4, 60.0)) needs a row with ts_ms AFTER the reseed
    # instant, or first_fresh_after() never resolves and the subprocess blows
    # its own timeout for a reason that has nothing to do with the gate.
    ledger = os.path.join(tmp, "upstream.jsonl")
    now_ms = int(time.time() * 1000)
    with open(ledger, "w", encoding="utf-8") as fh:
        for i in range(1, 500):
            fh.write(json.dumps({
                "kind": "call", "ts_ms": now_ms + i * 1000,
                "usage": {"prompt_tokens": 150000 + i},
                "messages_count": 2}) + "\n")
    events = os.path.join(tmp, "events.jsonl")
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_MODE": mode,
                "FAKE_CHECKPOINT": CHECKPOINT})
    proc = subprocess.run(
        [sys.executable, DRIVE, "--work", work, "--cwd", tmp,
         "--prompt", "поехали", "--transcript", os.path.join(tmp, "t.log"),
         "--events", events, "--ledger", ledger, "--stage-budget-s", "60",
         "--threshold", "100", "--idle-nudge-s", "0",
         "--", sys.executable, FAKE],
        capture_output=True, text=True, env=env, timeout=seconds + 120)
    rows = []
    if os.path.exists(events):
        rows = [json.loads(l) for l in open(events, encoding="utf-8") if l.strip()]
    return proc.returncode, rows, proc.stdout + proc.stderr


# THE GATE. Barren boundaries must stop at the SECOND one, not the twelfth.
rc, events, out = run_case("barren_boundaries")
kinds = [e.get("event") for e in events]
check("NO_PROGRESS" in kinds,
      "barren boundaries did not trip the gate: %r" % kinds)
check(rc == 9, "expected rc 9 for NO_PROGRESS, got %r (%s)" % (rc, out[-400:]))
check(kinds.count("batch_boundary") <= 3,
      "the gate let %d boundaries pass before stopping — two is the budget"
      % kinds.count("batch_boundary"))
detail = " ".join(e.get("detail", "") for e in events
                  if e.get("event") == "NO_PROGRESS")
for metric in ("report_sections_written", "resolved"):
    check(metric in detail,
          "NO_PROGRESS does not name %s and its values: %r" % (metric, detail))

# THE CONTROL. Slow but real progress must NOT trip it.
rc2, events2, out2 = run_case("slow_but_honest")
kinds2 = [e.get("event") for e in events2]
check("NO_PROGRESS" not in kinds2,
      "the gate aborted an honest run that was making slow progress: %r" % kinds2)
check(rc2 == 0, "the honest run did not finish cleanly: rc %r (%s)"
      % (rc2, out2[-400:]))

# THE ESCAPE'S OWN CONTROL. Two (really three) consecutive boundaries that
# move none of resolved/report_sections_written/report_bytes/a worklist seal,
# each one running a gate tool — must NOT trip the gate. Without this, the
# gate-tool escape in boundary_advanced is untested and could regress to dead
# code (Finding 1: GATE_TOOLS was declared and referenced by nothing) while
# every other test here stayed green.
rc3, events3, out3 = run_case("gate_tool_near_miss")
kinds3 = [e.get("event") for e in events3]
check("NO_PROGRESS" not in kinds3,
      "the gate tripped on gate-tool-only boundaries that ran reportcheck/"
      "citecheck/statecheck — the escape is not load-bearing: %r" % kinds3)
check(rc3 == 0, "the gate-tool near-miss run did not finish cleanly: rc %r (%s)"
      % (rc3, out3[-400:]))

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
