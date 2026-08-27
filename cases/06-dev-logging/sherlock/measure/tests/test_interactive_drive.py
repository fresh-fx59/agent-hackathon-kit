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

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(MEASURE)
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v40", "tools", "checkpoint.py")
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def scenario(mode, budget=25):
    root = tempfile.mkdtemp(prefix="drive-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8").write(
        HEADER + "".join(ROW % i for i in range(1, 4)))
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
                   capture_output=True)
    env = dict(os.environ)
    env.update({"FAKE_WORK": work, "FAKE_CHECKPOINT": CHECKPOINT,
                "FAKE_MODE": mode, "PYTHONUNBUFFERED": "1"})
    transcript = os.path.join(root, "transcript.log")
    events = os.path.join(root, "events.jsonl")
    p = subprocess.run(
        [sys.executable, DRIVER, "--work", work, "--cwd", root,
         "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
         "--events", events, "--stage-budget-s", str(budget),
         "--settle-s", "0.8", "--", sys.executable, "-u", FAKE],
        capture_output=True, text=True, env=env, timeout=budget * 4)
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

    p, work, rows, text = scenario("silent_advance")
    check("a stage that advances without printing the block is "
          "NO_HANDOFF_BLOCK", p.returncode == 5
          and "NO_HANDOFF_BLOCK" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))

    p, work, rows, text = scenario("die")
    check("a child that exits early is DIED, not a pass",
          p.returncode == 3 and "DIED" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))

    p, work, rows, text = scenario("stall", budget=8)
    check("a stage that never advances is STAGE_TIMEOUT",
          p.returncode == 4 and "STAGE_TIMEOUT" in [r["event"] for r in rows],
          "rc=%d %s" % (p.returncode, [r["event"] for r in rows]))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the interactive driver is proven on a stand-in")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
