#!/usr/bin/env python3
"""Mock corporate tracker (Jira-like issue tracker).

API (all JSON):
    GET   /health                 -> {"ok": true}
    GET   /issues?type=&status=   -> [issue, ...]   (both filters optional)
    GET   /issues/{id}            -> issue
    POST  /issues                 -> 201 + created issue (id "TRK-<n>", status "open")
    PATCH /issues/{id}            -> updated issue

Issue shape:
    {id, title, description, type: "BR"|"task"|"bug", priority: "P1".."P3",
     status, acceptance_criteria: [str], links: [str], meeting_ref: str,
     created_at, updated_at}

Run from any cwd:  python3 mocks/tracker/app.py
Listens on 127.0.0.1:8801 (override with TRACKER_PORT).
"""

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # mocks/ -- for common.py

from common import App, HttpError, Store, run  # noqa: E402

DEFAULT_PORT = 8801
SEED_PATH = os.path.join(HERE, "seed.json")

ISSUE_TYPES = ("BR", "task", "bug")
PRIORITIES = ("P1", "P2", "P3")
STATUSES = ("open", "in_progress", "resolved", "closed")

# Fields a client may set on create / change via PATCH.
MUTABLE_FIELDS = ("title", "description", "type", "priority", "status",
                  "acceptance_criteria", "links", "meeting_ref")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_enum(name, value, allowed):
    if value not in allowed:
        raise HttpError(400, "invalid %s: %r (allowed: %s)"
                        % (name, value, ", ".join(allowed)))


def _check_str_list(name, value):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise HttpError(400, "%s must be a list of strings" % name)
    return value


def make_app(seed_path=SEED_PATH):
    """Build the tracker App around a fresh Store; returns (app, store)."""
    store = Store(seed_path)
    issues = store.data.setdefault("issues", [])
    # Keep the id counter ahead of every seeded id ("TRK-3" -> 3).
    for issue in issues:
        tail = str(issue.get("id", "")).rpartition("-")[2]
        if tail.isdigit():
            store.bump_counter("issue", int(tail))

    app = App("tracker")

    def find(issue_id):
        for issue in issues:
            if issue["id"] == issue_id:
                return issue
        raise HttpError(404, "no such issue: %s" % issue_id)

    def list_issues(params, query, body):
        result = issues
        if query.get("type"):
            result = [i for i in result if i.get("type") == query["type"]]
        if query.get("status"):
            result = [i for i in result if i.get("status") == query["status"]]
        return result

    def get_issue(params, query, body):
        return find(params["id"])

    def create_issue(params, query, body):
        if not isinstance(body, dict):
            raise HttpError(400, "JSON object body required")
        title = str(body.get("title") or "").strip()
        if not title:
            raise HttpError(400, "title is required")
        issue_type = body.get("type", "task")
        _check_enum("type", issue_type, ISSUE_TYPES)
        priority = body.get("priority", "P2")
        _check_enum("priority", priority, PRIORITIES)
        now = _now()
        issue = {
            "id": "TRK-%d" % store.next_id("issue"),
            "title": title,
            "description": str(body.get("description") or ""),
            "type": issue_type,
            "priority": priority,
            "status": "open",
            "acceptance_criteria": _check_str_list(
                "acceptance_criteria", body.get("acceptance_criteria", [])),
            "links": _check_str_list("links", body.get("links", [])),
            "meeting_ref": str(body.get("meeting_ref") or ""),
            "created_at": now,
            "updated_at": now,
        }
        issues.append(issue)
        return (201, issue)

    def update_issue(params, query, body):
        issue = find(params["id"])
        if not isinstance(body, dict) or not body:
            raise HttpError(400, "JSON object body with at least one field required")
        unknown = [k for k in body if k not in MUTABLE_FIELDS]
        if unknown:
            raise HttpError(400, "unknown field(s): %s (allowed: %s)"
                            % (", ".join(sorted(unknown)), ", ".join(MUTABLE_FIELDS)))
        if "type" in body:
            _check_enum("type", body["type"], ISSUE_TYPES)
        if "priority" in body:
            _check_enum("priority", body["priority"], PRIORITIES)
        if "status" in body:
            _check_enum("status", body["status"], STATUSES)
        if "acceptance_criteria" in body:
            _check_str_list("acceptance_criteria", body["acceptance_criteria"])
        if "links" in body:
            _check_str_list("links", body["links"])
        if "title" in body and not str(body["title"]).strip():
            raise HttpError(400, "title must not be empty")
        issue.update(body)
        issue["updated_at"] = _now()
        return issue

    app.get("/issues", list_issues)
    app.post("/issues", create_issue)
    app.get("/issues/{id}", get_issue)
    app.patch("/issues/{id}", update_issue)
    return app, store


def main():
    app, _store = make_app()
    run(app, DEFAULT_PORT, port_env="TRACKER_PORT")


if __name__ == "__main__":
    main()
