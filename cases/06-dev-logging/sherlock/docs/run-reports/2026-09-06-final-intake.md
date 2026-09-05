# Fresh v45 corpus rendering — experiment report

## Current state

Both raw corpora rendered successfully with frozen v45, package SHA256 `36a0dfe2c674ac11a13d1dc1b6a0b8c3e720fdb08577c8c9ec5d386cd317e40e`. Winevtx: 143 JSONL files, 90,267 records, 3.778820 seconds. Independent corpus: one JSONL file, 469 records, 0.063937 seconds. Neither result is an accepted model investigation.

## Timeline

- Ran v45 ingest with explicit EVTX paths into exclusive fresh output roots; both exited0. Exact argv, raw source hashes, stdout and stderr retained.
- Initial aggregation incorrectly reported90,554 and472 by counting ingest-manifest metadata lines. Original result.json retained unchanged. Separate correction files exclude287 and3 metadata lines respectively.
- Verified complete output file-set equality and every rendered file hash against prior successful rendering. Rehashed all144 raw sources against pre-run inventory: unchanged. Correct JSONL counts90,267 and469.

## Findings and limits

Output bytes reproduce the structurally audited rendering; Winevtx includes29,913 physically recovered records beyond declared header chunk ranges. This is provenance, not proof that recovered records describe active log state. Model inputs must preserve this qualification. Only JSONL files are event corpus inputs; metadata is separate provenance. No old findings, reports or checkpoints were copied into fresh rendering roots. No provider request occurred.

## Evidence

Raw artifacts: `/Users/a/hack/sherlock-final-intake-20260906`. Exact inventory and retained artifact hashes: [JSON report](2026-09-06-final-intake.json). Earlier physical-record audit: `2026-09-05-header-audit*` reports. Next gate: independently initialized model investigations with the same frozen package.
