# Installed Qwen monitored clear/reseed fixture — r2

Provider-free installed-Qwen 0.22.0 PTY fixture passed on `contabo-prod` as `claude-developer`. It used an isolated HOME, toy skill, localhost scripted SSE endpoint, frozen lifecycle helper, and the actual `interactive-drive.py` clear-proof functions. No provider, credential, corpus, or production runner was used.

Frozen identities: Qwen entrypoint `68cb29eb7ccc936d78ece5564ef55cae41a55b630e6657dc417c1f2e561cf4c9`; lifecycle helper `b67790b700d0406aa0909e799b0672b4be8a0ab45088e0400b40b3d3032e4d2c`; driver `0c3cd3787aef3475b30c40e55bf8df1b2ca2a37bd9b77e81d5823419ede3d03a`.

The helper journal accepted startup and clear `SessionStart`, initial, `/sherlock`, and exact reseed `UserPromptSubmit` hooks. The driver captured the anchor before typing `/clear` and returned `complete`, binding old session `081e5e8b-6638-4c14-8a63-8419c9fe3b19` to clear session `66635fc5-e1ba-4729-a41b-5e791c02dfb5`. Its retained anchor, clear, skill, and reseed input digests are in `driver-clear-proof.json`.

Remote evidence: `/tmp/qwen-monitored-clear-reseed-20260906-r2`. Local selected-artifact mirror: `/Users/a/hack/qwen-monitored-clear-reseed-20260906-r2`; it excludes only Qwen's unexecuted `home/updates` auto-update cache. Raw helper bridge input, stdout, stderr, exit, observer journals, request/SSE bodies, PTY transcript, settings, argv, hashes, and result are retained.

r1 is retained as a fixture bridge-argv failure: no hooks reached the helper and `capture_clear_anchor` correctly raised `ClearProofError`. r2 fixes only the bridge argument ordering and passed.
