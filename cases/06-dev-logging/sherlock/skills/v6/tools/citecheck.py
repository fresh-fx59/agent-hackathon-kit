#!/usr/bin/env python3
"""citecheck — does the cited line actually SAY what the report claims?

    python3 citecheck.py report.md --corpus ./logs
    qwen ... | python3 citecheck.py - --corpus ./logs --json

Why this exists (measured, 2026-07-28). On the corporate model a run cited
`Linux_2k.log:106` as evidence for a «session opened for user test» claim.
Line 106 is an authentication-failure line; the 36 real occurrences are at
92, 585, 586, 587… Real file, real line number, **wrong content** — the shape
that survives any range check. An earlier decision killed this checker on the
strength of 79/79 verbatim-verified citations, but that was measured on a
*strong* model. Lesson: never generalise a capability finding across model tiers;
the weaker the model, the more a deterministic guard earns its keep.

Verdicts, one per citation:

    ok             the cited line supports the adjacent claim
    wrong-content  the line exists and does NOT support the claim  <- the whole point
    out-of-range   the file exists, the line number does not
    missing-file   nothing in the corpus resolves to that path
    unverifiable   nothing comparable (e.g. a Russian claim about an English line)

`unverifiable` is deliberately NOT a failure. Calling a true cross-language claim
a fabrication would teach the model to delete good evidence, which is the one
outcome worse than a decorative citation.

No LLM, no network, no config, stdlib only (AGENTS.md R1). Exit 0 when every
citation is ok or unverifiable, 1 when any is wrong-content / out-of-range /
missing-file, 2 on usage error.
"""
import argparse
import gzip
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# What counts as a citation. Measurement artifact #6: the old `line_refs`
# metric counted any `:\d+`, so ordinary log TIMESTAMPS inflated it — an
# OpenSSH baseline scored 114 "refs" with zero real citations. The gate here:
# the path must carry an extension containing a letter, which `20:29:26`,
# `22:22` and `127.0.0.1:8317` all fail.
# --------------------------------------------------------------------------
PATH_RE = r"(?:[A-Za-z0-9_.\-]+[/\\])*[A-Za-z0-9_.\-]*[A-Za-z0-9_\-]\.[A-Za-z][A-Za-z0-9]{0,7}"
CITE_RE = re.compile(r"(?<![A-Za-z0-9_/\\.\-])(" + PATH_RE + r")"
                     r":(\d{1,9})(?:\s*[-–—]\s*(\d{1,9}))?(?![0-9])")
FILEISH_RE = re.compile(r"^" + PATH_RE + r"$")

# Extensions we are willing to call a *missing* file rather than "not a citation".
FILE_EXTS = {
    "log", "logs", "txt", "out", "err", "json", "jsonl", "ndjson", "csv", "tsv",
    "gz", "bz2", "xz", "zip", "md", "yaml", "yml", "toml", "ini", "conf", "cfg",
    "xml", "html", "py", "java", "kt", "go", "rs", "rb", "php", "ts", "tsx",
    "js", "jsx", "c", "h", "cc", "cpp", "cs", "sh", "bash", "sql", "tf", "env",
}

QUOTE_RE = re.compile(r'"([^"\n]{4,400})"|«([^»\n]{4,400})»|“([^”\n]{4,400})”'
                      r'|`([^`\n]{4,400})`')

TOKEN_RE = re.compile(r"[0-9A-Za-z_Ѐ-ӿ]+")
CYR_RE = re.compile(r"[Ѐ-ӿ]")
LAT_RE = re.compile(r"[A-Za-z]")

STOP = set("""
the a an and or of in on at to for from by with is are was were it this that as
not but has have had be been will would can could should there their they them
then than out into over under after before all any some such no nor only own
same too very just now here when what which who whom why how
и в во на с со по что как из для это эта эти этот был была было были при его её
их но же то так все всё уже или если бы не да мы он она они там тут когда чтобы
после перед над под между около около него неё них
""".split())

MAX_INDEX_FILES = 50000
MAX_RANGE = 40
MIN_QUOTE_TOKENS = 4
MIN_WORDS = 2            # non-numeric comparable tokens


