# Sherlock single invocation fixture experiments

## Status

Development evidence only. These provider-free loopback fixtures test one combined startup invocation and skill-boundary handling. They do not qualify v47, do not contact a paid model, and do not accept a Winevent corpus report.

## Identity and limitation

The lane targets the v47 candidate repair, but r1 and r2 used the prior frozen v46 driver snapshot (`162d36a`, driver SHA-256 `2882f88ef73282256c409c5997c5fda8e413dbd096d8ef32e7c0597dfecb61c8`). The fixture sources and raw outputs are retained exactly. Any v47 claim requires a fresh run with the candidate runtime and the corrected fixture.

## r1 — rejected artificial settle timing

The fixture was invalid before provider contact. It used an artificial 0.5-second settle interval. The driver typed `/sherlock` at `13:40:49Z` and the task prompt at `13:40:52Z`; the first `SessionStart` arrived at `13:40:53.974Z`. The initial alarm was swallowed by the driver; the owned process remained live until root sent SIGTERM at `14:01:51Z`, after which the terminal result recorded driver exit 143. No provider request or model-under-test result exists. The terminal evidence is therefore a timing-bound fixture failure, not evidence against the single-invocation design.

The checkpoint remained at triage boundary 0 with zero work items, and the report retained the untouched synthesis placeholder. The source snapshot is preserved under `artifacts/2026-09-06-single-invocation/r1/`.

## r2 — expected red contextless skill request

The production settle interval was six seconds. The fixture made one loopback request and recorded `AssertionError('contextless skill request before task/reseed')` in `provider_errors`; the driver returned 143 after the intentional fixture stop path was not reached. Root terminated the owned driver. This is the expected red signal: the first bare skill request arrived without the task/reseed context required by the fixture. It is an error-stop result, distinct from the fixture's intended success-stop criterion.

The run proves that the old driver/fixture composition still exposes the contextless `/sherlock` request. It does not prove the v47 candidate fix, because the run used the old v46 driver snapshot and made no paid request. The source snapshot is preserved under `artifacts/2026-09-06-single-invocation/r2/`.

## r3 — combined startup reaches normal requests but no skill-scoped Stop

The corrected startup produced two normal loopback requests and repeated auxiliary suggestion responses. It recorded seven total requests, with no provider errors, but never reached a skill-scoped Stop execution: the receipt remained pending while the driver repeatedly reported `Stop has not accepted the stage pause`. Root terminated the owned driver at `13:52:03Z` after the fixture deadline path failed to produce its intentional stop. This is evidence that combined startup removes the r2 contextless request, while the Stop receipt remains unproven. It is not a v47 acceptance result.

## Evidence integrity

- r1 local mirror: `/Users/a/hack/qwen-single-invocation-20260906-r1`; 1,454 entries after the terminal result was written; local/remote inventory equal.
- r2 local mirror: `/Users/a/hack/qwen-single-invocation-20260906-r2`; 1,456 entries; local/remote inventory equal.
- r3 local mirror: `/Users/a/hack/qwen-single-invocation-20260906-r3`; 1,499 entries; local/remote inventory equal.
- r4 local mirror: `/Users/a/hack/qwen-single-invocation-20260906-r4`; 1,530 entries; local/remote inventory equal.
- r5 local mirror: `/Users/a/hack/qwen-single-invocation-20260906-r5`; 1,528 entries; local/remote inventory equal.
- r6 local mirror: `/Users/a/hack/qwen-single-invocation-20260906-r6`; 1,533 entries; local/remote inventory equal.
- Both mirrors exclude only `home/updates`; the complete terminal directories remain outside Git.
- Inventory artifacts, exact fixture sources, and r3 launch siblings are committed under `artifacts/2026-09-06-single-invocation/`.

## Decision and next step

Preserve r1 as an invalid timing fixture, r2 as the expected red contextless-request reproduction, and r3 as a combined-startup run with no accepted skill-scoped Stop. r4 is rejected for incomplete fixture command and shell capture bypass; r5 is rejected for watcher capture race; r6 passes the corrected v48 loopback lifecycle gate. Paid qualification still requires a fresh v48 run, and no corpus acceptance is recorded.

## r4 — receipt consumed and fresh clear observed, fixture command incomplete

The v48 candidate driver made eight loopback requests and five normal requests. The first Stop consumed the receipt and the clear-to-combined-fresh-reseed sequence succeeded. The fixture then failed because its partial command omitted the required `--done draft` argument; the resulting stale handoff was correctly denied. An unexpected normal request at index 4 produced the fixture assertion. The PATH capture shim bypassed the Qwen hook shell, so the raw Stop execution count was zero despite the receipt consumption. This is useful lifecycle evidence, but the fixture result is rejected and does not prove the hook path.

## r5 — lifecycle passed, capture teardown raced

The fixture made six loopback requests and four normal requests with no provider errors. Both Stop receipts were consumed and clear verification succeeded. The watcher sent SIGTERM while recording the second Stop; its input existed but its output and exit files did not, so the result ended in `FileNotFoundError`. This is a rejected capture race, not a lifecycle failure.

## r6 — corrected watcher passes

The corrected watcher waited for both Stop allow outputs and exit 0 before issuing the intentional fixture stop. The run made seven loopback requests and four normal requests, preserved the marker, consumed both receipts, and verified exact clear-to-new-session transitions. The expected fixture SIGTERM ended the run with driver exit 143 after the assertions had passed. This is a passing v48 loopback lifecycle fixture; it is not paid qualification or corpus acceptance.

## r7 — newline fixture red

The old v48 driver produced two normal loopback requests, but the first startup newline mismatch prevented the intentional fixture Stop from being reached. The terminal result is `AssertionError('driver did not reach the intentional fixture stop')`; no provider errors were recorded. This is the preserved red baseline for startup newline handling.

## r8 — newline fixture green

The corrected v48 driver produced six loopback requests, four normal requests, and two accepted Stop/clear cycles. The fixture passed with no provider errors. This validates the localhost newline normalization and receipt/clear lifecycle under the fixture; it is still not paid DeepSeek qualification or corpus acceptance.
