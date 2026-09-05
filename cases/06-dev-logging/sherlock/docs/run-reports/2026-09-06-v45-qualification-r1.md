# V45 qualification r1

## Objective

Qualify frozen v45 through the selected subscription harness, then DeepSeek v4 Flash in Qwen Code. Full paid runs remain explicitly authorized. No corpus report acceptance is implied by preparation.

## Identity

- Code: ccebb05, fast-forwarded onto contabo-prod test checkout. No services changed.
- Package: v45, SHA256 `36a0dfe2c674ac11a13d1dc1b6a0b8c3e720fdb08577c8c9ec5d386cd317e40e`.
- Target input: `/home/claude-developer/hack/sherlock-v45-qualification-20260906-r1`.
- Subscription output: `/home/claude-developer/hack/sherlock-v45-harness-20260906-r1`.
- Raw source preparation: final Winevtx JSONL-only corpus; generic qualification fixture created by the preparation helper.

## Current state

Target preparation passed. Subscription r1 failed before wrapper execution; r2 failed before Qwen because of an omitted launcher timeout0, now repaired. Subscription r3 made one valid gpt-5.5 investigation request but guardian tool-completion timing caused a false failure. Guardian timing and exact provider/hook ID correlation repairs are now verified, including actual Qwen0.22 positive and missing-hook scenarios. No DeepSeek target call or accepted corpus report yet.

## Timeline

- 2026-09-05T22:17:52.761532+00:00 — Reviewed integration ccebb05 synced by clean fast-forward. Started target-contract-probe prepare with schema2 monitored mode, v45, actual remote Qwen0.22 entrypoint, neuraldeep alias identity, current retained rate card. Awaiting precontact result.

- 2026-09-05T22:19:10.721740+00:00 — Target preparation exit0, fresh manifest e2be5328621338acb8883835f92e344ad7985efd896cda8b747e2f39c063b774. Subscription launch r1 failed before secret wrapper execution: supplied PATH omitted Nix /run/current-system/sw/bin, so env could not locate bash. Original stderr retained; output directory was not created. Corrected launcher PATH and started fresh subscription r2, retaining separate stdout/stderr. Current controller phase TESTING after matrix; no model request observed.

- 2026-09-05T22:20:49.725444+00:00 — Subscription r2 passed preflight/matrix and reached signed launch at22:19:17UTC. Root inspected exact launch/profile/package/observer identity and published genuine observation sequence0 at22:19:30.560586UTC. Runner then refused `monitored Qwen limits do not match the sealed unlimited policy`; controller recorded RUNNER_FAILED/RUNNER_HANDSHAKE_FAILED and a durable fault. Source diagnosis: selected launcher removes legacy SHERLOCK_TIMEOUT but omits explicit0, so runner defaults5400 and refuses. Assigned one-field launcher fix plus executable shell-propagation regression. No Qwen investigation request; subscription health contact occurred.

- 2026-09-05T22:23:36.137588+00:00 — Root independently verified launcher regression PASS1test/2.061s with explicit timeout0. Retained complete r2 trace140regular files locally and compared exact remote/local file set and all SHA256 values: PASS. Signed receipt statusFAULT/reasonRUNNER_HANDSHAKE_FAILED, expected/completed tools0, guardian exit-15. No upstream investigation journal exists. Ready for corrected fresh subscription retry.

- 2026-09-05T22:25:02.660954+00:00 — Timeout0 repair committed c80cb9f and fast-forwarded test host. Worker full harness18tests/33.974s PASS. Started subscription r3 with fresh output root and existing unconsumed, still-valid prepared v45 target inputs; separate launch stdout/stderr retained. No implementation changes during this run.

- 2026-09-05T22:27:48.766133+00:00 — Subscription r3 reached QWEN_RUNNING after actual root sequence0 at22:25:36.950526UTC. First actual response: gpt-5.5 requested/sent/returned, HTTP200, complete SSE35events/10291bytes, one `skill` toolcall, usage33810prompt/159completion, duration4940ms. Proxy recorded completion22:25:51.867; guardian faulted EXPECTED_TOOL_HOOK_MISSING at22:25:51.945914 (about79ms later), before any hook-start/event. Qwen err.txt shows FatalCancellationError130 and out.json is empty. Signed terminalFAULT expected1/completed0, guardianexit2. Hypothesis: guardian wrongly requires newly expected tools complete before client can begin execution; assigned exact ordering regression. Separate installed-Qwen skill-hook source check underway. Refreshed official Qwen hooks documentation https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/ (retrieved2026-09-06 local); installed0.22 source remains decisive for this client. No DeepSeek call, no corpus investigation acceptance.

