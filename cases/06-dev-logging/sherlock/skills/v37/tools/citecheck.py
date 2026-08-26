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

    ok             an aggregate citation whose count the gate RECOMPUTED and matched

TWO citation forms, not one. A line quote proves that ONE line exists and says
what the claim says; it can never prove a claim about a POPULATION («93 distinct
source IPs, 8 of them over 1000 failures»), because there is no single line to
quote. Measured: under a quote-only gate the model DELETED such claims rather
than fail — v36 failed its gates and named 12 attacker IPs, v37 passed all three
and named 4, out of 93 real ones. So there is a second first-class form,

    агрегат: <путь> · <предикат> = <число> · <команда>

graded by `aggregates_check`: the gate re-evaluates the predicate against the
corpus itself and compares the count exactly. The command is a RENDERING of the
predicate, compared for equality and never executed. See the block above
`_blocking_total` for the closed predicate vocabulary and every failure mode.

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
import importlib.util
import gzip
import io
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
    # Aggregate citations are graded by the same call, against the same index,
    # so a report can never be checked for one form and not the other.
    aggregates = aggregates_check(report, root, by_rel, by_base)
    summary["агрегатов"] = aggregates["total"]
    summary["агрегат-плохих"] = aggregates["blocking"]
    return {"corpus": os.path.abspath(root), "citations": results,
            "non_references": nonrefs, "require_quote": require_quote,
            "aggregates": aggregates, "summary": summary}



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
    """-> {reference path (and unambiguous basename): {line numbers}} flagged.

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
    by_path = {}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return {}
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
            by_path.setdefault(_norm_ref(head), set()).add(int(tail))
    # KEYED BY PATH, and by basename ONLY where the basename is unambiguous.
    # This index used to be keyed by lowercased basename alone. That was
    # survivable while it only had to answer «did the mapper flag ANY line of
    # something with this name», but P8 promotes it to the authority for a
    # CLOSED, per-file enumerated set — and with `hostA/System.jsonl` and
    # `hostB/System.jsonl` in one corpus (the ordinary multi-host winevtx
    # shape) the union of both files' flags authorized a citation into either.
    # The set was then neither closed nor per-file. The v37 corpus has no
    # duplicate basenames, which is the only reason it never showed.
    out = dict(by_path)
    owners = {}
    for norm in by_path:
        owners.setdefault(os.path.basename(norm), []).append(norm)
    for base, paths in owners.items():
        if len(paths) == 1 and base not in out:
            out[base] = by_path[paths[0]]
    return out


def _norm_ref(path):
    """One spelling for a reference path: forward slashes, lowercase, no `./`."""
    p = (path or "").replace("\\", "/").lower().strip()
    while p.startswith("./"):
        p = p[2:]
    return p.strip("/")


def flagged_key_for(flagged, path):
    """-> the ONE key of a flagged index that answers `path`, or None.

    Resolution order, most specific first, and AMBIGUITY NEVER RESOLVES: if two
    entries could answer, none does. That is the whole point of #7 — one file
    must not authorize another. Shared with `covermap.py`, whose index is keyed
    by the worklist's own reference paths, so producer and grader read the same
    map the same way.
    """
    if not flagged or not path:
        return None
    want = _norm_ref(path)
    if not want:
        return None
    byname = {}
    for k in flagged:
        byname.setdefault(_norm_ref(k), k)
    # `logmap` writes `rendered/Foo-4Admin.jsonl` where the report may write the
    # percent-escaped original `Foo%4Admin.jsonl`. Compare on the shape both
    # spellings share.
    alt = want.replace("%4", "-4")
    for probe in (want, alt):
        if probe in byname:
            return byname[probe]
    hits = {k for n, k in byname.items()
            if any(n.endswith("/" + pr) or pr.endswith("/" + n)
                   for pr in (want, alt))}
    if len(hits) == 1:
        return hits.pop()
    if hits:
        return None    # ambiguous: no file authorizes another
    base, balt = os.path.basename(want), os.path.basename(alt)
    hits = {k for n, k in byname.items()
            if os.path.basename(n) in (base, balt)}
    return hits.pop() if len(hits) == 1 else None


def _flagged_for(flagged, path):
    """The flagged lines of one coverage path, whatever spelling it uses."""
    key = flagged_key_for(flagged, path)
    return flagged.get(key) if key is not None else None


COVERAGE_SMALL_FILE = 2   # a file this short has no "boring first line"


def coverage_admissible_lines(abs_path, flagged=None, rel=None):
    """-> the set of line numbers a «наблюдение» coverage row MAY cite.

    P8, and the other half of P7. `flagged_lines` already refuses a quote that
    landed on no flagged line at all. It was not enough: MEASURED on the v37
    gate-clean run, 81 of 93 «наблюдение» rows quoted LINE 1, and the gate
    passed all 81. Line 1 is legal under P7 because `logmap` names the FIRST
    member of a group as that group's reference — every `cat`/`level`/`burst`
    class whose first record happens to be record 1 puts `path:1` on the
    worklist. So «quote a flagged line» degenerates to «quote line 1», which is
    what a tool does when it needs *a* line, not what an analyst does who has
    read the file. Opera, the daily PowerShell script and DPAPI were all
    "covered" that way and all three are missing from the findings.

    WHEN LINE 1 IS LEGITIMATE — a blanket ban would false-block small files,
    and a false-blocking gate gets switched off:
      * a file of <= COVERAGE_SMALL_FILE lines: there is no older, duller
        record to prefer, so any line, line 1 included, is the whole story;
      * a file whose ONLY flagged line is line 1: the mapper found exactly one
        interesting record and it genuinely is the first one.

    WHAT STOPS «then I will quote line 2» — the failure mode that killed the
    last four rounds. Nothing here says "line 1 is expensive". The admissible
    set is *closed*: for a file with flagged lines it is exactly the flagged
    lines above 1, so line 2 is admissible only when the mapper flagged line 2,
    and a file with no flags at all admits exactly ONE line — the last quotable
    one. There is therefore no cheaper answer than the correct answer, because
    for every file the correct answers are enumerated here and nothing else is
    accepted. `covermap.py` calls this same function to CHOOSE its line, so the
    honest row costs one tool invocation and the lazy row costs a guess against
    a closed set.

    WHY THE LAST LINE for an unflagged file: it is the newest record, the one
    an operator cares about; reaching it means reading to EOF; and it is
    single-valued, so it cannot be shopped for.

    Only lines `quote_example` can actually turn into a citation are admitted,
    at every tier — otherwise the rule would push an honest run into claiming
    «нечитабельно», which nothing machine-checks.

    Raises OSError if the file cannot be read. BOTH callers must fail closed:
    an unreadable file is not a clean one.
    """
    marks = {n for n in (flagged or ()) if isinstance(n, int) and n > 0}
    need_last = not marks
    quotable_marks, small, last_q, total = set(), set(), None, 0
    # THROUGH `opener`, NOT `io.open` — the same reason `looks_binary` reads
    # through gzip and says so in its own docstring. This function used to
    # `io.open` the file, so on a `.gz` it enumerated the lines of the RAW
    # COMPRESSED BYTES while `read_lines` (which produces the citation being
    # graded) decoded the stream. The two sets then had nothing to do with each
    # other, and NO line of a gzipped file could pass: the flagged line was
    # `cov_inadmissible_line`, every admissible "line" was
    # `cov_unflagged_citation`. There are 1486 `.gz` files in the corpora this
    # runs on. Every reader in this file opens the stream a reader sees.
    with opener(abs_path)(abs_path, "rt", encoding="utf-8",
                          errors="replace") as fh:
        for i, text in enumerate(fh, 1):
            total = i
            if not (need_last or i in marks or i <= COVERAGE_SMALL_FILE):
                continue
            if not quote_example({"path": rel or abs_path, "line": i,
                                  "text": text.rstrip("\n")}):
                continue
            last_q = i
            if i in marks:
                quotable_marks.add(i)
            if i <= COVERAGE_SMALL_FILE:
                small.add(i)
    if total == 0:
        return set()
    if total <= COVERAGE_SMALL_FILE:
        return small
    alts = {n for n in quotable_marks if n > 1}
    if alts:
        return alts
    if 1 in quotable_marks:
        return {1}
    return {last_q} if last_q else set()


def _coverage_line_admissible(row, cites, by_rel, want):
    """Does this «наблюдение» row cite a line the file is allowed to answer with?

    FAILS CLOSED. No resolved path, no ok quote, an unreadable file, a line
    number that never parsed, an admissible set that came back empty, any
    exception at all — every one of them returns False and the row blocks.
    This repo has already shipped a guard that failed open on a malformed
    gates.json and a lane audit that was dead code on the healthy path; the
    fourth one is not going to be this.
    """
    try:
        resolved = row.get("resolved_path")
        if not resolved:
            # A path that did not resolve is already counted as traversal /
            # ambiguous / missing, and that row already blocks. Anything else
            # with no resolved path is unverifiable, so it blocks here.
            if row.get("path_problem"):
                return True
            row["inadmissible_why"] = "путь не разрешился"
            return False
        abs_path = by_rel.get(resolved)
        if not abs_path:
            row["inadmissible_why"] = "файл не найден в корпусе"
            return False
        lines = {c.get("line") for c in cites
                 if c.get("verdict") == "ok" and c.get("resolved") == resolved}
        lines = {n for n in lines if isinstance(n, int) and n > 0}
        if not lines:
            row["inadmissible_why"] = "номер строки не разобран"
            return False
        ok = coverage_admissible_lines(abs_path, want, resolved)
        if not ok:
            row["inadmissible_why"] = "в файле нет ни одной цитируемой строки"
            return False
        if lines & ok:
            return True
        row["inadmissible_why"] = ("процитировано %s; допустимо %s"
                                   % (",".join(str(n) for n in sorted(lines)),
                                      ",".join(str(n) for n in sorted(ok)[:8])))
        return False
    except Exception as exc:                       # noqa: BLE001 — fail closed
        row["inadmissible_why"] = "проверка не выполнилась: %s" % (exc,)
        return False


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


def findings_without_evidence(report, results, aggregates=None):
    """A finding block with no citation whose verdict is `ok` is unproven.

    An aggregate citation that VERIFIED counts as proof here, and only when it
    verified. That is the whole point of the second form: a claim about a
    population is provable, so a finding that rests on one is not «unproven».
    A failing aggregate proves nothing and is already blocking on its own.

    ## OPEN QUESTION — nothing here links the evidence to the CLAIM.

    This function asks only «is there something with verdict ok inside the
    finding's line range». It does not ask whether that something is ABOUT the
    finding. So an aggregate that verified — any aggregate — discharges any
    finding it is pasted under, and the report reads as proven.

    State it plainly rather than softly: this is a REAL hole, and the aggregate
    form makes it CHEAPER, because a true aggregate is easier to manufacture
    than a true line quote. It is not, however, a regression the aggregate form
    introduced: the control was run, and an irrelevant LINE QUOTE discharges a
    finding exactly the same way. The hole is claim-relevance, it predates this
    form, and it is inherited whole.

    It is deliberately NOT patched here. A relevance check is a different kind
    of judgement from «does this number recompute» — every cheap version of it
    (token overlap between the claim and the quote) is the same heuristic that
    `quote_example` already applies to the quote text, and applying it to a
    finding's whole prose would reject honest reports. Until there is a
    measurement to design against, the honest state is: the gate proves the
    NUMBER and the LINE, and the author owns the link between the evidence and
    the sentence. `reference/report-format.md` §«Что агрегат НЕ доказывает»
    says the same thing to the model."""
    bounds = finding_blocks(report)
    if not bounds:
        return [], 0
    agg_ok = [a["report_line"] for a in ((aggregates or {}).get("items") or [])
              if a["verdict"] == "ok"]
    bad = []
    for num, lo, hi in bounds:
        ok = any(r["verdict"] == "ok" and lo <= r["report_line"] <= hi
                 for r in results)
        if not ok:
            ok = any(lo <= n <= hi for n in agg_ok)
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


# --------------------------------------------------------------------------
# v38: окно записей — did records go missing INSIDE the window we reason about?
#
# WHY A GATE AND NOT JUST A TOOL. covermap.py pairs with the coverage rules in
# this file for one reason the v37 run proved: the arm omits whatever the gate
# does not demand. `rollover.py` can compute the window for every channel in
# 3.6 s, and on the v37 report that number appears nowhere, because nothing
# asked for it. A number nobody is required to state is a number that will not
# be stated. So the producer is rollover.py and the grader is here — and the
# grader RE-DERIVES the truth from the corpus rather than believing the report.
#
# WHAT THE REPORT OWES (see rollover.required_keys for the full argument):
#   1. one «итог:» line whose six counts equal the gate's own scan;
#   2. one row per channel WITH A GAP;
#   3. one row per channel of a file that a FINDING cites — the anchor case:
#      the false «402 000 evicted from Security.jsonl» claim was about exactly
#      the channel the findings rested on.
# NOT one row per corpus file. The coverage table already costs 143 rows for
# 143 files and the project has an open question about whether that survives a
# 10 000-file corpus; a second full-corpus table doubles a cost already in
# doubt. This set is bounded by findings + gaps, not by corpus size.
#
# EVERY COUNTER BELOW FEEDS THE ONE `blocking` SUM AT THE END OF
# report_evidence(). There is no second ledger: defect #4 two rounds ago was a
# private counter that printed 160 where the gate printed 161.
# MATCHED AGAINST SECTION TITLES. Keep it NARROW. The `rollover|ротаци`
# aliases that used to live here were a false positive with teeth: in a
# log-analysis case «Ротация журналов» is an ordinary finding title, and any
# section whose heading matched became a rollover span, so every `|`-line in
# it was parsed as a rollover row and counted as `malformed`. The only escape
# the arm had was renaming its finding. One spelling, the one the tool emits.
ROLLOVER_SECTION_RE = re.compile(r"окно\s+запис", re.IGNORECASE)
ROLLOVER_SUMMARY_RE = re.compile(r"^\s*(?:[-*+>]\s*)?(?:\*\*|__)?\s*итог\s*:",
                                 re.IGNORECASE)
_RO_NUM = r"[\d   ]+"
ROLLOVER_KEYS = ("файлов", "каналов", "сплошных", "с-пропусками",
                 "неприменимо", "ошибок")
ROLLOVER_FIELDS = {"files": "файлов", "channels": "каналов",
                   "contiguous": "сплошных", "gapped": "с-пропусками",
                   "na": "неприменимо", "errors": "ошибок"}
ROLLOVER_WINDOW_RE = re.compile(
    r"окно\s*[:=]?\s*(" + _RO_NUM + r")\s*[-‐-―~.]{1,2}\s*(" + _RO_NUM + r")")
ROLLOVER_RECORDS_RE = re.compile(r"запис(?:ей|и|ь)\s*[:=]\s*(" + _RO_NUM + r")")
ROLLOVER_MISSING_RE = re.compile(r"нет\s*[:=]\s*(" + _RO_NUM + r")")


def _ro_int(text):
    """«34 916», «34 916» and «34916» are the same number; anything else is None."""
    s = re.sub(r"[  \s]", "", text or "")
    return int(s) if s.isdigit() else None


def load_rollover():
    """One definition of the scan, shared by the producer and this grader.

    Missing or broken module = a blocking defect, never a skip: `scan_failed`.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rollover.py")
    spec = importlib.util.spec_from_file_location("_sherlock_rollover", path)
    if spec is None or spec.loader is None:
        raise ImportError("rollover.py не загружается: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rollover_summary_line(lines, spans, structural):
    """-> (parsed dict or None, list of report_lines carrying «итог:»)."""
    hits = []
    for i in range(1, len(lines) + 1):
        if not structural[i - 1] or not _in_spans(i, spans):
            continue
        if ROLLOVER_SUMMARY_RE.match(lines[i - 1]):
            hits.append(i)
    if not hits:
        return None, []
    text = lines[hits[0] - 1]
    got = {}
    for key in ROLLOVER_KEYS:
        m = re.search(re.escape(key) + r"\s*[:=]\s*(" + _RO_NUM + r")", text)
        got[key] = _ro_int(m.group(1)) if m else None
    return got, hits


def rollover_rows(lines, spans, structural):
    """Parse the «окно записей» table. Unparseable rows are defects, not noise."""
    rows, malformed = [], []
    for lo, hi in spans:
        for i in range(lo, min(hi, len(lines) + 1)):
            if not structural[i - 1]:
                continue
            raw = lines[i - 1]
            if not raw.lstrip().startswith("|") or TABLE_SEP_RE.match(raw):
                continue
            cells = split_cells(raw)
            if cells and cells[0].strip().lower() in ("путь", "path", "файл", "file"):
                continue
            if len(cells) < 3:
                malformed.append(i)
                continue
            tail = " | ".join(cells[2:])
            w = ROLLOVER_WINDOW_RE.search(tail)
            n = ROLLOVER_RECORDS_RE.search(tail)
            g = ROLLOVER_MISSING_RE.search(tail)
            if not (w and n and g):
                malformed.append(i)
                continue
            lo_id, hi_id = _ro_int(w.group(1)), _ro_int(w.group(2))
            recs, miss = _ro_int(n.group(1)), _ro_int(g.group(1))
            if None in (lo_id, hi_id, recs, miss):
                malformed.append(i)
                continue
            rows.append({"report_line": i, "path": cells[0], "channel": cells[1],
                         "lo": lo_id, "hi": hi_id, "records": recs,
                         "missing": miss})
    return rows, malformed


ROLLOVER_ROW_SHAPE_RE = re.compile(
    r"окно\s*[:=].*запис(?:ей|и|ь)\s*[:=].*нет\s*[:=]", re.IGNORECASE)


def misplaced_rollover_rows(lines, structural):
    """Report lines that LOOK like «окно записей» rows but sit outside the section.

    Without this the diagnosis for a nested table is «дубликаты покрытия» and
    «строки покрытия без адреса» — twelve messages, none of which says the word
    rollover, for what is really one placement mistake.
    """
    hits = []
    for i, raw in enumerate(lines, 1):
        if not structural[i - 1]:
            continue
        if not raw.lstrip().startswith("|"):
            continue
        if ROLLOVER_ROW_SHAPE_RE.search(raw):
            hits.append(i)
    return hits


def rollover_evidence(report, corpus, cited_paths, structural=None, sections=None):
    """The whole «окно записей» verdict, as counters. Fails closed everywhere."""
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    sections = sections if sections is not None else _sections(report, structural)
    spans = _spans_for(sections, ROLLOVER_SECTION_RE)
    out = {"missing_section": False, "summary_missing": False,
           "summary_duplicate": False, "summary_mismatch": None,
           "undeclared": [], "wrong": [], "spurious": [], "duplicate_rows": [],
           "malformed": [], "scan_errors": [], "scan_failed": None,
           "misplaced_rows": [], "nested_section": False,
           "scan": None, "required": 0, "blocking": 0}

    try:
        ro = load_rollover()
        if not (corpus and os.path.isdir(corpus)):
            raise OSError("нет каталога корпуса: %r" % corpus)
        scan = ro.scan_corpus(corpus)
    except Exception as exc:
        # An exception in the check is NOT a clean run. This repo has shipped a
        # guard that failed open on a malformed gates.json; not twice.
        out["scan_failed"] = "%s: %s" % (type(exc).__name__, exc)
        out["blocking"] = 1
        return out

    out["scan"] = {k: scan[k] for k in
                   ("files", "channels", "contiguous", "gapped", "na",
                    "errors", "lost")}
    out["scan_errors"] = [{"path": e["path"], "detail": e["detail"]}
                          for e in scan["entries"] if e["status"] == ro.ERR]

    want = ro.required_keys(scan, cited_paths)
    out["required"] = len(want)

    if not spans:
        out["missing_section"] = True
        out["blocking"] = 1 + len(out["scan_errors"]) + len(want)
        out["undeclared"] = ["%s | %s" % k for k in sorted(want)]
        # Nesting the table under another heading («# Покрытие» is the one that
        # happens) is NOT a small mistake: the section stops existing, the rows
        # are then read as COVERAGE rows, and every message names coverage while
        # the real cause is placement. Say the real cause here.
        out["misplaced_rows"] = misplaced_rollover_rows(lines, structural)
        return out

    # A section NESTED inside «Покрытие» (a deeper heading level) does not end
    # where its author thinks: the span runs on to the next heading of the same
    # level, so the coverage rows below it are read as rollover rows and every
    # message names the wrong table. Name the real cause: placement.
    cov = _spans_for(sections, COVERAGE_SECTION_RE)
    out["nested_section"] = any(a < d and c < b
                                for a, b in spans for c, d in cov)

    summary, hits = rollover_summary_line(lines, spans, structural)
    if summary is None:
        out["summary_missing"] = True
    else:
        if len(hits) > 1:
            out["summary_duplicate"] = True
        bad = {}
        for field, key in ROLLOVER_FIELDS.items():
            if summary.get(key) != scan[field]:
                bad[key] = {"заявлено": summary.get(key), "на диске": scan[field]}
        if bad:
            out["summary_mismatch"] = bad

    rows, malformed = rollover_rows(lines, spans, structural)
    out["malformed"] = malformed
    seen = {}
    for r in rows:
        # The row was written by `row_for`, so its cells are ALREADY escaped.
        key = ro.key_of_cells(r["path"], r["channel"])
        seen.setdefault(key, []).append(r)
    out["duplicate_rows"] = sorted("%s | %s" % k for k, v in seen.items()
                                   if len(v) > 1)

    for key, e in sorted(want.items()):
        got = (seen.get(key) or [None])[0]
        if got is None:
            out["undeclared"].append("%s | %s" % key)
            continue
        if (got["lo"], got["hi"], got["records"], got["missing"]) != (
                e["lo"], e["hi"], e["records"], e["missing"]):
            out["wrong"].append(
                {"line": got["report_line"], "row": "%s | %s" % key,
                 "заявлено": "окно=%d–%d записей=%d нет=%d"
                             % (got["lo"], got["hi"], got["records"], got["missing"]),
                 "на диске": "окно=%d–%d записей=%d нет=%d"
                             % (e["lo"], e["hi"], e["records"], e["missing"])})
    # Declaring every channel «gapped» to be safe is not a cheap way out: a row
    # the corpus does not support costs exactly as much as a missing one.
    for key in sorted(seen):
        if key not in want:
            out["spurious"].append({"line": seen[key][0]["report_line"],
                                    "row": "%s | %s" % key})

    out["blocking"] = (int(out["summary_missing"]) + int(out["summary_duplicate"])
                       + (1 if out["summary_mismatch"] else 0)
                       + len(out["undeclared"]) + len(out["wrong"])
                       + len(out["spurious"]) + len(out["duplicate_rows"])
                       + len(out["malformed"]) + len(out["scan_errors"]))
    return out


def render_rollover(r):
    if not r:
        return ""
    out = ["ОКНО ЗАПИСЕЙ v38: %d блокирующих дефектов" % r.get("blocking", 0)]
    if r.get("scan_failed"):
        out.append("  ПРОВЕРКА НЕ ОТРАБОТАЛА: %s — это НЕ «чисто»." % r["scan_failed"])
        return "\n".join(out)
    s = r.get("scan") or {}
    # NOT «потеряно записей». «Lost» is the diagnosis, and «~402 000 записей
    # вытеснено» is the exact false claim this whole check exists to prevent;
    # printing it in the gate's own output re-introduces it. State the FACT:
    # ids absent inside the window. The cause — filtered export or a real wrap —
    # is the report's job, not this line's.
    out.append("  на диске: файлов %d, каналов %d, сплошных %d, с пропусками %d, "
               "неприменимо %d, ошибок %d, id нет внутри окон %d"
               % (s.get("files", 0), s.get("channels", 0), s.get("contiguous", 0),
                  s.get("gapped", 0), s.get("na", 0), s.get("errors", 0),
                  s.get("lost", 0)))
    if r.get("missing_section"):
        out.append("  НЕТ РАЗДЕЛА «Окно записей». Добавь его: "
                   "python3 rollover.py --corpus <корпус> --report --required-only "
                   "--cite <файл-улики> >> report.md")
    if r.get("nested_section"):
        out.append("  РАЗДЕЛ ВЛОЖЕН в «Покрытие»: его строки читаются ещё и как "
                   "строки ПОКРЫТИЯ (повторные пути, без адреса), а вложенный "
                   "заголовок не кончается там, где ты думаешь. «Окно записей» "
                   "обязан быть заголовком ВЕРХНЕГО уровня — «# Окно записей», "
                   "или «## Окно записей» ПОСЛЕ всего раздела «Покрытие», "
                   "НЕ внутри него.")
    if r.get("misplaced_rows"):
        out.append("  НО строки нужной формы в отчёте ЕСТЬ — строки %s. Раздел "
                   "вложен в чужой: «# Окно записей» обязан быть заголовком "
                   "ВЕРХНЕГО уровня (h1 «# », или h2 «## » сразу ПОСЛЕ раздела "
                   "«Покрытие»), НЕ внутри него."
                   % ", ".join(str(i) for i in r["misplaced_rows"][:10]))
    if r.get("summary_missing"):
        out.append("  нет строки «итог:» — раздел есть, чисел нет")
    if r.get("summary_duplicate"):
        out.append("  строк «итог:» больше одной — какая из них твоя?")
    if r.get("summary_mismatch"):
        out.append("  итог не сходится с диском: %s"
                   % json.dumps(r["summary_mismatch"], ensure_ascii=False))
    for k in ("undeclared", "duplicate_rows"):
        if r.get(k):
            out.append("  %s: %s" % ({"undeclared": "не объявлены окна",
                                      "duplicate_rows": "повторные строки"}[k],
                                     ", ".join(r[k][:10])))
    if r.get("wrong"):
        out.append("  окна заявлены неверно:")
        for w in r["wrong"][:10]:
            out.append("    строка %d %s — заявлено %s, на диске %s"
                       % (w["line"], w["row"], w["заявлено"], w["на диске"]))
    if r.get("spurious"):
        out.append("  лишние строки (корпус их не подтверждает): %s"
                   % ", ".join("строка %d %s" % (x["line"], x["row"])
                               for x in r["spurious"][:10]))
    if r.get("malformed"):
        out.append("  строки не по схеме | путь | канал | окно=a–b | записей=n | нет=m |: %s"
                   % ", ".join(str(i) for i in r["malformed"][:10]))
    if r.get("scan_errors"):
        out.append("  файлы, окно которых прочитать НЕ УДАЛОСЬ (каждый блокирует):")
        for e in r["scan_errors"][:10]:
            out.append("    %s — %s" % (e["path"], e["detail"]))
    return "\n".join(out)


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
    cov_inadmissible_line, cov_false_unreadable = [], []
    flagged = (checked or {}).get("flagged") or {}
    # DO WE KNOW WHAT THE MAPPER FLAGGED? `main` fills `flagged` only when
    # `--ledger` is given. Without it the map is EMPTY, which is not the same
    # fact as «the mapper flagged nothing»: the unflagged tier then applies to
    # every file and admits only each file's LAST line, so
    # `citecheck report.md --corpus ./logs` — the bare form printed in
    # tools/README.md and used in the step-6 draft check — told the model its
    # correct coverage table was wrong on every row. The official gate always
    # passes `--ledger`, so no run was affected; the model hitting this
    # mid-draft was, and a gate that cries wolf mid-draft gets ignored at the
    # end. Unknown is not empty: with no ledger the coverage-LINE rule does not
    # run at all, and says so.
    flagged_known = "flagged" in (checked or {})
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
        row["path_problem"] = bool(path_problem)
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
                elif (flagged_known
                      and not _coverage_line_admissible(row, cites, by_rel, want)):
                    # P8: quoting line 1 of a long file, or any line of a file
                    # the mapper never flagged other than the last, proves the
                    # file was opened and nothing more. See
                    # `coverage_admissible_lines`.
                    cov_inadmissible_line.append(row)
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
            if row["resolved_path"] and status == "нечитабельно":
                # Banning the lazy line-1 quote makes «нечитабельно» the next
                # cheapest way out — «I could not read it» costs nothing and,
                # until now, nothing falsified it, unlike «пусто» and
                # «двоичный». A rule that only moves the cliff is not a fix.
                # The same function that enumerates admissible lines answers
                # this: if the file yields a citable line, it was readable.
                #
                # WITH THE SAME `flagged` ARGUMENT `covermap` USED. This passed
                # `None` and so asked a DIFFERENT question than the producer:
                # on a file whose flagged lines are all unquotable but whose
                # last line is quotable, `covermap` correctly wrote
                # «нечитабельно | ошибка=нечем-цитировать» and this check then
                # called that row a lie and blocked the run — with the only
                # escape being a hand-typed citation, which SKILL.md forbids in
                # capitals. A gate that blocks its own tool's output is the
                # PR #78 infinite loop. One question, one answer.
                try:
                    ap = by_rel[row["resolved_path"]]
                    if (not looks_binary(ap)
                            and coverage_admissible_lines(
                                ap,
                                _flagged_for(flagged, row.get("path")
                                             or row.get("normalized_path")),
                                row["resolved_path"])):
                        cov_false_unreadable.append(row)
                except Exception:                  # noqa: BLE001
                    pass   # genuinely unreadable — the row's claim stands
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
    #
    # «не смотрел» IS AN ADMISSION, NOT COVERAGE. The first version of this check
    # counted any row as coverage, which made 12,368 bytes of «| path | не
    # смотрел | причина=лимит |» the new cheapest path to green: on the real v36
    # report it took blocking from 134 to 5. That is the same lie in a new shape.
    #
    # The other no-address statuses stay valid because they are MACHINE-CHECKED
    # against the file — `cov_false_empty` catches «пусто» on a non-empty file
    # and `cov_false_binary` catches «двоичный» on text. Only «не смотрел» is
    # unverifiable by construction, so only it fails to discharge the file.
    cov_uncovered, cov_unexamined = [], []
    if by_rel and not index_truncated:
        examined = {resolved for resolved, rows in path_rows.items()
                    if any((r.get("status") or "").strip().lower() != "не смотрел"
                           for r in rows)}
        cov_uncovered = sorted(set(by_rel) - set(path_rows))
        cov_unexamined = sorted(set(path_rows) - examined)

    # v38 rollover. `cited` = every corpus file a FINDING leans on; those
    # channels owe a window even when they are contiguous, because "no such
    # event in the log" is only sound inside a window with no holes.
    cited_by_findings = set()
    for _num, flo, fhi in finding_blocks_here:
        for c in _line_citations(citations, flo, fhi):
            if c.get("resolved"):
                cited_by_findings.add(c["resolved"])
    rollover = rollover_evidence(report, corpus, sorted(cited_by_findings),
                                 structural, sections)

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
                + len(cov_inadmissible_line) + len(cov_false_unreadable)
                + len(cov_unsupported) + len(cov_smuggled) + len(cov_malformed)
                + len(cov_traversal) + len(cov_ambiguous) + len(cov_missing_path)
                + len(cov_false_empty) + len(cov_false_binary)
                + len(cov_duplicate_paths)
                + len(cov_uncovered) + len(cov_unexamined)
                + (1 if index_truncated else 0)
                + rollover["blocking"])
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
                     "inadmissible_line": [r["report_line"] for r in cov_inadmissible_line],
                     "inadmissible_line_detail": [
                         {"line": r["report_line"], "path": r.get("path"),
                          "why": r.get("inadmissible_why")}
                         for r in cov_inadmissible_line],
                     "traversal_path": [r["report_line"] for r in cov_traversal],
                     "ambiguous_path": [{"line": r["report_line"], "path": r["path"],
                                         "candidates": resolve_coverage_path(
                                             r["path"], by_rel, by_base)[1]}
                                        for r in cov_ambiguous],
                     "missing_path": [r["report_line"] for r in cov_missing_path],
                     "false_empty": [r["report_line"] for r in cov_false_empty],
                     "false_binary": [r["report_line"] for r in cov_false_binary],
                     "false_unreadable": [r["report_line"] for r in cov_false_unreadable],
                     "line_rule": ("применено" if flagged_known
                                   else "пропущено — нужен --ledger worklist.tsv"),
                     "duplicate_paths": cov_duplicate_paths,
                     "uncovered_paths": cov_uncovered,
                    "unexamined_paths": cov_unexamined,
                     "index_truncated": index_truncated,
                     "unsupported_no_address": [r["report_line"] for r in cov_unsupported],
                     "invalid_no_address_detail": [r["report_line"] for r in cov_smuggled],
                     "content_claim_in_no_address": [r["report_line"] for r in cov_smuggled],
                     "malformed": [r["report_line"] for r in cov_malformed]},
        "rollover": rollover,
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
        out.append("  Каждый файл корпуса обязан получить строку.")
    unex = c.get("unexamined_paths") or []
    if unex:
        out.append("  НЕ СМОТРЕЛ: %d файлов закрыты признанием, а не проверкой"
                   % len(unex))
        for path in unex[:20]:
            out.append("    · %s" % path)
        if len(unex) > 20:
            out.append("    · … и ещё %d" % (len(unex) - 20))
        out.append("  «не смотрел» — это честная запись, но она НЕ закрывает файл: "
                   "её нечем проверить. «пусто» и «двоичный» закрывают, потому что "
                   "их сверяют с файлом. Посмотри — или оставь и не жди зелёного.")
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
    if c.get("inadmissible_line"):
        out.append("  строки покрытия, цитата которых стоит на строке-заглушке "
                   "(строка 1 длинного файла или произвольная строка неотмеченного "
                   "файла — доказывает, что файл открыли, и ничего больше): %s"
                   % ", ".join(str(x) for x in c["inadmissible_line"]))
        for det in (c.get("inadmissible_line_detail") or [])[:10]:
            out.append("    строка %s · %s · %s"
                       % (det.get("line"), det.get("path"), det.get("why")))
        out.append("    почини так: python3 covermap.py --corpus <LOG_DIR> "
                   "--worklist ./work/worklist.tsv — он выбирает допустимую строку сам")
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
    if c.get("line_rule", "").startswith("пропущено"):
        out.append("  ПРАВИЛО ДОПУСТИМОЙ СТРОКИ НЕ ПРОВЕРЕНО: нет --ledger "
                   "work/worklist.tsv, поэтому неизвестно, что пометил logmap. "
                   "Гейт запускается с --ledger; здесь строки покрытия по "
                   "номеру строки не проверялись.")
    if c.get("false_unreadable"):
        out.append("  строки «нечитабельно», чей файл читается и даёт цитату: %s"
                   % ", ".join(str(x) for x in c["false_unreadable"]))
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
    ro = render_rollover(e.get("rollover"))
    if ro:
        out.append("  " + ro.replace("\n", "\n  "))
    return "\n".join(out)


