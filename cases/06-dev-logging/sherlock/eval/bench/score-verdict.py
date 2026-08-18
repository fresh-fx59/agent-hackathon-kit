#!/usr/bin/env python3
"""score-verdict.py — the one number in this project that needs no judge.

Every other score here is a model grading a model. The verdict is not: the report
must end with one of exactly three answers, and the corpus either was compromised
or it was not. So this is string extraction and a comparison, it costs nothing, it
is deterministic, and it can be run on every historical trajectory retroactively.

    compromised          есть доказательство, что чужой получил доступ
    attacked-not-proven  попытки видны, успех не подтверждается
    clean                признаков вмешательства нет

WHY IT IS THE FIRST THING TO MEASURE. Until 2026-08-18 every corpus this project
held was an intrusion, so "always answer compromised" would have scored 100% on
all of them and nothing would have separated an investigator from an alarm. The
`fleet-negative` key fixes that: real production logs under continuous SSH attack
where nothing ever succeeded, whose correct answer is the MIDDLE one. An arm that
cannot distinguish attempt from outcome fails here and nowhere else.

A missing verdict section is NOT scored as wrong-but-close. It is `absent`, and
absent is a delivery defect: the skill's own contract makes the section mandatory.

    score-verdict.py --ledger runs-bench.jsonl --key answer-key-fleet-negative.json \
                     [--dataset fleet-negative] [--arm v14] [--trace 2026...]
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))

_dspec = importlib.util.spec_from_file_location(
    "deliverable", os.path.join(SHERLOCK, "measure", "deliverable.py"))
deliverable = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(deliverable)

_sspec = importlib.util.spec_from_file_location(
    "score_bench", os.path.join(HERE, "score-bench.py"))
SB = importlib.util.module_from_spec(_sspec)
_sspec.loader.exec_module(SB)

# The three answers, in both languages the skill and its prompts use. Ordered
# longest-first inside each verdict so "не доказано" cannot be swallowed by a
# shorter pattern for a different verdict.
PATTERNS = [
    ("attacked-not-proven", [
        r"атаков\w*[^.\n]{0,40}не\s+доказан",
        r"попытк\w*[^.\n]{0,60}не\s+подтвержда",
        r"attacked[-\s]?but[-\s]?(?:un)?decid",
        r"attacked[-\s,]+not\s+prov",
        r"атаковали,?\s+но",
    ]),
    ("compromised", [
        r"скомпрометирован",
        r"компрометац\w+\s+подтвержд",
        r"\bcompromised\b",
    ]),
    ("clean", [
        r"\bчисто\b",
        r"признак\w*\s+вмешательств\w*\s+нет",
        r"\bclean\b",
        r"не\s+скомпрометирован",
    ]),
]

# The report's own verdict section. Everything before it may quote log lines that
# themselves contain the words — an attacker's tooling can literally say
# "compromised" — so the tail is what counts, never the whole document.
SECTION = re.compile(r"(?:^|\n)[^\n]{0,20}(?:ВЕРДИКТ|VERDICT)\b[^\n]*\n?(.*)$",
                     re.IGNORECASE | re.DOTALL)


def extract(report):
    """-> (verdict, how). `how` says which text the answer was read out of, so a
    disagreement can be re-checked by eye instead of argued about."""
    if not (report or "").strip():
        return "absent", "empty report"
    m = SECTION.search(report)
    scope, how = (m.group(1), "verdict section") if m else (report, "WHOLE REPORT — no verdict section found")
    # Only the first ~1200 chars of the section: a long tail of caveats often
    # names the other two verdicts in order to rule them out.
    scope = scope[:1200]
    hits = []
    for name, pats in PATTERNS:
        for p in pats:
            mm = re.search(p, scope, re.IGNORECASE)
            if mm:
                hits.append((mm.start(), name, mm.group(0)))
                break
    if not hits:
        return ("absent", how + ": no verdict wording matched")
    hits.sort()
    # If the section names more than one verdict, the FIRST is the answer and the
    # ambiguity is reported rather than hidden.
    verdict = hits[0][1]
    if len({h[1] for h in hits}) > 1:
        how += " · AMBIGUOUS, matched: " + ", ".join(sorted({h[1] for h in hits}))
    return verdict, how + " · %r" % hits[0][2][:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "runs-bench.jsonl"))
    ap.add_argument("--key", required=True)
    ap.add_argument("--arm")
    ap.add_argument("--trace")
    ap.add_argument("--dataset")
    ap.add_argument("--out", default=os.path.join(HERE, "scores-verdict.jsonl"))
    ap.add_argument("--report", help="score a report FILE directly, no ledger")
    a = ap.parse_args()

    key = json.load(open(a.key, encoding="utf-8"))
    truth = key.get("verdict")
    if not truth:
        sys.exit("%s declares no `verdict` — nothing to score against" % a.key)

    if a.report:
        report = open(a.report, encoding="utf-8", errors="replace").read()
        run = {"arm": a.arm or "?", "dataset": key.get("dataset"),
               "trace_dir": a.report}
    else:
        rows = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
        if not rows:
            sys.exit("no rows in %s" % a.ledger)
        run = SB.select_row(rows, a.arm, a.trace, a.dataset or key.get("dataset"))
        SB.check_key_matches_dataset(key, run)
        report = deliverable.of_row(run)

    got, how = extract(report)
    ok = (got == truth)

    print("dataset : %s" % key.get("dataset"))
    print("arm     : %s" % run.get("arm"))
    print("truth   : %s" % truth)
    print("reported: %s" % got)
    print("read from: %s" % how)
    print()
    if ok:
        print("✓ CORRECT")
    elif got == "absent":
        print("✗ NO VERDICT — the skill's contract makes this section mandatory, so "
              "this is a delivery defect, not a wrong answer")
    elif truth == "attacked-not-proven" and got == "compromised":
        print("✗ CRIED WOLF — real attacks, none of them successful, reported as a "
              "breach. This is the failure the negative control exists to catch.")
    elif truth == "attacked-not-proven" and got == "clean":
        print("✗ MISSED THE ATTACK — 1,957 hostile attempts reported as nothing.")
    elif truth == "compromised" and got != "compromised":
        print("✗ MISSED THE INTRUSION")
    else:
        print("✗ WRONG")

    out = {"dataset": key.get("dataset"), "arm": run.get("arm"),
           "trace_dir": run.get("trace_dir"), "truth": truth, "reported": got,
           "correct": ok, "read_from": how}
    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(out, ensure_ascii=False) + "\n")
    print("wrote %s" % a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
