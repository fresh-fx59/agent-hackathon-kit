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

`unverifiable` is deliberately NOT a failure by default. Calling a true
cross-language claim a fabrication would teach the model to delete good evidence,
which is the one outcome worse than a decorative citation. `--require-quote`
turns that off for the final gate: there, an evidence row without a verbatim
substring of the cited line is `no-quote`, and that IS a failure.

    --ledger <worklist.tsv>   the stopping condition, as four numbers

Four counters, exit 0 only when all four are zero: rows still unadjudicated,
findings with no confirmed citation, bad citations, and citations that looked
like a file reference but resolved to nothing.

WHAT CHANGED HERE, and why (measured on a 649 MB, 31-file corpus): the previous
version decided "is this a citation?" from a hard-coded extension list. Anything
with an unlisted extension — or no extension at all — was dropped SILENTLY, with
no output line. Measured over the answer key of that corpus: 21 of 108 proof
locations (19.4 %) were un-citable, across 5 files; for two of the thirteen cards
the loss was total. A report that found them could not prove it. The extension
list is gone: a token is a citation if, and only if, it resolves against the
corpus index — which also still rejects `13:31`, `127.0.0.1:8317` and
`ORD-88240:11`, because none of those is a file.

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
# A CANDIDATE path — deliberately permissive, because the corpus index is the
# gate now, not a guess about extensions. Real corpora carry `.plog`, `dmesg`,
# `syslog` and `ordersync-prod-2026-07-28`; none of them has a listable extension.
PATH_RE = r"(?:[A-Za-z0-9_.\-]+[/\\])*[A-Za-z0-9_.\-]{1,120}"
CITE_RE = re.compile(r"(?<![A-Za-z0-9_/\\.\-])(" + PATH_RE + r")"
                     r":(\d{1,9})(?:\s*[-–—]\s*(\d{1,9}))?(?![0-9])")
FILEISH_RE = re.compile(r"^" + PATH_RE + r"$")

# The RESTRICTIVE pattern — a filename standing alone in prose, with a real
# extension. Only used to strip such names out of a claim before comparing it to
# a log line. It must stay narrow: the candidate pattern above matches almost any
# word, and reusing it here erased whole claims and turned 102 good citations
# into `unverifiable`.
BARE_PATH_RE = (r"(?:[A-Za-z0-9_.\-]+[/\\])*[A-Za-z0-9_.\-]*[A-Za-z0-9_\-]"
                r"\.[A-Za-z][A-Za-z0-9]{0,7}")

_HAS_ALPHA = re.compile(r"[A-Za-zА-Яа-я]")
_EXT = re.compile(r"\.[A-Za-z]{1,8}$")


def looks_like_path(p):
    """Is an UNRESOLVED candidate worth printing as `не-ссылка`?

    Yes when its last segment reads like a file name. `28/Jul/2026:14` has slashes
    but its last segment is a number, so it is a timestamp, not a lost citation."""
    last = p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not _HAS_ALPHA.search(last):
        return False
    return ("/" in p) or bool(_EXT.search(last))

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
    """Citation path -> (candidate relpaths, how). Relative path wins, always.

    Basename matching stays as a last resort, but it is now REPORTED: one corpus
    holds `syslog/node-a/syslog` and `syslog/node-b/syslog`, so a bare
    `syslog:8792` silently resolved to whichever of the two happened to agree —
    turning an ambiguous citation into a confident `ok`."""
    cited = cited.replace("\\", "/").lstrip("./")
    if cited in by_rel:
        return [cited], "rel"
    hits = sorted([r for r in by_rel if r.endswith("/" + cited)], key=len)
    if hits:
        return hits, ("rel" if len(hits) == 1 else "suffix-ambiguous")
    base = sorted(by_base.get(os.path.basename(cited), []), key=len)
    if not base:
        return [], "unresolved"
    return base, ("base" if len(base) == 1 else "base-ambiguous")


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


def citation_matches(line):
    """The CITE_RE matches on one line that are references, not quoted evidence."""
    spans = quoted_spans(line)
    return [m for m in CITE_RE.finditer(line)
            # A `file.go:104` INSIDE a quoted log line is part of the evidence,
            # not a reference to the corpus. Counting it made a correct report
            # unable to pass the ledger: a Go panic frame cites its own source.
            if not any(a <= m.start() and m.end() <= b for a, b in spans)]