# --------------------------------------------------------------------------
# AGGREGATE CITATIONS — evidence for a POPULATION, not for one line
# --------------------------------------------------------------------------
# WHY THIS EXISTS, measured. The line-quote citation proves that ONE line
# exists and says what the claim says. It cannot prove a statement about a
# population: «93 different source IPs authenticate against this host, and 8 of
# them fail more than 1000 times each» has no single line to quote, so under a
# quote-only gate the only legal move is to DELETE the claim. Measured on the
# winevtx corpus: v36 failed its gates and named 12 attacker IPs; v37 passed all
# three gates and named 4, out of 93 real distinct sources in Security.jsonl.
# Passing the gate made the report worse. EVIDENCE: fix-3.
#
# The shape: file + predicate + count + a reproducing command, and the gate
# RE-EXECUTES THE PREDICATE against the corpus and compares the count. That is
# what makes it evidence rather than an assertion.
#
# THE COMMAND IS NEVER EXECUTED BY THE GATE. It is *rendered* from the parsed
# predicate by `agg_render_command()` and compared for byte equality with what
# the report wrote. Two properties fall out of that, both load-bearing:
#   * no injection surface — a report cannot make the gate run anything;
#   * no producer/grader drift — a hand-edited command that no longer describes
#     the predicate the gate evaluated is a FAILURE, not a cosmetic difference.
# The predicate vocabulary is closed and implemented here, in Python; the
# rendered `jq`/`grep` pipeline exists so a human can reproduce the number by
# hand, and it is the only thing in the line a human is meant to paste.
#
# Grammar, ONE line, produced by `cite.py --aggregate` and never typed by hand:
#
#   агрегат: <path> · <predicate> = <count> · <command>
#
# `<path>` is from the corpus root, same rule as a line citation. There is no
# `:line`, which is exactly how this form is told apart from a line quote —
# CITE_RE requires `:\d+` and never matches an aggregate line.
#
# Predicates (closed vocabulary, three forms):
#
#   count(F op V[, F op V]*)          records matching EVERY filter
#   distinct(FIELD[, F op V]*)        distinct non-empty values of FIELD
#   distinct_over(FIELD, N[, F op V]*)  distinct values of FIELD occurring
#                                       strictly more than N times
#
# Operators: `=` exact string equality, `!=` inequality (a record whose field is
# absent never satisfies it — absence is not evidence), `~=` literal substring
# (NOT a regex —
# a regex in a report is unbounded work and a ReDoS surface), `>=` / `<=`
# LEXICOGRAPHIC string comparison. Lexicographic is not a compromise: the
# window predicate this exists for is an ISO-8601 timestamp, where lexicographic
# and chronological order coincide, and one comparison rule means the rendered
# `jq` says exactly what the gate did.
#
# FIELD is a dotted path into the JSON record (`Event.EventData.IpAddress`), or
# the pseudo-field `line` — the raw text of the line — for corpora that are not
# JSONL. A predicate may not mix the two.
#
# TOLERANCE IS ZERO. The count must match EXACTLY. The corpus is immutable, the
# evaluation is pure and deterministic, there is no sampling and no float, and
# the producer tool prints the number the gate computes — so exactness costs an
# honest report nothing, while any band is a place to park a number that is
# wrong. Silent tolerance is how gates go soft.
#
# FAIL CLOSED, every one of these:
#   malformed        the line or the predicate does not parse
#   missing-file     nothing in the corpus resolves to that path
#   ambiguous        the path means more than one file
#   binary-file      the path leads into a binary file
#   not-tabular      a JSON field was used on a file that is not JSON records
#   unknown-field    the field appears in NO record — a typo reads as zero
#   zero-match       the predicate matched nothing; zero is not a population
#   too-broad        the predicate matched EVERY record — «all records» proves
#                    nothing, and it is the cheapest fake number in the file
#   count-mismatch   the recomputed count differs from the claimed one
#   command-mismatch the pasted command is not the rendering of the predicate
#   unreadable       the file could not be read / the evaluation raised
# Anything that is not `ok` is blocking. There is no "probably fine".

