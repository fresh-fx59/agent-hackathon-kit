# Selected harness qualification — development report

## Current state

Reviewed implementation is ready. Combined selected and legacy provider-free suite passes30tests in38.371s. Actual subscription qualification remains pending lifecycle integration.
No subscription or paid target experiment has started for this change.

## Timeline

- Initial five tests failed because selected preparation did not exist.
- First implementation exposed macOS /var aliasing in the fixture. Resolved test
  paths before validation; negative tests no longer passed on an unrelated path
  refusal.
- Next positive test exposed missing request timeout in actual target settings.
  Lifecycle owner added explicit600000 for monitored preparation; legacy finite
  settings remain unchanged. All seven focused tests passed in1.992s.
- Added launcher selected-input syntax and canonical package/tool-tree audit
  bindings. A broad provider-free harness suite is running. Runtime v45 unchanged.

- Provider-free combined helper/legacy harness suite passed24tests/34.964s.
  New actual selected preparation CLI caught a stale prompt/input-package hash;
  fixed publication order and binding. Ten focused tests then passed2.960s.
- Actual shell propagation test passed2.030s using deliberately stubbed preparation
  and controller in a throwaway repo. It verifies v45/mode/settings arguments and
  absence of finite aggregate environment caps, not genuine harness acceptance.
- Claude Sonnet5 medium reviewed the ready diff (119.856s; raw JSON retained).
  Accepted missing direct executed-settings cross-check at selected audit boundary:
  regression failed before the check. Existing run-manifest verification supplies
  additional protection; no end-to-end exploit was asserted. Reviewer timeout
  claim rejected: shared validator already requires request_read_timeout_s600;
  new regression passed before any fix and proves that constraint remains active.
- Added direct settings binding. Final combined suite is running. Live integrated
  subscription qualification remains dependent on lifecycle/runner completion.

- 2026-09-06 — Final combined selected/legacy suite passed30tests in38.371s. Owned Python compilation, shell syntax and diff checks passed. Raw test log retained at `/tmp/sherlock-harness-final-tests.txt`. No provider contact.
