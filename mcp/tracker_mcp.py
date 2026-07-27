#!/usr/bin/env python3
"""MCP stdio server for the corporate tracker (Jira-like) mock.

Tools: create_issue, list_issues, get_issue, update_issue -- thin wrappers
over the tracker HTTP API (see mocks/tracker/app.py).

Config:
    TRACKER_URL  base URL of the tracker (default http://127.0.0.1:8801)

Client config example (mcpServers style):
    {"tracker": {"command": "python3", "args": ["mcp/tracker_mcp.py"]}}
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

BASE_URL = os.environ.get("TRACKER_URL", "http://127.0.0.1:8801").rstrip("/")
USER_AGENT = "agent-hackathon-kit/0.1"
TIMEOUT_S = 10


def _http(method, path, body=None, query=None):
    """One JSON request to the tracker; returns the parsed response."""
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
        raise ToolError("tracker returned HTTP %d: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise ToolError(
            "cannot reach the tracker at %s (%s) -- is the mock running? "
            "Start it with: python3 mocks/tracker/app.py" % (BASE_URL, exc.reason))


def _require_id(args):
    issue_id = str(args.get("id") or "").strip()
    if not issue_id:
        raise ToolError("'id' is required (e.g. \"TRK-1\")")
    return urllib.parse.quote(issue_id, safe="")


# -- tool handlers --------------------------------------------------------

def create_issue(args):
    body = {
        "title": args.get("title"),
        "description": args.get("description", ""),
        "type": args.get("type", "BR"),
        "priority": args.get("priority", "P2"),
        "acceptance_criteria": args.get("acceptance_criteria", []),
        "links": args.get("links", []),
        "meeting_ref": args.get("meeting_ref", ""),
    }
    return _http("POST", "/issues", body=body)


def list_issues(args):
    return _http("GET", "/issues",
                 query={"type": args.get("type"), "status": args.get("status")})


def get_issue(args):
    return _http("GET", "/issues/" + _require_id(args))


def update_issue(args):
    issue_id = _require_id(args)
    fields = {k: v for k, v in args.items() if k != "id"}
    if not fields:
        raise ToolError("pass at least one field to update besides 'id'")
    return _http("PATCH", "/issues/" + issue_id, body=fields)


# -- schemas (mirror the tracker HTTP API) --------------------------------

TYPE_ENUM = {"type": "string", "enum": ["BR", "task", "bug"],
             "description": "Issue type; BR = business requirements document."}
PRIORITY_ENUM = {"type": "string", "enum": ["P1", "P2", "P3"],
                 "description": "Priority, P1 is highest."}
STATUS_ENUM = {"type": "string",
               "enum": ["open", "in_progress", "resolved", "closed"],
               "description": "Workflow status."}
AC_LIST = {"type": "array", "items": {"type": "string"},
           "description": "Acceptance criteria, one testable statement per item."}
LINKS_LIST = {"type": "array", "items": {"type": "string"},
              "description": "Related issue ids or URLs."}

CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short issue title."},
        "description": {"type": "string",
                        "description": "Full body, markdown allowed."},
        "type": TYPE_ENUM,
        "priority": PRIORITY_ENUM,
        "acceptance_criteria": AC_LIST,
        "links": LINKS_LIST,
        "meeting_ref": {"type": "string",
                        "description": "Reference to the source meeting/transcript."},
    },
    "required": ["title", "description"],
}

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "type": TYPE_ENUM,
        "status": STATUS_ENUM,
    },
}

GET_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Issue id, e.g. \"TRK-1\"."},
    },
    "required": ["id"],
}

UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Issue id, e.g. \"TRK-1\"."},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "type": TYPE_ENUM,
        "priority": PRIORITY_ENUM,
        "status": STATUS_ENUM,
        "acceptance_criteria": AC_LIST,
        "links": LINKS_LIST,
        "meeting_ref": {"type": "string"},
    },
    "required": ["id"],
}

TOOLS = [
    Tool("create_issue",
         "Create an issue in the corporate tracker (returns the created issue "
         "with its new id).", CREATE_SCHEMA, create_issue),
    Tool("list_issues",
         "List tracker issues, optionally filtered by type and/or status.",
         LIST_SCHEMA, list_issues),
    Tool("get_issue", "Fetch one tracker issue by id.", GET_SCHEMA, get_issue),
    Tool("update_issue",
         "Update fields of an existing tracker issue by id.",
         UPDATE_SCHEMA, update_issue),
]


if __name__ == "__main__":
    serve(name="tracker-mcp", version="0.1.0", tools=TOOLS)
