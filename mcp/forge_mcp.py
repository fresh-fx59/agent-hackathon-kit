#!/usr/bin/env python3
"""MCP stdio server for the source forge (GitLab-like) mock.

Tools: get_mr, get_mr_diff, create_mr, get_file -- thin wrappers over the
forge HTTP API (see mocks/forge/app.py).

Config:
    FORGE_URL  base URL of the forge (default http://127.0.0.1:8803)

Client config example (mcpServers style):
    {"forge": {"command": "python3", "args": ["mcp/forge_mcp.py"]}}
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

BASE_URL = os.environ.get("FORGE_URL", "http://127.0.0.1:8803").rstrip("/")
USER_AGENT = "agent-hackathon-kit/0.1"
TIMEOUT_S = 10


def _http(method, path, body=None, query=None):
    """One JSON request to the forge; returns the parsed response."""
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
        raise ToolError("forge returned HTTP %d: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise ToolError(
            "cannot reach the forge at %s (%s) -- is the mock running? "
            "Start it with: python3 mocks/forge/app.py" % (BASE_URL, exc.reason))


def _project(args):
    """Project id, defaulting to the seeded project \"1\"."""
    return urllib.parse.quote(str(args.get("project_id") or "1"), safe="")


def _iid(args):
    iid = args.get("iid")
    if iid in (None, ""):
        raise ToolError("'iid' is required (the MR number, e.g. 1)")
    return urllib.parse.quote(str(iid), safe="")


# -- tool handlers --------------------------------------------------------

def get_mr(args):
    return _http("GET", "/projects/%s/merge_requests/%s"
                 % (_project(args), _iid(args)))


def get_mr_diff(args):
    return _http("GET", "/projects/%s/merge_requests/%s/changes"
                 % (_project(args), _iid(args)))


def create_mr(args):
    body = {
        "title": args.get("title"),
        "description": args.get("description", ""),
        "source_branch": args.get("source_branch"),
        "target_branch": args.get("target_branch", "main"),
    }
    return _http("POST", "/projects/%s/merge_requests" % _project(args),
                 body=body)


def get_file(args):
    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("'path' is required (e.g. \"app/cart.py\")")
    return _http("GET", "/projects/%s/repository/files" % _project(args),
                 query={"path": path})


# -- schemas (mirror the forge HTTP API) ----------------------------------

PROJECT_ID = {"type": "string",
              "description": "Project id; defaults to \"1\" (the seeded demo "
                             "project)."}
IID = {"type": "integer", "description": "Merge-request number (iid), e.g. 1."}

GET_MR_SCHEMA = {
    "type": "object",
    "properties": {"project_id": PROJECT_ID, "iid": IID},
    "required": ["iid"],
}

CREATE_MR_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": PROJECT_ID,
        "title": {"type": "string", "description": "Merge-request title."},
        "description": {"type": "string",
                        "description": "Full description, markdown allowed."},
        "source_branch": {"type": "string",
                          "description": "Branch with the changes."},
        "target_branch": {"type": "string",
                          "description": "Branch to merge into "
                                         "(default \"main\")."},
    },
    "required": ["title", "source_branch"],
}

GET_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": PROJECT_ID,
        "path": {"type": "string",
                 "description": "Repository-relative file path, "
                                "e.g. \"app/cart.py\"."},
    },
    "required": ["path"],
}

TOOLS = [
    Tool("get_mr",
         "Fetch a merge request's summary (title, description, branches, "
         "changes_count).", GET_MR_SCHEMA, get_mr),
    Tool("get_mr_diff",
         "Fetch a merge request's changed files with their unified diffs "
         "({changes: [{old_path, new_path, diff}]}).",
         GET_MR_SCHEMA, get_mr_diff),
    Tool("create_mr",
         "Open a new merge request (returns it with its new iid).",
         CREATE_MR_SCHEMA, create_mr),
    Tool("get_file",
         "Read one file's current content from the repository.",
         GET_FILE_SCHEMA, get_file),
]


if __name__ == "__main__":
    serve(name="forge-mcp", version="0.1.0", tools=TOOLS)
