## Task 4 — paid target probe state machine and receipt

Base: `177a193ebc2025d098704239f1b6989b17a37c8b`.

### TDD evidence

RED, before the production module existed:

```text
$ python3 cases/06-dev-logging/sherlock/tools/tests/test_target_contract_probe.py
Ran 7 tests — FAILED (errors=7)
FileNotFoundError: target-contract-probe.py
```

The test's tripwire list was untouched; no secret, proxy, runner, provider, network,
run, or corpus action occurred.

GREEN after implementation:

```text
$ python3 cases/06-dev-logging/sherlock/tools/tests/test_target_contract_probe.py
Ran 9 tests — OK

$ python3 cases/06-dev-logging/sherlock/tools/tests/test_target_contract_fixture.py
Ran 20 tests — OK

$ python3 cases/06-dev-logging/sherlock/measure/tests/test_probe_dispatch_budget.py
Ran 23 tests — OK

$ python3 -m py_compile ...target-contract-probe.py ...test_target_contract_probe.py
$ git diff --check
exit 0
```

### Delivered boundary

- `prepare` rejects an existing root, snapshots the Task 2 fixture without touching
  its source, emits a Task 1 schema-valid target profile, Task 3 default budget,
  input package, and exact-byte action manifest. It does not resolve `secret_ref`.
- `authorize` strictly parses raw manifest bytes, binds its supplied SHA-256, action,
  expiry, and nonce; `O_EXCL` nonce consumption makes concurrent process replay fail.
- `run` authorizes and verifies all sealed profile/fixture/budget/input bytes before
  secret resolution, proxy start, or runner call; the normal runner callback receives
  the exact profile/fixture/budget and `retries=0`.
- `audit` fails closed on absent/malformed/tampered/symlink artifacts, bad gates,
  oracle, identity, budget, or exit layers; it always records a no-overwrite result.
  A receipt and detached checksum are emitted only after accepted evidence. Billing
  fields are always `null` because this implementation has no provider trust root.

### Assumptions and scope

Task 1 supplies target-profile validation rather than a profile-construction helper;
this task composes the exact validated profile and hashes the current owned tools.
The existing runner has no safe, sealed CLI interface for provider-free integration,
so `run` takes its secret/proxy/runner actions by injection. This preserves the
pre-contact security boundary and lets the production launcher wire the existing
runner without any paid action in tests.

### Provider and secret confirmation

All tests used temporary directories, local test callbacks, and subprocesses only.
No external/provider endpoint was contacted, no real secret was read, and no real
run or corpus artifact was mutated.

### Commit

`4325ed586dee4c4fe8bc0b697d85d23ea3fe1af9`

## Fix round 1 — review regressions

RED on the reviewed commit, before the repair:

```text
$ python3 cases/06-dev-logging/sherlock/tools/tests/test_target_contract_probe.py
Ran 11 tests — FAILED (failures=2)
mutable fixture crossed run() and invoked the secret tripwire
prepare --json exited 2 (unrecognised argument)
```

The review also independently reproduced forged audit acceptance, nonce burn,
unsafe aliases/TOCTOU, incomplete dependency binding, non-strict budget/identity/
exit validation, and incomplete receipt transactions. The fix below treats these as
one sealed-snapshot and fail-closed state-machine boundary.

GREEN after the round-1 repair:

```text
test_target_contract_probe.py: 12 tests — OK
test_target_contract_fixture.py: 20 tests — OK
test_probe_dispatch_budget.py: 23 tests — OK
py_compile and git diff --check: exit 0
```

The repair rejects mutable fixture bytes before nonce/secret use, snapshots sealed
inputs before callbacks, defers nonce consumption until known validation completes,
executes the real fixture oracle and all four bound v44 gates, uses canonical report
fixture evidence instead of fabricated summaries, validates finite Task 3 counters
and strict Task 7 exit layers, sets receipt expiry from audit completion, and adds a
provider-free CLI `prepare --json` plus `run` stub integration proving retries are
disabled. All tests used temporary paths and local stubs; no provider, real secret,
real run, or corpus was accessed.

