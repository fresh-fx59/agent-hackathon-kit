"""minimcp -- a minimal, stdlib-only MCP (Model Context Protocol) server framework.

Speaks newline-delimited JSON-RPC 2.0 over stdio, which is exactly what MCP
clients expect from a server declared in an "mcpServers"-style config.
Python >= 3.9, zero dependencies.

Usage:
    from minimcp import Tool, ToolError, serve

    def say_hello(args):  # args = the "arguments" object sent by the client
        return "hello " + args.get("name", "world")  # return str or dict/list

    serve(name="hello-mcp", version="0.1.0", tools=[
        Tool("say_hello", "Greets someone.",
             {"type": "object", "properties": {"name": {"type": "string"}}},
             say_hello),
    ])

Rules of the road:
- stdout is RESERVED for protocol frames.  Log with log() -- it goes to stderr.
- A handler returns a str (sent verbatim) or a dict/list (json.dumps'd).
- Raise ToolError("message") for a *tool-level* failure: the client receives a
  normal tools/call result with isError=true (NOT a JSON-RPC error), so the
  model can read the message and try again.
"""

import json
import sys
import traceback

# Protocol version we advertise when the client does not state one.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"


def log(*parts):
    """Log a line to stderr (stdout belongs to the protocol)."""
    sys.stderr.write(" ".join(str(p) for p in parts) + "\n")
    sys.stderr.flush()


class ToolError(Exception):
    """Raise inside a tool handler to report a failure the model should see."""


class Tool(object):
    """One callable tool: name, human description, JSON-Schema dict, handler."""

    def __init__(self, name, description, input_schema, handler):
        self.name = name
        self.description = description
        self.input_schema = input_schema  # plain dict, e.g. {"type": "object", ...}
        self.handler = handler            # callable(arguments_dict) -> str | dict

    def describe(self):
        """The shape tools/list must return for this tool."""
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema}


def _send(message):
    """Write one JSON-RPC frame as a single line on stdout."""
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(msg_id, result):
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _reply_error(msg_id, code, message):
    _send({"jsonrpc": "2.0", "id": msg_id,
           "error": {"code": code, "message": message}})


def _error_result(text):
    """A tools/call result that marks the call as failed (isError=true)."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _call_tool(params, registry):
    """Run one tools/call request; always returns a result dict."""
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = registry.get(tool_name)
    if tool is None:
        return _error_result("Unknown tool: %r (see tools/list)" % (tool_name,))
    try:
        out = tool.handler(arguments)
    except ToolError as exc:              # expected, tool-level failure
        return _error_result(str(exc))
    except Exception:                     # unexpected bug in the handler
        log("[tool %s] crashed:\n%s" % (tool_name, traceback.format_exc()))
        return _error_result("Internal error in tool %r "
                             "(traceback on server stderr)" % (tool_name,))
    if not isinstance(out, str):          # dict/list results become pretty JSON
        out = json.dumps(out, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": out}], "isError": False}


def _handle(msg, name, version, registry):
    """Dispatch one parsed JSON-RPC message.  Notifications get no reply."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    # Lifecycle notifications we accept and deliberately ignore.
    if method in ("notifications/initialized", "notifications/cancelled"):
        return

    if method == "initialize":
        client_pv = (msg.get("params") or {}).get("protocolVersion")
        result = {
            "protocolVersion": client_pv or DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": name, "version": version},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [tool.describe() for tool in registry.values()]}
    elif method == "tools/call":
        result = _call_tool(msg.get("params") or {}, registry)
    else:
        if not is_notification:  # unknown *request* -> JSON-RPC error
            _reply_error(msg_id, -32601, "Method not found: %r" % (method,))
        return

    if not is_notification:
        _reply(msg_id, result)


def serve(name, version, tools):
    """Blocking main loop: read JSON-RPC lines from stdin until EOF."""
    registry = {}
    for tool in tools:
        registry[tool.name] = tool
    log("[%s %s] serving %d tool(s) on stdio" % (name, version, len(registry)))
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            _reply_error(None, -32700, "Parse error: not valid JSON")
            continue
        _handle(msg, name, version, registry)
    log("[%s] stdin closed, exiting" % (name,))
