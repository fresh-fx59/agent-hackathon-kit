# Controlled-run lifecycle supervisor — development report

**Scope:** provider-free observer freshness, generated-evidence lifecycle,
proxy dispatch refusal, and owned process supervision. Runtime gates, oracle,
v45 package content, and target findings are outside this report.

## Current state

The lifecycle supervisor is implemented in the development harness. Its
guardian timing and provider/Qwen tool-ID repairs passed local tests, scoped
review, and installed-Qwen 0.22.0 remote smoke. Selected-subscription
qualification r4 then exposed a separate client-prevalidation boundary: Qwen
rejected an invalid tool directory before firing PreToolUse and the next proxy
dispatch correctly refused the still-unreconciled provider expectation. The
narrow PostToolBatch repair now binds every expected provider ID to exactly one
accepted client batch item and either one completed Pre/Post pair or the exact
`invalid_tool_params`/`not_started` rejection. Local affected suites, independent
root tests, and the first installed-client rejection smoke passed; the final
installed-client rerun against the review-fixed exact-once snapshot is pending.
No metered target provider contact occurred while developing this repair.

## Timeline

- 2026-09-05 — Read the reviewed lifecycle spec and mapped the existing active
  marker, target-probe sealing, proxy dispatch, and controller owned-termination
  boundaries. The active marker already supplies the authoritative set of
  generated worklist ledgers, allowing path-based lifecycle registration
  without sampling or a fixed file count.
- 2026-09-05 — Inspected the installed Qwen behavior supplied by root: pinned
  0.22.0 treats hook command failure/exception as continuation, so exit status
  alone cannot enforce fail-closed behavior. The hook must emit the runtime's
  explicit denial plus `continue:false`; independent proxy and guardian faults
  remain authoritative when a hook crashes or never runs.
- 2026-09-05 — Added the first observer/evidence lifecycle tests and ran them
  before implementation. The suite failed while importing the absent
  `eval/bench/lifecycle-supervisor.py`, the expected red state for the new
  policy boundary.
- 2026-09-05 — Implemented the initial helper and ran nine focused tests; all
  passed in 0.074s. Added stricter malformed-state, verified-read race, symlink,
  and hook CLI tests. The 13-test red run exposed two intended defects: malformed
  observer JSON escaped without a durable fault, and explicit hook denial used
  exit 2, which pinned Qwen can treat as a hook execution failure and ignore.
- 2026-09-05 — Repaired both defects; all 13 helper tests and `py_compile`
  passed. Added proxy-level dummy-upstream tests next: the healthy observation
  reached the stub, while all six invalid lifecycle cases incorrectly reached
  it with HTTP 200 in the expected red state because dispatch was not wired yet.
- 2026-09-05 — Wired the lifecycle check ahead of proxy route, credential, and
  socket work. Both focused proxy tests passed: all six missing/invalid states
  returned one terminal HTTP 403 with zero dummy-upstream requests, while a
  fresh observation allowed the request. Qwen smoke evidence from root also
  showed that a crashed hook may still leave raw Pre/Post behavior or no hook;
  added provider-response tool expectations so the next dispatch can detect a
  missing hook even when no helper pair was ever created. Its proxy regression
  failed red with a second HTTP 200 reaching the stub before expectation wiring.
- 2026-09-05 — Added durable provider-response tool expectations and reran their
  two helper tests; both passed in 0.013s. The focused proxy lifecycle group then
  passed all three tests in 2.331s: invalid observation blocked before contact,
  a healthy observation reached the stub, and a returned tool call without a
  matching hook pair made the next dispatch fail permanently.
- 2026-09-05 — Tightened response expectation publication so a discarded
  wrong-model answer, malformed body, or HTTP error cannot create a hook event
  Qwen never had a chance to emit. Added a wrong-model tool-call regression and
  ran the combined helper and complete proxy suites: all 65 tests passed in
  32.766s.
- 2026-09-06 — Bound the controller-created, mode-0700 trace workspace into the
  signed launch record and changed monitored settings to the source-verified
  stable `../skill-catalogue` directory. All 17 lifecycle helper tests and the
  focused monitored prepare regression passed in 0.182s and 0.196s.
