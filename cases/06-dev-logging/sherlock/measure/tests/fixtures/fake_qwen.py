#!/usr/bin/env python3
"""A stand-in for interactive qwen: enough of the real thing to test the driver.

It reads typed lines from its tty, and it behaves like the target in the four
ways that matter: it only knows the stage from `work/checkpoint.json`, it prints
the handoff block, `/clear` forgets the loaded skill, and — in the modes the
tests ask for — it refuses `/clear`, or advances the stage without printing the
block, or dies. Operator time is expensive: the driver is proven here first.
"""
import json
import os
import subprocess
import sys

WORK = os.environ["FAKE_WORK"]
CHECKPOINT = os.environ.get("FAKE_CHECKPOINT")     # checkpoint.py to call
MODE = os.environ.get("FAKE_MODE", "happy")
# How much chatter the flood modes print before they hand off. The driver used
# to keep an 8 MB per-stage ring buffer, so «more than 8 MB» is the shape that
# made it answer `handoff_block_unknown` on the paid run.
FLOOD_MB = float(os.environ.get("FAKE_FLOOD_MB", "9"))
loaded = False

def say(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()

def flood(megabytes=None):
    """Print megabytes of plausible TUI chatter, the way a working stage does."""
    mb = FLOOD_MB if megabytes is None else megabytes
    line = (u"\x1b[2K\rЧитаю Security.evtx: " + u"событие " * 12) + u"\n"
    per = len(line.encode("utf-8"))
    for _ in range(int(mb * 1024 * 1024 / per) + 1):
        sys.stdout.write(line)
    sys.stdout.flush()


def stage():
    try:
        return json.load(open(os.path.join(WORK, "checkpoint.json")))["stage"]
    except Exception:
        return "triage"

say("\x1b[32mfake qwen ready\x1b[0m")
while True:
    # readline(), not `for raw in sys.stdin`: iteration read-aheads a block, so
    # a line-at-a-time terminal dialogue would stall until the buffer filled.
    raw = sys.stdin.readline()
    if not raw:
        break
    line = raw.strip()
    if not line:
        continue
    if line == "/clear":
        if MODE == "clear_refused":
            say("Stop the current session's running background tasks before "
                "starting a new session.")
            continue
        if MODE == "clear_noop":
            # THE SILENT NO-OP, which is what actually happened. Run
            # 20260831T214240Z-v43's TUI accepted the keystroke, printed
            # nothing, and kept the conversation: `messages` climbed to 633
            # across 124 reseeds. It never refused — CLEAR_REFUSED's needle
            # matched 0 times in a 374 MB transcript — so a stand-in that
            # refuses cannot express this failure.
            continue
        loaded = False
        say("\x1b[2JStarting a new session, resetting chat, and clearing terminal.")
        continue
    if line == "/sherlock":
        loaded = True
        say("Base directory for this skill: /fake/skills/v40")
        continue
    if loaded and line.startswith("handoff --partial "):
        # The driver, having crossed the token threshold, types this the way a
        # human would ask the model to checkpoint mid-stage. An obedient target
        # runs checkpoint.py handoff --partial for real, advancing boundary_seq
        # on disk — the only fact the driver trusts.
        st = stage()
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                              WORK, "--done", st, "--partial"],
                             capture_output=True, text=True)
        say(out.stdout or ("partial handoff failed: " + out.stderr))
        continue
    if not loaded:
        say("I have no skill loaded, so I do not know what to do.")
        continue
    if MODE == "die":
        say("boom")
        raise SystemExit(1)
    if MODE == "threshold_then_happy":
        # A stage the driver must interrupt: the FIRST message of every stage
        # is answered with idle chatter and nothing else, so a driver that
        # never checks the ledger would sit here for the whole stage budget.
        # Only once `stage_partial` is true on disk — i.e. the driver's own
        # `handoff --partial` (handled generically above) already ran for
        # THIS stage — does the stage complete normally. This is the shape
        # the threshold branch exists for: crossing the token ceiling BEFORE
        # the arm would otherwise have finished on its own.
        try:
            row = json.load(open(os.path.join(WORK, "checkpoint.json"),
                                 encoding="utf-8"))
        except (OSError, ValueError):
            row = {}
        st = row.get("stage", "triage")
        if not row.get("stage_partial"):
            say("Сейчас посмотрю логи...")
            continue
        if st == "triage":
            rows = open(os.path.join(WORK, "worklist.tsv"),
                       encoding="utf-8").read()
            open(os.path.join(WORK, "worklist.tsv"), "w",
                encoding="utf-8").write(rows.replace(
                    "\t?", "\tN a.log:1 «q» n=1 фон"))
        if st in ("draft", "repair"):
            open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
                "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                             WORK, "--done", st], capture_output=True,
                            text=True)
        say(out.stdout or ("handoff failed: " + out.stderr))
        continue
    if MODE == "partial_then_finish":
        # First turn of the triage stage: close HALF the rows and take a BATCH
        # boundary, exactly as a real arm does when its context is filling and
        # rows are still open. Then behave normally.
        import json as _json
        st = stage()
        if st == "triage" and not os.path.exists(os.path.join(WORK, ".half")):
            rows = open(os.path.join(WORK, "worklist.tsv"),
                        encoding="utf-8").read().splitlines()
            out = []
            closed = 0
            for line in rows:
                if line.startswith("#") or "\t?" not in line:
                    out.append(line); continue
                if closed < 3:
                    out.append(line.replace("\t?", "\tN a.log:1 «q» n=1 фон"))
                    closed += 1
                else:
                    out.append(line)
            open(os.path.join(WORK, "worklist.tsv"), "w",
                 encoding="utf-8").write("\n".join(out) + "\n")
            open(os.path.join(WORK, ".half"), "w").write("done")
            out = subprocess.run([sys.executable, CHECKPOINT, "handoff",
                                  "--work", WORK, "--done", "triage",
                                  "--partial"], capture_output=True, text=True)
            say(out.stdout or ("partial handoff failed: " + out.stderr))
            continue
        if st == "triage":
            rows = open(os.path.join(WORK, "worklist.tsv"),
                        encoding="utf-8").read()
            open(os.path.join(WORK, "worklist.tsv"), "w",
                 encoding="utf-8").write(
                rows.replace("\t?", "\tN a.log:1 «q» n=1 фон"))
        if st == "draft":
            open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
                "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                              WORK, "--done", st], capture_output=True,
                             text=True)
        say(out.stdout or ("handoff failed: " + out.stderr))
        continue
    if MODE in ("flood", "flood_silent", "flood_late"):
        # A LOUD STAGE. Print far more than any ring buffer would keep, then
        # take the boundary — and, depending on the mode, print the block
        # straight away, never, or a beat AFTER the checkpoint advanced (which
        # is what the real target did: the tool call lands on disk first and the
        # closing message is printed after it).
        st = stage()
        flood()
        if st == "draft":
            open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
                "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
        if st == "triage":
            rows = open(os.path.join(WORK, "worklist.tsv"),
                        encoding="utf-8").read()
            open(os.path.join(WORK, "worklist.tsv"), "w",
                 encoding="utf-8").write(
                rows.replace("\t?", "\tN a.log:1 «q» n=1 фон"))
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                              WORK, "--done", st], capture_output=True,
                             text=True)
        block = out.stdout or ("handoff failed: " + out.stderr)
        if MODE == "flood_silent":
            say("...done, and I printed megabytes but never the block.")
            continue
        if MODE == "flood_late":
            import time as _time
            _time.sleep(float(os.environ.get("FAKE_LATE_S", "2.0")))
        say(block)
        continue
    if MODE == "barren_boundaries":
        # THE FAILURE THE SUITE COULD NOT EXPRESS. Every existing mode does
        # the stage's real work in the same breath as the handoff, so progress
        # is a POSTCONDITION of taking a boundary and the livelock is
        # unreachable. This one takes the boundary and produces nothing —
        # exactly what 20260901T002401Z-v43 did 12 times.
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                        "--done", stage(), "--partial"],
                       capture_output=True, text=True)
        say("СТУПЕНЬ ЗАВЕРШЕНА: %s" % stage())
        continue
    if MODE == "slow_but_honest" and stage() == "triage":
        # THE CONTROL. Real progress, one worklist row per boundary — slower
        # than the gate's window but never zero. A gate that cannot tell this
        # from the mode above would have killed the paid run in its final four
        # minutes, which were the only productive minutes it had.
        #
        # Fixture convention: an open row carries an EMPTY verdict column and
        # the header is `#`-prefixed — inspect_worklists (checkpoint.py:55)
        # counts any non-empty, non-"?"-prefixed verdict as resolved, so a
        # placeholder like "D open" or an unprefixed header line would
        # silently inflate `resolved` on its own.
        #
        # Once every row is resolved, triage really is done — close it for
        # real (no --partial) so the run falls through to the generic
        # draft/repair/done handling below instead of looping forever inside
        # a stage that has nothing left to resolve.
        path = os.path.join(WORK, "worklist.tsv")
        lines = open(path, encoding="utf-8").read().splitlines()
        target = None
        for index, row in enumerate(lines[1:], start=1):
            cols = row.split("\t")
            if len(cols) >= 2 and not cols[1].strip():
                target = index
                lines[index] = cols[0] + "\tX closed"
                break
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        if target is not None:
            subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                            WORK, "--done", "triage", "--partial"],
                           capture_output=True, text=True)
            say("СТУПЕНЬ ЗАВЕРШЕНА: triage")
        else:
            out = subprocess.run([sys.executable, CHECKPOINT, "handoff",
                                  "--work", WORK, "--done", "triage"],
                                 capture_output=True, text=True)
            say(out.stdout or ("handoff failed: " + out.stderr))
        continue
    if MODE == "stall":
        # Working, talkative, and never finishing a stage — the shape of a run
        # that has to be judged by its checkpoint and not by its chatter.
        say("Сейчас посмотрю логи...")
        continue
    st = stage()
    if MODE == "forged_advance":
        # Advance the stage by hand, exactly as something that is NOT
        # checkpoint.py handoff would: no handoff.txt, no block. This is the
        # only shape that may kill the run.
        import json as _json
        path = os.path.join(WORK, "checkpoint.json")
        row = _json.load(open(path))
        row["stage"] = {"triage": "draft", "draft": "repair"}.get(row["stage"], "done")
        open(path, "w").write(_json.dumps(row))
        say("...moved on without telling anyone.")
        continue
    if st == "draft":                       # a real report before handing off
        open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
            "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
    if MODE == "silent_advance":
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                        "--done", st], capture_output=True)
        say("...done, but I said nothing useful.")
        continue
    out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                          "--done", st], capture_output=True, text=True)
    say(out.stdout or ("handoff failed: " + out.stderr))
