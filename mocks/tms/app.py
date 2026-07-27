#!/usr/bin/env python3
"""Mock test-management system (TMS).

API (all JSON):
    GET  /health           -> {"ok": true}
    GET  /testcases?area=  -> [testcase, ...]  (area filter optional)
    POST /runs             -> 201 + created run
         body: {case_ids: ["TC-1", ...], reason}

Testcase shape:
    {id: "TC-<n>", title, area, priority: "P1".."P3", files_covered: [paths],
     tags: [], last_result: "passed"|"failed"|"blocked", avg_duration_min}

Seed: the SAME ~25 cases as cases/testing-regression/testcase-map.json
(generated from it; a test asserts they stay identical).

Run from any cwd:  python3 mocks/tms/app.py
Listens on 127.0.0.1:8804 (override with TMS_PORT).
"""

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # mocks/ -- for common.py

from common import App, HttpError, Store, run  # noqa: E402

DEFAULT_PORT = 8804
SEED_PATH = os.path.join(HERE, "seed.json")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_app(seed_path=SEED_PATH):
    """Build the TMS App around a fresh Store; returns (app, store)."""
    store = Store(seed_path)
    cases = store.data.setdefault("testcases", [])
    runs = store.data.setdefault("runs", [])

    app = App("tms")

    def list_testcases(params, query, body):
        result = cases
        if query.get("area"):
            result = [c for c in result if c.get("area") == query["area"]]
        return result

    def create_run(params, query, body):
        if not isinstance(body, dict):
            raise HttpError(400, "JSON object body required")
        case_ids = body.get("case_ids")
        if (not isinstance(case_ids, list) or not case_ids
                or any(not isinstance(x, str) for x in case_ids)):
            raise HttpError(400, "case_ids must be a non-empty list of "
                                 "testcase ids (e.g. [\"TC-1\"])")
        known = {c["id"]: c for c in cases}
        unknown = [i for i in case_ids if i not in known]
        if unknown:
            raise HttpError(400, "unknown testcase id(s): %s"
                            % ", ".join(sorted(set(unknown))))
        ordered = []  # dedupe, preserve order
        for case_id in case_ids:
            if case_id not in ordered:
                ordered.append(case_id)
        run_record = {
            "run_id": "RUN-%d" % store.next_id("run"),
            "reason": str(body.get("reason") or ""),
            "created_at": _now(),
            "status": "created",
            "cases": [known[i] for i in ordered],
        }
        runs.append(run_record)
        return (201, run_record)

    app.get("/testcases", list_testcases)
    app.post("/runs", create_run)
    return app, store


def main():
    app, _store = make_app()
    run(app, DEFAULT_PORT, port_env="TMS_PORT")


if __name__ == "__main__":
    main()
