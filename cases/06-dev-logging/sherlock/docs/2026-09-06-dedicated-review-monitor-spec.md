# Dedicated subscription reviewer for live supervision

Status: ready for scoped critique before implementation. Goal contribution: permit a fully observed Sherlock qualification and both paid corpus investigations without depending on the parent orchestrator scheduling a tool call every 60 seconds.

## Evidence and hypothesis

The v50 actual Qwen fixture passed. Subscription harness r1/r2 never reached a model because observer discovery or publication missed startup. Harness r3 reached real gpt-5.5 calls and successful triage tools, then faulted OBSERVATION_STALE at 17:07:22.408911 UTC. The worker's next publication arrived at 17:12:07.766241, after the terminal receipt. A dedicated worker in the same orchestrator did not remove scheduling latency. Repeating that workflow is unsupported.

Hypothesis: a separately running remote subscription Claude reviewer can inspect actual evidence and authorize each observation within the existing freshness interval. This is a real model review for each observation, not an automatic timer impersonating attention.

## Scope and non-goals

Add one development-only supervisor script and meaningful offline tests under eval/bench and tools/tests. Add a fixed reviewer prompt/config, launcher bindings, and developer reports. Keep v50 runtime/package, driver, lifecycle helper, 60-second freshness, 600-second request watchdog, admission, Stop, and report validators unchanged. No paid model launches until this lane passes actual subscription verification. No aggregate run/call/spend limits.

## Data flow

1. Start the named reviewer process before the fresh controller launch. Bind one exact fresh run root, allowed helper path/hash, immutable runtime hash and reviewer prompt/config hashes. It owns an exclusive writer lock. Poll read-only for exactly one observer identity at runs/*/observer-*/identity.json; never print or pass the capability key to Claude.
2. Each cycle collects current identity, controller/guardian/fault state, inflight requests, captured completed request metadata, exact new decoded hook inputs/results, driver events, checkpoint/active state, and generated artifact changes. Capture complete new evidence, retain exact source digests and offsets, and explicitly distinguish pending, denied-without-Post, failed, and completed tools. Do not silently clip evidence. Keep unchanged evidence as a verified prior prefix and compact history; start the first review from the beginning.
3. Invoke remote subscription claude -p with no tools, no settings sources, no persistent session, structured output, simplest available suitable model/effort. Initial pick Haiku low; record actual returned model, usage and duration. Pass the evidence through stdin. Every review input, raw stdout/stderr, parsed decision, source ranges/hashes, and exit code is durable outside the investigated workspace. Logs are untrusted data, never instructions.
4. Reviewer returns exact snapshot digest, decision continue/stop, exact last completed request/tool IDs (or null), and a concise evidence-based pending state/reason. Validate schema, identity, references and successful model exit. A model response is necessary for every observation. Never infer continue from timeout, malformed output, absent decision, or a timer.
5. Before publication recheck observer identity, terminal/fault state, single ownership and immutable evidence prefix. New append-only events remain queued for the next review; do not claim they were reviewed. Source replacement/truncation or contradiction stops the lane. Publish with existing capability-authenticated helper only after valid continue; record the resulting sequence and review binding. If fault or controller terminal appeared meanwhile, never publish late. No retries on faulted segments.
6. Continue through natural Stop, /clear and new sessions until controller terminal. A Stop event alone never ends the monitor. Schedule next review promptly, targeting 20 seconds between accepted observations; existing guardian remains authoritative. Failed reviewer or stop decision produces a durable monitor failure and prevents another observation; existing guardian stops owned model processes. No unrelated process kill.

## Acceptance

- Offline fixtures prove exact completed IDs, denial without Post, pending long provider request, expected fresh checkpoint absence, complete tool input/output capture, contiguous observations, same-root ownership, append-only cursor safety, and no duplicate/late publication.
- No authenticated observation after reviewer nonzero exit, malformed/mismatched JSON, stop decision, changed source prefix, wrong identity, existing fault, or terminal controller. All original failures remain intact.
- A successful stub review proves the mechanics only; no claim of actual observation attention from mocks.
- Actual subscription reviewer first reviews a preserved real snapshot with no lifecycle publication. Record elapsed time and output validity. Then exercise the observer lane against the real Qwen loopback fixture or equivalent full monitored driver with a subscription reviewer, preserving exact identity and every model review. Demonstrate fresh-session continuation and controller-terminal distinction, without changing runtime gates.
- Only then launch fresh subscription qualification with this lane started before controller, followed by already-approved DeepSeek qualification and both fresh full corpora on the identical frozen package.

