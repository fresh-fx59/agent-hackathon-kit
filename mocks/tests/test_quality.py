#!/usr/bin/env python3
"""Tests for the quality mock (mocks/quality/app.py).

Boots the app on an EPHEMERAL port (never the fixed 8802) via common.serve
and talks real HTTP to it.

Run standalone:  python3 mocks/tests/test_quality.py
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MOCKS_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(MOCKS_DIR)
sys.path.insert(0, MOCKS_DIR)                          # import common
sys.path.insert(0, os.path.join(MOCKS_DIR, "quality"))  # import app

import common  # noqa: E402
import app as quality_app  # noqa: E402

HEADERS = {"User-Agent": "agent-hackathon-kit/0.1"}


class QualityMockTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        store = common.Store(quality_app.SEED_PATH)
        cls.server = common.serve(quality_app.build_app(store), port=0)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get_json(self, path):
        req = urllib.request.Request(self.base + path, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)

    def get_error(self, path):
        try:
            self.get_json(path)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        self.fail("expected an HTTP error for %s" % path)

    # -- basics -----------------------------------------------------------

    def test_health(self):
        status, body = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_issues_shape_and_count(self):
        status, issues = self.get_json("/projects/demo/issues")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(issues), 12)
        for issue in issues:
            for field in ("rule", "severity", "type", "component", "line",
                          "message", "effort_min", "tags"):
                self.assertIn(field, issue)
            self.assertIn(issue["severity"], quality_app.SEVERITIES)
            self.assertIn(issue["type"], quality_app.TYPES)
            self.assertIsInstance(issue["line"], int)
            self.assertIsInstance(issue["tags"], list)

    # -- filters ----------------------------------------------------------

    def test_filter_by_severity(self):
        _status, issues = self.get_json("/projects/demo/issues?severity=BLOCKER")
        self.assertGreaterEqual(len(issues), 1)
        self.assertTrue(all(i["severity"] == "BLOCKER" for i in issues))

    def test_filter_by_severity_is_case_insensitive(self):
        _status, upper = self.get_json("/projects/demo/issues?severity=INFO")
        _status, lower = self.get_json("/projects/demo/issues?severity=info")
        self.assertEqual(upper, lower)
        self.assertGreaterEqual(len(upper), 1)

    def test_filter_by_type(self):
        _status, issues = self.get_json("/projects/demo/issues?type=VULNERABILITY")
        self.assertGreaterEqual(len(issues), 2)
        self.assertTrue(all(i["type"] == "VULNERABILITY" for i in issues))

    def test_filter_by_severity_and_type(self):
        _status, issues = self.get_json(
            "/projects/demo/issues?severity=MAJOR&type=BUG")
        self.assertGreaterEqual(len(issues), 1)
        for issue in issues:
            self.assertEqual(issue["severity"], "MAJOR")
            self.assertEqual(issue["type"], "BUG")

    def test_invalid_severity_is_400(self):
        code, body = self.get_error("/projects/demo/issues?severity=HUGE")
        self.assertEqual(code, 400)
        self.assertIn("severity", body["error"])

    def test_invalid_type_is_400(self):
        code, body = self.get_error("/projects/demo/issues?type=FEATURE")
        self.assertEqual(code, 400)
        self.assertIn("type", body["error"])

    # -- measures + errors -------------------------------------------------

    def test_measures(self):
        status, measures = self.get_json("/projects/demo/measures")
        self.assertEqual(status, 200)
        for field in ("coverage", "duplicated_lines_density", "code_smells",
                      "bugs", "vulnerabilities", "sqale_index"):
            self.assertIn(field, measures)
        self.assertGreater(measures["sqale_index"], 0)

    def test_unknown_project_is_404(self):
        code, body = self.get_error("/projects/nope/issues")
        self.assertEqual(code, 404)
        self.assertIn("nope", body["error"])
        code, _body = self.get_error("/projects/nope/measures")
        self.assertEqual(code, 404)

    # -- seed consistency with the dev-techdebt case ----------------------

    def test_seed_components_point_at_real_files(self):
        _status, issues = self.get_json("/projects/demo/issues")
        for issue in issues:
            path = os.path.join(REPO_ROOT, issue["component"])
            self.assertTrue(os.path.isfile(path),
                            "seed component does not exist: %s" % issue["component"])


if __name__ == "__main__":
    unittest.main()
