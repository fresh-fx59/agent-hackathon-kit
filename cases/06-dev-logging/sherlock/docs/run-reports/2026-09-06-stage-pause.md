# Sherlock stage pause fixture experiments

Status: development evidence only. These four localhost loopback fixtures tested Stop and clear behavior. None used a metered or provider model call, and none is a Sherlock corpus acceptance run.

## Evidence integrity

Each remote fixture was mirrored locally with `home/updates` excluded. Local and remote inventories matched by relative path, type, byte count, and SHA-256:

- r1: 565 entries; inventory SHA-256 `47392ce4e2fc3d3aee5c1778fcd7553371f074aa9ee49d904ee9b54a16033acd`
- r2: 496 entries; inventory SHA-256 `b5850ef9076efb9f766f78d4fe49f5f8b53481a704312aa783e4cefec9bd4b2a`
- r3: 490 entries; inventory SHA-256 `6d67c9566d5ec0ef95e9330148bc2ce86ef9561776262561272c3d310e9e677f`
- r4: 1465 entries; inventory SHA-256 `c5828486eb207e07f78211f396eeda3f41f4a7c48c2cea53dcbca27fea3764f4`

The exact fixture sources are committed under `artifacts/2026-09-06-stage-pause/{r1,r2,r3,r4}/qwen_stage_pause_smoke.py`. The r1 source SHA-256 is `2e74b3bf0c38f930ff8c1857a86343eacd45b671e227898e87cad972f7f75663`; r2 is `52c58f1d57a720bc628966f0849fc2e016c735c55cbe78b07262fc7a3855671a`; r3 and r4 are `a903321cd1a9845257e5a9b8413e3e1c74b4513e771f605bdfe6a4cb2cc849ba`.

## r1 — v45 Stop blocks during provider activity

Result: failed with `AssertionError('stage pause was blocked')`. The fixture made ten loopback requests. The driver repeatedly reported missing hook pairs, an expected tool batch for `call_pause`, a busy client, or a pending provider request while waiting for `/clear`. The Stop path did not retire the active marker and allow the stage pause. The later operator `ALRM` was an external termination action after the already-recorded assertion; it cannot turn this into a pass.

This run establishes the old v45 behavior: Stop remained blocked while the mock provider continued responding. It does not establish a production provider failure.

## r2 — v46 candidate accepts Stop and clears a fresh session

Result: the fixture failed its old assertion with `AssertionError('unexpected continuation count: 3')` after three loopback responses. The evidence shows the important behavior passed: the durable marker was preserved, Stop returned `decision: allow` with reason `Sherlock stage handoff accepted`, and `/clear` produced fresh session `e28d35fe-dafe-493c-9fee-a950651a818c` from old session `ac43ed6c-aeee-420f-8059-93728efd824f`. The fixture waited for all three provider responses.

The third raw request contains Qwen auxiliary `[SUGGESTION MODE:]` content. The old fixture expected only two continuations, so its terminal assertion was too narrow. This is a fixture classification failure, not evidence that the Stop and fresh-session composition failed. The experiment remains rejected until the fixture verdict is updated and rerun.

## r3 — auxiliary classification and stable snapshot guard

Result: failed closed with `ClearProofError('cannot verify stage Stop outcome: FILE_CHANGED_WHILE_READ: /tmp/qwen-stage-pause-20260906-r3/workspace/work/checkpoint.json')` after two loopback requests. The revised fixture explicitly classifies post-Stop Qwen auxiliary requests whose user text begins `[SUGGESTION MODE:`; this addresses r2's third continuation. The r3 failure came earlier: the checkpoint changed during the read, so the driver refused to claim a Stop result. Root reproduced this as a legitimate atomic Stop receipt replacement and is adding a retry only for that exact transient. Stable malformed or unsafe state must still fail closed.

r3 does not prove a broken Stop outcome. It proves that the strict snapshot guard detected a concurrent file update and withheld acceptance.

## r4 — v46 accepts Stop, classifies Qwen auxiliary traffic, and clears fresh

Result: passed. The fixture recorded three loopback responses, preserved the durable marker, returned `decision: allow` with reason `Sherlock stage handoff accepted`, and cleared from old session `5602d387-df6c-408c-b080-25055401462b` to fresh session `d3ccd5bc-f12f-42e9-ba3f-e9cf2772b8d9`. It waited for all three provider responses and classified request index 2 as `Qwen suggestion`. No provider errors occurred.

This is a passing stage-pause fixture result for frozen Sherlock v46. It remains development evidence: it uses loopback transport and does not accept a Winevent corpus report.

## Decision and next step

r1 is the preserved v45 regression. r2 demonstrates the candidate composition but is a rejected fixture result because the expected continuation count ignored Qwen auxiliary traffic. r3 adds that classification and exposes a stable-read race, failing closed. r4 passes after the retry was limited to the exact `FILE_CHANGED_WHILE_READ` transient and after explicit auxiliary-request classification. The next gate is the fresh v46 qualification corpus run; its acceptance still requires the standard Sherlock report and gates.

## Evidence paths

- Local mirrors: `/Users/a/hack/qwen-stage-pause-20260906-r1`, `-r2`, `-r3`, `-r4`
- Remote originals: `/tmp/qwen-stage-pause-20260906-r1`, `-r2`, `-r3`, `-r4`
- Committed fixture sources and inventories: `artifacts/2026-09-06-stage-pause/`
- Results: each mirror's `result.json`
- Raw requests and responses: each mirror's `request-*.json` and `response-*.sse`
- Raw hooks: each mirror's `raw-hooks/`

