#!/usr/bin/env python3
"""citecheck — does the cited line actually SAY what the report claims?

    python3 citecheck.py report.md --corpus ./logs
    qwen ... | python3 citecheck.py - --corpus ./logs --json

Why this exists. Every "measured" note in this file is a POINTER: the number,
the corpus and the date live in `skills/DESIGN-EVIDENCE.md` in the repo, one
directory above the arms, so they are not copied into a workspace along with the
skill. A tool under measurement may state a general property; it may not carry a
fact about the corpus it is scored on.

Measured on a weak model: a run cited a real file at a real line number as
evidence for a claim about a successful login, and that line was an
authentication FAILURE — the occurrences it needed were elsewhere in the same
file. Real file, real line number, **wrong content** — the shape that survives
any range check. An earlier decision killed this checker on the strength of a
run in which every citation verified, but that was measured on a *strong* model.
Lesson: never generalise a capability finding across model tiers; the weaker the
model, the more a deterministic guard earns its keep. EVIDENCE §E28.

Verdicts, one per citation:

    ok             the cited line supports the adjacent claim
    wrong-content  the line exists and does NOT support the claim  <- the whole point
    out-of-range   the file exists, the line number does not
    missing-file   nothing in the corpus resolves to that path
    ambiguous      the path resolves to MORE THAN ONE file  <- fails closed
    unverifiable   nothing comparable (e.g. a Russian claim about an English line)

`unverifiable` is deliberately NOT a failure by default. Calling a true
cross-language claim a fabrication would teach the model to delete good evidence,
which is the one outcome worse than a decorative citation. `--require-quote`
turns that off for the final gate: there, an evidence row without a verbatim
substring of the cited line is `no-quote`, and that IS a failure.

    --ledger <worklist.tsv>   the stopping condition, as checkable counters

The gate exits 0 only when every counter is zero: rows still unadjudicated,
findings with no confirmed citation, bad citations, citations that looked like a
file reference but resolved to nothing, malformed outcomes, and malformed report
evidence.

WHAT CHANGED HERE, and why: the previous version decided "is this a citation?"
from a hard-coded extension list. Anything with an unlisted extension — or no
extension at all — was dropped SILENTLY, with no output line. Measured on a
benchmark corpus, that silently lost a fifth of the addresses at which its
planted defects can be proved, and for two of its cards the loss was total: a
report that found them could not prove it. The extension list is gone: a token
is a citation if, and only if, it resolves against the corpus index — which also
still rejects `13:31`, `127.0.0.1:8317` and `ORD-88240:11`, because none of
those is a file. EVIDENCE §E25.

WHAT CHANGED HERE: ambiguity used to resolve IN FAVOUR OF THE CITATION.
`resolve()` returns every file the path could mean — measured on a many-machine
bundle, HALF its basenames live on more than one machine, so one relative path
can mean a dozen files — and `check()` then graded all of them and KEPT THE BEST
verdict. A claim that is false on the host it names came back `ok` because some
other machine's file of the same name agreed at that line number. The ambiguity
was even printed; it was simply not allowed to change the answer, which is a
gate that reports its own bypass. EVIDENCE §E26.

Now ambiguity FAILS CLOSED: more than one candidate ⇒ verdict `ambiguous`, the
candidates are named, and no line is read from any of them. ONE candidate is a
match, not a guess, so a bare `app.log` on a single-host bundle is graded exactly
as before. The real fix is upstream of the tool — cite the path from the corpus
root, which is what `logmap.py` writes and what SKILL.md now requires — and this
verdict is what makes that rule enforceable instead of advisory.

No LLM, no network, no config, stdlib only (AGENTS.md R1). Exit 0 when every
citation is ok or unverifiable, 1 when any is wrong-content / out-of-range /
missing-file / ambiguous, 2 on usage error.
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
# gate now, not a guess about extensions. Real corpora carry names like `.plog`,
# `kernring` or `batchjob-stage-2019-03-11`; none has a listable extension.
# (Illustrative names only: nothing here may name a file of the corpus under
# test, or the skill would be handing the model a place to look.)
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
def opener(path):
    """The same one-liner logmap.py uses, and for the same reason: a compressed
    file is a file, and everything downstream should see the stream a reader
    sees."""
    return gzip.open if path.endswith(".gz") else open


def looks_binary(path):
    """A NUL byte in the first 8 KB of the DECOMPRESSED stream.  Same test as
    logmap.py, so all three tools agree on what "unreadable as text" means —
    and since v19 that sentence is asserted by a test rather than believed.

    WHY THIS EXISTS (2026-08-18).  Both readers below used to open every file in
    text mode with errors="replace", which never fails: an .evtx, a .pcap or a PE
    decodes into mojibake and a citation into it could be reported `ok` against a
    line that does not exist as text.  The gate that exists to stop fabrication
    was able to launder it.  Binary evidence must be RENDERED to text first
    (prepare-corpus.sh) and cited there.

    WHY IT READS THROUGH gzip (v19).  It used to read the RAW bytes, and a gzip
    stream is full of NULs, so EVERY `.gz` was called a binary — while
    `read_lines()` below opens exactly those files with `gzip.open` and reads
    them perfectly.  The guard was rejecting citations the tool could verify.
    Measured on a production corpus: EVERY `.gz` file in it — all of them plain
    text once decompressed — came back `binary-file`, ok 0 (EVIDENCE §E27).  The guard
    itself is unchanged in what it PROTECTS: a `.gz` whose decompressed content
    holds a NUL is still a binary, because what a reader would see there is
    still not text.  A `.gz` that is not valid gzip is unreadable, hence binary.
    """
    try:
        with opener(path)(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True
    except Exception:
        # a truncated or mislabelled .gz: no reader can address lines in it
        return True


def index_corpus_ex(root):
    """relpath -> abspath, basename -> [relpath], and DID THE WALK TRUNCATE.

    The third value exists because coverage completeness is scored against this
    index. A silently short index would understate the uncovered set, which is
    the same shape of lie the completeness check was added to stop.
    """
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
                return by_rel, by_base, True
    return by_rel, by_base, False


def index_corpus(root):
    """relpath -> abspath, plus basename -> [relpath]. One walk, no content read."""
    by_rel, by_base, _truncated = index_corpus_ex(root)
    return by_rel, by_base


def resolve(cited, by_rel, by_base):
    """Citation path -> (candidate relpaths, how). Relative path wins, always.

    Basename matching stays as a last resort. A corpus can hold
    `hostlog/edge-1/hostlog` and `hostlog/edge-2/hostlog`, so a bare
    `hostlog:4211` used to resolve to whichever of the two happened to agree —
    an ambiguous citation turned into a confident `ok`. Reporting `how` was not
    enough: `check()` now REFUSES anything that ends in `-ambiguous`. A single
    candidate is still a match, so this costs a single-host bundle nothing.
    (Names invented: this file ships inside the skill, so it must not name the
    corpus under test.)"""
    cited = cited.replace("\\", "/").lstrip("./")
    if cited in by_rel:
        return [cited], "rel"
    hits = sorted([r for r in by_rel if r.endswith("/" + cited)],
                  key=lambda r: (len(r), r))
    if hits:
        return hits, ("rel" if len(hits) == 1 else "suffix-ambiguous")
    base = sorted(by_base.get(os.path.basename(cited), []),
                  key=lambda r: (len(r), r))
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
    op = opener(path)
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
        "out-of-range": 4, "missing-file": 5, "binary-file": 6}
# `ambiguous` is deliberately NOT in RANK. RANK exists to pick the best of many
# candidates, and picking one of many IS the defect: the verdict is decided
# before that loop is ever reached.
BAD = ("wrong-content", "out-of-range", "missing-file", "no-quote",
       "binary-file", "ambiguous")
VERDICTS = ("ok", "unverifiable", "no-quote", "wrong-content", "out-of-range",
            "missing-file", "binary-file", "ambiguous")


def check(report, root, min_overlap=0.34, min_tokens=3, require_quote=False):
    by_rel, by_base = index_corpus(root)
    raw_cites = extract(report)

    # THE GATE: a token is a citation iff it resolves against the corpus index.
    # An unresolved token that still LOOKS like a file reference is reported as
    # `не-ссылка` — never dropped in silence, which is what the old extension
    # list did to 21 of 108 proof locations.
    # AND: a citation that could mean several files means none of them. This is
    # settled here, before `need` is built, so an ambiguous citation costs zero
    # file reads and cannot be graded against a machine it never named.
    cites, nonrefs, ambiguous = [], [], []
    for c in raw_cites:
        cand, how = resolve(c["path"], by_rel, by_base)
        c["candidates"], c["how"] = cand, how
        if cand and how.endswith("ambiguous"):
            ambiguous.append(c)
        elif cand:
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
    binaries = set()
    for rel, wanted in need.items():
        if looks_binary(by_rel[rel]):
            binaries.add(rel)
            cache[rel] = ({}, 0)
            continue
        try:
            cache[rel] = read_lines(by_rel[rel], wanted)
        except OSError as e:
            cache[rel] = ({}, 0)
            sys.stderr.write("! не смог прочитать %s: %s\n" % (rel, e))
    for rel in sorted(binaries):
        sys.stderr.write("! %s — двоичный файл: ссылки в него НЕ проверяются "
                         "(отрендерь в текст и цитируй рендер)\n" % rel)

    results = []
    for c in ambiguous:
        results.append({
            "citation": c["raw"], "path": c["path"], "line": c["line"],
            "range_end": c["range_end"], "report_line": c["lineno"],
            "claim": c["claim"].strip()[:400], "candidates": len(c["candidates"]),
            "how": c["how"], "candidate_paths": c["candidates"],
            "verdict": "ambiguous", "resolved": None, "text": None,
            "score": None, "file_lines": None})
    for c in cites:
        best = None
        for rel in c["candidates"]:
            got, total = cache.get(rel, ({}, 0))
            lo = c["line"]
            hi = min(c["range_end"] or lo, lo + MAX_RANGE)
            if rel in binaries:
                cand = {"verdict": "binary-file", "resolved": rel, "line": lo,
                        "text": None, "score": None, "file_lines": None}
                if best is None or RANK[cand["verdict"]] < RANK[best["verdict"]]:
                    best = cand
                continue
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
            "how": c["how"], "candidate_paths": c["candidates"], **best})

    results.sort(key=lambda r: (r["report_line"], r["line"]))

    summary = dict((v, 0) for v in VERDICTS)
    summary["total"] = len(results)
    for r in results:
        summary[r["verdict"]] += 1
    summary["не-ссылка"] = len(nonrefs)
    summary["verified_pct"] = (round(100.0 * summary["ok"] / len(results), 1)
                               if results else None)
    return {"corpus": os.path.abspath(root), "citations": results,
            "non_references": nonrefs, "require_quote": require_quote,
            "summary": summary}



# --------------------------------------------------------------------------
# the ledger: the stopping condition as counters instead of a feeling
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


def flagged_lines(path):
    """-> {basename: {line numbers}} that `logmap` put on the worklist.

    P7. A coverage row says "I looked at this file and it is normal", and it
    proves it with one quoted line. Any line used to pass. Measured on the run
    that missed the intrusion: the coverage row for `System.jsonl` quoted line
    192 — a real line, a true quote, and 71 lines away from the service install
    that made the whole corpus interesting. Quoting an arbitrary line proves
    that the file was opened, not that the flagged content was read.

    So the quote must land on a line the mapper actually flagged for that file.
    The index is built from the worklist's reference column, which is where
    `logmap` writes `path:line` for every row it emitted.
    """
    out = {}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for raw in fh:
            if raw.startswith("#") or not raw.strip():
                continue
            cells = raw.rstrip("\n").split("\t")
            if len(cells) < 4:
                continue
            ref = cells[3].strip()
            if ":" not in ref:
                continue
            head, _, tail = ref.rpartition(":")
            if not tail.isdigit() or not head:
                continue
            out.setdefault(os.path.basename(head).lower(), set()).add(int(tail))
    return out


def _flagged_for(flagged, path):
    """The flagged lines of one coverage path, whatever spelling it uses."""
    if not flagged or not path:
        return None
    base = os.path.basename(path).lower()
    hit = flagged.get(base)
    if hit:
        return hit
    # `logmap` writes `rendered/Foo-4Admin.jsonl` where the report may write the
    # percent-escaped original `Foo%4Admin.jsonl`. Compare on the shape both
    # spellings share.
    key = base.replace("%4", "-4")
    return flagged.get(key)


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


# The head of a finding block — and ONLY the head. `reference/report-format.md`
# tells the model to write a finding as
#
#     Н-n · заголовок
#
# the number, the interpunct, the title. So that is what is required here: the
# interpunct, then something that is not whitespace.
#
# The interpunct is the whole discriminator, and it is not an arbitrary choice.
# Prose refers to a finding by its number all the time — «это тот же процесс,
# что и Н-3» — and prose gets wrapped. A pattern that asks only for a line
# STARTING with the number reads such a reference as a new finding whenever the
# wrap happens to break in front of it. That is not a hypothetical: the same
# report handed over twice, wrapped into a file and flat into the message,
# parsed a different number of findings, and the difference was one wrapped
# reference. A sentence carries no «исход:» line, so the phantom block was then
# counted as a finding that forgot one — and a report that had stated the
# outcome of every finding it wrote was told it had not. The interpunct cannot
# appear in that position by accident: it is not punctuation a sentence uses.
#
# Everything the pattern tolerated IN FRONT of the number is unchanged — a
# markdown heading, a bullet, a quote marker, bold, indentation, or nothing at
# all. The format's own example is indented and carries no marker, so a marker
# cannot be what is required; the separator can, because the format has always
# demanded it.
# interpunct, bullet, dot operator: one glyph, three code points a keyboard
# and a model both reach for.
FINDING_HEAD_SEP = "·•∙"
FINDING_HEAD_RE = re.compile(
    r"^\s*(?:[#>*\-\s]{0,8})\*{0,2}\s*[НH]\s*[-–—]\s*(\d+)"
    r"\s*[" + FINDING_HEAD_SEP + r"]\s*\S")


def finding_blocks(report, structural=None):
    """-> [(number, first line, last line)] for every live `Н-n` block, 1-based.

    One place decides where a finding starts and stops, because two checks now
    ask that question: "does this block prove anything" and "does this block
    say whether the thing happened". A finding also stops at the next markdown
    section at the same or higher level; otherwise the final finding swallows the
    later `К-n` candidate and coverage sections, and reads their `исход:` lines as
    a second outcome of the finding. Markdown fence contents are examples, never
    report structure.
    """
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    heads = []
    head_re = re.compile(r"^\s*(#{1,6})\s*(.*?)\s*#*\s*$")
    for i, line in enumerate(lines, 1):
        if not structural[i - 1]:
            continue
        m = FINDING_HEAD_RE.match(line)
        if not m:
            continue
        hm = head_re.match(line)
        heads.append((i, m.group(1), len(hm.group(1)) if hm else None))
    out = []
    for k, (ln, num, level) in enumerate(heads):
        end = heads[k + 1][0] - 1 if k + 1 < len(heads) else len(lines)
        for j in range(ln + 1, end + 1):
            if not structural[j - 1]:
                continue
            hm = head_re.match(lines[j - 1])
            if not hm:
                continue
            if level is None or len(hm.group(1)) <= level:
                end = j - 1
                break
        out.append((num, ln, end))
    return out


def findings_without_evidence(report, results):
    """A finding block with no citation whose verdict is `ok` is unproven."""
    bounds = finding_blocks(report)
    if not bounds:
        return [], 0
    bad = []
    for num, lo, hi in bounds:
        ok = any(r["verdict"] == "ok" and lo <= r["report_line"] <= hi
                 for r in results)
        if not ok:
            bad.append(num)
    return bad, len(bounds)


# --------------------------------------------------------------------------
# the outcome of a finding: three states, no fourth
# --------------------------------------------------------------------------
# A finding block used to have no field that says whether the thing it
# describes actually HAPPENED. It had «улики» — what supports it — and «чем
# опровергал» — the method, which every block carries whatever the answer was.
# So a block that reads "I checked this and it was nothing" is written in
# exactly the same fields as one that reads "I found an intrusion", and the
# difference lived only in prose. Measured across eight runs: twelve planted
# non-defects were written up as findings and none as refuted.
#
# Three states, and they are the same three the final verdict has, one level
# down. That is not a coincidence — it is the composition rule: the report's
# answer is the strongest outcome any of its findings carries, so a register
# full of «норма» cannot end in «скомпрометирована».
#
#   успех    — действие достигло цели
#   попытка  — действие видно, и видно, что цели оно НЕ достигло
#   норма    — проверено и объяснено штатным поведением
#
# There is deliberately no fourth state for "suspicious". A vocabulary with an
# escape hatch measures nothing: everything uncertain goes into the hatch and
# the field stops discriminating. That is also why the grammar is a WHOLE LINE
# and a qualifier on the same line is refused — «успех, но не доказан» is the
# fourth state coming back through the tail of the sentence.
#
# The three words are pairwise non-containing, so a scorer regex cannot read
# one inside another (which is what would happen with «удался»/«не удался»).
OUTCOME_ORDER = ("норма", "попытка", "успех")
OUTCOME_VERDICT = {"норма": "clean",
                   "попытка": "attacked-not-proven",
                   "успех": "compromised"}

_WRAP = r"(?:\*\*|__|\*|`)?"
OUTCOME_LINE_RE = re.compile(
    r"^\s*[#>*\-\s]{0,8}" + _WRAP + r"\s*исход\s*" + _WRAP + r"\s*[:：]\s*"
    + _WRAP + r"\s*(успех|попытка|норма)\s*" + _WRAP + r"\s*\.?\s*$",
    re.IGNORECASE)
OUTCOME_HEAD_RE = re.compile(
    r"^\s*[#>*\-\s]{0,8}" + _WRAP + r"\s*исход\s*" + _WRAP + r"\s*[:：]",
    re.IGNORECASE)

# The three answers the report ends with, in the wording the operator's own
# request uses. Restated here rather than imported: this file ships inside the
# skill and has to work with nothing else on the machine.
VERDICT_SECTION_RE = re.compile(
    r"(?:^|\n)[^\n]{0,20}(?:ВЕРДИКТ|VERDICT)\b[^\n]*\n?(.*)$",
    re.IGNORECASE | re.DOTALL)
VERDICT_PATTERNS = (
    ("attacked-not-proven", (r"атаков\w*[^.\n]{0,40}не\s+доказан",
                             r"попытк\w*[^.\n]{0,60}не\s+подтвержда",
                             r"attacked[-\s,]+not\s+prov",
                             r"атаковали,?\s+но")),
    ("compromised", (r"скомпрометирован", r"компрометац\w+\s+подтвержд",
                     r"\bcompromised\b")),
    ("clean", (r"\bчисто\b", r"признак\w*\s+вмешательств\w*\s+нет",
               r"\bclean\b", r"не\s+скомпрометирован")),
)


def outcome_scan(lines, lo, hi, structural=None):
    """One block -> (outcome_or_None, report_line, bad_or_None).

    The outcome grammar has exactly one owner and exactly one accepted line. A
    second valid `исход:` line is not a tie-breaker; it means the block states two
    outcomes and the report is not machine-readable. An invalid `исход:` head in
    the same block is the same class of failure, even if another line happened to
    be valid. Fence contents are samples, not outcome structure.
    """
    valid, invalid = [], []
    for i in range(lo, min(hi, len(lines)) + 1):
        if structural is not None and not structural[i - 1]:
            continue
        raw = lines[i - 1]
        m = OUTCOME_LINE_RE.match(raw)
        if m:
            valid.append((i, m.group(1).lower(), raw.strip()))
            continue
        if OUTCOME_HEAD_RE.match(raw):
            invalid.append((i, raw.strip()))
    if len(valid) == 1 and not invalid:
        return valid[0][1], valid[0][0], None
    if len(valid) > 1:
        return None, valid[1][0], "несколько строк исхода: " + valid[1][2]
    if invalid:
        return None, invalid[0][0], invalid[0][1]
    return None, None, None


def finding_outcomes(report):
    """-> [{finding, outcome, report_line, bad}] — one record per `Н-n` block.

    `outcome` is one of OUTCOME_ORDER, or None. `bad` carries the offending
    text when the block wrote an outcome line that is not in the vocabulary,
    so "never said" and "said something else" stay different failures: one is
    a forgotten line, the other is an argument with the vocabulary."""
    lines = report.splitlines()
    structural = structural_mask(lines)
    out = []
    for num, lo, hi in finding_blocks(report, structural):
        outcome, report_line, bad = outcome_scan(lines, lo, hi, structural)
        out.append({"finding": num, "outcome": outcome,
                    "report_line": report_line, "bad": bad})
    return out


def implied_verdict(report):
    """The answer the register itself adds up to — the strongest outcome any
    finding carries. None when no finding states one."""
    seen = [r["outcome"] for r in finding_outcomes(report) if r["outcome"]]
    if not seen:
        return None
    return OUTCOME_VERDICT[max(seen, key=OUTCOME_ORDER.index)]


def stated_verdict(report):
    """The answer the report's own closing section states, or None."""
    m = VERDICT_SECTION_RE.search(report or "")
    if not m:
        return None
    scope = m.group(1)[:1200]
    hits = []
    for name, pats in VERDICT_PATTERNS:
        for p in pats:
            mm = re.search(p, scope, re.IGNORECASE)
            if mm:
                hits.append((mm.start(), name))
                break
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def outcomes_of(report):
    """The whole outcome picture of one document, as a scorer wants it."""
    rows = finding_outcomes(report)
    missing = [r["finding"] for r in rows if not r["outcome"] and not r["bad"]]
    invalid = [{"finding": r["finding"], "text": r["bad"],
                "report_line": r["report_line"]}
               for r in rows if not r["outcome"] and r["bad"]]
    implied = implied_verdict(report)
    stated = stated_verdict(report)
    contradiction = bool(implied and stated and implied != stated)
    return {"vocabulary": list(OUTCOME_ORDER),
            "grammar": "исход: " + "|".join(OUTCOME_ORDER),
            "findings": [{"finding": r["finding"], "outcome": r["outcome"],
                          "report_line": r["report_line"]} for r in rows],
            "missing": missing, "invalid": invalid,
            "implied": implied, "stated": stated,
            "contradiction": contradiction,
            "blocking": len(missing) + len(invalid) + (1 if contradiction else 0)}


