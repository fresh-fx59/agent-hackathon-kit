#!/usr/bin/env python3
"""FIX 6: a stage needs a BATCH boundary, and the driver must notice a stall.

Both gaps cost the paid run 20260827T104334Z-v40.

6a — A STAGE HAS NO BATCH BOUNDARY. `handoff` only advances
triage → draft → repair → done, and it refuses while worklist rows are open. But
262 rows do not close in one session: that run closed 13 in 35 minutes while the
parent grew to 227,030 prompt tokens. A long stage needs many bounded sessions,
so `handoff --done triage --partial` closes a BATCH: it prints the block, does not
advance the stage, and records how far the batch got.

6b — THE DRIVER CANNOT TELL IDLE FROM WORKING. MEASURED: the last upstream call was
at 11:18:03 and ended `finish_reason: error`; the session then sat at its input
prompt until it was stopped at 11:36, 18 minutes later, on the way to burning the
full 5,400-second stage budget before STAGE_TIMEOUT. A human would type «продолжай».
The driver now nudges when the LEDGER has been quiet for --idle-nudge-s, and gives
up only after a bounded number of nudges.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
MEASURE = os.path.join(SHERLOCK, "measure")
SKILLS = os.path.join(SHERLOCK, "skills")
CHECKPOINT = os.path.join(SKILLS, "v41", "tools", "checkpoint.py")
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
FAILED = []

HEADER = u"# id\tвердикт\n"
CLOSED = u"W-%d\tN a.log:1 «q» n=1 фон\n"
OPEN = u"W-%d\t?\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(tool, args, stdin=b""):
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.Popen([sys.executable, tool] + args, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e)
    out, err = p.communicate(stdin)
    return (p.returncode, out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


def work_with(closed, open_rows):
    d = tempfile.mkdtemp(prefix="gap-")
    w = os.path.join(d, "work")
    os.makedirs(w)
    body = HEADER + "".join(CLOSED % i for i in range(1, closed + 1))
    body += "".join(OPEN % i for i in range(closed + 1, closed + open_rows + 1))
    open(os.path.join(w, "worklist.tsv"), "w", encoding="utf-8").write(body)
    return w


def main():
    # ── 6a. the batch boundary ───────────────────────────────────────────────
    w = work_with(closed=13, open_rows=249)
    run(CHECKPOINT, ["init", "--work", w])
    rc, out, err = run(CHECKPOINT, ["handoff", "--work", w, "--done", "triage"])
    check("a full handoff STILL refuses while rows are open — the coverage rule "
          "does not move", rc != 0, out[:160])

    rc, out, err = run(CHECKPOINT, ["handoff", "--work", w, "--done", "triage",
                                    "--partial"])
    check("--partial exits 0 with rows still open", rc == 0, out + err)
    check("the block still tells the human to clear and re-invoke",
          "/clear" in out and "/sherlock" in out, out[:300])
    check("the block says the stage is CONTINUING, not finished",
          "triage" in out and ("ПРОДОЛЖ" in out.upper() or "ЧАСТ" in out.upper()),
          out[:400])
    row = json.load(open(os.path.join(w, "checkpoint.json"), encoding="utf-8"))
    check("--partial does NOT advance the stage", row.get("stage") == "triage",
          row)
    check("...and it records how far this batch got",
          row.get("resolved") == 13 and row.get("unresolved") == 249, row)
    check("it records that the last boundary was a partial one",
          row.get("stage_partial") is True, row)
    check("the block names the remaining row count so the human sees progress",
          "249" in out, out[:400])

    # Closing the rest must still let the real handoff through.
    body = HEADER + "".join(CLOSED % i for i in range(1, 263))
    open(os.path.join(w, "worklist.tsv"), "w", encoding="utf-8").write(body)
    rc, out, err = run(CHECKPOINT, ["handoff", "--work", w, "--done", "triage"])
    check("once every row is closed the full handoff advances to draft",
          rc == 0 and json.load(open(os.path.join(w, "checkpoint.json"),
                                     encoding="utf-8"))["stage"] == "draft",
          out + err)
    check("and the partial flag is cleared once the stage really ends",
          json.load(open(os.path.join(w, "checkpoint.json"),
                         encoding="utf-8")).get("stage_partial") is False)

    rc, out, err = run(CHECKPOINT, ["handoff", "--work", w, "--done", "draft",
                                    "--partial"])
    check("--partial is refused for a stage where a batch means nothing (draft)",
          rc != 0, out[:200])

    # ── 6b. the idle nudge, as a unit ────────────────────────────────────────
    import importlib.util
    spec = importlib.util.spec_from_file_location("idrive", DRIVER)
    idrive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(idrive)
    check("the driver exposes an idle detector", hasattr(idrive, "ledger_quiet_s"))
    if hasattr(idrive, "ledger_quiet_s"):
        d = tempfile.mkdtemp(prefix="ledger-")
        led = os.path.join(d, "upstream.jsonl")
        check("no ledger at all is not treated as a stall (nothing started yet)",
              idrive.ledger_quiet_s(led) is None)
        open(led, "w").write(json.dumps({"ts": "x", "status": 200}) + "\n")
        quiet = idrive.ledger_quiet_s(led)
        check("a ledger just written reads as ~0 seconds quiet",
              quiet is not None and quiet < 5, quiet)
        old = time.time() - 1200
        os.utime(led, (old, old))
        quiet = idrive.ledger_quiet_s(led)
        check("a ledger untouched for 20 minutes reads as ~1200 s quiet",
              quiet is not None and 1100 < quiet < 1300, quiet)

    check("the driver takes a nudge budget and a nudge text",
          "--idle-nudge-s" in open(DRIVER, encoding="utf-8").read()
          and "--max-nudges" in open(DRIVER, encoding="utf-8").read())
    src = open(DRIVER, encoding="utf-8").read()
    check("a stall is reported as its own terminal, not as a silent timeout",
          "STAGE_STALLED" in src)
    check("the nudge is recorded as an event, so a run can be audited for how "
          "much of it was pushed by the driver", "nudged" in src)

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ a long stage can finish, and a stall cannot cost 90 minutes")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
