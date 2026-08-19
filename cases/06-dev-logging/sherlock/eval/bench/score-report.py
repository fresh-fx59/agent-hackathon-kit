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

    # the hand-over scored beside the artefact, outside a run ledger
    python3 score-report.py --key … --corpus … --report work/report.md \
        --delivered final-message.md

Everything this project measured before now scored a WORKLIST — Step 1's 250-row
attention budget. That is an upper bound on what the skill can find and says
nothing about what it told the reader. The deliverable is a report; this scores
one, on seven axes that are deliberately never summed into a single number.

    verdict    compromised / attacked-not-proven / clean, or `absent`
    anchored   did the report CITE this defect's proof?      ← free, primary
    presented  did it cite it INSIDE its findings section?   ← free, structural
    asserted   did the report CLAIM this defect?             ← judged, optional
    citations  do the cited lines say what the report says?  ← free, citecheck
    delivery   does the HAND-OVER say it too?                ← free, per channel
    outcomes   did the finding say the thing HAPPENED?       ← free, v24 field

WHY THE OUTCOME AXIS EXISTS, AND WHAT IT MEASURES THAT NOTHING ELSE COULD. Every
axis above reads WHERE a citation was written. None of them reads what the report
CONCLUDED, because until `skills/v24` no field carried that: «Улики» is the
evidence for a finding and «Чем опровергал» is the method, which every block
writes whatever the answer turned out to be. So «I checked this and it was
nothing» and «I found an intrusion» are written in exactly the same fields.
Measured across all nine arms on disk, the decoy split is 12 presented and 0
refuted — not one report had a field that could record a refutation.

v24 mandates one closed-vocabulary line per finding — `исход: успех|попытка|
норма` — and that is the join this file was missing:

    a decoy cited inside a finding marked успех/попытка   = A FALSE POSITIVE
    the same decoy cited inside a finding marked норма    = A REFUTATION
    a REAL defect cited inside a finding marked норма     = a miss dressed
                                                            as diligence

It is the project's first false-positive-rate axis, and it is free. It lives in a
NESTED `outcomes` block so that not one column an old ledger row published
changes name, place or value — measured: 3,667 baseline field values across the
nine arms, zero moved. On every one of those arms it reads NOT MEASURED with a
reason, because they all predate the field. That is the whole point: «0
refutations» is a claim about the report, «not measurable» is the truth about the
field, and scoring the first would silently rewrite eight historical scores.

WHY DELIVERY IS ITS OWN AXIS. The v22 negative control is the first arm whose
citation integrity fell below 100 % (89.4 %), and the cause was not the
investigation. Scored per channel: `work/report.md` alone is 110 / 110 ok; the
final message alone is 74 / 95 with 21 `wrong-content`, all inside a condensed
inventory the run hand-wrote AFTER checking the draft. The composed 198-at-89.4 %
is an average describing NEITHER document, and `CHANNELS DIVERGE` was a printed
warning nobody could put in a table. So each channel is scored on its own beside
the union, and the divergence is integers: shared blocks, blocks unique to each
side, and whether each divergent side's citations verify. The composed columns
are unchanged — axes are never summed and never silently replaced, because a
number that moves must be explainable to somebody reading an old ledger row.

WHAT THE DELIVERY AXIS DOES NOT DO, because it was measured and rejected. «The
delivered citations must be a subset of the verified ones» catches 1 of those 21:
20 were citations ALREADY in the verified set, re-typed under a new sentence. A
`(citation, claim)` pair test separates perfectly and raised 45 false alarms. The
mechanism that works is re-checking the delivered text against the corpus, and
both nets here are citecheck's own — `check()` and `not_in_checked()` — with
`citecheck.delivery_failed` as the pass/fail, the same predicate `citecheck.py
--delivered` exits non-zero on.

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

ONE REPORT ON TWO CHANNELS IS ONE REPORT. Every citation total this project
published was doubled: `measure/deliverable.py` CONCATENATED the final message
and `work/report.md`, and on all six arms on disk both channels carried the same
report. Published 294 / 268 / 212 / 314 / 316 / 396 against file-only
147 / 141 / 106 / 157 / 158 / 198. The RATE never moved — 316/316 and 158/158 are
both 100 % — which is why it survived every review of this file. The fix is in
`deliverable.py` (2026-08-19), where the union became a union, block by block;
this scorer keeps importing it rather than de-duplicating locally. Where the two
channels genuinely disagree (AIT v16-contaminated answered a condensed rewrite
beside its file) BOTH are scored and `duplication` on the record says so — a
silent pick between disagreeing channels is the same class of error as counting
them twice.

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
CITECHECK_SHA = hashlib.sha1(open(CITECHECK_PATH, "rb").read()).hexdigest()
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

# --------------------------------------------------------------------------
# WHERE INSIDE A FINDING — «Улики» vs «Чем опровергал», the split that separates
# a false positive from an investigation.
# --------------------------------------------------------------------------
# `presented` says a decoy's proof sits inside «Находки». It cannot say whether
# the report entered it as an INCIDENT or named it and refuted it in the same
# breath, and those are different failures — the field benchmark this project
# measures against (OpenSec: 100 % containment, 92.5 % false-positive rate)
# counts only the first. A red herring that is silently ignored is also
# indistinguishable from one that was never seen, so «named and refuted» may be
# the correct behaviour rather than a defect.
#
# `reference/report-format.md` is byte-identical across v16, v19 and v22 and
# mandates NO per-finding outcome field — so there is nothing cheaper to read.
# What it does mandate is a fixed field ORDER inside every finding block, and two
# of those fields have opposite polarity:
#
#     «улики»          — the evidence FOR this finding
#     «чем опровергал» — the check that would have KILLED it, and what it returned
#
# So the split is read one level below the section parse above: which mandated
# field is this citation under. Parsing, not judgement, and it costs nothing.
#
# The asymmetry is deliberate. REFUTED is the narrow half and must be earned: the
# citation has to sit under «Чем опровергал». Everything else inside «Находки» —
# «Улики», any other mandated field, or no label at all — counts as ASSERTED,
# because a citation inside a finding block that is not in the refutation field
# is evidence for that finding. Erring the other way would let an unparsed block
# launder a false positive into a refutation.
FIELD_LABEL_RE = re.compile(
    r"^[ \t]{0,3}(?:[-*+][ \t]+)?\*{0,2}[ \t]*"
    r"(улики|чем опроверг\w*|что сломано|корневая причина|что делать сейчас|"
    r"правка)\b", re.I)