- 2026-09-06 — Integrated the target controller with controller-pinned trace
  inputs, a capability-signed launch, an external initial-observation wait, a
  250ms guardian, lifecycle environment propagation, and a signed terminal
  receipt before Task 7. The provider-free monitored real-runner/proxy/Task-7
  end-to-end regression passed in 5.333s with one localhost request.
- 2026-09-06 — Added the monitored schema-2 budget path at the proxy boundary:
  null aggregate limits still record every attempt, retry, request byte, and
  provider result without inventing a run-wide refusal threshold. The focused
  retry/accounting regression passed in 0.616s with two localhost contacts.
- 2026-09-06 — After wiring the full-controller prelaunch path, shell syntax and
  the embedded Python compiled cleanly. An initial combined test command named
  the wrong proxy test class and reported one loader error; the corrected helper
  plus monitored-proxy selection passed all 18 tests in 0.796s.
- 2026-09-06 — The first monitored full-controller fixture refused its initial
  observation because the test began the fresh observer sequence at 1; the
  helper correctly required sequence 0. The fixture now uses the observer's
  actual sequence contract and passed in 1.069s, including signed launch,
  null-limit state, trace-local child environment, guardian, and PASS receipt.
- 2026-09-06 — The first forced-guardian-death fixture ended without the test
  finding a terminal receipt; the immediate rerun passed, so this was treated
  as an unresolved race rather than success. Added receipt-presence diagnostics
  and repeated the exact forced-death test ten times. All ten passed (the last
  in 0.981s): the controller detected the dead guardian, stopped its owned child,
  retained the trace, and signed a permanent FAULT receipt.
- 2026-09-06 — Diagnosed that apparent race in the test harness: the monitored
  environment helper applied `FAKE_TARGET_MODE=wait` after the fixture had
  already translated it into the target command, so the child could finish
  normally while the test killed the guardian. Passing the mode through the
  actual command builder removed the race; the corrected forced-death case
  passed in 0.857s.
- 2026-09-06 — Ran the two monitored controller cases beside representative
  finite success and budget-termination cases. All four passed in 3.729s,
  preserving finite controller behavior while the monitored path remained
  guardian-owned and free of aggregate limit variables.
- 2026-09-06 — The complete 50-test controller suite exposed one finite-lane
  environment regression: the generic allowlist propagated the package source
  path. Moved package version/root propagation into the monitored trace-local
  branch only. The finite allowlist and monitored-launch regressions then both
  passed in 2.504s.
- 2026-09-06 — Added interrupted-segment recovery: a restarted controller
  terminates the authenticated old process tree, records a permanent lifecycle
  fault and terminal receipt, seals the failed trace, and refuses same-segment
  continuation. The first retry assertion mistakenly tried to observe the
  already closed prior observer and was refused at its expected next sequence;
  filtering terminal segments repaired the test. The crash/recovery/fresh-nonce
  regression passed in 1.982s.
- 2026-09-06 — Ran the complete controller/runner ownership suite after the
  interrupted-segment repair. All 51 tests passed in 61.025s, including finite
  admission/health/budget/replay behavior, monitored launch, dead-guardian
  cleanup, crash recovery, and target-probe process-group termination.
- 2026-09-06 — The first complete target-probe regression run after requiring
  signed lifecycle authority failed two cases and errored one: two monitored
  fixtures waited without publishing the required external observation, and a
  forged finite-to-monitored invocation was refused at the earlier missing-launch
  boundary rather than the older policy diagnostic. The fixtures now publish an
  actually inspected sequence-0 observation and assert the earlier refusal while
  retaining zero-contact and owned-cleanup checks. The three focused cases passed
  in 10.031s and the complete 73-test target-probe suite passed in 59.206s.
- 2026-09-06 — A scoped Claude integration review found that a signed launch
  could be left without a terminal receipt when the initial observation check or
  guardian startup failed. Two red regressions reproduced the missing receipt in
  the full subscription controller and target-probe paths. Startup is now atomic:
  after launch publication, either the guardian starts or the controller records
  a durable fault and signs a terminal receipt before returning. Four focused
  controller cases passed in 3.022s and four target/cleanup cases passed in
  10.477s, including invalid observation, guardian spawn failure, dead guardian,
  direct upgrade refusal, and outer process-group termination.
