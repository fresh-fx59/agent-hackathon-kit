#!/usr/bin/env python3
"""Tests for the TMS mock (mocks/tms/app.py).

Boots the app in-process on an EPHEMERAL port (port=0 -- never the fixed
8801-8804) and exercises the HTTP API with urllib.  Also asserts the seed
is byte-identical to cases/testing-regression/testcase-map.json.

Run standalone:  python3 mocks/tests/test_tms.py
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
REPO_DIR = os.path.dirname(MOCKS_DIR)
CASE_MAP_PATH = os.path.join(REPO_DIR, "cases", "testing-regression",
                             "testcase-map.json")
sys.path.insert(0, MOCKS_DIR)  # for common.py

import common  # noqa: E402

USER_AGENT = "agent-hackathon-kit/0.1"


def _load_tms_module():
    """Import mocks/tms/app.py under a unique module name."""
    path = os.path.join(MOCKS_DIR, "tms", "app.py")
    spec = importlib.util.spec_from_file_location("tms_app_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tms_app = _load_tms_module()


class TmsMockTest(unittest.TestCase):

    def setUp(self):
        app, self.store = tms_app.make_app()   # fresh store per test
        self.server = common.serve(app, port=0)  # ephemeral port
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

    def test_seed_matches_testcase_map(self):
        """The mock seed must mirror the case's testcase-map.json exactly."""
        _status, cases = self.http("GET", "/testcases")
        with open(CASE_MAP_PATH, encoding="utf-8") as fh:
            expected = json.load(fh)
        self.assertEqual(cases, expected,
                         "mocks/tms/seed.json drifted from testcase-map.json")

    def test_seed_shape(self):
        _status, cases = self.http("GET", "/testcases")
        self.assertEqual(len(cases), 25)
        for case in cases:
            self.assertRegex(case["id"], r"^TC-\d+$")
            self.assertIn(case["priority"], ("P1", "P2", "P3"))
            self.assertIn(case["last_result"], ("passed", "failed", "blocked"))
            self.assertIsInstance(case["files_covered"], list)
            self.assertTrue(case["files_covered"])
            self.assertIsInstance(case["avg_duration_min"], int)

    def test_filter_by_area(self):
        _status, cases = self.http("GET", "/testcases?area=cart")
        self.assertEqual([c["id"] for c in cases],
                         ["TC-3", "TC-4", "TC-5", "TC-6", "TC-7"])
        _status, none = self.http("GET", "/testcases?area=nope")
        self.assertEqual(none, [])

    # -- create run -------------------------------------------------------

    def test_create_run(self):
        status, run = self.http("POST", "/runs", {
            "case_ids": ["TC-3", "TC-8"],
            "reason": "MR !1: изменены cart.py и checkout.py",
        })
        self.assertEqual(status, 201)
        self.assertEqual(run["run_id"], "RUN-1")
        self.assertEqual([c["id"] for c in run["cases"]], ["TC-3", "TC-8"])
        self.assertEqual(run["reason"], "MR !1: изменены cart.py и checkout.py")
        self.assertEqual(run["status"], "created")
        self.assertTrue(run["created_at"])

    def test_run_ids_increment(self):
        _s1, first = self.http("POST", "/runs", {"case_ids": ["TC-1"]})
        _s2, second = self.http("POST", "/runs", {"case_ids": ["TC-2"]})
        self.assertEqual(first["run_id"], "RUN-1")
        self.assertEqual(second["run_id"], "RUN-2")

    def test_create_run_dedupes_preserving_order(self):
        _status, run = self.http("POST", "/runs",
                                 {"case_ids": ["TC-8", "TC-3", "TC-8"]})
        self.assertEqual([c["id"] for c in run["cases"]], ["TC-8", "TC-3"])

    def test_create_run_rejects_empty(self):
        for bad in ({}, {"case_ids": []}, {"case_ids": "TC-1"},
                    {"case_ids": [1, 2]}):
            status, _body = self.http("POST", "/runs", bad)
            self.assertEqual(status, 400)

    def test_create_run_rejects_unknown_ids(self):
        status, body = self.http("POST", "/runs",
                                 {"case_ids": ["TC-1", "TC-999"]})
        self.assertEqual(status, 400)
        self.assertIn("TC-999", body["error"])


if __name__ == "__main__":
    unittest.main()
