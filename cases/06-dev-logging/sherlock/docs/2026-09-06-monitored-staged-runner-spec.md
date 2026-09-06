# Scoped task: profile-bound staged monitored runner

## Problem and evidence

The v45 runtime deliberately ends each `triage`, `draft`, and optional `repair`
stage after `checkpoint.py handoff`. Its final message tells an interactive user
to run `/clear`, reload `/sherlock`, and resume from the durable `work/`
checkpoint. A headless `qwen -p` run cannot perform that protocol. Subscription
qualification r6 therefore completed triage correctly, returned the handoff
block, and was then classified as a final report; the lifecycle itself passed
with 21 expected tools, 20 completed tools, one proven pre-execution rejection,
one foreground child, and ten nested dispatch records.

This is a harness selection defect. The immutable v45 package already carries
the intended protocol, and `run-bench.sh` already has the tested PTY driver that
performs it. Do not create v46, change v45, teach the model to ignore its stage
boundary, resume the cleared conversation, or add a second stage scheduler.

The current integration makes the wrong selection in three places:

- `target-contract-probe.py` publishes `interactive.enabled: false` in the
  target profile;
- selected harness qualification explicitly exports
  `SHERLOCK_INTERACTIVE=0` while preserving that profile; and
- controlled `run-bench.sh` accepts an ambient `SHERLOCK_INTERACTIVE` value
  without binding it to the sealed target profile.

The existing interactive driver is close but not yet valid for operator
monitored execution. It interprets a zero stage budget as an immediate
`STAGE_TIMEOUT`, even though monitored execution has no aggregate time limit.
Its legacy clear check also accepts any completed two-message request. A
foreground child can produce the same count, so that is not proof that the
parent session was cleared. Finally, the interactive target-probe branch must
consume the same signed one-use launch-start authorization as the headless
branch before Qwen can contact the provider.

## Chosen boundary

Use the existing `run_qwen_interactive` and `measure/interactive-drive.py` path
for v45 controlled target, selected-subscription, and full monitored runs. The
sealed target profile is the sole authority for this choice:

1. A newly prepared v45 target profile says `interactive.enabled: true`.
2. Selected qualification preserves that exact field when it rewrites only the
   subscription provider identity.
3. The controlled runner reads `interactive.enabled` from the trace-local
   profile and derives its branch from that value. If an ambient
   `SHERLOCK_INTERACTIVE` is present and disagrees, refuse before Qwen or
   provider contact. Uncontrolled legacy use may retain its existing explicit
   environment switch.
4. Profile, input identity, action authorization, launch HMAC, and terminal
   manifest already bind the target-profile bytes. No unrecorded stage-mode
   flag may override them.

The PTY driver starts Qwen once and uses the runtime's durable checkpoint as the
only state that crosses stages. At every complete or partial handoff it waits
for the current turn to become idle, types `/clear`, reloads `/sherlock`, and
types the fresh `checkpoint.py reseed-line`. It must never use Qwen `--resume`
for a stage transition. The report remains `work/report.md`; interactive
`out.json` truthfully carries an empty result plus driver events and never
copies the report into a fabricated assistant response.

## Monitored timing and supervision

In operator monitored mode, pass stage budget `0` and define it as no automatic
stage deadline. Implement the wait loop without computing or comparing a zero
deadline. Do not replace it with a large number. Existing manual root
observations, the 60-second lifecycle freshness check, guardian ownership,
request watchdog, exact hook accounting, and semantic no-progress boundary
gate remain active. Finite legacy runs retain their positive stage budget and
existing `STAGE_TIMEOUT` behavior.

The outer monitored `SHERLOCK_TIMEOUT=0` continues to mean no aggregate runner
deadline. No request, token, tool, session, stage, or wall aggregate cap is
invented for the monitored path.

## Exact parent-clear proof

