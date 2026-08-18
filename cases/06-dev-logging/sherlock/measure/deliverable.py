#!/usr/bin/env python3
"""deliverable.py — everything a run produced, across every channel it used.

A run has two ways to hand over its report and the ledger used to see one.

2026-08-02, 649 MB corpus (`eval/bench/runs/20260802T221034Z-v11`). The model
finished its investigation, passed `citecheck` 45/45, announced «Теперь
финальный шаг — вывести отчёт полностью», called `read_file` on its own
`work/report.md`, and stopped. Final message: 101 chars. `work/report.md`:
19,991 chars, complete, every citation verified. Recorded: nothing, for
18,758,431 input tokens.

**That is not disobedience and no instruction can fix it.** The model output the
report; `read_file` puts output in a tool result, and `--output-format json`
exposes only the `result` record. Every phrasing of "output the report" is
satisfiable by a tool the model already has, which is why two instruction edits
(`ebf39ca` in SKILL.md, `6490599` in the tool's own green verdict) both failed —
they were the same experiment twice.

So the deliverable is the UNION of both channels. The union is defined here,
once, and imported by the runner that records it and the scorer that judges it:
two copies of this rule is how one measurement becomes two incomparable scales.
→ [[measurement-artifacts-discipline]]

What this deliberately does NOT do: hide which channel carried the report.
`channel()` is recorded on every row, so "the arm scores 8/11" and "the arm
answered in 101 characters" stay separately visible and separately fixable.

A UNION, NOT A CONCATENATION — the defect this file shipped for 16 days
----------------------------------------------------------------------
This module always said UNION. `compose()` concatenated. Every arm since the
artifact channel was added delivered the SAME report on both channels, so every
citation the project published was that report's citations counted twice.
Measured 2026-08-19 on all six runs on disk (`--key` and corpus per the score
ledger, citecheck pinned to v16, the version the scores were taken with):

    arm                     answer   file   composed   file-only  published
    fleet-negative          48,776  48,859    97,661        147       294
    AIT v16-contaminated    44,991  49,697    94,714        141       268
    BlueSky v16             42,076  42,077    84,179        106       212
    AIT v16-clean           45,962  46,048    92,036        157       314
    BlueSky v19             59,556  59,491   119,073        158       316
    AIT v19                 65,957  65,942   131,925        198       396

The *rate* never moved — 316/316 and 158/158 are both 100 % — which is why this
survived four reviews of the scorer.

**Equality was never the test.** Not one arm is byte-identical across its two
channels and only one (BlueSky v16) is identical modulo whitespace, because
`work/report.md` is hard-wrapped and the final message is not, and three arms
opened the message with a preamble («Отчёт целиком:») the file has no line for.

So the UNIT is the BLOCK: a paragraph, or a fenced code block, normalised for
whitespace. That is exactly the unit re-wrapping preserves, and it needs no
similarity threshold — a threshold is a number nobody can defend at the edge.
A block of the file that already appears in the message is the same block
delivered twice, and a union contains it once.

**A silent pick is the same class of error as the double count.** AIT
v16-contaminated answered a CONDENSED rewrite beside its file (`…access.log.2:5315`
in the message where the file wrote the whole path), and those two channels
genuinely say different things. Both are kept — dropping one would cost the very
finding this module was written to save — and `duplication()` reports the
disagreement so the record can print it.
"""
import re

SEP = "\n\n--- work/report.md ---\n\n"

