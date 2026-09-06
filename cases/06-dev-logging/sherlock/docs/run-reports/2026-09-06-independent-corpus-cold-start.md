# V48 independent-corpus cold-start launch package

Status: prepared — scripts syntax-validated; no target probe, paid admission, or full corpus run launched.

## Decision

The existing v48 subscription harness acceptance can be reused conditionally. `paid-admission.py` requires the harness receipt to have `proof_scope: harness_only`, then compares its settings, tool schema, selected package, and report/citation/state/triage gate digests with the fresh target profile. It does not bind the harness receipt to the winevtx corpus or its prompt. Therefore a fresh harness is unnecessary when those shared digests remain byte-identical to the frozen v48 acceptance.

The independent corpus still requires fresh target preparation and probe, a fresh admission directory and paid manifest, and a new full-input package. The target receipt/profile and full-input package are bound independently by the admission manifest. No prior findings, worklists, checkpoints, or pattern cards are copied.

## Frozen compatibility constraints

- Runtime package: v48, `be96db39cc0f4b8d36324dea9b66fdf2880d9dbefb1bea78fc927269f769c9ba`.
- Interactive driver: `6095a501e5b7f338f82276eb309624c165ad108027c332a022592f89bcd83717`.
- Lifecycle helper: `b67790b700d0406aa0909e799b0672b4be8a0ab45088e0400b40b3d3032e4d2c`.
- The reusable harness is `/home/claude-developer/hack/sherlock-v48-harness-20260906-r3`; launch-time checks must reject missing acceptance or shared-digest drift.
- The independent source must provide `corpus/`, `inventory-key.json`, `prompt.txt`, and `provenance/` below `/home/claude-developer/hack/sherlock-final-inputs-20260906/independent`.

## Prepared scripts

- `/tmp/sherlock-v48-prepare-independent-r1.sh` creates the fresh target `/home/claude-developer/hack/sherlock-v48-independent-qualification-20260906-r1` from the independent corpus. The rate snapshot remains the reviewed parent input because it is a provider-cost contract, not corpus evidence.
- `/tmp/sherlock-v48-launch-target-independent-r1.sh` runs the target-contract probe only after the fresh manifest and reusable harness acceptance exist.
- `/tmp/sherlock-v48-launch-full-independent-r1.sh` creates `/home/claude-developer/hack/sherlock-v48-independent-admission-20260906-r1` and `/home/claude-developer/hack/sherlock-v48-independent-full-20260906-r1`, writes a new `winevtx-independent` input package with fresh corpus/inventory/prompt/provenance hashes, and then invokes the paid controller.

All three scripts passed `bash -n`. They were not executed; no remote state or provider call was made.

## Evidence basis

`eval/bench/paid-admission.py` binds the reusable harness only through the shared settings, tool-schema, selected-package, and four gate digests; it separately hashes the fresh target receipt/profile, full-input package, and run budget. `eval/bench/target-contract-probe.py` seals the target runtime/settings/gates and builds a new fixture from the supplied source corpus. This is why the independent corpus changes the target and input-package bindings while leaving harness reuse conditional on the frozen shared digests.

## Launch order

Run prepare, inspect the produced manifest/profile, run the target probe, verify the resulting receipt and shared digest comparison, then run the full script. If any shared digest, source-package shape, or package identity check fails, stop before provider contact and prepare a fresh harness qualification.

## Execution correction

- 2026-09-06T14:48:50.953231+00:00 — Root review removed inherited `test ! -e "$HARNESS"` from prepare: reuse requires an existing harness. Updated harness reference to r4. Actual remote prepare on independent corpus exited1 before contact: PROBE_SOURCE_CHANGED because contract-probe-fixture.py fixed recipe requests `Security.jsonl`, absent from independent corpus. No runtime verdict. Failed root `/home/claude-developer/hack/sherlock-v48-independent-qualification-20260906-r1` is preserved. Next source review distinguishes model-contract calibration fixture from the cold full corpus; do not rename input or inject Winevtx evidence into independent workspace.

## Probe versus full-corpus binding

The target probe must use a WineVTX calibration source containing the recipe-required `Security.jsonl`; the independent corpus may lack that representative file. The probe receipt/profile do not bind a corpus identity. `paid-admission.py` separately binds the fresh full-input package and only rejects invalid schema or declared incomparable differences. The full run can therefore use the independent corpus with its own inventory, prompt, provenance, and cold-start hashes after a fresh WineVTX calibration probe.
