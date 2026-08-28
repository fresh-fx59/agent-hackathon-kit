#!/usr/bin/env python3
"""FIX 5: the sections a TRIAGE session never uses must not be re-paid every /clear.

MEASURED. `/clear` calls `clearLoadedSkills()` (qwen-code 0.22.0), so a staged run
re-pays the whole skill body at every boundary. §6 «Step 3. Links between sources»
(1,848 B) and §7 «Step 4. Checking and delivery» (11,678 B) — 13,526 B — are draft
and verify procedure: a triage session never acts on them.

The pattern to copy already exists inside this skill: `reference/report-format.md`
(23,840 B) is deferred by an explicit instruction «Only now, immediately before
writing the draft, read reference/report-format.md (do not read it at the start)».

WHAT THIS MUST NOT DO. Nothing may be LOST — the draft stage still has to be able to
read every word, and the deferred file must be named at the exact moment it is
needed. And the sections that govern all four stages stay in the body: the automaton,
the delivery rules, the budget, and «Rules you must not break».
"""
import io
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


def body(ver, name="SKILL.md"):
    return io.open(os.path.join(SKILLS, ver, name), encoding="utf-8").read()


def sections(text):
    out = {}
    idx = [(m.start(), m.group(0).strip()) for m in re.finditer(r"^## .*$", text, re.M)]
    for i, (pos, title) in enumerate(idx):
        end = idx[i + 1][0] if i + 1 < len(idx) else len(text)
        out[title] = text[pos:end]
    return out


def main():
    v41 = body("v41")
    secs = sections(v41)
    titles = list(secs)

    moved = [t for t in titles if t.startswith("## 6.") or t.startswith("## 7.")]
    check("§6 and §7 are no longer full sections of the body", not moved,
          moved)

    ref = os.path.join(SKILLS, "v41", "reference", "draft-and-verify.md")
    check("their text lives in reference/draft-and-verify.md",
          os.path.exists(ref))
    if not os.path.exists(ref):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1
    moved_text = io.open(ref, encoding="utf-8").read()

    v40 = body("v40")
    v40secs = sections(v40)
    src = "".join(v40secs[t] for t in v40secs
                  if t.startswith("## 6.") or t.startswith("## 7."))
    check("v40 still HAS those sections in its body — the fix lands in v41 only",
          bool(src.strip()))

    # NOTHING LOST: every command and every parsed literal survives the move.
    cmds = set(re.findall(r"python3 [^\n`]+", src))
    missing = sorted(c for c in cmds if c not in moved_text)
    check("every command from the moved sections is in the reference file",
          not missing, missing[:3])
    literals = ["## Находки", "## Отклонённые кандидаты", "## Покрытие",
                "улики:", "исход:", "СИНТЕЗ НЕ ЗАВЕРШЁН"]
    lost = [lit for lit in literals if lit in src and lit not in moved_text
            and lit not in v41]
    check("every gate-parsed literal survives somewhere the model still reads",
          not lost, lost)

    # NAMED AT THE RIGHT MOMENT: the draft step must point at it.
    check("the DRAFT step tells the model to read draft-and-verify.md",
          "draft-and-verify.md" in v41)
    draft_pos = v41.find("draft-and-verify.md")
    check("...and it is named at draft time, not at the start",
          draft_pos > len(v41) * 0.3,
          "named at byte %d of %d" % (draft_pos, len(v41)))

    # THE ALL-STAGE SECTIONS STAY.
    for keep in ("## MANDATORY AUTOMATON", "## 2. Two delivery rules",
                 "## 9. Budget", "## 10. Rules you must not break"):
        check("%s stays in the body" % keep,
              any(t.startswith(keep) for t in titles), titles)

    # AND THE POINT OF THE EXERCISE.
    saved = len(v40.encode("utf-8")) - len(v41.encode("utf-8"))
    # WHAT THIS NUMBER IS FOR, and why it moved on 2026-08-28.
    #
    # The point was never «10,000» — it is that the body a session re-reads on
    # every `/clear` must stay small, and that moving §6+§7 into
    # reference/draft-and-verify.md actually bought that. It did: 10,629 bytes
    # when the split landed.
    #
    # Then STEP 0 (ingest) added 679 bytes of instruction the model MUST have —
    # without it the skill cannot read an archive or a `.evtx`, which is what a
    # real user hands over — and the saving became 9,980. Shaving working prose
    # to defend a round number would be optimising the test, not the session.
    #
    # So the assertion is now the thing it always meant: an ABSOLUTE CEILING on
    # the body, with the headroom stated out loud. 37,000 bytes leaves ~800 for
    # the next necessary instruction; past that, something must move to
    # reference/ rather than the ceiling moving again.
    BODY_CEILING = 37000
    size = len(v41.encode("utf-8"))
    check("the body stays under the %d-byte ceiling it is re-read at on every "
          "/clear" % BODY_CEILING, size <= BODY_CEILING,
          "body is %d bytes" % size)
    check("the split still pays for itself against v40", saved >= 9000,
          "saved %d bytes (v40 %d -> v41 %d)"
          % (saved, len(v40.encode("utf-8")), size))
    print("   body: v40 %d B -> v41 %d B (%d B off every /clear)"
          % (len(v40.encode("utf-8")), len(v41.encode("utf-8")), saved))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ a triage session no longer pays for the draft procedure")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
