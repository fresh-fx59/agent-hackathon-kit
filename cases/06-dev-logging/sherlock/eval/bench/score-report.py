#!/usr/bin/env python3
"""score-report.py — how good is the REPORT, in one record.

    # free: no judge, no network, no money. This is the default on purpose.
    python3 score-report.py --key answer-key-bluesky.json \
        --corpus /path/to/corpus --report work/report.md

    # add the judged column (cliproxyapi broker, subscription — never linkapi)
    JUDGE_API_KEY=... python3 score-report.py ... --judge

    # or pull the report out of the run ledger, both delivery channels united
    python3 score-report.py --key answer-key.json --corpus … --ledger runs-bench.jsonl \
        --dataset bench649 --arm v11 --trace 20260802T221034Z

Everything this project measured before now scored a WORKLIST — Step 1's 250-row
attention budget. That is an upper bound on what the skill can find and says
nothing about what it told the reader. The deliverable is a report; this scores
one, on five axes that are deliberately never summed into a single number.

    verdict    compromised / attacked-not-proven / clean, or `absent`
    anchored   did the report CITE this defect's proof?      ← free, primary
    presented  did it cite it INSIDE its findings section?   ← free, structural
    asserted   did the report CLAIM this defect?             ← judged, optional
    citations  do the cited lines say what the report says?  ← free, citecheck

WHY `anchored` IS THE PRIMARY FINDINGS NUMBER. Every real defect in the key
carries `proof_locations`. Whether the report put one of them in front of the
reader is path-and-integer arithmetic against the corpus index: no judge, no
token, no money, and therefore replayable over every trajectory this project has
ever stored. It is also the number a judge cannot drift on.

WHY IT IS CALLED `anchored` AND NOT `found`. Citing the right line is not
understanding it. A report can quote the exact line that proves the intrusion and
conclude the box is clean. `asserted` is the separate, judged column, and the two
never share a cell. When no judge ran, `asserted` is None — never 0. An unasked
question recorded as a negative answer is the same class of lie as an unmeasured
cost recorded as 0.

WHY THERE IS A THIRD FINDINGS COLUMN, AND WHY IT IS FREE. Two measured cases show
the gap `anchored` cannot see. BlueSky D01 was anchored and then ARGUED AWAY as
pre-compromise inside «Отклонённые кандидаты»; BlueSky D09's evidence was filed
under a different finding. The only column that could catch either was `asserted`
— and the judge is the unstable axis: one identical report scored 0/2, 2/2, 2/2
on decoys across three runs. But these reports have a STRUCTURE, so the claim can
be read instead of judged. A citation inside the findings section is an
assertion; the same citation inside the rejected-candidates section is a mention.
That is parsing, and it costs nothing.

    presented   anchored by a citation written inside «Находки»
    dismissed   anchored ONLY inside «Отклонённые кандидаты»
    decoys_presented   the free half of `decoys_asserted`

`anchored` STAYS beside it and is never merged into it: `anchored` is what makes
every stored trajectory re-scorable, and this project does not sum axes. On the
six reports measured, `presented` never rose and twice fell — BlueSky v19 10 -> 9
(D01), the negative control 10 -> 6 (four observations that live only in the
inventory table). That is the honest direction for a stricter question.

FAIL LOUD ON A REPORT THAT HAS NO STRUCTURE. If the findings section is not
found, every column above is None and the record says why. Reporting 0 would
read as "the report asserted nothing", which is a claim about the report rather
than about the parse.

A REJECTION WITH NO CITATION IS NOT A JUDGEMENT. The same parse counts the
disposal rows — the rejected-candidates items and the coverage-table rows — and
how many of them hand the reader nowhere to look: no `path:line`, and no
reference to a finding this report actually wrote a heading for. «ничего
относящегося» against a whole path family is the shape being counted.

WHY DECOYS GET THEIR OWN TWO NUMBERS. `decoys_anchored` and `decoys_asserted`
never touch the findings numerator, and the denominator is real defects only —
otherwise a report that names everything it sees beats one that discriminates.
This is the number the field actually reports: OpenSec measures FP rate directly,
and Sonnet 4.6's 100 % containment comes with a 92.5 % false-positive rate. The
negative control (`answer-key-fleet-negative.json`) has NO real defects at all, so
these two are the only numbers it produces.

WHY AN AMBIGUOUS CITATION ANCHORS NOTHING. v16 made ambiguity fail closed inside
citecheck because a corpus that ships `auth.log` on ten hosts turns a bare
`auth.log:8977` into a confident claim about a machine the report never named.
Anchoring is a second door into the same room, so it obeys the same rule.

SECOND LOCATIONS FOR THE SAME EVENT. A key may carry
`alternate_proof_locations` beside `proof_locations`, and both anchor. AIT-LDS
labels ONE host's copy of traffic two hosts logged, so an analyst who proved the
DNS story from `inet-dns` was right and scored a miss. The equivalence is
COMPUTED in `build-answer-key-ait.py` — same timestamp, message body identical
byte for byte once the syslog PID is removed, and nothing else normalised — never
asserted here. Keys without the field are unaffected.

REUSE, DON'T FORK. `score-verdict.py` decides the verdict, `score-bench.py`
decides the judged column (including the inverted decoy prompt), `deliverable.py`
decides what a run actually handed over, and `skills/<current>/tools/citecheck.py`
decides citation integrity. A second copy of any of them is a second,
incomparable scale.

FAIL LOUD. Missing corpus, empty report, judge transport failure: all raise. A
transport error recorded as "not found" is indistinguishable from a real miss.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# WHICH citecheck — resolved, guarded, and receipted
# --------------------------------------------------------------------------
# `tools/citecheck.py` in the working tree is a v5-v10 snapshot with no
# `ambiguous` verdict at all, and loading it would silently delete the one column
# this scorer promises to keep visible. That was the reason the path used to be
# PINNED to `skills/v16`.
#
# A pin is a guard that expires. v19 taught citecheck to read gzipped text (v16
# calls every `.gz` a binary because it sniffs the RAW bytes, and a gzip stream is
# full of NULs), and the negative-control key was then re-derived across the
# rotations — R01 went 6,650 -> 16,501 because `mon/auth/auth.log.2.gz` alone
# holds 9,851 of them. On a v16 pin, a report that CORRECTLY cites a `.gz` proof
# location scores `binary-file`. No arm has cited one yet; the first that does
# would have been scored wrong.
#
# So the version is resolved to the highest one on disk (a named version, and
# `$SHERLOCK_CITECHECK_VERSION` pins it explicitly when an old score has to be
# re-checked), the guard is the BEHAVIOUR the comment always described — the
# loaded checker must actually PRODUCE `ambiguous` — and `citecheck_version` +
# `citecheck_sha` + `citecheck_path` in the record are the receipt.
VERSION_DIR_RE = re.compile(r"^v(\d+(?:\.\d+)*)$")


def _version_tuple(name):
    m = VERSION_DIR_RE.match(name)
    if not m:
        raise ValueError("not a skill version directory name: %r" % name)
    return tuple(int(x) for x in m.group(1).split("."))


def _skill_citecheckers(root):
    """[(version_tuple, name, path)] for every skills/vN that ships citecheck.py."""
    skills = os.path.join(root, "skills")
    out = []
    for name in sorted(os.listdir(skills)) if os.path.isdir(skills) else []:
        if not VERSION_DIR_RE.match(name):
            continue
        p = os.path.join(skills, name, "tools", "citecheck.py")
        if os.path.isfile(p):
            out.append((_version_tuple(name), name, p))
    return out


def resolve_citecheck(root=SHERLOCK, want=None):
    """-> (version_name, path). Highest version on disk, or the one asked for."""
    have = _skill_citecheckers(root)
    if not have:
        raise RuntimeError(
            "no skills/v*/tools/citecheck.py under %s. This scorer will not fall "
            "back to tools/citecheck.py: that copy is a v5-v10 snapshot with no "
            "`ambiguous` verdict, and scoring with it would delete a column "
            "silently." % root)
    want = want or os.environ.get("SHERLOCK_CITECHECK_VERSION")
    if want:
        for _v, name, path in have:
            if name == want:
                return name, path
        raise RuntimeError("no such citecheck version: %s (have %s)"
                           % (want, ", ".join(n for _v, n, _p in have)))
    _v, name, path = max(have)
    return name, path


def assert_ambiguity_capable(mod, name, path):
    """RAISE unless this checker really produces `ambiguous` on a two-host corpus.

    Not `"ambiguous" in VERDICTS` — that is a string, and a string cannot go
    stale in the direction that matters. This builds the exact shape v16 was
    written for (one basename, two machines) and demands the verdict come out.
    """
    import shutil
    import tempfile as _tf
    tmp = _tf.mkdtemp(prefix="citecheck-guard-")
    try:
        for host in ("host-a", "host-b"):
            d = os.path.join(tmp, host)
            os.makedirs(d)
            with open(os.path.join(d, "auth.log"), "w", encoding="utf-8") as fh:
                fh.write("Accepted password for root from 10.0.0.1\n")
        report = "auth.log:1 «Accepted password for root from 10.0.0.1»\n"
        try:
            got = mod.check(report, tmp)
        except Exception as e:
            raise RuntimeError("citecheck %s (%s) could not be run at all: %s"
                               % (name, path, e))
        n = (got.get("summary") or {}).get("ambiguous")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not n:
        raise RuntimeError(
            "citecheck %s (%s) does not produce the `ambiguous` verdict: a bare "
            "`auth.log:1` that could mean two machines came back %r. This scorer "
            "promises to keep that column visible, and a checker without it turns "
            "an unattributable citation into a confident one. Refusing to score."
            % (name, path, got.get("summary")))
    return True


CITECHECK_VERSION, CITECHECK_PATH = resolve_citecheck(SHERLOCK)
citecheck = _load("citecheck_" + CITECHECK_VERSION.replace(".", "_"), CITECHECK_PATH)
assert_ambiguity_capable(citecheck, CITECHECK_VERSION, CITECHECK_PATH)
score_case = _load("score_case", os.path.join(SHERLOCK, "measure", "score_case.py"))
score_bench = _load("score_bench", os.path.join(HERE, "score-bench.py"))
score_verdict = _load("score_verdict", os.path.join(HERE, "score-verdict.py"))
deliverable = _load("deliverable", os.path.join(SHERLOCK, "measure", "deliverable.py"))


def _probe_judge_limit():
    """How many characters of the report the judge actually sees — MEASURED.

    `score_case.build_prompt` slices the report and the number lives there, not
    here. Hard-coding 120000 in a second file is how a cap starts lying after
    somebody edits the first one, so it is probed instead: feed a long run of one
    character and see how much of it survives into the prompt.
    """
    probe = "A" * 200000
    p = score_case.build_prompt({"case_id": "PROBE", "title": "", "root_cause": ""},
                                probe)
    runs = re.findall(r"A+", p)
    return max((len(r) for r in runs), default=0)


JUDGE_PROMPT_LIMIT = _probe_judge_limit()


# --------------------------------------------------------------------------
# what the key says the proof is
# --------------------------------------------------------------------------
def _spans_of(locs):
    """[{file, line_start, line_end}] -> [(relpath, lo, hi)]; lo None = whole file."""
    out = []
    for pl in (locs or []):
        f = (pl.get("file") or "").replace("\\", "/").lstrip("./").strip()
        if not f:
            continue
        lo = pl.get("line_start")
        if lo is None:
            out.append((f, None, None))
            continue
        hi = pl.get("line_end")
        out.append((f, int(lo), int(hi if hi is not None else lo)))
    return out


def proof_spans(d):
    """One defect -> [(relpath, line_lo, line_hi)]; (relpath, None, None) = whole file.

    Two key shapes are real and both ship in this directory. `answer-key.json` and
    `answer-key-bluesky.json` carry a list of `proof_locations`
    ({file, line_start, line_end, …}). `answer-key-fleet-negative.json` — the only
    corpus whose right answer is the middle verdict — carries no proof locations
    at all: each decoy has a single `anchor` string, sometimes `path:line` and
    sometimes a bare path. A scorer that reads only the first shape cannot score
    the one run that separates an investigator from an alarm.
    """
    out = _spans_of(d.get("proof_locations"))
    # A second location for the SAME event, where the corpus proves it is the
    # same event. `answer-key-ait-russellmitchell.json` carries these because AIT
    # labels ONE host's copy of traffic two hosts logged: an analyst who proved
    # the DNS story from `inet-dns` was right and scored a miss. The equivalence
    # is computed in `build-answer-key-ait.py` (same timestamp, same message body,
    # only the syslog PID removed) — this scorer just reads it. Keys without the
    # field are unaffected.
    out = out + _spans_of(d.get("alternate_proof_locations"))
    if out:
        return out
    a = d.get("anchor")
    if isinstance(a, str) and a.strip():
        s = a.strip().replace("\\", "/").lstrip("./")
        head, sep, tail = s.rpartition(":")
        if sep and head and tail.isdigit():
            return [(head, int(tail), int(tail))]
        return [(s, None, None)]
    return []


# --------------------------------------------------------------------------
# what the report actually pointed at
# --------------------------------------------------------------------------
def cited_spans(report, root):
    """Every citation in the report, resolved to ONE corpus file, as a line span.

    Reuses citecheck's own extractor, corpus index and resolver — the same three
    functions that decide citation integrity — so "the report cited X" means
    exactly one thing in this project instead of two.

    Ambiguity is dropped, counted and printed. So is a citation that resolves to
    nothing. Neither is silently folded into "the report did not cite it": a
    report that names ten hosts' `auth.log` without saying which has a real defect
    and it is a different one from missing the evidence.
    """
    by_rel, by_base = citecheck.index_corpus(root)
    if not by_rel:
        raise RuntimeError("corpus index is empty: %s holds no files. Scoring a "
                           "report against an absent corpus would report every "
                           "citation as unresolved and every defect as missed."
                           % root)
    spans, ambiguous, unresolved, capped = [], [], [], []
    placed = []                  # the same spans, each with the report line it
    for c in citecheck.extract(report):   # was written on — for the structural axis
        cand, how = citecheck.resolve(c["path"], by_rel, by_base)
        if cand and how.endswith("ambiguous"):
            ambiguous.append({"citation": c["raw"], "candidates": len(cand)})
            continue
        if not cand:
            # citecheck's own gate, reused rather than reinvented: a token is
            # worth reporting as a LOST citation only if its last segment reads
            # like a filename. `11:00` and `10.42.12.20:8080` match the citation
            # regex and resolve to nothing, and counting them made the warning
            # say "15 citations resolved to no file" about a report whose real
            # count was 6. A noisy warning is one nobody reads.
            if citecheck.looks_like_path(c["path"]):
                unresolved.append(c["raw"])
            continue
        lo = c["line"]
        asked = c["range_end"] or lo
        hi = min(asked, lo + citecheck.MAX_RANGE)
        if asked > hi:
            capped.append({"citation": c["raw"], "asked_to": asked, "read_to": hi})
        spans.append((cand[0], lo, max(lo, hi)))
        placed.append({"rel": cand[0], "lo": lo, "hi": max(lo, hi),
                       "lineno": c["lineno"]})
    return {"spans": spans, "placed": placed, "ambiguous": ambiguous,
            "unresolved": unresolved, "capped": capped, "files": by_rel,
            "by_rel": by_rel, "by_base": by_base}


# --------------------------------------------------------------------------
# what the report CLAIMED — read off its own structure, no judge
# --------------------------------------------------------------------------
# `anchored` says the proof was put in front of the reader. It does NOT say the
# report concluded anything, and two measured cases show the gap: BlueSky D09's
# frame was cited under a DIFFERENT finding (the port scan), and BlueSky D01 was
# anchored and then argued away as pre-compromise. The column that could catch
# that was `asserted` — which needs the judge, and the judge is the unstable axis
# (one identical report scored 0/2, 2/2, 2/2 on decoys across three runs).
#
# These reports have a structure: a findings section, and a rejected-candidates
# section («Отклонённые кандидаты»). A citation inside the findings section is an
# ASSERTION; the same citation inside the rejected section is a MENTION. That is
# parsing, not judgement, and it costs nothing. It is reported BESIDE `anchored`
# and never merged with it — `anchored` stays because it is what makes
# retroactive scoring of every stored trajectory possible.
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
FINDINGS_TITLE = re.compile(r"находк|finding", re.I)
REJECTED_TITLE = re.compile(r"отклон|отвергн|reject", re.I)
COVERAGE_TITLE = re.compile(r"покрыти|coverage", re.I)
# A disposal ITEM: a list bullet, a bold lead-in paragraph, or a table row. The
# three shapes the arms actually used; nothing here reads the words.
ITEM_RE = re.compile(r"^(?:[ \t]{0,3}(?:[-*+]|\d+[.)])[ \t]+|\*\*|\|)")
# «Н-9» / «H-9»: the id these reports give their own findings. A disposal that
# points at one is pointing at a heading in the same document, and following it
# is parsing, not judgement.
FINDING_ID_RE = re.compile(r"[НH]-\s?(\d{1,3})")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def sections(report):
    """[{level, title, lineno, body_from, body_to}] over every ATX heading.

    A section owns its subsections: its body runs to the next heading of the same
    level or higher. Headings inside a fenced code block are sample text, not
    structure — a report that shows `## 2. Отклонённые кандидаты` inside a fence
    has not opened that section.
    """
    lines = report.splitlines()
    heads, fence = [], None
    for i, line in enumerate(lines, 1):
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence else m.group(1)
            continue
        if fence:
            continue
        h = HEADING_RE.match(line)
        if h:
            heads.append({"level": len(h.group(1)), "title": h.group(2).strip(),
                          "lineno": i})
    n = len(lines)
    for j, h in enumerate(heads):
        end = n + 1
        for k in range(j + 1, len(heads)):
            if heads[k]["level"] <= h["level"]:
                end = heads[k]["lineno"]
                break
        h["body_from"], h["body_to"] = h["lineno"] + 1, end
    return heads


def items_in(report, spans):
    """The disposal rows inside these line spans, as (first_line, text).

    A row is a bullet, a bold lead-in paragraph or a table data row, and it runs
    until the next such row. Table headers and separator rows are not rows.
    """
    lines = report.splitlines()
    out = []
    for (lo, hi) in spans:
        cur = None
        for i in range(lo, min(hi, len(lines) + 1)):
            line = lines[i - 1]
            if ITEM_RE.match(line):
                if line.lstrip().startswith("|") and TABLE_SEP_RE.match(line):
                    cur = None
                    continue
                cur = [i, [line]]
                out.append(cur)
            elif cur is not None and line.strip():
                cur[1].append(line)
        # a markdown table's first row is its header, not a disposal
    # Deduplicated by text. `measure/deliverable.py` composes the final message
    # and work/report.md into one deliverable, and on every arm measured so far
    # the two channels carry the SAME report — so every section, and every row in
    # it, appears twice. Counting 24 rejections in a report that wrote 12 would be
    # a fact about the delivery channel wearing the costume of a fact about the
    # analysis.
    seen, rows = set(), []
    for i, ls in out:
        text = "\n".join(ls)
        norm = " ".join(text.split())
        if norm in seen:
            continue
        seen.add(norm)
        rows.append((i, text))
    return rows


def _drop_table_headers(report, spans, rows):
    """The first data row of each table is its header line. Dropped by position,
    not by reading it: the row directly above a `|---|---|` separator."""
    lines = report.splitlines()
    heads = set()
    for i in range(1, len(lines) + 1):
        if TABLE_SEP_RE.match(lines[i - 1]) and lines[i - 1].count("|") >= 2:
            heads.add(i - 1)
    return [(i, t) for (i, t) in rows if i not in heads]


def structure_of(report, by_rel, by_base):
    """The report's own structure, or a refusal that says why.

    FAIL LOUD: if the findings section is not there, this axis returns
    `parsed: False` and every column it feeds is None. Reporting 0 would read as
    "the report asserted nothing", which is a claim about the report rather than
    about the parse.
    """
    secs = sections(report)
    finds = [s for s in secs if FINDINGS_TITLE.search(s["title"])]
    rejs = [s for s in secs if REJECTED_TITLE.search(s["title"])]
    covs = [s for s in secs if COVERAGE_TITLE.search(s["title"])]
    st = {"parsed": False, "why": None, "headings": len(secs),
          "findings_sections": [s["title"] for s in finds],
          "rejected_sections": [s["title"] for s in rejs],
          "coverage_sections": [s["title"] for s in covs],
          "findings_spans": [(s["body_from"], s["body_to"]) for s in finds],
          "rejected_spans": [(s["body_from"], s["body_to"]) for s in rejs],
          "rejections": None, "rejections_uncited": None,
          "uncited_rejections": [],
          "coverage_rows": None, "coverage_rows_uncited": None,
          "finding_ids": [], "sections": secs}
    if not finds:
        st["why"] = ("no findings section: none of the %d heading(s) matches "
                     "/находк|finding/ — saw %s. The structural assertion axis "
                     "cannot be read off this report and is None, NOT 0."
                     % (len(secs), ", ".join(repr(s["title"][:40])
                                             for s in secs[:8]) or "no headings"))
        return st
    st["parsed"] = True

    # Every finding id this report actually gave a heading to.
    finding_ids = set()
    for sec in secs:
        for (lo, hi) in st["findings_spans"]:
            if lo <= sec["lineno"] < hi:
                finding_ids |= set(FINDING_ID_RE.findall(sec["title"]))
    st["finding_ids"] = sorted(finding_ids, key=int)

    def uncited(rows):
        """A disposal is BACKED if it hands the reader somewhere checkable: a
        citation that resolves to a corpus file, or the id of a finding this same
        report wrote a heading for. Anything else — «ничего относящегося», a bare
        path, a pointer to a finding that does not exist — is a rejection nobody
        can check, and that is not a judgement."""
        out = []
        for (i, text) in rows:
            hit = False
            for c in citecheck.extract(text):
                cand, _how = citecheck.resolve(c["path"], by_rel, by_base)
                if cand:
                    hit = True
                    break
            if not hit and finding_ids:
                hit = bool(set(FINDING_ID_RE.findall(text)) & finding_ids)
            if not hit:
                out.append((i, " ".join(text.split())[:160]))
        return out

    if rejs:
        rows = items_in(report, st["rejected_spans"])
        rows = _drop_table_headers(report, st["rejected_spans"], rows)
        bad = uncited(rows)
        st["rejections"] = len(rows)
        st["rejections_uncited"] = len(bad)
        st["uncited_rejections"] = [t for _i, t in bad]
    else:
        # One missing half must not delete the other: `presented` is still
        # readable, `dismissed` is not, so it stays None rather than 0.
        st["why"] = ("no rejected-candidates section: none of the %d heading(s) "
                     "matches /отклон|отвергн|reject/. `presented` is still read "
                     "from the findings section; `dismissed` and the rejection "
                     "counts are None, NOT 0." % len(secs))
    if covs:
        spans = [(s["body_from"], s["body_to"]) for s in covs]
        rows = _drop_table_headers(report, spans, items_in(report, spans))
        st["coverage_rows"] = len(rows)
        st["coverage_rows_uncited"] = len(uncited(rows))
    return st


def zone_of(st, lineno):
    """findings | rejected | other — where in the report this citation sits."""
    if not st.get("parsed"):
        return None
    for (lo, hi) in st["findings_spans"]:
        if lo <= lineno < hi:
            return "findings"
    for (lo, hi) in st["rejected_spans"]:
        if lo <= lineno < hi:
            return "rejected"
    return "other"


def heading_of(st, lineno):
    """The innermost heading this line sits under — free, and the only thing that
    makes the BlueSky D09 shape visible: a citation filed under the WRONG finding
    is still inside the findings section, and no free axis can call it wrong, but
    the heading it landed under can be printed for a reader to judge."""
    best = None
    for sec in st.get("sections") or []:
        if sec["body_from"] <= lineno < sec["body_to"]:
            if best is None or sec["level"] >= best["level"]:
                best = sec
    return best["title"] if best else None


def _overlaps(span, plo, phi):
    _, lo, hi = span
    if plo is None:                      # a whole-file anchor
        return True
    return lo <= phi and plo <= hi


def anchor_zones(placed, proofs, st):
    """-> (zones, headings) for every citation of this defect's proof.

    Same overlap arithmetic as `anchor_hits`, carrying WHERE in the report the
    citation was written. A defect can be both presented and dismissed — a report
    that names it as a finding and also argues a piece of it away — and both are
    recorded rather than one overwriting the other.
    """
    zones, heads = set(), []
    for (f, plo, phi) in proofs:
        for c in placed:
            if c["rel"] != f:
                continue
            if not _overlaps((c["rel"], c["lo"], c["hi"]), plo, phi):
                continue
            z = zone_of(st, c["lineno"])
            if z:
                zones.add(z)
            h = heading_of(st, c["lineno"])
            if h and h not in heads:
                heads.append(h)
    return sorted(zones), heads


def anchor_hits(spans, proofs):
    """How many of this defect's proof locations the report pointed at.

    Every proof location inside a cited RANGE counts, not just the first. A
    citation is an address the reader is told to read around, so all of it is in
    front of them — the same rule score-ait.py had to be corrected into.
    """
    n = 0
    for (f, plo, phi) in proofs:
        if any(rel == f and _overlaps((rel, lo, hi), plo, phi)
               for (rel, lo, hi) in spans):
            n += 1
    return n


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------
def score(raw_key, report, root, call=None, min_overlap=0.34, min_tokens=3,
          require_quote=False):
    """One report + one key + one corpus -> one record. `call` is the judge, or None."""
    if not os.path.isdir(root):
        raise RuntimeError("no such corpus directory: %s" % root)
    if not (report or "").strip():
        raise RuntimeError("the report is empty — nothing to score. An empty "
                           "deliverable is a delivery defect, and recording it as "
                           "0-of-N findings would make it look like a bad "
                           "investigation instead of an absent one.")

    key = score_bench.load_key(raw_key)
    dataset = raw_key.get("dataset") if isinstance(raw_key, dict) else None

    # --- axis 1: the verdict, delegated whole -----------------------------
    truth = raw_key.get("verdict") if isinstance(raw_key, dict) else None
    got, how = score_verdict.extract(report)
    verdict = {"truth": truth, "reported": got, "read_from": how,
               "correct": (got == truth) if truth else None}

    # --- axis 2: anchoring, free ------------------------------------------
    cited = cited_spans(report, root)
    spans = cited["spans"]

    # --- axis 2b: the STRUCTURAL assertion column, free -------------------
    st = structure_of(report, cited["by_rel"], cited["by_base"])
    parsed = st["parsed"]

    per = {}
    unanchorable, missing_proof_files = [], set()
    anchored = anchorable = decoys_anchored = decoys = 0
    presented = dismissed = outside = 0
    decoys_presented = decoys_dismissed = 0
    for cid in sorted(key):
        d = dict(key[cid])
        d.setdefault("case_id", cid)
        herring = score_bench.is_herring(d)
        proofs = proof_spans(d)
        for (f, _lo, _hi) in proofs:
            if f not in cited["files"]:
                missing_proof_files.add(f)
        n_alt = len(_spans_of(d.get("alternate_proof_locations")))
        hits = anchor_hits(spans, proofs) if proofs else 0
        is_anchored = bool(proofs) and hits > 0
        zones, heads = (anchor_zones(cited["placed"], proofs, st)
                        if (proofs and parsed) else ([], []))
        is_presented = ("findings" in zones) if parsed else None
        is_dismissed = (bool(zones) and "findings" not in zones
                        and "rejected" in zones) if parsed else None
        per[cid] = {"defect": cid, "herring": herring,
                    "title": d.get("title", ""),
                    "proof_locations": len(proofs) - n_alt,
                    "alternate_proof_locations": n_alt,
                    "anchorable": bool(proofs),
                    "anchored": is_anchored, "anchor_hits": hits,
                    "presented": is_presented, "dismissed": is_dismissed,
                    "anchored_zones": zones, "anchored_headings": heads,
                    "asserted": None, "why": None}
        if herring:
            decoys += 1
            decoys_anchored += 1 if is_anchored else 0
            if parsed:
                decoys_presented += 1 if is_presented else 0
                decoys_dismissed += 1 if is_dismissed else 0
            continue
        if not proofs:
            unanchorable.append(cid)
            continue
        anchorable += 1
        anchored += 1 if is_anchored else 0
        if parsed:
            presented += 1 if is_presented else 0
            dismissed += 1 if is_dismissed else 0
            outside += 1 if (is_anchored and not zones or
                             (zones and "findings" not in zones
                              and "rejected" not in zones)) else 0

    # --- axis 3: citation integrity, free ---------------------------------
    cc = citecheck.check(report, root, min_overlap, min_tokens, require_quote)
    ccs = dict(cc["summary"])

    # --- axis 4: the judged column, opt-in --------------------------------
    asserted = decoys_asserted = total_real = None
    if call is not None:
        dropped = max(0, len(report) - JUDGE_PROMPT_LIMIT)
        if dropped:
            print("⚠ JUDGE TRUNCATION: the report is %d chars and the judge prompt "
                  "carries %d — %d chars dropped, unseen by every judged verdict "
                  "below. The free columns above read the WHOLE report."
                  % (len(report), JUDGE_PROMPT_LIMIT, dropped))
        jres = score_bench.score(raw_key, report, call)
        asserted = jres["found"]
        total_real = jres["total"]
        decoys_asserted = jres["false_positives"]
        for r in jres["rows"]:
            if r["defect"] in per:
                per[r["defect"]]["asserted"] = bool(r["found"])
                per[r["defect"]]["why"] = r.get("why")
    else:
        total_real = sum(1 for cid in key if not score_bench.is_herring(key[cid]))

    rec = {
        "dataset": dataset,
        "corpus": os.path.abspath(root),
        "corpus_files": len(cited["files"]),
        "report_chars": len(report),
        "verdict": verdict,
        "anchored": anchored, "anchorable": anchorable,
        "anchored_pct": (round(100.0 * anchored / anchorable, 1)
                         if anchorable else None),
        # BESIDE `anchored`, never merged with it and never summed into it.
        "presented": presented if parsed else None,
        "presentable": anchorable,
        "presented_pct": (round(100.0 * presented / anchorable, 1)
                          if (parsed and anchorable) else None),
        "dismissed": (dismissed if st["rejected_spans"] else None) if parsed else None,
        "anchored_outside_findings": outside if parsed else None,
        "asserted": asserted, "total_real": total_real,
        "asserted_pct": (round(100.0 * asserted / total_real, 1)
                         if (asserted is not None and total_real) else None),
        "decoys": decoys, "decoys_anchored": decoys_anchored,
        "decoys_asserted": decoys_asserted,
        # the free false-positive column: a decoy the report PRESENTED as one of
        # its own findings. `decoys_asserted` above stays the judged one and stays
        # None when no judge ran.
        "decoys_presented": decoys_presented if parsed else None,
        "decoys_dismissed": ((decoys_dismissed if st["rejected_spans"] else None)
                             if parsed else None),
        "unanchorable": unanchorable,
        "proof_files_absent_from_corpus": sorted(missing_proof_files),
        "ambiguous_citations": len(cited["ambiguous"]),
        "unresolved_citations": len(cited["unresolved"]),
        "capped_citations": cited["capped"],
        "citecheck": ccs,
        "citecheck_version": CITECHECK_VERSION,
        "citecheck_path": CITECHECK_PATH,
        "citecheck_sha": hashlib.sha1(
            open(CITECHECK_PATH, "rb").read()).hexdigest(),
        "structure": {k: v for k, v in st.items() if k != "sections"},
        "judged": call is not None,
        "judge_model": score_case.JUDGE_MODEL if call is not None else None,
        "judge_prompt_limit": JUDGE_PROMPT_LIMIT if call is not None else None,
        "per_defect": [per[cid] for cid in sorted(per)],
    }
    render(rec, cited)
    return rec


# --------------------------------------------------------------------------
# the human table
# --------------------------------------------------------------------------
def render(rec, cited):
    v = rec["verdict"]
    print("dataset   : %s" % rec["dataset"])
    print("corpus    : %s (%d files)" % (rec["corpus"], rec["corpus_files"]))
    print("report    : %d chars" % rec["report_chars"])
    if v["truth"]:
        mark = "✓" if v["correct"] else "✗"
        print("verdict   : truth=%s  reported=%s  %s"
              % (v["truth"], v["reported"], mark))
    else:
        print("verdict   : this key declares no verdict — NOT SCORED "
              "(reported=%s)" % v["reported"])
    print("            read from: %s" % v["read_from"])

    pct = "—" if rec["anchored_pct"] is None else "%.0f %%" % rec["anchored_pct"]
    print("anchored  : %d / %d real defects (%s)   ← judge-free, cites the proof"
          % (rec["anchored"], rec["anchorable"], pct))
    st = rec["structure"]
    if rec["presented"] is None:
        print("presented : — / %d               ← NOT MEASURED (structure "
              "unreadable; None, not 0)" % rec["presentable"])
        print("            %s" % st["why"])
    else:
        pp = ("—" if rec["presented_pct"] is None
              else "%.0f %%" % rec["presented_pct"])
        print("presented : %d / %d real defects (%s)   ← judge-free, cites the "
              "proof INSIDE «%s»"
              % (rec["presented"], rec["presentable"], pp,
                 st["findings_sections"][0][:40]))
        dm = "—" if rec["dismissed"] is None else str(rec["dismissed"])
        print("            of the anchored: %s dismissed (cited only under «%s»), "
              "%s cited outside both sections"
              % (dm, (st["rejected_sections"] or ["—"])[0][:34],
                 rec["anchored_outside_findings"]))
    if rec["asserted"] is None:
        print("asserted  : — / %s               ← NOT MEASURED (no judge ran; "
              "this is None, not 0)" % rec["total_real"])
    else:
        ap = "—" if rec["asserted_pct"] is None else "%.0f %%" % rec["asserted_pct"]
        print("asserted  : %d / %d real defects (%s)   ← judged by %s"
              % (rec["asserted"], rec["total_real"], ap, rec["judge_model"]))
    da = "—" if rec["decoys_asserted"] is None else str(rec["decoys_asserted"])
    dp = "—" if rec["decoys_presented"] is None else str(rec["decoys_presented"])
    dd = "—" if rec["decoys_dismissed"] is None else str(rec["decoys_dismissed"])
    print("decoys    : anchored %d / %d · presented %s / %d · dismissed %s · "
          "asserted %s / %d   ← false positives, never in the numerator above"
          % (rec["decoys_anchored"], rec["decoys"], dp, rec["decoys"], dd,
             da, rec["decoys"]))
    if st["rejections"] is not None:
        print("rejections: %d in «%s» — %d carry NO citation at all"
              % (st["rejections"], (st["rejected_sections"] or ["—"])[0][:34],
                 st["rejections_uncited"]))
    if st["coverage_rows"] is not None:
        print("            coverage rows %d — %d carry no citation (own count, "
              "never merged with the above)"
              % (st["coverage_rows"], st["coverage_rows_uncited"]))

    s = rec["citecheck"]
    vp = "—" if s.get("verified_pct") is None else "%.1f %%" % s["verified_pct"]
    print("citations : %d total — ok %d (%s), wrong-content %d, out-of-range %d, "
          "missing-file %d, no-quote %d, ambiguous %d, binary-file %d, "
          "unverifiable %d; не-ссылок %d"
          % (s.get("total", 0), s.get("ok", 0), vp, s.get("wrong-content", 0),
             s.get("out-of-range", 0), s.get("missing-file", 0),
             s.get("no-quote", 0), s.get("ambiguous", 0),
             s.get("binary-file", 0), s.get("unverifiable", 0),
             s.get("не-ссылка", 0)))

    # Everything below is a thing that was dropped, capped or could not be
    # scored. It prints even when it is zero-cost to ignore, because a cap the
    # reader cannot see is the same as no cap at all.
    if rec["ambiguous_citations"]:
        print("⚠ %d ambiguous citation(s) anchored NOTHING — the path matched more "
              "than one file, so it cannot be attributed to a host:"
              % rec["ambiguous_citations"])
        for a in cited["ambiguous"][:8]:
            print("    %s → %d candidates" % (a["citation"], a["candidates"]))
        if len(cited["ambiguous"]) > 8:
            print("    … and %d more" % (len(cited["ambiguous"]) - 8))
    if rec["unresolved_citations"]:
        print("⚠ %d citation(s) resolved to no file in this corpus: %s"
              % (rec["unresolved_citations"],
                 ", ".join(cited["unresolved"][:6])))
    for c in rec["capped_citations"]:
        print("⚠ SPAN CAPPED: %s asks to line %d, citecheck reads at most %d lines "
              "and stopped at %d. The rest was NOT checked and cannot anchor."
              % (c["citation"], c["asked_to"], citecheck.MAX_RANGE, c["read_to"]))
    if st.get("rejections_uncited"):
        print("⚠ %d rejection(s) без ссылки на строку — a disposal nobody can "
              "check is not a judgement:" % st["rejections_uncited"])
        for t in st["uncited_rejections"][:6]:
            print("    %s" % t[:120])
        if len(st["uncited_rejections"]) > 6:
            print("    … and %d more" % (len(st["uncited_rejections"]) - 6))
    if rec["unanchorable"]:
        print("⚠ %d real defect(s) carry no proof locations and left the anchoring "
              "denominator: %s"
              % (len(rec["unanchorable"]), ", ".join(rec["unanchorable"])))
    if rec["proof_files_absent_from_corpus"]:
        print("⚠ the key names %d proof file(s) that are NOT in this corpus — key "
              "and corpus may not match: %s"
              % (len(rec["proof_files_absent_from_corpus"]),
                 ", ".join(rec["proof_files_absent_from_corpus"][:6])))

    print()
    print("%-5s %-6s %-9s %-10s %-9s %s"
          % ("id", "kind", "anchored", "presented", "asserted", "title"))
    for r in rec["per_defect"]:
        kind = "DECOY" if r["herring"] else "real"
        if not r["anchorable"]:
            anc = "n/a"
        else:
            anc = "%s %d/%d" % ("✓" if r["anchored"] else "·",
                                r["anchor_hits"], r["proof_locations"])
        if r["presented"] is None:
            pre = "—"
        elif r["presented"]:
            pre = "✓ findings"
        elif r["dismissed"]:
            pre = "· rejected"
        elif r["anchored_zones"]:
            pre = "· " + ",".join(r["anchored_zones"])[:8]
        else:
            pre = "·"
        asr = "—" if r["asserted"] is None else ("✓" if r["asserted"] else "·")
        if r["herring"] and r["asserted"]:
            asr = "✗ FP"
        print("%-5s %-6s %-9s %-10s %-9s %s"
              % (r["defect"], kind, anc, pre, asr, r["title"][:52]))
    # the D09 shape, printed rather than judged: WHERE each anchor landed.
    shown = [r for r in rec["per_defect"] if r.get("anchored_headings")]
    if shown:
        print()
        print("where each anchor landed (a citation filed under the wrong finding "
              "is still inside «Находки» — no free axis can call that wrong, so it "
              "is printed):")
        for r in shown:
            print("    %-5s %s" % (r["defect"],
                                   " · ".join(h[:60] for h in r["anchored_headings"][:3])))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Score a report: verdict, anchored findings (free), asserted "
                    "findings (judged), citation integrity.")
    ap.add_argument("--key", required=True)
    ap.add_argument("--corpus", help="corpus root; defaults to the key's corpus_root")
    ap.add_argument("--report", help="a report FILE, or - for stdin")
    ap.add_argument("--ledger", help="take the report from this run ledger instead")
    ap.add_argument("--arm")
    ap.add_argument("--trace", help="substring of trace_dir — score THAT run")
    ap.add_argument("--dataset")
    ap.add_argument("--judge", action="store_true",
                    help="also fill the judged `asserted` column (needs "
                         "JUDGE_API_KEY; broker/subscription, never a metered API)")
    ap.add_argument("--min-overlap", type=float, default=0.34)
    ap.add_argument("--min-tokens", type=int, default=3)
    ap.add_argument("--require-quote", action="store_true")
    ap.add_argument("--label", help="free-text note recorded on the ledger row")
    ap.add_argument("--out", default=os.path.join(HERE, "scores-report.jsonl"))
    ap.add_argument("--json", action="store_true", help="also print the record")
    a = ap.parse_args()

    raw_key = json.load(open(a.key, encoding="utf-8"))
    root = a.corpus or raw_key.get("corpus_root")
    if not root:
        sys.exit("no --corpus and the key declares no corpus_root")
    if not os.path.isabs(root):
        # answer-key-fleet-negative.json stores a RELATIVE corpus_root while the
        # other two store absolute ones. Resolving it against cwd silently would
        # make the same command mean different things from different directories.
        cand = os.path.abspath(root)
        if not os.path.isdir(cand):
            sys.exit("the key's corpus_root %r is relative and does not resolve "
                     "from here (%s). Pass --corpus explicitly." % (root, os.getcwd()))
        print("note: key corpus_root %r is relative; resolved to %s" % (root, cand))
        root = cand

    if a.report and a.ledger:
        sys.exit("--report and --ledger are two different sources; pick one")
    if a.report:
        text = (sys.stdin.read() if a.report == "-"
                else open(a.report, encoding="utf-8", errors="replace").read())
        source = {"report_path": a.report, "arm": a.arm, "trace_dir": None,
                  "delivered_in": None}
    elif a.ledger:
        rows = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
        if not rows:
            sys.exit("no rows in %s" % a.ledger)
        run = score_bench.select_row(rows, a.arm, a.trace,
                                     a.dataset or raw_key.get("dataset"))
        score_bench.check_key_matches_dataset(raw_key, run)
        text = deliverable.of_row(run)
        source = {"report_path": None, "arm": run.get("arm"),
                  "trace_dir": run.get("trace_dir"),
                  "delivered_in": deliverable.channel_of_row(run)}
        if source["delivered_in"] == "file":
            print("⚠ DELIVERY: this run's report is in work/report.md, not in its "
                  "final message. The scores below measure the REPORT; delivery "
                  "is a separate, open defect.")
    else:
        sys.exit("give either --report or --ledger")

    call = None
    if a.judge:
        def call(prompt, _inner=score_case.http_call):
            """Retry the TRANSPORT, never the verdict — score-bench.py's rule.

            A dropped socket is not evidence about the report, so it is retried; a
            verdict that parses is used exactly once, because re-asking a judge
            until it agrees is how a score stops meaning anything.
            """
            last = None
            for attempt in range(1, 4):
                try:
                    return _inner(prompt)
                except Exception as e:                  # transport only
                    last = e
                    print("  ⚠ judge transport failed (attempt %d): %s"
                          % (attempt, str(e)[:120]))
                    time.sleep(2.0 * attempt)
            raise last

    rec = score(raw_key, text, root, call, a.min_overlap, a.min_tokens,
                a.require_quote)
    rec.update(source)
    rec["key"] = os.path.abspath(a.key)
    rec["label"] = a.label
    if a.json:
        print(json.dumps(rec, ensure_ascii=False, indent=1))
    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
