#!/usr/bin/env python3
"""v39 is FROZEN — it carries the first passing paid run — and v40 replaces it.

Run 20260827T0*-v39 r6 is the ONLY paid run this project has that exited 0 with
all three gates clean and a real 481-line report ($0.179994, 210 calls, zero
substitutions). Every byte of skills/v39 is part of that receipt, so a fix that
edits v39 makes the one good result unreproducible. The repair lands in v40.

Pinned on CONTENT, not on `git status`, so a stray edit is caught in the working
tree before it can be committed — the same discipline as V38_FROZEN in
test_gates_v36_fail_closed.py.

v40 starts life byte-identical to v39 and then diverges. This file therefore
asserts only what must stay true forever: v39 does not move, and v40 never
LOSES a file v39 had (a deleted tool is how an arm silently stops running a
gate).
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

V39_TREE_737de8f = "57161294867ef8c7c00ac3e74b344c0f2f9b567a"
V39_FROZEN = {
    "SKILL.md":                          "3e89ab4305d8807186ff8775cd01ad41",
    "reference/bulk-closure.md":         "22fd216deacb4d5d279162f409ef0614",
    "reference/code-and-spec.md":        "3dbfc0ca8f3386b318e868b23d52f1cf",
    "reference/enum-tables.tsv":         "36016a59e25f1964416da3e6fad5131b",
    "reference/report-format.md":        "8179fb57b2a9aca1b8c416ee91b09bb9",
    "reference/tools.md":                "eb8a2997d621addafd0251a9672984d7",
    "tools/brief.py":                    "12602a59addf285bf08ed6d35a251854",
    "tools/checkpoint.py":               "a2a80b108f65d3df2652ba9a2c4997f2",
    "tools/citecheck.py":                "ce19642acc419b19f1ff00f2752504f7",
    "tools/cite.py":                     "c0c294122a9c51e95b292824af7d4cd4",
    "tools/covermap.py":                 "a6a6852e59ff95ed552e89f0a96be128",
    "tools/logjoin.py":                  "dd4465b4736215f1f78bd641cd40a264",
    "tools/logmap.py":                   "88bcd7d8a085db93599c9f53275b1640",
    "tools/rollover.py":                 "cf6d9b4ff9f2c751f76a11e4da604c64",
    "tools/stage-corpus.py":             "761eff5c844a11fbd290ff3ec9f0a0de",
    "tools/statecheck.py":               "960204ec4d953580b9689e675758e0e0",
    "tools/stopcheck.py":                "5222822a1a3a9e5f0ecc0f81ba8ac7c6",
    "tools/triagecheck.py":              "bb9f4307242c8482756b385b0c19a20e",
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
    v39 = walk(os.path.join(SKILLS, "v39"))
    drift = []
    for rel, want in sorted(V39_FROZEN.items()):
        got = v39.get(rel)
        if got is None:
            drift.append("%s: DELETED from a frozen arm" % rel)
        elif got != want:
            drift.append("%s: %s != pinned %s" % (rel, got, want))
    for rel in sorted(set(v39) - set(V39_FROZEN)):
        drift.append("%s: NEW file in a frozen arm" % rel)
    check("skills/v39 is byte-identical to the arm that produced the passing "
          "paid run", not drift, "; ".join(drift))

    tree = subprocess.check_output(
        ["git", "-C", KIT, "rev-parse",
         "HEAD:cases/06-dev-logging/sherlock/skills/v39"]).decode().strip()
    check("skills/v39 committed tree still is 737de8f's tree",
          tree == V39_TREE_737de8f, tree)

    v40root = os.path.join(SKILLS, "v40")
    check("skills/v40 exists — the live arm is not a frozen one",
          os.path.isdir(v40root))
    if os.path.isdir(v40root):
        v40 = walk(v40root)
        missing = sorted(set(V39_FROZEN) - set(v40))
        check("skills/v40 keeps every file v39 had (a lost tool is a lost gate)",
              not missing, "missing: " + ", ".join(missing))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ v39 frozen, v40 live")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