def render_outcomes(o):
    out = []
    n = len(o["findings"])
    if not n:
        return ""
    named = len([f for f in o["findings"] if f["outcome"]])
    out.append("ИСХОДЫ находок: %d из %d названы · отчёт складывается в «%s»"
               % (named, n, o["implied"] or "—"))
    if o["missing"]:
        out.append("  без строки исхода: %s" % ", ".join("Н-%s" % f
                                                         for f in o["missing"]))
    for bad in o["invalid"]:
        out.append("  Н-%s, строка %s: %r — не из словаря"
                   % (bad["finding"], bad["report_line"], bad["text"][:80]))
    if o["missing"] or o["invalid"]:
        out.append("  в каждом блоке Н-n обязана быть отдельная строка, целиком:")
        for tok in OUTCOME_ORDER:
            out.append("      исход: %s" % tok)
        out.append("  ровно одно слово из трёх и больше ничего в этой строке — "
                   "«успех, но…» это уже четвёртый исход, а их три.")
    if o["contradiction"]:
        out.append("  ПРОТИВОРЕЧИЕ: находки складываются в «%s», а раздел "
                   "ВЕРДИКТ говорит «%s». Одно из двух неверно."
                   % (o["implied"], o["stated"]))
    return "\n".join(out)


# --------------------------------------------------------------------------
# v26: every observation in the report is addressable or explicitly limited. EVIDENCE §E30
# --------------------------------------------------------------------------
ATTRIBUTION_ORDER = ("установлена", "не установлена")
ATTRIBUTION_LINE_RE = re.compile(
    r"^\s*[#>*\-\s]{0,8}" + _WRAP + r"\s*атрибуция\s*" + _WRAP
    + r"\s*[:：]\s*" + _WRAP + r"\s*(установлена|не\s+установлена)\s*"
    + _WRAP + r"\s*\.?\s*$", re.IGNORECASE)
