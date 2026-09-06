# V50 boundary fixture r1

Status: rejected fixture run; no external provider contact. This report records the terminal evidence and does not treat the owner SIGTERM as an intentional fixture stop.

## Identity

Code `08ac52c`; runtime/skill SHA `0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7`; provider `loopback-only`; driver `6095a501e5b7f338f82276eb309624c165ad108027c332a022592f89bcd83717`; helper `0d6aacbb7ed0d5e2f665113154a79122b5e9f9072cc0ec721bc436c1da44c0ee`.

## Timeline

- 2026-09-06 — V50 full-driver fixture started from a fresh mirrored tree. No paid or external model call was made.
- 2026-09-06 — The result recorded five requests: four normal requests at indices `0,1,2,4` and one auxiliary suggestion at index `3`. The runtime counted two ledger rows and sealed `worklist.tsv`; the pending PreToolUse was denied, and the actual Stop path was allowed and consumed.
- 2026-09-06 — The driver received `Unknown command: /sherlock` twice, retried startup, and then the owner sent SIGTERM. The process exited `143`; `fixture_stop_requested` is empty and no clear occurred. The result is `passed:false` with `AssertionError('driver did not reach the intentional fixture stop')` and provider assertion `fresh task missing before next-stage edit`.
- 2026-09-06 — Local and remote inventories were compared after mirroring. Both contain 130 comparable files with equal path, byte count, and SHA-256. Inventory generation excluded directory names exactly `home` and `updates`; the original remote tree was left untouched.

- 2026-09-06T16:36:18.556115+00:00 — Root cause evidence:39copies of the rejection banner appear in584772PTYbytes. Exact expanded startup hook16:30:21.497666Z predates driver retry detection16:30:26. Duplicate original input16:30:36.513245Z retains the same session. Harness retry must consult exact hook acceptance before retyping. Provider contact above means external; all five requests used the scripted loopback provider.


## Proven facts and hypothesis

The proven terminal facts are the result fields and preserved driver event sequence above. The likely driver cause is startup readiness/command handling: the wrapper treated the initial `Unknown command: /sherlock` as a startup condition, retried the command, and did not reach the intended fresh-task/reseed stage. This is a cause hypothesis for the driver lane, not a runtime acceptance finding.

## Evidence

Raw result, driver events, launch streams, checkpoint output, and both inventories are under `docs/run-reports/artifacts/2026-09-06-v50-boundary-fixture-r1/`.
