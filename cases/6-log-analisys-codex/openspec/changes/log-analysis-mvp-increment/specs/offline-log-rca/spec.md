## ADDED Requirements

### Requirement: Offline ingest normalizes supported local artifacts
The system SHALL ingest TXT, JSONL, and ZIP archives containing those formats
without network access. It SHALL preserve source path and line number for each
accepted record, record parse failures separately, redact configured sensitive
values before persistence or report generation, and reject unsafe or
over-limit ZIP entries.

#### Scenario: Analyze a mixed valid and malformed archive
- **WHEN** a user provides a ZIP with supported log files and malformed lines
- **THEN** the system returns normalized valid records with source references
  and a parse-error count without aborting the analysis

#### Scenario: Reject unsafe ZIP entry
- **WHEN** a ZIP contains a traversal, absolute-path, symlink, or over-limit entry
- **THEN** the system rejects the archive with an actionable safety error and
  does not write the entry outside the configured analysis area

### Requirement: Deterministic RCA report is evidence-linked
The system SHALL correlate records by trace ID, correlation ID, and order ID in
that precedence order and evaluate versioned active rules. For the supplied
checkout fixture, it SHALL emit a structured incident report identifying the
affected services, classification, causal chain, immediate actions, code
recommendation, limitations, and stable evidence IDs that resolve to source
records.

#### Scenario: Diagnose checkout timeout after payment authorization
- **WHEN** the user analyzes the supplied checkout incident logs
- **THEN** the report identifies inventory degradation followed by reservation
  timeout and an order failure after payment authorization, citing evidence
  records for every causal-chain assertion

#### Scenario: Render a human-readable report
- **WHEN** the system creates a structured incident report
- **THEN** it can render a Russian Markdown representation without adding
  claims that are absent from the structured report

