# v50 subscription harness qualification r4-r6

Status: r4 and r5 rejected and preserved; r6 repair passed and awaits remote sync.

## Scope

Qualify the frozen v50 package with the dedicated remote Sonnet review monitor
before any paid DeepSeek target or corpus run. Target preparation is
provider-free. Every run root is fresh and terminal attempts remain immutable.

## Timeline

- 2026-09-06 18:24Z — Remote sync was confirmed clean at commit
  `257814918401c5f072d3e65a93294b3991f39dac`, with monitor
  `c6cd28b188b4b6eab5797998e138fe988d0e34e7272d9ac7ed7a42dee6b3816d`
  and no active Sherlock process.
- 2026-09-06 18:25Z — Provider-free target preparation created
  `/home/claude-developer/hack/sherlock-v50-qualification-20260906-r2`.
  Its sealed probe manifest is
  `c9d63bb5025fb899a32fd9ceb8d0081590f8bdd650aaf1f61ef069cb1c3a1aec`.
  The paid target probe did not run.
- 2026-09-06 18:25Z — The combined r4 launcher started the sibling monitor
  before creating
  `/home/claude-developer/hack/sherlock-v50-harness-20260906-r4`.
  The ready lock
  `/home/claude-developer/hack/.sherlock-v50-harness-20260906-r4.review-monitor.lock`
  authenticated live owned monitor PID `1155102`.
- 2026-09-06 18:27Z — Harness health probes completed 3/3 subscription broker
  calls at 100K, 250K, and 400K, all returning `gpt-5.5`. The controller then
  created observer
  `observer-db81b491ddaba03228148f9ab92c0757`, but the monitor exited before
  starting Claude with exact stderr
  `DEDICATED_REVIEW_MONITOR: review command argv`. No observation or review
  ledger was published and no controller Qwen request ran.
- 2026-09-06 18:27Z — The lifecycle correctly failed closed with
  `INITIAL_OBSERVATION_MISSING`; controller phase became
  `BLOCKED_UNKNOWN` with reason `LIFECYCLE_LAUNCH_INVALID`, and its signed
  terminal receipt retained no accepted observation. The combined launcher
  exited 2 because the review monitor exited 2. No paid DeepSeek request ran.
- 2026-09-06 — Root cause: `load_command` rejected every empty argv element,
  while the pinned real Claude command intentionally passes empty values to
  `--tools` and `--setting-sources`. The unit stub had not exercised the
  actual pinned command shape.
- 2026-09-06 — Mirrored the complete terminal r4 run, sibling monitor
  directory, and four launch/watch logs into
  `artifacts/2026-09-06-v50-harness-subscription-r4-failed/`. The mirror has
  144 source files; every local SHA-256 matched a fresh remote manifest.
- 2026-09-06 — Narrow candidate repair now requires a nonempty string
  executable and string arguments while permitting empty later values. Its
  regression loads the actual pinned Claude command and rejects an empty
  executable and non-string argument. Candidate monitor:
  `78e96502f4941ea353ab4337d5c2428af3f17e9aa3df62ef0c29ce32ef123dd8`;
  focused tests:
  `9dc6ccafb79efbce50d971ccf10a4b59f122c60b3b57c39438caefb4fcfbaa60`.
  The implementation lane reported 16/16, compile, and diff checks passing;
  independent review verification is pending.
- 2026-09-06 — The executed r2 launch artifacts retain their committed monitor
  pin. Fresh r5 launchers are being versioned separately under
  `artifacts/2026-09-06-v50-launch-preparation-r3/`.
- 2026-09-06 — Independent review passed the narrow repair. The focused suite
  passed 16/16; `py_compile`, all fresh-r3 launcher syntax, and
  `git diff --check` exited zero. A provider-free r3 owned-monitor stand-in
  propagated controller exit 7, kept the fresh root absent, removed its exact
  lock, and left no child. The executed r2 artifact directory is byte-identical
  to committed HEAD. A remote exact-argv inventory found zero live monitor,
  controller, or Qwen processes from r4.
- 2026-09-06 18:36Z — The passed argv repair and fresh r3 launch artifacts
  synced cleanly at commit
  `c9ee4d98ffe962bd2a104ee2202ed3de31b0c1d9`. The combined launcher created
  fresh root `/home/claude-developer/hack/sherlock-v50-harness-20260906-r5`
  only after owned monitor PID `1157039` and its exact parent lock were live.
- 2026-09-06 18:37Z — r5 failed before starting Claude. Observer identity mtime
  was `18:37:29.212Z`; 69 ms later the monitor tried to read the not-yet-
  published `lifecycle-launch.json` and exited with exact stderr
  `[Errno 2] No such file or directory: '.../lifecycle-launch.json'`.
  There was no snapshot, reviewer input/output/exit, monitor object, review
  ledger, or observation. The lifecycle then recorded
  `INITIAL_OBSERVATION_MISSING` 60.175 seconds after identity creation, and
  the controller ended `BLOCKED_UNKNOWN/LIFECYCLE_LAUNCH_INVALID`.
- 2026-09-06 — Correction to the transient chat status: identity discovery did
  not mean the monitor had entered a real Sonnet review. The empty object store
  and launch-file error prove that no Claude process was invoked.
- 2026-09-06 — Mirrored the complete terminal r5 root, sibling monitor
  directory, and launch/watch logs into
  `artifacts/2026-09-06-v50-harness-subscription-r5-failed/`. All 144 local
  files match their fresh remote SHA-256 manifest.
- 2026-09-06 — The narrow readiness repair polls while
  `lifecycle-launch.json` is absent after identity discovery, without changing
  any observer timestamp or freshness rule. Each poll rechecks fault, receipt,
  and normal terminal state; once the launch exists, `run_cycle` retains its
  strict signature and identity validation. A deterministic regression
  reproduces the held-launch ordering and proves no cycle starts early.
- 2026-09-06 — Independent verification passed 17/17 focused tests,
  `py_compile`, fresh-r4 launcher syntax, and `git diff --check`. A
  provider-free owned-monitor stand-in again propagated controller exit 7,
  preserved root freshness, removed its exact lock, and left no child. Remote
  exact-argv inventory was zero. Monitor candidate:
  `dcf42026de753649a169d6499077e2f1f2570b7c9af5ed104fd7d97ccc3a388e`;
  tests:
  `1de8dc83975b944c3c5c71f8dc2fea4ed77b1cdf24ba3dab9b1a9f96ffaa78cc`.
  Executed launch-preparation-r2 and r3 directories are byte-identical to HEAD;
  fresh harness r6 is separately versioned in launch-preparation-r4.

## Current decision

r4 and r5 are rejected evidence and cannot be reused. Do not run the paid target
probe or corpus. After commit and remote sync of the passed repair and
separately versioned launcher, start subscription harness r6 at a new root.
