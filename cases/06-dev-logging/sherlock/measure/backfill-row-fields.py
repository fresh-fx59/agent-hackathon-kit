#!/usr/bin/env python3
"""backfill-row-fields.py — put the fields meta.json always held onto the rows that
were scored before report-case.py started emitting them.

Two waves of the same defect. The COST fields (duration_s / input_tokens /
output_tokens / turns) reached results.jsonl only from 2026-07-31; the ARM CONDITIONS
(skill_delivery, subagent_available) only from 2026-08-02. Both were in each run dir's
meta.json the whole time. This reads each row's own `run_dir` — never a guess from the
timestamp, never a re-derivation from the stream — and copies them across.

Conditions are not decoration: `subagent_available` is what separates the fan-out-free
runs that converted D11 and D01 into mechanism greens from the earlier ones that had
the `agent` tool, and a ledger that cannot see it pools two different experiments.

Idempotent by construction: a field is written only if the KEY IS ABSENT from the
row. A key that is present, including one already holding null, is left exactly as it
is, so re-running can neither double-write nor overwrite a measured value with a
later absence.

A row whose run dir or meta.json is gone, or whose meta.json will not parse, gets
every key with an explicit null. Null is the point: it says "unrecoverable for this
run" and can never be mistaken for a measurement. Filling cost with 0 would invent a
free arm; defaulting `subagent_available` to False would claim fan-out was off on the
rows where it was on.

    python3 backfill-row-fields.py            # rewrite results.jsonl in place
    python3 backfill-row-fields.py --dry-run  # report only, touch nothing
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# field -> the type a value must ALREADY have in meta.json to be copied. Nothing is
# coerced: a cost must be a real number (a bool is not — True would average as 1) and
# `subagent_available` must be a real bool, because the string "false" is truthy and
# copying it would invert the record.
NUMBER = (int, float)
FIELDS = {"duration_s": NUMBER, "input_tokens": NUMBER, "output_tokens": NUMBER,
          "turns": NUMBER, "skill_delivery": str, "subagent_available": bool}

ap = argparse.ArgumentParser()
ap.add_argument("--results", default=os.path.join(HERE, "results.jsonl"))
ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()


def kept(v, want):
    """A meta.json value, only if it is already the type this field is recorded in.

    Same rule as report-case.py, in both directions: `isinstance(True, int)` is True,
    so a bool must be excluded from the numeric fields, and a bool field must accept
    ONLY a bool — never a truthy string.
    """
    if want is bool:
        return v if isinstance(v, bool) else None
    return v if isinstance(v, want) and not isinstance(v, bool) else None


def row_fields(row):
    """Every backfillable field for one row, plus why they are what they are.

    Same null-not-guess rule as report-case.py: a value that is absent, null, or of
    the wrong type in meta.json was never recorded, so it is None.
    """
    run_dir = row.get("run_dir")
    if not run_dir:
        return {k: None for k in FIELDS}, "row carries no run_dir"
    path = os.path.join(run_dir, "meta.json")
    try:
        meta = json.load(open(path, encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("meta.json is not an object")
    except OSError as e:
        return {k: None for k in FIELDS}, "meta.json unreadable (%s)" % e.strerror
    except ValueError as e:
        return {k: None for k in FIELDS}, "meta.json malformed (%s)" % e
    out, missing = {}, []
    for k, want in FIELDS.items():
        out[k] = kept(meta.get(k), want)
        if out[k] is None:
            missing.append(k)
    if missing:
        return out, "meta.json read; not measured: %s" % ", ".join(missing)
    return out, "meta.json read"


if not os.path.exists(a.results):
    sys.exit("✗ no results file: %s" % a.results)

rows, bad = [], 0
with open(a.results, encoding="utf-8") as fh:
    for n, line in enumerate(fh, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            # A ledger line we cannot parse is never dropped silently — dropping it
            # would shrink the artifact without leaving a trace that it happened.
            bad += 1
            print("✗ line %d is not JSON — REFUSING to rewrite the ledger" % n)
if bad:
    sys.exit("✗ %d unparseable line(s); fix them by hand first, nothing written" % bad)

filled = nulled = skipped = 0
for row in rows:
    if all(k in row for k in FIELDS):
        skipped += 1
        continue
    values, why = row_fields(row)
    for k in FIELDS:
        row.setdefault(k, values[k])
    if any(values[k] is not None for k in FIELDS):
        filled += 1
        mark = "✓"
    else:
        nulled += 1
        mark = "∅"
    print("%s %-24s %-4s %s" % (mark, row.get("case_id"), row.get("arm"), why))
    for k in FIELDS:
        print("    %-20s %s" % (k, values[k]))

print("\n%d row(s): %d got real values, %d got explicit nulls, %d already had the fields"
      % (len(rows), filled, nulled, skipped))

if a.dry_run:
    print("(--dry-run: results.jsonl not touched)")
elif filled or nulled:
    # Rewrite through a temp file in the SAME directory and os.replace: the ledger is
    # append-only for every other writer, and a half-written results.jsonl would
    # destroy measurements that cost real money to produce.
    tmp = a.results + ".backfill.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    # The temp file is born under the umask (0644); the ledger it replaces is 0600
    # because it is built from a corpus that must not leave this box. Carry the
    # original mode across or a maintenance script quietly widens the permissions on
    # the one file in this directory that is private on purpose.
    os.chmod(tmp, os.stat(a.results).st_mode & 0o7777)
    os.replace(tmp, a.results)
    print("wrote %s" % a.results)
else:
    print("nothing to do — results.jsonl not touched")
