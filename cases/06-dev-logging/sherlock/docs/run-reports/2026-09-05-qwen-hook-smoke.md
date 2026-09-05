# Installed Qwen hook compatibility — 2026-09-05

## Scope

Provider-free experiment against Qwen Code 0.22.0 on contabo-prod, executed as
claude-developer in fresh workspaces with fresh HOME and QWEN_HOME. A loopback
SSE server returned one shell invocation followed by final text. Dummy credential
only. No Sherlock skill was deliberately loaded. This tests CLI hook semantics,
not Sherlock quality or the completed lifecycle supervisor.

Script: `tools/tests/qwen_hook_smoke.py`. Raw requests, SSE responses, hook stdin,
CLI stdout/stderr, exact launch arguments and settings remain under local and
remote `sherlock-qwen-hook-smoke-20260905-r1` and `-r2` directories. The paired
JSON contains complete relative-path SHA256 inventories and compact outcomes.

## Results

| Run | Hook mode | Marker created | Hook captures | CLI exit | Seconds |
| --- | --- | --- | --- | --- | --- |
| r1 | allow | yes | none | 0 | 8.302 |
| r1 | explicit deny | yes | none | 0 | 7.532 |
| r1 | exit 7 | yes | none | 0 | 5.556 |
| r2 | allow | yes | matching Pre/Post | 0 | 5.425 |
| r2 | explicit deny | no | Pre only | 0 | 4.618 |
| r2 | exit 7 | yes | matching Pre/Post | 0 | 5.003 |

Each scenario sent two loopback requests. R1 is a rejected experiment: hook
`timeout: 10` meant ten milliseconds, not ten seconds. R2 corrected this to
10000. Explicit permission denial prevented the tool. A hook process exiting 7
allowed the tool to execute. CLI exit zero did not establish hook success or
absence of a denied action. A denied Pre event legitimately has no Post event.

Installed source `chunk-T6XLJRQY.js`, lines 42095–42225, independently shows
PreToolUse hook transport failures/exceptions returning `shouldProceed: true`.
The [official Qwen hook documentation](https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/)
confirms millisecond timeout units and the explicit permission decision format
(retrieved 2026-09-05).

## Decision and next acceptance gate

Treat hooks as observations, not a crash-proof enforcement boundary. The
external lifecycle supervisor must detect faults and stop the owned process;
the proxy must reject subsequent dispatch after a permanent fault. Run the real
CLI again with that helper wired before a paid target launch. This experiment
alone does not verify those controls or prevent create/delete within one tool.

## Timeline

- R1 completed and was rejected for zero captured hooks. Raw evidence retained.
- Official documentation and installed source identified timeout units and the
  hook error behavior. Sent both findings to the lifecycle implementer.
- R2 completed all three scenarios. Copied both raw directories locally and
  generated full file hash inventories. No production service changed.
