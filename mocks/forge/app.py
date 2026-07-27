#!/usr/bin/env python3
"""Mock source forge (GitLab-like merge requests + repository files).

API (all JSON):
    GET  /health                                   -> {"ok": true}
    GET  /projects/{id}/merge_requests/{iid}       -> MR summary
    GET  /projects/{id}/merge_requests/{iid}/changes
                                                   -> {"changes": [{old_path, new_path, diff}]}
    POST /projects/{id}/merge_requests             -> 201 + created MR
         body: {source_branch, target_branch, title, description}
    GET  /projects/{id}/repository/files?path=     -> {"path": ..., "content": ...}

MR summary shape:
    {iid, title, description, source_branch, target_branch, changes_count}

Seed: project "1" with MR !1 whose changes mirror
cases/testing-regression/diff.patch, plus the app/ file contents.

Run from any cwd:  python3 mocks/forge/app.py
Listens on 127.0.0.1:8803 (override with FORGE_PORT).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # mocks/ -- for common.py

from common import App, HttpError, Store, run  # noqa: E402

DEFAULT_PORT = 8803
SEED_PATH = os.path.join(HERE, "seed.json")

MR_SUMMARY_FIELDS = ("iid", "title", "description", "source_branch",
                     "target_branch", "changes_count")


def make_app(seed_path=SEED_PATH):
    """Build the forge App around a fresh Store; returns (app, store)."""
    store = Store(seed_path)
    projects = store.data.setdefault("projects", {})
    # Keep every project's MR counter ahead of its seeded iids.
    for project_id, project in projects.items():
        for mr in project.get("merge_requests", []):
            store.bump_counter("mr:%s" % project_id, int(mr["iid"]))

    app = App("forge")

    def find_project(project_id):
        project = projects.get(project_id)
        if project is None:
            raise HttpError(404, "no such project: %s" % project_id)
        return project

    def find_mr(project, iid):
        for mr in project.get("merge_requests", []):
            if str(mr["iid"]) == str(iid):
                return mr
        raise HttpError(404, "no such merge request: !%s" % iid)

    def summary(mr):
        return {k: mr.get(k) for k in MR_SUMMARY_FIELDS}

    def get_mr(params, query, body):
        return summary(find_mr(find_project(params["id"]), params["iid"]))

    def get_mr_changes(params, query, body):
        mr = find_mr(find_project(params["id"]), params["iid"])
        return {"changes": mr.get("changes", [])}

    def create_mr(params, query, body):
        project = find_project(params["id"])
        if not isinstance(body, dict):
            raise HttpError(400, "JSON object body required")
        title = str(body.get("title") or "").strip()
        source = str(body.get("source_branch") or "").strip()
        target = str(body.get("target_branch") or "").strip()
        if not title:
            raise HttpError(400, "title is required")
        if not source:
            raise HttpError(400, "source_branch is required")
        if not target:
            raise HttpError(400, "target_branch is required")
        mr = {
            "iid": store.next_id("mr:%s" % params["id"]),
            "title": title,
            "description": str(body.get("description") or ""),
            "source_branch": source,
            "target_branch": target,
            "changes_count": 0,
            "changes": [],
        }
        project.setdefault("merge_requests", []).append(mr)
        return (201, summary(mr))

    def get_file(params, query, body):
        project = find_project(params["id"])
        path = query.get("path", "")
        if not path:
            raise HttpError(400, "query parameter 'path' is required")
        files = project.get("files", {})
        if path not in files:
            raise HttpError(404, "no such file in project %s: %s"
                            % (params["id"], path))
        return {"path": path, "content": files[path]}

    app.get("/projects/{id}/merge_requests/{iid}", get_mr)
    app.get("/projects/{id}/merge_requests/{iid}/changes", get_mr_changes)
    app.post("/projects/{id}/merge_requests", create_mr)
    app.get("/projects/{id}/repository/files", get_file)
    return app, store


def main():
    app, _store = make_app()
    run(app, DEFAULT_PORT, port_env="FORGE_PORT")


if __name__ == "__main__":
    main()
