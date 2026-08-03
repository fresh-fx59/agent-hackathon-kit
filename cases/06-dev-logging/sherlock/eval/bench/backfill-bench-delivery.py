#!/usr/bin/env python3
"""backfill-bench-delivery.py — teach the bench ledger about the second channel.

Until 2026-08-03 `run-bench.sh` recorded only the final message, so three kinds
of row are wrong in the same way: the ledger cannot see what the run produced.

1. **artifact** — every recorded run's own `work/report.md` is sitting in its
   trajectory dir, unread. `20260802T221034Z-v11` answered «Отчёт готов…» in 101
   chars beside a complete 19,991-char report that had just passed `citecheck`
   45/45, and 18,758,431 input tokens scored nothing.
2. **stub** — four rows came from a stub `qwen` (`input_tokens: 11`, answer
   «apps/api.log:1 something broke»). They sit in the real ledger where
   `rows[-1]` would score one as a measurement.
3. **orphan** — `20260802T151710Z-v11` has a 0-byte `out.json` and a 24,233-char
   `work/report.md`. The runner exited before recording anything, so a paid-for
   run left no row at all. ~33 % of this project's spend has bought exactly that.

Rules, because every row in `runs-bench.jsonl` cost metered money:

* A field is written only if its KEY IS ABSENT. A key that is present, including
  one holding null, is left exactly as it is — so this can be re-run.
* An orphan's cost is NULL, never 0. Its tokens were spent and are unrecoverable;
  zero would make the arm look free. → [[eval-must-measure-cost-not-just-quality]]
* A stub row is MARKED, never deleted. It really was produced; the mark is what
  keeps it out of a measurement.
* The rewrite is atomic and leaves `<ledger>.bak`. The ledger is git-tracked
  (its dir's .gitignore does not win over tracking), so `git checkout` is the
  real undo — the backup is what covers an untracked ledger under --ledger.

    python3 backfill-bench-delivery.py            # rewrite in place
    python3 backfill-bench-delivery.py --dry-run  # report only, touch nothing
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))
_spec = importlib.util.spec_from_file_location(
    "deliverable", os.path.join(SHERLOCK, "measure", "deliverable.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

# The stub `qwen` in measure/tests/test_run_bench.py answers exactly this, for
# exactly this cost. Both are required: the sentence alone is one a real run
# could plausibly write, but 11 input tokens cannot buy a 649 MB investigation.
STUB_ANSWER = "apps/api.log:1 something broke"
STUB_MAX_INPUT_TOKENS = 1000


def read_artifact(trace_dir):
    """The report file a run left behind, or "" — never a guess.

    A missing run dir is recorded as "" and not as null: the ledger's own claim
    is then "this run delivered in its message", which is what the row already
    said. Null would imply the file might exist and we did not look.
    """
    if not trace_dir:
        return ""
    p = os.path.join(trace_dir, "work", "report.md")
    if not os.path.isfile(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def fill(row):
    """The delivery fields for one existing row. Absent keys only."""
    changed = []
    if "artifact" not in row:
        row["artifact"] = read_artifact(row.get("trace_dir"))
        changed.append("artifact")
    answer, art = row.get("answer") or "", row.get("artifact") or ""
    for k, v in (("answer_chars", len(answer)),
                 ("artifact_chars", len(art)),
                 ("deliverable_chars", len(D.compose(answer, art))),
                 ("delivered_in", D.channel(answer, art))):
        if k not in row:
            row[k] = v
            changed.append(k)
    if "stub" not in row and answer == STUB_ANSWER \
            and (row.get("input_tokens") or 0) <= STUB_MAX_INPUT_TOKENS:
        row["stub"] = True
        changed.append("stub")
    return changed


def orphans(runs_root, known):
    """Run dirs holding a report that no ledger row points at.

    The arm is read off the dir name (`20260802T151710Z-v11`), which is the only
    place it survives — `out.json` is empty, which is why there is no row. The
    model is NOT guessed: nothing on disk records it for these runs.
    """
    out = []
    if not os.path.isdir(runs_root):
        return out
    for name in sorted(os.listdir(runs_root)):
        d = os.path.join(runs_root, name)
        if not os.path.isdir(d) or os.path.abspath(d) in known:
            continue
        art = read_artifact(d)
        if not art.strip():
            continue
        out.append({"arm": name.split("-", 1)[1] if "-" in name else None,
                    "model": None, "client_model": None,
                    "turns": None, "duration_s": None,
                    "input_tokens": None, "output_tokens": None,
                    "answer": "", "artifact": art,
                    "answer_chars": 0, "artifact_chars": len(art),
                    "deliverable_chars": len(art), "delivered_in": "file",
                    "artifact_only": True, "dataset": "bench649",
                    "trace_dir": d})
    return out


def insert_ordered(rows, orphan):
    """Chronologically, so `rows[-1]` still means the most recent run."""
    name = os.path.basename(orphan["trace_dir"])
    idx = len(rows)
    for i, r in enumerate(rows):
        td = r.get("trace_dir")
        if td and os.path.basename(td) > name:
            idx = i
            break
    rows.insert(idx, orphan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "runs-bench.jsonl"))
    ap.add_argument("--runs", default=os.path.join(HERE, "runs"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = []
    if os.path.exists(a.ledger):
        with open(a.ledger, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]

    touched = 0
    for r in rows:
        ch = fill(r)
        if ch:
            touched += 1
            print("  %-34s + %s" % (os.path.basename(r.get("trace_dir") or "—"),
                                    ",".join(ch)))

    known = {os.path.abspath(r["trace_dir"]) for r in rows if r.get("trace_dir")}
    found = orphans(a.runs, known)
    for o in found:
        print("  %-34s RECOVERED artifact-only (%d chars, cost unknown)"
              % (os.path.basename(o["trace_dir"]), o["artifact_chars"]))
        insert_ordered(rows, o)

    print("%d row(s) updated, %d recovered" % (touched, len(found)))
    if a.dry_run:
        print("--dry-run: nothing written")
        return 0
    if not touched and not found:
        return 0

    if os.path.exists(a.ledger):
        shutil.copy2(a.ledger, a.ledger + ".bak")
    tmp = a.ledger + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, a.ledger)
    print("wrote %s (backup: %s.bak)" % (a.ledger, a.ledger))
    return 0


if __name__ == "__main__":
    sys.exit(main())
