#!/usr/bin/env python3
"""v40's SKILL.md must TELL the interactive user where the stage ends.

The tool half of this fix is test_stage_handoff_v40.py. This is the other half:
a `handoff` command nobody is instructed to run changes nothing. In the corporate
lane there is no launcher to run it — the model runs it, and the human copies
what it prints.

Three properties, and each one is a defect that has already cost a paid run:
  1. the stage is READ from disk on load, so a cleared session knows where it is
     (v38 died holding a 192-byte stub because nothing re-read its own state);
  2. the handoff block is printed VERBATIM and the turn then ENDS — a model that
     helpfully carries on into the next stage rebuilds the 327,639-token prompt
     this whole fix exists to prevent;
  3. every command runs in the FOREGROUND — `/clear` refuses while a background
     task is alive, so a backgrounded probe silently blocks the boundary.

Also asserted: the skill body stays overwhelmingly English (PR #64 measured the
translation at −33 % of every request), and v39's body did NOT gain any of this.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def body(ver):
    return open(os.path.join(SKILLS, ver, "SKILL.md"), encoding="utf-8").read()


def main():
    v40 = body("v40")
    low = v40.lower()

    check("v40 documents the resume command a cleared session runs first",
          "checkpoint.py resume" in v40)
    check("v40 documents the handoff command that closes a stage",
          "checkpoint.py handoff" in v40)
    for stage in ("triage", "draft", "repair"):
        check("v40 names the %s stage" % stage,
              re.search(r"--done\s+%s|stage\s+%s|`%s`" % (stage, stage, stage),
                        v40) is not None)
    check("v40 tells the model to print the handoff block VERBATIM",
          "verbatim" in low and "handoff" in low)
    check("v40 tells the model to STOP at the boundary instead of continuing",
          re.search(r"do not (start|begin|continue)[^.\n]*next stage", low)
          is not None, "no explicit stop-at-boundary instruction")
    check("v40 forbids background tasks and says why /clear needs that",
          "background" in low and "/clear" in v40)
    check("v40 states the measured reason (the 262,000 ceiling)",
          "262" in v40 and "327" in v40)
    check("v40 says the skill must be re-invoked after /clear (clearLoadedSkills)",
          "/sherlock" in v40 or "re-invoke" in low)

    # The stage rule has to be near the TOP: an instruction 500 lines in is read
    # after the model has already started working. It cannot come FIRST — the
    # commands need the <SKILL_BASE_DIR> paragraph above them to resolve — so
    # the real requirement is the ordering assertion underneath, and 7,000 bytes
    # is where that paragraph ends (measured: 6,326 on the v40 body).
    check("the stage protocol is in the first 7,000 bytes of the body",
          "checkpoint.py resume" in v40[:7000], "found only later in the file")
    check("the stage protocol comes BEFORE STEP 0, so it is read before work "
          "starts", v40.index("checkpoint.py resume") < v40.index("STEP 0"))

    cyr = len(re.findall(r"[А-Яа-яЁё]", v40))
    check("the body stays overwhelmingly English (< 8 %% Cyrillic)",
          cyr < 0.08 * len(v40), "%.1f%%" % (100.0 * cyr / len(v40)))

    v39 = body("v39")
    check("v39 did NOT gain the stage protocol — it is frozen",
          "checkpoint.py handoff" not in v39)

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the skill instructs the interactive boundary")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
