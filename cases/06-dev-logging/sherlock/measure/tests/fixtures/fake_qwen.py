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
loaded = False

def say(text):
    sys.stdout.write(text + "\n")
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
        loaded = False
        say("\x1b[2JStarting a new session, resetting chat, and clearing terminal.")
        continue
    if line == "/sherlock":
        loaded = True
        say("Base directory for this skill: /fake/skills/v40")
        continue
    if not loaded:
        say("I have no skill loaded, so I do not know what to do.")
        continue
    if MODE == "die":
        say("boom")
        raise SystemExit(1)
    if MODE == "stall":
        # Working, talkative, and never finishing a stage — the shape of a run
        # that has to be judged by its checkpoint and not by its chatter.
        say("Сейчас посмотрю логи...")
        continue
    st = stage()
    if MODE == "silent_advance":
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                        "--done", st], capture_output=True)
        say("...done, but I said nothing useful.")
        continue
    if st == "draft":                       # a real report before handing off
        open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
            "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
    out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                          "--done", st], capture_output=True, text=True)
    say(out.stdout or ("handoff failed: " + out.stderr))
