# Runtime remediation research timeline

- 2026-09-07: Scope received: read-only forensic analysis of the Winevtx v52 r3 runtime/model failure chain, with no run, deployment, or modification of the immutable v52/r3 artifacts.
- 2026-09-07: Read the vault and Sherlock instructions plus `sherlock-run-review`. The active-production-incident check was completed; root confirmed incidents were explicitly deferred for this session.
- 2026-09-07: Located the r3-final bundle. Its manifest lists only `report.md`, citation catalog/refs, settings, launch/terminal receipts, and hashes; it does not retain raw model request/response or tool-call events. This is a bounded evidence gap for causal claims about the model's tool use.
- 2026-09-07: Located preserved raw OpenAI/Qwen event captures for r2 and the r3 independent source audit, and separated the immutable `skills/v52` package from the current dirty working tree. Per root, r2 will not be used to infer r3 behavior.
- 2026-09-07: Corrected the artifact destination before adding research: the requested path is this Sherlock-repository artifact directory. The mistakenly created empty vault-side directory was removed.
- 2026-09-07: Retrieved one immutable remote r3 capture without inspecting a live process: 204 OpenAI request/response records plus r3 receipts/settings. Ordered-record manifest SHA-256: `af553c5b71ff5668453208fa14e53dfd924b62bed7aed6240496faf7bde93a6e`.
- 2026-09-07: Audited persisted response tool calls against v52 primitives and recorded bounded runtime findings, exact pointers, and acceptance checks in `research-memo.md`.
