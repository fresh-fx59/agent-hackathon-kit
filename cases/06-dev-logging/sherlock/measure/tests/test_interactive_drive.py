#!/usr/bin/env python3
"""The interactive driver is proven on a stand-in BEFORE it spends money.

AGENTS.md: never ask the operator to run something you have not proven works, and
reproduce it on throwaway data first. A paid interactive run costs real tokens and
takes hours, so the driver's mechanics — pty, typed slash commands, the
disk-triggered stage loop, and every named terminal — are established here against
fixtures/fake_qwen.py, which mimics the four target behaviours that matter:
`/clear` forgets the loaded skill, the stage is only known from
work/checkpoint.json, the handoff block is printed, and `/clear` can be refused
while background work is alive.

The four failure modes are tested as first-class outcomes, because a driver that
reports success on a stalled session is worse than no driver.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(MEASURE)
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v40", "tools", "checkpoint.py")
# v40's checkpoint.py has no --partial flag. Task 7 added it in v43 — used only
# by the threshold-handoff scenario below, which needs `handoff --partial` to
# actually advance boundary_seq the way the real arm would.
CHECKPOINT_V43 = os.path.join(SHERLOCK, "skills", "v43", "tools", "checkpoint.py")
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def scenario(mode, budget=25, checkpoint=CHECKPOINT, ledger=None, threshold=None,
             extra_env=None):
    root = tempfile.mkdtemp(prefix="drive-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, checkpoint, "init", "--work", work],
                   capture_output=True)
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": checkpoint,
                "FAKE_MODE": mode, "PYTHONUNBUFFERED": "1"})
    if extra_env:
        env.update(extra_env)
    transcript = os.path.join(root, "transcript.log")
    events = os.path.join(root, "events.jsonl")
    argv = [sys.executable, DRIVER, "--work", work, "--cwd", root,
            "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
            "--events", events, "--stage-budget-s", str(budget),
            "--settle-s", "0.8"]
    if ledger is not None:
        argv += ["--ledger", ledger]
    if threshold is not None:
        argv += ["--threshold", str(threshold)]
    argv += ["--", sys.executable, "-u", FAKE]
    p = subprocess.run(argv, capture_output=True, text=True, env=env,
                       timeout=budget * 4)
    rows = [json.loads(l) for l in open(events, encoding="utf-8")] \
        if os.path.exists(events) else []
    return p, work, rows, open(transcript, "rb").read().decode("utf-8", "replace")


def main():
    check("the driver exists", os.path.exists(DRIVER))
    if not os.path.exists(DRIVER):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1

    p, work, rows, text = scenario("happy")
    kinds = [r["event"] for r in rows]
    check("a full three-stage run exits 0", p.returncode == 0,
          "rc=%d kinds=%s stderr=%s" % (p.returncode, kinds, p.stderr[-400:]))
    check("it reached stage=done on disk",
          json.load(open(os.path.join(work, "checkpoint.json")))["stage"] == "done")
    check("it recorded every stage advance",
          [r["detail"] for r in rows if r["event"] == "stage_advanced"]
          == ["draft", "repair", "done"],
          [r for r in rows if r["event"] == "stage_advanced"])
    check("it re-typed the skill command after each /clear — the loaded skill "
          "does not survive one", text.count("Base directory for this skill") >= 3,
          text.count("Base directory for this skill"))
    check("the transcript is kept for review", len(text) > 200)
    check("the stand-in never answered without a loaded skill",
          "I have no skill loaded" not in text, text[-400:])

    # REGRESSION, caught on the first live rehearsal against real qwen: the arm
    # creates checkpoint.json partway THROUGH the triage stage, so a driver that
    # treats any change of the stage string as an advance reads `None -> triage`
    # as a finished stage and then reports NO_HANDOFF_BLOCK on a healthy run.
    sys.path.insert(0, MEASURE)
    import importlib.util
    spec = importlib.util.spec_from_file_location("idrive", DRIVER)
    idrive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(idrive)
    check("None -> triage is NOT an advance",
          not (idrive.stage_index("triage") > idrive.stage_index(None)))
    check("triage -> draft IS an advance",
          idrive.stage_index("draft") > idrive.stage_index("triage"))
    check("a stage name the machine does not know cannot look like progress",
          idrive.stage_index("nonsense") == -1)

    p, work, rows, text = scenario("clear_refused")
    check("a refused /clear is reported as CLEAR_REFUSED, not as success",
          p.returncode == 6 and "CLEAR_REFUSED" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))

    # TWO SIGNALS, TWO DIFFERENT QUESTIONS — the correction the free-lane
    # rehearsal forced at 10:11:41, when a run whose stage machine had worked
    # perfectly was killed for a block that a repainting TUI had simply scrolled
    # away. `work/handoff.txt` proves the boundary was taken THROUGH the
    # contract, and that is the terminal. Whether the block also reached the
    # SCREEN is a question about the skill's obedience: measured per boundary,
    # reported, never fatal — a TUI is a poor witness and judging on it fails
    # obedient runs.
    p, work, rows, text = scenario("silent_advance")
    kinds = [r["event"] for r in rows]
    check("a stage that hands off but shows nothing on screen still RUNS",
          p.returncode == 0, "rc=%d %s" % (p.returncode, kinds))
    check("...and every such boundary is recorded as not shown",
          kinds.count("handoff_block_not_shown") == 3, kinds)
    p, work, rows, text = scenario("forged_advance")
    check("a stage advanced WITHOUT checkpoint.py handoff is NO_HANDOFF_BLOCK",
          p.returncode == 5
          and "NO_HANDOFF_BLOCK" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))
    p2, work2, rows2, text2 = scenario("happy")
    check("an obedient run records the block ON SCREEN at every boundary",
          [r["event"] for r in rows2].count("handoff_block_on_screen") == 3,
          [r["event"] for r in rows2])

    p, work, rows, text = scenario("die")
    check("a child that exits early is DIED, not a pass",
          p.returncode == 3 and "DIED" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))

    p, work, rows, text = scenario("stall", budget=8)
    check("a stage that never advances is STAGE_TIMEOUT",
          p.returncode == 4 and "STAGE_TIMEOUT" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))

    # --- the ledger threshold forces a partial handoff -----------------------
    # A ledger whose last completed call is over the threshold must make the
    # driver type `handoff --partial <stage>` and then WAIT for boundary_seq on
    # disk before it sends /clear. Clearing first would throw away the state the
    # checkpoint is supposed to carry.
    tmp = tempfile.mkdtemp(prefix="ledger-")
    led = os.path.join(tmp, "upstream.jsonl")
    with open(led, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"usage": {"prompt_tokens": 100}}) + "\n")
        fh.write(json.dumps({"usage": {"prompt_tokens": 210000}}) + "\n")
    check("ledger_prompt_tokens reads the last completed call",
          idrive.ledger_prompt_tokens(led) == 210000,
          idrive.ledger_prompt_tokens(led))
    check("a missing ledger reads 0, not an exception",
          idrive.ledger_prompt_tokens(os.path.join(tmp, "nope.jsonl")) == 0,
          idrive.ledger_prompt_tokens(os.path.join(tmp, "nope.jsonl")))

    # END TO END: seed the ledger above threshold BEFORE the driver even
    # starts, so the very first poll must cross it. An obedient fake target
    # (fake_qwen.py's generic `handoff --partial ` handler) then really calls
    # checkpoint.py handoff --partial and boundary_seq advances on disk — the
    # existing batch_boundary path is what must then send /clear.
    #
    # This static ledger is never appended to during the run (nothing here
    # plays proxy), so Task 2's post-/clear verification (first_fresh_after)
    # would otherwise find no `kind == "call"` row newer than the reseed and
    # block for its full verification window (>=60s) at each of the three
    # reseeds — blowing this scenario's `budget * 4` subprocess timeout. Line
    # 1 is a synthetic "already fresh" call dated a day in the future, so
    # first_fresh_after matches it immediately at every reseed regardless of
    # wall-clock time; line 2 keeps the ledger over threshold (it is LAST, so
    # ledger_prompt_tokens — which wants the last, not the first, usage row —
    # still reads 300000). Never weaken the driver's check: it is the fixture
    # that must speak the v44 ledger schema now.
    far_future_ms = int(time.time() * 1000) + 24 * 3600 * 1000
    with open(led, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "call", "ts_ms": far_future_ms,
                             "usage": {"prompt_tokens": 5},
                             "messages_count": 2,
                             "session_id": "s-fresh"}) + "\n")
        fh.write(json.dumps({"kind": "call", "ts_ms": int(time.time() * 1000),
                             "usage": {"prompt_tokens": 300000},
                             "messages_count": 400,
                             "session_id": "s-stale"}) + "\n")
    p, work, rows, text = scenario("threshold_then_happy",
                                   checkpoint=CHECKPOINT_V43,
                                   ledger=led, threshold=169331)
    kinds = [r["event"] for r in rows]
    check("every threshold_handoff is immediately followed by batch_boundary "
          "— the latch fires once per crossing, not once per poll",
          [kinds[i + 1] for i, k in enumerate(kinds[:-1])
           if k == "threshold_handoff"]
          == ["batch_boundary"] * kinds.count("threshold_handoff"),
          kinds)
    check("the ledger stays over threshold the whole run, so every batched "
          "stage (triage, draft, repair) needed its own crossing — the fake "
          "never completes a stage on its own in this mode",
          kinds.count("threshold_handoff") == 3, kinds)
    check("no /clear was typed before boundary_seq advanced on disk — the "
          "checkpoint must be durable before the driver clears",
          text.index("handoff --partial")
          < text.index("Starting a new session, resetting chat"),
          "handoff@%r clear@%r"
          % (text.find("handoff --partial"),
             text.find("Starting a new session, resetting chat")))
    check("the run still finishes: the threshold handoff did not strand it",
          p.returncode == 0, "rc=%d %s" % (p.returncode, kinds))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the interactive driver is proven on a stand-in")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
