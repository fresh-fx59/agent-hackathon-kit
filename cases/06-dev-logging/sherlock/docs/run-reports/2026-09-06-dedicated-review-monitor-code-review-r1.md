# Dedicated review monitor — scoped code review r1

Status: complete — PASS.

## Scope

Review only the dedicated review monitor, fixed reviewer prompt, the scoped
`lifecycle-supervisor.py` terminal-publication guard, focused regression tests,
and the v50 launch-preparation-r2 templates. No implementation edits and no
provider calls.

Scope correction: the review lane was later authorized to repair only the new
launch-preparation-r2 wrappers and their documentation. Monitor source fixes
remained in the implementation lane. No provider was called.

Acceptance focus: one real remote subscription review per fresh evidence
snapshot; exact review inputs, outputs, model identity, and usage retained;
accepted observations bound to successful reviews and authenticated lifecycle
publication; unreviewed terminal deltas retained explicitly; no fabricated
heartbeats; 60-second freshness and 600-second request watchdog unchanged.

## Timeline

- 2026-09-06 — Read repository and Sherlock `AGENTS.md`, then the ready
  dedicated-review-monitor specification. The specification requires an
  evidence-complete, fail-closed reviewer lane with terminal/fault rechecks
  under the lifecycle observer lock, immutable trace-prefix verification,
  separately queued mutable-state changes, and a post-terminal binding audit.
- 2026-09-06 — Resolved the scoped worktree changes and launch artifact set.
  The original v50 runtime/package is absent from the diff; the implementation
  changes are limited to the new monitor/prompt/tests, two lifecycle-helper
  terminal checks, helper regressions, and untracked launch/report artifacts.
- 2026-09-06 — Provider-free focused reproductions found that the monitor
  rejected all three preserved real Claude JSON envelopes because they have no
  top-level `model`; a pending PreToolUse advanced as the completed tool;
  malformed exit-zero output left no durable monitor failure; the terminal
  audit accepted a deliberately corrupted snapshot object; and an atomic
  checkpoint replacement caused terminal audit to reject instead of preserving
  the final unreviewed state. These were sent to the implementation lane for
  correction and focused regressions.
- 2026-09-06 — Launch inspection found stale helper pins, direct-executable
  assumptions for a non-executable monitor file, a target wrapper that required
  an already-created monitor directory even though `watch` exclusively creates
  it, and controller launch scripts without a monitor-readiness gate or
  pre-observer failure cleanup. The launch lane began correcting these before
  final re-review.
- 2026-09-06 — Final evidence-shape inspection used real r3 receipt and provider
  artifacts. It exposed that the first readable view decoded only the outer
  receipt while leaving its exact hook input nested in `input_base64`, and that
  gzip provider bodies replayed their full decoded contents after the cursor
  advanced. The corrected fixture now verifies the nested input digest before
  exposing tool input/result/error, retains each raw source as a
  content-addressed object, exposes one readable request JSON or response SSE
  representation only for a new gzip source, and audits each retained object.
  Against r3 evidence, an unchanged next snapshot fell from 2,763,123 bytes to
  16,151 bytes with zero provider-body replay; a reconstructed new completion
  was 296,736 bytes and exposed the exact 172,561-byte request JSON and
  94,342-byte response SSE. Its cursor advanced both real request and tool IDs.
- 2026-09-06 — Replaced the monitor-only launch placeholders with combined lane
  launchers around the already-proven target, harness, and full controller
  commands. They derive the exact sibling monitor directory and parent lock,
  verify that the ready lock names their live owned child, recheck that the
  controller root is absent, propagate controller failure, and run the binding
  audit only at a real terminal receipt. A provider-free stand-in returned the
  injected early controller exit `7`, left the run root absent, removed the
  owned lock, and left no monitor process.
- 2026-09-06 — The fixed remote reviewer command now runs Claude through
  `sudo -u claude-developer` with the subscription account's explicit HOME and
  PATH. This prevents a root-launched combined wrapper from silently selecting
  root's unauthenticated Claude state while retaining Sonnet low, no tools,
  empty settings sources, no session persistence, and JSON output.
