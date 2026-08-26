#!/usr/bin/env python3
"""cite.py — print a citation that citecheck accepts. Never type one by hand.

WHY THIS EXISTS, measured rather than guessed. The full winevtx run on arm v36
(sherlock-winevtx-runs-v36-full-r1/20260825T061049Z-v36) exited 0 and was
refused by its own gate: of 41 resolved references, 18 were ok, 17 were
no-quote and 6 were wrong-content. The forensics were RIGHT — System.jsonl:263
really is the 3proxy service install, the report named 3proxy 16 times and
reached the correct verdict «скомпрометирована». Only the grammar was wrong: a
`path:line` with no verbatim fragment beside it, or a fragment that was the
model own prose. On System.jsonl:263 it offered «входящего доступа,
установленная от имени пользователя root. улики:» and matched 1 of 7 words.

Writing MORE RULES INTO THE SKILL IS THE FIX THAT ALREADY FAILED. citecheck
quote_example carries the receipts: D07 spent 40 turns and 11.15M tokens
reverse-engineering the checker rather than adding a pair of quotes, and D04
spent 123 turns doing the same. So this is a boundary fix, not another
paragraph: constrain the input at the point it is produced, and the downstream
grammar cannot be got wrong.

    python3 cite.py --corpus <LOG_DIR> System.jsonl:263
    System.jsonl:263 — «...\"ServiceName\":\"3proxy\",\"ImagePath\":...»

    python3 cite.py --corpus <LOG_DIR> System.jsonl:263 --contains 3proxy

Paste the output verbatim. It is built by citecheck OWN quote_example, so the
two cannot drift: whatever this prints, that gate accepts.

`--contains` matters more than it looks. Any quote off the right line passes,
including a boilerplate tail like `\"Binary\":null}}}` — legal and useless as
evidence. `--contains` centres the window on the token that made the line
interesting, and REFUSES if that token is not on the line, which is the case
where a hand-written citation would have quietly asserted something false.

AGGREGATE MODE — a citation for a POPULATION, not for one line.

    python3 cite.py --corpus <LOG_DIR> --file Security.jsonl \
        --aggregate 'distinct(Event.EventData.IpAddress)'
    агрегат: Security.jsonl · distinct(Event.EventData.IpAddress) = 93 · `jq …`

Measured, and the reason this mode exists: «93 distinct source IPs, 8 of them
over 1000 failed logons» HAS NO LINE TO QUOTE, so under a quote-only gate the
only legal move was to delete the claim. v36 failed its gates and named 12
attacker IPs; v37 passed all three gates and named 4, of 93 real ones. Passing
the gate made the report worse.

This mode does not take a number from you — it COMPUTES the number and prints
the finished line, and it is the same `citecheck` code that will re-compute it
at the gate, so the two cannot drift. The trailing command is a rendering of the
predicate for a human to paste; neither this tool nor the gate ever runs it.

Predicates (closed vocabulary; `citecheck.py` documents every failure mode):

    count(FIELD op VALUE[, …])          records matching every filter
    distinct(FIELD[, FIELD op VALUE…])  distinct non-empty values of FIELD
    distinct_over(FIELD, N[, …])        values of FIELD seen MORE than N times

`op` is `=` (exact), `~=` (literal substring), `>=` / `<=` (lexicographic, which
is chronological for ISO-8601 timestamps). FIELD is a dotted path into the JSON
record, or the pseudo-field `line` for corpora that are not JSONL.

Exit codes: 0 a citation was printed for every address; 1 at least one address
was refused (missing file, line out of range, --contains not on the line, or a
line too short to quote). Nothing is ever invented: a refusal prints why, on
stderr, and prints no citation for that address. In aggregate mode a refusal is
the gate's own verdict — zero matches, an unknown field, a predicate that
matches every record — printed with the reason and no citation.
"""
import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_citecheck():
    """Import the sibling citecheck so the quote rules have ONE definition."""
    path = os.path.join(HERE, "citecheck.py")
    spec = importlib.util.spec_from_file_location("_sherlock_citecheck", path)
    if spec is None or spec.loader is None:          # pragma: no cover
        raise SystemExit("cite.py: cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_address(raw):
    """`path:line` -> (path, line). The path may itself contain colons."""
    if ":" not in raw:
        return None, None
    path, _, tail = raw.rpartition(":")
    if not path or not tail.isdigit():
        return None, None
    return path, int(tail)


def read_line(root, path, lineno):
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return None, "нет файла: %s" % path
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        for i, text in enumerate(fh, 1):
            if i == lineno:
                return text.rstrip("\\n"), None
    return None, "в файле меньше %d строк: %s" % (lineno, path)


def window(text, needle, width):
    """Centre `width` characters of `text` on `needle`."""
    at = text.find(needle)
    if at < 0:
        return None
    start = max(0, at - (width - len(needle)) // 2)
    return text[start:start + width]


def aggregate(cc, args):
    """Build one aggregate citation, or refuse and say why.

    Every decision here is made by `citecheck` itself: `agg_parse_predicate`
    parses, `agg_evaluate` counts, `agg_render_citation` renders. This function
    only wires them together, so there is exactly one implementation of the
    form and the producer cannot drift from the grader.
    """
    if not args.file:
        print("cite.py: --aggregate требует --file <путь от корня корпуса>",
              file=sys.stderr)
        return 1
    try:
        pred = cc.agg_parse_predicate(args.aggregate)
    except cc.AggError as e:
        print("cite.py: предикат не разобран — %s" % e, file=sys.stderr)
        return 1
    by_rel, by_base = cc.index_corpus(args.corpus)
    cand, how = cc.resolve(args.file, by_rel, by_base)
    if not cand:
        print("cite.py: в корпусе нет файла %s" % args.file, file=sys.stderr)
        return 1
    if how.endswith("ambiguous"):
        print("cite.py: %s означает %d разных файла — назови один целиком:\n  %s"
              % (args.file, len(cand), "\n  ".join(cand[:8])), file=sys.stderr)
        return 1
    rel = cand[0]
    if cc.looks_binary(by_rel[rel]):
        print("cite.py: %s — двоичный файл, отрендерь его в текст" % rel,
              file=sys.stderr)
        return 1
    verdict, actual, detail = cc.agg_evaluate(by_rel[rel], pred)
    if verdict != "ok":
        print("cite.py: %s — %s: %s" % (rel, verdict, detail), file=sys.stderr)
        return 1
    try:
        print(cc.agg_render_citation(rel, pred, actual))
    except cc.AggError as e:
        print("cite.py: %s" % e, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Print a citation citecheck accepts. Paste it verbatim.")
    ap.add_argument("address", nargs="*", help="path:line, from the corpus root")
    ap.add_argument("--corpus", required=True, help="corpus root")
    ap.add_argument("--contains", default=None,
                    help="centre the quote on this text; refuse if absent")
    ap.add_argument("--aggregate", default=None, metavar="PREDICATE",
                    help="build an AGGREGATE citation: count(...), "
                         "distinct(...) or distinct_over(...)")
    ap.add_argument("--file", default=None,
                    help="corpus-root path the --aggregate predicate runs over")
    args = ap.parse_args(argv)

    cc = _load_citecheck()
    if args.aggregate is not None:
        return aggregate(cc, args)
    if not args.address:
        print("cite.py: нужен адрес путь:строка или --aggregate", file=sys.stderr)
        return 1
    bad = 0
    for raw in args.address:
        path, lineno = parse_address(raw)
        if path is None:
            print("cite.py: адрес не разобран (нужно путь:строка): %s" % raw,
                  file=sys.stderr)
            bad += 1
            continue
        text, why = read_line(args.corpus, path, lineno)
        if text is None:
            print("cite.py: %s — %s" % (raw, why), file=sys.stderr)
            bad += 1
            continue
        if args.contains:
            span = window(text, args.contains, cc.EXAMPLE_MAX)
            if span is None:
                print("cite.py: %s — на строке нет «%s». Цитату не выдумываю: "
                      "перечитай строку или сними утверждение."
                      % (raw, args.contains), file=sys.stderr)
                bad += 1
                continue
            text = span
        out = cc.quote_example({"path": path, "line": lineno, "text": text})
        if out is None:
            print("cite.py: %s — строку нечем процитировать (короче %d "
                  "символов или во всех кавычках)" % (raw, cc.EXAMPLE_MIN),
                  file=sys.stderr)
            bad += 1
            continue
        print(out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
