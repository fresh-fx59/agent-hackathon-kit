DECISION NEEDED — recommend Option B plus strong-claim ledger; need your go-ahead.

# Sherlock winevtx — design memo (r3 failure analysis)

## Evidence (as reported by the audit; I did not read the corpus or any raw event)

Everything below is conditioned on the prior source audit. I reproduced nothing independently.

The audit's PASS rows and FAIL rows separate cleanly by *kind of work*, and that split is the whole finding:

- **PASS**: file/event inventory, 33 456 failures, 17 798/15 658 day split, 1 976 names, 93 IPs, top-8 IP counts, RCM-O/Core windows, narrow ID negatives (no 4688/4103/4104/4720/Sysmon). Large mechanical aggregation was reliable.
- **FAIL**: container shape (`Event.UserData.EventXML` on LSM:40, RCM-O:1 vs `Event.EventData` on FW/Core); `EventID.#text` object form; universal claims contradicted by one partition (1075 credential reads "all failed" vs 966 `ReturnCode 3221226021`/count 0 **and 109 `ReturnCode 0`/count 1**); other allow rules (74 events `Direction 1`/`Action 3`, incl. mDNS 5353); PowerShell channel path wrong and `HostApplication` present on 25 EID 400; event semantics inverted (EID 25 = reconnect read as disconnect, EID 23 = logoff read as logon); arithmetic (13 min 06.147843 s narrated as 40 min; 14:53:59.463043–14:53:59.471379 narrated as 14:54:07); channel end read as corpus end (Core:1432 runs to 21:11:34.029721 past Security:19933); 1964 vs 1957 read as per-session 1:1 (delta 7; only 118/141 within 2 s); `root`/SID 1001 called an assumption when Security:13511 carries the field, while service `ImagePath`/`LocalSystem` was read as proven installer identity; and "no 4624 ⇒ credentials safe".

**Competing causes, ranked by what the evidence discriminates.** Model capability alone is a poor explanation: the same model got 90 267-event aggregates right. Scope/schema blindness explains the container and channel-path misses. Missing *outcome partitioning* explains every false negative on "all/none" claims — the model saw the modal bucket and generalised. Absent time arithmetic explains the delta errors. Absent event-semantics reference explains 25/23/40 and TermService 1056. Absent inference-boundary vocabulary explains both directions of attribution error. Citation formatting (3 unresolved refs) is real but secondary — the audit says so explicitly.

## Assumptions

Corporate runtime is deepseek-v4-flash, single pass, no LLM judge, no network. Qwen Code can run local deterministic tools over JSONL. Corpora vary; nothing corpus-specific may be baked in. Human review exists but is scarce.

## Options

**A. Prompt-only rules.** Zero infra. But the failures are precisely the class a fast model cannot self-check: it cannot know 109 of 1075 rows differ without scanning them. Prompt text like "verify all negations" produces the *claim* of verification, not verification. Predicted residual: schema variance, arithmetic, universal quantifiers all recur. Not sufficient.

**B. Targeted deterministic tools + semantic review.** Five small, generic primitives; the model must cite tool output IDs for strong claims. Semantic correctness stays human. Moderate build cost, no runtime LLM judge.

**C. Full claim ledger.** Every sentence becomes a typed record with evidence, scope, verifier. Maximises traceability but is mostly paperwork: it cannot detect that "EID 25 = disconnect" is wrong, and a well-formed ledger can faithfully document a false narrative. High token cost on a flash model raises the risk of degraded analysis.

## Recommendation: B, plus a ledger restricted to strong claims only

Ledger scope = universal ("all", "every", "only"), existential-negative ("no X"), and numeric/temporal claims. Ordinary narrative stays prose. This buys C's enforcement where it catches real failures and skips it where it produced no value.

### Requirements

1. **Normalizer (`evt_extract`).** One record shape from all containers: `EventData`, `UserData.EventXML`, numeric and `#text` EventID, plus `System.Security.UserID`, `CallerProcessName`, `ImagePath`, `ReturnCode`, `CountOfCredentialsReturned`. Every emitted field carries `file:line`. Unknown containers are surfaced as `unmapped_container` with counts — never silently dropped.
2. **Partition (`evt_partition filter by=field`).** Returns *all* buckets with counts, first/last timestamp, one example ref each, and a partition ID. Any "all/most/only" sentence must quote a partition ID; the report must print every bucket, not just the modal one.
3. **Exhaustive negation (`evt_negate predicate`).** Runs corpus-wide, provider/channel aware. Returns either zero hits with the literal predicate and scope, or counterexample refs. Output text is copied verbatim, so scope travels with the claim.
4. **Time (`evt_delta a b`).** Narrative durations must be the tool's string. No hand arithmetic.
5. **Coverage (`evt_coverage`).** Per-channel count and first/last timestamp. "End of data" claims are automatically channel-scoped and the limitations section is seeded from this.
6. **Offline semantics dictionary.** Provider+EventID → official label with a spec citation, generic across corpora. Unlisted IDs print raw ("LSM EID 40") rather than an invented meaning. This is a reference file, not answer injection.
7. **Claim vocabulary, four tiers.** `observed-field` / `computed-aggregate` / `inference` / `unknown`. Account context (SID, user name) is `observed-field`; person, intent, execution, and downstream effect are `inference` or `unknown` unless a field carries them. Aggregate co-occurrence may support a hypothesis but may not be stated as per-event mapping.

### Acceptance tests (falsifiable, run on synthetic mutations, not the audited corpus)

- **Minority-outcome mutation:** 1 000 rows, 999 failing, 1 succeeding → report must not say "all failed" and must show both buckets. Sweep the minority to 0.1 %.
- **Container mutation:** move a username into `UserData.EventXML.User` only → field must appear. Same for `EventID.#text`.
- **Channel-tail mutation:** one channel extends 30 s past the largest Security timestamp → any "end of data" sentence must name the channel.
- **Delta mutation:** two events 13 min 06 s apart → narrated duration matches `evt_delta` exactly; a ±20 % drift injected into the model's prose must be caught.
- **Negation replay:** harness re-runs every "no X" predicate; any hit is a FAIL.
- **Reference resolution:** every `file:line` resolves and quoted bytes match; unresolved short refs fail the build.
- **Near-1:1 adversarial corpus:** two channels with counts differing by ~0.4 % → report must not assert per-event causation.
- **Semantic decoy:** an ID absent from the dictionary must be printed raw, never labelled.
- **Overclaim flag (advisory, not a hard gate):** "proves/safe/benign/never" outside quoted evidence raises a review flag. Advisory on purpose — a hard block would push the model into euphemism.

### Limitations

None of this makes the model's semantics correct; the dictionary's coverage is the ceiling, and human review remains the only check on reasoning. The model can still narrate around tool output — the ledger verifier mitigates but does not eliminate this. Fields absent from a corpus stay invisible. Audit completeness versus real-world events is unprovable from logs. Passing these tests means the r3 failure *classes* are blocked, not that a given report is right.

From you:
1. Reply with A, B, or C to fix the option before I write the change spec.
