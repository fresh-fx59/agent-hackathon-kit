# Scoped task: full-corpus admission for the approved monitored profile

## Problem and scope

The full-run path rejects the schema2 monitored target profile already used by
qualification. It also requires positive aggregate caps in both admission and
controller code. The operator approved partial and full target runs without
aggregate call/cost/wall caps. Repair this compatibility gap without bypassing
admission, receipt verification, identity checks, or the existing four gates.
This task follows version/finalization and lifecycle supervision repairs.

## Required behavior

- Accept the exact validated schema2 monitored profile through the shared
  run-manifest validator; retain schema1 finite compatibility.
- Bind the selected explicit package version and canonical digest, settings,
  runner/driver/proxy/hook/supervision helpers, gate hashes and installed Qwen
  identity consistently from qualification through full execution.
- Require a fresh accepted target qualification and matching harness evidence.
  Historical field names such as skill_v44_sha256 cannot silently select v44;
  migrate schemas deliberately or document/test the legacy label mapping.
- Represent absent aggregate limits as null in an explicit monitored budget
  schema. Do not convert null to an arbitrary high number or disable the
  600-second per-request watchdog. Preserve positive finite limits in schema1.
- Carry the monitored mode through admission, controller, proxy, runner and
  terminal audit. No arithmetic/comparisons on null; supervision and integrity
  faults still stop owned execution. Account for every request and response.
- Fresh full-run nonce and sealed input package are required for Winevtx and
  the independent corpus. Record standing user authorization; no repeated
  permission question. Consumed or mismatched manifests remain refused.
- Cold-start runs get only their own source corpus, generic skill and generic
  prompt. No imported prior findings, worklists, checkpoints or pattern cards.
  Include raw-to-rendered provenance as input metadata without treating it as
  incident findings. Final qualification uses one unchanged package digest.

## Acceptance

Provider-free tests exercise actual CLI/controller boundaries, not only helper
functions: valid finite and monitored admission; wrong package/settings/hook
identity; stale or unaccepted target/harness receipt; nonce replay; missing
supervision; nullable budgets reaching runner/proxy without artificial caps;
terminal validation on a full-corpus fixture. Secret/proxy/provider tripwires
must remain untouched on every invalid case. Existing admission/controller and
manifest suites remain green. A standard report records failures and repairs.
One scoped code review follows implementation; no live model run before these
checks pass. This task does not change Sherlock runtime prose or scripts.

## Timeline

- 2026-09-05 — Read paid-admission.py, bench-controller.sh::run_fresh and the
  runner's full admission path. Confirmed schema1-only profile/budget checks and
  strictly positive aggregate limits. Wrote this narrow compatibility spec;
  implementation and final critique are pending.

## Final critique rulings

Codex gpt-5.6-sol low reviewed this ready spec on 2026-09-05. Accepted the
following precision requirements; raw verdict is in run-reports.

- Monitored full budget schema2 has exactly the existing BUDGET_KEYS plus
  execution_mode. execution_mode is operator_monitored; max_upstream_attempts,
  max_request_bytes (aggregate bytes), max_wall_seconds and
  max_consecutive_provider_failures are null. Remaining numeric fields are
  positive integers; request_timeout_ms is 600000. Provider failure policy is
  the existing sealed lane guard plus root diagnosis, not a new aggregate cap.
  Finite schema1 remains unchanged. Reserve per-request estimates and retain
  accounting even when aggregate authorization is unlimited.
- Target receipt freshness remains its existing checked created_at <= launch
  time < expires_at, maximum24h for provider-pinned identity and30min for aliases.
  Full action expiry cannot exceed target expiry. Harness evidence has no
  invented wall-age expiry: exact implementation/input bindings and a newly
  qualified matching revision establish applicability. Nonces are fresh per
  action and never reused; audit uses the sealed launch time so expiry while
  a healthy full investigation runs does not retroactively invalidate launch.
- Supervision requirements are exactly the reviewed lifecycle-supervisor spec:
  observation<=60s, independent guardian check<=1s, verified owned termination,
  terminal fault on missing/stale/wrong-identity evidence. Bind that spec's
  helper/settings hashes and trace evidence; do not invent a second heartbeat.
- Cold start requires a new workspace, HOME/QWEN_HOME, no resumed session,
  no ambient skill/memory/history files, and an explicit readable-input manifest.
  Provider-side prompt caching is allowed only when it does not inject content;
  local corpus/report/checkpoint caches from prior cases are excluded.
- Each attempted upstream request needs a durable terminal outcome including
  success, timeout, transport/malformed-response failure, cancellation or
  interruption. A missing response is recorded as such, never synthesized.
  Terminal audit reconciles attempts, outcomes, captures and accounting; an
  unmatched attempt prevents acceptance and triggers investigation.
