# Design & contracts

The kit is a rehearsal environment for agent-hackathon tracks built around one
pipeline shape shared by every known case type:

```
unstructured input ──► skill-driven analysis ──► structured artifact ──► MCP write ──► benchmark
(transcript, diff,      (tracks/*/skill.md)       (BR / findings.json /   (corporate      (cases/*/
 codebase)                                         selection.json)         system mock)    benchmark.py)
```

Everything is Python ≥3.9 **stdlib only** — no pip, no venv, no Node — so it
runs identically on a personal laptop and inside a restricted corporate
environment after a plain `git pull`.

## Ports & processes

| Mock | Role analog | Port | Env override | MCP server |
|------|-------------|------|--------------|------------|
| `mocks/tracker` | Jira-like corporate tracker | 8801 | `TRACKER_PORT` / `TRACKER_URL` | `mcp/tracker_mcp.py` |
| `mocks/quality` | SonarQube-like code quality | 8802 | `QUALITY_PORT` / `QUALITY_URL` | `mcp/quality_mcp.py` |
| `mocks/forge` | GitLab-like git platform | 8803 | `FORGE_PORT` / `FORGE_URL` | `mcp/forge_mcp.py` |
| `mocks/tms` | Test management system | 8804 | `TMS_PORT` / `TMS_URL` | `mcp/tms_mcp.py` |

All mocks bind `127.0.0.1` only and expose `GET /health` → `{"ok": true}`.
State is **in-memory** (seeds are read-only): restarting a mock resets it to a
clean seeded state — handy for re-running a demo from scratch.
`python3 mocks/run_all.py` boots all four; every `app.py` also runs standalone
from any cwd. To point an MCP server at a **real** corporate system instead of
the mock, set its `*_URL` env var (and add auth inside the server — they are
thin, readable wrappers meant to be edited).

## minimcp (`mcp/lib/minimcp.py`)

A ~150-line stdio MCP server framework:

```python
from minimcp import Tool, ToolError, serve
serve(name="tracker-mcp", version="0.1.0", tools=[
    Tool("create_issue", "Create a tracker issue", INPUT_SCHEMA, handler),
])
```

- Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout; log to stderr only.
- Implements `initialize`, `tools/list`, `tools/call`, `ping`; ignores
  `notifications/*`; `-32601` unknown method, `-32700` parse error.
- A handler returns `str` or `dict` (JSON-encoded for you); raise
  `ToolError("msg")` for a tool-level failure (`isError: true`), reserving
  JSON-RPC errors for protocol problems.

Smoke-test any server with a raw pipe (see `docs/mcp-cheatsheet.md`):

```sh
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python3 mcp/tracker_mcp.py
```

## Artifact schemas (skills produce → benchmarks consume)

- **Business requirements (analytics)** — Russian markdown following
  `tracks/analytics/templates/br-template-ru.md`; scored by
  `cases/analytics-meeting/benchmark.py` against `rubric.json`
  (`required_sections` + weighted keyword `required_facts`), 0–100.
- **`findings.json` (development)** —
  `[{file, line, category: security|bug|smell|duplication|dead-code|style,
  severity: high|medium|low, description, fix_hint}]`; scored as
  precision/recall/F1 vs `expected-findings.json` (match = file+category,
  line ±5).
- **`selection.json` (testing)** —
  `{selected: [{id, reason}], strategy}`; scored as recall over
  `expected-selection.json.must_run` (target 1.0 — a missed P1 is the cardinal
  sin) plus an efficiency ratio (tests skipped).

Benchmarks accept `--self-test` (score the shipped gold artifact; proves the
harness before you trust it with your output).

## Test & verify conventions

- Every `test_*.py` runs standalone (`python3 path/to/test_x.py`).
- Tests never bind the fixed ports — they bind port `0` (ephemeral) via
  `mocks/common.py` and read the real port back, so suites can run in parallel
  and next to live mocks.
- `bash scripts/verify.sh` = the whole contract in one command: version gate →
  all tests → boot mocks → health + MCP smoke → benchmark self-tests. Green
  verify after `git pull` in a new environment means the kit is fully
  operational there.
