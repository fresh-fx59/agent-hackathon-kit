# Qwen tool continuation at the clear/reseed boundary

## Outcome

Provider-free reproduction fails on the previous driver and passes on the focused repair. This does not accept subscription r8 or any corpus report. Runtime v45 remains immutable; only the development driver and fixture changed.

## Hypothesis and evidence

R8 reached a genuine SessionStart(clear), followed by actual `/sherlock`. Qwen emitted two internal UserPromptSubmit events with an empty `prompt` and no `submitted_prompt` while running tools. The driver assumed the second hook was the actual reseed and rejected it before queued input arrived. Its `CLEAR_NOT_EFFECTIVE` at12:21:59 precedes the final missing-tool-batch fault at12:22:07.

The installed Qwen0.22 bundle and official client source independently show that the submitted text projection is emitted only for nonempty interactive UserQuery input. See [source reference](artifacts/2026-09-06-clear-continuation/source-reference.json). The attempted web search failed at transport; direct official-source retrieval succeeded.

## Repair and acceptance

Commit1b1ad30 skips only empty prompts without a submitted_prompt key in the already verified fresh session, after exact skill reload. It still awaits exact prompt and submitted_prompt equality for reseed. A wrong session, wrong real prompt, or present empty/null submitted field is not skipped. Driver SHA256: `b5f89ccd917248ba4ac1a0dd527d3062a92a3004c4bab15784f0acddc90da106`.

Root regression run:7 tests pass, [output](artifacts/2026-09-06-clear-continuation/root-tests.txt). Frozen v45 version gate and diff checks pass. Existing fixture now offers --tool-continuation, sending one localhost read_file response after /sherlock and requiring the real continuation before submitting reseed.

## Experiments

- Real Qwen0.22 r1: old driver reproduces exact ClearProofError,6 localhost requests. [Result](artifacts/2026-09-06-clear-continuation/qwen-r1-result.json). Remote original /tmp/qwen-monitored-continuation-20260906-r1; local mirror /Users/a/hack/qwen-monitored-continuation-20260906-r1.
- Same client/fixture r2: patched driver passes, required tool continuation observed, exact new-session reseed proven,6 localhost requests. [Result](artifacts/2026-09-06-clear-continuation/qwen-r2-result.json). Remote original /tmp/qwen-monitored-continuation-20260906-r2; local mirror /Users/a/hack/qwen-monitored-continuation-20260906-r2. Auto-update cache home/updates excluded from local mirrors; remote originals retained. No paid provider used.
- Claude review r1 returned an unexecuted tool-like statement instead of a verdict; rejected as an invalid review. [Retained output](artifacts/2026-09-06-clear-continuation/review-r1-invalid.json).
- Claude review r2 explicitly reviewed the diff with tools unavailable: no actionable defects. Actual model claude-sonnet-5, medium effort; reported cost estimate0.0740476USD is not an invoice. [Input](artifacts/2026-09-06-clear-continuation/review-input.txt), [verdict and usage](artifacts/2026-09-06-clear-continuation/review-r2.json).

## Next goal-bound action

Sync the reviewed driver, regenerate target preparation to bind its new hash, and launch a fresh subscription qualification. After acceptance, run the approved target partial and both full corpus investigations. No accepted output is claimed here.

Mirror verification completed: r1 217files inventorySHA94e1e3f95b072ac81170acffd036cfd90f7798148e66b777264ed5a1f0bea97d; r2 218files inventorySHA24db858178ae74b475fbcb0cdece2bc3dce62d8419ff93c0bab301e8ed08aa03. Every selected path and byte hash matches remote; home/updates is the only excluded cache subtree.

Additional broad fake-driver command launched by the worker later terminated without recoverable stdout/exit in its asynchronous tool session. Its outcome is UNKNOWN and is not counted as a passing check. It exercised legacy timing paths rather than the changed monitored proof; the exact proof regression suite, real-client red/green reproduction, and scoped review supply the acceptance evidence for this repair. No duplicate broad run was launched.
