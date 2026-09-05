# Scoped task: preserve evidence and make final validation observable

## Objective and cause

Deliver a target-written report that can satisfy the unchanged four gates and
fixture oracle. r15 failed all gates. Exact visible commands /90 and /93 in
out.json wrapped validators with echo/pipelines; results /91 and /94 say outer
is_error:false while nested validators fail. /59→/60 created worklist.tsv, /65
shows it present, /97 removes it and its manifest, /98 shows terminal absence.

Probe prompt requires exactly four finding headings, then instructs “leave only
work/report.md”. Canonical passing fixture includes additional corporate sections
and retained evidence. Resolve this instructional ambiguity without changing
expected answers, reportcheck/citecheck/statecheck/triagecheck, or the oracle.

## Requirements

1. After version-gate integration, create v45 from frozen v44. Make all runtime
   prose/script updates in this reviewed change set, register v45 before any run
   of it. Do not modify v44. Developer tests may use isolated synthetic packages.
2. Fix probe prompt to explicitly allow required corporate scaffold alongside the
   four finding sections; state field syntax accepted by the existing oracle
   without supplying corpus values. Preserve required generated evidence; report
   is the deliverable, not the only remaining file. Select shipped version
   through the existing bound selector instead of prose hardcoded v44.
3. Add a portable stdlib finalize helper that executes the four unchanged shipped
   gates with correct arguments, captures exact argv/stdout/stderr/exits and
   source/package/report/evidence identity, and returns nonzero unless every
   existing gate is clean. Do not truncate saved output or silently forgive
   malformed/non-JSON/crashed validators. It must never delete evidence.
4. Runtime instructions use the helper to validate and repair the report, keeping
   all evidence. Draft failure triggers focused repair; no gate bypass or final
   success claim. Standalone invocation and the Stop lifecycle must exercise the
   same underlying gates, with real outputs visible and preserved.
5. Defer pre/post-tool lifecycle snapshots and automatic integrity stopping to
   the next independent lifecycle/supervision task. They remain prerequisites
   before paid contact; this task makes no claim to prevent arbitrary shell
   deletion. Keep finalization helper focused on non-destructive validation.
6. Replace Winevtx-specific instructional example values in the new package with
   generic examples; inspect all new runtime prose for embedded expected answers.
   Do not copy prior findings, checkpoints or learned pattern cards into a fresh
   corpus run. Preserve existing Russian parser-bound report literals.

## Verification

First reproduce masked nested exit and lost required evidence on throwaway inputs.
Prove finalize returns failure and retains full outputs/evidence on each gate
failure, exception and successful path. Prove a draft can be repaired and final
gates all rerun. Run existing gate/report/Stop regressions. A direct helper run must
preserve all files, emit all four exact gate outcomes and fail if any exit or
blocking count fails. Stop behavior may invoke the helper once per Stop event,
with explicit recursion guard and existing bounded deadline handling; failures
return blocking output, never silently allow termination. One scoped review; report each outcome before target qualification.

## Boundary

This is evidence/finalization repair. Supervision heartbeat and owned-process
cleanup enforcement are the next scoped task, required before a paid run. No paid
contact in this task. Corpus knowledge/oracle changes are excluded.

## Research

Qwen official hooks documentation, retrieved2026-09-05:
https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/ . It documents
PreToolUse, PostToolUse, PostToolUseFailure and Stop; command exit2 blocks while
other nonzero statuses may be nonblocking. Verify behavior on pinned installed
CLI before relying on it. Remote installed chunks contain these hook events;
skill v44 frontmatter declares Stop although captured settings have no global
hooks key. Absence of global hooks is not evidence of absent skill hooks.

## Exact integration contract and critique rulings

Codex gpt-5.6-sol low review,2026-09-05: accepted scope split and explicit
interfaces. New package directory is `skills/v45`; register through the version
gate helper, then use the dedicated package selector and bound snapshot. The
registry/package identity must pass before execution; old v44 unchanged.

From the workspace root, using `sys.executable` and selected package tools:

- `reportcheck.py work/report.md --json`
- `citecheck.py work/report.md --corpus corpus --require-quote --ledger work/worklist.tsv --json`
- `statecheck.py --corpus corpus --report work/report.md --json`
- `triagecheck.py --worklist work/worklist.tsv --rules work/rules.tsv --corpus corpus --json`

Use the same both-signals-clean interpretation as run-bench.sh: exit0 AND parsed
blocking0; missing or malformed required output is a failure. Preserve each
validator's own schema and content unchanged. Unique attempt directory under
`work/validation/`, exclusive creation, prevents overwritten failed evidence.
UTF-8 JSON metadata: schema1, attempt_id, started_at, finished_at, package tree
SHA256, report/evidence relative paths and SHA256 (explicit missing), per-gate
argv/exit_code/tool_sha256/stdout artifact/stderr artifact, aggregate verdict.
Raw stdout/stderr bytes are saved exactly; decoding failures are explicit.
Capture inputs before and after; changes during validation invalidate that attempt.

The prompt may name the existing contract field names and required Russian
headings, and use empty schema placeholders. No addresses, accounts, services,
timestamps, fixture rows or precomputed findings may enter it. Gate/oracle hashes
remain unchanged across this task. Separate lifecycle spec must define snapshot
location/session ownership, retention and hook failures before implementation.

## Compatibility amendment from candidate review

Root found a concrete existing-flow regression after the scoped Claude PASS:
SKILL.md still requires per-host worklist files when logmap discovers several
hosts; the new finalizer hardcodes worklist.tsv. Generic corpus support requires
preserving that established flow. Both standalone and Stop finalization must
select the exact active-manifest ledgers with the existing strict path and
record-identity checks. Preserve the composed gate input inside the unique
validation attempt, with identities for every source ledger. Do not change any
gate semantics or erase source worklists. Single-worklist behavior stays the same.
Tests must execute all four actual gates for a multi-host fixture and reject
missing, duplicate or outside-work ledgers. This is a necessary compatibility
correction before v45 is registered, not a separate feature or paid retry.
