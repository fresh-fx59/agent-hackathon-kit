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

Launched and under root observation. The target output and raw run evidence are not yet available for acceptance review. No model-under-test verdict, Winevtx report verdict, or corpus acceptance is recorded here.

## Timeline

- 2026-09-06 — Fresh v46 qualification target prepared and launch started with the frozen runtime and manifest above. Root is monitoring the signed lifecycle and retaining raw outputs. Further findings must be appended here after each observation.

## Gates pending

1. Verify signed launch, lifecycle, model identity, and complete raw trace.
2. Review every request, response, tool page, report, and attribution record from the fresh corpus.
3. Run report and corpus gates, including report completeness and evidence citations.
4. Record the final verdict only after all gates pass; a successful process exit alone is insufficient.
