#!/usr/bin/env python3
"""THE CURSOR MUST BE NAMED IN BOTH CHANNELS, or it does not exist.

Sibling of test_brief_agrees_v41.py, and the same harness rule applied to a
different failure: that test proves the two channels do not CONTRADICT each
other; this one proves they actually POINT AT the tool fix 4 built.

Paid run 20260827T150830Z-v41 is why. The context work had succeeded - peak
152,142 of a 262,000 ceiling, 28.4% of the window in use - and the run still
delivered nothing, because the triage children closed 0 of 250 rows while
hand-writing their own TSV parsers. Neither channel had ever named worklist.py.
"""
import os
import re
import subprocess
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
BRIEF = os.path.join(SKILLS, "v41", "tools", "brief.py")
V40 = os.path.join(SKILLS, "v40", "tools", "brief.py")
FAILED = []


def check(name, cond, detail=""):
    print(("\u2713 " if cond else "\u2717 ") + name
          + (("  \u2014 " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def generate(brief_py, arm):
    """Run the real brief.py exactly as the arm does, and read both channels."""
    root = tempfile.mkdtemp(prefix="cursor-")
    work = os.path.join(root, "work")
    corpus = os.path.join(root, "corpus")
    agents = os.path.join(root, ".qwen", "agents")
    os.makedirs(work)
    os.makedirs(corpus)
    open(os.path.join(corpus, "a.log"), "w").write("2026-01-01 INFO ok\n" * 50)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, brief_py, "--work", work, "--corpus", corpus,
                    "--skill-root", os.path.join(SKILLS, arm)],
                   capture_output=True, env=env, check=False)
    subprocess.run([sys.executable, brief_py, "--install-agents", agents],
                   capture_output=True, env=env, check=False)

    def read(path):
        try:
            return open(path, encoding="utf-8").read()
        except OSError:
            return ""
    return (read(os.path.join(work, "brief-triage.md")),
            read(os.path.join(agents, "sherlock-triage.md")))


# THE DEFECT THIS TEST EXISTS FOR, measured on paid run 20260827T150830Z-v41.
# FIX 4 built a cursor - `worklist.py next` hands out a batch without the
# `запись` column no gate reads, and `verdict --from-stdin` writes the
# answers back - and a full pass over 250 rows was measured at 39,427 B against
# 25,060 B for ONE unfinished whole-file read. The child never called it once.
#
# It was not disobedience. Both channels told it to edit the file by hand:
# «Замени его на D … И ЗАПИШИ ФАЙЛ ОБРАТНО» and «write your verdicts into
# it», with `worklist.py` named nowhere. So the `general-purpose` child spent
# 167 turns writing its own parsers - «Now — what IS this "токен"?» - and
# closed ZERO of 250 rows. A tool the model is never told to reach for saves
# nothing, and a cursor is worth exactly what the brief says about it.
CURSOR_CMDS = ("worklist.py next", "worklist.py verdict")


def main():
    brief, agent = generate(BRIEF, "v41")
    check("brief.py still writes work/brief-triage.md", bool(brief.strip()))
    check("brief.py still installs .qwen/agents/sherlock-triage.md",
          bool(agent.strip()))
    if not (brief and agent):
        print("\u2717 FAILED: " + ", ".join(FAILED))
        return 1

    for cmd in CURSOR_CMDS:
        check("the BRIEF names `%s` — the child cannot use a tool it is never "
              "told about" % cmd, cmd in brief, brief[:400])
        check("the SYSTEM PROMPT names `%s` too, because it is the stronger "
              "channel" % cmd, cmd in agent, agent[:400])

    # A hand-edit instruction is not merely redundant beside the cursor: it is
    # the CHEAPER-LOOKING path, so the model takes it. Every sentence that tells
    # the child to WRITE into the worklist must route through the tool.
    WRITE = re.compile(r"\b(write|пиши|запиши|впиши|замени|replace)\b",
                       re.IGNORECASE)
    for label, text in (("brief", brief), ("system prompt", agent)):
        offenders = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            low = sentence.lower()
            if "worklist.tsv" not in low and "леджер" not in low:
                continue
            if not WRITE.search(low):
                continue
            if "worklist.py" in low:
                continue
            offenders.append(sentence.strip()[:140])
        check("the %s never tells the child to write a verdict into "
              "worklist.tsv by hand" % label, not offenders,
              " | ".join(offenders))

    # PROOF THAT THIS TEST BITES. v40 is frozen and carries the defect verbatim:
    # it has no cursor at all, so if these checks had existed the paid run would
    # never have launched.
    if os.path.exists(V40):
        b40, a40 = generate(V40, "v40")
        if b40 and a40:
            check("frozen v40 FAILS the same check — the proof this test bites",
                  not all(c in b40 for c in CURSOR_CMDS),
                  "v40 already named the cursor, so the check proves nothing")

    print(("\u2717 FAILED: " + ", ".join(FAILED)) if FAILED
          else "\u2713 both channels route every worklist read and write through the cursor")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
