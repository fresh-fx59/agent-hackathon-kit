#!/usr/bin/env python3
"""MCP stdio server for the test-management system (TMS) mock.

Tools: list_testcases, create_run -- thin wrappers over the TMS HTTP API
(see mocks/tms/app.py).

Config:
    TMS_URL  base URL of the TMS (default http://127.0.0.1:8804)

Client config example (mcpServers style):
    {"tms": {"command": "python3", "args": ["mcp/tms_mcp.py"]}}
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from minimcp import Tool, ToolError, serve  # noqa: E402

BASE_URL = os.environ.get("TMS_URL", "http://127.0.0.1:8804").rstrip("/")
USER_AGENT = "agent-hackathon-kit/0.1"
TIMEOUT_S = 10


def _http(method, path, body=None, query=None):
    """One JSON request to the TMS; returns the parsed response."""
    url = BASE_URL + path
    if query:
        pairs = {k: v for k, v in query.items() if v not in (None, "")}
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)
    data = None
    headers = {"User-Agent": USER_AGENT}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:
            detail = ""
        raise ToolError("TMS returned HTTP %d: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise ToolError(
            "cannot reach the TMS at %s (%s) -- is the mock running? "
            "Start it with: python3 mocks/tms/app.py" % (BASE_URL, exc.reason))


# -- tool handlers --------------------------------------------------------

def list_testcases(args):
    return _http("GET", "/testcases", query={"area": args.get("area")})


def create_run(args):
    case_ids = args.get("case_ids")
    if (not isinstance(case_ids, list) or not case_ids
            or any(not isinstance(x, str) for x in case_ids)):
        raise ToolError("'case_ids' must be a non-empty list of testcase ids "
                        "(e.g. [\"TC-3\", \"TC-8\"])")
    body = {"case_ids": case_ids, "reason": args.get("reason", "")}
    return _http("POST", "/runs", body=body)


# -- schemas (mirror the TMS HTTP API) ------------------------------------

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "area": {"type": "string",
                 "description": "Optional area filter (e.g. \"cart\", "
                                "\"checkout\", \"auth\", \"e2e\")."},
    },
}

CREATE_RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "case_ids": {"type": "array", "items": {"type": "string"},
                     "description": "Testcase ids to include in the run, "
                                    "e.g. [\"TC-3\", \"TC-8\"]."},
        "reason": {"type": "string",
                   "description": "Why this selection (e.g. which MR/diff "
                                  "triggered the regression run)."},
    },
    "required": ["case_ids"],
}

TOOLS = [
    Tool("list_testcases",
         "List the testcases of the TMS (id, title, area, priority, "
         "files_covered, tags, last_result, avg_duration_min), optionally "
         "filtered by area.", LIST_SCHEMA, list_testcases),
    Tool("create_run",
         "Create a test run from selected testcase ids (returns run_id and "
         "the included cases).", CREATE_RUN_SCHEMA, create_run),
]


if __name__ == "__main__":
    serve(name="tms-mcp", version="0.1.0", tools=TOOLS)
