# Controlled-run Lifecycle Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve registered generated evidence and stop controlled execution before provider dispatch when root observation, hook evidence, or owned supervision becomes invalid.

**Architecture:** A single stdlib helper owns observer publication, permanent faults, hook receipts, and content-addressed evidence snapshots under a fresh trace-local directory. The proxy calls its fail-closed dispatch check, while the controller starts an independent one-second guardian and supplies Qwen command hooks; the probe seals all new paths and hashes into the launch contract.

**Tech Stack:** Python 3 stdlib, POSIX file locking and atomic rename, Bash controller wiring, Python `unittest`, pinned Qwen 0.22.0 smoke on the existing provider-free remote fixture.

**Spec:** `docs/2026-09-05-lifecycle-supervisor-spec.md`

## Global Constraints

- Keep the existing 600-second request watchdog and add no aggregate cost, call, or wall ceiling.
- Keep observer state outside the Qwen work tree, fresh per launch, mode `0700`, and bound to the launch nonce and boot identity.
- Observation freshness is at most 60 seconds; guardian checks are at most one second apart.
- A faulted segment is terminal; retries require a fresh launch nonce and process tree.
- Snapshot every manifest-referenced ledger without sampling or a fixed file-count cutoff.
- Preserve exact pre-tool bytes; reject links, traversal, races, duplicate conflicts, and missing registered paths.
- Keep v45, gates, oracle, and provider routes unchanged; make no paid provider calls.

---

### Task 1: Lifecycle state and evidence helper

**Files:**
- Create: `eval/bench/lifecycle-supervisor.py`
- Create: `eval/bench/test_lifecycle_supervisor.py`

**Interfaces:**
- Produces: `init_segment`, `publish_observation`, `check_dispatch`, `handle_hook`, `run_guardian`, and matching CLI subcommands.
- Stores: `identity.json`, `current-observation.json`, append-only journals, `registry.json`, `objects/<sha256>`, and first `fault.json`.

- [ ] Write failing observer tests for absent, stale, wrong-nonce, future, and regressed records plus a healthy record with no aggregate lifetime limit.
- [ ] Run the observer tests and confirm the helper is absent.
- [ ] Implement atomic segment initialization, capability-bound observation publication, strict check state, and terminal faults.
- [ ] Run the observer tests to green.
- [ ] Write failing hook tests for pre-snapshot recovery, post-loss fault, same-count rename, duplicate idempotence/conflict, races, and unmatched pairs.
- [ ] Implement manifest-driven path discovery, verified reads, object publication, serialized hook receipts, registration, pairing, and explicit blocking JSON.
- [ ] Run all helper tests to green and compile the helper.

### Task 2: Proxy pre-dispatch enforcement

**Files:**
- Modify: `measure/upstream-log-proxy.py`
- Modify: `measure/tests/test_upstream_log_proxy.py`

**Interfaces:**
- Consumes: lifecycle helper `check_dispatch` using sealed observer directory, nonce, boot identity, and capability.
- Produces: zero-contact refusal and durable fault before every upstream dispatch.

- [ ] Add failing dummy-upstream tests for missing, stale, wrong-nonce, future, and regressed observation records and unmatched hook pairs.
- [ ] Wire the helper check immediately before network dispatch and publish permanent refusal evidence before replying.
- [ ] Run focused proxy accounting, request-watchdog, and new lifecycle tests.

### Task 3: Controller guardian and Qwen hooks

**Files:**
- Modify: `eval/bench/bench-controller.sh`
- Modify: `tools/tests/test_bench_controller.sh`
- Modify: `tools/tests/test_target_contract_probe.py`

**Interfaces:**
- Consumes: lifecycle helper guardian and hook commands plus controller boot/start-ticks/process-group ownership.
- Produces: independent one-second guardian, Qwen PreToolUse/PostToolUse/PostToolUseFailure settings, guardian-death detection, and existing verified TERM/KILL cleanup.

- [ ] Add failing owned-process tests proving expiry stops controller/Qwen while an unrelated process survives and guardian death invalidates the run.
- [ ] Start and monitor the guardian from the controller; route guardian TERM through the existing owned-process cleanup path and record observed latency/outcome.
- [ ] Merge command hooks into the pinned Qwen settings without removing Stop and make hook errors emit installed-runtime denial plus `continue:false`.
- [ ] Run controller ownership and target-probe regressions.

### Task 4: Sealed launch identity

**Files:**
- Modify: `eval/bench/target-contract-probe.py`
- Modify: `tools/tests/test_target_contract_probe.py`

**Interfaces:**
- Produces: fresh trace observer segment and sealed observer path, helper hash, settings hash, nonce, nullable aggregate authorization, and predecessor metadata.

- [ ] Add failing prepare/run tests for observer reuse, identity/hash substitution, and absence before launch.
- [ ] Initialize the segment after launch authorization and seal all lifecycle identities before proxy/controller entry.
- [ ] Run the complete helper and target-probe suites.

### Task 5: Provider-free pinned runtime proof and report

**Files:**
- Create: `docs/2026-09-05-lifecycle-supervisor-report.md`

**Interfaces:**
- Consumes: installed Qwen 0.22.0 absolute binary on the existing remote host.
- Produces: retained provider-free trace proving matching pre/post tool IDs and visible intentional-hook blocking.

- [ ] Run Qwen as `claude-developer` with isolated `HOME` and `QWEN_HOME` against the local mock provider and fake corpus.
- [ ] Verify matching hook pairs, external snapshots, and zero paid requests; inject a hook fault and verify explicit blocking.
- [ ] Run affected proxy, controller, replay, accounting, request-watchdog, helper, and target-probe suites.
- [ ] Record each observed result and known source-bound limitations in the lifecycle report.
- [ ] Run `py_compile`, `bash -n`, and `git diff --check`.
