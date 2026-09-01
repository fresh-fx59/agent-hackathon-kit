#!/usr/bin/env python3
"""A session must be boundable in draft, because that is where it broke.

On 20260830T190815Z-v42 the draft stage ran 55 minutes (reseeded 19:38:06,
stage_advanced repair 20:33:36) and BOTH clipped state snapshots landed inside
it, at 20:03:32 and 20:06:07. v42's BATCHED_STAGES = ("triage",) made
`handoff --partial draft` raise, so the one mechanism that bounds a session
refused to run where the failure lives.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
V43 = os.path.join(SHERLOCK, "skills", "v43")
FAILED = []

spec = importlib.util.spec_from_file_location(
    "checkpoint43", os.path.join(V43, "tools", "checkpoint.py"))
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


check(cp.BATCHED_STAGES == ("triage", "draft", "repair"),
      "BATCHED_STAGES is %r" % (cp.BATCHED_STAGES,))


def fresh_work(stage, report_body=""):
    """A minimal work/ directory parked in `stage`."""
    work = tempfile.mkdtemp(prefix="v43-partial-")
    with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write("path\tstatus\n1.log\tresolved\n")
    row = cp.init(Path(work))
    row["stage"] = stage
    with open(os.path.join(work, "checkpoint.json"), "w", encoding="utf-8") as fh:
        json.dump(row, fh, ensure_ascii=False, sort_keys=True)
    if report_body:
        with open(os.path.join(work, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(report_body)
    return work


REPORT = "## ИНВЕНТАРЬ\nтекст\n\n## ЧЕГО НЕ ХВАТАЕТ В ЛОГАХ\nтекст\n"

for stage in ("draft", "repair"):
    work = fresh_work(stage, REPORT)
    try:
        row, block = cp.handoff(work, stage, partial=True)
    except Exception as exc:               # noqa: BLE001 - we want the message
        FAILED.append("handoff --partial %s raised %s" % (stage, exc))
        continue
    check(row["stage"] == stage,
          "a partial %s boundary must NOT advance the stage: %r"
          % (stage, row["stage"]))
    check(row["stage_partial"] is True,
          "stage_partial not set for %s" % stage)
    check(int(row[cp.BOUNDARY_SEQ]) == 1,
          "boundary_seq did not advance for %s: %r" % (stage, row[cp.BOUNDARY_SEQ]))
    check("report_sections_written" in row and "report_sections_required" in row,
          "%s boundary records no section counts: %r" % (stage, sorted(row)))
    check(row["report_bytes"] == len(REPORT.encode("utf-8")),
          "%s boundary recorded report_bytes %r" % (stage, row.get("report_bytes")))
    check("/clear" in block and "/sherlock" in block,
          "the %s partial block does not carry the three steps" % stage)
    check("разобрано" not in block,
          "the %s partial block reuses triage's row wording" % stage)
    shutil.rmtree(work)

# STILL TRUE, AND STILL CORRECT: the arm RECORDS a barren boundary, it does
# not judge one. Judging is the driver's job and lives in
# measure/tests/test_progress_gate_v44.py, which stops a run after two of
# these in a row. Before v44 nothing judged it at all and a paid run took 12.
#
# a partial boundary with NO report yet must still work and say zero
work = fresh_work("draft")
row, block = cp.handoff(work, "draft", partial=True)
check(row["report_sections_written"] == 0,
      "an empty draft must record zero sections written: %r"
      % row["report_sections_written"])
shutil.rmtree(work)

# `done` is still not batchable
work = fresh_work("triage")
try:
    cp.handoff(work, "done", partial=True)
    FAILED.append("handoff --partial done did not raise")
except ValueError:
    pass
shutil.rmtree(work)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
