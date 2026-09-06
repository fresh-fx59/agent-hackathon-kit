# v50 harness repair verification

Status: offline verification passed; real-Qwen r2 is next. No provider contact.

## Findings

Exact monitored hook acceptance now takes precedence over stale Unknown-command terminal repaints. The existing rejection probe remains bounded; genuine rejection retries and contradictory-proof rc8 remain intact. Guardian validation now samples its monotonic clock inside the observer lock, preventing valid concurrent publication from appearing in the future. No observation age tolerance or runtime changed.

## Verification

Root selected suites: 29 tests passed (1.222s): stale-banner, monitored-clear proof, single invocation handoff, terminal audit. Root lifecycle suite: 43 passed (0.654s), including stale/future rejection. Missing-skill script previously passed. Frozen v50 registration verification passed.

Initial broad discovery failed: first the actual guardian pre-lock clock race. Isolated retry then exposed an obsolete later FullMonitoredSelectedRunnerTest fake Qwen fixture: it never emits the exact SessionStart/UserPromptSubmit hooks now required, and reads the blank startup separator as its task. Its failure is retained, not reported as passing. Repairing that unrelated fake harness is deferred; actual Qwen r2 validates this production driver path. No gates weakened.

## Identity

Runtime v50: 0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7.
Driver: c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0.
Helper: 861593a235903f6de92c53a0b27d9bde5888aaead2d06acf97f3c3529c0fc2cb.
All seven unused v50 launch templates repinned; bash syntax checks passed.

## Timeline

- 2026-09-06T16:48:30.928366+00:00 — Root verified repaired helper and selected driver suites, preserved both earlier failures, and prepared real-Qwen r2. No DeepSeek call or accepted corpus report yet.
