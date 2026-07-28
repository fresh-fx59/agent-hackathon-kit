#!/usr/bin/env python3
"""Comparison table over the run ledger: baseline vs each skill version.

    python3 eval/report.py            # markdown table, per dataset + aggregate
    python3 eval/report.py --json     # machine-readable, for the presentation

Only the LAST run of each (dataset, arm) pair counts, so re-running a cell
replaces it rather than double-counting.
"""
import json, os, sys, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "runs.jsonl")

def load():
    if not os.path.exists(LEDGER):
        sys.exit("no runs yet: %s" % LEDGER)
    latest = {}
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest[(r.get("dataset"), r.get("arm"))] = r   # later line wins
    return latest

def arms_of(rows):
    seen = {a for (_, a) in rows}
    order = ["none"] + sorted(a for a in seen if a != "none")
    return [a for a in order if a in seen]

def agg(rows, arm, field):
    vals = [r[field] for (d, a), r in rows.items() if a == arm and r.get(field) is not None]
    return st.mean(vals) if vals else None

def fmt(v, spec="%.0f"):
    return "—" if v is None else spec % v

def main():
    rows = load()
    arms = arms_of(rows)
    datasets = sorted({d for (d, _) in rows})

    if "--json" in sys.argv:
        out = {"datasets": datasets, "arms": arms,
               "cells": {"%s|%s" % k: {kk: vv for kk, vv in v.items() if kk != "answer"}
                         for k, v in rows.items()}}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    print("# Sherlock — измерения\n")
    print("Модель: %s\n" % (next(iter(rows.values())).get("model") or "?"))

    # ---- per dataset ----
    print("## По датасетам\n")
    hdr = "| датасет | арм | шагов | время, с | ответ, симв. | ссылок file:line | покрытие файлов |"
    print(hdr); print("|" + "---|" * 7)
    for d in datasets:
        for a in arms:
            r = rows.get((d, a))
            if not r:
                continue
            print("| %s | **%s** | %s | %s | %s | %s | %s/%s |" % (
                d, a, r.get("turns"), r.get("duration_s"), r.get("answer_chars"),
                r.get("line_refs"), r.get("files_cited"), r.get("files_in_corpus")))
    print()

    # ---- aggregate ----
    print("## Сводно (среднее по датасетам)\n")
    print("| арм | шагов | время, с | ответ, симв. | ссылок file:line |")
    print("|---|---|---|---|---|")
    base = {}
    for a in arms:
        vals = (agg(rows, a, "turns"), agg(rows, a, "duration_s"),
                agg(rows, a, "answer_chars"), agg(rows, a, "line_refs"))
        if a == "none":
            base = dict(zip(("turns", "dur", "chars", "refs"), vals))
        print("| **%s** | %s | %s | %s | %s |" % (
            a, fmt(vals[0], "%.1f"), fmt(vals[1]), fmt(vals[2]), fmt(vals[3], "%.1f")))

    # ---- deltas vs baseline ----
    if base and len(arms) > 1:
        print("\n## Прирост относительно baseline (без навыка)\n")
        print("| арм | ответ | ссылок на строки | шагов | время |")
        print("|---|---|---|---|---|")
        for a in arms[1:]:
            def rel(field, key):
                v, b = agg(rows, a, field), base[key]
                if not v or not b:
                    return "—"
                return "×%.1f" % (v / b) if b else "—"
            print("| **%s** | %s | %s | %s | %s |" % (
                a, rel("answer_chars", "chars"), rel("line_refs", "refs"),
                rel("turns", "turns"), rel("duration_s", "dur")))

if __name__ == "__main__":
    main()
