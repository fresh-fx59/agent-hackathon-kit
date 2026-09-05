# Winevtx full-corpus state census — offline experiment

Verified frozen v45 registration, then ran its unchanged statecheck.py against all
143 rendered Winevtx files with no report, writing a fresh census TSV and raw JSON.
It returned exit4 in0.638s: nine state-change groups comprising864 matching records.
The source corpus still has90,267records; the checker's total_records counts its
state-change candidates, not every input event. Groups are obligations for later
report accounting, not findings of compromise.

Exit4 and blocking1 are expected and retained: stderr explicitly states that a
census without --report is not a passed gate. No report was manufactured. This
experiment establishes census scale and a developer verification baseline only.
Do not copy its groups/findings into the fresh target prompt. Package remains
36a0dfe2c674ac11a13d1dc1b6a0b8c3e720fdb08577c8c9ec5d386cd317e40e.

Exact argv, raw outputs, TSV and hashes are under
/Users/a/hack/sherlock-full-census-20260906-r1. The paired JSON preserves the full
census, terminal result and inventory. No model or provider was contacted.

## Timeline

- Verified v45 registration and launched state census in an exclusive output root.
- Observed exit4/0.638s, nine groups/864candidate records and explicit no-report
  diagnostic. Recorded these as an incomplete gate, not an accepted investigation.