- 2026-09-06 — Ran the provider-free lifecycle helper against the installed
  Qwen 0.22.0 entrypoint on `contabo-prod` as `claude-developer`. Normal Pre/Post
  hooks allowed two loopback requests and created evidence; missing, stale, and
  deleted registered evidence produced durable faults, explicit Qwen permission
  denial, and no marker; a stale guardian exited 2 and terminated its child with
  signal 15. Helper SHA-256 was
  `b11e5d8a9499a6185acd540319acb6b2c0f1cc084925af82683237213bd06cd9`;
  raw requests, SSE, launch data, and stdout are retained in the run report.
- 2026-09-06 — Reran both complete integration suites after the startup-terminal
  repair. All 53 controller/runner ownership tests passed in 60.701s and all 74
  target-probe tests passed in 61.155s. The combined 68-test lifecycle-helper and
  proxy suite passed in 33.603s, the seven terminal-audit tests passed in 0.095s,
  both shell entrypoints parsed, all embedded and standalone Python compiled,
  and `git diff --check` reported no errors.
- 2026-09-06 — The real selected-subscription r3 run received its first valid
  `gpt-5.5` tool response, then faulted 78ms after proxy completion with
  `EXPECTED_TOOL_HOOK_MISSING`; no hook start or event had yet appeared and Qwen
  ended with cancellation status 130. Source tracing confirmed that the proxy
  durably registers expected tool IDs before releasing the held response to
  Qwen, while the guardian continuously reused the next-dispatch reconciliation
  check. It therefore treated the legitimate response-to-Pre interval as a
  missing hook. The same reuse would also terminate a legitimately long tool
  between its completed Pre hook and later Post hook.
- 2026-09-06 — Added a provider-free regression that starts the real guardian,
  registers a provider tool expectation, delays the client Pre hook, then delays
  Post while the tool is notionally running. Before the repair, the guardian
  exited 2 and terminated the owned controller at the first 50ms interval; the
  focused test failed in 0.071s exactly at the expected pending-delivery check.
- 2026-09-06 — Split continuous supervision from dispatch reconciliation.
  Guardian cycles now enforce authenticated observer freshness and permanent
  lifecycle faults. The proxy still requires completed expected hook pairs
  immediately before every provider contact, and terminal finalization still
  reconciles all pairs. No delay, grace interval, or resumed segment was added.
  The red regression passed in 0.145s and all 18 lifecycle helper tests passed
  in 0.331s, including stale-observation termination and next-dispatch refusal.
- 2026-09-06 — The first complete proxy invocation was cut off by the command
  runner at 30 seconds after displaying one transient test error and no final
  traceback or suite result. A fresh complete invocation ran all 51 proxy tests
  in 33.541s with no failures, including zero-contact lifecycle refusal and a
  missing provider hook blocking the next dispatch.
- 2026-09-06 — Review of the narrow split found that a successful guardian-only
  check validated an observation but did not persist its accepted sequence. A
  regression advanced from sequence 0 to 1 through supervision checks, restored
  the still-authenticated sequence 0 record, and failed red in 0.006s because no
  regression fault was raised. Factoring the accepted-observation write into
  both supervision and dispatch made the rollback fail permanently as required.
  The first green attempt reached the right `OBSERVATION_REGRESSED` exception but
  its test expected lowercase text; correcting that assertion yielded two focused
  passes in 0.135s and all 19 helper tests passing in 0.324s.
- 2026-09-06 — Reran the complete proxy suite against the frozen helper after
  accepted-sequence persistence: all 51 tests passed in 33.099s. Standalone
  compilation and `git diff --check` also passed. The frozen helper SHA-256 for
  review and the next installed-Qwen smoke is
  `d364fa8cecaae814df2c71ef7c823cd3fbedc82a1b9663af7aa03f7e32aff330`.
- 2026-09-06 — A narrow Claude review of the frozen guardian repair returned
  PASS in 62.789s. Its only note was that the guardian does not infer an
  automatic stuck-tool fault while a Pre/Post pair is incomplete. That is the
  intended boundary: real root observations establish continued supervision,
  while elapsed time alone cannot prove that a long-running tool skipped its
  Post hook. The next provider dispatch and terminal finalization still require
  exact pair completion; no aggregate tool duration or grace window was added.
