# measure/ — the Sherlock measurement rig

Answers, for every missed defect: **was the evidence never opened (coverage), or
opened and not connected (reasoning)?** Deterministically, with no LLM call.

    # build per-defect slices (key and corpus live OUTSIDE this repo)
    SHERLOCK_ANSWER_KEY=~/hack/case06-measure/hetero-answer-key/answer-key.json \
    SHERLOCK_CORPUS=~/hack/case06-measure/hetero-corpus \
    python3 slice.py --out cases

    # tier 1 — iterate on one defect. gate.sh -> report-case.py -> score_case.score
    # needs BOTH keys: the model under test (SHERLOCK_API_KEY) AND the judge
    # (JUDGE_API_KEY). with-secret.sh injects one secret per invocation, so nest it —
    # verified the nesting composes (both vars land in the innermost env) before
    # documenting this; it is not just plausible-looking shell.
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- \
      with-secret.sh eval_broker_api_key --env JUDGE_API_KEY -- \
        ./gate.sh 1 v6 D11
    # tier 2 — MANDATORY before accepting anything
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- \
      with-secret.sh eval_broker_api_key --env JUDGE_API_KEY -- \
        ./gate.sh 2 v6

## What a green slice does NOT prove

A slice is a smaller, quieter haystack than the 649 MB corpus. **Slice-green does
not imply corpus-green**, and a tier-1 pass proves only that the fix does something
— not that it broke nothing else. Only a tier-3 full-corpus number may be quoted as
a benchmark result.

## Judge

`gpt-5.5` via the cliproxyapi broker, secret `eval_broker_api_key` (subscription).
Neutral to both the model under test and the skill's author, and it reproduces the
historical `eval/scores.jsonl` column. Do not switch judges casually: the same v5
report scored 3/11 and 5/11 under two different judges.

## Tier 0 — the capability floor

    python3 micro.py --out cases
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ./gate.sh 0 v6

Nine hand-written corpora, one per capability in the answer key's `requires`
vocabulary. Each is a few lines long and isolates a single skill: stitching an
interleaved stack trace, unescaping a docker `log` field, joining two formats on
time with no shared id, finding one success among 120 failures, seeing a RATE shift
rather than a new error, reading invented severity words (`ALARM`, `FATALITY`),
reading a Russian log where `grep ERROR` returns nothing, decompressing a `.gz`.

**Green at tier 0 proves nothing about the corpus** — these are far easier than a
slice, which is itself easier than the 649 MB corpus. RED at tier 0 is the valuable
signal: it localises a capability gap for almost no money, and the failing
capability maps directly onto the defects that need it (`cross-format correlation`
alone gates 6 of the 11 real defects).
