#!/usr/bin/env python3
"""MCP server for the quality platform (SonarQube-like mock on port 8802).

Tools:
    list_issues(project_key="demo", severity=None, type=None)
    get_measures(project_key="demo")

Base URL comes from the QUALITY_URL env var (default http://127.0.0.1:8802).

Client config example:
    {"mcpServers": {"quality": {"command": "python3",
                                "args": ["mcp/quality_mcp.py"]}}}
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

BASE_URL = os.environ.get("QUALITY_URL", "http://127.0.0.1:8802").rstrip("/")
USER_AGENT = "agent-hackathon-kit/0.1"
TIMEOUT_S = 10


def _get(path, query=None):
    """GET a JSON document from the quality service; ToolError on failure."""
    url = BASE_URL + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:
            detail = ""
        raise ToolError("quality service answered HTTP %d for %s: %s"
                        % (exc.code, path, detail))
    except urllib.error.URLError as exc:
        raise ToolError("cannot reach the quality service at %s: %s"
                        % (BASE_URL, exc.reason))


def list_issues(args):
    """Tool: list static-analysis issues, optionally filtered."""
    project = args.get("project_key") or "demo"
    query = {}
    if args.get("severity"):
        query["severity"] = str(args["severity"]).upper()
    if args.get("type"):
        query["type"] = str(args["type"]).upper()
    return _get("/projects/%s/issues" % urllib.parse.quote(project, safe=""),
                query)


def get_measures(args):
    """Tool: project-level quality measures (coverage, bugs, ...)."""
    project = args.get("project_key") or "demo"
    return _get("/projects/%s/measures" % urllib.parse.quote(project, safe=""))


TOOLS = [
    Tool(
        "list_issues",
        "List static-analysis issues of a project on the quality platform. "
        "Each issue has rule, severity (BLOCKER|CRITICAL|MAJOR|MINOR|INFO), "
        "type (BUG|VULNERABILITY|CODE_SMELL), component (file path), line, "
        "message, effort_min and tags.",
        {
            "type": "object",
            "properties": {
                "project_key": {
                    "type": "string",
                    "description": "Project key on the quality platform "
                                   "(default: demo).",
                },
                "severity": {
                    "type": "string",
                    "enum": ["BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"],
                    "description": "Only issues of this severity.",
                },
                "type": {
                    "type": "string",
                    "enum": ["BUG", "VULNERABILITY", "CODE_SMELL"],
                    "description": "Only issues of this type.",
                },
            },
        },
        list_issues,
    ),
    Tool(
        "get_measures",
        "Get project-level quality measures: coverage, "
        "duplicated_lines_density, code_smells, bugs, vulnerabilities, "
        "sqale_index (tech-debt minutes).",
        {
            "type": "object",
            "properties": {
                "project_key": {
                    "type": "string",
                    "description": "Project key on the quality platform "
                                   "(default: demo).",
                },
            },
        },
        get_measures,
    ),
]


if __name__ == "__main__":
    serve(name="quality-mcp", version="0.1.0", tools=TOOLS)
