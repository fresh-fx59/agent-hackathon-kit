#!/usr/bin/env python3
"""Merge knowledge/SKILL-SECTION.md into a SKILL.md the way a human would.

    merge-skill.py <base SKILL.md> <SKILL-SECTION.md> > merged SKILL.md

Naive appending buries "Шаг 0" behind the report format and the model never runs
it — measured on 2026-07-28, run 1: the agent produced a perfect RCA and ignored
the knowledge base entirely. Order in a procedure is load-bearing, so the merge
respects it:

  * «Шаг 0» goes BEFORE «Шаг 1. Карта» — reading the knowledge base is the first
    thing an investigation does.
  * «Шаг 7» + the human gate go AFTER «Формат отчёта», before the hard rules.
  * one extra numbered item is spliced into the report format list, so the
    mandatory `ЗНАНИЯ:` line is part of the contract the model is already reading.

Falls back to appending (with a warning on stderr) if the base skill does not
have the expected anchors, so it can never produce an empty or broken skill.
"""
import re
import sys

ANCHOR_STEP1 = "### Шаг 1. Карта"
ANCHOR_RULES = "## Правила, которые нельзя нарушать"
ANCHOR_ITEM7 = re.compile(r"^7\. \*\*Чего я не знаю\*\*.*$", re.M)

ITEM8 = ("8. **ЗНАНИЯ** — ровно одна строка о базе знаний: что применено, "
         "что\n   предложено, или почему предлагать нечего. Она есть всегда.")


def main():
    base = open(sys.argv[1], encoding="utf-8").read()
    section = re.sub(r"<!--.*?-->", "", open(sys.argv[2], encoding="utf-8").read(),
                     flags=re.S).strip()

    cut = section.find("## Шаг 7.")
    if cut < 0:
        print("merge: не нашёл «## Шаг 7.» в секции", file=sys.stderr)
        print(base + "\n\n" + section)
        return 0
    part_a, part_b = section[:cut].strip(), section[cut:].strip()

    if ANCHOR_STEP1 not in base or ANCHOR_RULES not in base:
        print("merge: в базовом SKILL.md нет ожидаемых якорей — дописываю в конец",
              file=sys.stderr)
        print(base + "\n\n" + part_a + "\n\n" + part_b)
        return 0

    out = base.replace(ANCHOR_STEP1, part_a + "\n\n" + ANCHOR_STEP1, 1)
    out = ANCHOR_ITEM7.sub(lambda m: m.group(0) + "\n" + ITEM8, out, count=1)
    out = out.replace(ANCHOR_RULES, part_b + "\n\n" + ANCHOR_RULES, 1)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
