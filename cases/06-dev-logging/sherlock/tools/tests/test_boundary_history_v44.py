#!/usr/bin/env python3
"""Boundaries must leave a history, or progress cannot be judged.

Paid run 20260901T002401Z-v43 took 12 boundaries with report_sections_written
stuck at 0 and never wrote report.md once in 1,139 tool calls. Free run
20260831T214240Z-v43 did 643 tool calls over 1h49m in `repair` with zero
edit/write_file on report.md. checkpoint.json is overwrite-in-place, so after
the fact only the FINAL state survives and no delta can be computed.

Worse, handoff() calls init() first (checkpoint.py:307), which refreshes the
counts before the row is judged — the previous values are destroyed, not
merely ignored.

Worklist fixture rows use an empty verdict column for "still open" and
"X closed" for resolved — matching the real triage verdict codes D/N/X
(all three ARE resolved verdicts per inspect_worklists and
skills/v43/agents/sherlock-triage.md:71), not the placeholder text
"D open" the plan draft used, which inspect_worklists would have counted
as resolved and made the resolved==0 assertion below fail for reasons
unrelated to append_boundary.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v44", "tools", "checkpoint.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def run(work, *args):
    return subprocess.run([sys.executable, CHECKPOINT, *args, "--work", work],
                          capture_output=True, text=True)


tmp = tempfile.mkdtemp(prefix="boundary-history-")
work = os.path.join(tmp, "work")
os.makedirs(work)
with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
    fh.write("# id\tverdict\n")
    for n in range(10):
        fh.write("g%03d\t\n" % n)

run(work, "init")
first = run(work, "handoff", "--done", "triage", "--partial")
check(first.returncode == 0, "first partial handoff failed: %s" % first.stderr)

history = os.path.join(work, "checkpoint.jsonl")
check(os.path.exists(history), "no work/checkpoint.jsonl after a boundary")

rows = []
if os.path.exists(history):
    rows = [json.loads(l) for l in open(history, encoding="utf-8") if l.strip()]
check(len(rows) == 1, "expected 1 history row, got %d" % len(rows))

second = run(work, "handoff", "--done", "triage", "--partial")
check(second.returncode == 0, "second partial handoff failed: %s" % second.stderr)
if os.path.exists(history):
    rows = [json.loads(l) for l in open(history, encoding="utf-8") if l.strip()]
check(len(rows) == 2, "history is not append-only: %d rows after 2 boundaries"
      % len(rows))

if len(rows) == 2:
    for row in rows:
        for field in ("boundary_seq", "stage", "resolved", "total",
                      "report_bytes", "report_sections_written",
                      "report_sections_required", "worklist_seals", "at"):
            check(field in row, "history row lacks %s: %r" % (field, sorted(row)))
    check(rows[0]["boundary_seq"] < rows[1]["boundary_seq"],
          "boundary_seq did not advance across rows")
    check(rows[0]["worklist_seals"] == rows[1]["worklist_seals"],
          "seals differ although nothing changed — churn would read as progress")
    check(rows[0]["resolved"] == rows[1]["resolved"] == 0,
          "resolved should be 0 for both barren boundaries")

# A boundary that DID resolve rows must show it.
with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
    fh.write("# id\tverdict\n")
    for n in range(10):
        fh.write("g%03d\t%s\n" % (n, "X closed" if n < 4 else ""))
run(work, "handoff", "--done", "triage", "--partial")
rows = [json.loads(l) for l in open(history, encoding="utf-8") if l.strip()]
check(len(rows) == 3, "third boundary not appended")
if len(rows) == 3:
    check(rows[2]["worklist_seals"] != rows[1]["worklist_seals"],
          "the seal did not change although the worklist did")

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
