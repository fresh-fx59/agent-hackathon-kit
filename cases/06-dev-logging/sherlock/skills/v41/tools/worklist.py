#!/usr/bin/env python3
"""A CURSOR over the worklist, so the child never reads the ledger whole.

WHY THIS EXISTS, in numbers measured on the paid runs. `work/worklist.tsv` is 250
rows / 118,488 bytes; the mean row is 440.9 characters and column 6 `запись` — a
raw log excerpt — is 313 of them, **71 % of every row**. The gate never validates
it: `triagecheck.read_worklist` parses it into the row dict and it appears nowhere
else except inside `CONTENT_FIELDS`, the list of field names a bulk rule is
FORBIDDEN to use — deliberately, «so nobody can bulk-close rows by
pattern-matching text nobody read». The columns the gate does check average 124
characters.

So the old read path paid a 25,060-character truncated read (the stock 25,000-char
cap, hit dead on) for a file most of which no gate reads, and then asked for the
next page. Four such pages of the worklist and six of the map came to ~76,908
tokens, 23 % of a peak request.

A batch of 20 rows without the excerpt is about 2,480 bytes, and a full pass over
250 rows about 32,000 — against ~100,244 bytes for ONE partial pass the old way.

THREE RULES THIS TOOL MAY NOT BREAK, each one something the arm already depends on:

  1. IT NEVER LOSES A COLUMN. `citecheck` and `triagecheck` read the FULL
     `worklist.tsv` and their contract is untouched — only the child's READ path
     changes. A verdict write replaces column 2 of one named row and nothing else.
  2. IT HANDS OUT ROWS BY AXIS ON DEMAND. A bulk rule closes a CLASS
     (reference/bulk-closure.md), and a class cannot be recognised in rows that
     were never seen together; `--axis` mirrors the `view-<axis>-NN.tsv` slices the
     brief already mandates.
  3. IT REFUSES RATHER THAN GUESSING. An unknown id, a placeholder verdict, a cell
     containing a tab (which would forge a column) and a missing work directory are
     all non-zero exits with a diagnosis. A tool that silently does nothing is how
     a run believes it closed rows it did not.

The cursor needs no state file: "unresolved" is a property of the ledger itself — a
verdict cell that is empty or starts with `?`. One source of truth, so a lost state
file cannot rewind the investigation.
"""
import argparse
import io
import json
import os
import sys
import tempfile

PLACEHOLDER = "?"
COLUMNS = 6
#: id, вердикт, ось, ссылка, частота — everything the gate reads. `запись`
#: (column 6) is deliberately withheld: see the module docstring.
BATCH_COLUMNS = 5


def read_rows(path):
    """-> (header_lines, [row_cells]). Byte-faithful: nothing is normalised."""
    header, rows = [], []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("#"):
                header.append(line)
                continue
            if not line.strip():
                continue
            cells = line.split("\t")
            cells += [""] * (COLUMNS - len(cells))
            rows.append(cells)
    return header, rows


def worklist_path(work):
    work = os.path.abspath(work)
    if not os.path.isdir(work):
        raise SystemExit("✗ no work directory at %s" % work)
    path = os.path.join(work, "worklist.tsv")
    if not os.path.exists(path):
        raise SystemExit("✗ no worklist.tsv in %s" % work)
    return path


def unresolved(cells):
    cell = (cells[1] or "").strip()
    return (not cell) or cell.startswith(PLACEHOLDER)


def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                              dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cmd_next(args):
    path = worklist_path(args.work)
    _, rows = read_rows(path)
    picked = []
    for cells in rows:
        if not unresolved(cells):
            continue
        if args.axis and (cells[2] or "").strip() != args.axis:
            continue
        picked.append(cells)
        if len(picked) >= args.batch:
            break
    out = [
        "# batch of %d unresolved row(s)%s — columns: id, вердикт, ось, ссылка, "
        "частота" % (len(picked), (" on axis %s" % args.axis) if args.axis else ""),
        "# the record excerpt is NOT here on purpose: no gate reads it. Need the "
        "raw record? open the corpus line named in `ссылка`, or grep "
        "worklist.tsv for that id.",
        "# write verdicts back with: worklist.py verdict --work %s --from-stdin "
        "(id<TAB>cell per line)" % os.path.abspath(args.work),
    ]
    for cells in picked:
        out.append("\t".join(cells[:BATCH_COLUMNS]))
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def apply_verdicts(path, updates):
    """updates: {id: cell}. Refuses before writing anything."""
    header, rows = read_rows(path)
    index = {}
    for i, cells in enumerate(rows):
        index.setdefault((cells[0] or "").strip(), i)
    missing = [rid for rid in updates if rid not in index]
    if missing:
        raise SystemExit("✗ no such row id: %s" % ", ".join(sorted(missing)[:8]))
    for rid, cell in updates.items():
        if "\t" in cell or "\n" in cell:
            raise SystemExit("✗ verdict for %s contains a tab or newline — that "
                             "would forge a column" % rid)
        if not cell.strip() or cell.strip().startswith(PLACEHOLDER):
            raise SystemExit("✗ verdict for %s is still a placeholder (%r) — a "
                             "row is closed by a letter with evidence, not by an "
                             "empty cell" % (rid, cell))
    for rid, cell in updates.items():
        rows[index[rid]][1] = cell
    text = "".join(line + "\n" for line in header)
    text += "".join("\t".join(cells) + "\n" for cells in rows)
    atomic_write(path, text)
    return len(updates)


def cmd_verdict(args):
    path = worklist_path(args.work)
    updates = {}
    if args.from_stdin:
        for raw in sys.stdin.read().splitlines():
            if not raw.strip():
                continue
            parts = raw.split("\t", 1)
            if len(parts) != 2:
                raise SystemExit("✗ stdin line is not `id<TAB>cell`: %r"
                                 % raw[:80])
            updates[parts[0].strip()] = parts[1]
    else:
        if not args.id or args.cell is None:
            raise SystemExit("✗ verdict needs --id and --cell, or --from-stdin")
        updates[args.id.strip()] = args.cell
    n = apply_verdicts(path, updates)
    print(json.dumps({"written": n}, ensure_ascii=False))
    return 0


def cmd_status(args):
    path = worklist_path(args.work)
    _, rows = read_rows(path)
    by_axis = {}
    open_rows = 0
    for cells in rows:
        axis = (cells[2] or "").strip() or "?"
        entry = by_axis.setdefault(axis, {"total": 0, "unresolved": 0})
        entry["total"] += 1
        if unresolved(cells):
            entry["unresolved"] += 1
            open_rows += 1
    print(json.dumps({"total": len(rows), "unresolved": open_rows,
                      "resolved": len(rows) - open_rows, "axes": by_axis},
                     ensure_ascii=False, sort_keys=True))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("next", help="hand out the next unresolved rows")
    p.add_argument("--work", required=True)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--axis", default="")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("verdict", help="write verdicts back into the ledger")
    p.add_argument("--work", required=True)
    p.add_argument("--id")
    p.add_argument("--cell")
    p.add_argument("--from-stdin", action="store_true")
    p.set_defaults(func=cmd_verdict)

    p = sub.add_parser("status", help="counts, overall and per axis")
    p.add_argument("--work", required=True)
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    if args.command == "next" and args.batch <= 0:
        raise SystemExit("✗ --batch must be positive")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
