## ADDED Requirements

### Requirement: Deterministic replay emits evidence-backed early warnings
The system SHALL replay a fixed event-time JSONL fixture deterministically and
evaluate active stream rules without an LLM in the processing path. An alert
SHALL include the matched rule, event-time, source evidence IDs, and processing
latency.

#### Scenario: Warn before the fixture's user-visible failure
- **WHEN** the degraded replay fixture crosses the configured precursor threshold
- **THEN** the system emits an early-warning alert before the fixture's marked
  user-visible failure event

### Requirement: Healthy traffic does not produce the incident alert
The system SHALL evaluate the same active stream rules against a healthy
control fixture.

#### Scenario: Replay healthy control
- **WHEN** the healthy control fixture is replayed
- **THEN** the system emits no alert for the checkout degradation rule

