#!/usr/bin/env python3
"""quality mock -- a SonarQube-like static analysis platform (port 8802).

Endpoints (all JSON):
    GET /health                          -> {"ok": true}
    GET /projects/{key}/issues           -> list of issues; filters:
                                            ?severity=BLOCKER|CRITICAL|MAJOR|MINOR|INFO
                                            ?type=BUG|VULNERABILITY|CODE_SMELL
    GET /projects/{key}/measures         -> project-level quality measures

Seed project "demo": issues whose component/line point at the real seeded
tech debt in cases/dev-techdebt/src/.

Run from anywhere:  python3 mocks/quality/app.py   (QUALITY_PORT overrides)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # mocks/ -> `import common`

import common  # noqa: E402
from common import App, HttpError, Store  # noqa: E402

SEED_PATH = os.path.join(HERE, "seed.json")
PORT = int(os.environ.get("QUALITY_PORT", 8802))

SEVERITIES = ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO")
TYPES = ("BUG", "VULNERABILITY", "CODE_SMELL")


def build_app(store):
    """Wire the route table around one Store instance."""
    app = App("quality")

    def project_or_404(key):
        project = (store.data.get("projects") or {}).get(key)
        if project is None:
            raise HttpError(404, "unknown project: %s" % key)
        return project

    def list_issues(params, query, body):
        project = project_or_404(params["key"])
        issues = project.get("issues", [])
        severity = query.get("severity")
        if severity:
            severity = severity.upper()
            if severity not in SEVERITIES:
                raise HttpError(400, "invalid severity %r (expected one of %s)"
                                % (severity, ", ".join(SEVERITIES)))
            issues = [i for i in issues if i.get("severity") == severity]
        issue_type = query.get("type")
        if issue_type:
            issue_type = issue_type.upper()
            if issue_type not in TYPES:
                raise HttpError(400, "invalid type %r (expected one of %s)"
                                % (issue_type, ", ".join(TYPES)))
            issues = [i for i in issues if i.get("type") == issue_type]
        return issues

    def get_measures(params, query, body):
        project = project_or_404(params["key"])
        return project.get("measures", {})

    app.get("/projects/{key}/issues", list_issues)
    app.get("/projects/{key}/measures", get_measures)
    return app


def main():
    store = Store(SEED_PATH)
    common.run(build_app(store), PORT)


if __name__ == "__main__":
    main()
