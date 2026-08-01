#!/usr/bin/env python3
"""spend.py — what this eval actually cost, recorded AND burned.

`results.jsonl` only holds runs that produced a measurement. On 2026-08-01 that
showed 5,306,617 tokens while another 5,896,031 had been spent on six runs that
died — more than half the day's spend, invisible in the one file anybody reads.
An arm's recorded cost systematically understates what it cost to obtain it.

    python3 measure/spend.py [--arm v12] [--since 20260801]
"""
import argparse
import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def rows(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm")
    ap.add_argument("--since", help="YYYYMMDD, matched against the run dir stamp")
    a = ap.parse_args()

    def keep(r):
        if a.arm and r.get("arm") != a.arm:
            return False
        if a.since and a.since not in os.path.basename(r.get("run_dir") or ""):
            return False
        return True

    rec = [r for r in rows(os.path.join(HERE, "results.jsonl")) if keep(r)]
    burn = [r for r in rows(os.path.join(HERE, "burned.jsonl")) if keep(r)]
    tr = sum(r.get("input_tokens") or 0 for r in rec)
    tb = sum(r.get("input_tokens") or 0 for r in burn)
    print("recorded : %13s tokens over %d rows" % (f"{tr:,}", len(rec)))
    print("burned   : %13s tokens over %d dead runs" % (f"{tb:,}", len(burn)))
    print("TOTAL    : %13s tokens   (%.0f%% of it bought nothing)"
          % (f"{tr + tb:,}", 100 * tb / (tr + tb) if tr + tb else 0))
    if burn:
        why = collections.Counter(r.get("reason") for r in burn)
        print("\nburn by reason:")
        for k, v in why.most_common():
            n = sum(r.get("input_tokens") or 0 for r in burn if r.get("reason") == k)
            print("  %-22s %2d runs  %13s tokens" % (k, v, f"{n:,}"))
    per = collections.defaultdict(lambda: [0, 0])
    for r in rec:
        per[r.get("case_id")][0] += r.get("input_tokens") or 0
    for r in burn:
        per[r.get("case_id")][1] += r.get("input_tokens") or 0
    if per:
        print("\n%-6s %14s %14s" % ("case", "recorded", "burned"))
        for c in sorted(per):
            print("%-6s %14s %14s" % (c, f"{per[c][0]:,}", f"{per[c][1]:,}"))


if __name__ == "__main__":
    main()
