# Dedicated monitor focused validation

Status: offline focused verification passed; live subscription qualification remains pending. No source fixes or commits were made in this verification lane.

## Commands and results

- `python3 tools/tests/test_dedicated_review_monitor.py` — exit 0, 0.40870725014247 s, 8 tests passed. Raw stdout/stderr are preserved under the validation artifact directory.
- `python3 eval/bench/test_lifecycle_supervisor.py` — exit 0, 0.7248807498253882 s, 44 tests passed.
- `python3 tools/tests/test_full_monitored_runner.py` — exit 0, 0.24632162507623434 s, 14 tests passed. This is the current monitored runner test; no obsolete fake-Qwen discovery path was invoked.
- `python3 -m compileall -q eval/bench tools` — exit 0, 0.3373170839622617 s.
- `python3 eval/bench/test_version_gate.py` — exit 0, 1.155 s, 12 tests passed.

## Bounded validation failure

The requested `python3 eval/bench/diffcheck.py` command could not run because `eval/bench/diffcheck.py` does not exist in the current checkout. The exact stderr is preserved as `diffcheck.stderr`; this is a missing-command validation gap, not a claimed diffcheck pass. No substitute was silently treated as equivalent.

## Pins and scope

The run used the current checkout. The frozen v50 registry entry remains `0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7`. The monitor/helper/prompt hashes were not rewritten by this lane. These are offline results only; no live reviewer publication, Qwen qualification, or DeepSeek corpus run is claimed.

Exact command outputs and measured metadata are under `docs/run-reports/artifacts/2026-09-06-dedicated-monitor-validation/`.

## Verification correction

- 2026-09-06 — The earlier `diffcheck.py` entry was a command-selection mistake in this report lane, not a source or validation failure. The intended `git diff --check` completed with exit 0.
- 2026-09-06 — The actual v50 gate `python3 eval/bench/version-gate.py --version v50 --skills-root skills` completed with exit 0 and selected `skills/v50`. The earlier `test_version_gate.py` result remains a separate 12-test unit suite result.

## Real-envelope regression correction

- 2026-09-06 — The dedicated monitor regression was rerun after binding validation to the preserved Claude CLI envelope shape. `python3 tools/tests/test_dedicated_review_monitor.py` exited 0 with **9/9 tests passed**; this supersedes the earlier 8-test count while preserving that earlier result as historical evidence. The test now covers `is_error=false`, `subtype=success`, `terminal_reason=completed`, `modelUsage.canonicalModel=claude-sonnet-5`, accepted auxiliary Haiku usage, wrong-primary rejection, explicit-error rejection, and malformed-envelope rejection.
- Implementation hash: `eval/bench/dedicated-review-monitor.py` = `65b189e5531d5bb80ae2186dfc0b3bdf7e2f10d9266b077dc1b4a09b2807c9ee`.
- Focused test hash: `tools/tests/test_dedicated_review_monitor.py` = `e48364396ef010e63561236d77ccc40ad89cc303ac7a87193f1196fd7c523649`.
- `py_compile` and `git diff --check` also exited 0. These are offline provider-free results; no live reviewer publication or Qwen/DeepSeek run is claimed.

## Narrow monitor blocker regressions

- 2026-09-06 — Focused monitor suite rerun after three reproduced fixes: `latest_tool` advances through completed Post events and denied Pre events only, never a pending Pre; audit rehashes every bound review object; authoritative mutable checkpoint state is excluded from the append-only workspace tree; hook, post-tool, and guardian JSONL entries retain raw bytes/digests plus validated decoded event objects, while registry state is captured. `python3 tools/tests/test_dedicated_review_monitor.py` exited 0 with **10/10 tests passed**.
- Exact implementation hash: `eval/bench/dedicated-review-monitor.py` = `ae8eb601d187d482809f96d919d421bcd1f0f54c02c39abfbfa974e23d9fe843`.
- Exact focused-test hash: `tools/tests/test_dedicated_review_monitor.py` = `b03ac717dd5190b989e1fa88cfcaa954762d6d238c9278ea0b7c719e40edc70a`.
- `py_compile` and `git diff --check` exited 0. These remain offline provider-free results; no live qualification or corpus acceptance is claimed.

## Readable nested receipt and provider evidence

