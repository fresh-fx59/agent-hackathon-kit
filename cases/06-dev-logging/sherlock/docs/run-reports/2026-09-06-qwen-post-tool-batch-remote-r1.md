# Installed Qwen PostToolBatch loopback smoke — remote r1

- Date: 2026-09-06
- Installed client: `/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js` (0.22.0); SHA-256 `68cb29eb7ccc936d78ece5564ef55cae41a55b630e6657dc417c1f2e561cf4c9`
- Frozen helper SHA-256: `9e91112fe810b505b5cce9e37a42d8f5838d0e2d33a781e46e5f2da4d96ee685`
- Provider: localhost scripted SSE only; no credentials or external provider contact.
- Remote evidence: `/Users/a/hack/sherlock-qwen-posttoolbatch-20260906-remote-r1`
- Local verified mirror: `/Users/a/hack/sherlock-qwen-posttoolbatch-20260906-remote-r1`
- Runner exit: 0; fixture result: `True`.

The invalid-directory response registered `call_invalid` before relay. Qwen reported it through PostToolBatch as `invalid_tool_params` with `not_started`; the helper recorded the rejection and permitted the separate corrected call. The corrected call received matching PreToolUse/PostToolUse hooks and the scenario completed three requests without a lifecycle fault.

The no-batch invalid-directory case and the PostToolBatch-only executed-tool case both stopped with `EXPECTED_TOOL_HOOK_MISSING` and only one successful provider response. The latter’s denied PostToolBatch is retained in `hook-fault-events.jsonl`; its raw input SHA-256 `58401ca9e71f28d347980c5b1cc0ac51c3ebe8264bec4e3d8c05efce2d293081` matches a fresh digest of the retained bytes. Accepted batch input remains in `post-tool-batch-events.jsonl`.

The local mirror matches the remote regular-file set and SHA-256 values: 232 files, `True` paths, `True` digests. The compact machine-readable summary is [the JSON report](2026-09-06-qwen-post-tool-batch-remote-r1.json).
