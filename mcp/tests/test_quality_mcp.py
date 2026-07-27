#!/usr/bin/env python3
"""End-to-end tests for mcp/quality_mcp.py.

Boots the real quality mock on an EPHEMERAL port (never the fixed 8802),
then spawns quality_mcp.py as a subprocess with QUALITY_URL pointing at it
and drives the stdio JSON-RPC transport like an MCP client would.

Run standalone:  python3 mcp/tests/test_quality_mcp.py
"""

import json
import os
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(MCP_DIR)
SERVER_PATH = os.path.join(MCP_DIR, "quality_mcp.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "mocks"))
sys.path.insert(0, os.path.join(REPO_ROOT, "mocks", "quality"))

import common  # noqa: E402
import app as quality_app  # noqa: E402


class QualityMcpTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        store = common.Store(quality_app.SEED_PATH)
        cls.http = common.serve(quality_app.build_app(store), port=0)
        cls.base_url = "http://127.0.0.1:%d" % cls.http.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def setUp(self):
        env = dict(os.environ)
        env["QUALITY_URL"] = self.base_url
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_PATH], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.next_id = 0
        self.initialize()

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

    # -- MCP client plumbing ----------------------------------------------

    def request(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, "MCP server closed stdout unexpectedly")
        reply = json.loads(line)
        self.assertEqual(reply.get("id"), self.next_id)
        return reply

    def initialize(self):
        reply = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0"}})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "quality-mcp")

    def call_tool(self, name, arguments):
        reply = self.request("tools/call",
                             {"name": name, "arguments": arguments})
        return reply["result"]

    def call_tool_json(self, name, arguments):
        result = self.call_tool(name, arguments)
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    # -- tools/list -------------------------------------------------------

    def test_tools_list(self):
        reply = self.request("tools/list")
        tools = {t["name"]: t for t in reply["result"]["tools"]}
        self.assertEqual(sorted(tools), ["get_measures", "list_issues"])
        for tool in tools.values():
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    # -- list_issues ------------------------------------------------------

    def test_list_issues_default_project(self):
        issues = self.call_tool_json("list_issues", {})
        self.assertGreaterEqual(len(issues), 12)
        self.assertIn("severity", issues[0])

    def test_list_issues_severity_filter(self):
        issues = self.call_tool_json("list_issues", {"severity": "BLOCKER"})
        self.assertGreaterEqual(len(issues), 1)
        self.assertTrue(all(i["severity"] == "BLOCKER" for i in issues))

    def test_list_issues_type_filter_lowercase_is_normalized(self):
        issues = self.call_tool_json(
            "list_issues", {"type": "vulnerability"})
        self.assertGreaterEqual(len(issues), 2)
        self.assertTrue(all(i["type"] == "VULNERABILITY" for i in issues))

    def test_list_issues_combined_filters(self):
        issues = self.call_tool_json(
            "list_issues", {"severity": "MAJOR", "type": "CODE_SMELL"})
        for issue in issues:
            self.assertEqual(issue["severity"], "MAJOR")
            self.assertEqual(issue["type"], "CODE_SMELL")

    def test_unknown_project_is_tool_error(self):
        result = self.call_tool("list_issues", {"project_key": "missing"})
        self.assertTrue(result["isError"])
        self.assertIn("404", result["content"][0]["text"])

    # -- get_measures -----------------------------------------------------

    def test_get_measures(self):
        measures = self.call_tool_json("get_measures", {})
        for field in ("coverage", "bugs", "vulnerabilities", "sqale_index"):
            self.assertIn(field, measures)

    def test_get_measures_unknown_project_is_tool_error(self):
        result = self.call_tool("get_measures", {"project_key": "missing"})
        self.assertTrue(result["isError"])


if __name__ == "__main__":
    unittest.main()