- 2026-09-06 — The focused monitor suite passed **11/11** after adding strict decoding and digest validation for lifecycle receipt `input_base64`/`input_sha256`, exposing decoded tool input/output/error/result fields while retaining raw receipt bytes. Gzip provider request/response artifacts now retain raw compressed bytes and expose validated UTF-8 text plus JSON when applicable; registry and guardian traces are included in the snapshot source set. The regression includes a known `gpt-5.5` provider marker and a known denied command marker.
- Exact implementation hash: `eval/bench/dedicated-review-monitor.py` = `bedc54372e287ea45c2f19f439f6334569f93c72b699845c88b53854fdcfce26`.
- Exact focused-test hash: `tools/tests/test_dedicated_review_monitor.py` = `8ec2dd2bdff8591b4e63fe501c553c11f94046b8edf8c6c87f10257d0449ef45`.
- `py_compile` and `git diff --check` exited 0. This remains offline provider-free verification; no live Qwen or DeepSeek run is claimed.

## Preserved r3 real-corpus snapshot proof

- 2026-09-06 — Read-only `collect_snapshot` was run against the preserved local r3 run `/Users/a/hack/sherlock-v50-harness-20260906-r3/runs/run-20260906T165936Z-fc4e0e5b1e36` with the current monitor, without reviewer/provider/lifecycle publication. It produced a 3,553,465-byte snapshot, SHA256 `5532c5939c27dd8f5524de7c054609f3cf24300fe1fb69eab54e71baac04e07c`, with 19 dynamic and 6 state entries.
- The snapshot contained 8 decoded lifecycle hook events. A representative decoded `tool_input.command` was `python3 .../skill-catalogue/log-rca/tools/checkpoint.py resume --work ./work` (full path retained in the generated snapshot; the report shortens it only for readability).
- The production `upstream-bodies` directory was captured and decoded: 13 gzip entries were readable while their raw compressed SHA256 values remained bound. A representative provider request retained raw SHA256 `7536e06d550b7c817931378b21ca8cc294e27f611d10ebefc51534f90770dd91`, decoded text began with a JSON request whose model was `gpt-5.5`, and the decoded body contained provider response/request text. All dynamic/state entries retained 64-character raw digests.
- The run used no provider call, no monitor publication, and no lifecycle mutation. A batched read-only `contabo-prod` check reported `active_processes=0`; no active Qwen, monitor, lifecycle, or Sherlock process was observed.
- Current implementation hash: `eval/bench/dedicated-review-monitor.py` = `b345fb3164d6e9b597d3e80cf33f59d8eb3bd0ac502127360c92db63d8eec653`; focused-test hash remains `8ec2dd2bdff8591b4e63fe501c553c11f94046b8edf8c6c87f10257d0449ef45`. Focused suite remains 11/11.

## Incremental r3 snapshot measurement

- 2026-09-06 — A provider-free cursor measurement used line-boundary cursors before later r3 evidence and retained complete gzip bodies. The resulting incremental snapshot was 3,402,691 bytes, SHA256 `93191ef3885b1418cbda1e63d29a9f1c8a36a4443e158a853a290cb13f8d034b`, with 19 dynamic and 6 state entries and 7 decoded events. The snapshot contained 590,248 bytes of base64 raw-delta fields and 1,448,233 bytes of decoded gzip text; 415,352 compressed-body bytes were retained in full. This quantifies the duplication tradeoff without invoking a reviewer or changing the preserved run.
- The current implementation hash changed from `bedc54372e287ea45c2f19f439f6334569f93c72b699845c88b53854fdcfce26` to `b345fb3164d6e9b597d3e80cf33f59d8eb3bd0ac502127360c92db63d8eec653` solely by adding the production `upstream-bodies` tree alongside the fixture-era `.upstream.bodies` path; no provider or runtime run was performed.

## Candidate final source validation

- 2026-09-06 — Candidate monitor source passed the focused **14/14** suite; compile and diff checks passed. Candidate monitor hash: `a6c8836e76e86e5dfe422c89caed2ec7d3c5e6d662ee9b04b7bab4a0f8f246f4`; candidate focused-test hash: `9f52e2eb301ae66acb15ee4189f5fe3ee069703ce88ef689f5479340cfb285c8`. This candidate remains pending Sol’s final review and is not yet committed or synchronized.
