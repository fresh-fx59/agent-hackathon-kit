# One Sherlock invocation per session

Goal: preserve cold-corpus task and checkpoint context while enforcing triage→draft→repair boundaries, so target-model reports can complete without queued or contextless investigations.

## Observed cause

V46 qualification r1 called `/sherlock` through `type_skill` and then separately typed a task already prefixed by `/sherlock` in run-bench.sh. First actual root submission13:23:31 began triage. Second actual submission13:28:19, in the same session, carried `/sherlock` plus task after triage boundary1. The stage-pause receipt stayed pending and draft began before clear. The continuation path likewise submits bare skill, then waits idle before the reseed; this permits autonomous work without the reseed. The fixture used global Stop settings and direct handoff input, so it did not prove the actual skill-scoped input composition. Terminal OBSERVATION_STALE13:31:06 is a separate root monitoring miss and must remain the original verdict.

## Focused contract

Each fresh session receives one actual user submission: `/sherlock <task arguments>`. Startup preserves the task exactly, recognizing an existing exact skill-command prefix so it is never duplicated. After a validated stage pause, wait for natural idle and no inflight provider/tools/children, clear, prove fresh SessionStart, then submit one skill invocation carrying the freshly generated checkpoint reseed as arguments. Do not send a bare skill turn followed by a task/reseed turn. Prove the first actual root submission equals the expected full invocation and its prompt contains real expanded skill instructions; preserve exact task/reseed and new-session hashes. Reject different-session, wrong-argument, unexpanded, or unexpected actual user input. Autonomous empty continuation hooks may remain only the already narrow observed form.

Runtime v47 copies immutable v46, updates checkpoint handoff instructions and SKILL/README prose to match the one-submission protocol. Preserve receipt validation and all four final gates. No source version is edited after freeze. Existing driver and root event streams remain the mechanism; no new orchestrator.

## Acceptance

Red tests reproduce duplicate startup and continuation ordering. Green tests prove exactly one dispatch with intact multiline task, an already-prefixed prompt is not doubled, clear precedes combined continuation, and evidence refuses altered arguments/session/missing expansion. Actual installed Qwen 0.22.0 test uses a real discovered Sherlock skill and skill-scoped Stop hook (not only global Stop), full driver entry, scripted localhost responses, delayed tool/response, startup→triage handoff→clear→combined reseed. Assert no provider request sees bare/contextless skill, no second actual startup input, receipt consumed before clear, fresh-session combined reseed, all requests/tools accounted, marker retained. Then finalizer/stage-pause regressions, subscription qualification, authorized target partial and full Winevtx plus independent cold corpus on one final package hash.

Non-goals: relaxing supervision deadlines, changing findings gates, new orchestration, or seeding investigation answers.
