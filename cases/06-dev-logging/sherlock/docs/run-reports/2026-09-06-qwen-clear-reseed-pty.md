# Installed Qwen `/clear` → `/sherlock` → reseed source proof

This provider-free PTY fixture ran installed Qwen Code 0.22.0 as `claude-developer` on `contabo-prod`, with an isolated HOME, a toy `sherlock` skill, and a localhost scripted OpenAI-compatible SSE server. It used no Sherlock corpus, credential, or external provider. The executable was `/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js`, SHA-256 `68cb29eb7ccc936d78ece5564ef55cae41a55b630e6657dc417c1f2e561cf4c9`.

The final capture is remote `/tmp/qwen-clear-reseed-pty-20260906-r6`; the local selected-artifact mirror is `/Users/a/hack/qwen-clear-reseed-pty-20260906-source-evidence`. It retains raw hook stdin, request bodies and non-authorisation headers, scripted SSE bodies, PTY transcript, chat transcript, settings, skill, action timestamps, argv, and result for every r1–r6 attempt.

## Observed result

r6 passed its narrow empirical gate. The raw hook order was:

1. `SessionStart`, source `startup`, session `8b040acb-8366-4c5a-9e15-ec2f5d1cfe23`.
2. `UserPromptSubmit`, `prompt` and `submitted_prompt` both `INITIAL: establish parent session`, in that startup session.
3. `SessionStart`, source `clear`, session `dc075482-7c88-459b-894a-3b87bea11055`.
4. `UserPromptSubmit`, `submitted_prompt` `/sherlock`, in the clear session.
5. `UserPromptSubmit`, `prompt` and `submitted_prompt` both `RESEED: use the toy Sherlock skill`, in the clear session.

The exact raw hook input SHA-256 values in that order were `98b302d5002fcc80df8ebce65a961cf8ab6af00cffd0fbe980e45d7b67f446bb`, `c785cad664d77ecbb22fc29cae143c34bb18c3870f16670f46b36b8341c2c8a5`, `f207c60ac86d27b78b34c012a94c6534d3854978975e1abb168de2be3dadfbac`, `406f84c71481603fd213fddffba85e8b2a5049f1fab3fc87d9f402cf12efd71d`, and `ad5888e950743692d77761bc0238d6a761a8ec8474005453b6c6e36d4efba4e0`.

`stage-actions.json` records the actual PTY sends; `request-*.json` preserves the six mock request bodies. Five had been accepted when the reseed proof was established; six existed after the client process group and mock server were stopped. The separate counts prevent a later Qwen memory-maintenance request from being hidden by an early result record.

`root-boundary-events` data can carry the raw root prompt and session ID, but it is insufficient by itself to attribute a transition to `/clear`. The selected-driver proof must pair the post-transition prompt with the prior `SessionStart` whose source is exactly `clear`. Child request counts are not a substitute for that association.

## Attempts retained

| Attempt | Result | Meaning |
| --- | --- | --- |
| r1 | Failed fixture assertion | `/clear` emitted `SessionStart(source=clear)` and changed sessions, but reseed was queued behind a busy slash turn and did not emit `UserPromptSubmit`. |
| r2 | Failed fixture assertion | `/sherlock` emitted a raw submit event; reseed remained queued because the fixture used a screen-text readiness heuristic. |
| r3 | Failed fixture assertion | Exact clear and reseed proof succeeded, but the fixture incorrectly required a separate skill HTTP turn. |
| r4 | Empirical boundary pass | First complete clear, slash, and exact reseed hook order. Its result captured request count before final cleanup; raw retained bodies showed later Qwen follow-up requests. |
| r5 | Failed fixture assertion | Terminal accounting was fixed; a slash command typed six milliseconds after `SessionStart(clear)` was not accepted. |
| r6 | Pass | Waited for accepted raw `/sherlock` submit, then captured the exact reseed hook and request body with terminal accounting. |

The isolated Qwen HOME also accumulated a Qwen self-update cache for 0.23.0. It was never executed: each capture argv pins the 0.22 entrypoint above. To avoid copying generated package cache, the local mirror excludes only `home/updates`. `excluded-home-updates-inventory.json` lists every excluded remote path, byte length, and SHA-256 without copying contents. The report does not call the selected-artifact mirror an all-files mirror; the complete original trees remain remotely at the paths above.

## Timeline

- 2026-09-06: r1 observed the real `SessionStart(source=clear)` and session-ID transition. The absent reseed hook diagnosed a queued-input timing issue.
- 2026-09-06: r2 and r3 showed that `/sherlock` can be a local context injection and that request count is not a valid slash-command or boundary witness.
- 2026-09-06: r4 supplied the first raw ordered source proof. Its stale mid-capture request count was corrected in fixture code rather than reported as terminal fact.
- 2026-09-06: r5 exposed the post-clear input repaint race; r6 waited for the accepted slash hook and passed.