The monitored driver must prove a parent reset from the external lifecycle
journal, not infer it from a provider message count. Installed Qwen 0.22 source
shows that `/clear` calls `startChat(undefined, "clear")`, which emits a
`SessionStart` hook. The first PTY capture then observed a `SessionStart` whose
retained raw input had `source: "clear"` and a session ID different from the
preceding root `UserPromptSubmit`. This is the reset authority; a later request
count alone is not.

Before typing `/clear`, the driver records the latest accepted root
`UserPromptSubmit` event and its session ID. It cannot derive this anchor later:
the skill reload itself becomes an intervening prompt in the new session. The
driver then requires exactly one new, ordered `SessionStart(source="clear")`
event for the same workspace and with a different session ID. Only after
accepting that event may it type `/sherlock`; it requires the next
`UserPromptSubmit` in the new session to retain `submitted_prompt: "/sherlock"`
and the installed client's expanded skill prompt. It then types the exact fresh
`checkpoint.py reseed-line` and requires the following `UserPromptSubmit` to
use that same new session ID with `prompt` and `submitted_prompt` both equal to
the reseed line. Missing, malformed, replayed, out-of-order, same-session,
wrong-source, wrong-workspace, wrong-command, or wrong-reseed evidence is
`CLEAR_NOT_EFFECTIVE` and terminates the segment.

The lifecycle supervisor retains exact raw `SessionStart` input and a canonical
journal projection, and the independent terminal auditor reconstructs and
binds that projection to the signed receipt. Initial startup events may be
recorded but cannot satisfy a clear transition. A clear event is single-use: a
later stage cannot reuse its session ID, sequence, or raw input hash.

### Qwen autonomous continuation between skill reload and reseed

Qwen 0.22 may emit one or more `UserPromptSubmit` hooks while its `/sherlock`
turn continues, before the driver has a real reseed submission to observe. The
installed client derives `submitted_prompt` only from a non-empty user query;
the retained continuation inputs observed in the failed 2026-09-06 driver run
therefore have the fresh session ID, `prompt: ""`, and no `submitted_prompt`
field. The proof may wait past only that exact raw shape. It must not infer a
reseed from it, and any other post-skill event — including a different session,
an empty present `submitted_prompt`, or a nonmatching submitted prompt — stays
`CLEAR_NOT_EFFECTIVE`. Completion still requires the first non-continuation
event to contain both exact reseed values in the fresh session.

The proof source is the controller-created observer directory already passed to
the child as `SHERLOCK_OBSERVER_DIR`. The driver treats it only as live
evidence; the independently authenticated lifecycle terminal audit remains the
authority over the journal. Child dispatch cannot satisfy this check. The exact
post-clear reseed projection must remain an explicit red acceptance test until
the in-progress installed Qwen 0.22 capture observes it; no implementation may
fall back to the known-ambiguous two-message heuristic.

Legacy non-monitored interactive runs may retain the existing ledger-based
diagnostic. It must not be labelled exact parent-reset proof.

## Launch and terminal parity

Before `interactive-drive.py` starts Qwen in target-probe mode, invoke
`target-contract-probe.py verify-launch --record-start` exactly once, with the
same sealed trace, approval, and nonce authority as the headless path. Refusal
or replay stops before the target child and provider contact.

All existing monitored evidence remains in one segment across the cleared Qwen
sessions: exact request and response bodies, proxy ledger, tool expectations,
Pre/Post/batch and subagent journals, manual observations, guardian state,
interactive transcript, driver events, checkpoint history, handoff blocks,
gates, report, signed lifecycle receipt, controller receipt, validity record,
and trace manifest. The terminal validator judges the target-written
`work/report.md` and fresh gates. It accepts the interactive `out.json` empty
result only as the already documented truthful driver record, never as the
report itself.

## Acceptance tests

- Preparing a monitored v45 target produces a schema-2 profile with
  `interactive.enabled: true`; its manifest, selected profile, input identity,
  launch, admission, and terminal bindings preserve the same bytes.
