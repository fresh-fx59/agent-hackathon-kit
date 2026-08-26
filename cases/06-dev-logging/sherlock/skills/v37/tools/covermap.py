#!/usr/bin/env python3
"""covermap.py — print the coverage table. Do not type it by hand.

PROVENANCE, because it matters. This is a generalisation of a script the MODEL
wrote for itself during the first gate-clean paid run
(sherlock-winevtx-runs-v37-full-r1/20260825T173021Z-v37, `work/gen_cov.py`).
Faced with a gate that blocks on any corpus file missing from the coverage
table, it did not hunt for a cheap path: it wrote a generator that imports
`citecheck.py` in-process, takes the line `logmap` had already flagged for each
file, and builds a quote the gate accepts. 143 rows for 143 files — 93
«наблюдение» with real quotes, 50 «пусто» with `байт=0`, zero «не смотрел».

Shipping it removes what that cost: the model paid to write it, debug it
(`work/cov_err.txt` is in the trace) and re-run it. It also removes the
alternative, which the previous run demonstrated — typing the rows by hand and
getting the grammar wrong 23 times out of 41.

THE TENSION, stated rather than hidden. A generated table makes coverage
mechanical. That is acceptable because a coverage row's job is «this file was
answered, here is a checkable address», not «this file was understood»: the
findings and the rejected candidates carry the thinking. The run this came from
still produced 5 findings and 9 rejected candidates with the generator in hand.
WHAT MUST NEVER BE GENERATED IS A FINDING.

    python3 covermap.py --corpus <LOG_DIR> --worklist ./work/worklist.tsv >> report.md

Every file gets exactly one row:
  * 0 bytes                 -> `пусто` + `байт=0`
  * binary by citecheck     -> `двоичный` + `формат=двоичный`
  * unreadable at that line -> `нечитабельно` + `ошибка=<причина>`
  * otherwise               -> `наблюдение` + `path:line «дословная цитата»`

The quoted line is chosen by `citecheck.coverage_admissible_lines` — the one
implementation of the rule, shared with the grader. That is not a nicety:

  * a quote that lands on no line `logmap` flagged is `cov_unflagged_citation`
    and blocks — an arbitrary line proves only that the file was opened;
  * a quote that lands on LINE 1 of a file with other flagged lines is
    `cov_inadmissible_line` and blocks too. Measured on the v37 gate-clean run:
    81 of 93 «наблюдение» rows quoted line 1 and every one of them passed,
    because `logmap` names a group's FIRST member as the group's reference. The
    oldest record in the file is what a tool reaches for when it needs *a*
    line. Line 1 stays legal only where it is honest — a file of two lines or
    fewer, or a file whose only flagged line is line 1.

So: run this tool. It returns the admissible line for free; guessing one costs
more and the gate enumerates the answers.

«не смотрел» is never emitted. It does not discharge a file — it is
unverifiable by construction — so generating it would be writing a lie.
"""
import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_citecheck():
    """One definition of the quote rules, shared with the grader."""
    path = os.path.join(HERE, "citecheck.py")
    spec = importlib.util.spec_from_file_location("_sherlock_citecheck", path)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise SystemExit("covermap.py: cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import re

TOKEN_RE = re.compile(r"[0-9A-Za-z_.\\-]{4,}")


def salient(display, text, cc):
    """Centre the quote on the token that made the line interesting.

    Without this the quote is the tail of the record — legal (citecheck only
    demands a verbatim span of the cited line) and worthless to a human, who
    gets `"AccountName":"LocalSystem"}}}` where the point was `3proxy`. logmap
    already wrote WHY the line is on the worklist, in the row's display column;
    the longest token common to that text and the line is the thing to show.
    """
    best = ""
    for tok in TOKEN_RE.findall(display or ""):
        if len(tok) > len(best) and tok in text:
            best = tok
    if not best:
        return None
    at = text.find(best)
    width = cc.EXAMPLE_MAX
    start = max(0, at - (width - len(best)) // 2)
    return text[start:start + width]


def flagged_lines(worklists):
    """path -> {line: display} from the reference column of every worklist."""
    out = {}
    for path in worklists:
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if raw.startswith("#") or not raw.strip():
                    continue
                parts = raw.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                ref = parts[3].strip()
                head, _, tail = ref.rpartition(":")
                if head and tail.isdigit():
                    # Column 4 is logmap's REASON («json:EventID=7045 …»),
                    # column 5 is the raw record. The reason names the token
                    # that put the row on the worklist, which is what a reader
                    # needs to see quoted; the record is a fallback only.
                    verdict = (parts[1] or "").strip()
                    out.setdefault(head, {})[int(tail)] = (
                        verdict,
                        "\t".join(parts[4:6]) if len(parts) > 4 else "")
    return out


def line_at(path, want):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, text in enumerate(fh, 1):
                if i == want:
                    return text.rstrip("\n"), None
    except OSError as exc:
        return None, "ошибка=%s" % (getattr(exc, "errno", "io") or "io")
    return None, "ошибка=нет-строки"


def rows_for(corpus, flagged, cc):
    rows = []
    for dirpath, dirnames, filenames in os.walk(corpus):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__", ".venv")]
        for name in filenames:
            ap = os.path.join(dirpath, name)
            rel = os.path.relpath(ap, corpus).replace(os.sep, "/")
            try:
                size = os.path.getsize(ap)
            except OSError:
                rows.append((rel, "нечитабельно", "ошибка=stat"))
                continue
            if size == 0:
                rows.append((rel, "пусто", "байт=0"))
                continue
            if cc.looks_binary(ap):
                rows.append((rel, "двоичный", "формат=двоичный"))
                continue
            marks = flagged.get(rel) or {}
            # WHICH LINE. Not "any flagged line" and never "line 1 because it
            # is there": `citecheck.coverage_admissible_lines` owns that rule
            # and this is the only place it is consulted, so the producer
            # cannot drift from the grader. It hands back the CLOSED set of
            # lines a coverage row is allowed to cite for this file — flagged
            # lines above 1, or line 1 when line 1 is the only flag, or the
            # whole of a <=2-line file, or the last quotable line when the
            # mapper flagged nothing. Read its docstring before touching this.
            try:
                allowed = cc.coverage_admissible_lines(ap, set(marks), rel)
            except OSError as exc:
                rows.append((rel, "нечитабельно",
                             "ошибка=%s" % (getattr(exc, "errno", "io") or "io")))
                continue
            # Inside the allowed set, prefer the line TRIAGE called a defect,
            # then the mapper's own order: covermap on the real corpus
            # otherwise cited System.jsonl:3 (a kernel boot record) for the
            # file whose point is System.jsonl:263 (the 3proxy install). Both
            # are admissible; only one is worth a reader's time.
            want = sorted(allowed, key=lambda n: (
                not (marks.get(n, ("", ""))[0] or "").startswith("D"), n))
            picked = None
            # There is no fallback outside `allowed`. A file with nothing
            # citable is reported as such, not papered over with line 1.
            for candidate in want:
                text, why = line_at(ap, candidate)
                if text is None:
                    continue
                hint = (marks.get(candidate) or ("", ""))[1]
                span = salient(hint, text, cc) or text
                quoted = cc.quote_example({"path": rel, "line": candidate,
                                           "text": span})
                if quoted is None and span is not text:
                    quoted = cc.quote_example({"path": rel, "line": candidate,
                                               "text": text})
                if quoted:
                    picked = (candidate, quoted)
                    break
            if picked is None:
                rows.append((rel, "нечитабельно", "ошибка=нечем-цитировать"))
                continue
            _line, quoted = picked
            rows.append((rel, "наблюдение", quoted))
    rows.sort(key=lambda r: r[0])
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Print the coverage table for a corpus. Paste it verbatim.")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--worklist", action="append", default=[],
                    help="worklist.tsv; repeat for several hosts")
    ap.add_argument("--header", action="store_true",
                    help="also print the markdown table header")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.corpus):
        sys.exit("covermap.py: нет такого каталога: %s" % args.corpus)

    cc = _load_citecheck()
    rows = rows_for(args.corpus, flagged_lines(args.worklist), cc)
    if args.header:
        print("| путь | статус | улики |")
        print("| --- | --- | --- |")
    for rel, status, detail in rows:
        print("| %s | %s | %s |" % (rel, status, detail))
    counts = {}
    for _rel, status, _d in rows:
        counts[status] = counts.get(status, 0) + 1
    sys.stderr.write("covermap: %d файлов — %s\n"
                     % (len(rows), ", ".join("%s %d" % kv
                                             for kv in sorted(counts.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
