#!/usr/bin/env python3
"""Paid-probe dispatch envelope: localhost only, never a provider.

Each refusal assertion checks the upstream's real request counter.  A 503 alone
is not evidence of pre-dispatch safety: it could be a provider response.
"""
import json
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(size)
        self.server.requests += 1
        if self.server.sse_fragments:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for fragment, pause in self.server.sse_fragments:
                try:
                    self.wfile.write(fragment); self.wfile.flush()
                except BrokenPipeError:
                    return
                time.sleep(pause)
            return
        status = (self.server.statuses.pop(0) if self.server.statuses else 200)
        delay = getattr(self.server, "delay_s", 0)
        if delay:
            time.sleep(delay)
        payload = json.dumps({
            "model": "fixture-model",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": None if status >= 400 else self.server.usage,
        }).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.server.drip_s:
            midpoint = max(1, len(payload) // 3)
            for offset in range(0, len(payload), midpoint):
                self.wfile.write(payload[offset:offset + midpoint]); self.wfile.flush()
                time.sleep(self.server.drip_s)
        else:
            self.wfile.write(payload)


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DispatchBudget(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.upstream = HTTPServer(("127.0.0.1", free_port()), Upstream)
        self.upstream.requests = 0
        self.upstream.delay_s = 0
        self.upstream.drip_s = 0
        self.upstream.statuses = []
        self.upstream.sse_fragments = []
        self.upstream.usage = {"prompt_tokens": 10, "completion_tokens": 5}
        self.up_port = self.upstream.server_port
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.px_port = free_port()
        self.process = None

    def tearDown(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process.stdout.close()
            self.process.stderr.close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.temp.cleanup()

    def _paths(self):
        root = pathlib.Path(self.temp.name)
        return root / "action-budget.json", root / "rates.json", root / "state.json"

    def _action(self, limits, run_tag="dispatch-fixture"):
        return {"schema": 1, "run_tag": run_tag, "limits": limits}

    def _rates(self, run_tag="dispatch-fixture"):
        row = {"schema": 1, "run_tag": run_tag,
                "effective_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "fixture",
                "prompt_rub_per_token": 0.01,
                "completion_rub_per_token": 0.02}
        return self._sign_rates(row)

    def _sign_rates(self, row):
        signed = dict(row); signed.pop("sha256", None)
        canonical = json.dumps(signed, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        signed["sha256"] = hashlib.sha256(canonical).hexdigest()
        return signed

    def start(self, limits=None, rates=True, action_tag="dispatch-fixture",
              rate_tag="dispatch-fixture", action_document=None, rate_document=None,
              keep_controls=False, extra_env=None):
        limits = limits or {"max_provider_calls": 10, "max_prompt_tokens": 1000,
                            "max_completion_tokens": 1000, "max_wall_time_s": 300,
                            "max_estimated_cost_rub": 100.0}
        action, rate, state = self._paths()
        if not keep_controls:
            action.write_text(json.dumps(self._action(limits, action_tag)
                                         if action_document is None else action_document),
                              encoding="utf-8")
            if rates is True:
                rate.write_text(json.dumps(self._rates(rate_tag)
                                            if rate_document is None else rate_document),
                                encoding="utf-8")
        env = dict(os.environ, UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=str(pathlib.Path(self.temp.name) / "ledger.jsonl"),
                   LISTEN_PORT=str(self.px_port), RUN_TAG="dispatch-fixture",
                   UPSTREAM_BUDGET_STATE=str(state),
                   UPSTREAM_ACTION_BUDGET=str(action),
                   UPSTREAM_RATE_SNAPSHOT=str(rate), UPSTREAM_READ_TIMEOUT="2")
        env.update(extra_env or {})
        self.process = subprocess.Popen([sys.executable, PROXY], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % self.px_port,
                                            timeout=0.2) as response:
                    response.read()
                return state
            except Exception:
                time.sleep(0.02)
        out, err = self.process.communicate(timeout=5)
        self.fail("proxy never came up: %r %r" % (out, err))

    def post(self, max_tokens=10):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": "fixture", "max_tokens": max_tokens,
                             "messages": [{"role": "user", "content": "hello"}]}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post_raw(self, raw):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=raw, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def assert_refused_without_upstream(self, *, action_document=None, rate_document=None,
                                        action_tag="dispatch-fixture",
                                        rate_tag="dispatch-fixture", max_tokens=10,
                                        reason="RATE_SNAPSHOT_INVALID"):
        state = self.start(action_document=action_document, rate_document=rate_document,
                           action_tag=action_tag, rate_tag=rate_tag)
        status, _ = self.post(max_tokens)
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], reason)

    def budget(self, path):
        for _ in range(100):
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            time.sleep(0.02)
        self.fail("action state was not written")

    def test_next_dispatch_crossing_each_limit_is_not_sent(self):
        fields = ("max_provider_calls", "max_prompt_tokens", "max_completion_tokens",
                  "max_wall_time_s", "max_estimated_cost_rub")
        for field in fields:
            with self.subTest(field=field):
                _, _, old_state = self._paths()
                old_state.unlink(missing_ok=True)
                pathlib.Path(str(old_state) + ".lock").unlink(missing_ok=True)
                limits = {"max_provider_calls": 10, "max_prompt_tokens": 1000,
                          "max_completion_tokens": 1000, "max_wall_time_s": 300,
                          "max_estimated_cost_rub": 100.0}
                limits[field] = 0 if field != "max_wall_time_s" else 1
                try:
                    state = self.start(limits)
                    status, _ = self.post()
                    self.assertEqual(status, 503)
                    self.assertEqual(self.upstream.requests, 0, field)
                    self.assertEqual(self.budget(state)["reason"], "MAX_" + field.upper())
                finally:
                    if self.process:
                        self.process.terminate(); self.process.wait(timeout=5)
                        self.process.stdout.close(); self.process.stderr.close(); self.process = None
                    self.px_port = free_port()

    def test_missing_rate_snapshot_blocks_before_upstream(self):
        state = self.start(rates=False)
        status, payload = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], "RATE_SNAPSHOT_INVALID",
                         (payload, self.budget(state)))

    def test_provider_overshoot_is_recorded_not_relabelled_as_prevented(self):
        self.upstream.usage = {"prompt_tokens": 10, "completion_tokens": 110}
        limits = {"max_provider_calls": 10, "max_prompt_tokens": 1000,
                  "max_completion_tokens": 100, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits)
        status, _ = self.post(max_tokens=100)
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.requests, 1)
        for _ in range(100):
            budget = self.budget(state)
            if budget.get("observed", {}).get("completion_tokens") == 110:
                break
            time.sleep(0.02)
        self.assertEqual(budget["budget_assurance"], "client_pre_dispatch")
        self.assertEqual(budget["completed_overshoot"]["completion_tokens"], 10)

    def test_malformed_rate_and_action_controls_never_contact_upstream(self):
        rates = self._rates()
        variants = {
            "rate_boolean": (None, dict(rates, prompt_rub_per_token=True),
                               "RATE_SNAPSHOT_INVALID"),
            "rate_nan": (None, dict(rates, prompt_rub_per_token=float("nan")),
                          "RATE_SNAPSHOT_INVALID"),
            "rate_infinity": (None, dict(rates, completion_rub_per_token=float("inf")),
                               "RATE_SNAPSHOT_INVALID"),
            "rate_unknown": (None, dict(rates, surprise=1), "RATE_SNAPSHOT_INVALID"),
            "rate_stale": (None, dict(rates, effective_at="2000-01-01T00:00:00Z"),
                           "RATE_SNAPSHOT_INVALID"),
            "rate_run_tag": (None, dict(rates, run_tag="another-run"),
                            "RATE_SNAPSHOT_INVALID"),
            "action_unknown": (dict(self._action({"max_provider_calls": 10,
                            "max_prompt_tokens": 1000, "max_completion_tokens": 1000,
                            "max_wall_time_s": 300, "max_estimated_cost_rub": 100.0}),
                            surprise=1), None, "ACTION_BUDGET_INVALID"),
            "action_run_tag": (self._action({"max_provider_calls": 10,
                            "max_prompt_tokens": 1000, "max_completion_tokens": 1000,
                            "max_wall_time_s": 300, "max_estimated_cost_rub": 100.0},
                            "another-run"), None, "ACTION_BUDGET_INVALID"),
        }
        for name, (action, rate, reason) in variants.items():
            with self.subTest(name=name):
                self.assert_refused_without_upstream(action_document=action,
                                                      rate_document=rate, reason=reason)
                self.process.terminate(); self.process.wait(timeout=5)
                self.process.stdout.close(); self.process.stderr.close(); self.process = None
                self.px_port = free_port()
                _, _, old_state = self._paths()
                old_state.unlink(missing_ok=True)
                pathlib.Path(str(old_state) + ".lock").unlink(missing_ok=True)

    def test_missing_or_boolean_output_cap_never_contacts_upstream(self):
        for value in (None, True):
            with self.subTest(value=value):
                state = self.start()
                status, _ = self.post(value)
                self.assertEqual(status, 503)
                self.assertEqual(self.upstream.requests, 0)
                self.assertEqual(self.budget(state)["reason"], "REQUEST_MAX_TOKENS_INVALID")
                self.process.terminate(); self.process.wait(timeout=5)
                self.process.stdout.close(); self.process.stderr.close(); self.process = None
                self.px_port = free_port()
                _, _, old_state = self._paths()
                old_state.unlink(missing_ok=True)
                pathlib.Path(str(old_state) + ".lock").unlink(missing_ok=True)

    def test_concurrent_reservations_allow_only_one_contact(self):
        state = self.start({"max_provider_calls": 1, "max_prompt_tokens": 1000,
                            "max_completion_tokens": 1000, "max_wall_time_s": 300,
                            "max_estimated_cost_rub": 100.0})
        barrier = threading.Barrier(3)
        results = []
        def post_once():
            barrier.wait(); results.append(self.post()[0])
        threads = [threading.Thread(target=post_once) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=10)
        self.assertEqual(sorted(results), [200, 503])
        self.assertEqual(self.upstream.requests, 1)
        self.assertEqual(self.budget(state)["projected"]["provider_calls"], 1)

    def test_crash_retains_durable_reservation_for_the_next_proxy(self):
        limits = {"max_provider_calls": 1, "max_prompt_tokens": 1000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits)
        self.upstream.delay_s = 2
        thread = threading.Thread(target=self.post)
        thread.start()
        deadline = time.time() + 3
        while self.upstream.requests != 1 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.upstream.requests, 1)
        self.process.terminate(); self.process.wait(timeout=5)
        self.process.stdout.close(); self.process.stderr.close(); self.process = None
        thread.join(timeout=6)
        self.upstream.delay_s = 0
        self.px_port = free_port()
        self.start(limits, keep_controls=True)
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 1)
        self.assertEqual(self.budget(state)["projected"]["provider_calls"], 1)

    def test_budget_refusal_precedes_route_and_credential_reads(self):
        limits = {"max_provider_calls": 0, "max_prompt_tokens": 1000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        missing = str(pathlib.Path(self.temp.name) / "does-not-exist-key")
        state = self.start(limits, extra_env={"UPSTREAM_API_KEY_FILE": missing})
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], "MAX_MAX_PROVIDER_CALLS")

    def test_duplicate_max_tokens_is_refused_before_upstream(self):
        state = self.start()
        status, _ = self.post_raw(
            b'{"model":"fixture","max_tokens":1000,"max_tokens":1,'
            b'"messages":[{"role":"user","content":"hello"}]}')
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], "REQUEST_JSON_INVALID")

    def test_rate_digest_tampering_is_refused_before_upstream(self):
        bad = self._rates(); bad["sha256"] = "0" * 64
        state = self.start(rate_document=bad)
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], "RATE_SNAPSHOT_INVALID")

    def test_unknown_usage_still_counts_completed_contact(self):
        self.upstream.usage = None
        state = self.start()
        status, _ = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.requests, 1)
        for _ in range(100):
            row = self.budget(state)
            if row["observed"]["provider_calls"] == 1:
                break
            time.sleep(0.02)
        self.assertEqual(row["observed"]["provider_calls"], 1)
        self.assertEqual(row["observed_usage_unknown"], 1)

    def test_post_route_model_growth_is_covered_before_contact(self):
        limits = {"max_provider_calls": 10, "max_prompt_tokens": 100,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits, extra_env={"UPSTREAM_MODEL": "m" * 300})
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], "MAX_MAX_PROMPT_TOKENS")

    def test_retry_needs_a_second_reservation_before_second_contact(self):
        self.upstream.statuses = [503, 200]
        limits = {"max_provider_calls": 2, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits, extra_env={"UPSTREAM_RETRY_MAX": "1"})
        status, _ = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.requests, 2)
        for _ in range(100):
            row = self.budget(state)
            if row["observed"]["provider_calls"] == 2:
                break
            time.sleep(0.02)
        self.assertEqual(row["projected"]["provider_calls"], 2)
        self.assertEqual(row["observed"]["provider_calls"], 2)
        ledger = [json.loads(line) for line in
                  (pathlib.Path(self.temp.name) / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual(len({row["action_attempt_id"] for row in ledger}), 2)

    def test_drip_body_obeys_reserved_end_to_end_deadline(self):
        self.upstream.drip_s = 0.75
        limits = {"max_provider_calls": 10, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 2,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits)
        began = time.monotonic(); status, _ = self.post(); elapsed = time.monotonic() - began
        self.assertEqual(status, 502)
        self.assertLess(elapsed, 2.5)
        self.assertEqual(self.upstream.requests, 1)
        self.assertEqual(self.budget(state)["projected"]["wall_time_s"], 2.0)

    def test_fragmented_unterminated_sse_cannot_finish_after_action_deadline(self):
        self.upstream.sse_fragments = [
            (b'data: {"model":"fixture-model","choices":[', 0.7),
            (b'{"delta":{"content":"o', 0.7),
            (b'k"}}]}', 0.7),
            (b'\n\n', 0),
        ]
        limits = {"max_provider_calls": 10, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 2,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits)
        began = time.monotonic(); status, _ = self.post(); elapsed = time.monotonic() - began
        self.assertEqual(status, 502)
        self.assertLess(elapsed, 2.5)
        self.assertEqual(self.upstream.requests, 1)
        self.assertEqual(self.budget(state)["projected"]["wall_time_s"], 2.0)

    def test_semantic_state_rollback_fails_closed_after_restart(self):
        limits = {"max_provider_calls": 1, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits)
        self.assertEqual(self.post()[0], 200); self.assertEqual(self.upstream.requests, 1)
        for _ in range(100):
            if self.budget(state)["observed"]["provider_calls"] == 1:
                break
            time.sleep(0.02)
        self.assertEqual(self.budget(state)["observed"]["provider_calls"], 1)
        self.process.terminate(); self.process.wait(timeout=5)
        self.process.stdout.close(); self.process.stderr.close(); self.process = None
        row = self.budget(state); row["projected"]["provider_calls"] = 0
        state.write_text(json.dumps(row), encoding="utf-8")
        self.px_port = free_port(); self.start(limits, keep_controls=True)
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 1)

    def test_restart_reconciles_every_projected_dimension_before_new_contact(self):
        limits = {"max_provider_calls": 10, "max_prompt_tokens": 500,
                  "max_completion_tokens": 15, "max_wall_time_s": 3,
                  "max_estimated_cost_rub": 5.0}
        fields = ("prompt_tokens", "completion_tokens", "wall_time_s",
                  "estimated_cost_rub")
        for field in fields:
            with self.subTest(field=field):
                self.upstream.requests = 0
                state = self.start(limits)
                try:
                    self.assertEqual(self.post()[0], 200)
                    self.assertEqual(self.upstream.requests, 1)
                    self.process.terminate(); self.process.wait(timeout=5)
                    self.process.stdout.close(); self.process.stderr.close(); self.process = None
                    row = self.budget(state)
                    self.assertGreater(row["projected"][field], 0)
                    row["projected"][field] = 0
                    state.write_text(json.dumps(row), encoding="utf-8")
                    self.px_port = free_port(); self.start(limits, keep_controls=True)
                    status, _ = self.post()
                    self.assertEqual(status, 503)
                    self.assertEqual(self.upstream.requests, 1)
                finally:
                    if self.process:
                        self.process.terminate(); self.process.wait(timeout=5)
                        self.process.stdout.close(); self.process.stderr.close(); self.process = None
                    # Fresh controls/state isolate the next projected dimension.
                    pathlib.Path(self.temp.name, "state.json").unlink(missing_ok=True)
                    pathlib.Path(self.temp.name, "state.json.reservations.jsonl").unlink(
                        missing_ok=True)
                    pathlib.Path(self.temp.name, "ledger.jsonl").unlink(missing_ok=True)
                    self.px_port = free_port()

    def test_http_error_still_counts_completed_contact_with_unknown_usage(self):
        self.upstream.statuses = [500]
        state = self.start()
        status, _ = self.post()
        self.assertEqual(status, 500)
        self.assertEqual(self.upstream.requests, 1)
        for _ in range(100):
            row = self.budget(state)
            if row["observed"]["provider_calls"] == 1:
                break
            time.sleep(0.02)
        self.assertEqual(row["observed"]["provider_calls"], 1)
        self.assertEqual(row["observed_usage_unknown"], 1)

    def test_rate_is_rechecked_under_the_admission_lock(self):
        state = self.start()
        _, rate, _ = self._paths()
        stale = self._rates(); stale["effective_at"] = "2000-01-01T00:00:00Z"
        rate.write_text(json.dumps(self._sign_rates(stale)), encoding="utf-8")
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 0)
        self.assertEqual(self.budget(state)["reason"], "RATE_SNAPSHOT_INVALID")

    def test_substitution_retry_needs_a_fresh_reservation_before_contact(self):
        limits = {"max_provider_calls": 1, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        state = self.start(limits, extra_env={
            "UPSTREAM_EXPECTED_RETURNED_IDENTITY": "different-model",
            "UPSTREAM_SUBSTITUTION_RETRY_MAX": "1"})
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 1)
        self.assertEqual(self.budget(state)["projected"]["provider_calls"], 1)

    def test_fallback_advance_needs_a_fresh_reservation_before_contact(self):
        limits = {"max_provider_calls": 2, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        root = pathlib.Path(self.temp.name)
        base = "http://127.0.0.1:%d/v1" % self.up_port
        route = root / "route.json"; fallbacks = root / "fallbacks.json"
        route.write_text(json.dumps({"schema": 1, "base": base, "model": "first",
                                     "expected_returned_identity": "alpha/alpha-flash-0731",
                                     "generation": 0}), encoding="utf-8")
        fallbacks.write_text(json.dumps([{"schema": 1, "base": base, "model": "second",
                                           "expected_returned_identity": "fixture-model"}]),
                             encoding="utf-8")
        state = self.start(limits, extra_env={
            "UPSTREAM_ROUTE_FILE": str(route), "UPSTREAM_ROUTE_FALLBACKS": str(fallbacks),
            "UPSTREAM_SUBSTITUTION_RETRY_MAX": "1"})
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream.requests, 2)
        self.assertEqual(self.budget(state)["projected"]["provider_calls"], 2)
        self.assertEqual(json.loads(route.read_text(encoding="utf-8"))["model"], "first")

    def test_fallback_success_has_its_own_reservation_and_completion(self):
        limits = {"max_provider_calls": 3, "max_prompt_tokens": 10000,
                  "max_completion_tokens": 1000, "max_wall_time_s": 300,
                  "max_estimated_cost_rub": 100.0}
        root = pathlib.Path(self.temp.name)
        base = "http://127.0.0.1:%d/v1" % self.up_port
        route = root / "route.json"; fallbacks = root / "fallbacks.json"
        route.write_text(json.dumps({"schema": 1, "base": base, "model": "first",
                                     "expected_returned_identity": "alpha/alpha-flash-0731",
                                     "generation": 0}), encoding="utf-8")
        fallbacks.write_text(json.dumps([{"schema": 1, "base": base, "model": "second",
                                           "expected_returned_identity": "fixture-model"}]),
                             encoding="utf-8")
        state = self.start(limits, extra_env={
            "UPSTREAM_ROUTE_FILE": str(route), "UPSTREAM_ROUTE_FALLBACKS": str(fallbacks),
            "UPSTREAM_SUBSTITUTION_RETRY_MAX": "1"})
        status, _ = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.requests, 3)
        for _ in range(100):
            row = self.budget(state)
            if row["observed"]["provider_calls"] == 3:
                break
            time.sleep(0.02)
        self.assertEqual(row["projected"]["provider_calls"], 3)
        self.assertEqual(row["observed"]["provider_calls"], 3)
        ledger = [json.loads(line) for line in
                  (root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        attempts = [entry["action_attempt_id"] for entry in ledger
                    if entry.get("action_contact_completed")]
        self.assertEqual(len(set(attempts)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
