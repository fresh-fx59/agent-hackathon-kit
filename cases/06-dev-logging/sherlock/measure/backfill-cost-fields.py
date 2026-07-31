#!/usr/bin/env python3
"""backfill-cost-fields.py — put duration_s/input_tokens/output_tokens/turns on the
rows that were scored before report-case.py started emitting them.

Every one of those numbers already exists on disk, in the run dir's meta.json; the
rows just never carried it. This reads each row's own `run_dir` — never a guess from
the timestamp, never a re-derivation from the stream — and copies the four fields
across.

Idempotent by construction: a field is written only if the KEY IS ABSENT from the
row. A key that is present, including one already holding null, is left exactly as it
is, so re-running can neither double-write nor overwrite a measured value with a
later absence.

A row whose run dir or meta.json is gone, or whose meta.json will not parse, gets the
four keys with explicit nulls. Null is the point: it says "this run's cost is
unrecoverable" and can never be mistaken for a run that was measured and cost
nothing. Filling those with 0 would invent a free arm.

    python3 backfill-cost-fields.py            # rewrite results.jsonl in place
    python3 backfill-cost-fields.py --dry-run  # report only, touch nothing
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIELDS = ("duration_s", "input_tokens", "output_tokens", "turns")

ap = argparse.ArgumentParser()
ap.add_argument("--results", default=os.path.join(HERE, "results.jsonl"))
ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()


def cost_fields(row):
    """The four fields for one row, plus why they are what they are.

    Same null-not-zero rule as report-case.py: a value that is not a real number in
    meta.json — absent, null, a string — is unmeasured, so it is None.
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
    for k in FIELDS:
        v = meta.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        else:
            out[k] = None
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
    values, why = cost_fields(row)
    for k in FIELDS:
        row.setdefault(k, values[k])
    if any(values[k] is not None for k in FIELDS):
        filled += 1
        mark = "✓"
    else:
        nulled += 1
        mark = "∅"
    print("%s %-24s %-4s %s" % (mark, row.get("case_id"), row.get("arm"), why))
    print("    " + "  ".join("%s=%s" % (k, values[k]) for k in FIELDS))

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