MAX_AGG_LINES = 5000000
MAX_AGG_FILTERS = 6

AGG_KEYWORD = "агрегат"
# The `улики:` prefix is accepted because that is where the report grammar puts
# evidence: `улики: агрегат: …` must be one aggregate citation, not prose that
# the gate silently ignores. A form the gate cannot see is a form that does not
# exist — that is the failure this whole fix is about.
AGG_LINE_RE = re.compile(
    r"^\s*[#>*\-\s]{0,8}(?:\*\*|__|`)?\s*(?:улики\s*[:：]\s*)?"
    + AGG_KEYWORD + r"\s*[:：]\s*(?P<body>.+?)\s*$", re.IGNORECASE)
# The path is EITHER bare (no space, no `·`) OR double-quoted. Without the
# quoted alternative `cite.py --aggregate` printed, for `My Log.jsonl`, a line
# this very regex then graded `malformed` — and both SKILL.md and
# report-format.md forbid hand-editing the line, so the model had no legal
# move: an unfixable loop. Windows event-log exports with spaces in the name
# are ordinary. A `"` in the path itself is REFUSED at both ends (see
# `agg_quote_path`) rather than escaped: an escape grammar is one more thing
# the producer and the grader can disagree about.
AGG_BODY_RE = re.compile(
    r"^(?:\"(?P<qpath>[^\"]+)\"|(?P<path>[^\s·]+))\s*·\s*"
    r"(?P<pred>[a-z_]+\([^·]*\))\s*=\s*"
    r"(?P<count>\d{1,12})\s*·\s*(?P<cmd>.+?)$")
