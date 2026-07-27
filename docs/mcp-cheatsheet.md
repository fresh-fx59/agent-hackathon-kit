# MCP cheatsheet — everything you need in one page

## MCP in 60 seconds

MCP (Model Context Protocol) lets an agent call your tools. The transport used
by this kit is the simplest one: **stdio** — the agent spawns your server as a
subprocess and exchanges **newline-delimited JSON-RPC 2.0** messages over
stdin/stdout (UTF-8, one message per line).

The handshake and the two calls that matter:

```
client → {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}
server → {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"tracker-mcp","version":"0.1.0"}}}
client → {"jsonrpc":"2.0","method":"notifications/initialized"}          (no reply — it's a notification)
client → {"jsonrpc":"2.0","id":2,"method":"tools/list"}
server → {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"create_issue","description":"...","inputSchema":{...}}]}}
client → {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_issue","arguments":{"title":"..."}}}
server → {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{...}"}],"isError":false}}
```

Rules the kit servers follow (and yours should too):

- Tool results go in `content` as text blocks; a *tool-level* failure is
  `"isError": true` with an explanatory text block — **not** a JSON-RPC error.
- JSON-RPC errors are for protocol problems only: unknown method → `-32601`,
  unparseable line → `-32700`.
- Requests carry an `id` and get exactly one response; notifications
  (`notifications/initialized`, `notifications/cancelled`) have no `id` and
  get **no** response. Reply `{}` to `ping`.
- **stdout is for protocol messages ONLY.** All logging goes to stderr.

Writing a new server with the kit framework takes ~30 lines — see
`mcp/lib/minimcp.py` and any `mcp/*_mcp.py` as a template.

## Registering a stdio server in an agent CLI

Most agent CLIs (Claude-Code-like, qwen-coder-like) share one config shape —
a JSON map of server name → spawn command:

```json
{
  "mcpServers": {
    "tracker": {
      "command": "python3",
      "args": ["/absolute/path/to/mcp/tracker_mcp.py"],
      "env": {"TRACKER_URL": "http://127.0.0.1:8801"}
    }
  }
}
```

Where that JSON lives differs per CLI (a `.json` file in the project root, a
global config dir, or a `config add-mcp` command) — check the target CLI's
docs; the inner object is nearly always the same `command`/`args`/`env`
triple. A generic variant, if the CLI takes servers as a flat list:

```json
{
  "servers": [
    {"name": "tracker", "transport": "stdio",
     "command": ["python3", "mcp/tracker_mcp.py"],
     "env": {"TRACKER_URL": "http://127.0.0.1:8801"}}
  ]
}
```

Use **absolute paths** for `args` — CLIs differ in what cwd they spawn servers
from. Ready-made config examples for all four kit servers: `mcp/configs/`.

## Smoke-testing a server with a raw pipe

No client needed — a server is just a program that reads lines and writes
lines. From the repo root:

```bash
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 mcp/tracker_mcp.py
```

Expect two JSON lines back: an `initialize` result and a `tools` array with
at least one tool. Add a third line with `tools/call` to test a real call
(start the mocks first: `python3 mocks/run_all.py`). This is exactly what
`scripts/verify.sh` does for every kit server.

## Debugging tips

- **Server prints nothing:** it probably crashed on import — run
  `python3 mcp/tracker_mcp.py < /dev/null` and read stderr.
- **Client says "invalid response":** you printed something non-JSON to
  stdout (a stray `print()`). Log with
  `print(..., file=sys.stderr)` only.
- **Hangs:** you forgot to flush. Write with `sys.stdout.write(line + "\n");
  sys.stdout.flush()` (minimcp does this for you).
- **Multi-line JSON breaks the pipe:** one message = one line. Use
  `json.dumps(obj)` (no `indent`).
- **Tool errors vanish:** raise `ToolError("human-readable reason")` inside a
  handler — the agent sees the text and can react; a raw exception becomes an
  opaque failure.
- Watch a live conversation: wrap the command in the client config with
  `tee`, e.g. `"command": "bash", "args": ["-c", "tee /tmp/in.log | python3 mcp/tracker_mcp.py | tee /tmp/out.log"]`.

## Pointing kit servers at REAL corporate systems

Every kit server reads its backend base URL from an env var and defaults to
the local mock:

| Server | Env var | Default |
|---|---|---|
| `mcp/tracker_mcp.py` | `TRACKER_URL` | `http://127.0.0.1:8801` |
| `mcp/quality_mcp.py` | `QUALITY_URL` | `http://127.0.0.1:8802` |
| `mcp/forge_mcp.py` | `FORGE_URL` | `http://127.0.0.1:8803` |
| `mcp/tms_mcp.py` | `TMS_URL` | `http://127.0.0.1:8804` |

So switching from rehearsal to the real corporate tracker is a config-only
change — set the env var in the `mcpServers` entry:

```json
"env": {"TRACKER_URL": "https://tracker.internal.example", "TRACKER_TOKEN": "..."}
```

Real systems need auth; the mocks don't. Add the header once at the
urllib call site (all kit HTTP goes through one helper per server and always
sends `User-Agent: agent-hackathon-kit/0.1`) — see `docs/transfer.md`.