- 2026-09-06 — The next installed-Qwen 0.22.0 mock smoke confirmed the guardian
  timing repair but failed closed at the following dispatch. Provider evidence
  registered ID `call_skill`, while Qwen keyed its Pre/Post pair with generated
  ID `toolu_1788647733813_2r4yf9sit`; the hook payload retained both values as
  `tool_call_id` and `tool_use_id`. Installed source confirmed Qwen passes the
  original API call ID alongside its internal tool-use ID to both hook phases.
  The helper had incorrectly compared the provider ID to its pair-map key.
- 2026-09-06 — Added five red regressions before the ID repair. The exact-domain
  terminal case derived zero completed tools for one completed pair. The helper
  also accepted mismatched Pre/Post call IDs and cross-request expectation
  reuse, silently deduplicated one response's repeated ID, and reported a
  generic missing-hook fault when two internal pairs claimed one provider ID.
  All five failures matched the unbridged or ambiguous-ID behavior.
- 2026-09-06 — Stored the authoritative provider `tool_call_id` on each pair and
  hook receipt while retaining session plus Qwen `tool_use_id` as the pair
  identity. Dispatch and terminal reconciliation now consume provider call IDs;
  mismatched phases, duplicate consumption, and cross-request reuse fault
  explicitly. The independent terminal auditor applies the same exact mapping
  from sealed state. The five helper regressions passed in 0.029s and the actual
  ID-domain terminal-audit regression passed in 0.010s.
- 2026-09-06 — Full local runs passed 24 lifecycle tests in 0.366s, 43 verdict
  tests in 7.406s, and nine monitored-runner tests in 5.393s. The first proxy
  invocation exposed one old unmatched-hook fixture without the now-required
  real `tool_call_id`; after updating that fixture, its focused lifecycle and
  streaming cases passed in 1.099s. The same invocation also had one transient
  streaming connection reset, so a clean complete proxy rerun remains required
  before freezing the repair.
- 2026-09-06 — A fresh complete proxy run passed all 51 tests in 33.738s.
  Standalone compilation and `git diff --check` also passed. The frozen helper
  SHA-256 supplied to the remote installed-Qwen rerun is
  `7da43129492379c5b46ebfdb6e9d8ccefb304187d91b1c1c59dab7191786fb0f`;
  the independently updated terminal auditor SHA-256 is
  `1ef6a04218344a02d350a2261d52efe2365abf0dbaf602d687736777026376df`.
- 2026-09-06 — The tools-disabled Claude Sonnet 5 exact-ID review completed in
  211.318s. It found no other reachable bridge defect and one narrow input-gate
  issue: the separator used to encode session plus internal tool-use identity
  was not forbidden inside either component. Existing dispatch and terminal
  shape checks already rejected the resulting multi-separator key before
  acceptance, but ingestion could temporarily write an ambiguous key. The new
  regression failed red because the malformed hook continued; rejecting the
  separator in `_hook_key` made it pass with the other five exact-ID cases, six
  tests total in 0.033s.
- 2026-09-06 — After the review fix, all 25 lifecycle tests passed in 0.368s
  and all nine monitored-runner tests passed in 4.679s. The final helper SHA-256
  supplied for the installed-Qwen rerun is
  `f38353b4e788dab6a210b66dcd463cec2a6f67be41e48d3998093d9f8b3f4197`.
- 2026-09-06 — Preserved the exact 211.318s review response and its lossless
  214,878-byte input under `docs/run-reports/`. The review input records the
  reviewed preliminary helper and auditor hashes plus the final post-review
  helper hash. The review's suggested byte-identical collision between
  different session/tool-use components is impossible because those components
  are inside the retained raw JSON and therefore change its input hash; a
  cross-slot collision was already rejected by the downstream key-shape check.
  The delimiter check was retained as the cleaner input boundary because it
  prevents any ambiguous pair key from being written at all.
- 2026-09-06 — The final installed-Qwen 0.22.0 remote r4 smoke passed against
  helper SHA-256 `f38353b4e788dab6a210b66dcd463cec2a6f67be41e48d3998093d9f8b3f4197`.
  Built-in `skill` and delayed `run_shell_command` calls each produced matching
  Pre/Post pairs whose provider call IDs reconciled to their distinct Qwen
  tool-use IDs while the guardian stayed live. A deliberate no-hook case
  refused the next dispatch with `EXPECTED_TOOL_HOOK_MISSING` and no second
  successful response. The remote/local mirror inventories matched all 111
  regular files at canonical SHA-256
  `cd454472b7c40e3932d9b3f319040a0639f940bd185b16d440d374e3d929f67f`;
  the durable r4 JSON and Markdown reports retain the identities and mappings.