## Fix round 2 — additional review regressions

RED:

```text
test_target_contract_probe.py: Ran 13 tests — FAILED
an unaware `created_at` plus an unbounded future expiry reached the secret tripwire
```

2026-09-04 RED (round 2, post-review adversarial batch): `python3
cases/06-dev-logging/sherlock/tools/tests/test_target_contract_probe.py` ran 16
tests and failed 3 as intended: `--runner-command /usr/bin/true` was accepted
(exit 0); an undeclared fixture leaf reached the secret tripwire; and a secret
callback rewrote the sealed profile so proxy/runner were reached. All inputs
were temporary localhost-free fixtures; no provider, network, real secret, or
real run/corpus artifact was accessed.

2026-09-04 GREEN (first round-2 repair): the same focused suite ran 16 tests
and passed. Package verification now treats the Task 2 fixture as a closed
tree and revalidates its private snapshot after secret resolution; the CLI no
longer accepts a caller-selected runner. The remaining controlled-controller
integration is being added in the approved runner files before handoff.

2026-09-04 RED (controlled-path seam): the new local-only
`TargetContractProbeControllerTests.test_probe_mode_has_a_separate_sealed_input_parser`
failed against the current controller with `usage: bench-controller.sh [--resume
CONTROLLER_ID]`. This proves no probe dispatch exists yet; the invocation
included a forbidden arbitrary executable only to prove it cannot become a
fallback. No provider endpoint, secret, or real artifact was used.

2026-09-04 GREEN (round-2 controlled-path and dependency pass): Task 4 now
has 17 passing provider-free tests, including regressions for closed fixture
trees, post-secret snapshot substitution, parent symlink reads, forbidden
runner substitution, and controller probe parsing. The repository controller
owns `--target-contract-probe`: it accepts only a sealed input/work pair,
launches the repository `run-bench.sh` path, and permits a transport override
only for `127.0.0.1` under explicit test mode; retries, resume, and upstream
retry are forced to zero. `prepare` emits a corporate-settings artifact using
the corporate-settings owner and binds its digest plus controller, driver,
proxy, oracle, audit, Qwen, skill, gate, and closed-fixture bytes before nonce
use. The audit binds raw ledger bytes and requires per-call sent/returned model
identity and IDs with sealed default limits. Every check remained local,
temporary, and provider/secret-free.

2026-09-04 baseline note: the full controller suite retains its documented
baseline reds in `ControlledRunnerTests`: `test_collision_is_rejected_before_proxy_or_qwen`,
`test_controlled_mode_requires_both_identity_values`,
`test_every_state_row_uses_one_runner_proof_and_qwen_gets_no_caps`, and
`test_strict_proxy_failure_is_terminal_before_qwen`. The first failure records
the fake Qwen preflight capture before controlled-mode validation. This Task 4
round did not modify `run-bench.sh` (empty exact-path diff); the new isolated
probe-controller tests pass 3/3. These baseline reds do not exercise the new
`--target-contract-probe` branch.

2026-09-04 RED (publication transaction): 19 focused Task 4 tests reproduced
two additional failures before repair. A nonce root or receipt parent symlink
redirected publication, and an injected checksum interruption left an accepted
receipt beside a rejected terminal result. Fixtures and interruption hooks were
local only; no provider/network/secret access occurred.

2026-09-04 RED (run terminal): 20 focused tests showed malformed pre-contact
run authorization raised without any `probe-result.json`. This is the final
terminal-result gap exercised with a temporary manifest and tripwire callbacks.

2026-09-04 GREEN (publication and terminal pass): focused Task 4 is now 20/20.
All read, nonce, and no-replace publication paths descriptor-walk directories
with no-follow flags, reject parent symlinks, fsync the published leaf and its
directory, and nonce-root creation uses the same path. A checksum interruption
rolls back the exact receipt leaf before writing one rejected terminal result;
both pre-contact and post-contact run failures now write exactly one terminal
`probe-result.json`. Focused controller probe tests are 3/3, Task 2 is 20/20,
Task 3 is 23/23, compile and diff checks pass. The known four unrelated full
controller baseline reds remain recorded above. No provider/network/real-secret
or real-run/corpus access occurred.

