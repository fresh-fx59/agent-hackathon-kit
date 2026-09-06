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
