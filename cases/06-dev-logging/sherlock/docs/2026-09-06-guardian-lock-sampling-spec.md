# Guardian observation sampling inside the lock

Status: approved scoped harness repair.

## Evidence

`run_guardian()` sampled `started = time.monotonic_ns()` before calling
`check_supervision(..., now_monotonic_ns=started)`. Both observer publication
and supervision acquire the same observer lock. If the publisher wins that
lock after `started` and before validation, its valid observation has a later
monotonic timestamp; validation then falsely creates permanent
`OBSERVATION_FUTURE`.

`check_supervision()` already samples `time.monotonic_ns()` while holding that
lock when no explicit test time is supplied. The r50 full monitored runner
failed this exact race; the publisher was valid.

## Change and boundary

`run_guardian()` calls `check_supervision()` without a supplied timestamp, so
validation samples only after acquiring the shared lock. Its pre-lock `started`
remains only for guardian outcome timing and interval scheduling.

Do not change observation age limits, add a future-time tolerance, alter
publication, or touch the frozen v50 runtime.

## Acceptance

A deterministic test publishes a valid observation after the guardian enters
its first supervision attempt but before validation; it must not create a
fault. Existing direct future and stale observation tests remain failures.