# --------------------------------------------------------------------------
# corpus index
# --------------------------------------------------------------------------
def index_corpus(root):
    """relpath -> abspath, plus basename -> [relpath]. One walk, no content read."""
    by_rel, by_base = {}, {}
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__", ".venv")]
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            by_rel[rel] = ap
            by_base.setdefault(fn, []).append(rel)
            n += 1
            if n >= MAX_INDEX_FILES:
                return by_rel, by_base
    return by_rel, by_base


def resolve(cited, by_rel, by_base):
    """Citation path -> candidate relpaths, best guess first."""
    cited = cited.replace("\\", "/").lstrip("./")
    if cited in by_rel:
        return [cited]
    hits = [r for r in by_rel if r == cited or r.endswith("/" + cited)]
    if hits:
        return sorted(hits, key=len)
    return sorted(by_base.get(os.path.basename(cited), []), key=len)


# --------------------------------------------------------------------------
# reading only the lines we were asked about
# --------------------------------------------------------------------------
def read_lines(path, wanted):
    """-> ({lineno: text}, total_lines_or_None)

    Streams and stops at the highest line asked for. A citation to line 12 of a
    6 GB file must cost 12 lines, not 6 GB — so the total is returned only when
    the file ran out first, which is exactly the case where it is needed (the
    out-of-range message)."""
    if not wanted:
        return {}, None
    top = max(wanted)
    op = gzip.open if path.endswith(".gz") else open
    out, total, exhausted = {}, 0, True
    with op(path, "rt", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            total = i
            if i in wanted:
                out[i] = line.rstrip("\n")
            if i >= top:
                exhausted = False
                break
    return out, (total if exhausted else None)


# --------------------------------------------------------------------------
# extraction: citations + the claim sitting next to them
# --------------------------------------------------------------------------
def split_cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_table_row(line):
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 3


def extract(report):
    """-> [{path, line, range_end, claim, raw, lineno}]"""
    lines = report.splitlines()
    found = []
    for idx, line in enumerate(lines):
        if is_table_row(line):
            cells = split_cells(line)
            if all(set(c) <= set("-: ") for c in cells):
                continue
            fi = next((i for i, c in enumerate(cells)
                       if FILEISH_RE.match(c) and plausible(c)), None)
            ni = next((i for i, c in enumerate(cells)
                       if i != fi and re.fullmatch(r"\d{1,9}", c)), None)
            if fi is not None and ni is not None:
                claim = " ".join(c for i, c in enumerate(cells) if i not in (fi, ni))
                found.append({"path": cells[fi], "line": int(cells[ni]),
                              "range_end": None, "claim": claim,
                              "raw": "%s:%s" % (cells[fi], cells[ni]), "lineno": idx + 1})
                continue
        for m in CITE_RE.finditer(line):
            path, start, end = m.group(1), int(m.group(2)), m.group(3)
            if not plausible(path):
                continue
            claim = claim_for(lines, idx, line)
            found.append({"path": path, "line": start,
                          "range_end": int(end) if end else None,
                          "claim": claim, "raw": m.group(0), "lineno": idx + 1})
    return found


def plausible(path):
    ext = path.rsplit(".", 1)[-1].lower()
    return ext in FILE_EXTS


def claim_for(lines, idx, line):
    """The words next to the citation. Falls back to the line above when the
    citation stands alone (`- app.log:7` under a claim sentence)."""
    claim = strip_citations(line)
    if len(re.sub(r"[^0-9A-Za-zЀ-ӿ]", "", claim)) < 8:
        for back in range(idx - 1, max(-1, idx - 3), -1):
            prev = strip_citations(lines[back])
            if len(re.sub(r"[^0-9A-Za-zЀ-ӿ]", "", prev)) >= 8:
                claim = (prev + " " + claim).strip()
                break
    return claim


# `Linux_2k.log:128-138` — a citation that IS a code span. Remove the backticks
# with it: leaving them orphaned makes the quote matcher pair the wrong pair of
# delimiters and read the surrounding prose as a quoted log line. Calibration on
# 18 saved transcripts: that bug alone produced 8 of 48 wrong-content verdicts.
BACKTICK_CITE = re.compile(r"[`\"']\s*" + PATH_RE +
                           r":\d{1,9}(?:\s*[-–—]\s*\d{1,9})?\s*[`\"']")


def strip_citations(line):
    line = BACKTICK_CITE.sub(" ", line)
    line = CITE_RE.sub(" ", line)
    # a bare filename left in the prose only dilutes the overlap
    return re.sub(r"\b" + PATH_RE + r"\b", " ", line)


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------
def toks(text):
    out = []
    for t in TOKEN_RE.findall(text.lower()):
        if t in STOP:
            continue
        if t.isdigit():
            if len(t) >= 2:
                out.append(t)
        elif len(t) >= 3:
            out.append(t)
    return out


def checkable(claim_tokens, line):
    """Only compare what could possibly match. A Russian sentence about an
    English log line shares no tokens BY CONSTRUCTION — that is not a lie."""
    has_cyr, has_lat = bool(CYR_RE.search(line)), bool(LAT_RE.search(line))
    keep = []
    for t in claim_tokens:
        if t.isdigit():
            keep.append(t)
        elif CYR_RE.search(t):
            if has_cyr:
                keep.append(t)
        elif has_lat:
            keep.append(t)
    return keep


def words(tokens):
    """Comparable tokens that are not bare numbers."""
    return sum(1 for t in tokens if not t.isdigit())


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def quotes_in(claim):
    out = []
    for m in QUOTE_RE.finditer(claim):
        q = next(g for g in m.groups() if g is not None)
        out.append(q)
    return out


def support(claim, line, min_overlap, min_tokens):
    """-> (verdict, score, matched, total) for one candidate line."""
    for q in sorted(quotes_in(claim), key=len, reverse=True):
        if norm(q) and norm(q) in norm(line):
            return "ok", 1.0, 0, 0                       # verbatim quote
        qt = set(checkable(toks(q), line))
        # A two-word quote is usually a SEARCH TERM in prose («всегда искать
        # "Accepted password"»), not a quoted log line. Calibration on 18 saved
        # transcripts: treating those as claimed quotes produced 10 of the 54
        # wrong-content verdicts, and all 10 were wrong. Below the floor, fall
        # through to the sentence path.
        if len(qt) >= MIN_QUOTE_TOKENS and words(qt) >= MIN_WORDS:
            hit = qt & set(toks(line))
            score = len(hit) / len(qt)
            return ("ok" if score >= 0.6 else "wrong-content"), score, len(hit), len(qt)
    ct = set(checkable(toks(claim), line))
    # Numbers alone do not identify a log line. «Все 43 строки: 16,75,80,139,…»
    # is a list of line numbers, not a claim about line 16's content — judging it
    # produced pure false positives on the saved transcripts.
    if len(ct) < min_tokens or words(ct) < MIN_WORDS:
        return "unverifiable", None, 0, len(ct)
    hit = ct & set(toks(line))
    score = len(hit) / len(ct)
    return ("ok" if score >= min_overlap else "wrong-content"), score, len(hit), len(ct)


RANK = {"ok": 0, "unverifiable": 1, "wrong-content": 2, "out-of-range": 3,
        "missing-file": 4}
BAD = ("wrong-content", "out-of-range", "missing-file")


def check(report, root, min_overlap=0.34, min_tokens=3):
    by_rel, by_base = index_corpus(root)
    cites = extract(report)

    # group the line reads per file so a corpus scan happens once, not per citation
    need = {}
    for c in cites:
        c["candidates"] = resolve(c["path"], by_rel, by_base)
        for rel in c["candidates"]:
            lo = c["line"]
            hi = min(c["range_end"] or lo, lo + MAX_RANGE)
            need.setdefault(rel, set()).update(range(lo, max(lo, hi) + 1))
    cache = {}
    for rel, wanted in need.items():
        try:
            cache[rel] = read_lines(by_rel[rel], wanted)
        except OSError as e:
            cache[rel] = ({}, 0)
            sys.stderr.write("! не смог прочитать %s: %s\n" % (rel, e))

    results = []
    for c in cites:
        best = None
        for rel in c["candidates"]:
            got, total = cache.get(rel, ({}, 0))
            lo = c["line"]
            hi = min(c["range_end"] or lo, lo + MAX_RANGE)
            for n in range(lo, max(lo, hi) + 1):
                if n not in got:
                    cand = {"verdict": "out-of-range", "resolved": rel, "line": n,
                            "text": None, "score": None, "file_lines": total}
                else:
                    v, score, hit, tot = support(c["claim"], got[n], min_overlap,
                                                 min_tokens)
                    cand = {"verdict": v, "resolved": rel, "line": n,
                            "text": got[n], "score": None if score is None
                            else round(score, 3), "matched_tokens": hit,
                            "claim_tokens": tot, "file_lines": total}
                if best is None or RANK[cand["verdict"]] < RANK[best["verdict"]]:
                    best = cand
        if best is None:
            best = {"verdict": "missing-file", "resolved": None, "line": c["line"],
                    "text": None, "score": None, "file_lines": None}
        results.append({
            "citation": c["raw"], "path": c["path"], "line": c["line"],
            "range_end": c["range_end"], "report_line": c["lineno"],
            "claim": c["claim"].strip()[:400], "candidates": len(c["candidates"]),
            **best})

    summary = {"total": len(results), "ok": 0, "wrong-content": 0,
               "out-of-range": 0, "missing-file": 0, "unverifiable": 0}
    for r in results:
        summary[r["verdict"]] += 1
    summary["verified_pct"] = (round(100.0 * summary["ok"] / len(results), 1)
                               if results else None)
    return {"corpus": os.path.abspath(root), "citations": results,
            "summary": summary}


# --------------------------------------------------------------------------
def render(d):
    out = []
    mark = {"ok": "✓", "unverifiable": "?", "wrong-content": "✗",
            "out-of-range": "✗", "missing-file": "✗"}
    for r in d["citations"]:
        out.append("%s %-13s %s:%d" % (mark[r["verdict"]], r["verdict"],
                                       r["path"], r["line"]))
        if r["verdict"] == "wrong-content":
            out.append("    утверждение: %s" % r["claim"][:160])
            out.append("    строка %d говорит: %s" % (r["line"], (r["text"] or "")[:200]))
            if r["score"] is not None:
                out.append("    совпало слов: %s из %s (%.0f %%)"
                           % (r["matched_tokens"], r["claim_tokens"], r["score"] * 100))
        elif r["verdict"] == "out-of-range":
            out.append("    в файле %s строк" % r["file_lines"])
        elif r["verdict"] == "missing-file":
            out.append("    в корпусе такого файла нет")
        elif r["text"]:
            out.append("    %s" % r["text"][:200])
    s = d["summary"]
    out.append("")
    out.append("итого: %d ссылок — ok %d, wrong-content %d, out-of-range %d, "
               "missing-file %d, unverifiable %d"
               % (s["total"], s["ok"], s["wrong-content"], s["out-of-range"],
                  s["missing-file"], s["unverifiable"]))
    if s["wrong-content"]:
        out.append("НЕ ОТДАВАЙ отчёт с wrong-content: перечитай строку или удали "
                   "утверждение (SKILL.md, шаг 6).")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Проверить, что процитированные строки действительно "
                    "подтверждают утверждения отчёта.")
    ap.add_argument("report", help="файл с отчётом, или - для stdin")
    ap.add_argument("--corpus", default=".", help="корень корпуса логов")
    ap.add_argument("--min-overlap", type=float, default=0.34,
                    help="доля слов утверждения, которые обязаны быть в строке")
    ap.add_argument("--min-tokens", type=int, default=3,
                    help="меньше сопоставимых слов — вердикт unverifiable")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    text = (sys.stdin.read() if args.report == "-"
            else open(args.report, encoding="utf-8", errors="replace").read())

    d = check(text, args.corpus, args.min_overlap, args.min_tokens)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(d))
    return 1 if any(d["summary"][k] for k in BAD) else 0


if __name__ == "__main__":
    sys.exit(main())
