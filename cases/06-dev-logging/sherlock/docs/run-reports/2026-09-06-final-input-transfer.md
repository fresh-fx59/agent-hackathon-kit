# Fresh full-run input preparation

## Current state

Both JSONL-only corpora and separate provenance reached the test host. Complete161-file set,93,867,616bytes and every SHA256 matched the local transfer manifest. Two subsequently added generic investigation prompts also match their remote hashes. Files belong to claude-developer. This verifies input preparation, not model execution or report acceptance.

## Timeline

- Built exclusive local `/Users/a/hack/sherlock-final-inputs-20260906` from freshly rendered v45 outputs. Copied only JSONL into event corpora; placed ingest metadata, raw source hashes and rendering evidence under separate provenance directories.
- Created external inventory keys with dataset label, path/bytes/lines/SHA256 rows and defects: []. Actual run-manifest inspector accepted both; an initial Path-valued caller was rejected before inspection and corrected to string.
- Transferred inputs to `contabo-prod:/home/claude-developer/hack/sherlock-final-inputs-20260906`. Compared complete relative file sets, sizes and content hashes, then set ownership to the test account. All161files passed.
- Added full-investigation prompts requiring primary-source research, whole-corpus coverage, evidence-backed findings, uncertainty, all four final gates and retained artifacts. Prompts include acquisition limitations, without expected findings. Transferred both; hashes match.

## Boundaries

No earlier report, findings, worklist, checkpoint or pattern card is included. Winevtx physical-recovery qualification is retained. Inventory keys have no semantic oracle: do not run score-bench or infer recall/negative-control/false-positive scores. Accepted report correctness still needs direct evidence review beyond mechanical gates. Provider credentials were not used.

## Evidence

[Transfer record](2026-09-06-final-input-transfer.json). Committed exact manifest: [JSON inventory](2026-09-06-final-input-manifest.json); local and remote roots above retain inputs and prompts. Next: bind fresh qualification receipts and launch the approved monitored model runs.
