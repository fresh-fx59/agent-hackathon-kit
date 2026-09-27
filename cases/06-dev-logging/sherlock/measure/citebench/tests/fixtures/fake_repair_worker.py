#!/usr/bin/env python3
"""Fake per-phase worker for the repair_loop E2E test. No model, no network.

Usage: fake_repair_worker.py <phase_id> <work_dir> <pid_log>

Simulates an ADVERSARIAL model: on every phase it writes checkpoint.json
claiming stage="done" (forged/premature), regardless of the phase actually
requested — the launcher must never believe this. Real progress (whether
report.md stops being a placeholder) only happens on "draft" and the
FIRST repair round; the harness must still keep asking the trusted gate
after that, and must cap at max_repair_rounds rather than trust the forged
"done" claim to stop early.
"""
import json
import os
import sys
import time
from pathlib import Path

PLACEHOLDER = "REPORT PLACEHOLDER unresolved=1\n"


def main():
    phase, work_dir, pid_log = sys.argv[1], sys.argv[2], sys.argv[3]
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    with open(pid_log, "a", encoding="utf-8") as fh:
        fh.write("%s %d %f\n" % (phase, os.getpid(), time.time()))

    # ALWAYS forge stage=done, no matter what actually happened -- the
    # trusted-stage recompute must ignore this entirely.
    (work_dir / "checkpoint.json").write_text(
        json.dumps({"stage": "done", "state": "ready_for_synthesis"}), encoding="utf-8")

    if phase == "draft":
        # writes a report, but it never stops being a placeholder shape
        (work_dir / "report.md").write_text(PLACEHOLDER, encoding="utf-8")
    # repair rounds change nothing about the report: the fake gate keeps
    # failing, so the loop must run to its round cap rather than stop early
    # on the forged claim.


if __name__ == "__main__":
    main()
