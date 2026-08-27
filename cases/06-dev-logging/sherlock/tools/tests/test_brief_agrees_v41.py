#!/usr/bin/env python3
"""FIX 3: the brief and the child's SYSTEM PROMPT may not contradict each other.

MEASURED on the paid run 20260827T104334Z-v40, in that run's own artefacts.

`work/brief-triage.md` said:
    «оглавление срезов: `worklist-index.tsv` — читай СРЕЗЫ `view-<ось>-NN.tsv`,
     каждый влезает в одно чтение. `worklist.tsv` — леджер для проверок, правки
     пиши в него, но НЕ читай его целиком.»
and `work/worklist-index.tsv` repeated «ЧИТАЙ СРЕЗ, НЕ worklist.tsv».

`.qwen/agents/sherlock-triage.md` — which IS the child's system prompt, read
before the brief it points at — said at step 2:
    «Read the corpus map and the worklist.»
naming no slice and never mentioning the view files at all. Its Notes added
    «Read as much of the corpus as you need. Your context is your own»
which is false in the way that matters: the child's window is the same 262,000
and its prompt is billed on every one of its turns.

Both artefacts are written by the same brief.py, in the same run, for the same
child. The system prompt is the stronger channel, so the expensive behaviour was
not the model being careless — it was obeying the instruction we put in the more
authoritative place. The measured cost: the parent and children between them
paginated `map.txt` and `worklist.tsv` in 25,060-25,072-character pages.

This file is the harness fix: the two generated artefacts CANNOT disagree, because
a convention nobody checks is not a fix.
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
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def generate():
    """Run the real brief.py exactly as the arm does, and read both artefacts."""
    root = tempfile.mkdtemp(prefix="agree-")
    work = os.path.join(root, "work")
    corpus = os.path.join(root, "corpus")
    agents = os.path.join(root, ".qwen", "agents")
    os.makedirs(work)
    os.makedirs(corpus)
    open(os.path.join(corpus, "a.log"), "w").write("2026-01-01 INFO ok\n" * 50)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, BRIEF, "--work", work, "--corpus", corpus,
                    "--skill-root", os.path.join(SKILLS, "v41")],
                   capture_output=True, env=env, check=False)
    subprocess.run([sys.executable, BRIEF, "--install-agents", agents],
                   capture_output=True, env=env, check=False)
    def read(path):
        try:
            return open(path, encoding="utf-8").read()
        except OSError:
            return ""
    return (read(os.path.join(work, "brief-triage.md")),
            read(os.path.join(agents, "sherlock-triage.md")))


def main():
    brief, agent = generate()
    check("brief.py still writes work/brief-triage.md", bool(brief.strip()))
    check("brief.py still installs .qwen/agents/sherlock-triage.md",
          bool(agent.strip()))
    if not (brief and agent):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1

    # 1 — the brief's rule must survive into the stronger channel.
    check("the brief tells the child NOT to read worklist.tsv whole",
          "НЕ читай его целиком" in brief, brief[:300])
    check("the SYSTEM PROMPT names the slice mechanism the brief mandates",
          "view-" in agent or "срез" in agent.lower() or "slice" in agent.lower(),
          "the agent definition never mentions the view slices")
    bad = re.search(r"[Rr]ead the corpus map and the worklist", agent)
    check("the SYSTEM PROMPT no longer says «Read the corpus map and the "
          "worklist» with no slice named", bad is None,
          bad.group(0) if bad else "")

    # 2 — it may not tell the child that reading is free. It is not: the child
    #     inherits the same window and pays for its prompt every turn.
    lie = re.search(r"[Yy]our context is your own", agent)
    check("the SYSTEM PROMPT does not claim the child's context is free",
          lie is None, lie.group(0) if lie else "")
    check("...and it states the real constraint instead",
          re.search(r"same (context )?window|billed|262|каждый ход|every turn",
                    agent) is not None,
          "no statement of the child's actual budget")

    # 3 — THE HARNESS RULE, not a convention: whatever the brief forbids, the
    #     definition must not require. Checked as a pair, so the next person who
    #     edits one of the two files cannot silently reopen the contradiction.
    # Sentence-level, because a word-level lookahead cannot tell «Read the
    # SLICES, never the whole worklist» from «Read the worklist»: the qualifier
    # can sit on either side of the noun. Every sentence that tells the child to
    # READ something and names the worklist must also name the bounded mechanism
    # — a slice, the index, a batch — or forbid the whole-file read outright.
    QUALIFIERS = ("slice", "срез", "view-", "index", "batch", "батч",
                  "do not read", "never", "не читай", "ledger", "леджер")
    offenders = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", agent):
        low = sentence.lower()
        if "worklist" not in low:
            continue
        if not re.search(r"\bread\b", low):
            continue
        if not any(q in low for q in QUALIFIERS):
            offenders.append(sentence.strip()[:120])
    check("every read-the-worklist instruction names a bounded mechanism",
          not offenders, " | ".join(offenders))

    # PROOF THAT THIS TEST BITES, the way test_gates_v36_fail_closed proves its
    # own defects: v40 is frozen and STILL CARRIES the contradiction verbatim, so
    # if these checks had existed they would have caught it before a paid run did.
    v40src = open(os.path.join(SKILLS, "v40", "tools", "brief.py"),
                  encoding="utf-8").read()
    check("v40 still carries «Read the corpus map and the worklist» — the "
          "instruction this fix removes",
          "Read the corpus map and the worklist." in v40src)
    check("v40 still carries «Your context is your own» — the claim that is "
          "false for a child inheriting the same window",
          "Your context is your own" in v40src)

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the brief and the system prompt agree")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