FIELD_KIND = {"улики": "evidence"}


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
    # De-duplicated BY POSITION, not by text. A section owns its subsections, so
    # a «Отклонённые кандидаты» heading nested under another one hands the same
    # report line to this function twice; counting it twice would be a fact about
    # the parse. Position is exact — and unlike the text-level de-duplication this
    # replaced, it does not quietly merge two rows a report really did write twice.
    #
    # That text-level rule existed because `measure/deliverable.py` CONCATENATED
    # the final message and work/report.md, so every row arrived twice. It is now
    # a union (2026-08-19), the duplicate never reaches here, and the six arms
    # re-score identically without it — so the second copy of the rule goes.
    seen, rows = set(), []
    for i, ls in out:
        if i in seen:
            continue
        seen.add(i)
        rows.append((i, "\n".join(ls)))
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
          "finding_ids": [], "sections": secs,
          "fields_parsed": False, "why_fields": None, "field_labels": 0,
          "field_spans": []}
    if not finds:
        st["why"] = ("no findings section: none of the %d heading(s) matches "
                     "/находк|finding/ — saw %s. The structural assertion axis "
                     "cannot be read off this report and is None, NOT 0."
                     % (len(secs), ", ".join(repr(s["title"][:40])
                                             for s in secs[:8]) or "no headings"))
        return st
    st["parsed"] = True

    # --- the per-finding field parse, one level below the section parse ---
    spans = fields_of(report, secs)
    st["field_spans"] = spans
    st["field_labels"] = len(spans)

    def _in_findings(lo):
        return any(a <= lo < b for (a, b) in st["findings_spans"])

    kinds = {k for (lo, _hi, k) in spans if _in_findings(lo)}
    if "evidence" in kinds and "refutation" in kinds:
        st["fields_parsed"] = True
    else:
        missing = [n for n, k in (("«улики»", "evidence"),
                                  ("«чем опровергал»", "refutation"))
                   if k not in kinds]
        st["why_fields"] = (
            "the findings section carries no %s field label (%d mandated label(s) "
            "found in the whole report). `report-format.md` mandates both, and "
            "without the refutation field a decoy cited under a finding cannot be "
            "told apart from one the report named and killed. The split is None, "
            "NOT 0 — scoring 0 refutations here would be a claim about the report "
            "instead of about the parse."
            % (" and no ".join(missing), len(spans)))

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


def fields_of(report, secs):
    """[(lo, hi, kind)] — every mandated per-finding field label, SCOPED.

    A field runs from its label line to the next label line or to the end of the
    heading section that owns it, whichever comes first. The scoping is
    load-bearing: without it «Что делать сейчас», the last label of the first
    finding, claims every citation in every later section of the report — the
    verdict included — and the split becomes a fact about the parse.

    Labels inside a fenced block are sample text, exactly as for headings.
    """
    lines = report.splitlines()
    fence, marks = None, []
    for i, line in enumerate(lines, 1):
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence else m.group(1)
            continue
        if fence:
            continue
        f = FIELD_LABEL_RE.match(line)
        if f:
            name = f.group(1).lower()
            kind = ("refutation" if name.startswith("чем опроверг")
                    else FIELD_KIND.get(name, "other"))
            marks.append((i, kind))
    out = []
    for j, (i, kind) in enumerate(marks):
        owner = None
        for sec in secs:
            if sec["body_from"] <= i < sec["body_to"]:
                if owner is None or sec["level"] >= owner["level"]:
                    owner = sec
        end = owner["body_to"] if owner else len(lines) + 1
        if j + 1 < len(marks):
            end = min(end, marks[j + 1][0])
        out.append((i, end, kind))
    return out


def field_of(st, lineno):
    """evidence | refutation | other — which mandated field owns this line."""
    for (lo, hi, kind) in st.get("field_spans") or []:
        if lo <= lineno < hi:
            return kind
    return None


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
    """-> (zones, headings, fields, linenos) for every citation of this defect's
    proof.

    Same overlap arithmetic as `anchor_hits`, carrying WHERE in the report the
    citation was written. A defect can be both presented and dismissed — a report
    that names it as a finding and also argues a piece of it away — and both are
    recorded rather than one overwriting the other.

    `linenos` are the report lines those citations were written on. The outcome
    axis below needs them because its unit is one level lower again than the
    field parse: not the section and not the mandated field, but which `Н-n`
    BLOCK the citation landed in, and what that block said the outcome was.
    """
    zones, heads, fields, linenos = set(), [], set(), []
    for (f, plo, phi) in proofs:
        for c in placed:
            if c["rel"] != f:
                continue
            if not _overlaps((c["rel"], c["lo"], c["hi"]), plo, phi):
                continue
            z = zone_of(st, c["lineno"])
            if z:
                zones.add(z)
            if z == "findings":
                fields.add(field_of(st, c["lineno"]))
            h = heading_of(st, c["lineno"])
            if h and h not in heads:
                heads.append(h)
            if c["lineno"] not in linenos:
                linenos.append(c["lineno"])
    return sorted(zones), heads, fields, sorted(linenos)


def disposition_of(zones, fields, anchored, st):
    """asserted | refuted | dismissed | elsewhere | None — what the report DID
    with this defect, read off its own structure.

    None means unreadable, never «nothing»: the defect was not anchored at all,
    or the report wrote no mandated field labels to read the polarity from.
    """
    if not anchored:
        return None
    if "findings" in zones:
        if not st.get("fields_parsed"):
            return None
        return "refuted" if fields == {"refutation"} else "asserted"
    if "rejected" in zones:
        return "dismissed"
    return "elsewhere"


