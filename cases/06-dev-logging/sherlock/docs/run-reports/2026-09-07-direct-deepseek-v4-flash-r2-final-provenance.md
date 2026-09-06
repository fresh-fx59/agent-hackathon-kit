# Direct DeepSeek-v4-flash r2 final-report provenance

The model-authored r2 report is preserved unchanged at
`2026-09-07-direct-deepseek-v4-flash-r2.md` (SHA-256
`237569ed1ad0f70d7d52790c7c8b732168856ca19eccb55c31a3fcd6ea642345`).

The review-ready copy, `2026-09-07-direct-deepseek-v4-flash-r2-final.md`
(SHA-256 `9590d152b6f764166350a0a3cb96b0d4643b1a6943c99ab43d29e0c77867e6d0`),
contains one editorial citation normalization by Codex. It makes no model-claim
or evidence change:

```diff
- Security-Mitigations-4KernelMode.jsonl:5
+ rendered/Microsoft-Windows-Security-Mitigations-4KernelMode.jsonl:5
```

The canonical corpus path exists and line 5 is in bounds. A full final-copy
mechanical audit found 17 citation occurrences (12 unique), zero missing paths,
and zero out-of-bounds lines. The corpus's established sorted-file SHA-256 digest
was unchanged before and after the run:
`061d83326012f759742d8de659e0d37697a6669588129c6f7d01cf97bb3f5b7c`.

The raw evidence index is
`artifacts/2026-09-07-direct-deepseek-v4-flash-r2/raw-evidence-sha256.txt`
(SHA-256 `be24024a5516357524836652df7dba21f0e0237587587c78f9af3b63bc8ed2ca`).
It indexes 68 parseable exchanges; every captured request and response identifies
`deepseek-v4-flash`, with no captured provider error. The launch wrapper did not
retain an OS exit-status receipt. Process absence, empty stderr, and the final
captured DeepSeek response (`finish_reason=stop`, no error) establish terminal
completion but do not independently prove exit code 0.
