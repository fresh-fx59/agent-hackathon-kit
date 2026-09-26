#!/usr/bin/env python3
"""cite.py — print a citation that citecheck accepts. Never type one by hand.

v60 — THE FIRST LINE THIS TOOL PRINTS IS THE REFERENCE TO PASTE. The v59 small
test pasted `file:N — «window»` (what --contains printed) 37 times and wrote 0
references; now every mode prints `Security.jsonl:15#TargetUserName` on its own
line, and the verbatim quote below it only as a preview.

v59 — CITE BY REFERENCE. Write `Security.jsonl:15#TargetUserName,Status` (or
`Security.jsonl@417272#TargetUserName` by EventRecordID) in the report; the
gate cuts `"TargetUserName":"KATE"` verbatim out of the raw line itself, so the
quote cannot be mistyped. Measured reason: the v58 small test spent ~55-60 % of
its post-draft repair (≈9M of 17.68M tokens) on typed-quote mechanics.

    python3 cite.py Security.jsonl:15#TargetUserName,Status   # show + paste line
    python3 cite.py --find KATE --file Security.jsonl         # address by content
    python3 cite.py --render work/report.md                    # expand references

Every line this tool prints is recorded in work/cite-shown.tsv. A reference to
a line no tool showed you is refused (`not-shown`): a line number from memory
is how v58 cited «KATE» at Security:13 (KATE is line 15). --corpus defaults to
$SHERLOCK_CORPUS, ./corpus or /work/corpus.

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
    """`path:line[#F,..]` or `path@rid[#F,..]` -> (path, line, rid, fields)."""
    m = CC.RID_RE.fullmatch(raw.strip())
    if m:
        return m.group(1), None, int(m.group(2)), CC._fields(m.group(3))
    m = CC.CITE_RE.fullmatch(raw.strip())
    if m and not m.group(3):
        return m.group(1), int(m.group(2)), None, CC._fields(m.group(4))
    if ":" not in raw:
        return None, None, None, None
    path, _, tail = raw.rpartition(":")
    if not path or not tail.isdigit():
        return None, None, None, None
    return path, int(tail), None, None


def read_line(root, path, lineno):
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return None, "нет файла: %s" % path
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        for i, text in enumerate(fh, 1):
            if i == lineno:
                return text.rstrip("\n"), None
    return None, "в файле меньше %d строк: %s" % (lineno, path)


def window(text, needle, width):
    """Centre `width` characters of `text` on `needle`."""
    at = text.find(needle)
    if at < 0:
        return None
    start = max(0, at - (width - len(needle)) // 2)
    return text[start:start + width]


def default_corpus():
    for cand in (os.environ.get("SHERLOCK_CORPUS"), "corpus", "/work/corpus"):
        if cand and os.path.isdir(cand):
            return cand
    return None


def default_work():
    for cand in (os.environ.get("SHERLOCK_WORK"), "work"):
        if cand and os.path.isdir(cand):
            return cand
    return None


def reference_line(path, n, fields, text):
    """v60 -> (paste line, preview). The paste line is the bare reference
    `path:N#F1,F2`; the preview is what the gate will cut out of the line."""
    if not fields and CC.line_fields(text):
        fields = list(dict.fromkeys(CC.auto_fields(text))) or None
    slices, missing = CC.ref_slices(text, fields)
    if missing:
        return None, "в строке нет поля %s" % ",".join(missing)
    wrapped = [w for w in (CC.quote_wrap(sl) for sl in slices) if w]
    addr = "%s:%d%s" % (path, n, ("#" + ",".join(fields)) if fields else "")
    return (addr, "    вырежет: " + " ".join(wrapped) if wrapped else ""), None


def field_holding(text, needle):
    """The field whose verbatim slice holds `needle` (payload first), or None."""
    f, _exact = CC.fields_for_quote(needle, text)
    return f[:1] or None


def resolve_rel(root, path):
    by_rel, by_base = CC.index_corpus(root)
    cand, how = CC.resolve(path, by_rel, by_base)
    if not cand:
        return None, None, "нет файла: %s" % path
    if how.endswith("ambiguous"):
        return None, None, "%s означает %d файла — назови один от корня корпуса" % (
            path, len(cand))
    return cand[0], by_rel[cand[0]], None


def show(args, shown):
    bad = 0
    for raw in args.address:
        path, lineno, rid, fields = parse_address(raw)
        if path is None:
            print("cite.py: адрес не разобран (нужно путь:строка[#Поле] или путь@EventRecordID): %s"
                  % raw, file=sys.stderr)
            bad += 1
            continue
        rel, abspath, why = resolve_rel(args.corpus, path)
        if rel is None:
            print("cite.py: %s — %s" % (raw, why), file=sys.stderr)
            bad += 1
            continue
        if rid is not None:
            lineno = CC.find_record(abspath, rid)
            if lineno is None:
                print("cite.py: %s — нет записи с EventRecordID=%d. Номер не выдумываю."
                      % (raw, rid), file=sys.stderr)
                bad += 1
                continue
        text, why = read_line(args.corpus, rel, lineno)
        if text is None:
            print("cite.py: %s — %s" % (raw, why), file=sys.stderr)
            bad += 1
            continue
        if args.contains:
            span = window(text, args.contains, CC.EXAMPLE_MAX)
            held = field_holding(text, args.contains) if span is not None else None
            if held and not fields:
                fields = held                 # v60: a reference, not a window
            if span is None:
                print("cite.py: %s — на строке нет «%s». Цитату не выдумываю: "
                      "перечитай строку или сними утверждение."
                      % (raw, args.contains), file=sys.stderr)
                bad += 1
                continue
            if not CC.line_fields(text):
                out = CC.quote_example({"path": path, "line": lineno, "text": span})
                if out is None:
                    bad += 1
                    continue
                print(out)
                shown.append((rel, lineno, text))
                continue
        ref, why = reference_line(path, lineno, fields, text)
        if ref is None:
            print("cite.py: %s — %s" % (raw, why), file=sys.stderr)
            bad += 1
            continue
        print(ref[0])
        if ref[1]:
            print(ref[1])
        if args.full:
            print("    строка: %s" % text)
        shown.append((rel, lineno, text))
    return 1 if bad else 0


def find(args, shown):
    if not args.file:
        print("cite.py: --find требует --file <путь от корня корпуса>", file=sys.stderr)
        return 1
    rel, abspath, why = resolve_rel(args.corpus, args.file)
    if rel is None:
        print("cite.py: %s" % why, file=sys.stderr)
        return 1
    import json as _json
    needles = {args.find, _json.dumps(args.find, ensure_ascii=False)[1:-1]}
    hits, more = 0, 0
    with open(abspath, "r", encoding="utf-8", errors="replace") as fh:
        for n, text in enumerate(fh, 1):
            text = text.rstrip("\n")
            if not any(nd and nd in text for nd in needles):
                continue
            if hits >= args.limit:
                more += 1
                continue
            ref, _why = reference_line(args.file, n, field_holding(text, args.find), text)
            print(ref[0] if ref else "%s:%d" % (args.file, n))
            shown.append((rel, n, text))
            hits += 1
    if not hits:
        print("cite.py: в %s нет «%s»" % (args.file, args.find), file=sys.stderr)
        return 1
    if more:
        print("… ещё %d строк — сузь поиск или посчитай их: cite.py --file %s --aggregate "
              "'count(line~=%s)'" % (more, args.file, args.find))
    return 0


def render(args):
    report = args.render
    work = os.path.dirname(os.path.abspath(report))
    shown = CC.shown_for(args.corpus, os.path.join(work, CC.SHOWN_FILE),
                         os.path.join(work, "worklist.tsv"))
    with open(report, encoding="utf-8") as fh:
        text = fh.read()
    new, expanded, refused = CC.render_refs(text, args.corpus, shown)
    if new != text:
        tmp = report + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(tmp, report)
    converted = getattr(CC.render_refs, "converted", [])
    print("render: развёрнуто ссылок %d, отказано %d, заменено цитат на ссылки %d"
          % (expanded, len(refused), len(converted)))
    for ln, old, ref in converted:
        print("  заменено стр.%d %s → %s — дальше пиши ссылку сам" % (ln, old, ref))
    for ln, ref, why in refused:
        print("  ✗ стр.%d %s — %s" % (ln, ref, why), file=sys.stderr)
    return 1 if refused else 0


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
        description="Show a corpus line as a ready reference, find lines, or render a report.")
    ap.add_argument("address", nargs="*",
                    help="path:line[#Field,..] or path@EventRecordID[#Field,..]")
    ap.add_argument("--corpus", default=None, help="corpus root (default: ./corpus)")
    ap.add_argument("--work", default=None, help="work dir for cite-shown.tsv (default: ./work)")
    ap.add_argument("--contains", default=None,
                    help="centre a legacy quote on this text; refuse if absent")
    ap.add_argument("--full", action="store_true", help="also print the raw line")
    ap.add_argument("--find", default=None, metavar="TEXT",
                    help="list the addresses of lines in --file that contain TEXT")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--render", default=None, metavar="REPORT",
                    help="expand references in REPORT into verbatim quotes, in place")
    ap.add_argument("--aggregate", default=None, metavar="PREDICATE",
                    help="build an AGGREGATE citation: count(...), "
                         "distinct(...) or distinct_over(...)")
    ap.add_argument("--file", default=None,
                    help="corpus-root path for --aggregate / --find")
    args = ap.parse_args(argv)
    args.corpus = args.corpus or default_corpus()
    if not args.corpus:
        print("cite.py: корпус не найден — укажи --corpus", file=sys.stderr)
        return 1
    cc = CC
    if args.aggregate is not None:
        return aggregate(cc, args)
    if args.render:
        return render(args)
    shown = []
    if args.find is not None:
        rc = find(args, shown)
    elif args.address:
        rc = show(args, shown)
    else:
        print("cite.py: нужен адрес путь:строка, --find, --render или --aggregate",
              file=sys.stderr)
        return 1
    if shown:
        CC.record_shown(args.work or default_work(), shown)
    return rc


CC = _load_citecheck()

if __name__ == "__main__":
    sys.exit(main())