- 2026-09-06 — Selected-subscription qualification r4 stopped before a
  second provider dispatch with `EXPECTED_TOOL_HOOK_MISSING`. The retained Qwen
  transcript binds the provider call ID to `invalid_tool_params` and
  `executionStatus: not_started`: the model supplied the trace root as a shell
  directory, outside Qwen's registered workspace. Installed Qwen 0.22.0 source
  confirms `buildInvocation` rejects this directory before
  `_executeToolCallBody` creates an internal ID or fires PreToolUse, so absence
  of a Pre/Post pair is correct for this specific rejected attempt. The outbound
  next-request tool message drops `error_type` and `execution_status`, therefore
  neither its text nor a generic tool error can waive the expectation.
- 2026-09-06 — Scoped the repair before editing. Monitored settings will add
  Qwen's awaited `PostToolBatch` hook. The helper will accept a hookless expected
  call only from a retained raw batch item with the exact provider call ID,
  `status: error`, `error_type: invalid_tool_params`, and
  `execution_status: not_started`. Expected IDs must form a disjoint union of
  completed/denied Pre/Post pairs and authenticated pre-execution rejections;
  overlaps, duplicate or unexpected IDs, mismatched names when present, and all
  other error states fault. Missing/crashed batch delivery remains
  `EXPECTED_TOOL_HOOK_MISSING`, and every started or executed call still needs
  its normal pair. The signed receipt and independent terminal auditor will bind
  separate rejection state, raw-event digests, and rejected counts. Acceptance
  requires red/green helper, proxy, auditor, and settings regressions plus an
  installed-Qwen invalid-directory-then-repaired-call smoke; v45 remains
  unchanged.

- 2026-09-06 — Added the exact captured batch payload and three negative
  helper cases plus an independent terminal-audit case before implementation.
  The four-test run failed four assertions in 0.044s: the valid prevalidation
  rejection was denied as `INVALID_HOOK_INPUT`, both successful/started batch
  cases reported that generic phase error instead of the required missing-hook
  fault, and terminal rejection evidence could not be created. This is the
  expected red state for the absent PostToolBatch boundary.

- 2026-09-06 — Implemented the exact PostToolBatch input gate, durable
  rejection state and raw batch journal, disjoint dispatch/terminal
  reconciliation, signed receipt fields, independent raw-event audit, and the
  fourth monitored settings hook. The five focused helper, negative, terminal
  auditor, and settings tests passed in 0.240s. Successful or started tool
  results without normal pairs now fault as `EXPECTED_TOOL_HOOK_MISSING`; only
  the captured invalid/not-started tuple discharges its exact provider ID.

- 2026-09-06 — Root review identified that the independent auditor did
  not reject an unknown non-rejection ID in an otherwise valid raw batch. A
  forged signed fixture with a completed expected call and an unrelated batch
  success incorrectly audited clean in 0.014s. This red regression proves the
  auditor still needs the helper's every-batch-ID-must-be-expected invariant.

- 2026-09-06 — Added the missing expected-owner check to the raw batch
  audit. The forged unknown-success regression then passed in 0.013s, so both
  qualifying rejections and ordinary batch items must now map to provider IDs
  already registered from accepted responses.

- 2026-09-06 — Full affected local suites passed: 28 lifecycle tests in
  0.491s, 11 monitored-runner/auditor tests in 4.825s, 52 proxy tests in
  31.954s, 43 verdict tests in 7.908s, and 74 target-contract tests in 62.379s.
  A final six-case helper/auditor/proxy group passed in 0.705s after the
  expected-owner audit repair; compilation and `git diff --check` passed. The
  frozen production identities sent to the installed-client smoke are helper
  `9e91112fe810b505b5cce9e37a42d8f5838d0e2d33a781e46e5f2da4d96ee685`,
  terminal auditor
  `c5937287748f6ed49fa51f1990bdd28369f404ecf4eee698fe9b0ad022fc90a6`,
  and monitored settings producer
  `12f492d9f3932c3625ae2285f0a9fd00b58ffae450e79bca55ef65f9f75a1361`.

