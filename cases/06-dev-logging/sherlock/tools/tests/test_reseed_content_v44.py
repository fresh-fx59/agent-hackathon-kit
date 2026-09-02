#!/usr/bin/env python3
"""The reseed must carry the state, not a pointer to it.

The paid run's reseed user message was byte-identical at all 10 reseeds
(sha1 6701809b69): «ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ <dir> — СТУПЕНЬ <stage>». It
names a directory and a stage. It does not say what was already tried, which
sections exist, or stop re-reading the format spec — which the model then
re-read in 11 of 11 cycles, about 153,000 tokens on one document.

Worklist fixture rows use an empty verdict column for "still open" and
"X closed" for resolved — matching inspect_worklists()'s actual rule (a row
counts RESOLVED when column 2 is non-empty and does not start with `?`) and
the shape tools/tests/test_boundary_history_v44.py already uses, NOT the
"D open" placeholder text a plan draft used, which inspect_worklists would
have counted as resolved.

KNOWN TEST DEBT, disclosed rather than hidden: the second fixture below
reaches `stage=draft` with 7 of 10 worklist rows still open by HAND-EDITING
checkpoint.json after `init()`, not through a real `triage` -> `draft`
transition. That state is UNREACHABLE through the real state machine:
`handoff --done triage` refuses while any row is open (checkpoint.py's
`handoff()`), so a genuine triage->draft transition always leaves
`unresolved == 0`. The hand-edit only exists to exercise `reseed_line()`'s
field-reading (resolved/total, section counts, bytes) with numbers that
differ from each other; it proves nothing about that combination of stage
and worklist state being reachable in a real run.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
CHECKPOINT = os.path.join(SHERLOCK, "skills", "v44", "tools", "checkpoint.py")
SKILL = os.path.join(SHERLOCK, "skills", "v44", "SKILL.md")
BENCH = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


tmp = tempfile.mkdtemp(prefix="reseed-")
work = os.path.join(tmp, "work")
os.makedirs(work)
with open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
    fh.write("# id\tverdict\n")
    for n in range(10):
        fh.write("g%03d\t%s\n" % (n, "X closed" if n < 7 else ""))
with open(os.path.join(work, "report.md"), "w", encoding="utf-8") as fh:
    fh.write("# Отчёт\n\n## Находки\n\nсодержимое\n\n## Покрытие\n\nсодержимое\n")
subprocess.run([sys.executable, CHECKPOINT, "init", "--work", work],
               capture_output=True, text=True)

# BOUNDARY 0 — a brand-new investigation that has read NOTHING yet must
# NOT be told not to re-read: that is exactly the failure the reviewer
# caught (reseed_line() used to append the anti-redo clause
# unconditionally, so a fresh checkpoint at границ пройдено 0 printed «НЕ
# ПЕРЕЧИТЫВАЙ» before anything had ever been read once).
fresh_proc = subprocess.run([sys.executable, CHECKPOINT, "reseed-line",
                             "--work", work], capture_output=True, text=True)
check(fresh_proc.returncode == 0,
      "reseed-line failed on a fresh checkpoint: %s" % fresh_proc.stderr[-300:])
fresh_line = fresh_proc.stdout.strip()
check(not re.search(r"(НЕ ПЕРЕЧИТЫВАЙ|не перечитывай)", fresh_line),
      "a FRESH checkpoint (boundary 0) still carries the anti-redo clause "
      "— it tells a model that has read nothing not to re-read: %r"
      % fresh_line)
check(fresh_line, "reseed-line printed nothing on a fresh checkpoint")
# The fixture wants the stage past triage (7 of 10 rows closed, 3 still
# open — triage itself never completes with rows open) so it can exercise
# the draft stage's own section-count fields. init() never advances a
# stage on its own — only handoff() does, and only once triage is fully
# resolved — so the checkpoint is hand-advanced here to `draft`, exactly
# as if triage had genuinely finished with an unrelated worklist.
import json as _json
ckpt_path = os.path.join(work, "checkpoint.json")
with open(ckpt_path, encoding="utf-8") as fh:
    ckpt = _json.load(fh)
ckpt["stage"] = "draft"
with open(ckpt_path, "w", encoding="utf-8") as fh:
    _json.dump(ckpt, fh, ensure_ascii=False, sort_keys=True)

r2 = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", work,
                     "--done", "draft", "--partial"], capture_output=True,
                    text=True)
check(r2.returncode == 0, "draft partial handoff failed: %s" % r2.stderr[-300:])

proc = subprocess.run([sys.executable, CHECKPOINT, "reseed-line",
                       "--work", work], capture_output=True, text=True)
check(proc.returncode == 0, "reseed-line failed: %s" % proc.stderr[-300:])
line = proc.stdout.strip()

check(line, "reseed-line printed nothing")
check("\n" not in proc.stdout.strip("\n"),
      "reseed-line printed more than one line — the driver types this "
      "straight into a TUI, and a newline would submit it early")
check("draft" in line, "the reseed line does not name the stage: %r" % line)
check(re.search(r"\b7\b", line) and re.search(r"\b10\b", line),
      "the reseed line does not carry resolved/total: %r" % line)
check(re.search(r"\b2\b", line),
      "the reseed line does not carry the sections written: %r" % line)
check("границ" in line.lower(),
      "the reseed line does not carry the boundary count: %r" % line)
# The anti-redo clause is the whole point.
check(re.search(r"(НЕ ПЕРЕЧИТЫВАЙ|не перечитывай)", line),
      "the reseed line has no anti-redo instruction: %r" % line)

skill = open(SKILL, encoding="utf-8").read()
check("They are not conditional" not in skill,
      "SKILL.md still declares STEP 0 unconditional — it re-ingests the corpus "
      "on every reseed")
check(re.search(r"boundary_seq\s*==\s*0|boundary_seq\s*=\s*0|границ пройдено 0",
                skill),
      "SKILL.md does not make STEP 0 conditional on the first boundary")

bench = open(BENCH, encoding="utf-8").read()
check("--reseed-command" in bench,
      "run-bench.sh still passes no reseed at all, so the 60-byte default "
      "template ships")

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
