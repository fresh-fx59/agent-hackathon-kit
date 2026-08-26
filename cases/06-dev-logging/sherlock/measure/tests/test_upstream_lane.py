#!/usr/bin/env python3
"""Tests for upstream-lane.sh — the one place that puts the proxy in the path.

Two runners need the same three things in front of the provider, and having them
in only one of the two is how `run-bench.sh` kept sending the prefixed model id
long after `run-case.sh` stopped:

  1. attribution — every request logged with what was asked for and what answered
  2. the model-id split — the CLI gets the clean id (1,000,000-token table match),
     the provider gets its routing alias (`[SP]…`)
  3. a fallback that cannot make things worse — if the proxy does not come up the
     caller keeps the direct URL AND the aliased id, because with no proxy in the
     path nothing can restore the prefix and a stripped id would 404.

These tests drive the helper the way a runner does: source it, call it, then make
a real request through whatever it handed back, to a real stub provider.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
LANE = os.path.join(MEASURE, "upstream-lane.sh")


class StubProvider:
    """Records the bodies it is sent; answers like an OpenAI-compatible API."""

    def __init__(self, fail_times=0):
        self.seen = []
        seen = self.seen
        state = {"fail": fail_times}
        self.state = state

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                if state["fail"] > 0:
                    state["fail"] -= 1
                    out = b'{"error":{"message":"Upstream request failed"}}'
                    self.send_response(400)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n)
                try:
                    seen.append(json.loads(raw))
                except ValueError:
                    seen.append({"unparseable": True})
                out = json.dumps({"model": "DeepSeek-V4-Flash",
                                  "choices": [{"message": {"content": "ok"}}]}
                                 ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# Sources the helper, starts a lane, POSTs one request through it, and prints the
# three values a runner cares about. Exactly how a runner uses it.
DRIVER = r"""
set -uo pipefail
. "$LANE"
LANE_PROXY_PID=""
trap '[ -n "${LANE_PROXY_PID:-}" ] && kill "$LANE_PROXY_PID" 2>/dev/null' EXIT
if ! upstream_lane_start "$UP_BASE" "$LOG_PATH" "run-tag" "$THE_MODEL" \
    "${INFLIGHT_PATH:-}" "${ATTEMPT_PATH:-}"; then
  echo "LANE_FAILED=1"
  exit 9
fi
echo "BASE=$LANE_BASE_URL"
echo "CLIENT_MODEL=$LANE_CLIENT_MODEL"
export LANE_BASE_URL LANE_CLIENT_MODEL
python3 -c '
import json, os, sys, urllib.request
url = os.environ["LANE_BASE_URL"].rstrip("/") + "/chat/completions"
body = json.dumps({"model": os.environ["LANE_CLIENT_MODEL"], "messages": []}).encode()
req = urllib.request.Request(url, data=body,
                             headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10).read()
except Exception as e:
    print("POST-FAILED", e, file=sys.stderr)