AGG_PRED_RE = re.compile(r"^(count|distinct|distinct_over)\((.*)\)$")
AGG_FIELD_RE = re.compile(r"^(?:line|[A-Za-z_#][A-Za-z0-9_.#\-]*)$")
AGG_FILTER_RE = re.compile(
    r"^(?P<field>line|[A-Za-z_#][A-Za-z0-9_.#\-]*)\s*(?P<op>~=|!=|>=|<=|=)\s*"
    r"(?P<value>.+)$")
AGG_OPS = ("~=", "!=", ">=", "<=", "=")
# `>=` / `<=` are LEXICOGRAPHIC, and the docstring's whole defence of that is
# «the window predicate this exists for is an ISO-8601 timestamp, where
# lexicographic and chronological order coincide». Nothing enforced it, so
# `count(Event.System.EventID<=5) = 33841` verified — those are 4625/4624
# events, and "EventID <= 5" is numerically absurd. Lexicographic order is
# honest only for a zero-padded fixed-width encoding; ISO-8601 is the one such
# encoding in this vocabulary. So the operator is CONSTRAINED to it rather than
# quietly reinterpreted: a numeric comparison the gate cannot do honestly is
# refused, not approximated. Range comparisons on numbers stay unavailable —
# say what you mean with `=` / `~=`, or state the number in prose beside a
# `count(...)` that is exact.
AGG_ORDERED_VALUE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}(?::\d{2}(?::\d{2}(?:\.\d{1,9})?)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$")
# A one-character `~=` value is not a filter, it is «records that have this
# field», dressed as one: `count(Event.EventData.SubStatus~=0) = 33456` on the
# real corpus. Two characters is not a cure for that class — the population
# guard below is — but it removes the cheapest instance of it.
AGG_MIN_CONTAINS = 2
AGG_VERDICTS = ("ok", "malformed", "missing-file", "ambiguous", "binary-file",
                "not-tabular", "unknown-field", "zero-match", "too-broad",
                "count-mismatch", "command-mismatch", "unreadable")

_MISSING = object()


class AggError(Exception):
    """Malformed aggregate citation. Carries the reason the report will print."""


def _agg_strip_wrap(s):
    s = s.strip()
    for a, b in (("`", "`"), ("«", "»"), ('"', '"'), ("**", "**")):
        while len(s) > len(a) + len(b) and s.startswith(a) and s.endswith(b):
            s = s[len(a):len(s) - len(b)].strip()
    return s


def _agg_split_args(inner):
    """Split predicate arguments on commas. No nesting: a value containing a
    comma or a parenthesis is a parse error, not a guess."""
    if "(" in inner or ")" in inner:
        raise AggError("в аргументах предиката не может быть скобок")
    return [a.strip() for a in inner.split(",")] if inner.strip() else []


def agg_parse_predicate(text):
    """`count(a=b, c=d)` -> a dict. Raises AggError. The ONE parser."""
    text = text.strip()
    m = AGG_PRED_RE.match(text)
    if not m:
        raise AggError("предикат должен быть count(...), distinct(...) "
                       "или distinct_over(...)")
    kind, inner = m.group(1), m.group(2)
    args = _agg_split_args(inner)
    field, threshold = None, None
    if kind in ("distinct", "distinct_over"):
        if not args:
            raise AggError("%s(...) требует поле первым аргументом" % kind)
        field = args.pop(0)
        if not AGG_FIELD_RE.match(field):
            raise AggError("не поле: %s" % field)
        if field == "line":
            raise AggError("distinct по псевдополю line не поддерживается")
    if kind == "distinct_over":
        if not args:
            raise AggError("distinct_over(поле, N) требует порог N")
        raw = args.pop(0)
        if not raw.isdigit():
            raise AggError("порог distinct_over должен быть целым: %s" % raw)
        threshold = int(raw)
        if threshold < 1:
            raise AggError("порог distinct_over должен быть >= 1")
    filters = []
    for a in args:
        fm = AGG_FILTER_RE.match(a)
        if not fm:
            raise AggError("фильтр не разобран (нужно поле=значение): %s" % a)
        filters.append((fm.group("field"), fm.group("op"),
                        fm.group("value").strip()))
    if kind == "count" and not filters:
        raise AggError("count() без фильтров считает весь файл — это не улика")
    if len(filters) > MAX_AGG_FILTERS:
        raise AggError("слишком много фильтров (максимум %d)" % MAX_AGG_FILTERS)
    fields = ([field] if field else []) + [f for f, _o, _v in filters]
    if any(f == "line" for f in fields) and any(f != "line" for f in fields):
        raise AggError("предикат смешивает line и поля JSON — так нельзя")
    for f, op, v in filters:
        if f == "line" and op in (">=", "<="):
            raise AggError("для псевдополя line допустимы только =, != и ~=")
        if op == "~=" and len(v) < AGG_MIN_CONTAINS:
            raise AggError(
                "значение для ~= короче %d символов: «%s» — такой фильтр "
                "совпадает почти со всем и означает «у записи есть это поле», "
                "а не «поле равно чему-то»" % (AGG_MIN_CONTAINS, v))
        if op in (">=", "<=") and not AGG_ORDERED_VALUE_RE.match(v):
            raise AggError(
                "сравнение %s лексикографическое и допустимо только для "
                "значения в форме ISO-8601 (2021-06-01 или "
                "2021-06-01T18:36:04.949933Z); «%s» не такое — для чисел "
                "лексикографический порядок врёт" % (op, v))
    return {"kind": kind, "field": field, "threshold": threshold,
            "filters": filters, "text": text,
            "mode": "line" if "line" in fields else "json"}


def agg_parse_line(body):
    """The whole `агрегат:` body -> a parsed citation dict. Raises AggError."""
    m = AGG_BODY_RE.match(body.strip())
    if not m:
        raise AggError("строка не разобрана; формат: "
                       "агрегат: <путь> · <предикат> = <число> · <команда>")
    pred = agg_parse_predicate(m.group("pred"))
    path = m.group("qpath") or m.group("path")
    if '"' in path:
        raise AggError("в пути не может быть символа \" — переименуй файл")
    return {"path": path, "predicate": pred,
            "claimed": int(m.group("count")),
            "command": _agg_strip_wrap(m.group("cmd"))}


# ---- rendering the reproducing command (never executed, only compared) -----
def _sh_q(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _jq_s(s):
    return json.dumps(s, ensure_ascii=False)


def _jq_path(field):
    return "." + ".".join(('"%s"' % p) if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", p)
                          else p for p in field.split("."))


def agg_quote_path(path):
    """The path as it appears IN the citation line. Bare when it can be, double
    quoted when a space or the `·` separator would otherwise break the grammar.
    `agg_parse_line` reads both, so producer and grader round-trip."""
    if '"' in path:
        raise AggError("в пути не может быть символа \" — переименуй файл")
    if path == "" or any(c.isspace() for c in path) or "\u00b7" in path:
        return '"%s"' % path
    return path


def _jq_cond(field, op, value):
    """One filter, as jq — and it must decide EXACTLY what `_agg_cmp` decides.

    It did not. `_agg_str` returns None for a field that is absent or JSON
    null, and `agg_evaluate` then EXCLUDES the record — absence is not
    evidence. The rendered jq said only `(.f|tostring) != "v"`, and in jq a
    missing path is `null`, whose `tostring` is the string "null", which
    happily satisfies `!=` and `contains`. Measured on the real corpus:
    `count(Event.EventData.IpAddress!=-)` — the gate says 33455, the command it
    printed says 34728. The entire justification for shipping a command is that
    a human can reproduce the number; for `!=` and `~=` they could not.
    `distinct(...)` only looked right because the trailing `// empty` masked
    the same bug.

    So every condition is now guarded by presence: `!= null` first, and the
    whole thing wrapped in `try … catch false` so a path through a non-object
    (which `_agg_get` reports as absent) is excluded rather than an error.
    `false` survives the guard, because `_agg_str(False)` is "false".
    """
    p = _jq_path(field)
    lhs = "(%s|tostring)" % p
    if op == "=":
        cmp_ = "%s == %s" % (lhs, _jq_s(value))
    elif op == "!=":
        cmp_ = "%s != %s" % (lhs, _jq_s(value))
    elif op == "~=":
        cmp_ = "(%s|contains(%s))" % (lhs, _jq_s(value))
    else:
        cmp_ = "%s %s %s" % (lhs, op, _jq_s(value))
    return "(try (%s != null and %s) catch false)" % (p, cmp_)


def agg_render_command(path, pred):
    """The pasteable rendering of a predicate. Deterministic; the gate compares
    the report's command to this string and NEVER runs either one."""
    if pred["mode"] == "line":
        f, op, v = pred["filters"][0]
        if len(pred["filters"]) != 1:
            raise AggError("для псевдополя line поддерживается ровно один фильтр")
        if op == "=":
            return "grep -c -x -F -- %s %s" % (_sh_q(v), _sh_q(path))
        if op == "!=":
            return "grep -c -v -x -F -- %s %s" % (_sh_q(v), _sh_q(path))
        return "grep -c -F -- %s %s" % (_sh_q(v), _sh_q(path))
    conds = " and ".join(_jq_cond(f, o, v) for f, o, v in pred["filters"])
    sel = "select(%s) | " % conds if conds else ""
    if pred["kind"] == "count":
        return "jq -c %s -- %s | wc -l" % (_sh_q("select(%s)" % conds), _sh_q(path))
    # `X // empty` also drops `false` and, for a missing path, printed nothing
    # only by luck. `agg_evaluate` skips a value that is absent, null or the
    # empty string and keeps everything else, including `false`. Say that.
    val = ("%s(try (%s) catch null) | select(. != null) | tostring "
           "| select(. != \"\")" % (sel, _jq_path(pred["field"])))
    if pred["kind"] == "distinct":
        return "jq -r %s -- %s | sort -u | wc -l" % (_sh_q(val), _sh_q(path))
    return ("jq -r %s -- %s | sort | uniq -c | awk %s | wc -l"
            % (_sh_q(val), _sh_q(path),
               _sh_q("$1 > %d" % pred["threshold"])))


def agg_render_citation(path, pred, count):
    return "агрегат: %s · %s = %d · `%s`" % (
        agg_quote_path(path), pred["text"], count,
        agg_render_command(path, pred))


# ---- evaluation ----------------------------------------------------------
def _agg_get(rec, field):
    cur = rec
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _agg_str(v):
    if v is None or v is _MISSING:
        return None
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return str(v)


def _agg_cmp(sv, op, value):
    if op == "=":
        return sv == value
    if op == "!=":
        return sv != value
    if op == "~=":
        return value in sv
    if op == ">=":
        return sv >= value
    return sv <= value


def agg_evaluate(abspath, pred):
    """-> (verdict, actual_or_None, detail). Pure, deterministic, no shell.

    Fails closed: any unexpected condition becomes a named non-`ok` verdict.
    """
    fields = ([pred["field"]] if pred["field"] else []) \
        + [f for f, _o, _v in pred["filters"]]
    seen_field = dict((f, False) for f in fields)
    total = 0
    parsed = 0
    unparsed = 0
    matched = 0
    present = 0        # records where EVERY filter field is there to test
    values = {}
    try:
        op = opener(abspath)
        with op(abspath, "rt", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                total += 1
                if total > MAX_AGG_LINES:
                    return ("unreadable", None,
                            "файл длиннее %d строк" % MAX_AGG_LINES)
                if pred["mode"] == "line":
                    text = raw.rstrip("\n")
                    parsed += 1
                    present += 1
                    seen_field["line"] = True
                    if all(_agg_cmp(text, o, v) for _f, o, v in pred["filters"]):
                        matched += 1
                    continue
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    unparsed += 1
                    continue
                if not isinstance(rec, dict):
                    unparsed += 1
                    continue
                parsed += 1
                ok = True
                all_there = True
                for f, o, v in pred["filters"]:
                    sv = _agg_str(_agg_get(rec, f))
                    if sv is None:
                        ok = False
                        all_there = False
                        continue
                    seen_field[f] = True
                    if not _agg_cmp(sv, o, v):
                        ok = False
                if all_there:
                    present += 1
                if not ok:
                    continue
                if pred["field"]:
                    sv = _agg_str(_agg_get(rec, pred["field"]))
                    if sv is None or sv == "":
                        continue
                    seen_field[pred["field"]] = True
                    values[sv] = values.get(sv, 0) + 1
                matched += 1
    except OSError as e:
        return "unreadable", None, "не читается: %s" % e
    except Exception as e:                                # fail closed
        return "unreadable", None, "ошибка разбора: %s" % e

    if pred["mode"] == "json" and parsed == 0:
        return ("not-tabular", None,
                "в файле нет ни одной JSON-записи (%d строк не разобрано); "
                "агрегат по полям JSON требует JSONL" % unparsed)
    if pred["mode"] == "json" and unparsed > parsed:
        return ("not-tabular", None,
                "разобрано %d записей, не разобрано %d — это не JSONL"
                % (parsed, unparsed))
    for f, was in seen_field.items():
        if not was:
            return ("unknown-field", None,
                    "поля «%s» нет ни в одной записи файла" % f)

    if pred["kind"] == "count":
        actual = matched
        # NOT `parsed`. `too-broad` fired only on «matched every record in the
        # file», so a predicate that matched every record that merely HAS the
        # field walked through: on the real corpus
        # `count(Event.EventData.IpAddress!=zzzz) = 33643` and
        # `count(Event.EventData.SubStatus~=0) = 33456` both graded ok. Each is
        # «how many records have this field» wearing a filter, and each reads
        # in a report as a substantive census — the cheapest path to a big
        # true-looking number. The honest population for a count is the set of
        # records the filter could have discriminated between, so that is what
        # it is compared against. This is strictly tighter than the old rule
        # (present <= parsed), never looser.
        population = present
    elif pred["kind"] == "distinct":
        actual = len(values)
        population = parsed
    else:
        actual = sum(1 for v in values.values() if v > pred["threshold"])
        population = len(values)

    if actual == 0:
        return "zero-match", 0, "предикат не совпал ни с чем"
    if population and actual == population:
        return ("too-broad", actual,
                "предикат совпал со ВСЕЙ популяцией (%d из %d) — "
                "утверждение «все записи» ничего не доказывает"
                % (actual, population))
    return "ok", actual, ""


def agg_extract(report, structural=None):
    """-> [{lineno, body, ...}] every `агрегат:` line outside code fences."""
    lines = report.splitlines()
    structural = structural if structural is not None else structural_mask(lines)
    out = []
    for i, line in enumerate(lines, 1):
        if not structural[i - 1]:
            continue
        m = AGG_LINE_RE.match(line)
        if m:
            out.append({"report_line": i, "body": m.group("body")})
    return out


def aggregates_check(report, root, by_rel=None, by_base=None):
    """Grade every aggregate citation in the report. Never runs a command."""
    if by_rel is None:
        by_rel, by_base = index_corpus(root)
    items = []
    for raw in agg_extract(report):
        item = {"report_line": raw["report_line"], "raw": raw["body"],
                "path": None, "predicate": None, "claimed": None,
                "actual": None, "verdict": "malformed", "detail": "",
                "suggest": None}
        try:
            parsed = agg_parse_line(raw["body"])
        except AggError as e:
            item["detail"] = str(e)
            items.append(item)
            continue
        except Exception as e:                            # fail closed
            item["detail"] = "разбор упал: %s" % e
            items.append(item)
            continue
        pred = parsed["predicate"]
        item.update({"path": parsed["path"], "predicate": pred["text"],
                     "claimed": parsed["claimed"],
                     "command": parsed["command"]})
        cand, how = resolve(parsed["path"], by_rel, by_base)
        if not cand:
            item.update(verdict="missing-file",
                        detail="в корпусе такого файла нет")
            items.append(item)
            continue
        if how.endswith("ambiguous"):
            item.update(verdict="ambiguous", candidate_paths=cand,
                        detail="путь означает %d разных файла" % len(cand))
            items.append(item)
            continue
        rel = cand[0]
        item["resolved"] = rel
        if looks_binary(by_rel[rel]):
            item.update(verdict="binary-file",
                        detail="двоичный файл — отрендерь в текст")
            items.append(item)
            continue
        try:
            expect_cmd = agg_render_command(rel, pred)
        except AggError as e:
            item.update(verdict="malformed", detail=str(e))
            items.append(item)
            continue
        verdict, actual, detail = agg_evaluate(by_rel[rel], pred)
        item.update(verdict=verdict, actual=actual, detail=detail)
        if verdict != "ok":
            items.append(item)
            continue
        if actual != parsed["claimed"]:
            item.update(verdict="count-mismatch",
                        detail="в отчёте %d, пересчёт даёт %d"
                               % (parsed["claimed"], actual),
                        suggest=agg_render_citation(rel, pred, actual))
            items.append(item)
            continue
        if parsed["command"] != expect_cmd:
            item.update(verdict="command-mismatch",
                        detail="команда не является рендером предиката",
                        expected_command=expect_cmd,
                        suggest=agg_render_citation(rel, pred, actual))
            items.append(item)
            continue
        items.append(item)
    blocking = sum(1 for i in items if i["verdict"] != "ok")
    return {"items": items, "total": len(items),
            "ok": len(items) - blocking, "blocking": blocking}


def render_aggregates(a):
    if not a or not a["total"]:
        return ""
    out = ["АГРЕГАТЫ: %d ссылок на популяцию, блокирующих %d"
           % (a["total"], a["blocking"])]
    for i in a["items"]:
        mark = "✓" if i["verdict"] == "ok" else "✗"
        out.append("%s %-16s строка %d: %s"
                   % (mark, i["verdict"], i["report_line"], i["raw"][:120]))
        if i["detail"]:
            out.append("    %s" % i["detail"])
        if i.get("expected_command"):
            out.append("    команда должна быть: %s" % i["expected_command"])
        if i.get("suggest"):
            out.append("    например: %s" % i["suggest"])
        if i["verdict"] == "malformed":
            out.append("    не пиши агрегат руками — "
                       "cite.py --corpus <корпус> --file <путь> "
                       "--aggregate '<предикат>'")
    return "\n".join(out)


def _blocking_total(d, report, ledger_path=None, report_path=None):
    """The stop number, taken from `ledger()` itself when there is a ledger.

    The first version of this re-derived the sum and got it WRONG — it dropped
    `unproven` (findings with no confirmed quote), `open_rows` (unparsed ledger
    rows) and two of the six BAD verdicts, so it printed 160 where the gate
    printed «осталось 161», and a report with 200 open rows would have reported
    blocking: 0. A review caught it. Its own docstring had warned that two
    definitions of "how many defects" is how a gate reports one number and exits
    on another — and then was one.

    So there is one definition. With a ledger, ask `ledger()`. Without one, fall
    back to the parts that exist, and say so via the `ledger` key in the JSON.
    """
    if ledger_path:
        _body, total = ledger(d, report, ledger_path, report_path)
        return total
    summary = d.get("summary") or {}
    bad = sum(summary.get(k, 0) for k in BAD)
    o = d.get("outcomes") or {}
    ev = d.get("report_evidence") or report_evidence(report, d)
    agg = d.get("aggregates") or {"blocking": 0}
    return (bad + (summary.get("не-ссылка") or 0)
            + (o.get("blocking") or 0) + (ev.get("blocking") or 0)
            + (agg.get("blocking") or 0))


WORKLIST_MANIFEST = "worklist.manifest.json"


def worklist_removed(path):
    """-> (ids `logmap` emitted that the ledger no longer contains, manifest?).

    P8 review #5. `logmap` writes `worklist.manifest.json` next to
    `worklist.tsv` with the ids it emitted (see `logmap.write_worklist_manifest`
    for why). The model is meant to CLOSE rows, not delete them, and deleting
    them was the cheapest path to green there was: dropping every reference that
    is not `:1` shrank the recorded v37 worklist 250 -> 126 rows and took
    `citecheck` from 59 blocking to 7, because the coverage rule's admissible
    set is derived from the reference column of that very file. Rows this tool
    emitted and cannot find again are reported and they block.

    No manifest -> ([], False): a hand-written fixture worklist is graded
    exactly as it was before. Tamper-EVIDENT, not tamper-proof — the same
    process can rewrite the sidecar; what is gone is the one-`awk` bypass.
    """
    man = os.path.join(os.path.dirname(os.path.abspath(path)),
                       WORKLIST_MANIFEST)
    try:
        with open(man, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return [], False
    want = [str(i) for i in (data.get("ids") or []) if str(i).strip()]
    if not want:
        return [], False
    have = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if raw.startswith("#") or not raw.strip():
                    continue
                rid = raw.split("\t", 1)[0].strip()
                if rid:
                    have.add(rid)
    except OSError:
        return want, True
    return [i for i in want if i not in have], True


def ledger(d, report, path, report_path=None):
    rows, closed = read_ledger(path)
    removed, has_manifest = worklist_removed(path)
    open_rows = [r for r in rows
                 if r["state"] == "open" and not (set(r["ids"]) & closed)]
    agg = d.get("aggregates") or {"items": [], "total": 0, "blocking": 0}
    unproven, n_find = findings_without_evidence(report, d["citations"], agg)
    o = d.get("outcomes") or outcomes_of(report)
    s = d["summary"]
    bad = sum(s[k] for k in BAD)
    nonref = s["не-ссылка"]
    out = ["", "ЛЕДЖЕР — условие остановки (все семь чисел обязаны быть 0)",
           "  строк, удалённых из рабочего списка: %d%s"
           % (len(removed),
              "" if has_manifest else "  (нет worklist.manifest.json — не проверено)"),
           "  неразобранных строк: %d из %d" % (len(open_rows), len(rows)),
           "  находок без подтверждённой цитаты: %d из %d" % (len(unproven), n_find),
           "  находок без строки «исход»: %d из %d"
           % (len(o["missing"]) + len(o["invalid"]), n_find),
           "  плохих цитат (wrong-content/out-of-range/missing-file/без цитаты): %d"
           % bad,
           "  ссылок, которые я не проверял (не разрешились в корпусе): %d" % nonref,
           "  агрегатов, не сошедшихся с корпусом: %d из %d"
           % (agg["blocking"], agg["total"])]
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
    agg_block = render_aggregates(agg)
    if agg_block:
        out.append("")
        out.append(agg_block)
    if removed:
        out.append("  РАБОЧИЙ СПИСОК УКОРОЧЕН. logmap выдал %d строк; в файле "
                   "нет %d из них: %s%s"
                   % (len(removed) + len(rows), len(removed),
                      ", ".join(removed[:12]),
                      " …" if len(removed) > 12 else ""))
        out.append("  Строку закрывают вердиктом, её не удаляют. Верни "
                   "рабочий список logmap (или перезапусти logmap.py) и "
                   "закрой строки вердиктом.")
    total = (len(open_rows) + len(unproven) + bad + nonref + o["blocking"]
             + ev.get("blocking", 0) + agg["blocking"] + len(removed))
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
                or (dd.get("report_evidence") or {}).get("blocking")
                or (dd.get("aggregates") or {}).get("blocking"))


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
    agg = render_aggregates(d.get("aggregates"))
    if agg:
        out.append("")
        out.append(agg)
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
        _removed, _has_manifest = worklist_removed(args.ledger)
        d["ledger"] = {"unresolved_total": left, "rows": ledger_rows,
                       "empty": ledger_rows == 0,
                       "manifest": _has_manifest,
                       "removed_rows": _removed}
    d.pop("flagged", None)
    if isinstance(d.get("delivered"), dict):
        d["delivered"].pop("flagged", None)
    if args.json:
        # The stop number, at top level and under the name every gate uses.
        # `ledger()` computes it and returns it beside the human render; the
        # JSON carried only the parts, so run-bench.sh's blocking check was
        # dead for this gate. Recomputed here from the same pieces.
        d["blocking"] = _blocking_total(
            d, text, args.ledger,
            None if args.report == "-" else args.report)
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
                            or d.get("report_evidence", {}).get("blocking")
                            or (d.get("aggregates") or {}).get("blocking"))
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
