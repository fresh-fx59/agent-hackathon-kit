#!/usr/bin/env python3
"""score-ait.py — score a Step-1 worklist against AIT-LDS v2.1's shipped labels.

AIT-LDS is the only corpus in this project whose ground truth is addressed the way
`citecheck` and `score-step1.py` already work: by PHYSICAL LINE NUMBER.

    labels/<host>/logs/<path>   ->  {"line": 145, "labels": ["escalate", ...], ...}
    gather/<host>/logs/<path>   ->  the log itself

So scoring needs no authoring and no judge. A worklist row cites `file:line`; the
label file says whether that physical line is part of the attack.

    score-ait.py --root <ait extracted dir> --worklist work/worklist.tsv \
                 [--label NAME] [--scope <host>] [--detail]

WHY PER-FILE AND NOT POOLED. The needle-to-haystack ratio varies by three orders of
magnitude across the eight labelled files:

    inet-firewall/logs/dnsmasq.log        54,035 labelled of 275,900
    intranet_server/.../apache2-access.2   7,695 labelled of   8,530
    intranet_server/logs/auth.log              8 labelled of      272
    intranet_server/logs/audit/audit.log       9 labelled of    2,316
    internal_share/logs/audit/audit.log        2 labelled of      732

The DNS exfiltration is loud. The privilege escalation that made it possible is
eight lines in 272. A pooled recall number is dominated by dnsmasq and would
flatter every arm — an arm that scores well there and misses `auth.log` has found
the noise and missed the intrusion. So every number here is reported per file, and
the summary counts FILES TOUCHED, not lines matched.

TWO COLUMNS, BECAUSE THEY ARE TWO QUANTITIES
--------------------------------------------
Until 2026-08-18 this tool printed ONE number per file, built by appending an entry
per worklist ROW that landed on a labelled line — and that number was read as
recall. It is not. Two rows citing the same labelled line counted twice, so the v15
run printed

    intranet_server/logs/auth.log       8      272    2.9%  **9**

nine, in the column next to its own denominator of eight. So the two quantities now
have two columns and two names:

  LINES COVERED   distinct labelled lines the worklist put in front of the model,
                  over the labelled lines in that file. This is the recall number.
                  It cannot exceed its denominator.
  ROWS ON ATTACK  how many worklist rows landed on the attack. Genuine information
                  — it says how much of a finite budget went to the intrusion
                  rather than to noise — and unbounded by the label count.

A cited RANGE covers every labelled line inside it, not just the first. A worklist
row is an ADDRESS the model is told to open and read around, so `auth.log:140-160`
really does put all eight labelled lines in front of it. The old walk broke at the
first match and credited such a row with 1. The row is still ONE row on attack.

ATTRIBUTION IS HOST-QUALIFIED, AND NEVER GUESSED
------------------------------------------------
The old match clause was

    if path.endswith(rel) or rel.endswith(path.split("/")[-1]):

whose second half is a bare BASENAME test. This testbed ships `auth.log` on 10
hosts, `logs/audit/audit.log` on 7, and `apache2/error.log.2` on 3 — so a row citing
one machine was scored as a hit on another machine's labels. Measured on
`_runs/ait-all-v15-x`: the 9th row on `intranet_server/logs/auth.log` was really
`mail/logs/auth.log:146`, and ALL FIVE credits on
`intranet.smith.russellmitchell.com-error.log.2` came from
`intranet_server/logs/apache2/error.log.2` and `cloud_share/logs/apache2/error.log.2`.
The first half was unanchored too: `...com-error.log.2`.endswith(`error.log.2`) is
true, and so would be `.../notauth.log`.endswith(`auth.log`).

So there is no basename fallback and no ranking of candidates. A citation is
attributed only when it resolves to EXACTLY ONE label file, by equality or by a
suffix match anchored on `/`. Anything that fits two or more is counted
UNATTRIBUTED and printed. A wrong attribution is worse than a missing one: it is a
confident number pointing at the wrong evidence.

A run pointed at a single host cites host-relative paths (`logs/auth.log`), which
are ambiguous against a whole-testbed label tree by construction. That is what
`--scope <host>` is for — the run's scope is metadata to be DECLARED, not inferred
from a path.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

CITE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_./+-]*\.(?:log|json|tsv|csv|txt|out|[0-9]+)"
                  r"(?:\.gz)?):(\d+)(?:-(\d+))?")

# How far a cited RANGE is walked. The widest citation in any published AIT run
# spans 80 lines (`ait-all-v15-x`, one row), and the clamp exists only so a
# malformed `file:1-9999999` cannot cost a scan of the label dict. It used to be
# 40 — undocumented, and narrower than a real row.
MAX_SPAN = 200


def load_labels(root):
    """-> {relative log path under gather/<host>/ : {line: [labels]}}"""
    out = {}
    lab_root = os.path.join(root, "labels")
    for dirpath, _dirs, files in os.walk(lab_root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, lab_root).replace(os.sep, "/")
            marks = {}
            try:
                with open(p, errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        if "line" in d:
                            marks[int(d["line"])] = d.get("labels") or []
            except OSError:
                continue
            if marks:
                out[rel] = marks
    return out


def total_lines(root, rel):
    p = os.path.join(root, "gather", rel)
    if not os.path.exists(p):
        return 0
    n = 0
    try:
        with open(p, errors="replace") as fh:
            for _ in fh:
                n += 1
    except OSError:
        return 0
    return n


def candidates(path, rels):
    """Label files a cited path could be, matched only on `/` boundaries.

    Both directions are legitimate and both are anchored:
      * the citation carries more of the tree than the label root
        (`gather/intranet_server/logs/auth.log` -> `intranet_server/logs/auth.log`)
      * the citation is host-relative and the label path carries the host
        (`logs/auth.log` -> `intranet_server/logs/auth.log`)
    There is deliberately NO basename fallback: see the module docstring.
    """
    path = path.replace("\\", "/").lstrip("./")
    return sorted(r for r in rels
                  if r == path or path.endswith("/" + r) or r.endswith("/" + path))


def attribute(path, rels, scope=""):
    """-> (rel, None) | (None, "ambiguous") | (None, "unlabelled")

    With --scope, the scoped reading is tried FIRST and wins outright if it
    resolves; that is the declared truth about where the worklist came from.
    """
    for p in ([scope.strip("/") + "/" + path.lstrip("./")] if scope else []) + [path]:
        c = candidates(p, rels)
        if len(c) == 1:
            return c[0], None
        if len(c) > 1:
            return None, "ambiguous"
    return None, "unlabelled"


def score(labels, rows, scope=""):
    """-> (covered {rel: set(line)}, rows_on {rel: int}, stats)"""
    covered = defaultdict(set)      # rel -> distinct labelled lines put in front
    rows_on = Counter()             # rel -> worklist rows that landed on it
    cited_files = Counter()
    stats = Counter()
    resolved = {}                   # cited path -> (rel, why) — one walk per path
    for r in rows:
        m = CITE.search(r)
        if not m:
            stats["no_citation"] += 1
            continue
        path, lo = m.group(1), int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        cited_files[path] += 1
        if path not in resolved:
            resolved[path] = attribute(path, labels.keys(), scope)
        rel, why = resolved[path]
        if rel is None:
            stats["ambiguous" if why == "ambiguous" else "unlabelled"] += 1
            continue
        marks = labels[rel]
        got = [n for n in range(lo, min(hi, lo + MAX_SPAN) + 1) if n in marks]
        if got:
            covered[rel].update(got)
            rows_on[rel] += 1
    stats["cited_files"] = len(cited_files)
    stats["ambiguous_paths"] = sum(1 for v in resolved.values() if v[1] == "ambiguous")
    return covered, rows_on, stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--worklist", required=True)
    ap.add_argument("--label", default="arm")
    ap.add_argument("--scope", default="",
                    help="host this worklist is relative to, e.g. intranet_server")
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)

    labels = load_labels(args.root)
    if not labels:
        sys.exit("no label files found under %s/labels" % args.root)

    with open(args.worklist, errors="replace") as fh:
        rows = [l for l in fh if not l.startswith("#") and l.strip()]

    covered, rows_on, stats = score(labels, rows, args.scope)

    print("== %s ==%s" % (args.label, ("  [scope: %s]" % args.scope) if args.scope else ""))
    print("   worklist rows: %d · rows with a citation: %d · distinct files cited: %d"
          % (len(rows), len(rows) - stats["no_citation"], stats["cited_files"]))
    print()
    print("   %-52s %8s %7s  %13s %4s  %s"
          % ("labelled file", "total", "share", "lines covered", "of", "rows on attack"))
    touched = 0
    for rel in sorted(labels):
        tot = total_lines(args.root, rel)
        nlab = len(labels[rel])
        cov = len(covered.get(rel, ()))
        nrows = rows_on.get(rel, 0)
        if cov:
            touched += 1
        share = ("%.1f%%" % (100.0 * nlab / tot)) if tot else "?"
        print("   %-52s %8d %7s  %13s %4d  %s"
              % (rel, tot, share, ("**%d**" % cov) if cov else "0", nlab, nrows))
    print()
    print("   FILES TOUCHED: %d of %d labelled files" % (touched, len(labels)))
    print("   LINES COVERED: %d of %d labelled lines"
          % (sum(len(v) for v in covered.values()), sum(len(v) for v in labels.values())))
    print("   ROWS ON ATTACK: %d of %d rows" % (sum(rows_on.values()), len(rows)))
    print("   unattributed citations (ambiguous — fit more than one labelled file, "
          "credited to none): %d rows / %d distinct paths"
          % (stats["ambiguous"], stats["ambiguous_paths"]))
    print("   citations on files with no labels at all: %d rows" % stats["unlabelled"])

    names = Counter()
    for rel, lines in covered.items():
        for n in lines:
            for l in labels[rel][n]:
                names[l] += 1
    if names:
        print("   attack labels reached: " + ", ".join(
            "%s×%d" % (k, v) for k, v in names.most_common(12)))
    else:
        print("   attack labels reached: NONE")

    if args.detail:
        print()
        for rel in sorted(covered):
            for n in sorted(covered[rel])[:10]:
                print("     %s:%d  %s" % (rel, n, ",".join(labels[rel][n])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