- 2026-09-05T22:29:22.190104+00:00 — Installed remote Qwen0.22 source independently confirms skill is a normal BaseDeclarativeTool and sharedscheduler awaits globalPreToolUse before and PostToolUse after execution (chunk-T6XLJRQY.js63990–64053,64455–64480; skill-DKEMQSNJ.js90–122,260–370). Helper run_guardian calls check_dispatch every250ms, and that predicate rejects both newly expected missing hooks and legitimate pendingPre/Post pairs. Scoped repair: guardian continuously checks observation/fault/state integrity but validates pair completion at nextproviderdispatch or terminal audit. No grace/call/time cap; preserve permanentfault and missinghooks rejection at the correct boundary. Add delayedclient/inprogresstool regressions and actual installedQwen mockskill+shell test beforefreshqualification.

- 2026-09-05T22:31:33.760708+00:00 — Complete r3 trace transfer verified267regular files, exact fullsets+allSHA256; artifact index retained. Run-review index unavailable because Qwen out.json is empty after cancellation; used explicit raw response/journal/guardian/Qwen-error fallback. New installedclient regression draft review caught fakeboot identity and guardian_started-only assertion plus lost exception captures; returned precise test-fixture corrections before execution.

- 2026-09-05T22:35:11.654792+00:00 — Guardian timing repair frozen at helperSHA256d364fa8cecaae814df2c71ef7c823cd3fbedc82a1b9663af7aa03f7e32aff330. Root independently passed19helper tests/0.329s and versiongatev45. Workerproxy51tests/33.099s PASS; delayedhooks+observationregression2tests/.135s PASS. Narrow Claude Sonnetmedium review started; actual installedQwenmockskill+longshell regression starts against this exact frozen helper. New target qualificationr2 and subscriptionr4 must bind newhelper; old inputs cannot authorize it.

- 2026-09-05T22:36:45.675441+00:00 — Narrow Claude Sonnetmedium guardian repair review PASS62.789s (list-price estimateUSD0.1058066, notinvoice). Confirmed continuousobservationregression preserved, nextdispatch/terminalcompletion unchanged, no timinggrace. Nondefectlimit: guardian does not infer a stucktool from an incompletepair alone; actual root observes progress and can stop demonstrated loops/stalls, while missinghooks block nextrequest/terminal. This matches approved noarbitraryaggregatecaps. No userreconfirmation needed. Raw review/input retained.

- 2026-09-05T22:44:48.188977+00:00 — Root independently decoded only exact hook metadata from preserved actual-Qwen smoke r2. PreToolUse and PostToolUse share session9e3c90c8-1a5e-4ce3-914f-b80f6b92ef64, internal tool_use_id toolu_1788647733813_2r4yf9sit, provider tool_call_id call_skill, canonical tool skill and input {"skill":"mockskill"}. Fault expects call_skill. This proves an explicit authoritative mapping exists; no heuristic tool-name correlation is needed. Vault state committed e32045d after lint passed with366 existing warnings.

- 2026-09-05T22:54:01.413017+00:00 — Root independent ID-bridge verification PASS: helper24tests/0.352s and verdict43tests/7.213s. Frozen helper7da43129492379c5b46ebfdb6e9d8ccefb304187d91b1c1c59dab7191786fb0f, verdict1ef6a04218344a02d350a2261d52efe2365abf0dbaf602d687736777026376df. Worker also passed monitored-runner9tests/5.393s and proxy51tests/33.738s. InstalledQwen smoke and narrow Claude review now run independently against this snapshot. Full-launch handoff paths verified on remote; provider/model fields are derived from consumed admission profile before health contact.

- 2026-09-05T22:57:31.494804+00:00 — Root independently inspected remote installed-Qwen r3 result files: skill and slow-shell each exit0,2requests,2successful responses, exact Pre/Post provider/internal ID mapping, no fault, guardian alive before cleanup; slow-shell marker=slow. Helper SHA7da43129492379c5b46ebfdb6e9d8ccefb304187d91b1c1c59dab7191786fb0f. Missing-hook case correctly faults EXPECTED_TOOL_HOOK_MISSING with no response-1, but smoke cleanup stalls because a child Node holds pipes after Qwen leader exit; worker is correcting owned process-group cleanup and preserving failed experiment. No production-helper defect inferred from this test harness cleanup failure.

- 2026-09-05T23:01:06.340852+00:00 — Root verified final actualQwen r4 summary: helperf38353b4e788dab6a210b66dcd463cec2a6f67be41e48d3998093d9f8b3f4197, skill+slow-shell exact Pre/Post correlation and guardianalive; missing-hook exactly2requests/1successfulresponse, nextresponseabsent, ownedgroup exit-15 with no retryloop. Final helper25tests/0.371s rootPASS. Claude bridge reviewPASS with delimiter inputcheck accepted and tested; its hypothetical byte-identical different-ID collision was disproved. Ready to commit/sync repaired harness and prepare freshboundqualification.
