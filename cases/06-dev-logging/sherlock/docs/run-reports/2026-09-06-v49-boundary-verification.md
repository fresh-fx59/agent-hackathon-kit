# V49 stage-boundary verification

Status: in progress; no provider acceptance claim.

## Scope

Portable admission gate and truthful composed hook accounting; immutable v48 retained. New package must be registered before actual Qwen execution.

## Timeline

- 2026-09-06T15:30:41.344858+00:00 — Implementation worker finished. Root combined focused suite64 tests PASS3.771s (includes prior-version Stop regression). Final v49-specific Stop/finalizer26 tests running separately. Claude Sonnet low subscription review active, exact prompt and output retained under /tmp/sherlock-v49-claude-review until terminal copy. Actual Qwen fixture has not started; no DeepSeek contact.
- 2026-09-06T15:31:02.890878+00:00 — V49-specific Stop/finalizer suite26 PASS3.637s, exit0. Intentional negative validator outputs inside the tests are expected assertions, not run acceptance. Raw test output copied alongside focused suite.
- 2026-09-06T15:31:35.979960+00:00 — Claude review PASS with request to verify `_event_hash` accepts Pre events. Root source verification at stopcheck.py945–951 confirms canonical JSON hashing of arbitrary JSON events, no Stop-only fields. Fresh-session allow test also passes. No code change required. Actual primary model claude-sonnet-5 low; ancillary claude-haiku-4-5-20251001 appears in usage. Sonnet5555 output/4641 thinking,27050 cache creation/3289 cache read; subscription estimated list cost is not a paid-provider invoice. Review duration61.852s.
- 2026-09-06T15:33:00.928233+00:00 — Frozen v49 d9eb69b2f45a549778ef60cc2ee5c83817394468f52d076504b6ee95285d0c9e registered/committed07b0382 and fast-forwarded onto Contabo test checkout. Initial bundle command with two detached revisions produced empty-bundle refusal; corrected to named HEAD range, transfer verified exit0. Installed-Qwen localhost boundary fixture r1 launched; no external provider/secret used.
- 2026-09-06T15:34:46.458026+00:00 — Installed Qwen0.22 fixture r1 PASS:7 localhost requests/6 normal, no provider errors, denied sentinel unchanged, exact denial captured without fake post, fresh-stage worklist edit allowed, Stop/clear/fresh combined reseed proven. Driver143 is intentional owned fixture termination after second consumed pause, not a production interruption. Report worker preserving full evidence. Fresh v49 target calibration preparation launched; still no DeepSeek contact.
- 2026-09-06T15:38:18.394621+00:00 — Independent fresh calibration prepare PASS, manifest10afc05357d003cf880dd72ece517c0a83b48c9f7a8fd53494ca8609c8718cfe. Its future full input is independent469-event corpus, while calibration uses Security fixture from Winevtx. No target call or prior finding/state reuse. Fresh v49 scripts all individually pass bash -n.
