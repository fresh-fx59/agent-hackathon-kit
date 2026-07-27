#!/usr/bin/env python3
"""Tests for the tracker mock (mocks/tracker/app.py).

Boots the app in-process on an EPHEMERAL port (port=0 -- never the fixed
8801-8804) and exercises the HTTP API with urllib.

Run standalone:  python3 mocks/tests/test_tracker.py
"""

import importlib.util
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MOCKS_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, MOCKS_DIR)  # for common.py

import common  # noqa: E402

USER_AGENT = "agent-hackathon-kit/0.1"


def _load_tracker_module():
    """Import mocks/tracker/app.py under a unique module name."""
    path = os.path.join(MOCKS_DIR, "tracker", "app.py")
    spec = importlib.util.spec_from_file_location("tracker_app_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tracker_app = _load_tracker_module()


class TrackerMockTest(unittest.TestCase):

    def setUp(self):
        app, self.store = tracker_app.make_app()  # fresh store per test
        self.server = common.serve(app, port=0)   # ephemeral port
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def http(self, method, path, body=None):
        """Return (status_code, parsed_json) for one request."""
        data = None
        headers = {"User-Agent": USER_AGENT}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            return exc.code, payload

    # -- health + seed ----------------------------------------------------

    def test_health(self):
        status, body = self.http("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_seed_has_three_issues(self):
        status, issues = self.http("GET", "/issues")
        self.assertEqual(status, 200)
        self.assertEqual([i["id"] for i in issues], ["TRK-1", "TRK-2", "TRK-3"])

    # -- list + filters ---------------------------------------------------

    def test_filter_by_type(self):
        _status, issues = self.http("GET", "/issues?type=BR")
        self.assertEqual([i["id"] for i in issues], ["TRK-1"])
        for issue in issues:
            self.assertEqual(issue["type"], "BR")

    def test_filter_by_status(self):
        _status, issues = self.http("GET", "/issues?status=in_progress")
        self.assertEqual([i["id"] for i in issues], ["TRK-2"])

    def test_filter_combined_no_match(self):
        _status, issues = self.http("GET", "/issues?type=BR&status=in_progress")
        self.assertEqual(issues, [])

    # -- get --------------------------------------------------------------

    def test_get_issue(self):
        status, issue = self.http("GET", "/issues/TRK-2")
        self.assertEqual(status, 200)
        self.assertEqual(issue["id"], "TRK-2")
        self.assertEqual(issue["type"], "bug")

    def test_get_unknown_issue_404(self):
        status, body = self.http("GET", "/issues/TRK-999")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # -- create -----------------------------------------------------------

    def test_create_issue_full_body(self):
        status, issue = self.http("POST", "/issues", {
            "title": "BR: заявки на командировки",
            "description": "Бизнес-требования по итогам встречи.",
            "type": "BR",
            "priority": "P1",
            "acceptance_criteria": ["Форма заявки доступна каждому сотруднику"],
            "links": ["TRK-1"],
            "meeting_ref": "meet-2026-07-27-trips",
        })
        self.assertEqual(status, 201)
        self.assertEqual(issue["id"], "TRK-4")  # counter continues after seed
        self.assertEqual(issue["status"], "open")
        self.assertEqual(issue["type"], "BR")
        self.assertEqual(issue["acceptance_criteria"],
                         ["Форма заявки доступна каждому сотруднику"])
        self.assertTrue(issue["created_at"])
        self.assertEqual(issue["created_at"], issue["updated_at"])
        # And it is visible in the listing.
        _s, issues = self.http("GET", "/issues")
        self.assertEqual(len(issues), 4)

    def test_create_defaults(self):
        status, issue = self.http("POST", "/issues", {"title": "minimal"})
        self.assertEqual(status, 201)
        self.assertEqual(issue["type"], "task")
        self.assertEqual(issue["priority"], "P2")
        self.assertEqual(issue["acceptance_criteria"], [])
        self.assertEqual(issue["links"], [])
        self.assertEqual(issue["meeting_ref"], "")

    def test_create_ids_increment(self):
        _s1, first = self.http("POST", "/issues", {"title": "a"})
        _s2, second = self.http("POST", "/issues", {"title": "b"})
        self.assertEqual(first["id"], "TRK-4")
        self.assertEqual(second["id"], "TRK-5")

    def test_create_requires_title(self):
        status, body = self.http("POST", "/issues", {"description": "no title"})
        self.assertEqual(status, 400)
        self.assertIn("title", body["error"])

    def test_create_rejects_bad_type(self):
        status, body = self.http("POST", "/issues",
                                 {"title": "x", "type": "epic"})
        self.assertEqual(status, 400)
        self.assertIn("type", body["error"])

    def test_create_rejects_bad_priority(self):
        status, _body = self.http("POST", "/issues",
                                  {"title": "x", "priority": "P9"})
        self.assertEqual(status, 400)

    def test_create_rejects_non_list_acceptance_criteria(self):
        status, _body = self.http("POST", "/issues",
                                  {"title": "x", "acceptance_criteria": "not a list"})
        self.assertEqual(status, 400)

    # -- update -----------------------------------------------------------

    def test_patch_status(self):
        status, issue = self.http("PATCH", "/issues/TRK-1",
                                  {"status": "resolved"})
        self.assertEqual(status, 200)
        self.assertEqual(issue["status"], "resolved")
        # Persisted:
        _s, again = self.http("GET", "/issues/TRK-1")
        self.assertEqual(again["status"], "resolved")

    def test_patch_multiple_fields(self):
        _status, issue = self.http("PATCH", "/issues/TRK-3", {
            "priority": "P1",
            "description": "Updated description",
            "acceptance_criteria": ["Справочник совпадает с кадровой системой"],
        })
        self.assertEqual(issue["priority"], "P1")
        self.assertEqual(issue["description"], "Updated description")
        self.assertEqual(issue["acceptance_criteria"],
                         ["Справочник совпадает с кадровой системой"])

    def test_patch_unknown_issue_404(self):
        status, _body = self.http("PATCH", "/issues/TRK-999", {"status": "open"})
        self.assertEqual(status, 404)

    def test_patch_rejects_unknown_field(self):
        status, body = self.http("PATCH", "/issues/TRK-1", {"assignee": "x"})
        self.assertEqual(status, 400)
        self.assertIn("assignee", body["error"])

    def test_patch_rejects_bad_status(self):
        status, _body = self.http("PATCH", "/issues/TRK-1", {"status": "banana"})
        self.assertEqual(status, 400)

    def test_patch_rejects_empty_body(self):
        status, _body = self.http("PATCH", "/issues/TRK-1", {})
        self.assertEqual(status, 400)

    def test_patch_rejects_id_change(self):
        status, _body = self.http("PATCH", "/issues/TRK-1", {"id": "TRK-777"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
