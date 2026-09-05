# Installed Qwen lifecycle loopback — remote r1

Status: **PASS**. This was a local loopback SSE simulation on `contabo-prod` as `claude-developer`; it made no provider contact and loaded no Sherlock skill.

The exact remote command was:

```sh
/run/current-system/sw/bin/python3 /tmp/sherlock-qwen-lifecycle-loopback-20260906-remote-r1/tools/tests/qwen_lifecycle_loopback_smoke.py --qwen /home/claude-developer/.local/bin/qwen --output /tmp/sherlock-qwen-lifecycle-loopback-20260906-remote-r1/evidence
```

Qwen resolved to `/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js`, version `0.22.0`, SHA-256 `68cb29eb7ccc936d78ece5564ef55cae41a55b630e6657dc417c1f2e561cf4c9`. The copied helper SHA-256 is `b11e5d8a9499a6185acd540319acb6b2c0f1cc084925af82683237213bd06cd9` and the smoke script SHA-256 is `9313f3a1984771b17ee0fcbfdf1a120cd3b679f11bbc030bee6258a5e96e44b5`.

| Scenario | Exit | Mock requests | Marker | Durable fault | Real Qwen denial |
| --- | ---: | ---: | --- | --- | --- |
| normal | 0 | 2 | True | none | None |
| missing | 0 | 2 | False | OBSERVATION_MISSING | True |
| stale | 0 | 2 | False | OBSERVATION_STALE | True |
| delete | 0 | 2 | False | REGISTERED_EVIDENCE_MISSING | None |

The guardian returned `2` for stale observation and its child exited `-15` before cleanup. Each scenario retains immutable launch/settings/stdout/stderr and both raw request and SSE response captures.

The local mirror is `/Users/a/hack/sherlock-qwen-lifecycle-loopback-20260906-remote-r1`. Its [compact JSON record](2026-09-06-qwen-lifecycle-loopback-remote-r1.json) inventories all **135** remote regular files by SHA-256. `transfer-verification.json` is local transfer provenance, excluded from the remote inventory; root independently compared the remote and local relative sets and hashes.

Earlier local attempts remain evidence of their respective defects: r1 failed because hooks alone did not enforce freshness; r3 lacked retained installed-client denial output; r4 used a fabricated guardian boot id; r5 was incorrectly described as Qwen 0.22 but was local 0.21.1; r7 is supplementary local 0.21.1 coverage. Remote r1 is the actual installed-Qwen 0.22.0 result.