# A block boundary is a blank line — but NOT one inside a fenced code block. A
# fence split in two leaves a bare "```" as its own block, and a bare "```"
# matches every other bare "```" in the other channel; dropping it as a duplicate
# unbalances the fence and turns the next heading into sample text for
# `score-report.py`'s section parser.
BLANK_RE = re.compile(r"^[ \t]*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def blocks(text):
    """[block, …] — paragraphs and fenced code blocks, in document order."""
    out, cur, fence = [], [], None
    for line in (text or "").splitlines():
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence else m.group(1)
            cur.append(line)
            continue
        if fence is None and BLANK_RE.match(line):
            if cur:
                out.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return [b for b in out if b.strip()]


def _norm(block):
    """Whitespace-insensitive identity: the only thing re-wrapping changes."""
    return " ".join(block.split())


def compose(answer, artifact):
    """The UNION, with the final message first — it is what the run said last.

    Message-only rows compose byte-identically to their answer. Thirteen rows
    predate the artifact channel; if composition perturbed them at all, the
    0-of-11 baseline would stop being comparable to its own published number.

    So does a run whose file repeats its message: four of the six measured arms
    add not one block, and their deliverable is the message, byte for byte.

    De-duplication is BETWEEN channels, never inside one. A report that wrote the
    same row twice wrote it twice, and that is a fact about the report.
    """
    a = answer or ""
    r = artifact or ""
    if not r.strip():
        return a
    if not a.strip():
        return r
    already = {_norm(b) for b in blocks(a)}
    new = [b for b in blocks(r) if _norm(b) not in already]
    if not new:
        return a
    return a.rstrip() + SEP + "\n\n".join(new)


def channel(answer, artifact):
    """Which channel carried the report: message | file | both | none.

    The 0.5 threshold is a ratio, not a byte count, because report length is set
    by the corpus. Rep 2 answered 25,559 chars beside a 34,398-char file (0.74 →
    `both`, a real delivery); the collapsed run answered 101 beside 19,991
    (0.005 → `file`).
    """
    a = (answer or "").strip()
    r = (artifact or "").strip()
    if not r:
        return "message" if a else "none"
    if not a:
        return "file"
    return "both" if len(a) >= 0.5 * len(r) else "file"


def duplication(answer, artifact):
    """How much of the two channels is the SAME report — block by block.

    -> {relation, message_blocks, file_blocks, shared_blocks, only_in_message,
        only_in_file, warning}

    `relation` is one of:

        none                    the run handed over nothing
        message-only            one channel
        file-only               one channel
        identical               each channel's blocks all appear in the other
        file-repeats-message    the file adds nothing — four of the six arms
        message-repeats-file    the message adds nothing
        divergent               each channel has blocks the other does not

    Only `divergent` carries a `warning`, and it is a warning rather than a
    silent pick on purpose: two channels that disagree are a DELIVERY fact, and
    the scorer that prints it must not be the one to decide which half was the
    report. Both are scored.
    """
    a_blocks = blocks(answer)
    r_blocks = blocks(artifact)
    a_norm = [_norm(b) for b in a_blocks]
    r_norm = [_norm(b) for b in r_blocks]
    a_set, r_set = set(a_norm), set(r_norm)
    only_msg = sum(1 for n in a_norm if n not in r_set)
    only_file = sum(1 for n in r_norm if n not in a_set)
    shared = len(r_norm) - only_file
    if not a_blocks and not r_blocks:
        rel = "none"
    elif not r_blocks:
        rel = "message-only"
    elif not a_blocks:
        rel = "file-only"
    elif not only_msg and not only_file:
        rel = "identical"
    elif not only_file:
        rel = "file-repeats-message"
    elif not only_msg:
        rel = "message-repeats-file"
    else:
        rel = "divergent"
    warning = None
    if rel == "divergent":
        warning = (
            "CHANNELS DIVERGE: %d block(s) appear only in the final message and "
            "%d only in work/report.md. This run did not hand over one report on "
            "two channels — it handed over two documents, and BOTH are scored. "
            "Picking one silently would be the same class of error as counting "
            "the shared %d twice." % (only_msg, only_file, shared))
    return {"relation": rel,
            "message_blocks": len(a_blocks), "file_blocks": len(r_blocks),
            "shared_blocks": shared,
            "only_in_message": only_msg, "only_in_file": only_file,
            "warning": warning}


def of_row(row):
    """The deliverable of a ledger row, tolerating rows written before the field."""
    return compose(row.get("answer"), row.get("artifact"))


def channel_of_row(row):
    return row.get("delivered_in") or channel(row.get("answer"), row.get("artifact"))


def duplication_of_row(row):
    return duplication(row.get("answer"), row.get("artifact"))