- 2026-09-06 — The first remote installed-Qwen 0.22 smoke against frozen
  helper `9e9111…` passed all three behavioral modes: invalid then repaired made
  three loopback requests and reconciled one batch rejection plus the repaired
  Pre/Post pair; invalid without a batch stopped the next dispatch; a successful
  batch without execution hooks stopped inside PostToolBatch. The last denial's
  exact raw input and explicit `continue:false` output were retained in
  `hook-fault-events.jsonl`, while accepted batches were retained in the batch
  journal. This disproved the initial concern that denied batch input was lost.
- 2026-09-06 — The required Codex gpt-5.6-sol medium review returned `FAIL`
  with two valid findings in the same boundary. A normally completed pair did
  not also have to appear in accepted PostToolBatch evidence, so missing or
  fail-open batch delivery could pass. Duplicate detection likewise covered
  rejected IDs but not completed IDs replayed across batches. Both contradict
  the scoped missing-batch and duplicate-ID contract. The repair will durably
  bind all batch IDs, require each expected provider ID in exactly one accepted
  batch as well as exactly one completed/rejected outcome, and make the auditor
  independently derive the same uniqueness from raw bytes. The retained review
  output SHA-256 is
  `70a6a19a9fb058e4aa32638177bcd7e4dd2a2d7bf332f51d13a833a7a1dcbf61`;
  there will be no second model-review round.

- 2026-09-06 — Added both review regressions before repair. A completed
  pair without any batch receipt incorrectly passed dispatch, and an identical
  successful batch replay incorrectly returned `continue:true`; both tests
  failed in 0.018s, reproducing the review findings exactly.

- 2026-09-06 — The first combined helper/runner run after enforcing one batch
  item per expected provider ID failed five existing fixtures in 4.853s. Three
  completed-pair fixtures and the monitored terminal fixture omitted the real
  client's successful `PostToolBatch`; the older missing-hook assertion now hit
  the earlier and correct `EXPECTED_TOOL_BATCH_MISSING` boundary. This is test
  drift exposed by the stricter contract, not a reason to weaken it; the
  fixtures must model the installed client's batch event explicitly.

- 2026-09-06 — Updated the real-client fixtures and added an independent
  terminal-auditor replay regression. Eight focused cases passed in 0.208s:
  missing batch, replayed completed batch, missing execution hooks, completed
  pair, provider/client ID bridge, live guardian timing, terminal bridge, and
  forged raw-journal replay. Compilation and diff checks passed. The final
  production snapshot sent for installed-client rerun is helper
  `c8fb1cd4e6f149b9f1c49c3d9b6e2974412bc7c76c5c92646b21f3957f599eda`,
  auditor
  `65c6afb410efcf4a94775ba9086f5712719bd6aef13c07a69ceb847635ee79d6`,
  with settings producer unchanged at
  `12f492d9f3932c3625ae2285f0a9fd00b58ffae450e79bca55ef65f9f75a1361`.

- 2026-09-06 — On the final snapshot, the combined helper/runner suite passed
  42 tests in 5.231s, the terminal-verdict suite passed 43 in 7.502s, and the
  target-contract suite passed 74 in 61.288s. The proxy suite had one stale
  missing-hook assertion that now correctly encountered the earlier missing
  batch boundary, plus one streaming connection reset in an unrelated
  pass-through test while the long suites ran concurrently. Both proxy cases
  require isolated reruns after updating only the stale fixture expectation.

- 2026-09-06 — The updated proxy missing-hook fixture and the initially reset
  streaming case passed together in 1.218s. A subsequent full proxy run passed
  all lifecycle cases but hit a connection reset in a different existing
  streaming-response case (52 tests, 34.024s); that exact case then passed alone
  in 0.611s. This isolates the resets from the batch reconciliation change, but
  the full proxy suite still needs one clean recorded run.

- 2026-09-06 — The complete proxy suite then passed all 52 tests in 34.389s on
  an isolated rerun. Root independently passed 30 helper tests in 0.481s and 12
  monitored-runner/auditor tests in 4.784s on the same final helper/auditor
  hashes, and inspected the exact-once dispatch and raw-auditor reconstruction;
  both retained Codex review findings are addressed with no further review
  requested.

## Limits and next boundary

- The contract observes tool boundaries. A file created and deleted entirely
  inside one tool invocation before registration cannot be recovered; exact
  tool input/result remains journaled and missing required final evidence still
  fails finalization.
- Same-UID hostile arbitrary code is outside this operational-failure model.
