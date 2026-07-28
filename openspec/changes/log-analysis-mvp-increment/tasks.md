## 1. Foundation and contracts

- [ ] 1.1 Create the isolated `claude-code` Python package, CLI entry point,
  stable output directories, and README quickstart for Python 3.9+ stdlib use.
- [ ] 1.2 Define normalized record, evidence, incident-report, rule, case, and
  feedback JSON contracts; document supported formats and ZIP safety limits.
- [ ] 1.3 Add deterministic fixtures for checkout incident, malformed inputs,
  unsafe ZIP cases, replay degradation, and healthy control.

## 2. Offline ingest and evidence spine

- [ ] 2.1 Implement TXT and JSONL readers that preserve source path/line,
  tolerate malformed input, and emit parse-error accounting.
- [ ] 2.2 Implement in-memory safe ZIP traversal with path, symlink, file-count,
  and uncompressed-size checks.
- [ ] 2.3 Implement configurable secret/PII masking before records reach
  storage or reports, with tests for raw-value absence.
- [ ] 2.4 Implement correlation precedence, deterministic timeline ordering, and
  stable `EV-*` evidence IDs resolving to original normalized records.

## 3. Rule-based checkout RCA

- [ ] 3.1 Define versioned active JSON rules for inventory degradation,
  reservation timeout, and payment-authorized/order-failed inconsistency.
- [ ] 3.2 Implement deterministic rule evaluation and structured JSON incident
  report generation with causal chain, actions, recommendations, limitations,
  confidence, and evidence references.
- [ ] 3.3 Implement Russian Markdown rendering strictly from the structured
  report and test no unsupported claim is introduced.
- [ ] 3.4 Add fixture-level tests proving the checkout RCA and source evidence
  links, including malformed-record resilience.

## 4. Confirmed knowledge and feedback loop

- [ ] 4.1 Create SQLite schema and repository for proposed cases, immutable
  feedback events, status transitions, fingerprints, and analysis timings.
- [ ] 4.2 Implement explicit CLI operations to save a proposed case and to
  confirm or reject it with validation notes.
- [ ] 4.3 Implement similarity retrieval that returns only confirmed cases and
  labels reuse context in the warm report.
- [ ] 4.4 Add cold/warm demo and tests proving rejected/proposed cases are not
  retrieved and that each warm finding retains independent evidence.

## 5. Replay early-warning proof

- [ ] 5.1 Implement deterministic event-time JSONL replay and windowed
  precursor rules with alert evidence IDs and processing-latency fields.
- [ ] 5.2 Add assertions that degraded replay alerts before its marked failure
  and healthy control produces no checkout-degradation alert.
- [ ] 5.3 Document that the result is early warning rather than prediction and
  that no LLM participates in replay's hot path.

## 6. Acceptance and handoff

- [ ] 6.1 Implement one offline end-to-end demo command: ingest → checkout RCA
  → proposed/confirmed feedback → warm reuse → replay checks.
- [ ] 6.2 Add stdlib unit and integration test command plus expected-report
  snapshots; ensure it runs without network access.
- [ ] 6.3 Update README, SKILL.md, example prompts, rule catalog notes, and
  MVP boundary/next-increment backlog for MCP, live adapters, generator, CI,
  and predictive features.
