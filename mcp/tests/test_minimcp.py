#!/usr/bin/env python3
"""End-to-end tests for mcp/lib/minimcp.py.

Writes a tiny MCP server script into a temp dir, spawns it as a subprocess and
drives the real stdio JSON-RPC transport through its stdin/stdout pipes --
exactly the way an MCP client would.  No network, no fixed ports.

Run standalone:  python3 mcp/tests/test_minimcp.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(TESTS_DIR, os.pardir, "lib")

# The server-under-test: three tools exercising every handler return path.
SERVER_SCRIPT = """\
import sys
sys.path.insert(0, %(lib_dir)r)
from minimcp import Tool, ToolError, serve

def echo(args):
    return "echo:" + str(args.get("text", ""))

def stats(args):
    return {"count": 2, "items": ["a", "b"]}

def boom(args):
    raise ToolError("boom failed")

serve(name="test-mcp", version="0.0.1", tools=[
    Tool("echo", "Echo text back.",
         {"type": "object", "properties": {"text": {"type": "string"}}}, echo),
    Tool("stats", "Return a dict result.", {"type": "object"}, stats),
    Tool("boom", "Always fails with ToolError.", {"type": "object"}, boom),
])
""" % {"lib_dir": os.path.abspath(LIB_DIR)}


class MiniMcpTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="minimcp-test-")
        cls.server_path = os.path.join(cls.tmpdir, "server.py")
        with open(cls.server_path, "w", encoding="utf-8") as fh:
            fh.write(SERVER_SCRIPT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.proc = subprocess.Popen(
            [sys.executable, self.server_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")

    def tearDown(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        if self.proc.stdout:
            self.proc.stdout.close()

    # -- plumbing ---------------------------------------------------------

    def send_raw(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def read_message(self):
        line = self.proc.stdout.readline()
        self.assertTrue(line, "server closed stdout unexpectedly")
        return json.loads(line)

    def request(self, msg_id, method, params=None):
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))
        reply = self.read_message()
        self.assertEqual(reply.get("id"), msg_id)
        return reply

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))

    def initialize(self, protocol_version="2025-06-18"):
        return self.request(1, "initialize", {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0"},
        })

    def call_tool(self, msg_id, name, arguments):
        reply = self.request(msg_id, "tools/call",
                             {"name": name, "arguments": arguments})
        return reply["result"]

    # -- lifecycle --------------------------------------------------------

    def test_initialize_echoes_client_protocol_version(self):
        reply = self.initialize(protocol_version="2024-11-05")
        result = reply["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "test-mcp")
        self.assertEqual(result["serverInfo"]["version"], "0.0.1")
        self.assertIn("tools", result["capabilities"])

    def test_initialize_defaults_protocol_version(self):
        reply = self.request(1, "initialize", {"capabilities": {}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")

    def test_notifications_are_silently_ignored(self):
        self.initialize()
        self.notify("notifications/initialized")
        self.notify("notifications/cancelled", {"requestId": 1})
        # The very next reply must belong to this ping -- proving neither
        # notification produced any output.
        reply = self.request(99, "ping")
        self.assertEqual(reply["result"], {})

    def test_ping_returns_empty_object(self):
        self.initialize()
        reply = self.request(2, "ping")
        self.assertEqual(reply["result"], {})

    # -- tools/list -------------------------------------------------------

    def test_tools_list(self):
        self.initialize()
        reply = self.request(2, "tools/list")
        tools = reply["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(sorted(names), ["boom", "echo", "stats"])
        for tool in tools:
            self.assertIn("description", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    # -- tools/call -------------------------------------------------------

    def test_call_tool_string_result(self):
        self.initialize()
        result = self.call_tool(2, "echo", {"text": "hi"})
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"], [{"type": "text", "text": "echo:hi"}])

    def test_call_tool_dict_result_is_json_encoded(self):
        self.initialize()
        result = self.call_tool(2, "stats", {})
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]),
                         {"count": 2, "items": ["a", "b"]})

    def test_call_tool_error_sets_is_error(self):
        self.initialize()
        result = self.call_tool(2, "boom", {})
        self.assertTrue(result["isError"])
        self.assertIn("boom failed", result["content"][0]["text"])

    def test_call_unknown_tool_sets_is_error(self):
        self.initialize()
        result = self.call_tool(2, "no_such_tool", {})
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])

    # -- protocol errors --------------------------------------------------

    def test_unknown_method_returns_32601(self):
        self.initialize()
        reply = self.request(5, "resources/list")
        self.assertEqual(reply["error"]["code"], -32601)

    def test_parse_error_returns_32700(self):
        self.send_raw("this is not json {")
        reply = self.read_message()
        self.assertIsNone(reply["id"])
        self.assertEqual(reply["error"]["code"], -32700)
        # The server must survive a parse error and keep working.
        reply = self.request(6, "ping")
        self.assertEqual(reply["result"], {})


if __name__ == "__main__":
    unittest.main()
