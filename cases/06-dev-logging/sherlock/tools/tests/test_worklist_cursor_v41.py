#!/usr/bin/env python3
"""FIX 4: give the child a CURSOR, not a file.

MEASURED on the paid runs. `work/worklist.tsv` is 250 rows / 118,488 bytes, mean
row 440.9 characters, and column 6 `запись` — a raw log excerpt — is 313 of them,
71 % of every row. The gate never validates it: `read_worklist` parses it at
triagecheck.py:489 and it appears nowhere else except inside `CONTENT_FIELDS`, the
list of field names a bulk rule is FORBIDDEN to use, deliberately, «so nobody can
bulk-close rows by pattern-matching text nobody read». Columns 1-5, the ones the
gate does check, average 124 characters.

So the child pays 25,060-character truncated reads for a file 71 % of which no gate
reads, then asks for the next page. A batch of 20 rows without `запись` is ~2,480
bytes; a full pass over 250 rows is ~32,000 bytes against the ~100,244 the v40 run
burned on ONE partial pass.

WHAT THIS TOOL MUST NOT DO, and each is a rule the arm already depends on:
  * it must never lose a column — `citecheck` and `triagecheck` read the FULL file
    and their contract is untouched; only the child's READ path changes;
  * it must be able to hand out rows BY AXIS, because a bulk rule closes a class
    and the child cannot recognise a class it never sees grouped (see
    reference/bulk-closure.md and the existing view-<axis>-NN.tsv slices);
  * a verdict write must be atomic and must never touch a row it was not given.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
TOOL = os.path.join(SKILLS, "v41", "tools", "worklist.py")
FAILED = []

HEADER = (u"# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
          u"# вердикт: ? не разобрано · D дефект · N норма · X данных не хватает\n")
BIG = u"{\"Event\":{\"System\":{\"Provider\":\"x\"}," + u"\"pad\":\"" + u"z" * 300 + u"\"}}"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(args, stdin=b""):
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.Popen([sys.executable, TOOL] + args, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e)
    out, err = p.communicate(stdin)
    return (p.returncode, out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


def make(rows=250, resolved=0):
    d = tempfile.mkdtemp(prefix="wl-")
    work = os.path.join(d, "work")
    os.makedirs(work)
    axes = ["odd", "cat", "rare", "burst", "bg"]
    lines = [HEADER]
    for i in range(1, rows + 1):
        verdict = (u"N%d a.log:1 «q» n=1 фон" % i) if i <= resolved else u"?"
        lines.append(u"g%03d\t%s\t%s\ta.log:%d\tn=%d · окно\t%s\n"
                     % (i, verdict, axes[i % len(axes)], i, i, BIG))
    with io.open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write(u"".join(lines))
    return work


def main():
    check("skills/v41/tools/worklist.py exists", os.path.exists(TOOL))
    if not os.path.exists(TOOL):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1

    work = make(250)
    rc, out, err = run(["next", "--work", work, "--batch", "20"])
    check("next exits 0", rc == 0, err)
    data = [l for l in out.splitlines() if l and not l.startswith("#")]
    check("next hands out exactly the batch size", len(data) == 20, len(data))
    check("a batch of 20 is under 4,000 bytes — a truncated read was 25,060",
          len(out.encode("utf-8")) < 4000, len(out.encode("utf-8")))
    check("the excerpt column is NOT in the batch — the gate never reads it",
          "zzz" not in out)
    check("every row keeps its id, axis, reference and frequency",
          all(len(l.split("\t")) >= 4 for l in data), data[:1])
    check("the batch says where the full record can be found if it is needed",
          "worklist.tsv" in out, out[:200])

    # A full pass costs what the spec promised.
    total = 0
    seen = set()
    while True:
        rc, out, err = run(["next", "--work", work, "--batch", "20"])
        rows = [l for l in out.splitlines() if l and not l.startswith("#")]
        if not rows:
            break
        total += len(out.encode("utf-8"))
        ids = [r.split("\t")[0] for r in rows]
        check("no batch repeats a row already handed out (%s)" % ids[0],
              not (set(ids) & seen), sorted(set(ids) & seen)[:3])
        seen |= set(ids)
        payload = u"".join(u"%s\tN a.log:1 «q» n=1 фон\n" % i for i in ids)
        rc, o2, e2 = run(["verdict", "--work", work, "--from-stdin"],
                         payload.encode("utf-8"))
        check("verdict --from-stdin closes a whole batch (%s)" % ids[0], rc == 0,
              e2[:200])
    check("a full pass over 250 rows costs under 40,000 bytes", total < 40000,
          total)
    check("a full pass reached every row", len(seen) == 250, len(seen))

    # The ledger is intact and still gate-readable.
    text = io.open(os.path.join(work, "worklist.tsv"), encoding="utf-8").read()
    body = [l for l in text.splitlines() if l and not l.startswith("#")]
    check("the ledger still has all 250 rows", len(body) == 250, len(body))
    check("the ledger still has all six columns",
          all(len(l.split("\t")) == 6 for l in body))
    check("the excerpt column was preserved in the ledger", "zzz" in text)
    check("no row is left unresolved after the pass",
          not any(l.split("\t")[1].strip().startswith("?") for l in body))

    # Axis-aware, because a bulk rule closes a class.
    work = make(250)
    rc, out, err = run(["next", "--work", work, "--batch", "10",
                        "--axis", "rare"])
    rows = [l for l in out.splitlines() if l and not l.startswith("#")]
    check("--axis hands out only that axis, so a class can be recognised",
          rows and all(r.split("\t")[2] == "rare" for r in rows),
          [r.split("\t")[2] for r in rows][:5])

    # Refusals: precise, and never a silent no-op.
    rc, out, err = run(["verdict", "--work", work, "--id", "g999",
                        "--cell", "N x"])
    check("a verdict for an id that does not exist FAILS", rc != 0, out + err)
    rc, out, err = run(["verdict", "--work", work, "--id", "g001",
                        "--cell", "?"])
    check("a verdict that is still a placeholder FAILS", rc != 0, out + err)
    rc, out, err = run(["verdict", "--work", work, "--id", "g001",
                        "--cell", "N a.log:1\tsplit"])
    check("a verdict containing a tab FAILS — it would forge a column",
          rc != 0, out + err)
    rc, out, err = run(["next", "--work", os.path.join(work, "nope"),
                        "--batch", "5"])
    check("a missing work directory FAILS instead of printing an empty batch",
          rc != 0, out + err)

    rc, out, err = run(["status", "--work", work])
    check("status reports the counts as JSON", rc == 0 and "total" in out, out[:200])
    row = json.loads(out) if rc == 0 else {}
    check("status counts 250 total", row.get("total") == 250, row)

    # One row at a time still works, and touches nothing else.
    rc, out, err = run(["verdict", "--work", work, "--id", "g007",
                        "--cell", "D1 a.log:7 «q» n=7"])
    check("a single verdict write exits 0", rc == 0, err)
    text = io.open(os.path.join(work, "worklist.tsv"), encoding="utf-8").read()
    got = [l for l in text.splitlines() if l.startswith("g007\t")]
    check("the verdict landed in column 2 of that row",
          got and got[0].split("\t")[1] == "D1 a.log:7 «q» n=7", got[:1])
    others = [l for l in text.splitlines()
              if l.startswith("g008\t") or l.startswith("g006\t")]
    check("its neighbours were not touched",
          all(l.split("\t")[1] == "?" for l in others), others)

    check("v40 does NOT have this tool — the fix lands in v41 only",
          not os.path.exists(os.path.join(SKILLS, "v40", "tools", "worklist.py")))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the child gets a cursor, not a file")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
