# Monitored staged runner development report

## Outcome

The implementation reuses the existing interactive stage driver for monitored Sherlock v40+ packages. Frozen v45 runtime bytes remain unchanged. Full target suite passed 76 tests; selected/full monitored composition passed 27 tests. Legacy driver scenario checks also passed. No new DeepSeek run has begun.

## Problem and repair

Subscription r6 completed triage with valid lifecycle accounting, then stopped at the runtime's required fresh-context handoff. The headless launcher treated that intermediate stop as terminal. Newly prepared monitored profiles now select the existing staged driver. The runner derives mode from the sealed profile and rejects conflicting ambient mode. Exact interactive driver bytes are bound at preparation and checked before launch, including selected subscription and full runs. Interactive target launch consumes the same one-use authenticated start as headless launch.

SessionStart raw inputs now have a separate observer journal, signed digest/count and independent replay checks. The driver captures a root prompt anchor before clear, verifies a fresh SessionStart(clear), then exact skill reload and checkpoint reseed prompts in the new session. Provider message counts cannot substitute for this proof. Stage budget zero means no stage deadline. Monitored timeout zero uses an exec-transparent env prefix; finite runs retain their timeout.

## Failed experiments and root causes

- Target binding tests first failed on missing mode/hash and demonstrated secret access before mutated driver rejection. After repair all three focused checks passed.
- Two headless-only provider-free model fixtures needed actual PTY input and explicit fixture terminal checkpoint/handoff. Real report gates remained authoritative.
- The expiry-crossing fixture's eight-second admission expired during PTY startup. It now admits for thirty seconds and holds the local response until exact expiry plus one second, preserving the authenticated-start assertion.
- The original explicit-stop test exposed an orphaned forkpty child. Cleanup now owns its known process group, sends TERM then KILL after a short grace even if the leader exits, and suppresses duplicate TERM during cleanup. A pre-fork handler records early termination until cleanup is armed.
- The timeout wrapper created another process-group boundary. Monitored execution omits it. An initial empty-array prefix failed under Bash nounset before Qwen; exec-transparent env fixes that portability failure.
- One broad suite was interrupted while a shared production edit was needed. Its output is preserved and not accepted. The final stable suite passed.
- Selected qualification initially omitted the new driver digest. It now propagates the verified source digest; composition and mutation checks pass.
- Background agents stopped at their usage limit. Root recovered their saved work and completed remaining checks. No active agent was mistaken for a running investigation.

## Verification and evidence

- Target contract: 76 tests, 100.969 seconds, PASS; `/tmp/root-target-staged-final-r2.txt`.
- Full monitored and selected composition: 27 tests, 31.868 seconds, PASS; `/tmp/root-staged-full-selected-final.txt`.
- Lifecycle helper: 41 tests, 0.664 seconds, PASS; `/tmp/root-staged-lifecycle-final.txt`.
- Exact reset proof: 5 tests, 0.007 seconds, PASS; `/tmp/root-staged-clear-final.txt`.
- Interactive lane wiring: script assertions PASS; `/tmp/root-staged-wiring-final.txt`.
- Legacy driver: all script scenarios PASS; `artifacts/2026-09-06-monitored-staged-runner/root-staged-driver-final-r2.txt`.
- Final installed-Qwen loopback smoke r3: PASS; [retained identities and evidence](2026-09-06-qwen-monitored-clear-reseed-r2.md).
- Ready specification received the required Codex gpt-5.6-sol medium critique, PASS; `2026-09-06-monitored-staged-runner-review.json`.
- Chronological experiment details and corrections: [qualification timeline](2026-09-06-v45-qualification-r1.md).

Final driver SHA256: `4ca7eb56657a3de81b900cddce89b813d3ab81e1c693bc1a5574595b3aebbec8`.
Lifecycle helper SHA256: `b67790b700d0406aa0909e799b0672b4be8a0ab45088e0400b40b3d3032e4d2c`.
Frozen v45 package SHA256: `36a0dfe2c674ac11a13d1dc1b6a0b8c3e720fdb08577c8c9ec5d386cd317e40e`.

## Next acceptance gate

All scoped checks passed. Commit and sync the test checkout. Prepare fresh target input r5 and subscription run r7. Root must inspect and publish genuine observations within sixty seconds. Only accepted subscription qualification permits the next target partial run, then full Winevtx and independent fresh-corpus reports on identical runtime bytes.