2026-09-04 GREEN (re-review fixture replay): Task 4 is 21/21 after replaying
the forged budget (999), sent-model mismatch, and cross-tree hardlink body
attacks; all reject with no receipt. The nonce directory creation race was
also fixed so concurrent authorization again yields exactly one acceptance and
one `APPROVAL_REPLAYED`. Required focused evidence: controller probe 3/3, Task
2 20/20, Task 3 23/23, Python compile and exact diff check all passed.

2026-09-04 RED (Round 3 fresh review regressions): focused Task 4 was 18
passing / 7 failing subcases (25 tests total) before this repair. The exact
failures were a missing parsed local-transport validator (five assertions), a
proxy callback that changed sealed `target-profile.json` after its final
validation and reached the runner, and `subprocess.TimeoutExpired` escaping
without `probe-result.json`. The alias-TTL assertion already demonstrated the
prepared 30-minute expiry. All cases used temporary fixtures and injected
callbacks only; no provider/network/real secret/real run or corpus was touched.

2026-09-04 RED (Round 3 correlation): 25/26 focused tests passed while a
forged response body (`call_id=other`, returned identity `other`) was accepted
because audit trusted the ledger summary and synthetic exit layers. The replay
also placed a Task 7-shaped local verdict beside the trace to prove that merely
having it did not bind consumption. Local temporary fixture only; zero provider
or secret access.

2026-09-04 GREEN (Round 3): 27/27 Task 4 focused tests now pass. The sealed
local test path validates a parsed exact loopback transport before nonce use,
passes the sealed `target` arm/settings/profile/fixture/budget to the dedicated
repository runner, emits a trace, and runs the real Task 2 oracle plus all four
bound v44 gates. Audit consumes Task 7-shaped `run-verdict.json`, re-hashes raw
request/response trees, correlates each call ID/model/returned identity/usage,
and validates a fresh digest-bound rate snapshot, budget attempts and
overshoot. Alias authorizations are 30 minutes; pinned versions are 24 hours.
Receipt now records fixture/budget/gate/cost/checksum bindings and rolls back
receipt/checksum if its terminal result cannot be published. `prepare` claims
its root with non-replacing mkdir plus durable directory sync rather than
replacing a raced root. Task 2 was 20/20 and Task 3 23/23. All execution was
local temporary/stub data: zero provider/network contact, zero real secret
resolution, and zero real run/corpus mutation.

2026-09-04 RED (Round 4 fresh implementation): added five local regressions
against `0adb038` before changing production code. `test_target_contract_probe.py`
ran 32 tests: 28 pass, 1 failure, 3 errors. Audit accepted a fabricated trace
without a consumed action nonce; it rejects the actual Task 7 `finished` exit
schema; a gate `TimeoutExpired` escaped without `probe-result.json`; and prepare
followed a parent symlink far enough to create `outside/probe` before raising
`NotADirectoryError`. The per-call call-ID/usage forgery regression was already
rejected. Only temporary local directories, callbacks, and fixture bytes were
used; no provider, network, secret, real run, or corpus was accessed.

2026-09-04 GREEN (Round 4 partial focused repair): the fresh regression suite
is 32/32. The production probe no longer has a test-only branch that copies a
canonical report or writes synthetic request/response, ledger, budget, or
verdict artifacts; it enters the ordinary v44 runner. Audit now requires a
durable action-authorization record and nonce token before accepting, consumes
Task 7's `finished` projection with every exit layer, mints a distinct receipt
nonce, sums response usage per call, requires identical body/ledger/budget call
IDs, and recomputes the estimated cost from the authenticated rate snapshot.
Parent creation descriptor-walks before any temporary directory can be created
through a symlink and fsyncs each created parent entry; audit catches unexpected
exceptions and seals one terminal result. Focused controller probe 3/3, Task 2
20/20, Task 3 23/23, Python compile, shell parse, and `git diff --check` passed.
All execution was local, fixture-only and credential-free.