# --------------------------------------------------------------------------
# axis 6: THE OUTCOME — what the report said HAPPENED, joined to the anchoring
# --------------------------------------------------------------------------
# Every axis above reads WHERE a citation was written. None of them can read
# what the report concluded about the thing it points at, because until v24 no
# field carried that. «Улики» is the evidence FOR a finding and «Чем опровергал»
# is the method — which every block writes whatever the answer turned out to be
# — so a block that means «I checked this and it was nothing» is written in
# exactly the same fields as one that means «I found an intrusion», and the
# difference lived only in prose. That is why, measured across all nine arms on
# disk, the decoy split is 12 presented and 0 refuted: not one report had a
# field that could record a refutation, so every presented decoy landed under
# «Улики».
#
# `skills/v24` mandates a closed-vocabulary outcome line inside every `Н-n`
# block, and the vocabulary is the verdict's own three answers one level down:
#
#     исход: успех     — действие достигло цели            (compromised)
#     исход: попытка   — видно, и видно, что цели не достигло (attacked-not-proven)
#     исход: норма     — проверено, объяснено штатным поведением (clean)
#
# So the join this file has been missing is one line of arithmetic:
#
#     a decoy cited inside a finding marked успех/попытка  = A FALSE POSITIVE
#     the same decoy cited inside a finding marked норма   = A REFUTATION
#     a REAL defect cited inside a finding marked норма    = a miss dressed as
#                                                            diligence
#
# THE GRAMMAR IS NOT REIMPLEMENTED HERE. `citecheck.finding_blocks`,
# `finding_outcomes`, `implied_verdict` and `stated_verdict` own it — including
# the rule that a trailing qualifier («успех, но не доказан») is refused
# SEPARATELY from a forgotten line, because a fourth state creeping back through
# the tail of a sentence and a forgotten field are different defects and a skill
# has to be told which one it has. A second copy of that grammar would be a
# second, incomparable scale. This file joins and grades.
#
# THE STRONGEST OUTCOME WINS when one defect is cited from several blocks — the
# same asymmetry the field split already uses. A decoy cited under a «норма»
# finding AND under a «успех» one was still entered as evidence for something
# that happened; erring the other way lets an unlabelled or refuted block
# launder a false positive.
#
# FAIL LOUD ON A REPORT THAT PREDATES THE FIELD. Every report this project has
# on disk was written before v24, so every block is `missing`. The axis returns
# None with a reason and NEVER a zero: «0 refutations» is a claim about the
# report, «not measurable» is the truth about the field, and scoring the first
# would silently rewrite eight historical scores.
OUTCOME_INCIDENT = ("успех", "попытка")
OUTCOME_ZONE = {"успех": "incident", "попытка": "incident", "норма": "normal"}
NO_OUTCOME_API = (
    "citecheck %s has no `finding_outcomes`: the per-finding outcome line is a "
    "v24 field and this checker predates it, so the false-positive / refutation "
    "split IS NOT MEASURABLE and every column of it is None, NOT 0.")


def outcome_structure_of(report):
    """The v24 outcome parse of ONE document -> {available, why, blocks, ...}.

    `blocks` is [(line_lo, line_hi, outcome_or_None, finding_id)], index-aligned
    with citecheck's own block list. Everything else is the health of the outcome
    lines themselves: how many blocks stated one, which forgot it, which argued
    with the vocabulary, and whether the register adds up to the stated verdict.
    """
    out = {"available": False, "why": None, "blocks": [],
           "finding_blocks": None, "outcomes_stated": None,
           "outcome_missing": None, "outcome_missing_findings": [],
           "outcome_invalid": None, "outcome_invalid_findings": [],
           "implied_verdict": None, "stated_verdict": None,
           "contradiction": None,
           "vocabulary": None, "grammar": None}
    blocks_fn = getattr(citecheck, "finding_blocks", None)
    outcomes_fn = getattr(citecheck, "finding_outcomes", None)
    if blocks_fn is None or outcomes_fn is None:
        out["why"] = NO_OUTCOME_API % CITECHECK_VERSION
        return out
    out["vocabulary"] = list(citecheck.OUTCOME_ORDER)
    out["grammar"] = "исход: " + "|".join(citecheck.OUTCOME_ORDER)
    bounds = blocks_fn(report)
    rows = outcomes_fn(report)
    if len(bounds) != len(rows):
        # FAIL LOUD rather than zip-shortest: the two lists are the same walk
        # over the same blocks in citecheck, and if that ever stops being true
        # every outcome would be attributed to the wrong finding.
        raise RuntimeError(
            "citecheck %s returned %d finding block(s) and %d outcome row(s) — "
            "these must be the same walk over the same blocks, and joining them "
            "positionally would attribute outcomes to the wrong findings."
            % (CITECHECK_VERSION, len(bounds), len(rows)))
    if not bounds:
        out["why"] = ("no `Н-n` finding block in this report: the outcome is a "
                      "per-finding field and there is no finding to read it "
                      "from. Every outcome column is None, NOT 0.")
        return out
    out["blocks"] = [(lo, hi, r["outcome"], num)
                     for (num, lo, hi), r in zip(bounds, rows)]
    out["finding_blocks"] = len(bounds)
    out["outcomes_stated"] = sum(1 for r in rows if r["outcome"])
    out["outcome_missing_findings"] = [r["finding"] for r in rows
                                       if not r["outcome"] and not r["bad"]]
    out["outcome_missing"] = len(out["outcome_missing_findings"])
    out["outcome_invalid_findings"] = [
        {"finding": r["finding"], "text": r["bad"],
         "report_line": r["report_line"]}
        for r in rows if not r["outcome"] and r["bad"]]
    out["outcome_invalid"] = len(out["outcome_invalid_findings"])
    out["implied_verdict"] = citecheck.implied_verdict(report)
    out["stated_verdict"] = citecheck.stated_verdict(report)
    # None, not False, when either side is silent. A verdict cannot contradict a
    # register that states nothing — that is a missing field, and it is already
    # counted as one. `citecheck.outcomes_of` folds this to False because it
    # gates a skill's own self-check; a SCORE has to keep the two apart.
    if out["implied_verdict"] and out["stated_verdict"]:
        out["contradiction"] = (out["implied_verdict"] != out["stated_verdict"])
    if not out["outcomes_stated"]:
        out["why"] = (
            "no finding block states an «исход:» line — %d of %d block(s) are "
            "missing it. This report predates the per-finding outcome field "
            "(skills/v24), so the false-positive / refutation split IS NOT "
            "MEASURABLE on it and every column of it is None, NOT 0. A zero "
            "here would read as «this report refuted nothing», which is a claim "
            "about the report instead of about the field."
            % (out["outcome_missing"] + out["outcome_invalid"],
               out["finding_blocks"]))
        return out
    out["available"] = True
    return out