def strip_line(line):
    """Blank out exactly the citations THIS line carries, and nothing else.

    Blanking by pattern instead of by position is how 102 verbatim-correct
    citations turned into `unverifiable`: the permissive candidate pattern also
    matched `13:50:01` inside the quoted log line and deleted it, so the quote
    could no longer be found in the line it came from."""
    chars = list(line)
    for m in citation_matches(line):
        a, b = m.start(), m.end()
        # a citation that IS a code span: take the delimiters with it, or the
        # quote matcher pairs the wrong pair and reads prose as a quoted line
        if a > 0 and line[a - 1] in "`\"'" and b < len(line) and line[b] in "`\"'":
            a, b = a - 1, b + 1
        for i in range(a, b):
            chars[i] = " "            # same length ⇒ quote offsets stay valid
    blanked = "".join(chars)
    # The bare-name stripper must stay OUT of the quoted evidence. Left loose it
    # deleted `c.a.checkout.web.CheckoutController` and
    # `github.com/acme/...batch.(*Retrier).flush` from the quotes themselves, so
    # a verbatim quote no longer matched the line it was copied from.
    out, pos = [], 0
    for a, b in protected_spans(line):
        if a < pos:                   # nested/overlapping span, already covered
            continue
        out.append(BARE_NAME_RE.sub(" ", blanked[pos:a]))
        out.append(blanked[a:b])
        pos = b
    out.append(BARE_NAME_RE.sub(" ", blanked[pos:]))
    return "".join(out)


def extract(report):
    """-> [{path, line, range_end, claim, raw, lineno}]"""
    lines = report.splitlines()
    stripped = [strip_line(l) for l in lines]
    found = []
    for idx, line in enumerate(lines):
        if is_table_row(line):
            cells = split_cells(line)
            if all(set(c) <= set("-: ") for c in cells):
                continue
            fi = next((i for i, c in enumerate(cells)
                       if FILEISH_RE.match(c) and looks_like_path(c)), None)
            ni = next((i for i, c in enumerate(cells)
                       if i != fi and re.fullmatch(r"\d{1,9}", c)), None)
            if fi is not None and ni is not None:
                claim = " ".join(c for i, c in enumerate(cells) if i not in (fi, ni))
                found.append({"path": cells[fi], "line": int(cells[ni]),
                              "range_end": None, "claim": claim,
                              "raw": "%s:%s" % (cells[fi], cells[ni]), "lineno": idx + 1})
                continue
        for m in citation_matches(line):
            path, start, end = m.group(1), int(m.group(2)), m.group(3)
            claim = claim_for(stripped, idx)
            found.append({"path": path, "line": start,
                          "range_end": int(end) if end else None,
                          "claim": claim, "raw": m.group(0), "lineno": idx + 1})
    return found


def claim_for(stripped, idx):
    """The words next to the citation. Falls back to the line above when the
    citation stands alone (`- app.log:7` under a claim sentence)."""
    claim = stripped[idx]
    if len(re.sub(r"[^0-9A-Za-zЀ-ӿ]", "", claim)) < 8:
        for back in range(idx - 1, max(-1, idx - 3), -1):
            prev = stripped[back]
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
BARE_NAME_RE = re.compile(r"\b" + BARE_PATH_RE + r"\b")


def strip_citations(line):
    line = BACKTICK_CITE.sub(" ", line)
    line = CITE_RE.sub(" ", line)
    # a bare filename left in the prose only dilutes the overlap
    return BARE_NAME_RE.sub(" ", line)


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


# Long quoted spans are QUOTED LOG TEXT. Short ones may themselves be citations
# («смотри "app.log:12"»), so the cut is on length, not on the delimiter.
LONG_QUOTE_MIN = 40
LONG_QUOTE_RE = re.compile(r'"[^"\n]{%d,400}"|«[^»\n]{%d,400}»|“[^”\n]{%d,400}”'
                           % (LONG_QUOTE_MIN, LONG_QUOTE_MIN, LONG_QUOTE_MIN))


def quoted_spans(line):
    return [(m.start(), m.end()) for m in LONG_QUOTE_RE.finditer(line)]


