# SDD ledger — plan: /Users/a/Documents/projects/personal-os/docs/superpowers/plans/2026-09-03-sherlock-model-aware-paid-admission.md

## Setup

- 2026-09-03: Existing isolation verified: `/Users/a/hack/wt-v42`, branch `tools/sherlock-v42`, linked worktree, no superproject, clean status.
- 2026-09-03: Baseline focused suites: `test_run_manifest.py` 54/54, `test_run_verdict.py` 26/26, `test_run_state.py` 10/10.
- 2026-09-03: `test_bench_controller.sh` has four baseline-red `ControlledRunnerTests`; reproduced the same four failures in `/Users/a/hack/wt-v42-baseline`. Its other 32 tests pass.
- 2026-09-03: Required `codex exec -m gpt-5.6-sol` plan critique skipped after the CLI refused `/tmp` as untrusted; AGENTS.md requires skip on error and forbids substitution.

## Preflight compatibility scan

| Tasks | Producer → consumer or internal consistency | Finding |
|---|---|---|
| 1 | target profile and `input_identity` → 4, 6 | Compatible exact JSON/hash interface. |
| 1, 6 | `run-manifest.py` schema 3 → controller invocation | Task 6 must update every create call before integration tests can pass. |
| 1, 8 | canonical comparison → final verification | Compatible; raw path-only prompt differences use `prompt_path_only`. |
| 2 | fixture builder/oracle files and tests | Internally consistent after plan correction: the semantic predicate comes from the oracle, not a copied gate. |
| 2, 4 | fixture manifest/oracle → probe prepare/audit | Compatible exact artifact hashes. |
| 2, 5 | bounded corpus → free qualification launcher | Compatible; real external execution occurs only in Task 8. |
| 3 | proxy action-budget interface and tests | Internally consistent; reservation precedes `urlopen`, completed usage reconciles later. |
| 3, 4 | proxy budget state → paid probe | Compatible schema 2; Task 4 supplies the exact rate snapshot and limits. |
| 3, 6 | action budget → full runner | Compatible; full budget is separate from probe budget. |
| 4 | prepare/run/audit and tests | Internally consistent, except exit-layer source is delivered by Task 7. |
| 4, 6 | accepted target receipt → full admission | Compatible detached checksum and single-use receipt nonce. |
| 5 | harness matrix/receipt/launcher and tests | Internally consistent; launcher depends on Task 6 controller integration only when Task 8 executes it. |
| 5, 6 | harness receipt → full admission | Compatible shared-digest proof with `proof_scope: harness_only`. |
| 6 | admission helper/controller/runner tests | Internally consistent; paid refusal must occur before health environment creation. |
| 7 | first-failure and exit/billing summary | Internally consistent and supplies Task 4 audit's exit-layer source. |
| 7, 4 | `run-verdict.py` exit layers → probe receipt audit | Dependency order in numbered plan is reversed. |
| 7, 6 | terminal/billing schema → full run result | Compatible nullable provider billing fields. |
| 8 | local suites, matrix, free external acceptance | Internally consistent; remote sync/push is an external side effect and must follow repository policy. |
| 9 | prepare/review/handoff | Internally consistent and stops before secret read or paid contact. |

- Task order ruling: execute `1, 2, 3, 7, 4, 5, 6, 8, 9`, because Task 4's receipt must consume Task 7's sole terminal-summary interface. Cost if wrong: task numbers no longer match chronological commits, but no code is duplicated or built against a temporary exit schema.

## Task results

| Task | Base → head | Implementer | Review | Verification | Result |
|---|---|---|---|---|---|
| 1 — canonical target profile and input comparison | `b85c14f` → `498f58d` | `gpt-5.6-terra`, commits `d36964b`, `4ac7050`, `498f58d` | `gpt-5.6-sol`; FIX (5), FIX (2), PASS | 67/67 focused tests; 23/23 independent adversarial probes; net diff check clean; two-file scope | Complete; no provider contact |
| 2 — deterministic probe fixture and exact oracle | `498f58d` → `bc19505` | `gpt-5.6-terra`; commits `7b7398a`, `b623be4`, `30d2ca3`, `934e0e9`, then fresh implementer `bc19505` | `gpt-5.6-sol`; FIX, FIX, FIX, FIX, PASS | 20/20 focused tests; 3/3 final Setext probes; all 12 gate invocations documented; py_compile/diff clean; exact smoke source-unchanged | Complete after four fix rounds; no provider/network contact |
| 3 — pre-dispatch paid budget at proxy boundary | `bc19505` → `9e66ec8` | `gpt-5.6-terra`; commits `74f3034`, `df9587e`, `e2b81fa`, `9e66ec8` | `gpt-5.6-sol`; FIX, FIX, FIX, PASS | 23/23 focused, 45/45 legacy proxy, 8/8 pre-send; exact post-release SSE replay returns 502 with one durable unknown-usage contact; diff clean | Complete after three fix rounds; localhost only, no provider contact |
| 7 — honest terminal layers and billing vocabulary | `9e66ec8` → `177a193` | `gpt-5.6-terra`; commits `553a763`, `c9b4670`, `28ab184`, `177a193` | `gpt-5.6-sol`; FIX, FIX, FIX, PASS | 17/17 state, 34/34 status, 40/40 verdict, 11/11 gate exits; exact copied-tree tamper/deletion probes; 50-code equality and 21 producers; compile/diff clean | Complete after three fix rounds; local fixtures only, no provider/network contact |
| 4 — paid target probe state machine and receipt | `177a193` → pending final commit | `gpt-5.6-terra`; commits `4325ed5`, `d57b5c5`, `f6b2557`, `0adb038`; fresh Round 5 implementation | `gpt-5.6-sol`; FIX (9), FIX (9), FIX (9), FIX (7 blockers), fresh Round 4 FIX (5 blockers); Round 5 closure | Provider-free normal-path E2E 1/1 (3.847s): sealed Qwen → normal runner → Task 3 proxy → localhost stub → normal report/audit/Task 7. Task 2 fixture 20/20; Task 3 budget 23/23 and lane 13/13; target probe 38/38; target controller 4/4; status 35/35; verdict 41/41; compile/shell/diff clean. Persistent controller 31/31 equals clean baseline; only four documented ControlledRunner baseline failures remain in both trees. | Complete; localhost-only fixture, no provider/network/secret contact |
