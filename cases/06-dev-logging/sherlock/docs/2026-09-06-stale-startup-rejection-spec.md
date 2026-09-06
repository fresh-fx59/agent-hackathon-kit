# Accepted skill invocation versus stale terminal rejection

Status: approved for implementation with scoped Codex dispositions.

## Goal contribution and evidence
V50 real indexed Qwen fixture r1 correctly counts2ledger rows and consumes Stop.
Driver erroneously repeats startup instead of reaching clear: exact expanded
UserPromptSubmit16:30:21.497666Z precedes second typed log16:30:26; transcript
584772bytes contains39 repainted Unknown command banners. Duplicate original
submission16:30:36.513245Z is in the same session. Original artifacts preserved.

## Scope
Harness-only measure/interactive-drive.py and focused regression test.
Frozen v50 never changes. Add optional exact-acceptance predicate to type_skill;
check before retries and while probing terminal rejection. Startup uses existing
monitored_start_evidence state complete; clear uses monitored_clear_evidence
state complete. Existing exact submitted input, skill expansion, nonce/session
and boundary matching remain mandatory. Contradictory hooks retain rc8 failure.
No proof preserves genuine Unknown-command retries and missing-skill rc11.
No new services, sleep thresholds, runtime prose or provider calls.

## Acceptance
Repainted old rejection + exact hook acceptance: one accepted invocation, no
additional retry. Genuine initial rejection without hook acceptance retries.
A later accepted retry cannot be followed by duplicate submission. Wrong prompt
or expansion cannot become positive acceptance. Existing startup/clear tests pass.
Actual indexed Qwen fixture rerun in fresh directory after reviewed driver commit.

## Timeline
- 2026-09-06 — Terra inspected real hooks and39 terminal repeats; root agrees
  positive exact acceptance outranks terminal repaint. Ready scoped critique.

## Scoped critique dispositions

- Codex gpt-5.6-sol low returned two concrete clarifications. Input19,911,
  cached10,624, output533 (reasoning417); exact artifacts retained.
- Explicitly catch ClearProofError from the predicate at both startup and clear
  callers and preserve existing rc8 diagnostic. Boolean no-acceptance remains
  separate from contradictory evidence and the missing-skill rc11 path.
- When the stale needle appears, keep polling acceptance through the existing
  SKILL_REJECT_PROBE_S window rather than breaking immediately. Recheck after
  existing wait_ready and immediately before another typed invocation. No new
  arbitrary delay constant. Tests cover hook acceptance arriving after the
  banner and during readiness wait, as well as genuine missing-skill rejection.
- This is one scoped ready-plan critique; no additional review panel.
