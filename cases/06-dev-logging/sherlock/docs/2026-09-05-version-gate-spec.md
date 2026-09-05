# Scoped specification: immutable skill versions before model contact

## Goal contribution

Enforce the operator-approved version rule and permit future v45 qualification
without the target-probe code silently hashing or validating v44 instead.
No paid contact in this task. Keep every existing quality gate unchanged.

## Facts and hypothesis

The existing runner already interprets v<number> arms. The target probe hardcodes
v44 in profile, package sealing, validation, and final gate execution. A new
runtime version cannot safely qualify until these identities agree. Existing
hashes bind bytes to one run but do not prevent the same version naming changed
bytes in a later run. A small version registry plus selected-version binding
should close this gap without a new orchestration layer.

## Requirements

1. Add a version registry owned by the developer checkout, recording immutable
   package tree digests. Register the current frozen v44 as baseline, without
   modifying any v44 byte. Registration of an existing version with different
   bytes fails; identical re-registration may be idempotent.
2. Reuse the existing canonical tree hashing/path-safety conventions rather
   than defining an incompatible digest. Reject symlinks, missing/mutated files,
   invalid version names, unregistered versions, and malformed registry data.
   Registry data is excluded from skill package trees and versioned in Git.
   Verify append-only registry history against committed ancestor registrations;
   changing or deleting a previously registered digest must fail, including when
   the edited registry and package agree. Establish an explicit trusted baseline
   for exported checkouts rather than trusting a newly rewritten registry.
3. New version selection must be explicit and bound to preparation/manifest
   identity, validation, runner selection, and gate execution. No unsealed env
   override may substitute another package. Preserve legacy v44 behavior and
   old schema interpretation where safe; do not reinterpret an arm label as a
   package version if the old interface allowed arbitrary labels. Use a dedicated
   package-version selector; `args.arm` remains an arbitrary legacy run label.
4. Before model contact, both direct run-bench and target-probe paths must
   reject a changed registered package. Capture the selected version and digest
   in durable run identity. Prove order with a dummy contact sentinel. Seal a
   verified package snapshot before contact; validation, runner and gates must
   consume that exact snapshot. Detect mutation between checking and copying;
   never validate one tree and execute a mutable different tree.
5. A new version with valid generic changes must run through the selected
   package's actual gate tools, with its tool digests bound. Do not weaken
   gate semantics or fabricate a passing report. Test selection with synthetic
   package copies rather than editing v44.
6. No runtime skill package is changed in this task. The first actual runtime
   update will create v45 in the next scoped task after these checks work.

## File scope

Likely: a small helper/registry, target-contract-probe.py, run-bench.sh,
run-manifest.py only if its existing schema requires it, and focused tests.
Inspect callers before selecting the exact helper interface; record that choice
in the task report. Avoid changing unrelated controller/paid policy behavior.

## Verification sequence

- First write failing tests for mutated same-version prose/script, unsupported
  version selection, and direct/probe contact prevention.
- Implement smallest complete registry and binding integration, then rerun
  focused tests plus existing target-probe/runner identity tests.
- Include valid registration/verification, duplicate/conflicting registration,
  malformed/symlink paths, and a distinct valid version routed consistently.
- One scoped spec/quality review. Record code, tests, limitations and next task
  in a dedicated version-gate report; no paid provider or real secret used.

## Non-goals

Run-report analyzer, semantic report repair, monitoring supervisor, EVTX parser,
corpus-specific expectations, and broad runtime refactoring are separate tasks.

## Final critique ruling

Codex gpt-5.6-sol, low effort, 2026-09-05: three requested safeguards accepted
above: immutable registry history, dedicated version selector, verified snapshot
binding. No operator decision needed; these implement the approved rule.
