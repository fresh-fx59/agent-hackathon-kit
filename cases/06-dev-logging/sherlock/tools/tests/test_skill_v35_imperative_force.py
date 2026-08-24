#!/usr/bin/env python3
"""v35: the automaton's delegation must not read as optional, and nothing later
in the file may contradict it.

Measured 2026-08-24 on the 3-file synthetic corpus, same model (`deepseek-v4-flash`
on linkapi), same prompt, same harness — only the skill differed:

  * v33 (English, monolithic): ran `logmap.py`, read all three residue files, ran
    `brief.py`, ran `brief.py --install-agents`, and reached the `sherlock-triage`
    delegation. Compliant.
  * v34 (English, split): ran `logmap.py`, then read `reference/tools.md`,
    `reference/bulk-closure.md` and `reference/report-format.md` ("let me read the
    reference files as required"), and NEVER ran `brief.py`, NEVER installed the
    named agents and NEVER emitted a `subagent_type`. The same shape as the first
    paid winevtx run, which produced no `work/report.md` at all.

So the split did two things the ceiling did not require: it compressed the
delegation step's measured justification down to one sentence, and it added six
fresh `You MUST read <reference>` imperatives that a weak model satisfies INSTEAD
of the procedure. This file locks the repair:

  1. the delegation step is unconditional — the two `brief.py` invocations are not
     gated behind a condition the model has to evaluate first;
  2. no section flatly forbids what the automaton mandates (v34 shipped both
     "Do not spawn subagents and do not fork the investigation" in §3 and
     "**Do not spawn subagents.**" in §10, hundreds of lines AFTER the automaton
     ordered exactly that);
  3. the write-back of verdicts, the report file and the gates are stated as
     obligations, not as description;
  4. reading a reference is explicitly not progress;
  5. the body still fits the 500-line ceiling;
  6. and v34 still carries the defect verbatim — it is a frozen arm with a paid
     result attached, so this file must never be "fixed" by editing it.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SK = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills"
V35 = SK / "v35" / "SKILL.md"
V34 = SK / "v34" / "SKILL.md"
LINE_CEILING = 500
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    body = V35.read_text(encoding="utf-8")
    lines = body.splitlines()

    check("v35 SKILL.md still fits the %d-line ceiling" % LINE_CEILING,
          len(lines) < LINE_CEILING, "%d lines" % len(lines))
    print("  v35 SKILL.md: %d lines / %d bytes" % (len(lines), len(body.encode("utf-8"))))

    # 1. the delegation step is not conditional
    check("the phase-subagent step is no longer gated on 'IF THE agent TOOL EXISTS'",
          "IF THE `agent` TOOL EXISTS" not in body)
    check("the two brief.py commands are announced as unconditional",
          re.search(r"not\s+conditional", body) is not None)
    check("brief.py --install-agents is still documented",
          "--install-agents" in body)
    check("run_in_background: false is still mandatory",
          "`run_in_background: false`" in body and "MANDATORY" in body)

    # 2. nothing contradicts the automaton
    for banned in ("**Do not spawn subagents.**",
                   "Do not spawn subagents and do not fork the investigation"):
        check("no flat prohibition %r" % banned[:44], banned not in body)
    # the mandatory-delegation claim must be made somewhere outside the automaton,
    # because that is where the old contradiction lived
    tail = "\n".join(lines[lines.index("## 3. One thread, and the only two delegations in it"):]) \
        if "## 3. One thread, and the only two delegations in it" in lines else ""
    check("§3 exists and states the delegation is mandatory", bool(tail) and "MANDATORY" in tail[:900])

    # 3. the obligations that the failing run skipped
    obligations = {
        "the verdict write-back is an obligation":
            r"verdict lives in the file|write the verdicts back",
        "triagecheck exit zero closes step 2":
            r"`triagecheck` exits zero|triagecheck.{0,40}exits? zero",
        "no final message without work/report.md on disk":
            r"No final message while `work/report\.md` does not exist",
        "brief.py runs before step 1 every time":
            r"run before step 1, every time",
    }
    for name, pat in obligations.items():
        check(name, re.search(pat, body) is not None)

    # 4. reading a reference is not progress
    check("reading a reference file is explicitly not progress",
          "Reading a reference file is never progress" in body)

    # 5. the fallback ladder is ordered, not a free choice
    check("the fallback ladder is explicit and ordered",
          "fallback ladder" in body and re.search(r"\(1\).{0,400}\(2\).{0,400}\(3\)", body, re.S) is not None)

    # v34 is frozen: the defect it shipped must still be there, or the measured
    # r1 failure stops being attributable to anything.
    old = V34.read_text(encoding="utf-8")
    check("v34 stays frozen with its own defect (IF THE `agent` TOOL EXISTS)",
          "IF THE `agent` TOOL EXISTS" in old)
    check("v34 stays frozen with its own flat prohibition",
          "**Do not spawn subagents.**" in old)
    check("v35 is a distinct arm, not an edit of v34", old != body)

    print()
    if FAILED:
        print("✗ v35 imperative force: %d проверок упало" % len(FAILED))
        return 1
    print("✓ v35 imperative force: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