# QUOTE_RE accepts FOUR delimiters; LONG_QUOTE_RE protects only three of them.
# That asymmetry cost D04 123 of its 146 turns and 14.2M input tokens: a
# backtick-quoted SQL line was accepted as a quote and then had `c.title` and
# `c.attrs` blanked out of it by BARE_NAME_RE, so the verbatim quote no longer
# matched the line it was copied from. Log lines are full of dotted identifiers
# (Java logger names, Python modules, SQL column lists) and a backtick code span
# is the markdown convention the report format itself uses, so the gate was
# unpassable for the commonest way of quoting the commonest kind of log line.
#
# Kept SEPARATE from LONG_QUOTE_RE on purpose: `citation_matches` must go on
# treating a backtick span as a possible citation (`app.log:12` is written that
# way), and only the bare-name stripper needs to keep its hands off the evidence.
PROTECTED_SPAN_RE = re.compile(LONG_QUOTE_RE.pattern
                               + r'|`[^`\n]{%d,400}`' % LONG_QUOTE_MIN)


def protected_spans(line):
    """Spans strip_line must not touch — quoted evidence, any delimiter."""
    return [(m.start(), m.end()) for m in PROTECTED_SPAN_RE.finditer(line)]


def quotes_in(claim):
    out = []
    for m in QUOTE_RE.finditer(claim):
        q = next(g for g in m.groups() if g is not None)
        out.append(q)
    return out


def support(claim, line, min_overlap, min_tokens):
    """-> (verdict, score, matched, total, via) for one candidate line.

    `via` says HOW it was decided — "quote" only when a verbatim substring of the
    line appears in the claim. `--require-quote` accepts nothing else."""
    # EVERY quote gets its verbatim chance before any of them falls back. The
    # single loop returned on the first quote that merely cleared the token
    # floor, so a long inexact quote short-circuited a short verbatim one
    # standing right beside it — the second half of the D04 collapse, and on its
    # own enough to fail a citation whose evidence was present and exact.
    for q in sorted(quotes_in(claim), key=len, reverse=True):
        if norm(q) and norm(q) in norm(line):
            return "ok", 1.0, 0, 0, "quote"              # verbatim quote
    for q in sorted(quotes_in(claim), key=len, reverse=True):
        qt = set(checkable(toks(q), line))
        # A two-word quote is usually a SEARCH TERM in prose («всегда искать
        # "Accepted password"»), not a quoted log line. Calibration on 18 saved
        # transcripts: treating those as claimed quotes produced 10 of the 54
        # wrong-content verdicts, and all 10 were wrong. Below the floor, fall
        # through to the sentence path.
        if len(qt) >= MIN_QUOTE_TOKENS and words(qt) >= MIN_WORDS:
            hit = qt & set(toks(line))
            score = len(hit) / len(qt)
            return (("ok" if score >= 0.6 else "wrong-content"), score, len(hit),
                    len(qt), "quote-tokens")
    ct = set(checkable(toks(claim), line))
    # Numbers alone do not identify a log line. «Все 43 строки: 16,75,80,139,…»
    # is a list of line numbers, not a claim about line 16's content — judging it
    # produced pure false positives on the saved transcripts.
    if len(ct) < min_tokens or words(ct) < MIN_WORDS:
        return "unverifiable", None, 0, len(ct), "none"
    hit = ct & set(toks(line))
    score = len(hit) / len(ct)
    return (("ok" if score >= min_overlap else "wrong-content"), score, len(hit),
            len(ct), "sentence")


RANK = {"ok": 0, "unverifiable": 1, "no-quote": 2, "wrong-content": 3,
        "out-of-range": 4, "missing-file": 5}
BAD = ("wrong-content", "out-of-range", "missing-file", "no-quote")
VERDICTS = ("ok", "unverifiable", "no-quote", "wrong-content", "out-of-range",
            "missing-file")


