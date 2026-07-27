#!/usr/bin/env python3
"""Tests for the forge mock (mocks/forge/app.py).

Boots the app in-process on an EPHEMERAL port (port=0 -- never the fixed
8801-8804) and exercises the HTTP API with urllib.  Also asserts that the
seed stays consistent with cases/testing-regression/ (diff.patch and app/).

Run standalone:  python3 mocks/tests/test_forge.py
"""

import importlib.util
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MOCKS_DIR = os.path.dirname(TESTS_DIR)
REPO_DIR = os.path.dirname(MOCKS_DIR)
CASE_DIR = os.path.join(REPO_DIR, "cases", "testing-regression")
sys.path.insert(0, MOCKS_DIR)  # for common.py

import common  # noqa: E402

USER_AGENT = "agent-hackathon-kit/0.1"


def _load_forge_module():
    """Import mocks/forge/app.py under a unique module name."""
    path = os.path.join(MOCKS_DIR, "forge", "app.py")
    spec = importlib.util.spec_from_file_location("forge_app_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forge_app = _load_forge_module()


class ForgeMockTest(unittest.TestCase):

    def setUp(self):
        app, self.store = forge_app.make_app()  # fresh store per test
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

    def test_seed_mr_summary(self):
        status, mr = self.http("GET", "/projects/1/merge_requests/1")
        self.assertEqual(status, 200)
        self.assertEqual(mr["iid"], 1)
        self.assertEqual(mr["target_branch"], "main")
        self.assertEqual(mr["changes_count"], 2)
        self.assertNotIn("changes", mr)  # summary must not embed the diffs

    def test_seed_changes_mirror_diff_patch(self):
        _status, payload = self.http("GET", "/projects/1/merge_requests/1/changes")
        changes = payload["changes"]
        self.assertEqual([c["new_path"] for c in changes],
                         ["app/cart.py", "app/checkout.py"])
        with open(os.path.join(CASE_DIR, "diff.patch"), encoding="utf-8") as fh:
            patch = fh.read()
        for change in changes:
            self.assertEqual(change["old_path"], change["new_path"])
            # Every per-file diff is literally a chunk of diff.patch.
            self.assertIn(change["diff"], patch)
            self.assertIn("--- a/%s" % change["old_path"], change["diff"])
            self.assertIn("+++ b/%s" % change["new_path"], change["diff"])

    def test_seed_files_match_case_app_sources(self):
        """repository/files must serve the exact on-disk app/ sources."""
        for name in ("cart.py", "checkout.py", "catalog.py", "auth.py",
                     "notifications.py", "models.py"):
            rel = "app/" + name
            _status, payload = self.http(
                "GET", "/projects/1/repository/files?path="
                + urllib.parse.quote(rel, safe=""))
            with open(os.path.join(CASE_DIR, rel), encoding="utf-8") as fh:
                self.assertEqual(payload["content"], fh.read(),
                                 "seed drifted from %s -- regenerate seed.json" % rel)
            self.assertEqual(payload["path"], rel)

    # -- error paths ------------------------------------------------------

    def test_unknown_project_404(self):
        status, body = self.http("GET", "/projects/42/merge_requests/1")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_unknown_mr_404(self):
        status, _body = self.http("GET", "/projects/1/merge_requests/99")
        self.assertEqual(status, 404)
        status, _body = self.http("GET", "/projects/1/merge_requests/99/changes")
        self.assertEqual(status, 404)

    def test_file_requires_path(self):
        status, body = self.http("GET", "/projects/1/repository/files")
        self.assertEqual(status, 400)
        self.assertIn("path", body["error"])

    def test_unknown_file_404(self):
        status, _body = self.http("GET",
                                  "/projects/1/repository/files?path=app/nope.py")
        self.assertEqual(status, 404)

    # -- create MR --------------------------------------------------------

    def test_create_mr(self):
        status, mr = self.http("POST", "/projects/1/merge_requests", {
            "source_branch": "fix/cart-stock-check",
            "target_branch": "main",
            "title": "fix(cart): stock check on add",
            "description": "Adds the stock validation.",
        })
        self.assertEqual(status, 201)
        self.assertEqual(mr["iid"], 2)  # counter continues after seed
        self.assertEqual(mr["changes_count"], 0)
        # And it is retrievable afterwards.
        _s, again = self.http("GET", "/projects/1/merge_requests/2")
        self.assertEqual(again["title"], "fix(cart): stock check on add")

    def test_create_mr_iids_increment(self):
        _s1, first = self.http("POST", "/projects/1/merge_requests",
                               {"source_branch": "a", "target_branch": "main",
                                "title": "one"})
        _s2, second = self.http("POST", "/projects/1/merge_requests",
                                {"source_branch": "b", "target_branch": "main",
                                 "title": "two"})
        self.assertEqual(first["iid"], 2)
        self.assertEqual(second["iid"], 3)

    def test_create_mr_requires_fields(self):
        for missing in ({"target_branch": "main", "title": "t"},
                        {"source_branch": "s", "title": "t"},
                        {"source_branch": "s", "target_branch": "main"}):
            status, body = self.http("POST", "/projects/1/merge_requests",
                                     missing)
            self.assertEqual(status, 400, body)

    def test_create_mr_unknown_project_404(self):
        status, _body = self.http("POST", "/projects/42/merge_requests",
                                  {"source_branch": "s", "target_branch": "t",
                                   "title": "x"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
