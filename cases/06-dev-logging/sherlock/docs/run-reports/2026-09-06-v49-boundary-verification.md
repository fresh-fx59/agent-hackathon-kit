# V49 stage-boundary verification

Status: in progress; no provider acceptance claim.

## Scope

Portable admission gate and truthful composed hook accounting; immutable v48 retained. New package must be registered before actual Qwen execution.

## Timeline

- 2026-09-06T15:30:41.344858+00:00 — Implementation worker finished. Root combined focused suite64 tests PASS3.771s (includes prior-version Stop regression). Final v49-specific Stop/finalizer26 tests running separately. Claude Sonnet low subscription review active, exact prompt and output retained under /tmp/sherlock-v49-claude-review until terminal copy. Actual Qwen fixture has not started; no DeepSeek contact.
- 2026-09-06T15:31:02.890878+00:00 — V49-specific Stop/finalizer suite26 PASS3.637s, exit0. Intentional negative validator outputs inside the tests are expected assertions, not run acceptance. Raw test output copied alongside focused suite.
- 2026-09-06T15:31:35.979960+00:00 — Claude review PASS with request to verify `_event_hash` accepts Pre events. Root source verification at stopcheck.py945–951 confirms canonical JSON hashing of arbitrary JSON events, no Stop-only fields. Fresh-session allow test also passes. No code change required. Actual primary model claude-sonnet-5 low; ancillary claude-haiku-4-5-20251001 appears in usage. Sonnet5555 output/4641 thinking,27050 cache creation/3289 cache read; subscription estimated list cost is not a paid-provider invoice. Review duration61.852s.