def check(report, root, min_overlap=0.34, min_tokens=3, require_quote=False):
    by_rel, by_base = index_corpus(root)
    raw_cites = extract(report)

    # THE GATE: a token is a citation iff it resolves against the corpus index.
    # An unresolved token that still LOOKS like a file reference is reported as
    # `не-ссылка` — never dropped in silence, which is what the old extension
    # list did to 21 of 108 proof locations.
    cites, nonrefs = [], []
    for c in raw_cites:
        cand, how = resolve(c["path"], by_rel, by_base)
        c["candidates"], c["how"] = cand, how
        if cand:
            cites.append(c)
        elif looks_like_path(c["path"]):
            nonrefs.append({"citation": c["raw"], "path": c["path"],
                            "line": c["line"], "report_line": c["lineno"],
                            "verdict": "не-ссылка"})

    # group the line reads per file so a corpus scan happens once, not per citation
    need = {}
    for c in cites:
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
                    v, score, hit, tot, via = support(c["claim"], got[n],
                                                      min_overlap, min_tokens)
                    if require_quote and v != "wrong-content" and via != "quote":
                        v = "no-quote"
                    cand = {"verdict": v, "resolved": rel, "line": n,
                            "text": got[n], "score": None if score is None
                            else round(score, 3), "matched_tokens": hit,
                            "claim_tokens": tot, "file_lines": total, "via": via}
                if best is None or RANK[cand["verdict"]] < RANK[best["verdict"]]:
                    best = cand
        if best is None:
            best = {"verdict": "missing-file", "resolved": None, "line": c["line"],
                    "text": None, "score": None, "file_lines": None}
        results.append({
            "citation": c["raw"], "path": c["path"], "line": c["line"],
            "range_end": c["range_end"], "report_line": c["lineno"],
            "claim": c["claim"].strip()[:400], "candidates": len(c["candidates"]),
            "how": c["how"], **best})

    summary = dict((v, 0) for v in VERDICTS)
    summary["total"] = len(results)
    for r in results:
        summary[r["verdict"]] += 1
    summary["не-ссылка"] = len(nonrefs)
    summary["ambiguous"] = sum(1 for r in results if r["how"].endswith("ambiguous"))
    summary["verified_pct"] = (round(100.0 * summary["ok"] / len(results), 1)
                               if results else None)
    return {"corpus": os.path.abspath(root), "citations": results,
            "non_references": nonrefs, "require_quote": require_quote,
            "summary": summary}



# --------------------------------------------------------------------------
# the ledger: the stopping condition as four numbers instead of a feeling
# --------------------------------------------------------------------------
ROW_ID_RE = re.compile(r"^([A-Za-zА-Яа-я]*)0*(\d+)$")
RANGE_ID_RE = re.compile(r"^([A-Za-zА-Яа-я]*)0*(\d+)\s*[-–—]\s*([A-Za-zА-Яа-я]*)0*(\d+)$")
# «Н-2» / «H-2» is a POINTER to finding 2, i.e. a defect verdict — not «норма».
FINDING_REF_RE = re.compile(r"^[НH]\s*[-–—]?\s*\d")
DEFECT_RE = re.compile(r"^[DДdд]\b|^[DДdд]$")
GAP_RE = re.compile(r"^[XХxх]\b|^[XХxх]$")
NORM_RE = re.compile(r"^[NНnн]\b|^[NНnн]")
DIGIT_RE = re.compile(r"\d")


def _ids_of(cell):
    """A row id cell -> the ids it closes. `g041-g068` closes all 28."""
    cell = cell.strip()
    m = RANGE_ID_RE.match(cell)
    if m and (m.group(1) or "") == (m.group(3) or ""):
        a, b = int(m.group(2)), int(m.group(4))
        if 0 <= b - a <= 5000:
            # Split on the dash THAT MATCHED, not on ASCII "-". RANGE_ID_RE accepts
            # [-–—], so a Russian-writing model typing an en-dash produced a width of
            # len(whole cell), i.e. ids like g00000041 that close nothing — and the
            # counter just sat there with no diagnostic. Tested both dashes now.
            head = re.split(r"[-–—]", cell, maxsplit=1)[0].strip()
            width = len(head) - len(m.group(1) or "")
            return ["%s%0*d" % (m.group(1), width, i) for i in range(a, b + 1)]
    return [cell]