def outcome_zone_of(oc, st, linenos, zones, anchored):
    """-> (zone, outcome_token, finding_id) for one defect.

    zone is incident | normal | unlabelled | dismissed | elsewhere | None.
    None means the report never anchored this defect at all — never «the report
    said nothing about it», which is what a zero would say.
    """
    if not anchored:
        return None, None, None
    seen = []
    for ln in linenos:
        if zone_of(st, ln) != "findings":
            continue
        for (lo, hi, tok, num) in oc["blocks"]:
            if lo <= ln <= hi:
                seen.append((tok, num))
                break
        else:
            # inside «Находки» but owned by no `Н-n` block — a preamble, a
            # summary table. Presented, and the outcome is unreadable.
            seen.append((None, None))
    if seen:
        named = [(t, n) for (t, n) in seen if t]
        if not named:
            return "unlabelled", None, None
        tok, num = max(named, key=lambda tn: citecheck.OUTCOME_ORDER.index(tn[0]))
        return OUTCOME_ZONE[tok], tok, num
    if "rejected" in zones:
        return "dismissed", None, None
    return "elsewhere", None, None


OUTCOME_BUCKETS = ("incident", "normal", "unlabelled", "dismissed", "elsewhere",
                   None)
DECOY_BUCKET_FIELD = {"incident": "decoys_false_positive",
                      "normal": "decoys_refutation",
                      "unlabelled": "decoys_unlabelled",
                      "dismissed": "decoys_dismissed",
                      "elsewhere": "decoys_elsewhere",
                      None: "decoys_not_anchored"}
REAL_BUCKET_FIELD = {"incident": "real_asserted_as_incident",
                     "normal": "real_marked_normal",
                     "unlabelled": "real_unlabelled",
                     "dismissed": "real_dismissed",
                     "elsewhere": "real_elsewhere",
                     None: "real_not_anchored"}


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


def outcomes_record(oc, measured, parsed, st, decoys, real_anchorable, buckets):
    """The whole outcome axis for ONE document, health and join together.

    HEALTH is a fact about the document and is read whenever the report has
    `Н-n` blocks at all — including on every pre-v24 report, where it is exactly
    the evidence that the axis did not exist yet. A block that forgot its
    outcome line is a delivery defect in the same way a missing verdict section
    is, so it is counted, not inferred.

    THE JOIN is None — never 0 — unless some block actually stated an outcome
    AND the report has a readable findings section. Both halves are needed and
    the reason says which one is missing.
    """
    rec = {"measured": measured, "why": None,
           "vocabulary": oc["vocabulary"], "grammar": oc["grammar"],
           "citecheck_version": CITECHECK_VERSION,
           "finding_blocks": oc["finding_blocks"],
           "outcomes_stated": oc["outcomes_stated"],
           "outcome_missing": oc["outcome_missing"],
           "outcome_missing_findings": oc["outcome_missing_findings"],
           "outcome_invalid": oc["outcome_invalid"],
           "outcome_invalid_findings": oc["outcome_invalid_findings"],
           "implied_verdict": oc["implied_verdict"],
           "stated_verdict": oc["stated_verdict"],
           "contradiction": oc["contradiction"],
           "decoys": decoys, "real_anchorable": real_anchorable}
    for bucket, field in DECOY_BUCKET_FIELD.items():
        rec[field] = buckets["decoy"][bucket] if measured else None
    for bucket, field in REAL_BUCKET_FIELD.items():
        rec[field] = buckets["real"][bucket] if measured else None
    if not measured:
        rec["why"] = oc["why"] or (
            "the findings section is unreadable, so a citation cannot be placed "
            "inside or outside a finding at all: %s The outcome split is None, "
            "NOT 0." % (st.get("why") or ""))
    return rec


# --------------------------------------------------------------------------
# the free findings columns, for ONE document
# --------------------------------------------------------------------------
def findings_of(key, report, root):
    """One document + one key + one corpus -> every judge-free findings column.

    Lifted out of `score()` unchanged so the same arithmetic can be run over one
    CHANNEL as well as over the union. `delivery_axis` grades the hand-over and
    the checked artefact with exactly this function, which is why a per-channel
    `anchored` means what the composed one means. A second copy of this loop is a
    second scale — the defect this project has already shipped twice.
    """
    cited = cited_spans(report, root)
    spans = cited["spans"]

    st = structure_of(report, cited["by_rel"], cited["by_base"])
    parsed = st["parsed"]
    oc = outcome_structure_of(report)
    # The join needs BOTH parses: which `Н-n` block owns the citation (v24's) and
    # whether that line is inside «Находки» at all (this file's). One without the
    # other is not a measurement.
    outcome_measured = bool(oc["available"] and parsed)

    per = {}
    unanchorable, missing_proof_files = [], set()
    anchored = anchorable = decoys_anchored = decoys = 0
    presented = dismissed = outside = 0
    decoys_presented = decoys_dismissed = 0
    decoys_asserted_incident = decoys_refuted = decoys_elsewhere = 0
    obuck = {"decoy": dict.fromkeys(OUTCOME_BUCKETS, 0),
             "real": dict.fromkeys(OUTCOME_BUCKETS, 0)}
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
        zones, heads, fields, linenos = (
            anchor_zones(cited["placed"], proofs, st)
            if (proofs and parsed) else ([], [], set(), []))
        disp = (disposition_of(zones, fields, bool(proofs) and hits > 0, st)
                if parsed else None)
        is_presented = ("findings" in zones) if parsed else None
        is_dismissed = (bool(zones) and "findings" not in zones
                        and "rejected" in zones) if parsed else None
        ozone, otok, ofind = (
            outcome_zone_of(oc, st, linenos, zones, is_anchored)
            if outcome_measured else (None, None, None))
        if outcome_measured:
            obuck["decoy" if herring else "real"][ozone] += 1
        per[cid] = {"defect": cid, "herring": herring,
                    "title": d.get("title", ""),
                    "proof_locations": len(proofs) - n_alt,
                    "alternate_proof_locations": n_alt,
                    "anchorable": bool(proofs),
                    "anchored": is_anchored, "anchor_hits": hits,
                    "presented": is_presented, "dismissed": is_dismissed,
                    "anchored_zones": zones, "anchored_headings": heads,
                    "anchored_fields": sorted(f for f in fields if f),
                    "disposition": disp,
                    # axis 6, beside `disposition` and never merged with it: the
                    # field parse reads which mandated field a citation sat
                    # under, this reads what the finding said HAPPENED. On a
                    # «норма» finding whose evidence field carries a decoy the
                    # two disagree, and both readings are true.
                    "outcome": otok, "outcome_finding": ofind,
                    "outcome_zone": ozone,
                    "asserted": None, "why": None}
        if herring:
            decoys += 1
            decoys_anchored += 1 if is_anchored else 0
            if parsed:
                decoys_presented += 1 if is_presented else 0
                decoys_dismissed += 1 if is_dismissed else 0
                decoys_asserted_incident += 1 if disp == "asserted" else 0
                decoys_refuted += 1 if disp == "refuted" else 0
                decoys_elsewhere += 1 if disp == "elsewhere" else 0
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

    return {"cited": cited, "structure": st, "per": per,
            "outcomes": outcomes_record(oc, outcome_measured, parsed, st,
                                        decoys, anchorable, obuck),
            "unanchorable": unanchorable,
            "missing_proof_files": missing_proof_files,
            "anchored": anchored, "anchorable": anchorable,
            "presented": presented, "dismissed": dismissed, "outside": outside,
            "decoys": decoys, "decoys_anchored": decoys_anchored,
            "decoys_presented": decoys_presented,
            "decoys_dismissed": decoys_dismissed,
            "decoys_asserted_as_incident": decoys_asserted_incident,
            "decoys_presented_refuted": decoys_refuted,
            "decoys_anchored_elsewhere": decoys_elsewhere}


