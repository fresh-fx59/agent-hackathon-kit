# Dedicated monitor reviewer probes

Status: subscription reviewer probes completed; no lifecycle publication and no provider-under-test calls.

## Direct terminal probe

The same preserved r3 terminal snapshot (SHA256 `768e6b61ad8b8164ea21a9760d253e0c2c47dc14e9e6102af0660137f5594988`, 18,608 snapshot bytes) was sent with tools disabled to two Claude aliases:

- Haiku 4.5, low effort: elapsed 18.040168568957597 s, API duration 17,233 ms, decision `continue`, exit 0. It incorrectly treated the terminal `OBSERVATION_STALE`/blocked controller as recoverable because later tool execution was present.
- Sonnet 5, low effort: elapsed 6.928299456019886 s, API duration 5,842 ms, decision `stop`, exit 0. It correctly treated the terminal evidence as a stop. The exact Sonnet output and result metadata are under `docs/run-reports/artifacts/2026-09-06-dedicated-monitor-sonnet-probe/`; the Haiku comparison is under the corresponding Haiku directory.

Usage and cost accounting are reported as returned by the CLI. Sonnet model usage was input 2, output 169, cache creation 15,174, cache read 3,289, returned list-price basis USD `0.0630478`; the CLI aggregate was USD `0.0717298` because the invocation also reports a Haiku subagent (input 8,607, output 15, list basis USD `0.008682`). Haiku model usage was input 10, output 1,150, cache creation 14,002, returned list-price basis USD `0.042446`; the CLI aggregate was also USD `0.042446`. These are provider list-price estimates from the CLI, not invoices.

## Historical r3 reconstruction replay

This is a reconstruction, not a contemporary observation. It was cut at the last accepted observation (`2026-09-06T17:06:22.167304Z`) from preserved r3 evidence. It retained only hooks through that cut, the earlier completed upstream records, and the request `55161263799d4db6af43ef030f69838d` explicitly recorded as pending since `17:01:26`. The reconstructed snapshot is 6,154 bytes, SHA256 `b9ec136b26983f6113b0b8f0784fe4ffaa63ebe52288c4c1a475991d60f905b6`.

The same reviewer instruction and `claude -p --model sonnet --effort low --tools "" --setting-sources "" --no-session-persistence --output-format json` were used. It returned `continue`, exit 0, elapsed approximately 6.4 seconds in the captured wrapper and API duration 5,595 ms. Replay primary Sonnet list-price usage was USD `0.0423938`; its auxiliary Haiku was USD `0.003538`, aggregate USD `0.0459318`. Its reason was that the reconstructed controller was `QWEN_RUNNING`, `fault` was null, and the pending request alone was not a stall. This supports the distinction between a pre-fault historical state and the later terminal fault; it does not prove the live monitor would have continued safely.

Replay input, output, stderr, and result metadata are under `docs/run-reports/artifacts/2026-09-06-dedicated-monitor-sonnet-replay-r3/`. No acceptance or lifecycle decision was published from these probes.
