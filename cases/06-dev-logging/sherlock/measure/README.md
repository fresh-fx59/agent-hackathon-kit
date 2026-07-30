# measure/ — the Sherlock measurement rig

Answers, for every missed defect: **was the evidence never opened (coverage), or
opened and not connected (reasoning)?** Deterministically, with no LLM call.

    # build per-defect slices (key and corpus live OUTSIDE this repo)
    SHERLOCK_ANSWER_KEY=~/hack/case06-measure/hetero-answer-key/answer-key.json \
    SHERLOCK_CORPUS=~/hack/case06-measure/hetero-corpus \
    python3 slice.py --out cases

    # tier 1 — iterate on one defect
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ./gate.sh 1 v6 D11
    # tier 2 — MANDATORY before accepting anything
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ./gate.sh 2 v6

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
