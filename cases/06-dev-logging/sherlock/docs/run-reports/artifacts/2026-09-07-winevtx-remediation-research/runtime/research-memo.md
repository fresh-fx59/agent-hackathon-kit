# Winevtx r3 runtime/model failure research

## Evidence boundary

This is read-only analysis of immutable v52 r3, not an inference from r2. The
original r3-final package retained only report/catalogue/settings/receipts. One
stable remote capture recovered 204 persisted OpenAI records; its ordered-record
manifest SHA-256 is `af553c5b71ff5668453208fa14e53dfd924b62bed7aed6240496faf7bde93a6e`.
The local raw capture is review material and must not be committed wholesale.

## Observed chronology and failure chain

| UTC | Observed evidence | Effect |
|---|---|---|
| 05:05:58 | r3 is `deepseek-v4-flash`, thinking disabled, no fallback ([settings](../../2026-09-07-v52-winevtx-r3-final/settings.json), SHA `a95a80c61ea62fde395983c287e9cf7617d5520c505e468cf57cdf0882633514`). First `./corpus`/`./work` calls are rejected for relative paths. | Setup/context loss. |
| 05:06 | Broad count/list output has 292 lines; only lines 1–212 are read. | Partial inspection only; it does not prove later absence. |
| 05:09:08 | Custom Security script declares `targets` including 5379 but actual `want=[4648,4634,4616,4799,4798]`; 5379 details are skipped. [`…f1220c6c#/response/choices/0/message/tool_calls`](remote-r3-capture/openai-logs/openai-2026-09-07T05-09-08.861Z-f1220c6c.json), SHA `25d0c680a3032425291b8426f60ca6a44b1b945615a486709657ebf2e149936f`. | Model saw the 5379 count but did not inspect outcome fields: a trace-supported explanation for flattening reads into failure. |
| 05:09–05:24 | One-off Python dumps inspect LSM/RCM/RdpCoreTS/firewall, e.g. [`…23c3960a#/request/messages/56`](remote-r3-capture/openai-logs/openai-2026-09-07T05-09-23.580Z-23c3960a.json), SHA `891ba6c1e8d38241a09043a3ca2b0f814b57168d74a6c806b28c57484c048d3a`. | Data was available, but no normalized result, semantic time-delta, or scoped-negative analysis ran. |
| 05:29:10 | `work/report.md` is written before any final gate. [`…350832bd#/response/choices/0/message/tool_calls`](remote-r3-capture/openai-logs/openai-2026-09-07T05-29-10.349Z-350832bd.json), SHA `1aa386267d0f6684df687dae2f555d890b1d835d7638bce42a42587709c429e8`. | Unsupported text reaches deliverable. |
| 05:29–05:33 | Model searches `/tools/stopcheck.py`, then tries a `/tools` symlink; policy blocks it. [`…9304d02e#/request/messages/39`](remote-r3-capture/openai-logs/openai-2026-09-07T05-32-01.359Z-9304d02e.json), SHA `dc9dcfd066e0c0f1d5564c6b1b7897438543bcda8984109869952d73d890b8a2`. | No `stopcheck`; no persisted execution of `brief`, `logmap`, `logjoin`, `worklist`, `cite`, `triagecheck`, `citecheck`, `reportcheck`, or `statecheck`. |

## Correctness facts and classification

Independent source audit: [source-audit.md](../../2026-09-07-v52-winevtx-r3-independent-source-audit/source-audit.md).

| Error in report | Verified fact | Required mechanism |
|---|---|---|
| 1,075 Credential Manager reads all failed | 109 Event 5379 have `ReturnCode=0` and `CountOfCredentialsReturned=1`; 966 fail. A successful read does not prove theft, actor, content, or follow-on use. | Result-field normalizer and success/failure/unknown partitions. |
| May root/service as attacker attribution | `root` and SID `…-1001`, and 7045 same SID, are observed. Human identity, installation execution, and link to June are unknown. | Entity-link ledger; prohibit attribution from SID/proximity. |
| 3proxy 40 minutes after disconnect | Source shows **13m06s after reconnect**, not disconnect/40 minutes. | Semantic event decoder plus signed anchor/delta with endpoint citations. |
| Blanket PowerShell/firewall negatives | Narrow negatives are supportable, but firewall allow and PowerShell-host counterexamples exist. | Negative-scope record: channel/provider/EID/time predicate and counterexample count. |
| Correlation marked `[PROVEN]` | RDP brute force is a supported hypothesis; per-event mapping, port/NLA state, and causation are unknown. | Strong-claim ledger: direct predicate required for `[PROVEN]`; correlations remain inference with missing edge. |

## Existing v52 baseline and planned-change boundary

`logmap.py` already provides `status_axis` and `outcome_rows`
([lines 1277 and 1317](../../../../../skills/v52/tools/logmap.py)), with time/host
partitioning; `logjoin.py` provides time-window joining. These primitives are
**available but unused** in r3. Do not replace them with a broad new map.

Add the small mandatory semantic-claim layer between discovery and prose:

1. normalize outcomes; emit temporal anchors/deltas; emit scoped-negative records;
2. retain a compact ledger only for `[PROVEN]`, actor/service attribution, causal
   language, blanket negatives, and numeric deltas;
3. have final validation consume ledger plus report, failing closed on missing
   artifacts or failed exits.

Prompt-only wording cannot establish that 5379 result fields, counterexamples, or
endpoint deltas were checked. The r3 hook failure is runner integration: it sought
external `/tools`, although the v52 tool is project-local. Commit
`73c1c8f36a17c4ebaa55791b02ef86b723892112` post-dates r3 and updates
`eval/bench/run-clean-direct-qwen.sh` to export hook skill root; use that
runner/hook boundary to resolve project-local tools, record gate receipts, and
fail runs whose `triagecheck`, `citecheck`, `statecheck`, or stop receipt is absent.

## Acceptance checks

1. 1,075 5379 fixture (109 success/966 failure) rejects “all failed”.
2. Reconnect/disconnect fixture rejects swapped semantics and `40m`; accepts cited `13m06s` only.
3. Firewall/PowerShell counterexample fixture rejects blanket negative, accepts named scope.
4. Same-SID May service fixture rejects actor/June attribution without direct link.
5. `/tools`-absent runner resolves local v52 tools, writes all receipts, and fails closed if a required gate is missing.

## Unknowns

The trace establishes skipped inspections/gates, not why each generated sentence
was selected or whether an unpersisted provider-side action occurred. No persisted
r3 gate receipt exists; that is the bounded evidence gap.

## Root review correction

- 2026-09-07 — Narrowed the claimed direct cause to observed skipped inspection; trace evidence cannot establish the model’s internal reason for a sentence. Corrected the relative tool-source links. Full raw-file hashes and selected exact JSON-pointer extracts are saved separately for review. Corpus-specific numerical fixtures above belong to evaluator-only regression data; portable synthetic tests must vary them.
