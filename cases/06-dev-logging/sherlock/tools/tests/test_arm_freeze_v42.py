#!/usr/bin/env python3
"""v41 is FROZEN — it carries a paid run — and v42 replaces it.

The paid run 20260827T173511Z-v41 is the first run this project called accepted:
341 ledger rows, 337 answered calls, a peak prompt+budget of 236,678 tokens under
the 262,000 ceiling, `finished stage=done`, exit 0. The 2026-08-28 independent
review then found that acceptance was a FALSE POSITIVE: the stored citecheck,
statecheck and triagecheck gates never enforced the operator's report contract,
so a report that did not meet it still walked through green. The numbers are real
and every byte of skills/v41 is part of that ledger, so v41 does not move and the
repair lands in v42.

Same discipline as V39_FROZEN / V40_FROZEN: pinned on CONTENT, so a stray edit is
caught in the working tree before it can be committed, plus the committed subtree
hash. v42 starts byte-identical and then diverges; this file asserts only what must
stay true forever — v41 does not move, and v42 never LOSES a file (a deleted tool
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

V41_TREE = "be6e87279a0e501005f0366b60e6488c80d8ce68"
V41_FROZEN = {
    "README.md":                           "b235a7380e1c94429128c753239647bb",
    "SKILL.md":                            "e643e56b368eb69b51882317853afb4e",
    "agents/sherlock-triage.md":           "d7c2d028669ceb6d89cb0cb187e62721",
    "reference/bulk-closure.md":           "22fd216deacb4d5d279162f409ef0614",
    "reference/code-and-spec.md":          "3dbfc0ca8f3386b318e868b23d52f1cf",
    "reference/draft-and-verify.md":       "ae79019ac70bcd4070ded85a39171e3d",
    "reference/enum-tables.tsv":           "36016a59e25f1964416da3e6fad5131b",
    "reference/report-format.md":          "8179fb57b2a9aca1b8c416ee91b09bb9",
    "reference/tools.md":                  "9d6531d41b0c478767987afc14b348e0",
    "tools/brief.py":                      "1fa2fa259f36ef8fd5ac51743d93c8fb",
    "tools/checkpoint.py":                 "6c0dd3f996a5dcdeb7d7953923775cfe",
    "tools/citecheck.py":                  "ce19642acc419b19f1ff00f2752504f7",
    "tools/cite.py":                       "c0c294122a9c51e95b292824af7d4cd4",
    "tools/covermap.py":                   "a6a6852e59ff95ed552e89f0a96be128",
    "tools/ingest.py":                     "3cb1b29f133fa293315801252d710e83",
    "tools/logjoin.py":                    "dd4465b4736215f1f78bd641cd40a264",
    "tools/logmap.py":                     "4de386a30c3e0098b04a47e458625899",
    "tools/rollover.py":                   "cf6d9b4ff9f2c751f76a11e4da604c64",
    "tools/stage-corpus.py":               "761eff5c844a11fbd290ff3ec9f0a0de",
    "tools/statecheck.py":                 "960204ec4d953580b9689e675758e0e0",
    "tools/stopcheck.py":                  "5222822a1a3a9e5f0ecc0f81ba8ac7c6",
    "tools/triagecheck.py":                "d9669d13376e1be24cb99c77c2f3a4fc",
    "tools/worklist.py":                   "b3674f70468b94a95eb401403bdf4185",
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
    v41 = walk(os.path.join(SKILLS, "v41"))
    drift = []
    for rel, want in sorted(V41_FROZEN.items()):
        got = v41.get(rel)
        if got is None:
            drift.append("%s: DELETED from a frozen arm" % rel)
        elif got != want:
            drift.append("%s: %s != pinned %s" % (rel, got, want))
    for rel in sorted(set(v41) - set(V41_FROZEN)):
        drift.append("%s: NEW file in a frozen arm" % rel)
    check("skills/v41 is byte-identical to the arm that produced the paid "
          "accepted run", not drift, "; ".join(drift))

    tree = subprocess.check_output(
        ["git", "-C", KIT, "rev-parse",
         "HEAD:cases/06-dev-logging/sherlock/skills/v41"]).decode().strip()
    check("skills/v41 committed tree has not moved", tree == V41_TREE, tree)

    v42root = os.path.join(SKILLS, "v42")
    check("skills/v42 exists — the live arm is not a frozen one",
          os.path.isdir(v42root))
    if os.path.isdir(v42root):
        v42 = walk(v42root)
        missing = sorted(set(V41_FROZEN) - set(v42))
        check("skills/v42 keeps every file v41 had (a lost tool is a lost gate)",
              not missing, "missing: " + ", ".join(missing))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ v41 frozen, v42 live")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
