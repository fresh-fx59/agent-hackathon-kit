# v45 selected-subscription qualification r6 — retained runner failure

This report records a failed qualification attempt. The full remote harness root and its sibling launch stdout/stderr were mirrored without alteration before this report was written. The run used the monitored selected-subscription lane; the lifecycle receipt passed, while the runner’s final visible triage hand-off did not complete in the one headless Qwen session.

The controller receipt records 19 provider attempts, no provider failures, 169 seconds wall time, and `WITHIN` for the uncapped monitored envelope. The lifecycle receipt is `PASS`: 21 expected tools, 20 completed tools, one rejected tool, one completed foreground subagent, and 10 nested dispatches. These values establish lifecycle/accounting completion; they do not make the runner result successful.

The runner failed the final gates because the visible triage hand-off requires `/clear` and a fresh `/sherlock` draft context, but the headless launcher supplies only one session. The retained Qwen output reports the context limit condition and the required clear/reload sequence. No recovery command was issued in this run.

Evidence is preserved at `/Users/a/hack/sherlock-v45-qualification-development-20260906/sherlock-v45-harness-20260906-r6`, including `runs/run-20260906T001815Z-15f066d69e7d/lifecycle-receipt.json`, controller receipt, runner gate output, complete request/response and hook artifacts, and the sibling `sherlock-v45-harness-20260906-r6.launch.{stdout,stderr}`. The remote source remains `/home/claude-developer/hack/sherlock-v45-harness-20260906-r6` plus its sibling launch files.

The 382 mirrored regular files have the exact matching canonical relative-path/SHA-256 inventory digest `81715eb46c242223057a8aa29afb450b20cb7416deac6985bfde34a7bf144a3d`. The complete inventory is retained locally as `sherlock-v45-harness-20260906-r6.sha256-inventory.json`; the compact index beside this report contains the terminal values and artifact identities without reproducing model text.