- 2026-09-06 — Terminal-boundary review races were reproduced and corrected.
  A controller receipt or successful terminal phase reached before publication
  now produces a separately signed `terminal-unpublished-reviews.jsonl` record
  rather than an observation. That record binds the exact snapshot, prompt,
  command, launch, helper, and any completed reviewer stdout/stderr, exit code,
  and valid model envelope. The terminal audit verifies the record while still
  exposing the final unreviewed delta for the separate semantic review. Failed
  and blocked controller phases remain failures.
- 2026-09-06 — The last audit omission was reproduced: an accepted-review audit
  could pass after its prompt, command, or launch object was deleted because
  only snapshot/stdout/stderr objects were re-hashed. The final audit now
  validates all six content-addressed review objects and compares the signed
  helper digest with the helper actually loaded for audit. The same applicable
  checks cover terminal-unpublished reviews.
- 2026-09-06 — Final provider-free verification passed. The dedicated monitor
  suite passed 15/15; `py_compile`, shell syntax for every r2 launch script,
  and `git diff --check` exited zero. The owned-monitor stand-in again returned
  the injected controller exit `7`, kept the fresh root absent, retained the
  sibling monitor evidence directory, removed the exact parent lock, and left
  no child process. Static launch checks confirmed monitor start precedes each
  target, harness, and full controller command; target roots are exactly
  `TARGET/probe-work`.
- 2026-09-06 — Final pins: monitor
  `c6cd28b188b4b6eab5797998e138fe988d0e34e7272d9ac7ed7a42dee6b3816d`;
  focused tests
  `c3b0ff153f747c8d130b6104d5487cf9ec49998d4d6c62e47c7752d9df4a4a07`;
  prompt
  `c73b76c53d06aec57fb63ff0e27fb3a8f9157d17e80a555700152a4ab59f1856`;
  lifecycle helper
  `235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907`;
  reviewer command
  `231c83332933f9d66371f96f37f9243c64e7e4279a6ef1aa7e6f36e7b5c66f73`.
  The frozen v50 runtime remains
  `0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7`
  and is outside the diff.
- 2026-09-06 — Post-PASS subscription qualification exposed one real-command
  compatibility defect: `load_command` rejected the intentional empty values
  after `--tools` and `--setting-sources`, so r4 failed closed before
  invoking Claude and published no observation. The narrow correction keeps
  the executable nonempty and every argument a string while accepting empty
  later values. Its regression reads the actual pinned command. Independent
  rerun passed 16/16 plus compile and diff checks. This supersedes only the
  monitor/test pins above: monitor
  `78e96502f4941ea353ab4337d5c2428af3f17e9aa3df62ef0c29ce32ef123dd8`;
  tests
  `9dc6ccafb79efbce50d971ccf10a4b59f122c60b3b57c39438caefb4fcfbaa60`.
  Executed r2 launch artifacts remain immutable; fresh r5 artifacts live in
  launch-preparation-r3.
- 2026-09-06 — The fresh r5 qualification then exposed an earlier observer-init
  boundary: identity was visible 69 ms before `lifecycle-launch.json`, and the
  monitor exited on that absent file before invoking Claude. The narrow repair
  waits for launch publication without changing observer timestamps or the
  60-second freshness rule, rechecks terminal/fault state on each poll, and
  still authenticates the launch strictly before snapshot/review. Independent
  verification passed 17/17 plus compile/diff and the owned-launch stand-in.
  Current monitor pin:
  `dcf42026de753649a169d6499077e2f1f2570b7c9af5ed104fd7d97ccc3a388e`;
  tests:
  `1de8dc83975b944c3c5c71f8dc2fea4ed77b1cdf24ba3dab9b1a9f96ffaa78cc`.
  Executed r3 launch artifacts remain immutable; fresh r6 artifacts live in
  launch-preparation-r4.

## Findings

None.

## Verdict

PASS for the scoped source, prompt, lifecycle guard, focused regressions, and
combined launch contract. The next gate is the real subscription target and
harness qualification using these pinned launchers. No paid corpus run is
admissible until that qualification and its binding audit pass. Its terminal
tail still requires the separately specified semantic post-run review; this
code review does not pre-accept it.
