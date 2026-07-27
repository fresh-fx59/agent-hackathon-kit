#!/usr/bin/env python3
"""End-to-end tests for mcp/forge_mcp.py.

Boots the forge mock in-process on an EPHEMERAL port (never 8801-8804),
then spawns the real MCP server as a subprocess with FORGE_URL pointing at
that mock, and drives the stdio JSON-RPC transport like an MCP client would.

Run standalone:  python3 mcp/tests/test_forge_mcp.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.path.dirname(TESTS_DIR)
REPO_DIR = os.path.dirname(MCP_DIR)
MOCKS_DIR = os.path.join(REPO_DIR, "mocks")
SERVER_PATH = os.path.join(MCP_DIR, "forge_mcp.py")

sys.path.insert(0, MOCKS_DIR)  # for common.py

import common  # noqa: E402


def _load_forge_module():
    path = os.path.join(MOCKS_DIR, "forge", "app.py")
    spec = importlib.util.spec_from_file_location("forge_app_for_mcp_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forge_app = _load_forge_module()


class ForgeMcpBase(unittest.TestCase):
    """Plumbing: mock on an ephemeral port + MCP server subprocess."""

    # Subclasses may point the MCP server elsewhere (e.g. a dead URL).
    def forge_url(self):
        return "http://127.0.0.1:%d" % self.mock_server.server_address[1]

    def setUp(self):
        app, self.store = forge_app.make_app()  # fresh seed data per test
        self.mock_server = common.serve(app, port=0)
        env = dict(os.environ)
        env["FORGE_URL"] = self.forge_url()
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_PATH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, text=True, encoding="utf-8")

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
        self.mock_server.shutdown()
        self.mock_server.server_close()

    def request(self, msg_id, method, params=None):
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, "MCP server closed stdout unexpectedly")
        reply = json.loads(line)
        self.assertEqual(reply.get("id"), msg_id)
        return reply

    def initialize(self):
        return self.request(1, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0"},
        })

    def call_tool(self, msg_id, name, arguments):
        """Return (is_error, text) of one tools/call."""
        reply = self.request(msg_id, "tools/call",
                             {"name": name, "arguments": arguments})
        result = reply["result"]
        return result["isError"], result["content"][0]["text"]

    def call_tool_json(self, msg_id, name, arguments):
        """Like call_tool but asserts success and parses the JSON payload."""
        is_error, text = self.call_tool(msg_id, name, arguments)
        self.assertFalse(is_error, "tool unexpectedly failed: %s" % text)
        return json.loads(text)


class ForgeMcpTest(ForgeMcpBase):

    def test_initialize_and_tools_list(self):
        reply = self.initialize()
        self.assertEqual(reply["result"]["serverInfo"]["name"], "forge-mcp")
        tools = self.request(2, "tools/list")["result"]["tools"]
        names = sorted(t["name"] for t in tools)
        self.assertEqual(names, ["create_mr", "get_file", "get_mr",
                                 "get_mr_diff"])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])

    def test_get_mr_defaults_to_project_1(self):
        self.initialize()
        mr = self.call_tool_json(2, "get_mr", {"iid": 1})
        self.assertEqual(mr["iid"], 1)
        self.assertEqual(mr["target_branch"], "main")
        self.assertEqual(mr["changes_count"], 2)

    def test_get_mr_diff(self):
        self.initialize()
        payload = self.call_tool_json(2, "get_mr_diff",
                                      {"project_id": "1", "iid": 1})
        paths = [c["new_path"] for c in payload["changes"]]
        self.assertEqual(paths, ["app/cart.py", "app/checkout.py"])
        self.assertIn("MAX_QUANTITY_PER_LINE", payload["changes"][0]["diff"])

    def test_get_file(self):
        self.initialize()
        payload = self.call_tool_json(2, "get_file", {"path": "app/cart.py"})
        self.assertEqual(payload["path"], "app/cart.py")
        self.assertIn("class Cart", payload["content"])

    def test_create_mr_then_get(self):
        self.initialize()
        created = self.call_tool_json(2, "create_mr", {
            "title": "fix(cart): stock check",
            "source_branch": "fix/stock",
        })
        self.assertEqual(created["iid"], 2)
        self.assertEqual(created["target_branch"], "main")  # default applied
        again = self.call_tool_json(3, "get_mr", {"iid": 2})
        self.assertEqual(again["title"], "fix(cart): stock check")

    # -- error paths ------------------------------------------------------

    def test_get_mr_requires_iid(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_mr", {})
        self.assertTrue(is_error)
        self.assertIn("iid", text)

    def test_unknown_mr_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_mr", {"iid": 99})
        self.assertTrue(is_error)
        self.assertIn("404", text)

    def test_get_file_requires_path(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_file", {})
        self.assertTrue(is_error)
        self.assertIn("path", text)

    def test_unknown_file_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_file", {"path": "nope.py"})
        self.assertTrue(is_error)
        self.assertIn("404", text)


class ForgeMcpUnreachableTest(ForgeMcpBase):
    """The MCP server must fail gracefully when the forge is down."""

    def forge_url(self):
        # Reserve a port, then close it: nothing listens there afterwards.
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return "http://127.0.0.1:%d" % port

    def test_unreachable_forge_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_mr", {"iid": 1})
        self.assertTrue(is_error)
        self.assertIn("is the mock running", text)


if __name__ == "__main__":
    unittest.main()