ATTRIBUTION_HEAD_RE = re.compile(
    r"^\s*[#>*\-\s]{0,8}" + _WRAP + r"\s*атрибуция\s*" + _WRAP
    + r"\s*[:：]", re.IGNORECASE)

SECTION_HEAD_RE = re.compile(r"^\s*(#{1,6})\s*(.*?)\s*#*\s*$")
REJECTED_SECTION_RE = re.compile(r"отклон|отвергн|reject", re.IGNORECASE)
COVERAGE_SECTION_RE = re.compile(r"покрыти|coverage", re.IGNORECASE)
CANDIDATE_HEAD_RE = re.compile(
    r"^\s*(?:[#>*\-\s]{0,8})\*{0,2}\s*[КK]\s*[-–—]\s*(\d+)"
    r"\s*[" + FINDING_HEAD_SEP + r"]\s*\S")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
COVERAGE_FACT = ("наблюдение", "факт")
COVERAGE_NO_ADDRESS = ("пусто", "двоичный", "нечитабельно", "не смотрел")
NO_ADDRESS_DETAIL_GRAMMAR = {
    "пусто": r"(?:байт|bytes|размер|size|строк|lines)\s*[:=]\s*0",
    "двоичный": r"(?:формат|format|тип|type)\s*[:=]\s*(?:двоичный|binary)|(?:nul|binary)\s*[:=]\s*(?:1|true|yes|да)",
    "нечитабельно": r"(?:ошибка|error|errno|кодировка|encoding|gzip|доступ|permission)\s*[:=]\s*[^;,.\s]+",
    "не смотрел": r"(?:причина|reason)\s*[:=]\s*(?:лимит|limit|дубликат|duplicate|scope|область|пропуск|skip|sampling|выборка)",
}
ITEMISH_RE = re.compile(r"^(?:\s{0,3}(?:[-*+]|\d+[.)])\s+|\s*\|)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def structural_mask(lines):
    """True for lines Markdown parsers may treat as report structure.

    This follows score-report.py's deliberately simple fence rule: either triple
    backticks or triple tildes toggles the mask. Fence content is an example, not
    a live finding, candidate, section or coverage row.
    """
    inside, out = False, []
    for line in lines:
        if FENCE_RE.match(line):
            inside = not inside
            out.append(False)
        else:
            out.append(not inside)
    return out


def _sections(report, structural=None):
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    heads = []
    for i, line in enumerate(lines, 1):
        if not structural[i - 1]:
            continue
        m = SECTION_HEAD_RE.match(line)
        if m:
            heads.append({"level": len(m.group(1)), "title": m.group(2),
                          "lineno": i})
    for j, h in enumerate(heads):
        end = len(lines) + 1
        for k in range(j + 1, len(heads)):
            if heads[k]["level"] <= h["level"]:
                end = heads[k]["lineno"]
                break
        h["body_from"] = h["lineno"] + 1
        h["body_to"] = end
    return heads


def _spans_for(sections, title_re):
    return [(s["body_from"], s["body_to"]) for s in sections
            if title_re.search(s["title"])]


def _in_spans(line, spans):
    return any(lo <= line < hi for lo, hi in spans)


def _line_citations(citations, lo, hi):
    return [r for r in citations if lo <= r.get("report_line", 0) <= hi]


def _has_ok_quote(citations):
    return any(r.get("verdict") == "ok" and r.get("via") == "quote"
               for r in citations)


def finding_attributions(report, blocks=None, structural=None):
    """The closed attribution line for each live finding block."""
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    blocks = blocks if blocks is not None else finding_blocks(report, structural)
    out = []
    for num, lo, hi in blocks:
        valid, invalid = [], []
        for i in range(lo, min(hi, len(lines)) + 1):
            if not structural[i - 1]:
                continue
            raw = lines[i - 1]
            m = ATTRIBUTION_LINE_RE.match(raw)
            if m:
                valid.append((i, " ".join(m.group(1).lower().split()), raw.strip()))
                continue
            if ATTRIBUTION_HEAD_RE.match(raw):
                invalid.append((i, raw.strip()))
        if len(valid) == 1 and not invalid:
            out.append({"finding": num, "attribution": valid[0][1],
                        "report_line": valid[0][0], "bad": None})
        elif len(valid) > 1:
            out.append({"finding": num, "attribution": None,
                        "report_line": valid[1][0],
                        "bad": "несколько строк атрибуции: " + valid[1][2]})
        elif invalid:
            out.append({"finding": num, "attribution": None,
                        "report_line": invalid[0][0], "bad": invalid[0][1]})
        else:
            out.append({"finding": num, "attribution": None,
                        "report_line": None, "bad": None})
    return out


def _candidate_blocks(report, spans, structural=None):
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    heads = []
    for i, line in enumerate(lines, 1):
        if structural[i - 1] and _in_spans(i, spans):
            m = CANDIDATE_HEAD_RE.match(line)
            if m:
                heads.append((i, m.group(1)))
    out = []
    for j, (lo, num) in enumerate(heads):
        hi = spans[-1][1] - 1
        for a, b in spans:
            if a <= lo < b:
                hi = b - 1
                break
        if j + 1 < len(heads):
            hi = min(hi, heads[j + 1][0] - 1)
        out.append(("К-%s" % num, lo, hi))
    return out


def _coverage_rows(report, spans, structural=None):
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    rows = []
    for lo, hi in spans:
        for i in range(lo, min(hi, len(lines) + 1)):
            if not structural[i - 1]:
                continue
            raw = lines[i - 1]
            if not raw.lstrip().startswith("|"):
                continue
            if TABLE_SEP_RE.match(raw):
                continue
            cells = split_cells(raw)
            if cells and cells[0].strip().lower() in ("путь", "path", "файл", "file"):
                continue
            if len(cells) < 3:
                rows.append({"report_line": i, "raw": raw.strip(), "path": None,
                             "status": None, "detail": None,
                             "problem": "строка покрытия не по схеме path|status|detail"})
                continue
            rows.append({"report_line": i, "raw": raw.strip(), "path": cells[0],
                         "status": cells[1].lower(), "detail": " | ".join(cells[2:]),
                         "problem": None})
    return rows


def valid_no_address_detail(status, detail):
    """Closed positive grammar for coverage rows with no addressable content.

    These rows may state only why there is no addressable line.  They do not get a
    content-word denylist: any free prose that is not one of the explicit access
    facts fails closed instead of trying to guess whether it smuggles a conclusion.
    """
    pat = NO_ADDRESS_DETAIL_GRAMMAR.get((status or "").strip().lower())
    text = (detail or "").strip().lower()
    return bool(pat and re.fullmatch(pat, text, re.IGNORECASE))


def normalized_coverage_path(path):
    """A safe corpus-relative spelling for one coverage-table path."""
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return None, "пустой путь"
    if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        return None, "путь должен быть относительным к корню корпуса"
    parts = raw.split("/")
    if ".." in parts:
        return None, "путь с переходом .. за пределы корпуса"
    normalized = "/".join(p for p in parts if p and p != ".")
    return (normalized, None) if normalized else (None, "пустой путь")


def resolve_coverage_path(path, by_rel, by_base):
    """Resolve a coverage row without allowing `resolve()` to erase traversal."""
    normalized, problem = normalized_coverage_path(path)
    if problem:
        return None, [], problem
    candidates, _how = resolve(normalized, by_rel, by_base)
    if not candidates:
        return normalized, [], "файла нет в корпусе"
    if len(candidates) != 1:
        return normalized, candidates, "путь неоднозначен"
    return normalized, candidates, None


def report_evidence(report, checked=None):
    """Machine-readable v26 evidence grammar for findings, rejections and coverage."""
    citations = (checked or {}).get("citations") or []
    lines = report.splitlines()
    structural = structural_mask(lines)
    sections = _sections(report, structural)
    rejected_spans = _spans_for(sections, REJECTED_SECTION_RE)
    coverage_spans = _spans_for(sections, COVERAGE_SECTION_RE)

    finding_blocks_here = finding_blocks(report, structural)
    attrs = finding_attributions(report, finding_blocks_here, structural)
    attr_missing = [r["finding"] for r in attrs if not r["attribution"] and not r["bad"]]
    attr_invalid = [{"finding": r["finding"], "text": r["bad"],
                     "report_line": r["report_line"]}
                    for r in attrs if not r["attribution"] and r["bad"]]
    finding_seen = {}
    for num, lo, _hi in finding_blocks_here:
        finding_seen.setdefault("Н-%s" % num, []).append(lo)
    finding_dupes = sorted(fid for fid, locs in finding_seen.items() if len(locs) > 1)

    cand_items, cand_missing_outcome, cand_invalid_outcome = [], [], []
    cand_missing_citation, cand_invalid_citation = [], []
    blocks = _candidate_blocks(report, rejected_spans, structural)
    rejected_missing_section = not rejected_spans
    rejected_empty_section = bool(rejected_spans) and not blocks
    cand_seen, cand_dupes = {}, []
    for cid, lo, hi in blocks:
        cand_seen.setdefault(cid, []).append(lo)
        oc, oc_line, bad = outcome_scan(lines, lo, hi, structural)
        cites = _line_citations(citations, lo, hi)
        good = _has_ok_quote(cites)
        cand_items.append({"id": cid, "line": lo, "outcome": oc,
                           "citations": len(cites), "quoted_ok": good})
        if not oc and not bad:
            cand_missing_outcome.append(cid)
        if bad:
            cand_invalid_outcome.append({"id": cid, "line": oc_line, "text": bad})
        if not cites:
            cand_missing_citation.append(cid)
        elif not good:
            cand_invalid_citation.append(cid)
    cand_dupes = sorted(cid for cid, locs in cand_seen.items() if len(locs) > 1)

    malformed_candidates = []
    if rejected_spans:
        in_candidate = []
        for _cid, lo, hi in blocks:
            in_candidate.append((lo, hi + 1))
        for i, line in enumerate(lines, 1):
            if not structural[i - 1] or not _in_spans(i, rejected_spans):
                continue
            if not line.strip() or CANDIDATE_HEAD_RE.match(line):
                continue
            if _in_spans(i, in_candidate):
                continue
            if ITEMISH_RE.match(line):
                malformed_candidates.append({"line": i, "text": line.strip()[:120]})

    cov_rows = _coverage_rows(report, coverage_spans, structural)
    coverage_missing_section = not coverage_spans
    coverage_empty_section = bool(coverage_spans) and not cov_rows
    cov_missing_citation, cov_invalid_citation, cov_mismatched_citation = [], [], []
    cov_unsupported, cov_smuggled, cov_malformed = [], [], []
    cov_traversal, cov_ambiguous, cov_missing_path = [], [], []
    cov_false_empty, cov_false_binary, cov_duplicate_paths = [], [], []
    cov_unflagged_citation = []
    flagged = (checked or {}).get("flagged") or {}
    cov_observed = cov_no_address = 0
    corpus = (checked or {}).get("corpus")
    index_truncated = False
    if corpus and os.path.isdir(corpus):
        by_rel, by_base, index_truncated = index_corpus_ex(corpus)
    else:
        by_rel, by_base = {}, {}
    path_rows = {}
    for row in cov_rows:
        if row["problem"]:
            cov_malformed.append(row)
            continue
        status = (row["status"] or "").strip().lower()
        normalized, candidates, path_problem = resolve_coverage_path(
            row.get("path"), by_rel, by_base)
        row["normalized_path"] = normalized
        row["resolved_path"] = candidates[0] if len(candidates) == 1 else None
        if path_problem:
            if ".." in path_problem:
                cov_traversal.append(row)
            elif candidates:
                cov_ambiguous.append(row)
            else:
                cov_missing_path.append(row)
        elif row["resolved_path"]:
            path_rows.setdefault(row["resolved_path"], []).append(row)

        lo = hi = row["report_line"]
        cites = _line_citations(citations, lo, hi)
        if status in COVERAGE_FACT:
            cov_observed += 1
            if not cites:
                cov_missing_citation.append(row)
            elif not _has_ok_quote(cites):
                cov_invalid_citation.append(row)
            elif row["resolved_path"] is not None and not any(
                    c.get("verdict") == "ok" and c.get("via") == "quote"
                    and c.get("resolved") == row["resolved_path"] for c in cites):
                cov_mismatched_citation.append(row)
            else:
                want = _flagged_for(flagged, row.get("path") or row.get("normalized_path"))
                if want and not any(
                        c.get("verdict") == "ok" and c.get("line") in want for c in cites):
                    cov_unflagged_citation.append(row)
        elif status in COVERAGE_NO_ADDRESS:
            cov_no_address += 1
            if cites:
                cov_unsupported.append(row)
            if not valid_no_address_detail(status, row.get("detail")):
                cov_smuggled.append(row)
            if row["resolved_path"] and status == "пусто":
                try:
                    if os.path.getsize(by_rel[row["resolved_path"]]) != 0:
                        cov_false_empty.append(row)
                except OSError:
                    cov_missing_path.append(row)
            if row["resolved_path"] and status == "двоичный":
                if not looks_binary(by_rel[row["resolved_path"]]):
                    cov_false_binary.append(row)
        else:
            cov_unsupported.append(row)
    for resolved, rows in sorted(path_rows.items()):
        if len(rows) > 1:
            cov_duplicate_paths.append({"path": resolved,
                                        "lines": [r["report_line"] for r in rows]})

    # COMPLETENESS. Scoring only the rows the report chose to show made deletion
    # the cheapest way to raise the score: on the v36 winevtx run a 33,326-byte
    # report with 128 coverage rows scored «осталось 80», and the 18,052-byte
    # rewrite that kept 16 of them scored «осталось 32» — 65 ok citations and the
    # attacker IP thrown away for a better number. A corpus file with no row is
    # now a defect, so removing a row can only ever cost.
    #
    # Nothing is exempt: the row grammar already has «пусто», «двоичный»,
    # «нечитабельно» and «не смотрел», so every file on disk can be answered.
    cov_uncovered = []
    if by_rel and not index_truncated:
        cov_uncovered = sorted(set(by_rel) - set(path_rows))

    blocking = (len(attr_missing) + len(attr_invalid) + len(finding_dupes)
                + (1 if rejected_missing_section else 0)
                + (1 if rejected_empty_section else 0)
                + len(cand_dupes)
                + len(cand_missing_outcome) + len(cand_invalid_outcome)
                + len(cand_missing_citation) + len(cand_invalid_citation)
                + len(malformed_candidates)
                + (1 if coverage_missing_section else 0)
                + (1 if coverage_empty_section else 0)
                + len(cov_missing_citation) + len(cov_invalid_citation)
                + len(cov_mismatched_citation) + len(cov_unflagged_citation)
                + len(cov_unsupported) + len(cov_smuggled) + len(cov_malformed)
                + len(cov_traversal) + len(cov_ambiguous) + len(cov_missing_path)
                + len(cov_false_empty) + len(cov_false_binary)
                + len(cov_duplicate_paths)
                + len(cov_uncovered)
                + (1 if index_truncated else 0))
    return {
        "grammar": {
            "finding_attribution": "атрибуция: установлена|не установлена",
            "rejected_item": "К-n · заголовок + исход: успех|попытка|норма + path:line «quote»",
            "coverage_row": "| path | наблюдение|факт | path:line «quote» | OR | path | пусто|двоичный|нечитабельно|не смотрел | closed access-limit detail |",
            "coverage_no_address": list(COVERAGE_NO_ADDRESS),
            "coverage_no_address_detail": {
                "пусто": "байт=0|bytes=0|размер=0|size=0|строк=0|lines=0",
                "двоичный": "формат=двоичный|format=binary|тип=двоичный|type=binary|nul=1|binary=true",
                "нечитабельно": "ошибка=<код>|error=<code>|errno=<code>|кодировка=<code>|encoding=<code>|gzip=<code>|доступ=<code>|permission=<code>",
                "не смотрел": "причина=лимит|limit|дубликат|duplicate|scope|область|пропуск|skip|sampling|выборка",
            },
        },
        "attribution": {"findings": len(attrs), "missing": attr_missing,
                         "invalid": attr_invalid, "duplicate_ids": finding_dupes},
        "rejected": {"items": cand_items,
                      "missing_section": rejected_missing_section,
                      "empty_section": rejected_empty_section,
                      "duplicate_ids": cand_dupes,
                      "missing_outcome": cand_missing_outcome,
                      "invalid_outcome": cand_invalid_outcome,
                      "missing_citation": cand_missing_citation,
                      "invalid_citation": cand_invalid_citation,
                      "malformed_items": malformed_candidates},
        "coverage": {"rows": len(cov_rows), "observations": cov_observed,
                     "no_address": cov_no_address,
                     "missing_section": coverage_missing_section,
                     "empty_section": coverage_empty_section,
                     "missing_citation": [r["report_line"] for r in cov_missing_citation],
                     "invalid_citation": [r["report_line"] for r in cov_invalid_citation],
                     "mismatched_citation": [r["report_line"] for r in cov_mismatched_citation],
                     "unflagged_citation": [r["report_line"] for r in cov_unflagged_citation],
                     "traversal_path": [r["report_line"] for r in cov_traversal],
                     "ambiguous_path": [{"line": r["report_line"], "path": r["path"],
                                         "candidates": resolve_coverage_path(
                                             r["path"], by_rel, by_base)[1]}
                                        for r in cov_ambiguous],
                     "missing_path": [r["report_line"] for r in cov_missing_path],
                     "false_empty": [r["report_line"] for r in cov_false_empty],
                     "false_binary": [r["report_line"] for r in cov_false_binary],
                     "duplicate_paths": cov_duplicate_paths,
                     "uncovered_paths": cov_uncovered,
                     "index_truncated": index_truncated,
                     "unsupported_no_address": [r["report_line"] for r in cov_unsupported],
                     "invalid_no_address_detail": [r["report_line"] for r in cov_smuggled],
                     "content_claim_in_no_address": [r["report_line"] for r in cov_smuggled],
                     "malformed": [r["report_line"] for r in cov_malformed]},
        "blocking": blocking,
    }


def render_report_evidence(e):
    if not e:
        return ""
    out = ["ОТЧЁТНЫЕ НАБЛЮДЕНИЯ v26: %d блокирующих дефектов" % e["blocking"]]
    a = e["attribution"]
    if a["findings"]:
        out.append("  атрибуция находок: %d блоков, без строки %d, неверных %d, повторных id %d"
                   % (a["findings"], len(a["missing"]), len(a["invalid"]),
                      len(a.get("duplicate_ids") or [])))
    r = e["rejected"]
    if r["items"] or r["malformed_items"] or r.get("missing_section") or r.get("empty_section"):
        out.append("  отклонённые кандидаты: %d блоков, без исхода %d, без цитаты %d, неверных цитат %d, не по схеме %d"
                   % (len(r["items"]), len(r["missing_outcome"]) + len(r["invalid_outcome"]),
                      len(r["missing_citation"]), len(r["invalid_citation"]),
                      len(r["malformed_items"])))
    c = e["coverage"]
    if c["rows"] or c.get("missing_section") or c.get("empty_section"):
        out.append("  покрытие: %d строк, наблюдений %d, no-address %d, без цитаты %d, неверных/чужих цитат %d/%d, неверных путей %d, дубликатов %d"
                   % (c["rows"], c["observations"], c["no_address"],
                      len(c["missing_citation"]), len(c["invalid_citation"]),
                      len(c.get("mismatched_citation") or []),
                      len(c.get("traversal_path") or []) + len(c.get("ambiguous_path") or [])
                      + len(c.get("missing_path") or []),
                      len(c.get("duplicate_paths") or [])))
    unc = c.get("uncovered_paths") or []
    if c.get("index_truncated"):
        out.append("  ИНДЕКС КОРПУСА ОБРЕЗАН на %d файлах — полноту покрытия "
                   "проверить нечем. Разбей корпус или подними MAX_INDEX_FILES; "
                   "молча зачесть неполный список нельзя." % MAX_INDEX_FILES)
    if unc:
        out.append("  НЕ ПОКРЫТО: %d файлов корпуса нет в таблице покрытия"
                   % len(unc))
        for path in unc[:20]:
            out.append("    · %s" % path)
        if len(unc) > 20:
            out.append("    · … и ещё %d" % (len(unc) - 20))
        out.append("  Каждый файл корпуса обязан получить строку. Не смотрел — "
                   "так и напиши: «не смотрел» + причина=лимит. Удаление строки "
                   "теперь только ухудшает счёт.")
    if a["missing"]:
        out.append("  находки без атрибуции: %s" % ", ".join("Н-%s" % x for x in a["missing"]))
    for bad in a["invalid"][:8]:
        out.append("  Н-%s, строка %s: неверная атрибуция %r"
                   % (bad["finding"], bad["report_line"], bad["text"][:80]))
    if a.get("duplicate_ids"):
        out.append("  неоднозначные номера находок: %s"
                   % ", ".join(a["duplicate_ids"]))
    if r.get("missing_section"):
        out.append("  нет обязательного непустого раздела «Отклонённые кандидаты»")
    if r.get("empty_section"):
        out.append("  раздел «Отклонённые кандидаты» пуст: нужен хотя бы один блок К-n")
    if r.get("duplicate_ids"):
        out.append("  неоднозначные номера кандидатов: %s" % ", ".join(r["duplicate_ids"]))
    if r["missing_citation"]:
        out.append("  кандидаты без path:line с цитатой: %s" % ", ".join(r["missing_citation"]))
    if r["invalid_citation"]:
        out.append("  кандидаты с неподтверждённой цитатой: %s" % ", ".join(r["invalid_citation"]))
    if c.get("missing_section"):
        out.append("  нет обязательного непустого раздела «Покрытие»")
    if c.get("empty_section"):
        out.append("  раздел «Покрытие» пуст: нужна таблица со строками")
    if c["missing_citation"] or c["invalid_citation"]:
        out.append("  строки покрытия с наблюдением без проверяемой цитаты: %s"
                   % ", ".join(str(x) for x in c["missing_citation"] + c["invalid_citation"]))
    if c.get("unflagged_citation"):
        out.append("  строки покрытия, цитата которых не попала ни в одну строку, отмеченную logmap: %s"
                   % ", ".join(str(x) for x in c["unflagged_citation"]))
    if c.get("mismatched_citation"):
        out.append("  цитата наблюдения указывает не на файл своей строки покрытия: %s"
                   % ", ".join(str(x) for x in c["mismatched_citation"]))
    if c.get("traversal_path"):
        out.append("  путь покрытия с переходом .. за пределы корпуса: %s"
                   % ", ".join(str(x) for x in c["traversal_path"]))
    if c.get("ambiguous_path"):
        out.append("  неоднозначные пути покрытия: %s"
                   % ", ".join("строка %s (%s)" % (x["line"], ", ".join(x["candidates"]))
                              for x in c["ambiguous_path"]))
    if c.get("missing_path"):
        out.append("  пути покрытия, которых нет в корпусе: %s"
                   % ", ".join(str(x) for x in c["missing_path"]))
    if c.get("false_empty"):
        out.append("  строки «пусто», чей файл не нулевого размера: %s"
                   % ", ".join(str(x) for x in c["false_empty"]))
    if c.get("false_binary"):
        out.append("  строки «двоичный», чей файл читается как текст: %s"
                   % ", ".join(str(x) for x in c["false_binary"]))
    if c.get("duplicate_paths"):
        out.append("  повторные пути покрытия: %s"
                   % "; ".join("%s (строки %s)" % (x["path"], ", ".join(str(n) for n in x["lines"]))
                               for x in c["duplicate_paths"]))
    if c["unsupported_no_address"]:
        out.append("  строки покрытия с неподдержанным статусом/no-address: %s"
                   % ", ".join(str(x) for x in c["unsupported_no_address"]))
    bad_no_addr = c.get("invalid_no_address_detail") or c["content_claim_in_no_address"]
    if bad_no_addr:
        out.append("  no-address строки не по закрытой грамматике ограничения доступа: %s"
                   % ", ".join(str(x) for x in bad_no_addr))
    if e["blocking"]:
        out.append("  исправь грамматику: наблюдение = path:line + дословная цитата; без адреса — только закрытая деталь доступа вида байт=0, формат=двоичный, ошибка=код или причина=лимит.")
    return "\n".join(out)


def ledger(d, report, path, report_path=None):
    rows, closed = read_ledger(path)
    open_rows = [r for r in rows
                 if r["state"] == "open" and not (set(r["ids"]) & closed)]
    unproven, n_find = findings_without_evidence(report, d["citations"])
    o = d.get("outcomes") or outcomes_of(report)
    s = d["summary"]
    bad = sum(s[k] for k in BAD)
    nonref = s["не-ссылка"]
    out = ["", "ЛЕДЖЕР — условие остановки (все пять чисел обязаны быть 0)",
           "  неразобранных строк: %d из %d" % (len(open_rows), len(rows)),
           "  находок без подтверждённой цитаты: %d из %d" % (len(unproven), n_find),
           "  находок без строки «исход»: %d из %d"
           % (len(o["missing"]) + len(o["invalid"]), n_find),
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
    block = render_outcomes(o)
    if block:
        out.append("")
        out.append(block)
    ev = d.get("report_evidence") or report_evidence(report, d)
    ev_block = render_report_evidence(ev)
    if ev_block:
        out.append("")
        out.append(ev_block)
    total = (len(open_rows) + len(unproven) + bad + nonref + o["blocking"]
             + ev.get("blocking", 0))
    out.append("")
    if total:
        out.append("ИТОГ: НЕ ЗАКОНЧЕНО — осталось %d" % total)
        return "\n".join(out), total
    # GREEN. This is the last thing the model reads before it decides it is
    # finished, so it is where the delivery step belongs — not 400 lines up in
    # SKILL.md. D03 reached 10 proofs, wrote a 28,960-char report, read «можно
    # писать отчёт» at a moment when the report was already written, and
    # answered «Отлично. Все проверки пройдены. Отчёт готов и доставлен.» in 56
    # characters. The row scored `collapse`: the judge never saw the report.
    # Permission to write is not an instruction to deliver.
    out.append("ИТОГ: можно отдавать отчёт.")
    out.append("ПОСЛЕДНИЙ ШАГ: выведи файл целиком — cat %s"
               % (report_path or "<файл отчёта>"))
    out.append("Файл на диске — НЕ доставка. Ответом считается только текст "
               "твоего последнего сообщения; «отчёт готов» в нём = пустой ответ.")
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
# ЧТО СДАЁШЬ — ТО И ПРОВЕРЯЛ.
#
# `report.md` is a draft: the user never sees it. The hand-over is the last
# message. Nothing used to compare the two, and a run can pass the check on one
# document and hand over another — the skill names this as its one unchecked
# stopping condition.
#
# Two nets, because one is not enough. MEASURED on a saved run whose draft
# scored 110/110 and whose hand-over scored 74/95: of the 21 failing citations
# in the hand-over, **20 were already in the draft's verified set** — same file,
# same line, re-typed under a different sentence. A subset test over citations
# alone would have caught one of twenty-one. So:
#
#   1. the delivered text goes through the SAME check against the SAME corpus.
#      A re-typed citation is a new claim, and it is graded like one.
#   2. and every delivered citation that was never in the verified set is named
#      on its own, because a claim nobody checked is not made true by the fact
#      that its line exists.
#
# Both are arithmetic. Neither asks anybody to judge whether a condensation is
# faithful.
def cite_key(c):
    """The identity of a citation: which file, which line, how far."""
    return (c.get("resolved") or c.get("path"), c.get("line"), c.get("range_end"))


def not_in_checked(checked, delivered):
    """-> the delivered citations that never came back `ok` on the checked text."""
    verified = {cite_key(c) for c in checked["citations"] if c["verdict"] == "ok"}
    out = []
    for c in delivered["citations"]:
        if cite_key(c) not in verified:
            out.append({"citation": c["citation"], "path": c["path"],
                        "line": c["line"], "report_line": c["report_line"],
                        "verdict": c["verdict"]})
    return out


def delivery_failed(dd):
    return bool(any(dd["summary"][k] for k in BAD) or dd["not_in_checked"]
                or (dd.get("outcomes") or {}).get("blocking")
                or (dd.get("report_evidence") or {}).get("blocking"))


def render_delivery(dd):
    s = dd["summary"]
    out = ["", "ПОСТАВКА: %s" % dd["path"],
           "  ссылок %d — ok %d, wrong-content %d, вне диапазона %d, нет файла %d, "
           "непроверяемых %d, без цитаты %d, неоднозначных %d; не-ссылок %d"
           % (s["total"], s["ok"], s["wrong-content"], s["out-of-range"],
              s["missing-file"], s["unverifiable"], s["no-quote"],
              s["ambiguous"], s["не-ссылка"])]
    block = render_outcomes(dd.get("outcomes") or {"findings": []})
    if block:
        out.append("  " + block.replace("\n", "\n  "))
    ev = render_report_evidence(dd.get("report_evidence"))
    if ev:
        out.append("  " + ev.replace("\n", "\n  "))
    n = len(dd["not_in_checked"])
    if n:
        out.append("  НЕ БЫЛО В ПРОВЕРЕННОМ НАБОРЕ: %d" % n)
        for c in dd["not_in_checked"][:10]:
            out.append("    %s — строка поставки %d" % (c["citation"],
                                                        c["report_line"]))
        if n > 10:
            out.append("    … и ещё %d" % (n - 10))
    if delivery_failed(dd):
        out.append("  СДАЁШЬ НЕ ТО, ЧТО ПРОВЕРИЛ. Перепечатанная ссылка — это "
                   "новое утверждение, и ему нужна своя проверка. Либо сдавай "
                   "проверенный текст дословно, либо гоняй проверку по поставке "
                   "до нуля ошибок.")
    else:
        out.append("  поставка совпадает с проверенным: можно отдавать.")
    return "\n".join(out)


def render(d):
    out = []
    # `.get`, not `[...]`: v13 added `binary-file` and v16 adds `ambiguous`, and a
    # verdict missing from this table used to raise KeyError inside render() —
    # i.e. the DEFAULT, non-JSON mode the skill tells the model to run crashed on
    # exactly the citations the guard exists to refuse. Reproduced on v15.
    mark = {"ok": "✓", "unverifiable": "?", "no-quote": "✗", "wrong-content": "✗",
            "out-of-range": "✗", "missing-file": "✗", "binary-file": "✗",
            "ambiguous": "✗"}
    for r in d["citations"]:
        out.append("%s %-13s %s:%d%s" % (mark.get(r["verdict"], "✗"), r["verdict"],
                                         r["path"], r["line"],
                                         "  [неоднозначно: %d файла]" % r["candidates"]
                                         if r["how"].endswith("ambiguous") else ""))
        if r["verdict"] == "ambiguous":
            out.append("    эта ссылка означает %d разных файла — я не выбираю "
                       "за тебя, какой из них ты имел в виду:" % r["candidates"])
            for cand in r["candidate_paths"][:8]:
                out.append("      %s" % cand)
            if len(r["candidate_paths"]) > 8:
                out.append("      … и ещё %d" % (len(r["candidate_paths"]) - 8))
            out.append("    возьми ОДИН из них целиком, от корня корпуса, "
                       "и подставь вместо %s — какой именно, знаешь только ты."
                       % r["path"])
        elif r["verdict"] == "no-quote":
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
               "missing-file %d, unverifiable %d, без цитаты %d, "
               "неоднозначных %d; не-ссылок %d"
               % (s["total"], s["ok"], s["wrong-content"], s["out-of-range"],
                  s["missing-file"], s["unverifiable"], s["no-quote"],
                  s["ambiguous"], s["не-ссылка"]))
    if s["ambiguous"]:
        out.append("НЕ ОТДАВАЙ отчёт с неоднозначными ссылками: путь до файла "
                   "пиши от корня корпуса, вместе с именем машины.")
    if s["wrong-content"]:
        out.append("НЕ ОТДАВАЙ отчёт с wrong-content: перечитай строку или удали "
                   "утверждение (SKILL.md, шаг 6).")
    block = render_outcomes(d.get("outcomes") or {"findings": []})
    if block:
        out.append("")
        out.append(block)
    ev = render_report_evidence(d.get("report_evidence"))
    if ev:
        out.append("")
        out.append(ev)
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
                    help="условие остановки: проверяемые счётчики по рабочему списку и отчёту")
    ap.add_argument("--delivered", metavar="handover.md",
                    help="текст, который ты СДАЁШЬ. Проверяется тем же кодом по "
                         "тому же корпусу, и его ссылки обязаны быть "
                         "подмножеством проверенных")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    text = (sys.stdin.read() if args.report == "-"
            else open(args.report, encoding="utf-8", errors="replace").read())

    d = check(text, args.corpus, args.min_overlap, args.min_tokens,
              args.require_quote)
    if args.ledger and os.path.exists(args.ledger):
        d["flagged"] = flagged_lines(args.ledger)
    d["outcomes"] = outcomes_of(text)
    d["report_evidence"] = report_evidence(text, d)

    bad_delivery = False
    if args.delivered:
        if not os.path.exists(args.delivered):
            sys.exit("нет такого файла: %s" % args.delivered)
        handed = open(args.delivered, encoding="utf-8", errors="replace").read()
        dd = check(handed, args.corpus, args.min_overlap, args.min_tokens,
                   args.require_quote)
        dd["flagged"] = d.get("flagged") or {}
        dd["path"] = args.delivered
        dd["outcomes"] = outcomes_of(handed)
        dd["report_evidence"] = report_evidence(handed, dd)
        dd["not_in_checked"] = not_in_checked(d, dd)
        d["delivered"] = dd
        bad_delivery = delivery_failed(dd)

    ledger_rows = None
    if args.ledger:
        if not os.path.exists(args.ledger):
            sys.exit("нет такого файла: %s" % args.ledger)
        body, left = ledger(d, text, args.ledger,
                            None if args.report == "-" else args.report)
        ledger_rows = len(read_ledger(args.ledger)[0])
        d["ledger"] = {"unresolved_total": left, "rows": ledger_rows,
                       "empty": ledger_rows == 0}
    d.pop("flagged", None)
    if isinstance(d.get("delivered"), dict):
        d["delivered"].pop("flagged", None)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(d))
    if args.delivered and not args.json:
        print(render_delivery(d["delivered"]))
    # ONE definition of "this report has a blocking defect", used by BOTH
    # exits. v35 had two: the --ledger branch returned on the ledger counters
    # alone, so the BAD / outcomes / report_evidence verdicts could only ever
    # reach the exit code through `ledger()`'s `total`. That happened to be a
    # superset in v35 — but it was an accident of one arithmetic expression in
    # another function, not a stated invariant, and the gate that decides
    # whether a paid run is deliverable must not rest on an accident.
    blocking_defects = bool(any(d["summary"][k] for k in BAD)
                            or d["outcomes"]["blocking"]
                            or d.get("report_evidence", {}).get("blocking"))
    if args.ledger:
        if not args.json:
            print(body)
        # A ledger with zero rows is not a resolved ledger: it is a ledger that
        # was never built. v35 printed «ИТОГ: можно отдавать отчёт.» and exited
        # 0 for it — MEASURED on the v35 r3 report with a header-only
        # worklist.tsv. A gate whose input is empty has checked nothing.
        if ledger_rows == 0:
            print("ЛЕДЖЕР ПУСТ: %s не содержит ни одной строки — "
                  "рабочий список не построен, проверять нечего." % args.ledger)
            return 1
        return 0 if (left == 0 and not bad_delivery
                     and not blocking_defects) else 1
    return 1 if (blocking_defects or bad_delivery) else 0


if __name__ == "__main__":
    sys.exit(main())
