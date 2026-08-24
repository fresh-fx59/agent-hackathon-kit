#!/usr/bin/env python3
"""v33: the two named agent definitions must be installable, legal and cheap.

Why this test exists. Measured 2026-08-24 on qwen-code 0.21.1 with a logging
proxy in front of the provider: a trivial request is 83,705 bytes, the same
request with this skill loaded is 152,245 — the skill body is ~68 KB of EVERY
request, because `skill.ts` returns `buildSkillLlmContent()` as an ordinary tool
result that never leaves the history. A named subagent has its own history: a
parent that launched one grew 1,570 bytes for the child's ENTIRE run.

The lever is where the prose lives. An agent definition's Markdown BODY becomes
the CHILD's system prompt (`subagent-manager.ts parseSubagentContent` ->
`promptConfig.systemPrompt` -> `agent-core.ts buildChatSystemPrompt`), while the
PARENT only ever carries `- **name**: description` per agent
(`agent.ts updateDescriptionAndSchema`). So a definition body costs the parent
tens of bytes, and the parent routes on the description line ALONE — which is
why the two descriptions must be distinguishable, and why every one of these
checks is about the file's shape rather than about the model's behaviour.

The child does NOT inherit qwen-code's core system prompt, so each body must be
self-sufficient. Reference scale, qwen's own builtins: general-purpose 15 lines
/ 1,443 chars, Explore 40 / 2,762, statusline-setup 145 / 6,239.
`validation.ts` warns above 10,000 chars of system prompt and above 1,000 chars
of description.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SKILL = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v33"
TOOL = SKILL / "tools" / "brief.py"
NAMES = ("sherlock-triage", "sherlock-draft")

# The literals the python gates parse. They are RUSSIAN on purpose: the report
# is a Russian artefact even though the instructions are English. Anything here
# that gets translated or re-spelled silently fails citecheck/triagecheck.
RU_LITERALS = (
    "## Находки",
    "## Отклонённые кандидаты",
    "## Покрытие",
    "улики:",
    "чем опровергал:",
    "атрибуция:",
    "исход:",
    "успех",
    "попытка",
    "норма",
)

FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def install(dest, *extra):
    return subprocess.run([sys.executable, str(TOOL), "--install-agents", dest] + list(extra),
                          capture_output=True, text=True)


def split_front(text):
    """Frontmatter as a flat dict + the body. No PyYAML: the suite must pass on
    a python3 with nothing installed."""
    m = re.match(r"^---\n(.*?\n)---\n(.*)$", text, re.S)
    if not m:
        return None, text
    front = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if km:
            val = km.group(2).strip()
            if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1]
            front[km.group(1)] = val
    return front, m.group(2)


def cyr_share(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return cyr / float(len(letters))


def main():
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "proj", ".qwen", "agents")

        p = install(dest)
        check("--install-agents exits 0 and creates the directory",
              p.returncode == 0 and os.path.isdir(dest), p.stderr[-300:])

        paths = {n: Path(dest, n + ".md") for n in NAMES}
        check("both definitions are written",
              all(v.is_file() for v in paths.values()), p.stdout)
        # A directory is the ONLY place qwen-code looks, and only for flat .md.
        check("nothing but the two .md files is left behind",
              sorted(os.listdir(dest)) == sorted(n + ".md" for n in NAMES),
              str(sorted(os.listdir(dest))))

        texts = {n: paths[n].read_text(encoding="utf-8") for n in NAMES}
        fronts, bodies = {}, {}

        for n in NAMES:
            front, body = split_front(texts[n])
            fronts[n], bodies[n] = front, body
            check("%s: frontmatter parses" % n, front is not None)
            if front is None:
                continue

            check("%s: name matches the filename" % n, front.get("name") == n,
                  repr(front.get("name")))
            desc = front.get("description", "")
            check("%s: has a description" % n, bool(desc.strip()))
            # validation.ts warns above 1,000 chars.
            check("%s: description is %d chars (< 1000)" % (n, len(desc)),
                  0 < len(desc) < 1000, str(len(desc)))
            # The parent routes on this line alone, so it must say what AND when.
            check("%s: description says what it does and when to use it" % n,
                  "Use it" in desc and "brief" in desc)

            # An explicit allowlist silently drops `skill` and `write_file`, so
            # the field must be ABSENT, not merely permissive.
            check("%s: no tools: key — the child inherits everything" % n,
                  "tools" not in front and not re.search(r"(?m)^tools:", texts[n]),
                  repr(front.get("tools")))
            check("%s: approvalMode is yolo" % n, front.get("approvalMode") == "yolo",
                  repr(front.get("approvalMode")))
            check("%s: maxTurns is set" % n, front.get("maxTurns", "").isdigit(),
                  repr(front.get("maxTurns")))

            # The body IS the child's system prompt. Builtins run 15-145 lines;
            # validation.ts warns past 10,000 chars.
            lines = [ln for ln in body.strip().splitlines()]
            check("%s: body is %d lines (30..60)" % (n, len(lines)),
                  30 <= len(lines) <= 60, str(len(lines)))
            check("%s: body is %d chars (< 10000)" % (n, len(body)),
                  len(body) < 10000, str(len(body)))

            # Instructions in English, artefacts in Russian: the mandated
            # literals are allowed, Russian PROSE is not.
            share = cyr_share(body)
            check("%s: body is English prose (Cyrillic share %.1f%% < 15%%)"
                  % (n, share * 100), share < 0.15, "%.3f" % share)
            for lit in RU_LITERALS:
                check("%s: keeps the Russian literal %r verbatim" % (n, lit),
                      lit in body)
            check("%s: says the report must be in Russian, in English" % n,
                  "RUSSIAN" in body or "Russian" in body)
            check("%s: keeps the verdict letters D/N/X" % n,
                  all(("`%s`" % c) in body for c in ("D", "N", "X")))

            # Self-sufficiency: the child gets no core system prompt.
            check("%s: tells the worker to read its brief file" % n,
                  "brief" in body.lower())
            check("%s: forbids guessing a path" % n,
                  "guess" in body.lower() and "absolute path" in body.lower())
            check("%s: fixes a reply contract" % n,
                  "## Report" in body)

        # It must not quietly re-inline the skill body — that is the whole cost
        # we are removing.
        skill_body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        longest = max((ln for ln in skill_body.splitlines() if len(ln) > 60),
                      key=len, default="")
        check("the bodies copy no long line of SKILL.md",
              all(longest not in b for b in bodies.values()), longest[:60])
        long_lines = [ln.strip() for ln in skill_body.splitlines() if len(ln.strip()) > 70]
        dupes = [ln for ln in long_lines if any(ln in b for b in bodies.values())]
        check("no long SKILL.md line appears in either body", not dupes,
              "; ".join(dupes[:2]))

        # The parent sees ONLY the descriptions, so they must be tellable apart.
        d1 = fronts[NAMES[0]].get("description", "")
        d2 = fronts[NAMES[1]].get("description", "")
        w1 = set(re.findall(r"[a-z]{4,}", d1.lower()))
        w2 = set(re.findall(r"[a-z]{4,}", d2.lower()))
        jac = len(w1 & w2) / float(len(w1 | w2) or 1)
        check("the two descriptions are distinguishable (word overlap %.0f%% < 60%%)"
              % (jac * 100), jac < 0.60, "%.3f" % jac)
        check("each description names its own phase",
              "TRIAGE" in d1 and "DRAFT" in d2,
              "%r / %r" % (d1[:40], d2[:40]))

        # Idempotence: a rerun is a no-op, byte for byte.
        before = {n: paths[n].read_bytes() for n in NAMES}
        p = install(dest)
        check("a rerun exits 0 and changes nothing",
              p.returncode == 0 and all(paths[n].read_bytes() == before[n] for n in NAMES),
              p.stdout + p.stderr)

        # A hand-edited definition is data, not scratch space.
        victim = paths[NAMES[1]]
        victim.write_text(texts[NAMES[1]] + "\nHAND EDIT\n", encoding="utf-8")
        edited = victim.read_bytes()
        p = install(dest)
        check("a modified definition is NOT clobbered without --force",
              p.returncode != 0 and victim.read_bytes() == edited,
              "rc=%d" % p.returncode)
        check("the refusal names the file and points at --force",
              "--force" in (p.stderr + p.stdout) and victim.name in (p.stderr + p.stdout),
              (p.stderr + p.stdout)[-200:])
        check("the untouched sibling stayed byte-identical",
              paths[NAMES[0]].read_bytes() == before[NAMES[0]])

        p = install(dest, "--force")
        check("--force restores it and says what it overwrote",
              p.returncode == 0 and victim.read_bytes() == before[NAMES[1]]
              and victim.name in p.stdout, p.stdout + p.stderr)

        # The new mode must not have broken the brief mode's usage contract.
        p = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True)
        check("brief.py with no arguments is still a usage error",
              p.returncode == 2, "rc=%d" % p.returncode)

    print()
    if FAILED:
        print("✗ agents: %d проверок упало" % len(FAILED))
        return 1
    print("✓ agents: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
