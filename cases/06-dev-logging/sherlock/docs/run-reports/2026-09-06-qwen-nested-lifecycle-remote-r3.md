# Installed Qwen nested-lifecycle smoke — remote r3

This provider-free smoke used installed Qwen 0.22.0 as `claude-developer`, an isolated HOME and workspace, a localhost scripted SSE endpoint, and frozen lifecycle helper `f2b6d04eceec5bdf52ab65cae3d334893a76fa5520c35af8fd7276e701d966c1`. It did not load Sherlock, contact a provider, or use a credential.

The Qwen binary SHA-256 was `68cb29eb7ccc936d78ece5564ef55cae41a55b630e6657dc417c1f2e561cf4c9`. The fixture registered each parent/sibling and child provider ID before returning its SSE response, using the SHA-256 of the exact incoming request body as both request reference and `check_dispatch` binding. Every hook raw input, helper stdout/stderr/exit code, request, response, Qwen stdout/stderr, guardian argv/stdout/stderr, and observer artifact is retained.

| Mode | Result | Requests / successful SSE | Terminal lifecycle result |
| --- | --- | --- | --- |
| normal | PASS, Qwen exit 0 | 4 / 4 | no fault; guardian alive at client completion |
| capped (`maxTurns: 1`) | PASS, Qwen exit 0 | 3 / 3 | no fault; the unused renewal was revoked at Stop |
| missing Stop | PASS negative, Qwen terminated after fault | 3 / 3 | `SUBAGENT_STOP_MISSING` |
| missing parent Post | PASS negative, Qwen terminated after fault | 3 / 3 | `HOOK_PAIR_MISSING` |
| missing inner batch | PASS negative, dispatch denied | 3 / 2 | `EXPECTED_TOOL_BATCH_MISSING` at request index 2 for `call_child_shell` |

The normal case proves child continuation only after the inner batch. The capped case proves a child Stop can revoke an unused continuation permit. The negative cases received no root-continuation SSE response after their missing lifecycle evidence. The fixture retains a raw no-op hook output only where that specific hook was deliberately withheld from the helper; it never fabricates an accepted helper receipt.

Remote evidence is `/tmp/qwen-nested-lifecycle-smoke-20260906-r3`; its full local mirror is `/Users/a/hack/qwen-nested-lifecycle-smoke-20260906-r3`. Both have 373 regular capture files with the matching canonical relative-path/SHA-256 inventory digest `8ebdd33a11cf4ceb19619b5a3ff050fd0286a7f7b06af86831a0f9e2b70a5c29`. The independent verifier can inspect `evidence/summary.json`, each `*/result.json`, `*/raw-hooks/*.input.json`, paired `*.input.output.json`, and `sha256-inventory.json` without relying on this prose.

Two earlier remote staging attempts were retained and made zero Qwen or loopback requests: r1 blocked fixture reads in a root-owned mode-0700 directory; r2 permitted reads but blocked evidence creation in a root-owned mode-0755 directory. Their stderrs remain at `/tmp/qwen-nested-lifecycle-smoke-20260906-r{1,2}/stderr`. r3 created the staging root as `claude-developer` before the root-side code copy.
