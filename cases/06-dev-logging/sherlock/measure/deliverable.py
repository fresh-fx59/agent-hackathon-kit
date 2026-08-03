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
"""

SEP = "\n\n--- work/report.md ---\n\n"


def compose(answer, artifact):
    """The union, with the final message first — it is what the run said last.

    Message-only rows compose byte-identically to their answer. Thirteen rows
    predate the artifact channel; if composition perturbed them at all, the
    0-of-11 baseline would stop being comparable to its own published number.
    """
    a = answer or ""
    r = artifact or ""
    if not r.strip():
        return a
    if not a.strip():
        return r
    return a.rstrip() + SEP + r


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


def of_row(row):
    """The deliverable of a ledger row, tolerating rows written before the field."""
    return compose(row.get("answer"), row.get("artifact"))


def channel_of_row(row):
    return row.get("delivered_in") or channel(row.get("answer"), row.get("artifact"))
