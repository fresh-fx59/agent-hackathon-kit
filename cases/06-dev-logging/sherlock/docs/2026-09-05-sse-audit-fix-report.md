---
title: SSE audit parser repair report
type: development-report
status: active
created: 2026-09-05
updated: 2026-09-05
tags: [sherlock, task-1, audit, sse]
links: [[2026-09-05-development-progress]]
---

# SSE audit parser repair report

## Current state

Task 1 repairs the offline target-contract audit so it can read the raw gzip SSE
response captures already accepted by its completion-journal filename contract.
No provider, secret, remote, or package-version action is in scope.

## Timeline

- 2026-09-05 — Read the root and Sherlock agent contracts plus the development
  contract and progress ledger. Scope is limited to the Task 3 audit parser,
  its focused tests, and this report.
- 2026-09-05 — Inspected `target-contract-probe.py`, its existing test suite,
  and `measure/upstream-log-proxy.py`. Root cause: `_task3_observations`
  accepts `*.res.sse.gz` filenames but passes every response to `_gzip_json`,
  which requires a single JSON document. The proxy instead captures the raw
  concatenation of SSE `data:` lines and writes it atomically as gzip.
- 2026-09-05 — Added a synthetic gzip capture containing a keepalive, two
  `data:` JSON events, and `data: [DONE]`. The focused regression failed as
  expected: `_gzip_json` raised `TARGET_PROBE_NOT_AUTHORIZED: invalid JSON` at
  the raw SSE bytes. No production code had changed at this point.
- 2026-09-05 — Added `_gzip_sse` and selected it only for admitted
  `.res.sse.gz` response names. It strictly decompresses UTF-8 SSE, accepts
  SSE metadata/comments, requires JSON `data:` events and one terminating
  `data: [DONE]`, and carries the first observed model with the final observed
  usage. The focused provider-free regression passed: 2 tests, 0.397 s. It
  covers a real raw-stream shape, malformed event JSON, a missing terminator,
  and final-usage accounting without summing intermediate usage events.
- 2026-09-05 — Updated the synthetic real-SSE regression to use CRLF framing,
  an `event:` field, and one JSON payload split over two `data:` fields. The
  parser now dispatches only at a blank line and joins the data fields with a
  newline, per the SSE framing contract. Focused result: 2 tests, 0.475 s,
  passed. Full scoped result: `python3 -m unittest
  tools.tests.test_target_contract_probe` — 70 tests, 49.525 s, passed.
- 2026-09-05 — Added final-usage nested token-detail coverage and two integrity
  regressions. Before their parser change, the focused malformed/incomplete
  test was red with 2 failures: an invalid mid-stream usage object and a
  conflicting later model were silently ignored when a later event looked
  valid. This is now treated as a separate fail-closed repair requirement.
- 2026-09-05 — Tightened event validation: every supplied model must be a
  non-empty string equal to any earlier model, and every non-null supplied
  usage object must satisfy the ordinary counter contract before it can become
  the final observation. Nested final usage details are retained verbatim.
  Focused result: 2 tests, 0.433 s, passed.
- 2026-09-05 — Added SSE framing edge coverage for a literal U+2028 within a
  JSON content string and for `data:  [DONE]`. The first implementation was
  red (one JSON parse error and one accepted over-spaced terminator), because
  `str.splitlines()` recognizes extra Unicode separators and `lstrip()` removed
  too much. The parser now splits only CR/LF/CRLF and removes exactly one
  optional space after `data:`. Focused result: 2 tests, 0.412 s, passed.
  Final scoped result: `python3 -m unittest tools.tests.test_target_contract_probe`
  — 70 tests, 49.759 s, passed.

## Decision log

- 2026-09-05 — Parse an SSE response only when its admitted filename selects
  that format. Require complete, valid event data and reconcile its final
  observed model and usage with the completion journal. Retain the ordinary
  JSON reader unchanged for `.res.json.gz` captures.

## Open questions

None.
