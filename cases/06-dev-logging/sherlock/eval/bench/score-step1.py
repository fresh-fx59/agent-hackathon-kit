#!/usr/bin/env python3
"""score-step1.py — grade a Step-1 working list against an answer key. No model, no judge.

    python3 score-step1.py --key answer-key-bluesky.json --worklist work/worklist.tsv \
                          [--label logmap/bluesky] [--windows 0,10,50]

WHY THIS EXISTS
---------------
A skill's Step 1 decides what the model will spend its context on.  If the
working list never points at the intrusion, no later prose can recover it, and
the run's recall number tells you about the tool, not the model.  This scorer
measures that in isolation and for free:

  defect coverage   how many REAL defects have >= 1 proof location addressed
  anchor recall     how many of the key's proof locations are addressed
  decoy coverage    how many RED HERRINGs the list also nominates
  rows on nothing   rows pointing at neither -- the context they will burn

A row is "addressing" an anchor when it names the same file and a line inside
[line_start - W, line_end + W].  W matters because a working-list row is an
ADDRESS to open, not the evidence itself: the skill tells the model to read
around it (v11 says offset ~N-20/limit 60; v12 tightened that to +-10).  All
three windows are printed so nobody has to trust one.

Decoy coverage is reported, never subtracted.  A Step 1 that surfaces a decoy is
not wrong -- the skill's job is then to reject it with a base-rate number.  What
matters is the RATIO: real defects found vs decoys nominated.
"""
import argparse
import collections
import json
import os
import re
import sys

ROW_REF = re.compile(r"^([^\t]+):(\d+)$")


def load_key(path):
    key = json.load(open(path, encoding="utf-8"))
    real, herring = [], []
    for d in key.get("defects", []):
        title = d.get("title") or ""
        anchors = []
        for pl in d.get("proof_locations") or []:
            f = pl.get("file")
            a = pl.get("line_start")
            b = pl.get("line_end", a)
            if f and a:
                anchors.append((f, int(a), int(b or a)))
        entry = {"id": d.get("id"), "title": title, "anchors": anchors}
        (herring if title.strip().upper().startswith("RED HERRING") else real).append(entry)
    return real, herring, key


def load_worklist(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        cells = line.rstrip("\n").split("\t")
        if len(cells) < 4:
            continue
        rid, ref = cells[0], cells[3]
        m = ROW_REF.match(ref.strip())
        if not m:
            continue
        rows.append((rid, m.group(1), int(m.group(2))))
    return rows


def score(real, herring, rows, window):
    by_file = collections.defaultdict(list)
    for rid, f, ln in rows:
        by_file[f].append((ln, rid))

    def hits(anchors):
        out = []
        for f, a, b in anchors:
            lo, hi = a - window, b + window
            for ln, rid in by_file.get(f, ()):
                if lo <= ln <= hi:
                    out.append(((f, a, b), rid))
                    break
        return out

    res = {"window": window, "rows": len(rows)}
    real_rows, herring_rows = set(), set()
    covered, anchors_hit, anchors_total = 0, 0, 0
    for d in real:
        h = hits(d["anchors"])
        anchors_total += len(d["anchors"])
        anchors_hit += len(h)
        if h:
            covered += 1
        real_rows.update(rid for _, rid in h)
    res["real_covered"], res["real_total"] = covered, len(real)
    res["anchors_hit"], res["anchors_total"] = anchors_hit, anchors_total

    hcov = 0
    for d in herring:
        h = hits(d["anchors"])
        if h:
            hcov += 1
        herring_rows.update(rid for _, rid in h)
    res["herring_covered"], res["herring_total"] = hcov, len(herring)
    res["rows_on_real"] = len(real_rows)
    res["rows_on_herring"] = len(herring_rows - real_rows)
    res["rows_on_nothing"] = len(rows) - len(real_rows | herring_rows)
    return res


def per_defect(real, herring, rows, window):
    by_file = collections.defaultdict(list)
    for rid, f, ln in rows:
        by_file[f].append((ln, rid))
    out = []
    for d in real + herring:
        found = []
        for f, a, b in d["anchors"]:
            for ln, rid in by_file.get(f, ()):
                if a - window <= ln <= b + window:
                    found.append("%s@%s:%d" % (rid, os.path.basename(f), ln))
                    break
        out.append((d["id"], "HERRING" if d in herring else "REAL",
                    len(found), len(d["anchors"]), ",".join(sorted(set(found))[:4])))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--worklist", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--windows", default="0,10,50")
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)

    real, herring, _ = load_key(args.key)
    rows = load_worklist(args.worklist)
    label = args.label or os.path.basename(args.worklist)
    print("== %s ==" % label)
    print("   key: %d real defects (%d anchors) + %d red herrings · worklist: %d rows"
          % (len(real), sum(len(d["anchors"]) for d in real), len(herring), len(rows)))
    for w in [int(x) for x in args.windows.split(",")]:
        r = score(real, herring, rows, w)
        print("   W=%-3d real %2d/%-2d  anchors %3d/%-3d  decoys %d/%-d"
              "   rows: on-real %3d · on-decoy %3d · on-nothing %3d"
              % (w, r["real_covered"], r["real_total"], r["anchors_hit"],
                 r["anchors_total"], r["herring_covered"], r["herring_total"],
                 r["rows_on_real"], r["rows_on_herring"], r["rows_on_nothing"]))
    if args.detail:
        print("   per defect (W=10):")
        for did, kind, got, tot, where in per_defect(real, herring, rows, 10):
            mark = "ok " if got else "   "
            print("     %s %-8s %-4s %d/%d %s" % (mark, did, kind, got, tot, where))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
