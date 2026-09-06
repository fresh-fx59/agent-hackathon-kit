# V46 qualification r1

## Objective

Run frozen Sherlock v46 from a fresh Winevtx corpus target through the selected subscription harness and then the approved DeepSeek v4 Flash in Qwen Code. This note records launch state only; no corpus report acceptance is claimed.

## Identity

- Runtime: v46 frozen, canonical SHA-256 `bc000c21e46c8da3312df80d18e7a833735af38787f6a950b3dbca96f8455bb3`
- Runtime commit: `162d36a`
- Manifest: `5b85a178030a7bf6205b2a9ebf810e9a766ef23df1fe06608290770dc28b3396`
- Trace: `run-20260906T132201Z-b3f54f8a4477`
- Target: `/home/claude-developer/hack/sherlock-v46-qualification-20260906-r1`
- Subscription harness output: `/home/claude-developer/hack/sherlock-v46-harness-20260906-r1`

## Current state

Blocked during observation. The terminal lifecycle recorded `OBSERVATION_STALE` at `2026-09-06T13:31:06.700Z`; the driver was then stopped with SIGTERM. The last accepted root observation was sequence 11 at `13:30:06.626Z`, so the observed gap was 60.065 seconds. This terminal fault is preserved as the supervisor's primary verdict; the later SIGTERM is not substituted for it.

The run made 34 completed gpt-5.5 subscription requests. The generated report is an artifact of the interrupted run and is not an accepted corpus report. Provider/model-under-test acceptance was never reached.

Two findings must remain separate:

1. **Root input defect:** the interactive driver typed bare `/sherlock` at `13:23:31`, then typed a first prompt containing `/sherlock` at `13:23:40`, leaving a duplicate startup command queued. The actual submitted prompt, `/sherlock\n\nInvestigate...`, arrived at `13:28:19` in the old session after triage boundary 1, while its receipt remained pending. The model continued draft work before the clear boundary.
2. **Terminal supervisor verdict:** after root observation sequence 11, the observation deadline elapsed and the controller recorded `OBSERVATION_STALE` at `13:31:06.700Z`. The run was stopped around that time. This is the terminal run outcome, independent of the earlier driver input defect.

## Timeline

- 2026-09-06T13:22:01Z — Fresh v46 qualification launched with the frozen runtime and manifest above. The harness completed its provider-free suites: 43 tests, 17 tests, and 69 tests (one skipped); all passed.
- 2026-09-06T13:23:31Z — Driver typed bare `/sherlock`.
- 2026-09-06T13:23:40Z — Driver typed the first prompt containing `/sherlock`, creating a duplicate queued startup prompt.
- 2026-09-06T13:28:19Z — Actual submitted prompt `/sherlock\n\nInvestigate...` appeared in the old session after triage boundary 1; the receipt remained pending while the model continued draft work.
- 2026-09-06T13:30:06.626Z — Root accepted observation sequence 11.
- 2026-09-06T13:31:06.700Z — Controller recorded terminal `OBSERVATION_STALE` after a 60.065-second observation gap. Root sent SIGTERM to the owned driver around the same time. This is a blocked run, not an accepted report.

## Gates pending

1. Verify signed launch, lifecycle, model identity, and complete raw trace.
2. Review every request, response, tool page, report, and attribution record from the fresh corpus.
3. Run report and corpus gates, including report completeness and evidence citations.
4. Record the final verdict only after all gates pass; a successful process exit alone is insufficient.

## Evidence preservation

- Local harness mirror: `/Users/a/hack/sherlock-v46-harness-20260906-r1` (461 entries; local/remote inventory equal).
- Local target mirror: `/Users/a/hack/sherlock-v46-qualification-20260906-r1` (68 entries; local/remote inventory equal).
- Launch siblings: `/Users/a/hack/sherlock-v46-harness-20260906-r1.launch.stdout` and `.launch.stderr`.
- Committed compact evidence: `artifacts/2026-09-06-v46-qualification-r1/`.
- Raw mirrors retain the complete terminal run outside Git; no files were omitted from the local mirrors. The committed artifacts contain inventories, launch siblings, and the compact terminal summary rather than duplicating the full raw mirror.
