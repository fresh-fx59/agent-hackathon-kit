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
- Both mirrors exclude only `home/updates`; the complete terminal directories remain outside Git.
- Inventory artifacts, exact fixture sources, and r3 launch siblings are committed under `artifacts/2026-09-06-single-invocation/`.

## Decision and next step

Preserve r1 as an invalid timing fixture, r2 as the expected red contextless-request reproduction, and r3 as a combined-startup run with no accepted skill-scoped Stop. Run the corrected fixture against the v47 candidate before any paid qualification. No corpus acceptance is recorded.