- Controlled target, selected-subscription, and full monitored fixtures select
  the interactive branch from their trace-local profile. A mismatching ambient
  value refuses before the mock provider counter or Qwen child advances.
- The target-probe interactive fixture consumes one launch-start authorization
  before Qwen. Missing, changed, or reused authorization produces zero contact.
- With stage budget zero, a fake target can remain in a stage past an arbitrary
  test interval without `STAGE_TIMEOUT`; a positive finite budget retains the
  existing timeout regression.
- A PTY fixture advances `triage -> draft -> repair/done` only through real
  checkpoint handoffs, performs `/clear` and `/sherlock` between stages, retains
  one `work/` tree, and never invokes `--resume`.
- An installed-Qwen 0.22 loopback capture proves the ordered parent
  `UserPromptSubmit -> SessionStart(source=clear) -> /sherlock UserPromptSubmit
  -> reseed UserPromptSubmit` transition used by the clear check. A child
  two-message call, startup `SessionStart`, same-session reseed, missing root
  event, wrong command, wrong reseed, or stale event cannot satisfy it.
- Interactive completion leaves an empty result in `out.json`, preserves the
  full transcript/events, and passes target audit only when `work/report.md`,
  all four fresh gates, lifecycle receipt, and controller verdict pass.
- Existing headless legacy, lifecycle, proxy, target-probe, harness,
  admission, runner, and terminal-auditor regressions remain green. No paid
  provider call is needed for implementation acceptance.

## Timeline

- 2026-09-06: Added five focused reset/deadline regressions and ran
  `python3 -m unittest measure.tests.test_monitored_clear_proof`; it failed
  with eight expected `AttributeError`s because the exact anchor, lifecycle
  proof, and zero-budget APIs did not exist. This establishes the red state
  before the driver implementation.
- 2026-09-06: Implemented strict retained-hook decoding, an exact pre-clear
  anchor, ordered `SessionStart(clear)` → `/sherlock` → reseed reconstruction,
  and a true `None` deadline for stage budget zero. The same focused command
  passed 5 tests in 0.008s; `py_compile` also passed. Provider message counts
  are now excluded from the monitored proof path.
- 2026-09-06: Extended the existing lane-wiring acceptance and ran it. Its
  legacy checks passed, while five new checks failed exactly on the missing
  monitored profile derivation, unbounded stage setting, lifecycle proof
  arguments, target launch-start consumption, and selected interactive flag.
- 2026-09-06: Bound the monitored runner branch to the sealed schema-2 profile,
  rejected an ambient disagreement, selected stage budget zero, passed the
  lifecycle observer/nonce to the driver, and added target `--record-start`
  parity. Selected subscription now requests interactive mode. Both shell
  syntax checks and all lane-wiring assertions passed.
- 2026-09-06: Focused integration verification passed: lifecycle supervisor
  41 tests in 0.642s, full monitored runner/auditor 15 tests in 4.655s,
  exact reset proof 5 tests in 0.008s, and every interactive lane-wiring
  assertion. The terminal auditor independently rejects a re-signed
  SessionStart journal whose projection differs from its retained raw input.
- 2026-09-06: The first combined target-probe run exposed a real ownership
  regression alongside two obsolete headless fixtures: on outer `SIGTERM`,
  the PTY Qwen child ignored TERM and survived as `Ss+` because `forkpty`
  placed it outside the controller's process group and the driver had no TERM
  cleanup handler. The unchanged stubborn-child regression remained red; this
  is a required runner fix, not a fixture adjustment.

- 2026-09-06 — Read r6's failure boundary, v45's immutable stage contract,
  `run_qwen_interactive`, `interactive-drive.py`, profile preparation, selected
  qualification, target audit, and controlled environment allowlist. The
  existing PTY driver already owns the required checkpoint protocol and
  evidence artifacts; the smallest repair is profile-bound selection plus the
  monitored deadline, parent-clear proof, and launch-start parity above.
