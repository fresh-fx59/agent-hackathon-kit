# Controlled-run lifecycle supervisor — development report

**Scope:** provider-free observer freshness, generated-evidence lifecycle,
proxy dispatch refusal, and owned process supervision. Runtime gates, oracle,
v45 package content, and target findings are outside this report.

## Current state

The lifecycle supervisor is implemented in the development harness. A real
selected-subscription qualification exposed and reproduced a guardian boundary
error after its first provider response. Its narrow repair passed local tests
and scoped review; the subsequent installed-Qwen smoke exposed the distinct
provider/Qwen tool-ID domains. The exact-ID correlation repair is locally green
and awaiting the fresh installed-Qwen rerun. No metered target provider contact
has occurred.

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

## Limits and next boundary

- The contract observes tool boundaries. A file created and deleted entirely
  inside one tool invocation before registration cannot be recovered; exact
  tool input/result remains journaled and missing required final evidence still
  fails finalization.
- Same-UID hostile arbitrary code is outside this operational-failure model.