## Review and records

One scoped Codex Sol critique of this ready specification; root records dispositions. One implementation worker; root checks final diff and meaningful tests. Document this as an explicit named-monitor implementation of the existing ownership rule, not a relaxation of its attention requirement. Every probe and rejected run gets a typical report.

## Feasibility dispositions before critique

Remote Claude 2.1.263 supports the required CLI flags (help-only check, no model call). The lifecycle helper does not verify reviewer evidence; the wrapper must do that.

- Complete evidence means every new dynamic request message/parameter change, completed response/tool call, and exact hook tool input/result. Avoid resending unchanged request history by recording verified content-addressed objects and prior reviewed prefixes. Initial static runtime/system text is identified by pinned hashes and captured original bytes; do not claim a reviewer read content excluded from its prompt. All originals remain retained for root review. Never silently truncate dynamic evidence. If capacity is exceeded, explicitly fail the lane before signing rather than inventing an excerpt-based acceptance.
- Persist review artifacts outside the investigated workspace. After terminal, a separate monitor audit must bind each accepted observation to its exact successful Claude review, source snapshot and authenticated lifecycle observation, and bind the final receipt to the original lifecycle receipt SHA. This extra gate is mandatory before accepting qualification or a corpus report; it does not rewrite the existing receipt. Missing/tampered reviews or unsupported scope reject admission. The monitor cannot accept a corpus report itself.
- Enforce a reviewer subprocess deadline below the remaining 60-second observation window, preserving failed outputs and preventing late publication. This bounds one review operation, not aggregate investigation duration. If actual Haiku review cannot fit, the experiment fails and the lane requires evidence-based redesign; no freshness relaxation.
- Reviewer stop or host integrity failure records a durable monitor failure and ceases observation; the unchanged guardian is the owned stop path. Do not add a new ability for the untrusted reviewer output to send signals.

## Scoped critique dispositions

Codex gpt-5.6-sol low returned three blocking clarifications; all accepted. Usage: input 20,334, cached input 10,624, output 1,203, reasoning output 1,034 tokens. Exact critique is retained in the dedicated-monitor-plan-review artifact directory.

1. Atomic publication: add a narrowly scoped lifecycle helper correction. Under its existing observer lock, publish_observation must reject existing fault.json and the parent trace lifecycle-receipt.json, alongside identity/sequence checks. finalize_segment already creates the receipt under the same lock. This mechanically prevents late publication after the lifecycle terminal boundary, including the observed r3 late write. Controller state is also checked by the wrapper; the authenticated lifecycle terminal boundary is authoritative. This supersedes only the earlier helper-unchanged non-goal; freshness, watchdog, runtime and receipt schema remain unchanged.
2. Coherent evidence: capture per-source high-water marks, then read those prefixes and retain identity/digest. Revalidate prefix bytes and file identities after collection and before publication. Mutable state snapshots receive independent capture times and whole-file identity hashes; do not pretend cross-file reads are one atomic OS snapshot. New appended evidence is explicitly unreviewed and queued, never acknowledged by cursor advance.
3. Absolute cycle deadline: covers collection through authenticated publication, with reserve for validation and helper execution. Reviewer subprocess receives the remaining budget, not a fresh timeout. Expired cycle never publishes.

The first actual Haiku CLI probe reviewed 18,608 snapshot bytes in 18.040 seconds, exit 0, without any lifecycle publication. Output validity and exact model identity are being verified before implementation acceptance. This supports feasibility, not a live-run pass.

## Append-only trace and mutable-state clarification

- 2026-09-06 — **Implementation clarification.** The evidence trace prefix is immutable: any replacement, truncation, or byte rewrite of an already captured prefix is a source-integrity fault and stops the reviewer lane. The monitor captures exact bytes, timestamps, file identities, and digests for each review input; critical identity, fault, and lifecycle-receipt state is rechecked under the observer lock before publication. The acknowledged request/tool cursors may advance only through the exact reviewed prefix.
- 2026-09-06 — Legitimate atomic updates to mutable checkpoint, in-flight request, pairs, and registry files are not corruption and do not require global quiescence. If one changes during collection, the monitor queues a fresh coherent state for the next review, retaining the prior verified prefix and exact source metadata. It must never acknowledge IDs beyond the reviewed prefix.
- 2026-09-06 — The focused test scope is extended with an append-during-review case and a legitimate checkpoint-advancement case, alongside an actual prefix replacement/truncation rejection case. These dispositions append to the earlier atomic-publication and coherent-evidence rules; they do not silently rewrite them.