- 2026-09-06 — Installed Qwen 0.22 source established that `/clear` invokes
  `startChat(undefined, "clear")` and emits `SessionStart`; the first actual PTY
  capture observed `source: "clear"` with a new session ID after the prior root
  prompt. Replaced the ambiguous message-count proposal with this journal-bound
  reset authority. The exact post-clear reseed event is still being captured.
- 2026-09-06 — Installed Qwen 0.22 PTY capture r4 completed the reset chain:
  startup and initial prompt used one session; `/clear` emitted
  `SessionStart(source="clear")` with a new session; `/sherlock` emitted a
  `UserPromptSubmit` whose submitted form was the slash command; and the exact
  reseed line emitted the following `UserPromptSubmit` with matching prompt and
  submitted-prompt fields in that new session. Five raw loopback request bodies
  had message counts 2, 4, 2, 4, and 4, independently confirming that count is
  not reset authority.
- 2026-09-06 — The required single Codex gpt-5.6-sol medium critique returned
  `PASS` with no findings after inspecting the ready contract and current
  integration. The retained input SHA-256 is
  `a8d6db3dcb351ec3ce38370f5f1864d6105ca8522082942661dbcbe3f89f229d`;
  the structured verdict SHA-256 is
  `68f64937a7e8eb41c09b3f8de639063b0808367c9dc3234da1a73dcbb6cc6d98`.
  The r4 reseed capture completed while the critique ran, and the exact observed
  `/sherlock` then reseed ordering above is the implementation authority.
- 2026-09-06 — Added the first two lifecycle regressions before production
  changes. The 41-test supervisor suite failed exactly once in 0.622s because a
  valid installed-client `SessionStart(source="clear")` was denied as an unknown
  phase; the malformed-source case already failed closed and retained its raw
  input through the generic hook-fault journal. This isolates the missing
  accepted-event integration.
- 2026-09-06 — The actual installed-Qwen monitored clear fixture passed against
  lifecycle helper `b67790b700d0406aa0909e799b0672b4be8a0ab45088e0400b40b3d3032e4d2c`.
  It captured the pre-clear anchor, the new `SessionStart(source="clear")`, the
  slash-command boundary, and the exact reseed boundary through the production
  proof functions. Evidence is retained in
  `/tmp/qwen-monitored-clear-reseed-20260906-r2` on the qualified host.
- 2026-09-06 — The first unchanged explicit-stop regression remained red after
  merely adding PTY cleanup: the stubborn Qwen process survived as `Ss+`. The
  next run exposed the separate `timeout 0` boundary (`timeout_command[@]:
  unbound variable` in the first empty-array implementation). The final repair
  installs a setup-safe TERM handler before `forkpty`, retains the forkpty PGID,
  always kills that group after the TERM grace even if its leader was reaped,
  ignores duplicate TERM during cleanup, and replaces the zero-timeout wrapper
  with exec-transparent `env`; finite runs retain GNU `timeout`. The original
  regression then passed in 2.623s.
- 2026-09-06 — The full selected composition initially refused the honest path
  because `prepare_selected` did not propagate the newly required, already
  verified `interactive_driver_sha256`. Propagating that one source field made
  the honest controller → runner → proxy PTY composition pass in 24.772s; its
  preceding tampered-digest attempt still reached neither Qwen nor the loopback
  provider.
- 2026-09-06 — Final provider-free verification passed: lifecycle supervisor
  41 tests in 0.701s; monitored runner/auditor 15 in 24.986s; selected package
  12 in 3.803s; harness qualification 18 in 34.620s; reset proof 5 in 0.009s;
  and all interactive lane-wiring assertions. Root independently ran the final
  target-probe suite, including both PTY admission-window fixtures and the
  unchanged stubborn-Qwen stop regression: 76 tests passed in 100.969s. Shell
  syntax, Python compilation, and `git diff --check` also passed.
