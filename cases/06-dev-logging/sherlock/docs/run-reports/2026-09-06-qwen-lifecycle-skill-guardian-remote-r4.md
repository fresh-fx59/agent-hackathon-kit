# Installed Qwen lifecycle skill guardian — remote r4

Status: **PASS**. This ran actual installed Qwen 0.22.0 on `contabo-prod` as `claude-developer`, backed only by a localhost scripted SSE endpoint. It used no provider credential or external model request, and loaded only the fixture `mockskill`.

The helper identity is `f38353b4e788dab6a210b66dcd463cec2a6f67be41e48d3998093d9f8b3f4197`. The [compact JSON record](2026-09-06-qwen-lifecycle-skill-guardian-remote-r4.json) contains exact Qwen, helper, and smoke identities plus the full regular-file inventory.

| Scenario | Result | Lifecycle evidence |
| --- | --- | --- |
| built-in `skill` | PASS | Matching PreToolUse/PostToolUse pair; API `call_skill` reconciled to Qwen `toolu_1788649181516_mvlyhcrz0`; guardian live before cleanup. |
| delayed `run_shell_command` | PASS | Matching pair; `call_slow-shell` reconciled to `toolu_1788649187057_9xa1ro1m2`; guardian live before cleanup; marker contains `slow`. |
| hooks deliberately omitted | PASS (refusal) | Second dispatch rejected as `EXPECTED_TOOL_HOOK_MISSING`; exactly one successful SSE response, no `response-1.sse`, and Qwen group exited `-15` without retries. |

The mirrored evidence root is `/Users/a/hack/sherlock-qwen-skill-guardian-20260906-remote-r4`. Remote and local inventories match byte-for-byte: 111 regular files, sorted-inventory SHA-256 `cd454472b7c40e3932d9b3f319040a0639f940bd185b16d440d374e3d929f67f`.

r1/r2/r3 remain retained failed evidence: nonce validation before Qwen, the pre-bridge identifier-domain fault, and a fixture-only group-cleanup stall, respectively. r4 is the completed frozen-helper result.
