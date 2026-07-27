#!/usr/bin/env python3
"""End-to-end tests for mcp/tms_mcp.py.

Boots the TMS mock in-process on an EPHEMERAL port (never 8801-8804), then
spawns the real MCP server as a subprocess with TMS_URL pointing at that
mock, and drives the stdio JSON-RPC transport like an MCP client would.

Run standalone:  python3 mcp/tests/test_tms_mcp.py
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
SERVER_PATH = os.path.join(MCP_DIR, "tms_mcp.py")

sys.path.insert(0, MOCKS_DIR)  # for common.py

import common  # noqa: E402


def _load_tms_module():
    path = os.path.join(MOCKS_DIR, "tms", "app.py")
    spec = importlib.util.spec_from_file_location("tms_app_for_mcp_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tms_app = _load_tms_module()


class TmsMcpBase(unittest.TestCase):
    """Plumbing: mock on an ephemeral port + MCP server subprocess."""

    # Subclasses may point the MCP server elsewhere (e.g. a dead URL).
    def tms_url(self):
        return "http://127.0.0.1:%d" % self.mock_server.server_address[1]

    def setUp(self):
        app, self.store = tms_app.make_app()  # fresh seed data per test
        self.mock_server = common.serve(app, port=0)
        env = dict(os.environ)
        env["TMS_URL"] = self.tms_url()
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


class TmsMcpTest(TmsMcpBase):

    def test_initialize_and_tools_list(self):
        reply = self.initialize()
        self.assertEqual(reply["result"]["serverInfo"]["name"], "tms-mcp")
        tools = self.request(2, "tools/list")["result"]["tools"]
        names = sorted(t["name"] for t in tools)
        self.assertEqual(names, ["create_run", "list_testcases"])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])

    def test_list_testcases_returns_seed(self):
        self.initialize()
        cases = self.call_tool_json(2, "list_testcases", {})
        self.assertEqual(len(cases), 25)
        self.assertEqual(cases[0]["id"], "TC-1")

    def test_list_testcases_with_area_filter(self):
        self.initialize()
        cases = self.call_tool_json(2, "list_testcases", {"area": "checkout"})
        self.assertEqual([c["id"] for c in cases],
                         ["TC-8", "TC-9", "TC-10", "TC-11", "TC-12"])

    def test_create_run(self):
        self.initialize()
        run = self.call_tool_json(2, "create_run", {
            "case_ids": ["TC-3", "TC-8", "TC-20"],
            "reason": "MR !1 меняет cart.py и checkout.py",
        })
        self.assertEqual(run["run_id"], "RUN-1")
        self.assertEqual([c["id"] for c in run["cases"]],
                         ["TC-3", "TC-8", "TC-20"])
        self.assertEqual(run["reason"], "MR !1 меняет cart.py и checkout.py")

    # -- error paths ------------------------------------------------------

    def test_create_run_requires_case_ids(self):
        self.initialize()
        for bad_args in ({}, {"case_ids": []}, {"case_ids": "TC-1"},
                         {"case_ids": [1]}):
            is_error, text = self.call_tool(2, "create_run", bad_args)
            self.assertTrue(is_error)
            self.assertIn("case_ids", text)

    def test_create_run_unknown_id_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "create_run",
                                        {"case_ids": ["TC-999"]})
        self.assertTrue(is_error)
        self.assertIn("TC-999", text)


class TmsMcpUnreachableTest(TmsMcpBase):
    """The MCP server must fail gracefully when the TMS is down."""

    def tms_url(self):
        # Reserve a port, then close it: nothing listens there afterwards.
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return "http://127.0.0.1:%d" % port

    def test_unreachable_tms_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "list_testcases", {})
        self.assertTrue(is_error)
        self.assertIn("is the mock running", text)


if __name__ == "__main__":
    unittest.main()