def classify_verdict(cell):
    """-> ('open'|'closed', reason). The one judgement call this tool makes."""
    cell = (cell or "").strip()
    if not cell or cell in ("?", "-", "—"):
        return "open", "не разобрано"
    if FINDING_REF_RE.match(cell):
        return "closed", ""
    if DEFECT_RE.match(cell):
        # A bare `D` closed a row while pointing at nothing, so the worklist half and
        # the report half of the mechanism were never joined — 250 rows of `D` was a
        # legal "done". A defect verdict must name the finding block that carries it
        # (`D Н-2`), which is what the skill already tells the model to write.
        if DIGIT_RE.search(cell):
            return "closed", ""
        return "open", "вердикт D без номера находки — укажи блок, напр. «D Н-2»"
    if GAP_RE.match(cell):
        # Same disease as bare `D`: `X` used to be the free verdict, so the cheapest
        # way to empty the worklist — including for the red herrings — was to mark
        # every row `X`. A genuine gap can always be named in three words; naming it
        # 250 times cannot be done without reading. Proven bypass before this guard:
        # `sed 's/\t?\t/\tX\t/'` on all 250 rows returned «ИТОГ: можно писать отчёт».
        if len(re.sub(r"^[XХxх]\W*", "", cell).strip()) >= 3:
            return "closed", ""
        return "open", "вердикт X без причины — назови, каких данных не хватает"
    if NORM_RE.match(cell):
        # «Выглядит штатно» is not a verdict. Normality is proved by frequency,
        # so a rejection without a number stays open. This is the whole reason
        # the red herrings are refutable at all: both need a measurement.
        if DIGIT_RE.search(cell):
            return "closed", ""
        return "open", "вердикт N без цифры — норма доказывается частотой"
    return "open", "непонятный вердикт: %s" % cell[:40]


def read_ledger(path):
    """-> (rows, closed_ids) from a worklist.tsv the model has written back."""
    rows, closed = [], set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if raw.startswith("#") or not raw.strip():
                continue
            cells = raw.rstrip("\n").split("\t")
            if len(cells) < 2:
                continue
            state, reason = classify_verdict(cells[1])
            ids = _ids_of(cells[0])
            rows.append({"id": cells[0].strip(), "ids": ids, "state": state,
                         "reason": reason, "cite": cells[3] if len(cells) > 3 else ""})
            if state == "closed":
                closed.update(ids)
    return rows, closed


FINDING_HEAD_RE = re.compile(r"^\s*(?:[#>*\-\s]{0,8})\*{0,2}\s*[НH]\s*[-–—]\s*(\d+)")


def findings_without_evidence(report, results):
    """A finding block with no citation whose verdict is `ok` is unproven."""
    lines = report.splitlines()
    heads = [(i + 1, m.group(1)) for i, l in enumerate(lines)
             for m in [FINDING_HEAD_RE.match(l)] if m]
    if not heads:
        return [], 0
    bounds = []
    for k, (ln, num) in enumerate(heads):
        end = heads[k + 1][0] - 1 if k + 1 < len(heads) else len(lines)
        bounds.append((num, ln, end))
    bad = []
    for num, lo, hi in bounds:
        ok = any(r["verdict"] == "ok" and lo <= r["report_line"] <= hi
                 for r in results)
        if not ok:
            bad.append(num)
    return bad, len(bounds)


def ledger(d, report, path):
    rows, closed = read_ledger(path)
    open_rows = [r for r in rows
                 if r["state"] == "open" and not (set(r["ids"]) & closed)]
    unproven, n_find = findings_without_evidence(report, d["citations"])
    s = d["summary"]
    bad = sum(s[k] for k in BAD)
    nonref = s["не-ссылка"]
    out = ["", "ЛЕДЖЕР — условие остановки (все четыре числа обязаны быть 0)",
           "  неразобранных строк: %d из %d" % (len(open_rows), len(rows)),
           "  находок без подтверждённой цитаты: %d из %d" % (len(unproven), n_find),
           "  плохих цитат (wrong-content/out-of-range/missing-file/без цитаты): %d"
           % bad,
           "  ссылок, которые я не проверял (не разрешились в корпусе): %d" % nonref]
    if open_rows:
        out.append("")
        out.append("  первые незакрытые строки:")
        for r in open_rows[:15]:
            out.append("    %-12s %-34s %s" % (r["id"], r["cite"][:34], r["reason"]))
        if len(open_rows) > 15:
            out.append("    … и ещё %d" % (len(open_rows) - 15))
    if unproven:
        out.append("  находки без цитаты со вердиктом ok: %s"
                   % ", ".join("Н-%s" % n for n in unproven))
    total = len(open_rows) + len(unproven) + bad + nonref
    out.append("")
    out.append("ИТОГ: %s" % ("можно писать отчёт" if total == 0
                             else "НЕ ЗАКОНЧЕНО — осталось %d" % total))
    return "\n".join(out), total


