# Monitored paid-admission component — focused verification

## Scope

This report covers only `eval/bench/paid-admission.py` and its provider-free
fixtures. Controller, runner, proxy, lifecycle, audit, manifest, runtime, and
registration changes remain separately owned.

## Result

Admission now delegates target-profile structure validation to the shared
`run-manifest.py` validator. Schema1 profiles and strictly positive finite
budgets remain accepted unchanged. Schema2 accepts only the reviewed
`operator_monitored` profile and a budget with exactly the existing budget keys
plus `execution_mode`: the four aggregate ceilings are null, remaining values
are positive, and `request_timeout_ms` is exactly 600000.

For schema2, the target receipt, profile, and harness binding must agree on an
explicit selected package version and digest. The historical
`skill_v44_sha256` harness binding is explicitly documented and tested as a
digest slot for the selected package, never a selector for v44. Admission also
refuses an overlong target receipt and an action whose expiry exceeds the target
receipt. Existing target/action nonce consumption remains unchanged.

## Timeline

- 2026-09-05: Added schema2 monitored fixtures before implementation. The first
  run failed because paid admission required schema1 profile fields; the budget
  assertion also stopped at that profile failure. This reproduced the reported
  compatibility defect without provider, proxy, secret, or target contact.
- 2026-09-05: Replaced duplicate profile schema checking with the shared
  run-manifest validator; added explicit package identity and reviewed budget
  validation. The alias fixture exposed the new 30-minute alias receipt limit;
  its fixture was corrected to a 30-minute receipt and action expiry bounded by
  it. No legacy rule was weakened.
- 2026-09-05: `python3 -m unittest tools.tests.test_paid_admission && python3
  -m py_compile eval/bench/paid-admission.py tools/tests/test_paid_admission.py`
  passed: `Ran 16 tests in 0.168s`, `OK`.
- 2026-09-05: Added a finite schema1 regression with explicit package identity;
  it remains accepted when the target receipt and legacy-named harness digest
  slot bind that selected package, rather than silently selecting v44.
- 2026-09-05: Scoped review identified a defensive schema2 `skill_sha256`
  access that could become a `KeyError` if an upstream validator changed. The
  package comparison now uses an empty fallback and a missing digest has a
  regression proving the clean `INPUTS_INCOMPARABLE` refusal, never admission or
  an uncaught exception.
- 2026-09-05: `python3 -m unittest tools.tests.test_run_manifest
  tools.tests.test_paid_admission` passed: `Ran 87 tests in 5.509s`, `OK`.
- 2026-09-05: Root review found that profile and budget were validated
  independently, allowing a finite schema1 profile with a monitored schema2
  budget (and the converse). Added both regression fixtures; each initially
  failed because admission succeeded. Admission now compares their effective
  execution modes after their individual schema validation and refuses either
  mismatch as `INPUTS_INCOMPARABLE`. Focused regressions passed (`Ran 2 tests
  in 0.013s`); the combined manifest/admission suite passed (`Ran 89 tests in
  5.140s`, `OK`).

## Integration requirements

The controller, runner, proxy, and terminal audit must consume
`execution_mode` as `operator_monitored`, preserve null aggregate limits without
arithmetic or substituted caps, retain the 600-second per-request watchdog, and
carry the returned package version/digest through their sealed receipts. They
must keep per-request reservation/accounting and reject missing or mismatched
supervision and terminal outcomes. Launch must use the accepted action's sealed
time for expiry evaluation and retain one-use action and target nonce evidence.

## Review adjudication

Claude Sonnet5 medium review is retained in run-reports/2026-09-05-paid-admission-review-r1.json. Its claimed missing-skill KeyError is not reachable through the current shared validator, which already refuses the missing field. Root rejected the immediate-crash claim; the fallback and regression are defensive hardening. Root separately found and reproduced profile/budget mode mismatch admission. Both mismatch directions now refuse; worker shared suite passed89 tests/5.140s. No target launch.
