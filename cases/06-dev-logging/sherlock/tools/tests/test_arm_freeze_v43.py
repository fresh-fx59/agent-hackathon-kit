#!/usr/bin/env python3
"""v42 is FROZEN — it carries the 20260830T190815Z-v42 trace — and v43 replaces it.

That run produced a GOOD report and was rejected by four terminal failures, three
of them real. The report, the ledger and every gate exit in that trace were
produced by exactly these bytes, so v42 does not move and the repair lands in v43.

Same discipline as V39/V40/V41/V42_FROZEN: pinned on CONTENT, so a stray edit is
caught in the working tree before it can be committed, plus the committed subtree
hash. v43 starts byte-identical and then diverges; this file asserts only what must
stay true forever — v42 does not move, and v43 never LOSES a file (a deleted tool
is how an arm silently stops running a gate).
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
FAILED = []

V42_TREE = "c270a08dae6d2517f9b99d5a44b85b5205c52eb2"
V42_FILES = {
    'README.md': '4b412f6f6567658ae15094f49ab1e62f',
    'SKILL.md': '1dfb0c0b068f8afd636eda6ca4fdabaf',
    'agents/sherlock-triage.md': 'd7c2d028669ceb6d89cb0cb187e62721',
    'reference/bulk-closure.md': '22fd216deacb4d5d279162f409ef0614',
    'reference/code-and-spec.md': '3dbfc0ca8f3386b318e868b23d52f1cf',
    'reference/draft-and-verify.md': 'e719c479d176873e206315d80c254cfb',
    'reference/enum-tables.tsv': 'a2fbcfb032aa1b5dde4fadd2cc9f4c6c',
    'reference/logon-failure-reason.json': '5f236ffb9efa72c56274ad8e4d518aa3',
    'reference/population-scope.json': '9749f7c4f367160349d7401833637d39',
    'reference/report-contract.corporate.json': '771c06420fb4377e0ac39243fd6256b0',
    'reference/report-format.md': '81ec5ec8633b5782f221e0cd52d873d4',
    'reference/tools.md': 'beadb71ac62ad11b6e63d7866979a6a9',
    'tools/brief.py': '1fa2fa259f36ef8fd5ac51743d93c8fb',
    'tools/checkpoint.py': '6c0dd3f996a5dcdeb7d7953923775cfe',
    'tools/cite.py': 'c0c294122a9c51e95b292824af7d4cd4',
    'tools/citecheck.py': '05d47c3e3d88250238618e1d911cc202',
    'tools/covermap.py': 'a6a6852e59ff95ed552e89f0a96be128',
    'tools/ingest.py': 'b9e986ddddc4499e58d79853525c28f7',
    'tools/logjoin.py': 'dd4465b4736215f1f78bd641cd40a264',
    'tools/logmap.py': '4de386a30c3e0098b04a47e458625899',
    'tools/reportcheck.py': '622e37491d80aea96145da2a9d82f7d8',
    'tools/rollover.py': 'cf6d9b4ff9f2c751f76a11e4da604c64',
    'tools/stage-corpus.py': '761eff5c844a11fbd290ff3ec9f0a0de',
    'tools/statecheck.py': '960204ec4d953580b9689e675758e0e0',
    'tools/stopcheck.py': '9bd613bc601a84420addf227d9b3f107',
    'tools/triagecheck.py': '8b515d509d1707127cc0189e74d92523',
    'tools/worklist.py': 'bcfce0db571701413a8124bd6a63129d',
}


def walk(root):
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = hashlib.md5(fh.read()).hexdigest()
    return out


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def main():
    live = walk(os.path.join(SKILLS, "v42"))
    check(live == V42_FILES,
          "skills/v42 changed: added %s, removed %s, edited %s"
          % (sorted(set(live) - set(V42_FILES)),
             sorted(set(V42_FILES) - set(live)),
             sorted(k for k in set(live) & set(V42_FILES) if live[k] != V42_FILES[k])))

    tree = subprocess.run(
        ["git", "rev-parse", "HEAD:cases/06-dev-logging/sherlock/skills/v42"],
        cwd=SHERLOCK, capture_output=True, text=True)
    check(tree.returncode == 0 and tree.stdout.strip() == V42_TREE,
          "committed skills/v42 subtree is %r, expected %s"
          % (tree.stdout.strip(), V42_TREE))

    v43 = walk(os.path.join(SKILLS, "v43"))
    missing = sorted(set(V42_FILES) - set(v43))
    check(not missing, "skills/v43 LOST files v42 had: %s" % missing)

    for msg in FAILED:
        print("FAIL: %s" % msg)
    print("OK" if not FAILED else "FAILED %d" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
