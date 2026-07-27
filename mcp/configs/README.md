# MCP client configs

The four MCP servers in `mcp/` are plain **stdio** servers: the client starts
`python3 mcp/<name>_mcp.py` as a subprocess and exchanges newline-delimited
JSON-RPC 2.0 over stdin/stdout. Any MCP-capable client (CLI agent, IDE plugin)
can use them — only the config file shape differs.

## Servers and their env vars

| Server | Script | Base-URL env var | Default | Mock port |
|---|---|---|---|---|
| tracker (Jira-like) | `mcp/tracker_mcp.py` | `TRACKER_URL` | `http://127.0.0.1:8801` | 8801 |
| quality (SonarQube-like) | `mcp/quality_mcp.py` | `QUALITY_URL` | `http://127.0.0.1:8802` | 8802 |
| forge (GitLab-like) | `mcp/forge_mcp.py` | `FORGE_URL` | `http://127.0.0.1:8803` | 8803 |
| tms (test management) | `mcp/tms_mcp.py` | `TMS_URL` | `http://127.0.0.1:8804` | 8804 |

Point the env var at the real corporate system instead of the mock and the
same server works unchanged — that is the whole idea.

## Example configs in this directory

- **`mcpServers.example.json`** — the common `"mcpServers"` object shape used
  by most desktop/CLI MCP clients. Copy the relevant block into your client's
  config and **replace `/absolute/path/to/agent-hackathon-kit` with the real
  checkout path** (many clients do not expand relative paths or `~`).
- **`generic-servers.example.json`** — a client-agnostic inventory of all four
  servers (command, args, env, tool list). Use it as the source of truth when
  your client wants a different config syntax.

## Debugging without any client

A stdio MCP server is just a process reading lines — you can drive it from a
shell pipe:

```sh
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 mcp/tracker_mcp.py
```

You should see two JSON replies (initialize result, then the tool list).
Server-side logs go to **stderr** only — stdout carries nothing but protocol
frames. See `docs/mcp-cheatsheet.md` for the full message walkthrough.
