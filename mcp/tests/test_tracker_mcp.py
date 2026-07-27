#!/usr/bin/env python3
"""End-to-end tests for mcp/tracker_mcp.py.

Boots the tracker mock in-process on an EPHEMERAL port (never 8801-8804),
then spawns the real MCP server as a subprocess with TRACKER_URL pointing at
that mock, and drives the stdio JSON-RPC transport like an MCP client would.

Run standalone:  python3 mcp/tests/test_tracker_mcp.py
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
SERVER_PATH = os.path.join(MCP_DIR, "tracker_mcp.py")

sys.path.insert(0, MOCKS_DIR)  # for common.py

import common  # noqa: E402


def _load_tracker_module():
    path = os.path.join(MOCKS_DIR, "tracker", "app.py")
    spec = importlib.util.spec_from_file_location("tracker_app_for_mcp_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tracker_app = _load_tracker_module()


class TrackerMcpBase(unittest.TestCase):
    """Plumbing: mock on an ephemeral port + MCP server subprocess."""

    # Subclasses may point the MCP server elsewhere (e.g. a dead URL).
    def tracker_url(self):
        return "http://127.0.0.1:%d" % self.mock_server.server_address[1]

    def setUp(self):
        app, self.store = tracker_app.make_app()  # fresh seed data per test
        self.mock_server = common.serve(app, port=0)
        env = dict(os.environ)
        env["TRACKER_URL"] = self.tracker_url()
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


class TrackerMcpTest(TrackerMcpBase):

    def test_initialize_and_tools_list(self):
        reply = self.initialize()
        self.assertEqual(reply["result"]["serverInfo"]["name"], "tracker-mcp")
        tools = self.request(2, "tools/list")["result"]["tools"]
        names = sorted(t["name"] for t in tools)
        self.assertEqual(names, ["create_issue", "get_issue",
                                 "list_issues", "update_issue"])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])

    def test_list_issues_returns_seed(self):
        self.initialize()
        issues = self.call_tool_json(2, "list_issues", {})
        self.assertEqual([i["id"] for i in issues], ["TRK-1", "TRK-2", "TRK-3"])

    def test_list_issues_with_filter(self):
        self.initialize()
        issues = self.call_tool_json(2, "list_issues", {"type": "BR"})
        self.assertEqual([i["id"] for i in issues], ["TRK-1"])

    def test_create_then_get_issue(self):
        self.initialize()
        created = self.call_tool_json(2, "create_issue", {
            "title": "BR: заявки на командировки",
            "description": "Собрано из стенограммы встречи.",
            "type": "BR",
            "priority": "P1",
            "acceptance_criteria": ["Форма заявки открывается из портала"],
            "meeting_ref": "meet-2026-07-27-trips",
        })
        self.assertEqual(created["id"], "TRK-4")
        self.assertEqual(created["status"], "open")
        fetched = self.call_tool_json(3, "get_issue", {"id": "TRK-4"})
        self.assertEqual(fetched["title"], "BR: заявки на командировки")
        self.assertEqual(fetched["meeting_ref"], "meet-2026-07-27-trips")

    def test_create_issue_defaults_to_br_type(self):
        self.initialize()
        created = self.call_tool_json(2, "create_issue",
                                      {"title": "t", "description": "d"})
        self.assertEqual(created["type"], "BR")

    def test_update_issue(self):
        self.initialize()
        updated = self.call_tool_json(2, "update_issue",
                                      {"id": "TRK-2", "status": "resolved",
                                       "priority": "P3"})
        self.assertEqual(updated["status"], "resolved")
        self.assertEqual(updated["priority"], "P3")
        again = self.call_tool_json(3, "get_issue", {"id": "TRK-2"})
        self.assertEqual(again["status"], "resolved")

    # -- error paths ------------------------------------------------------

    def test_get_unknown_issue_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_issue", {"id": "TRK-999"})
        self.assertTrue(is_error)
        self.assertIn("404", text)

    def test_get_issue_requires_id(self):
        self.initialize()
        is_error, text = self.call_tool(2, "get_issue", {})
        self.assertTrue(is_error)
        self.assertIn("id", text)

    def test_update_issue_requires_fields(self):
        self.initialize()
        is_error, text = self.call_tool(2, "update_issue", {"id": "TRK-1"})
        self.assertTrue(is_error)
        self.assertIn("at least one field", text)

    def test_create_issue_validation_error_surfaces(self):
        self.initialize()
        is_error, text = self.call_tool(2, "create_issue",
                                        {"title": "x", "description": "y",
                                         "priority": "P9"})
        self.assertTrue(is_error)
        self.assertIn("400", text)


class TrackerMcpUnreachableTest(TrackerMcpBase):
    """The MCP server must fail gracefully when the tracker is down."""

    def tracker_url(self):
        # Reserve a port, then close it: nothing listens there afterwards.
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return "http://127.0.0.1:%d" % port

    def test_unreachable_tracker_is_tool_error(self):
        self.initialize()
        is_error, text = self.call_tool(2, "list_issues", {})
        self.assertTrue(is_error)
        self.assertIn("is the mock running", text)


if __name__ == "__main__":
    unittest.main()
