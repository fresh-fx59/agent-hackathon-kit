# V52 corporate DeepSeek thinking-disable probe

## Purpose

R2 produced two consecutive `max_tokens=20000` responses with empty visible
content and no tool calls while consuming the output reserve as reasoning. This
probe verifies the smallest portable request-shaping correction before r3. It
does not qualify a Sherlock report or corpus investigation.

## Versioned correction

Commit `b756a90c23d7ea9fc5958533f369dfab0115c033` changes
`measure/corporate-settings.py` only in the generated
`model.generationConfig`:

```json
{
  "samplingParams": {"max_tokens": 20000},
  "reasoning": false,
  "extra_body": {"thinking": {"type": "disabled"}}
}
```

The 262,000 context window, 230,000 session-token limit, and 20,000-token
compaction reserve are unchanged. `reasoning: false` is Qwen's control; the
explicit `extra_body` is necessary because the corporate base URL is
`api.neuraldeep.ru`, not Qwen's special-cased `api.deepseek.com` hostname.

## Verification

`python3 measure/tests/test_corporate_settings.py` passed after the test first
asserted both emitted fields. A direct emitted-settings assertion also passed
and `git diff --check` passed.

The authorized one-turn paid probe used a new root
`/home/claude-developer/hack/sherlock-thinking-probe-hkVrfw`. Its raw OpenAI
record is
`openai-logs/openai-2026-09-07T05-04-18.510Z-aefebadc.json`. It records:

* requested model `deepseek-v4-flash`, `max_tokens: 20000`, and
  `thinking: {"type":"disabled"}`;
* returned model `deepseek-v4-flash`, `finish_reason: stop`, visible content
  `PONG`, zero reasoning characters, and three completion tokens;
* exit status `0`, correct run-root cwd, and empty stderr.

This proves the Qwen 0.22.0 request and the corporate proxy behavior needed for
the next fresh run. It does not prove report correctness, source coverage, or
model behavior on an investigation prompt.
