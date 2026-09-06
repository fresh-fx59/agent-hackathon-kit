# V49 boundary composition fixture r1

Status: PASS — provider-free loopback fixture only; no external model or corpus run.

## Identity

Code commit `07b0382`; runtime package v49 `d9eb69b2f45a549778ef60cc2ee5c83817394468f52d076504b6ee95285d0c9e`; lifecycle/helper `0d6aacbb7ed0d5e2f665113154a79122b5e9f9072cc0ec721bc436c1da44c0ee`.

## Result

The fixture recorded `passed: true` across 7 loopback requests: 6 normal requests at indices 0, 1, 2, 4, 5, and 6, with the intentional fixture stop at driver exit 143. It verified two Stop/clear cycles, exact fresh-stage reseed and clear proof, unchanged sentinel, allowed fresh-stage worklist edit, and boundary denial with no fake post-tool event. The denial returned `permissionDecision: deny` and the reason to end the turn and wait for Stop plus a fresh `/clear` session.

## Evidence

Remote run root: `/home/claude-developer/hack/sherlock-v49-boundary-fixture-20260906-r1`.

Local mirror: `/Users/a/hack/sherlock-v49-boundary-fixture-20260906-r1`; retained inventory has 174 files and matches the remote inventory exactly. The mirror excludes only directories named `home` or `updates`, as recorded by the inventory artifacts; the raw remote tree remains available at the source path.

Artifacts under `artifacts/2026-09-06-v49-boundary-fixture-r1/` include the exact result, local/remote inventories, and `/tmp` launch stdout/stderr. This was provider-free and made no DeepSeek request.
