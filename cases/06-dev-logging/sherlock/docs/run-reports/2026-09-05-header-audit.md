# Independent EVTX record-header audit — 2026-09-05

Purpose: verify structural raw-to-rendered identity independently of the converter.
Scope:143 source EVTX files, existing r2 rendered corpus; no model or provider.
Inputs and hashes: paired JSON receipts in this directory, complete source manifest
in 2026-09-05-raw-input-manifest.json. Runtime package v44 unchanged.

## Results and correction

First attempt used python-evtx Evtx.records(), traversing only the file header's
declared chunk count. It found60,354 records versus90,267 rendered;26files differed.
All visited record sizes verified. This is a rejected identity comparison, retained
in header-audit-r1.json; do not overwrite it into acceptance.

Source inspection showed FileHeader.chunks(include_inactive=False) stops at the
header count. Rust evtx deliberately traverses physical chunks beyond that count
for dirty/extended files. Application.evtx demonstrated15declared versus33physical
chunks; full physical traversal yielded all2,942rendered records. A diagnostic
attempt to print chunk first_record_number raised AttributeError after printing
those counts; corrected access was unnecessary for the final audit.

Second attempt explicitly used physical chunks: all143files and90,267ordered
record IDs reconcile; all record sizes and nonempty chunk checksums verify.
29,913records are in chunks beyond header-declared counts. This verifies structural
identity only. Do not infer active-log membership or semantic payload correctness
from checksums; beyond-header provenance belongs in the corpus report. No records
were deleted or silently excluded to achieve agreement.

## Method and retained evidence

python-evtx traversed record headers, compared record_num() with each rendered
Event.System.EventRecordID in order, checked record.verify() and chunk.verify().
Each receipt preserves raw/rendered SHA256, counts, mismatch state, and r2 declared
chunk count, flags, beyond-header count and invalid-checksum indexes. Per-file
append-only events remain in both local audit directories. First1.763s, second1.968s.
Next: carry this input provenance into target intake/report; payload interpretation
and full report acceptance remain separate tasks.

## Primary sources

Retrieved2026-09-05. Python traversal policy:
https://github.com/williballenthin/python-evtx/blob/master/Evtx/Evtx.py .
Rust parser physical-chunk policy:
https://github.com/omerbenamram/evtx/blob/master/src/evtx_parser.rs .
Installed Python source and actual converter0.12.2 behavior were inspected locally;
web master references support rationale, not a claim of pinned source identity.

## Timeline

- First traversal disagreed on26files; investigated before interpreting as loss.
- Source comparison and Application.evtx reproduction located traversal policy.
- Explicit physical traversal reconciled all records and retained provenance.
