#!/usr/bin/env python3
"""v40: the stage boundary an INTERACTIVE user can actually execute.

WHY THIS EXISTS. The corporate lane runs `qwen` interactively — confirmed by the
operator on 2026-08-27 — not `qwen -p`. There is no launcher there, no env block
and no wrapper: only a settings.json, the skill, and a human typing. So the
"separate session per stage" fix that closes the 262,000-token gate cannot be a
process boundary. MEASURED on the passing paid run r6: the single parent session
peaked at a 327,639-token prompt while its two children peaked at 186,812 and
43,279 — no bounded session came near the ceiling.

The interactive boundary is therefore `/clear`, read out of the installed
qwen-code 0.22.0 bundle (packages/cli/src/ui/commands/clearCommand.ts):
`altNames ["reset","new"]`, `geminiClient.resetChat()`, `config.startNewSession()`,
telemetry reset, background tasks aborted, SessionEnd hook fired with reason
`Clear`. It is a genuine fresh context in the same process. TWO mechanics from
the same source shape this contract:

  * it calls `skillTool.clearLoadedSkills()`, so the skill body is DROPPED and
    `/sherlock` must be re-invoked after every clear;
  * it REFUSES while blocking background work is alive ("Stop the current
    session's running background tasks before starting a new session."), so a
    stage may not end with a background task running.

A human who is not told to clear simply keeps typing, and the context keeps
growing. So the last thing a finished stage emits must be a literal,
copy-pasteable block — and this file is what makes that block a contract instead
of a suggestion.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
V39 = os.path.join(SKILLS, "v39", "tools", "checkpoint.py")
V40 = os.path.join(SKILLS, "v40", "tools", "checkpoint.py")
FAILED = []

HEADER = u"# id\tвердикт\tось\tчастота\tзапись\n"
OPEN_ROW = u"W-1\t?\todd\tn=1\talpha beta\n"
CLOSED_ROW = u"W-%d\tN a.log:1 «alpha beta» n=1 фон\todd\tn=1\talpha beta\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(tool, args):
    e = dict(os.environ)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.Popen([sys.executable, tool] + args, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=e)
    out, err = p.communicate()
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def work_dir(rows_closed, rows_open=0, report=None):
    d = tempfile.mkdtemp(prefix="stage-")
    w = os.path.join(d, "work")
    os.makedirs(w)
    body = HEADER + "".join(CLOSED_ROW % i for i in range(1, rows_closed + 1))
    body += OPEN_ROW * rows_open
    open(os.path.join(w, "worklist.tsv"), "w", encoding="utf-8").write(body)
    if report is not None:
        open(os.path.join(w, "report.md"), "w", encoding="utf-8").write(report)
    return w


def main():
    # ── 1. the receipt names the stage, and init seeds it ────────────────────
    w = work_dir(3)
    rc, out, err = run(V40, ["init", "--work", w])
    check("checkpoint init still exits 0", rc == 0, err)
    row = json.loads(out) if rc == 0 else {}
    check("init records a stage, not only a state", row.get("stage") == "triage",
          row)
    on_disk = json.load(open(os.path.join(w, "checkpoint.json"), encoding="utf-8"))
    check("the stage is on DISK, so a cleared session can read it back",
          on_disk.get("stage") == "triage", on_disk)

    # ── 2. handoff refuses to advance out of an UNFINISHED triage ────────────
    w = work_dir(2, rows_open=1)
    run(V40, ["init", "--work", w])
    rc, out, err = run(V40, ["handoff", "--work", w, "--done", "triage"])
    check("handoff --done triage FAILS while a worklist row is still open",
          rc != 0, "rc=%d out=%r" % (rc, out[:200]))
    check("...and says how many rows are left", "1" in (out + err), out + err)

    # ── 3. the happy boundary: triage -> draft, with the literal block ───────
    w = work_dir(262)
    run(V40, ["init", "--work", w])
    rc, out, err = run(V40, ["handoff", "--work", w, "--done", "triage"])
    check("handoff --done triage exits 0 on a fully resolved worklist", rc == 0,
          err)
    block = out
    for must in ["СТУПЕНЬ ЗАВЕРШЕНА: triage", "/clear", "/sherlock",
                 "checkpoint.json", "draft", "262"]:
        check("the printed block contains %r" % must, must in block, block[:400])
    ordered = all(t in block for t in ("/clear", "/sherlock", "draft")) and (
        block.index("/clear") < block.index("/sherlock") < block.rindex("draft"))
    check("the block orders the three actions", ordered, block[:400])
    check("the block warns that /clear refuses while background work runs",
          "фон" in block.lower() or "background" in block.lower(), block[:400])
    check("the block names an ABSOLUTE work path a cleared session can find",
          os.path.realpath(w) in block, block[:400])
    check("the stage advanced on disk to draft",
          json.load(open(os.path.join(w, "checkpoint.json"),
                         encoding="utf-8")).get("stage") == "draft")
    check("the block is also persisted, so it survives a scrolled terminal",
          os.path.exists(os.path.join(w, "handoff.txt")))
    check("the persisted block is byte-identical to what was printed",
          open(os.path.join(w, "handoff.txt"), encoding="utf-8").read() == block)

    # ── 4. resume: the ONE command a re-invoked skill runs ───────────────────
    rc, out, err = run(V40, ["resume", "--work", w])
    check("resume exits 0 and names the stage to run now", rc == 0, err)
    check("resume names draft", "draft" in out, out)
    check("resume does NOT advance the stage",
          json.load(open(os.path.join(w, "checkpoint.json"),
                         encoding="utf-8")).get("stage") == "draft")
    rc, out, err = run(V40, ["resume", "--work", os.path.join(w, "nope")])
    check("resume on a missing work dir fails instead of inventing a stage",
          rc != 0, out + err)

    # ── 5. draft -> done requires a report that is not the placeholder ───────
    w = work_dir(5)
    run(V40, ["init", "--work", w])
    run(V40, ["handoff", "--work", w, "--done", "triage"])
    rc, out, err = run(V40, ["handoff", "--work", w, "--done", "draft"])
    check("handoff --done draft FAILS while report.md is still the placeholder",
          rc != 0, out[:200])
    open(os.path.join(w, "report.md"), "w", encoding="utf-8").write(
        u"# Отчёт Sherlock\n\n## Находки\n\n### Н-1 ...\n")
    rc, out, err = run(V40, ["handoff", "--work", w, "--done", "draft"])
    check("handoff --done draft exits 0 once a real report exists", rc == 0,
          out + err)
    check("draft hands off to repair, the last bounded stage",
          "repair" in out, out[:300])

    # ── 5b. the OTHER writer of checkpoint.json must not lose the stage ─────
    # `triagecheck.py --refresh-checkpoint` rewrites this file too. On v39 it
    # hard-coded `"schema": 1` while checkpoint.py wrote 1 as well, so nobody
    # noticed there were two writers. The moment the stage exists, a refresh
    # that drops it silently rewinds the investigation to triage.
    w2 = work_dir(4)
    run(V40, ["init", "--work", w2])
    run(V40, ["handoff", "--work", w2, "--done", "triage"])
    tc = os.path.join(SKILLS, "v40", "tools", "triagecheck.py")
    run(tc, ["--worklist", os.path.join(w2, "worklist.tsv"),
             "--refresh-checkpoint"])
    after = json.load(open(os.path.join(w2, "checkpoint.json"), encoding="utf-8"))
    check("triagecheck --refresh-checkpoint keeps the stage",
          after.get("stage") == "draft", after)
    ck = open(os.path.join(SKILLS, "v40", "tools", "checkpoint.py"),
              encoding="utf-8").read()
    tct = open(tc, encoding="utf-8").read()
    check("only checkpoint.py names the schema number; triagecheck imports it",
          "SCHEMA = 2" in ck and "ckmod.SCHEMA" in tct
          and '"schema": 1' not in tct, "two writers still disagree")
    check("both writers agree on the schema after a refresh",
          after.get("schema") == 2, after)

    # ── 6. a stage name that is not in the machine aborts ────────────────────
    rc, out, err = run(V40, ["handoff", "--work", w, "--done", "synthesis"])
    check("an unknown stage aborts instead of being treated as the next one",
          rc != 0, out + err)

    # ── 7. v39 is frozen and must NOT have grown the stage machine ──────────
    rc, out, err = run(V39, ["handoff", "--work", w, "--done", "triage"])
    check("v39's checkpoint.py has no handoff — the fix lands in v40 only",
          rc != 0, out[:120])

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the interactive stage boundary is a contract")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
