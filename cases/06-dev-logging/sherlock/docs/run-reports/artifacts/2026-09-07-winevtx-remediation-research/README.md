# Winevtx evidence-correctness research — 2026-09-07

Specification only. Original report, corpus and runtime are unchanged. No new target-model investigation was launched.

## Deliverables

- Specification: `/Users/a/Documents/projects/personal-os/docs/superpowers/specs/2026-09-07-sherlock-winevtx-evidence-correctness.md`.
- `source/`: reproducible independent source extraction and full-file comparison with the preserved source manifest.
- `runtime/`: original r3 trace analysis and capture provenance. Large raw capture remains local/remote; compact extracts and hashes are versioned.
- `web/research.md`: primary sources, design implications and semantic-authority gaps.
- `sol/`: final specification critique and exact CLI receipts. Initial blockers were addressed in the final spec; no second Sol approval is claimed.
- `claude/`: subscription Claude CLI prompt, actual model receipt, assessment and terminal status. Context-only architecture assessment, not a raw-source audit.

## Scope and interpretation

Only Winevtx is in scope. The historical BlueSky launch is not proof of a corpus-selection software bug. The proposed explicit case/manifest gate prevents silent future mismatch regardless of whether selection comes from automation or an agent.

109 successful credential reads do not prove theft. Service configuration and account context do not prove a particular human acted. A citation-valid report can still contain wrong interpretations; independent semantic assessment remains required.

## Timeline

- 2026-09-07 — Independent source worker and Terra runtime worker ran separate investigations; Claude Opus via `claude -p` provided a separate architecture assessment.
- 2026-09-07 — Root reproduced the source extraction: exit 0, output identical, all 143 files match prior hashes and line counts. Original report SHA256 remains `49828debdfcebd9579a454c58c60fb43caa948de1b2054e99fcb3b6248024dc5`.

- 2026-09-07 — Final Sol critique completed; root revised trust/replay/acceptance contracts and retained the original negative verdict with its disposition. Workflow-economy Claude Haiku review identified excess status checks; remaining verification/save steps are batched. Its percentage estimate is not a measured result.