EXAMPLE_MAX = 90          # a suggested quote long enough to be evidence, short
EXAMPLE_MIN = 4           # enough to paste; QUOTE_RE's own floor is 4 chars


def quote_example(r):
    """A pasteable citation built from the line that was refused.

    A refusal that names the accepted delimiters is better than one that does
    not, and a refusal that hands back the finished line is better again: D07
    spent 40 turns and 11.15 M tokens reverse-engineering this checker rather
    than adding a pair of quotes, and D04 spent 123 turns doing the same thing
    before it. The next move should be a paste, not an investigation.

    The delimiter is chosen so the span cannot close it early, and «» is tried
    first because it is one of the three the bare-filename stripper protects —
    a backtick span containing a dotted identifier gets blanked and would not
    match the line it was copied from, which is the exact bug D04 died on.
    """
    text = (r.get("text") or "").strip()
    if len(text) < EXAMPLE_MIN:
        return None
    span = text
    if len(span) > EXAMPLE_MAX:
        # The tail carries the message; the head is a timestamp and a thread
        # name. Cut forward to a space so the span never starts mid-token.
        span = span[-EXAMPLE_MAX:]
        cut = span.find(" ")
        if 0 <= cut < EXAMPLE_MAX // 2:
            span = span[cut + 1:]
    span = span.strip()
    if len(span) < EXAMPLE_MIN:
        return None
    for op, cl in (("«", "»"), ('"', '"'), ("“", "”")):
        if op not in span and cl not in span:
            return "%s:%d — %s%s%s" % (r["path"], r["line"], op, span, cl)
    return None


# --------------------------------------------------------------------------
def render(d):
    out = []
    mark = {"ok": "✓", "unverifiable": "?", "no-quote": "✗", "wrong-content": "✗",
            "out-of-range": "✗", "missing-file": "✗"}
    for r in d["citations"]:
        out.append("%s %-13s %s:%d%s" % (mark[r["verdict"]], r["verdict"],
                                         r["path"], r["line"],
                                         "  [неоднозначно: %d файла]" % r["candidates"]
                                         if r["how"].endswith("ambiguous") else ""))
        if r["verdict"] == "no-quote":
            out.append("    строка не процитирована — оберни её кусок в «…», "
                       "\"…\", “…” или `…`")
            out.append("    строка %d: %s" % (r["line"], (r["text"] or "")[:200]))
            ex = quote_example(r)
            if ex:
                out.append("    например: %s" % ex)
        elif r["verdict"] == "wrong-content":
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
    for r in d["non_references"]:
        out.append("· не-ссылка     %s — в корпусе ничего с таким именем не нашлось"
                   % r["citation"])
    s = d["summary"]
    out.append("")
    out.append("итого: %d ссылок — ok %d, wrong-content %d, out-of-range %d, "
               "missing-file %d, unverifiable %d, без цитаты %d; не-ссылок %d"
               % (s["total"], s["ok"], s["wrong-content"], s["out-of-range"],
                  s["missing-file"], s["unverifiable"], s["no-quote"],
                  s["не-ссылка"]))
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
    ap.add_argument("--require-quote", action="store_true",
                    help="улика без дословной цитаты строки — это провал")
    ap.add_argument("--ledger", metavar="worklist.tsv",
                    help="условие остановки: четыре числа по рабочему списку")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    text = (sys.stdin.read() if args.report == "-"
            else open(args.report, encoding="utf-8", errors="replace").read())

    d = check(text, args.corpus, args.min_overlap, args.min_tokens,
              args.require_quote)
    if args.ledger:
        if not os.path.exists(args.ledger):
            sys.exit("нет такого файла: %s" % args.ledger)
        body, left = ledger(d, text, args.ledger)
        d["ledger"] = {"unresolved_total": left}
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(d))
    if args.ledger:
        if not args.json:
            print(body)
        return 0 if left == 0 else 1
    return 1 if any(d["summary"][k] for k in BAD) else 0


if __name__ == "__main__":
    sys.exit(main())
