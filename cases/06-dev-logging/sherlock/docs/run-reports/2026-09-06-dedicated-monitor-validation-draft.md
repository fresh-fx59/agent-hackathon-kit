# Dedicated monitor validation report (draft)

Status: waiting for Terra’s implementation lane and focused test results. No result is claimed here.

## Scope

Validate the dedicated remote reviewer path against the fixed 60-second observation gate and 600-second request watchdog. Preserve the existing lifecycle gates, avoid synthetic heartbeats, and keep terminal faults immutable.

## Focused evidence to record

- Implementation commit and helper/driver SHA256.
- Test command, exit code, elapsed time, test count, and exact failing assertion if any.
- Reviewer model/effort, tool policy, source snapshot SHA256 and byte size.
- Primary model usage and auxiliary subagent usage separately; list-price estimates versus CLI aggregate; no invoice claim.
- Live monitor sequence accepted before and after any terminal fault, with late observations explicitly excluded.
- Direct reviewer output and raw stderr/stdout hashes.

## Required cases

1. Healthy completed observation: reviewer continues within the 60-second deadline.
2. Terminal stale/blocked controller: reviewer stops without publication.
3. Historical snapshot: clearly labeled reconstruction and excluded from live acceptance.
4. Provider-under-test request pending: monitor does not synthesize progress or weaken the 600-second watchdog.
5. Reviewer failure/timeout: fail closed and preserve the original lifecycle verdict.

## Acceptance record

- Focused test result: pending.
- Implementation review: pending.
- Live qualification: pending; this draft does not authorize or claim a run.