# --------------------------------------------------------------------------
# axis 5: DELIVERY INTEGRITY — the hand-over, scored beside the artefact
# --------------------------------------------------------------------------
# The v22 negative control is the first arm whose citation integrity fell below
# 100 %, and the cause was not the investigation. Scored per channel:
# `work/report.md` alone is 110 / 110 ok; the final message alone is 74 / 95 with
# 21 `wrong-content`, all inside a condensed inventory the run hand-wrote AFTER
# checking the draft. The composed number — 198 citations, 89.4 % — is an average
# that describes NEITHER document, and `CHANNELS DIVERGE` was a printed warning
# nobody could put in a table.
#
# So the channels are scored separately as well as together, the divergence
# becomes record fields (shared blocks, blocks unique to each side, and whether
# each divergent side's citations verify), and the composed columns stay exactly
# where they were: axes are never summed and never silently replaced, because a
# number that moves has to be explainable to somebody reading an old ledger row.
#
# WHAT WAS ALREADY MEASURED AND REJECTED, so nobody rebuilds it. «The delivered
# citations must be a subset of the verified ones» catches 1 of those 21, because
# 20 were citations ALREADY in the verified set, re-typed under a new sentence. A
# `(citation, claim)` pair test separates perfectly and raised 45 false alarms.
# The mechanism that works is re-checking the delivered text against the corpus.
# Both nets here are citecheck's own — `check()` and `not_in_checked()` — and the
# pass/fail is `citecheck.delivery_failed`, the same predicate `citecheck.py
# --delivered` exits non-zero on. This file grades; it does not re-decide.
NOT_DELIVERABLE = (
    "scored from a single document, so there is no hand-over to compare against "
    "the checked artefact. Delivery integrity is measurable from a run ledger "
    "(--ledger), or from --report plus --delivered.")

DELIVERY_KEYS = ("channel", "relation", "diverged", "blocks", "channels",
                 "divergent_sides", "divergent_side_verifies", "handover",
                 "checked", "handover_verified", "handover_verified_pct",
                 "handover_not_in_checked", "handover_not_in_checked_examples",
                 "handover_failed", "warning")

CHANNEL_FILE = {"message": "the final message", "file": "work/report.md"}


def _no_delivery(why):
    """The unmeasured shape — every field None, never 0, and it says why."""
    rec = {k: None for k in DELIVERY_KEYS}
    rec.update({"measured": False, "why": why, "channels": {},
                "divergent_sides": None,
                "citecheck_version": CITECHECK_VERSION,
                "citecheck_path": CITECHECK_PATH,
                "citecheck_sha": CITECHECK_SHA})
    return rec


def _channel_score(key, text, root, unique_blocks, min_overlap, min_tokens,
                   require_quote):
    """One channel, graded on its own -> (record, the raw citecheck result).

    Same key, same corpus, same citecheck, same `findings_of` as the union: the
    only thing that differs is the document, which is the whole point.
    """
    cc = citecheck.check(text, root, min_overlap, min_tokens, require_quote)
    s = dict(cc["summary"])
    f = findings_of(key, text, root)
    parsed = f["structure"]["parsed"]
    total = s.get("total", 0)
    rec = {
        "chars": len(text),
        "blocks": len(deliverable.blocks(text)),
        "unique_blocks": unique_blocks,
        "citations": s,
        "verified_pct": s.get("verified_pct"),
        # None — never False — when the document cites nothing at all. A hand-over
        # with no citations has not failed a check; it has skipped one, and those
        # are different facts about a delivery.
        "verified": (s.get("ok", 0) == total) if total else None,
        "anchored": f["anchored"], "anchorable": f["anchorable"],
        "anchored_pct": (round(100.0 * f["anchored"] / f["anchorable"], 1)
                         if f["anchorable"] else None),
        "presented": f["presented"] if parsed else None,
        "presentable": f["anchorable"],
        "presented_pct": (round(100.0 * f["presented"] / f["anchorable"], 1)
                          if (parsed and f["anchorable"]) else None),
        "decoys": f["decoys"], "decoys_anchored": f["decoys_anchored"],
        "decoys_presented": f["decoys_presented"] if parsed else None,
        # A missing outcome line is a delivery defect exactly as a missing
        # verdict section is, and the two channels can disagree about it: a
        # condensed hand-over that drops the register keeps every citation and
        # loses every outcome, and the composed record cannot see that.
        "outcomes": f["outcomes"],
        "rejections": f["structure"]["rejections"],
        "rejections_uncited": f["structure"]["rejections_uncited"],
        "coverage_rows": f["structure"]["coverage_rows"],
        "coverage_rows_uncited": f["structure"]["coverage_rows_uncited"],
        "structure_parsed": parsed,
    }
    return rec, cc


