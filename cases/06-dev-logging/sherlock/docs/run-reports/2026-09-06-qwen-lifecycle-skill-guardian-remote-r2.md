# Installed Qwen lifecycle skill guardian — remote r2

Status: **FAIL CLOSED**. This intentionally used only a localhost scripted SSE server on `contabo-prod` as `claude-developer`; it made no external provider call, used no credential, and loaded only the fixture `mockskill`.

The frozen helper was `d364fa8cecaae814df2c71ef7c823cd3fbedc82a1b9663af7aa03f7e32aff330`. Installed Qwen was invoked through `/home/claude-developer/.local/bin/qwen`; its exact resolved identity is recorded in the [compact inventory](2026-09-06-qwen-lifecycle-skill-guardian-remote-r2.json).

The built-in `skill` tool produced real PreToolUse and PostToolUse hooks. Both contained the provider API `tool_call_id` `call_skill` and Qwen’s generated `tool_use_id` `toolu_1788647733813_2r4yf9sit`. The proxy-style expectation used `call_skill`, while the frozen helper’s completed-pair index used `toolu_…`, so it correctly stopped with `EXPECTED_TOOL_HOOK_MISSING`. This records an identifier correlation defect; it is not evidence of a missing Qwen hook.

The remote evidence root is mirrored locally at `/Users/a/hack/sherlock-qwen-skill-guardian-20260906-remote-r2-final`. The remote and local inventories each contain 92 regular files and match byte-for-byte; the canonical inventory text SHA-256 is `9be24345ccfb1319241e16dfc1bc984facf6299bdfe686e8de68bb8ada1870e3`. The test process was explicitly terminated after the durable fault because Qwen retried the rejected request. The fixture now has a bounded-client regression so future failed runs retain their result and stop automatically.

Remote r1 remains preserved at `/Users/a/hack/sherlock-qwen-skill-guardian-20260906-remote-r1`; it failed before Qwen because its nonce did not satisfy helper validation.
