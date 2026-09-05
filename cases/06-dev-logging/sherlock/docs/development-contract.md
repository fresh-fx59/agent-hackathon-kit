# Sherlock development contract

## Status and current authority

The operator approved this workflow on 2026-09-05, including autonomous partial
and full paid qualification. Record fresh run specifications and preserve all
existing gates. Do not ask for paid permission again within this authorized work.
The approved detailed design is in Personal OS:
`docs/superpowers/specs/2026-09-05-sherlock-development-rules-brainstorm.md`.

These rules are binding; mechanical enforcement is not claimed implemented until
its task is verified in the execution ledger. Existing v44 packages are frozen.
The first shipped runtime update will be v45; the same version must never name
two different runnable packages. Developer reports alone do not bump the runtime.

## Run evidence contract

Every experimental attempt has a unique ID, purpose/spec, hypothesis, input and
package identity, commands/request references, time range, terminal result,
actual process/gate exits, evidence pointers, findings/unknowns and next step.
A compact provider-free receipt obeys the same minimum contract; model and usage
fields say not applicable. A model run additionally preserves raw requests and
responses, decoded events, tool outputs, usage and artifact lifecycle evidence.

Use a readable report and machine-readable summary backed by append-only events.
Report indexes are derived views bound to source hash and parser/schema revision.
Correct by appending; never rewrite original failed evidence into acceptance.
A corpus report describes logs; the developer report describes Sherlock's run.

Before accepting a run, reconcile every captured call/tool/gate by identity and
terminal outcome. Required artifacts must exist with expected hashes. Detect
stale summaries, truncation, missing events and parser errors explicitly.
No capture of hidden model reasoning is promised. Credentials are never evidence.

## Supervisor and failure policy

Observe at least every 60 seconds with monotonic sequence/time and owned process
identity. Missing supervision blocks new dispatch and follows the tested owned
process stop path. Log the gap permanently; no later recovery can claim continuous
monitoring for the earlier segment. This automatic gate is pending implementation.

Draft gate failures pause unrelated investigation for a specific repair attempt.
Hard integrity/configuration failures stop the run. Progress means meaningful new
evidence, coverage, or successful repair; detect repeated completed actions with
unchanged evidence before declaring a loop. Keep the 600-second per-request
watchdog. Do not add an aggregate runtime, call or spending ceiling.

## Review and portability

One scoped spec/quality review per coherent change. Root validates the final diff
and runs required checks. Use a final bounded cross-model critique as required by
the vault; do not create redundant panels. Record review rulings and limitations.

Preserve standalone operation, stdlib dependency rules and the existing Russian
report contract. Do not silently translate parser-bound headings. Preserve existing
language conventions until a separately scoped compatibility change is justified.
Run `bash scripts/verify.sh` from the repository root before pushing code.