def delivery_axis(key, answer, artifact, root, min_overlap=0.34, min_tokens=3,
                  require_quote=False):
    """The two channels, scored apart, with the divergence as an integer.

    `deliverable` owns what a channel IS and what the relation between two of
    them is; this reads both and grades. Nothing about block identity, the union
    or the `divergent` relation is decided here.
    """
    parts = deliverable.channels(answer, artifact)
    if not parts:
        return _no_delivery(NOT_DELIVERABLE)
    dup = deliverable.duplication(answer, artifact)
    uniq = {"message": dup["only_in_message"], "file": dup["only_in_file"]}
    recs, checks = {}, {}
    for name, text in parts.items():
        recs[name], checks[name] = _channel_score(
            key, text, root, uniq[name], min_overlap, min_tokens, require_quote)

    # The hand-over is what the reader received. The final message is it when
    # there is one — the collapsed 101-char run is why `channel()` exists — and
    # `work/report.md` is a DRAFT the user never sees unless it is all there is.
    handover = "message" if "message" in parts else "file"
    checked = "file" if (handover == "message" and "file" in parts) else None

    nic, examples, why = None, None, None
    if checked is not None:
        fn = getattr(citecheck, "not_in_checked", None)
        if fn is None:
            why = ("citecheck %s has no `not_in_checked`: the second net — a "
                   "delivered citation that was never in the checked verified "
                   "set — is NOT MEASURED here, and is None rather than 0."
                   % CITECHECK_VERSION)
        else:
            rows = fn(checks[checked], checks[handover])
            nic, examples = len(rows), rows[:6]

    failed_fn = getattr(citecheck, "delivery_failed", None)
    if failed_fn is None:
        failed = None
        why = ("citecheck %s has no `delivery_failed`: the pass/fail this scorer "
               "refuses to re-derive is NOT MEASURED." % CITECHECK_VERSION)
    else:
        failed = bool(failed_fn({"summary": recs[handover]["citations"],
                                 "not_in_checked": [None] * (nic or 0)}))

    diverged = dup["relation"] == "divergent"
    sides = ["message", "file"] if diverged else []
    if not sides:
        dsv = None
    else:
        verdicts = [recs[n]["verified"] for n in sides]
        dsv = None if any(v is None for v in verdicts) else all(verdicts)

    return {
        "measured": True,
        "why": why,
        "channel": deliverable.channel(answer, artifact),
        "relation": dup["relation"],
        "diverged": diverged,
        "blocks": {"message": dup["message_blocks"], "file": dup["file_blocks"],
                   "shared": dup["shared_blocks"],
                   "only_in_message": dup["only_in_message"],
                   "only_in_file": dup["only_in_file"]},
        "channels": recs,
        "divergent_sides": sides,
        "divergent_side_verifies": dsv,
        "handover": handover,
        "checked": checked,
        "handover_verified": recs[handover]["verified"],
        "handover_verified_pct": recs[handover]["verified_pct"],
        "handover_not_in_checked": nic,
        "handover_not_in_checked_examples": examples,
        "handover_failed": failed,
        "warning": dup["warning"],
        "citecheck_version": CITECHECK_VERSION,
        "citecheck_path": CITECHECK_PATH,
        "citecheck_sha": CITECHECK_SHA,
    }


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------
def score(raw_key, report, root, call=None, min_overlap=0.34, min_tokens=3,
          require_quote=False, answer=None, artifact=None):
    """One report + one key + one corpus -> one record. `call` is the judge, or None.

    `answer` and `artifact` are the two DELIVERY CHANNELS, when the caller has
    them: the final message and `work/report.md`. `report` stays the union of the
    two — every composed column keeps meaning exactly what it meant on every
    ledger row already written — and axis 5 grades each channel on its own beside
    it. Pass neither and the delivery axis records that it was not measurable.
    """
    if not os.path.isdir(root):
        raise RuntimeError("no such corpus directory: %s" % root)
    if not (report or "").strip():
        raise RuntimeError("the report is empty — nothing to score. An empty "
                           "deliverable is a delivery defect, and recording it as "
                           "0-of-N findings would make it look like a bad "
                           "investigation instead of an absent one.")
    if answer is not None or artifact is not None:
        # FAIL LOUD. A delivery block that describes two channels, printed beside
        # a composed score taken from some third document, is a record that lies
        # quietly — and the union is `deliverable`'s to define, not this file's.
        if deliverable.compose(answer, artifact) != report:
            raise RuntimeError(
                "the report being scored is not the union of the two channels "
                "handed in: len(report)=%d, len(compose(answer, artifact))=%d. "
                "Score `deliverable.compose(answer, artifact)`, or pass no "
                "channels at all."
                % (len(report), len(deliverable.compose(answer, artifact))))

    key = score_bench.load_key(raw_key)
    dataset = raw_key.get("dataset") if isinstance(raw_key, dict) else None

    # --- axis 1: the verdict, delegated whole -----------------------------
    truth = raw_key.get("verdict") if isinstance(raw_key, dict) else None
    got, how = score_verdict.extract(report)
    verdict = {"truth": truth, "reported": got, "read_from": how,
               "correct": (got == truth) if truth else None}

    # --- axes 2 and 2b: anchoring and presentation, free ------------------
    f = findings_of(key, report, root)
    cited, st, per = f["cited"], f["structure"], f["per"]
    parsed = st["parsed"]
    unanchorable, missing_proof_files = f["unanchorable"], f["missing_proof_files"]
    anchored, anchorable = f["anchored"], f["anchorable"]
    presented, dismissed, outside = f["presented"], f["dismissed"], f["outside"]
    decoys, decoys_anchored = f["decoys"], f["decoys_anchored"]
    decoys_presented, decoys_dismissed = (f["decoys_presented"],
                                          f["decoys_dismissed"])
    decoys_asserted_incident = f["decoys_asserted_as_incident"]
    decoys_refuted = f["decoys_presented_refuted"]
    decoys_elsewhere = f["decoys_anchored_elsewhere"]

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

    # --- axis 5: delivery integrity, free ---------------------------------
    delivery = delivery_axis(key, answer, artifact, root, min_overlap,
                             min_tokens, require_quote)

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
        # THE SPLIT OF `decoys_presented`, never a replacement for it. A decoy
        # cited under «Улики» of a finding was entered in the findings register
        # as an incident — the failure OpenSec's 92.5 % measures. A decoy cited
        # only under the mandated «Чем опровергал» field was named and killed in
        # the same breath, which is not that failure. Both are None — never 0 —
        # when the report writes no mandated field labels to read.
        "decoys_asserted_as_incident": (decoys_asserted_incident
                                        if (parsed and st["fields_parsed"])
                                        else None),
        "decoys_presented_refuted": (decoys_refuted
                                     if (parsed and st["fields_parsed"])
                                     else None),
        # The third outcome, which is neither half and is the GOOD one: the
        # report anchored the decoy somewhere that claims nothing — a verdict, a
        # coverage row, a register — and never filed it as a finding. The
        # negative control's D03 (our own evidence collector) is this shape.
        "decoys_anchored_elsewhere": decoys_elsewhere if parsed else None,
        # AXIS 6, a nested block of its own so that not one column an old ledger
        # row published changes name, place or value. It is the first axis that
        # can tell a false positive from a refutation: the SAME citation of the
        # SAME planted non-defect is one or the other depending on whether the
        # finding that carries it says «успех»/«попытка» or «норма». Everything
        # in it is None — never 0 — on a report written before v24 mandated the
        # field, which is every report this project has on disk.
        "outcomes": f["outcomes"],
        # `rejections_uncited` is rising as the skill closes more rows by rule
        # (AIT v16 0/7 → v19 2/11 → v22 6/10) and the v22 Linux arm's sharpest
        # loss sits in that gap. Top-level, beside `anchored`, not buried in
        # `structure` where a reader has to go looking.
        "rejections": st["rejections"],
        "rejections_uncited": st["rejections_uncited"],
        "coverage_rows": st["coverage_rows"],
        "coverage_rows_uncited": st["coverage_rows_uncited"],
        "unanchorable": unanchorable,
        "proof_files_absent_from_corpus": sorted(missing_proof_files),
        "ambiguous_citations": len(cited["ambiguous"]),
        "unresolved_citations": len(cited["unresolved"]),
        "capped_citations": cited["capped"],
        "citecheck": ccs,
        "citecheck_version": CITECHECK_VERSION,
        "citecheck_path": CITECHECK_PATH,
        "citecheck_sha": CITECHECK_SHA,
        "structure": {k: v for k, v in st.items() if k != "sections"},
        "judged": call is not None,
        "judge_model": score_case.JUDGE_MODEL if call is not None else None,
        "judge_prompt_limit": JUDGE_PROMPT_LIMIT if call is not None else None,
        "per_defect": [per[cid] for cid in sorted(per)],
        # BESIDE every column above, never merged into one and never replacing
        # one. `citecheck` above is the UNION's integrity; this is each channel's.
        "delivery": delivery,
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
    # The split of `decoys_presented`: reporting a benign thing AS AN INCIDENT is
    # the failure the field measures; naming it and refuting it in the same
    # breath is not, and a red herring that is silently ignored is
    # indistinguishable from one that was never seen.
    if rec["decoys_asserted_as_incident"] is None:
        print("            of the presented: — asserted as incident · — with "
              "refutation   ← NOT MEASURED (None, not 0)")
        if st.get("why_fields"):
            print("            %s" % st["why_fields"])
    else:
        print("            of the presented: %d asserted as incident (cited "
              "under «Улики» of a finding) · %d presented with refutation "
              "(cited only under «Чем опровергал»)"
              % (rec["decoys_asserted_as_incident"],
                 rec["decoys_presented_refuted"]))
    if rec["decoys_anchored_elsewhere"] is not None:
        print("            %d anchored elsewhere — verdict/coverage/register, "
              "claimed by no finding and refuted by no rejection"
              % rec["decoys_anchored_elsewhere"])

    # THE OUTCOME HEADLINE. Two lines of health — a block that forgot its
    # outcome is a delivery defect, and a register that does not add up to the
    # verdict is a report arguing with itself — and then the join that is the
    # whole point: the same citation of the same planted non-defect is a false
    # positive under «успех»/«попытка» and a refutation under «норма».
    o = rec["outcomes"]
    if o["finding_blocks"] is None:
        print("outcomes  : — NOT MEASURED (%s)" % o["why"])
    else:
        print("outcomes  : %d finding block(s) — %d state «исход:», %d without "
              "the line, %d outside the vocabulary"
              % (o["finding_blocks"], o["outcomes_stated"],
                 o["outcome_missing"], o["outcome_invalid"]))
        agree = ("—" if o["contradiction"] is None
                 else ("ПРОТИВОРЕЧИЕ" if o["contradiction"] else "agree"))
        print("            the register implies «%s» · the ВЕРДИКТ section "
              "states «%s» — %s"
              % (o["implied_verdict"] or "—", o["stated_verdict"] or "—", agree))
        for bad in o["outcome_invalid_findings"][:4]:
            print("            Н-%s, line %s: %r — not in the vocabulary %s"
                  % (bad["finding"], bad["report_line"], bad["text"][:70],
                     o["grammar"]))
    if not o["measured"]:
        print("            decoy split (false positive / refutation): — NOT "
              "MEASURED (None, not 0)")
        print("            %s" % o["why"])
    else:
        print("            decoys: %d false positive (cited inside a finding "
              "marked успех/попытка) · %d refutation (marked норма) · %d "
              "dismissed · %d elsewhere · %d unlabelled · %d never anchored"
              % (o["decoys_false_positive"], o["decoys_refutation"],
                 o["decoys_dismissed"], o["decoys_elsewhere"],
                 o["decoys_unlabelled"], o["decoys_not_anchored"]))
        print("            real:   %d asserted as incident · %d marked «норма» "
              "— a miss dressed as diligence · %d dismissed · %d elsewhere · "
              "%d unlabelled · %d never anchored"
              % (o["real_asserted_as_incident"], o["real_marked_normal"],
                 o["real_dismissed"], o["real_elsewhere"],
                 o["real_unlabelled"], o["real_not_anchored"]))
    if o["contradiction"]:
        print("⚠ ПРОТИВОРЕЧИЕ: the findings add up to «%s» and the ВЕРДИКТ "
              "section says «%s». One of the two is wrong, and a hand-over that "
              "contradicts itself is a delivery defect."
              % (o["implied_verdict"], o["stated_verdict"]))
    if o["outcome_missing"]:
        print("⚠ %d finding block(s) state no outcome at all: %s. A reader "
              "cannot tell «I found an intrusion» from «I checked this and it "
              "was nothing» — both are written in the same fields."
              % (o["outcome_missing"],
                 ", ".join("Н-%s" % f
                           for f in o["outcome_missing_findings"][:10])))
    # THE UNCITED-DISPOSAL HEADLINE. Both counts, side by side, never summed: one
    # is a rejected candidate nobody can check, the other is a whole file closed
    # in a coverage row nobody can check, and the v22 Linux arm lost a whole
    # exfiltration in the second kind.
    def _un(n, d, what):
        if d is None:
            return "%s — / — (NOT MEASURED: no such section; None, not 0)" % what
        pct = ("%.0f %%" % (100.0 * n / d)) if d else "—"
        return "%s %d / %d (%s)" % (what, n, d, pct)
    print("uncited   : %s · %s   ← a disposal nobody can check is not a judgement"
          % (_un(st["rejections_uncited"], st["rejections"], "rejections"),
             _un(st["coverage_rows_uncited"], st["coverage_rows"],
                 "coverage rows")))
    if st["rejections"] is not None:
        print("            rejections read from «%s»"
              % (st["rejected_sections"] or ["—"])[0][:34])

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

    # THE DELIVERY HEADLINE. `citations` above is the UNION's integrity, which on
    # a divergent run is an average describing neither document. This line says
    # what each channel scored on its own, and turns `CHANNELS DIVERGE` from a
    # warning into three integers plus a verdict per side.
    d = rec["delivery"]
    if not d.get("measured"):
        print("delivery  : — NOT MEASURED (%s)" % d.get("why"))
    else:
        b = d["blocks"]
        if d["diverged"]:
            print("delivery  : two channels DIVERGE — %d shared block(s), %d only "
                  "in the final message, %d only in work/report.md"
                  % (b["shared"], b["only_in_message"], b["only_in_file"]))
        elif len(d["channels"]) > 1:
            print("delivery  : both channels carried the same report (%s) — %d of "
                  "%d file block(s) already in the message, scored ONCE"
                  % (d["relation"], b["shared"], b["file"]))
        else:
            print("delivery  : one channel (%s) — nothing to diverge from"
                  % d["relation"])
        for name in ("message", "file"):
            c = d["channels"].get(name)
            if not c:
                continue
            role = ("hand-over" if name == d["handover"] else "checked  ")
            s2 = c["citations"]
            vp = ("—" if c["verified_pct"] is None
                  else "%.1f %%" % c["verified_pct"])
            pres = "—" if c["presented"] is None else str(c["presented"])
            co = c["outcomes"]
            print("            %s (%-16s): citations %d / %d ok (%s), "
                  "wrong-content %d · anchored %d / %d · presented %s / %d · "
                  "outcomes %s of %s finding block(s) · %d block(s) this "
                  "channel alone"
                  % (role, CHANNEL_FILE[name], s2.get("ok", 0),
                     s2.get("total", 0), vp, s2.get("wrong-content", 0),
                     c["anchored"], c["anchorable"], pres, c["presentable"],
                     "—" if co["outcomes_stated"] is None
                     else co["outcomes_stated"],
                     "—" if co["finding_blocks"] is None
                     else co["finding_blocks"],
                     c["unique_blocks"]))
        if d["handover_not_in_checked"] is None:
            print("            delivered-but-never-checked: — NOT MEASURED (%s)"
                  % (d["why"] or "there is no second channel to have checked it "
                                 "against; None, not 0"))
        else:
            print("            delivered-but-never-checked: %d citation(s)"
                  % d["handover_not_in_checked"])
            for c in (d["handover_not_in_checked_examples"] or [])[:4]:
                print("                %s (%s) — hand-over line %d"
                      % (c["citation"], c["verdict"], c["report_line"]))
        if d["handover_failed"]:
            print("⚠ THE HAND-OVER DOES NOT PASS ITS OWN CHECK — `citecheck.py "
                  "--delivered` exits non-zero on this. A re-typed citation is a "
                  "new claim and needs its own check; being in the verified set "
                  "catches 1 of 21.")
        if d["warning"]:
            print("⚠ %s" % d["warning"])

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
    print("%-5s %-6s %-9s %-10s %-9s %-11s %-12s %s"
          % ("id", "kind", "anchored", "presented", "asserted", "disposition",
             "outcome", "title"))
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
        disp = r.get("disposition") or "—"
        if r["herring"] and disp == "asserted":
            disp = "✗ asserted"
        # The word the report itself used, and what it means for THIS kind of
        # defect: the same «норма» is a refutation on a decoy and a miss on a
        # real one, so the mark differs and the token does not.
        oz, otok = r.get("outcome_zone"), r.get("outcome")
        if oz is None:
            oc_col = "—"
        elif oz == "incident":
            oc_col = ("✗ FP %s" % otok) if r["herring"] else otok
        elif oz == "normal":
            oc_col = otok if r["herring"] else ("✗ %s" % otok)
        else:
            oc_col = "· " + oz
        print("%-5s %-6s %-9s %-10s %-9s %-11s %-12s %s"
              % (r["defect"], kind, anc, pre, asr, disp, oc_col,
                 r["title"][:44]))
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
    ap.add_argument("--delivered", metavar="handover.md",
                    help="the HAND-OVER beside --report: the text the reader "
                         "actually received. Scores both channels separately and "
                         "fills the delivery axis; --ledger does this by itself.")
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
    if a.delivered and not a.report:
        sys.exit("--delivered is the hand-over BESIDE --report; --ledger already "
                 "carries both channels")
    if a.report:
        text = (sys.stdin.read() if a.report == "-"
                else open(a.report, encoding="utf-8", errors="replace").read())
        answer, artifact = None, None
        if a.delivered:
            # `--report` is the checked artefact, `--delivered` the hand-over —
            # the same two roles `citecheck.py --delivered` names. What gets
            # scored stays the UNION of the two, so the composed columns mean
            # what they mean everywhere else.
            answer = open(a.delivered, encoding="utf-8", errors="replace").read()
            artifact = text
            text = deliverable.compose(answer, artifact)
        source = {"report_path": a.report, "arm": a.arm, "trace_dir": None,
                  "delivered_in": (deliverable.channel(answer, artifact)
                                   if a.delivered else None),
                  "duplication": (deliverable.duplication(answer, artifact)
                                  if a.delivered else None)}
    elif a.ledger:
        rows = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
        if not rows:
            sys.exit("no rows in %s" % a.ledger)
        run = score_bench.select_row(rows, a.arm, a.trace,
                                     a.dataset or raw_key.get("dataset"))
        score_bench.check_key_matches_dataset(raw_key, run)
        text = deliverable.of_row(run)
        dup = deliverable.duplication_of_row(run)
        parts = deliverable.channels_of_row(run)
        answer, artifact = parts.get("message"), parts.get("file")
        source = {"report_path": None, "arm": run.get("arm"),
                  "trace_dir": run.get("trace_dir"),
                  "delivered_in": deliverable.channel_of_row(run),
                  "duplication": dup}
        if source["delivered_in"] == "file":
            print("⚠ DELIVERY: this run's report is in work/report.md, not in its "
                  "final message. The scores below measure the REPORT; delivery "
                  "is a separate, SCORED axis — see `delivery` below.")
        # A run that hands the SAME report to both channels is scored once —
        # `deliverable.compose` is a union, not a concatenation. When the two
        # channels genuinely disagree, both are scored, the disagreement is a
        # record field rather than a resolved question, and `render` prints it:
        # which half was "the report" is not something a scorer may answer
        # silently.
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
                a.require_quote, answer=answer, artifact=artifact)
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
