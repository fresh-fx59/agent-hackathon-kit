# V45 qualification r1

## Objective

Qualify frozen v45 through the selected subscription harness, then DeepSeek v4 Flash in Qwen Code. Full paid runs remain explicitly authorized. No corpus report acceptance is implied by preparation.

## Identity

- Code: ccebb05, fast-forwarded onto contabo-prod test checkout. No services changed.
- Package: v45, SHA256 `36a0dfe2c674ac11a13d1dc1b6a0b8c3e720fdb08577c8c9ec5d386cd317e40e`.
- Target input: `/home/claude-developer/hack/sherlock-v45-qualification-20260906-r1`.
- Subscription output: `/home/claude-developer/hack/sherlock-v45-harness-20260906-r1`.
- Raw source preparation: final Winevtx JSONL-only corpus; generic qualification fixture created by the preparation helper.

## Current state

Target preparation passed. Subscription r1 failed before execution; r2 failed at the runner boundary after subscription health. No Qwen investigation request started. Launcher repair is in progress.

## Timeline

- 2026-09-05T22:17:52.761532+00:00 — Reviewed integration ccebb05 synced by clean fast-forward. Started target-contract-probe prepare with schema2 monitored mode, v45, actual remote Qwen0.22 entrypoint, neuraldeep alias identity, current retained rate card. Awaiting precontact result.

- 2026-09-05T22:19:10.721740+00:00 — Target preparation exit0, fresh manifest e2be5328621338acb8883835f92e344ad7985efd896cda8b747e2f39c063b774. Subscription launch r1 failed before secret wrapper execution: supplied PATH omitted Nix /run/current-system/sw/bin, so env could not locate bash. Original stderr retained; output directory was not created. Corrected launcher PATH and started fresh subscription r2, retaining separate stdout/stderr. Current controller phase TESTING after matrix; no model request observed.

- 2026-09-05T22:20:49.725444+00:00 — Subscription r2 passed preflight/matrix and reached signed launch at22:19:17UTC. Root inspected exact launch/profile/package/observer identity and published genuine observation sequence0 at22:19:30.560586UTC. Runner then refused `monitored Qwen limits do not match the sealed unlimited policy`; controller recorded RUNNER_FAILED/RUNNER_HANDSHAKE_FAILED and a durable fault. Source diagnosis: selected launcher removes legacy SHERLOCK_TIMEOUT but omits explicit0, so runner defaults5400 and refuses. Assigned one-field launcher fix plus executable shell-propagation regression. No Qwen investigation request; subscription health contact occurred.

- 2026-09-05T22:23:36.137588+00:00 — Root independently verified launcher regression PASS1test/2.061s with explicit timeout0. Retained complete r2 trace140regular files locally and compared exact remote/local file set and all SHA256 values: PASS. Signed receipt statusFAULT/reasonRUNNER_HANDSHAKE_FAILED, expected/completed tools0, guardian exit-15. No upstream investigation journal exists. Ready for corrected fresh subscription retry.
