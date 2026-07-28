## ADDED Requirements

### Requirement: Human feedback governs reusable incident knowledge
The system SHALL persist proposed incident cases and immutable feedback events
in local SQLite storage. Only a case explicitly marked `confirmed` SHALL be
returned as reusable knowledge; proposed and rejected cases SHALL remain
auditable but SHALL NOT be selected for reuse.

#### Scenario: Confirm a proposed case
- **WHEN** an operator confirms a proposed incident case with validation notes
- **THEN** the system records the feedback event and makes that case available
  to similarity retrieval

#### Scenario: Reject a proposed case
- **WHEN** an operator rejects a proposed incident case
- **THEN** the case and feedback remain auditable but a subsequent similarity
  search does not return it as reusable knowledge

### Requirement: Reuse is explicit and measurable
The system SHALL support a cold investigation and a subsequent warm
investigation that receives similar confirmed cases as labelled context. The
demo SHALL record elapsed analysis time and report whether the warm run met the
defined speedup threshold without claiming that similarity is direct proof of
the new root cause.

#### Scenario: Run cold then warm demo
- **WHEN** a confirmed case exists before a similar second investigation
- **THEN** the warm report labels the retrieved case, retains independent
  evidence for its own finding, and outputs cold/warm timing comparison

