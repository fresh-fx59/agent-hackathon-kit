#!/usr/bin/env python3
"""v40 is FROZEN — it carries a paid run — and v41 replaces it.

The paid run 20260827T104334Z-v40 is the first interactive, ceiling-obeying run
this project has: 137 ledger rows, 126 accepted calls, 11,727,497 prompt tokens,
50.5 % cache, $0.069907, zero substitutions, and — re-audited with the corrected
gate — NOT ONE request over the 262,000-token ceiling. It did not deliver a report
(the session stalled 18 minutes after an errored turn and was stopped), but every
byte of skills/v40 is part of that ledger, so the repair lands in v41.

Same discipline as V38_FROZEN / V39_FROZEN: pinned on CONTENT, so a stray edit is
caught in the working tree before it can be committed, plus the committed subtree
hash. v41 starts byte-identical and then diverges; this file asserts only what must
stay true forever — v40 does not move, and v41 never LOSES a file (a deleted tool
is how an arm silently stops running a gate).
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
KIT = os.path.normpath(os.path.join(SHERLOCK, "..", "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
FAILED = []

V40_TREE = "bec18d4f018c0e2afd49b26f1d39ef6f7d61b956"
V40_FROZEN = {
    "SKILL.md":                            "4de44d690b731d669c99ea29e1df6ede",
    "reference/bulk-closure.md":           "22fd216deacb4d5d279162f409ef0614",
    "reference/code-and-spec.md":          "3dbfc0ca8f3386b318e868b23d52f1cf",
    "reference/enum-tables.tsv":           "36016a59e25f1964416da3e6fad5131b",
    "reference/report-format.md":          "8179fb57b2a9aca1b8c416ee91b09bb9",
    "reference/tools.md":                  "eb8a2997d621addafd0251a9672984d7",
    "tools/brief.py":                      "12602a59addf285bf08ed6d35a251854",
    "tools/checkpoint.py":                 "eba5484a565949f7fed96decabe89bd9",
    "tools/citecheck.py":                  "ce19642acc419b19f1ff00f2752504f7",
    "tools/cite.py":                       "c0c294122a9c51e95b292824af7d4cd4",
    "tools/covermap.py":                   "a6a6852e59ff95ed552e89f0a96be128",
    "tools/logjoin.py":                    "dd4465b4736215f1f78bd641cd40a264",
    "tools/logmap.py":                     "88bcd7d8a085db93599c9f53275b1640",
    "tools/rollover.py":                   "cf6d9b4ff9f2c751f76a11e4da604c64",
    "tools/stage-corpus.py":               "761eff5c844a11fbd290ff3ec9f0a0de",
    "tools/statecheck.py":                 "960204ec4d953580b9689e675758e0e0",
    "tools/stopcheck.py":                  "5222822a1a3a9e5f0ecc0f81ba8ac7c6",
    "tools/triagecheck.py":                "fef32caed25efd0581a695fb5620d297",
}


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def walk(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            ap = os.path.join(dirpath, name)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            out[rel] = hashlib.md5(open(ap, "rb").read()).hexdigest()
    return out


def main():
    v40 = walk(os.path.join(SKILLS, "v40"))
    drift = []
    for rel, want in sorted(V40_FROZEN.items()):
        got = v40.get(rel)
        if got is None:
            drift.append("%s: DELETED from a frozen arm" % rel)
        elif got != want:
            drift.append("%s: %s != pinned %s" % (rel, got, want))
    for rel in sorted(set(v40) - set(V40_FROZEN)):
        drift.append("%s: NEW file in a frozen arm" % rel)
    check("skills/v40 is byte-identical to the arm that produced the paid "
          "interactive run", not drift, "; ".join(drift))

    tree = subprocess.check_output(
        ["git", "-C", KIT, "rev-parse",
         "HEAD:cases/06-dev-logging/sherlock/skills/v40"]).decode().strip()
    check("skills/v40 committed tree has not moved", tree == V40_TREE, tree)

    v41root = os.path.join(SKILLS, "v41")
    check("skills/v41 exists — the live arm is not a frozen one",
          os.path.isdir(v41root))
    if os.path.isdir(v41root):
        v41 = walk(v41root)
        missing = sorted(set(V40_FROZEN) - set(v41))
        check("skills/v41 keeps every file v40 had (a lost tool is a lost gate)",
              not missing, "missing: " + ", ".join(missing))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ v40 frozen, v41 live")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