'
"""


class TheLanePutsTheProxyInThePath(unittest.TestCase):
    def drive(self, model, env=None, fail_times=0):
        prov = StubProvider(fail_times=fail_times)
        self.addCleanup(prov.close)
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        log = os.path.join(d, "upstream.jsonl")
        e = dict(os.environ)
        e.update({"LANE": LANE, "UP_BASE": prov.url, "LOG_PATH": log,
                  "THE_MODEL": model})
        e.update(env or {})
        p = subprocess.run(["bash", "-c", DRIVER], capture_output=True,
                           text=True, env=e, timeout=90)
        out = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
        rows = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        return out, prov.seen, rows, p

    def test_the_client_is_pointed_at_a_local_port(self):
        out, _seen, _rows, p = self.drive("[SP]deepseek-v4-flash")
        self.assertTrue(out.get("BASE", "").startswith("http://127.0.0.1:"),
                        "BASE was %r (stderr: %s)" % (out.get("BASE"), p.stderr))

    def test_the_client_id_loses_the_routing_prefix(self):
        out, _seen, _rows, _p = self.drive("[SP]deepseek-v4-flash")
        self.assertEqual(out.get("CLIENT_MODEL"), "deepseek-v4-flash")

    def test_the_provider_is_sent_the_aliased_id(self):
        _out, seen, _rows, _p = self.drive("[SP]deepseek-v4-flash")
        self.assertEqual([r.get("model") for r in seen], ["[SP]deepseek-v4-flash"])

    def test_the_request_is_logged_with_both_names(self):
        _out, _seen, rows, _p = self.drive("[SP]deepseek-v4-flash")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["requested_model"], "deepseek-v4-flash")
        self.assertEqual(rows[0]["sent_model"], "[SP]deepseek-v4-flash")
        self.assertEqual(rows[0]["returned_model"], "DeepSeek-V4-Flash")

    def test_an_unprefixed_model_is_left_alone(self):
        # SHERLOCK_SUBSTITUTION_RETRY=0 because this stub always answers as
        # `DeepSeek-V4-Flash` whatever it was asked for, so an unprefixed
        # `qwen3-coder-plus` lane sees a substitution on every call and the
        # lane default would — correctly — re-issue it twice. This test is
        # about the ID REWRITE, so the retry is switched off to keep the call
        # count at one. The retry gets its own test below, and the whole
        # mechanism is covered in measure/tests/test_substitution_retry.py.
        out, seen, _rows, _p = self.drive("qwen3-coder-plus",
                                          {"SHERLOCK_SUBSTITUTION_RETRY": "0"})
        self.assertEqual(out.get("CLIENT_MODEL"), "qwen3-coder-plus")
        self.assertEqual([r.get("model") for r in seen], ["qwen3-coder-plus"])

    def test_the_lane_re_asks_a_substituted_model_by_default(self):
        """The lane must ship the substitution retry ON.

        A paid run sets SHERLOCK_UPSTREAM_RETRY=0 — provider ERROR retries off,
        deliberately. If the substitution retry rode on that switch it would be
        off exactly where it is needed, and one wrong-model answer would kill
        the run. So the lane defaults it ON, and this stub — which answers as a
        different family every single time — proves it: one client call, cap+1
        upstream calls, then fail closed.

        The expected count is DERIVED from the lane's own default, never
        hardcoded. It was hardcoded to 3, and when the default moved 2 -> 12 in
        `fefcc8e` this test broke and the breakage shipped, because the commit
        was merged on the retry arithmetic alone without running this suite.
        A test that pins a constant it does not read is a test that dies the
        first time the constant is tuned.
        """
        cap = self.lane_default_substitution_retry()
        _out, seen, rows, _p = self.drive("qwen3-coder-plus",
                                          {"SHERLOCK_UPSTREAM_RETRY": "0"})
        self.assertGreaterEqual(cap, 1, "the lane must ship the retry ON")
        self.assertEqual(len(seen), cap + 1,
                         "the lane did not re-ask cap+1 times: %r" % (len(seen),))
        self.assertEqual([r.get("discarded_substitution") for r in rows],
                         [True] * (cap + 1))
        self.assertEqual([r.get("substitution_retry_exhausted") for r in rows],
                         [False] * cap + [True])

    def lane_default_substitution_retry(self):
        """Read the cap out of upstream-lane.sh so the two cannot drift."""
        lane = LANE
        with open(lane, encoding="utf-8") as fh:
            m = re.search(r'SHERLOCK_SUBSTITUTION_RETRY:-(\d+)', fh.read())
        self.assertIsNotNone(m, "upstream-lane.sh no longer defaults the cap")
        return int(m.group(1))

    def test_when_the_proxy_cannot_start_the_caller_keeps_the_alias(self):
        """No proxy means nothing can restore the prefix — a stripped id would 404."""
        out, _seen, _rows, p = self.drive(
            "[SP]deepseek-v4-flash",
            {"UPSTREAM_LANE_PROXY": "/nonexistent/upstream-log-proxy.py"})
        self.assertEqual(out.get("CLIENT_MODEL"), "[SP]deepseek-v4-flash")
        self.assertTrue(out.get("BASE", "").startswith("http://127.0.0.1:"),
                        "expected the DIRECT provider url, got %r" % out.get("BASE"))
        self.assertNotIn("/v1/v1", out.get("BASE", ""))
        self.assertIn("upstream lane", p.stderr,
                      "a silently missing lane is an unattributed run")

    def test_it_rides_out_a_provider_burst(self):
        """The client's retry budget is shorter than a linkapi burst.

        D11 died after 5 consecutive 400s at 171 KB — 98,515 tokens billed, no
        row — while the same request succeeded on the 5th try at 143 KB. The
        lane is the only place that can wait longer than qwen-code will.
        """
        # real backoff is 2s,4s,8s… — deliberately longer than a burst. Shrink
        # it here so the test measures the MECHANISM, not the wall clock.
        out, seen, rows, p = self.drive(
            "[SP]deepseek-v4-flash", {"SHERLOCK_UPSTREAM_RETRY_BASE_MS": "20"},
            fail_times=3)
        self.assertEqual(len(seen), 1, "the retried request never landed")
        statuses = [r["status"] for r in rows]
        self.assertEqual(statuses, [400, 400, 400, 200],
                         "expected 3 recorded failures then a success: %r" % rows)

    def test_it_can_be_switched_off_entirely(self):
        out, seen, rows, _p = self.drive("[SP]deepseek-v4-flash",
                                         {"SHERLOCK_UPSTREAM_LOG": "0"})
        self.assertEqual(out.get("CLIENT_MODEL"), "[SP]deepseek-v4-flash")
        self.assertEqual(rows, [])
        self.assertEqual([r.get("model") for r in seen], ["[SP]deepseek-v4-flash"])

    def test_strict_attribution_refuses_disabled_logging_before_provider_use(self):
        """A controlled paid run must not silently take the direct fallback."""
        out, seen, rows, p = self.drive(
            "[SP]deepseek-v4-flash",
            {"SHERLOCK_REQUIRE_ATTRIBUTION": "1", "SHERLOCK_UPSTREAM_LOG": "0"})
        self.assertEqual(p.returncode, 9)
        self.assertEqual(out.get("LANE_FAILED"), "1")
        self.assertEqual(seen, [])
        self.assertEqual(rows, [])

    def test_strict_attribution_refuses_a_missing_proxy_before_provider_use(self):
        """Missing local attribution is RUN_FAILED, never direct paid traffic."""
        out, seen, _rows, p = self.drive(
            "[SP]deepseek-v4-flash",
            {"SHERLOCK_REQUIRE_ATTRIBUTION": "1",
             "UPSTREAM_LANE_PROXY": "/nonexistent/upstream-log-proxy.py"})
        self.assertEqual(p.returncode, 9)
        self.assertEqual(out.get("LANE_FAILED"), "1")
        self.assertEqual(seen, [])

    def test_strict_lane_forwards_all_four_caps_only_to_the_proxy(self):
        """A retry must be stopped by the proxy's atomic one-attempt reservation."""
        trace = tempfile.mkdtemp()
        with open(os.path.join(trace, "upstream-budget-state.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "run_tag": "run-tag", "updated_at": "fixture",
                       "attempts_charged": 0, "request_bytes": 0,
                       "consecutive_provider_failures": 0,
                       "limits": {"max_upstream_attempts": 1,
                                  "max_request_bytes": 100000,
                                  "max_wall_seconds": 300,
                                  "max_consecutive_provider_failures": 5},
                       "verdict": "WITHIN", "reason": None}, fh)
        out, _seen, rows, p = self.drive(
            "[SP]deepseek-v4-flash", {
                "SHERLOCK_REQUIRE_ATTRIBUTION": "1",
                "SHERLOCK_EXPECTED_RETURNED_IDENTITY": "DeepSeek-V4-Flash",
                "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS": "1",
                "SHERLOCK_BUDGET_MAX_REQUEST_BYTES": "100000",
                "SHERLOCK_BUDGET_MAX_WALL_SECONDS": "300",
                "SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES": "5",
                "SHERLOCK_UPSTREAM_RETRY_BASE_MS": "1",
                "INFLIGHT_PATH": os.path.join(trace, "upstream-inflight.json"),
            }, fail_times=3)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(out.get("BASE", "").startswith("http://127.0.0.1:"))
        self.assertEqual([row["status"] for row in rows], [400])
        with open(os.path.join(trace, "upstream-budget-state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual((state["attempts_charged"], state["verdict"], state["reason"]),
                         (1, "EXCEEDED", "MAX_UPSTREAM_ATTEMPTS"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
