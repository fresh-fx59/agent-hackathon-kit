#!/usr/bin/env python3
"""Tests for measure/upstream-log-proxy.py — the thing that makes a row nameable.

Why it exists: `[SP]deepseek-v4-flash` is an ALIAS. Measured 2026-08-01 over 40
byte-identical requests, it answers as two models that are ~19x apart on whether
they emit a tool call (4.8 % vs 89.5 %). The arm under test IS a tool-execution
mechanism, so the upstream can decide whether the arm runs at all. qwen-code
stamps the REQUESTED alias on every assistant message and never the returned
name, so no recorded row could be attributed to an upstream.

This proxy sits in front of the provider — `run-case.sh` already reads
SHERLOCK_BASE_URL from the environment, so it is a URL swap, not a harness
change — and writes one JSONL line per request naming what actually answered.

Everything here runs against a STUB upstream. No metered tokens.

    python3 measure/tests/test_upstream_log_proxy.py
"""
import json
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


class Stub(BaseHTTPRequestHandler):
    """Plays the provider. `mode` is set per-test on the server object."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        self.server.seen.append({"path": self.path,
                                 "auth": self.headers.get("Authorization"),
                                 "body": json.loads(body or b"{}")})
        mode = self.server.mode
        # Simulate a provider BURST: fail the next N calls, then behave. This is
        # what linkapi actually does — transient, minute-scale, independent of
        # request size and shape (both controlled for on 2026-08-02).
        if getattr(self.server, "fail_times", 0) > 0:
            self.server.fail_times -= 1
            msg = getattr(self.server, "fail_body", "Upstream request failed")
            payload = json.dumps({"error": {"message": msg}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if mode == "json_toolcall":
            payload = json.dumps({
                "model": "DeepSeek-V4-Flash",
                "choices": [{"message": {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": "c1", "type": "function",
                                                         "function": {"name": "read_file",
                                                                      "arguments": "{}"}}]}}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif mode == "json_prose":
            payload = json.dumps({
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"role": "assistant",
                                         "content": "no tools, just prose"}}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif mode == "json_length":
            # THE v41 SHAPE: a real HTTP 200 answer that the requested budget
            # cut. finish_reason "length" is the provider's own word for it.
            payload = json.dumps({
                "model": "DeepSeek-V4-Flash",
                "choices": [{"finish_reason": "length",
                             "message": {"role": "assistant",
                                         "content": "a summary that stops mid-"}}],
                "usage": {"prompt_tokens": 187825, "completion_tokens": 6700},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (
                {"model": "DeepSeek-V4-Flash",
                 "choices": [{"delta": {"role": "assistant"}}]},
                {"model": "DeepSeek-V4-Flash",
                 "choices": [{"delta": {"tool_calls": [{"index": 0,
                                                        "function": {"name": "read_file"}}]}}]},
            ):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif mode == "malformed_sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"model":"DeepSeek-V4-Flash"}\n\n')
            self.wfile.write(b'data: {"object":"chat.completioHTTP/1.1 502 Bad Gateway\n\n')
            self.wfile.flush()
        elif mode == "error":
            payload = json.dumps({"error": {"message": "Upstream request failed"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProxyCase(unittest.TestCase):

    def setUp(self):
        self.up_port = free_port()
        self.srv = HTTPServer(("127.0.0.1", self.up_port), Stub)
        self.srv.seen = []
        self.srv.mode = "json_toolcall"
        self.srv.fail_times = 0
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "upstream.jsonl")
        self.px_port = free_port()
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            for s in (self.proc.stdout, self.proc.stderr):
                if s:
                    s.close()
        self.srv.shutdown()
        self.srv.server_close()

    def start(self, mode):
        self.srv.mode = mode
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log,
                   LISTEN_PORT=str(self.px_port))
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):                       # wait for the port to answer
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        out, err = self.proc.communicate(timeout=5)
        self.fail("proxy never came up: %s %s" % (out, err))

    def start_retrying(self, mode, attempts, delay_ms="10"):
        self.srv.mode = mode
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log, LISTEN_PORT=str(self.px_port),
                   UPSTREAM_RETRY_MAX=str(attempts),
                   UPSTREAM_RETRY_BASE_MS=delay_ms)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def start_budgeted(self, mode="json_toolcall", bootstrap=True, **updates):
        """Start the real proxy with finite, trace-local paid reservations."""
        self.srv.mode = mode
        self.budget_state = os.path.join(self.tmp, "upstream-budget-state.json")
        env = dict(
            os.environ,
            UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
            UPSTREAM_LOG=self.log,
            LISTEN_PORT=str(self.px_port),
            RUN_TAG="controlled-run",
            UPSTREAM_BUDGET_STATE=self.budget_state,
            UPSTREAM_MAX_UPSTREAM_ATTEMPTS="20",
            UPSTREAM_MAX_REQUEST_BYTES="1000000",
            UPSTREAM_MAX_WALL_SECONDS="300",
            UPSTREAM_MAX_CONSECUTIVE_PROVIDER_FAILURES="5",
            UPSTREAM_EXPECTED_RETURNED_IDENTITY="DeepSeek-V4-Flash",
        )
        env.update({key: str(value) for key, value in updates.items()})
        if bootstrap:
            limits = {
                "max_upstream_attempts": int(env["UPSTREAM_MAX_UPSTREAM_ATTEMPTS"]),
                "max_request_bytes": int(env["UPSTREAM_MAX_REQUEST_BYTES"]),
                "max_wall_seconds": int(env["UPSTREAM_MAX_WALL_SECONDS"]),
                "max_consecutive_provider_failures": int(
                    env["UPSTREAM_MAX_CONSECUTIVE_PROVIDER_FAILURES"]),
            }
            pathlib.Path(self.budget_state).write_text(json.dumps({
                "schema": 1, "run_tag": "controlled-run", "updated_at": "fixture",
                "attempts_charged": 0, "request_bytes": 0,
                "consecutive_provider_failures": 0, "limits": limits,
                "verdict": "WITHIN", "reason": None,
            }) + "\n", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        out, err = self.proc.communicate(timeout=5)
        self.fail("budgeted proxy never came up: %s %s" % (out, err))

    def budget(self):
        return json.loads(pathlib.Path(self.budget_state).read_text())

    def post(self, body=None, auth="Bearer sekrit"):
        body = body if body is not None else {"model": "[SP]deepseek-v4-flash",
                                              "messages": [{"role": "user", "content": "hi"}]}
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": auth})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.getcode(), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def lines(self, expect=1):
        """The recorded rows, once at least `expect` of them exist.

        The final record is written AFTER the client's bytes go out, so post()
        can return before the last line lands. Waiting for content alone made a
        two-row assertion flaky in the row order it cares about."""
        rows = []
        for _ in range(200):                       # the write is off the hot path
            if os.path.exists(self.log):
                with open(self.log, encoding="utf-8") as fh:
                    rows = [json.loads(l) for l in fh if l.strip()]
                if len(rows) >= expect:
                    break
            time.sleep(0.05)
        return rows


class ItNamesWhatActuallyAnswered(ProxyCase):

    def test_records_the_returned_model_not_the_requested_alias(self):
        self.start("json_toolcall")
        code, _ = self.post()
        self.assertEqual(code, 200)
        rec = self.lines()[0]
        self.assertEqual(rec["requested_model"], "[SP]deepseek-v4-flash")
        self.assertEqual(rec["returned_model"], "DeepSeek-V4-Flash")

    def test_records_whether_a_tool_call_came_back(self):
        self.start("json_toolcall")
        self.post()
        self.assertTrue(self.lines()[0]["tool_call"])

    def test_records_a_prose_answer_as_no_tool_call(self):
        self.start("json_prose")
        self.post()
        rec = self.lines()[0]
        self.assertEqual(rec["returned_model"], "deepseek-v4-flash")
        self.assertFalse(rec["tool_call"])

    def test_reads_the_model_out_of_a_streaming_response(self):
        self.start("sse")
        code, body = self.post()
        self.assertEqual(code, 200)
        rec = self.lines()[0]
        self.assertEqual(rec["returned_model"], "DeepSeek-V4-Flash")
        self.assertTrue(rec["tool_call"])

    def test_marks_a_malformed_200_sse_as_a_stream_failure(self):
        self.start("malformed_sse")
        code, _ = self.post()
        self.assertEqual(code, 200)
        rec = self.lines()[0]
        self.assertEqual(rec["status"], 200)
        self.assertEqual(rec["stream_parse_errors"], 1)
        self.assertFalse(rec["stream_complete"])
        self.assertEqual(rec["upstream_error"], "malformed_sse_embedded_http_status:502")

    def test_records_a_provider_error_instead_of_swallowing_it(self):
        self.start("error")
        code, _ = self.post()
        self.assertEqual(code, 400)
        rec = self.lines()[0]
        self.assertEqual(rec["status"], 400)

    def test_records_how_big_the_request_was(self):
        """~52 % of upstream calls in a real run come back 400 while 7 KB probes
        pass 40/40. Request size is the leading hypothesis and the ledger has
        never carried it, so a 400 could never be correlated with anything."""
        self.start("json_toolcall")
        self.post({"model": "[SP]deepseek-v4-flash",
                   "messages": [{"role": "user", "content": "x" * 5000}]})
        rec = self.lines()[0]
        self.assertGreater(rec["request_bytes"], 5000)


class ItStaysOutOfTheWay(ProxyCase):

    def test_the_client_gets_the_upstream_body_unchanged(self):
        self.start("json_toolcall")
        _code, body = self.post()
        got = json.loads(body)
        self.assertEqual(got["model"], "DeepSeek-V4-Flash")
        self.assertEqual(got["choices"][0]["message"]["tool_calls"][0]["id"], "c1")

    def test_a_streaming_body_arrives_intact(self):
        self.start("sse")
        _code, body = self.post()
        self.assertIn(b"data: [DONE]", body)
        self.assertEqual(body.count(b"data: "), 3)

    def test_the_auth_header_reaches_the_provider(self):
        self.start("json_toolcall")
        self.post(auth="Bearer sekrit")
        self.assertEqual(self.srv.seen[0]["auth"], "Bearer sekrit")

    def test_the_request_body_reaches_the_provider_unchanged(self):
        self.start("json_toolcall")
        self.post({"model": "[SP]deepseek-v4-flash", "messages": [], "tools": [1, 2]})
        self.assertEqual(self.srv.seen[0]["body"]["tools"], [1, 2])

    def test_the_secret_is_never_written_to_the_log(self):
        self.start("json_toolcall")
        self.post(auth="Bearer sekrit")
        self.assertTrue(self.lines(), "upstream log row never became durable")
        with open(self.log, encoding="utf-8") as fh:
            self.assertNotIn("sekrit", fh.read())


class ItRidesOutAProviderBurst(ProxyCase):
    """qwen-code's retry budget is SHORTER than a linkapi burst, so runs die.

    Measured 2026-08-02. The 400s on this lane are transient bursts lasting a
    minute or two; neither request size nor request shape explains them —
    controlled for both, interleaved, 12/12 succeeded. But D11 died anyway:
    4 consecutive 400s at 143 KB then a 200, then **5 consecutive 400s at
    171 KB and the run was over** — «Model stream ended without a finish
    reason», 98,515 tokens billed for no row. The client gave up mid-burst.

    The proxy is already in the path and it is the only place that can wait
    longer than the client will. It retries a failed upstream call with
    exponential backoff, and it records EVERY attempt, because a retry
    re-uploads the whole context and is therefore billed — an invisible retry
    would be exactly the "failed calls are free" mistake that the spend cap
    exists to prevent.

    It never retries once bytes have reached the client: a half-streamed answer
    cannot be un-sent.
    """

    def test_a_burst_shorter_than_the_budget_is_survived(self):
        self.srv.fail_times = 3
        self.start_retrying("json_toolcall", attempts=6)
        code, _ = self.post({"model": "m", "messages": []})
        self.assertEqual(code, 200, "the proxy gave up inside a survivable burst")

    def test_every_attempt_is_recorded_because_every_attempt_is_billed(self):
        self.srv.fail_times = 2
        self.start_retrying("json_toolcall", attempts=6)
        self.post({"model": "m", "messages": []})
        rows = self.lines(3)
        self.assertEqual(len(rows), 3, "retries must not be invisible: %r" % rows)
        self.assertEqual([r["status"] for r in rows], [400, 400, 200])
        self.assertEqual([r["attempt"] for r in rows], [1, 2, 3])

    def test_a_burst_longer_than_the_budget_still_surfaces(self):
        self.srv.fail_times = 99
        self.start_retrying("json_toolcall", attempts=2)
        code, _ = self.post({"model": "m", "messages": []})
        self.assertEqual(code, 400, "a genuine failure must reach the client")

    def test_retrying_is_off_unless_asked_for(self):
        """Default stays a pass-through: one request in, one request out."""
        self.srv.fail_times = 1
        self.start("json_toolcall")
        self.post({"model": "m", "messages": []})
        self.assertEqual(len(self.lines()), 1)


class ItRecordsWhyTheUpstreamRefused(ProxyCase):
    """60 failures on D04 and not one of them says WHY.

    Every 400 this project has reasoned about was a bare status code. Three
    successive theories — size, then shape, then bursts — were each argued from
    counts alone, and two of them were wrong, because the one artifact that could
    have settled it in a minute was being read and thrown away: the retry path
    already calls `e.read()` and discards the provider's own message. "Rate limit
    exceeded" and "invalid request" are the same integer in this ledger.

    So a non-2xx now records the provider's text, truncated. Truncation is not
    only tidiness: an error body can echo part of the request, and the request
    carries corpus lines. It lands in the run dir beside the trajectory, which is
    already 0700, and it never carries a header.
    """

    def test_a_retried_failure_records_what_the_provider_said(self):
        self.srv.fail_body = "Rate limit exceeded for org-42"
        self.srv.fail_times = 1
        self.start_retrying("json_toolcall", attempts=6)
        self.post({"model": "m", "messages": []})
        rows = self.lines(2)
        self.assertEqual([r["status"] for r in rows], [400, 200])
        self.assertIn("Rate limit exceeded", rows[0]["upstream_error"],
                      "the retry path reads the body already — record it: %r" % rows[0])

    def test_the_final_failure_records_what_the_provider_said(self):
        """Retrying off: the single row IS the failure, and it is the row a
        post-mortem reads."""
        self.srv.fail_body = "context_length_exceeded: 412000 > 400000"
        self.srv.fail_times = 1
        self.start("json_toolcall")
        code, _ = self.post({"model": "m", "messages": []})
        self.assertEqual(code, 400)
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        self.assertIn("context_length_exceeded", rows[0]["upstream_error"])

    def test_the_client_still_gets_the_real_error_body(self):
        """Reading the body to log it must not consume it. A proxy that logs the
        reason and hands the client an empty 400 has moved the blindness, not
        removed it."""
        self.srv.fail_body = "Upstream request failed: burst"
        self.srv.fail_times = 1
        self.start("json_toolcall")
        code, body = self.post({"model": "m", "messages": []})
        self.assertEqual(code, 400)
        self.assertIn("burst", json.loads(body)["error"]["message"])

    def test_a_long_error_body_is_truncated(self):
        self.srv.fail_body = "E" * 5000
        self.srv.fail_times = 1
        self.start("json_toolcall")
        self.post({"model": "m", "messages": []})
        self.assertLessEqual(len(self.lines()[0]["upstream_error"]), 300)

    def test_a_successful_call_records_no_error_text(self):
        self.start("json_toolcall")
        self.post()
        self.assertIsNone(self.lines()[0].get("upstream_error"),
                          "a 200 has nothing to explain")

    def test_a_provider_error_echoing_a_key_is_redacted(self):
        """`upstream_error` is the ONE free-text field that reaches disk.

        The proxy never logs the Authorization header — every record() call is an
        explicit field list. But a provider that quotes the caller's key back in its
        refusal writes it here, verbatim and durable, into every run directory. The
        key is metered (linkapi), so this is the credential worth protecting.
        """
        # Built at runtime, never written as a literal: this repo is PUBLIC and its
        # pii-guard hook rejects a key-shaped string in a diff — correctly, since a
        # scanner cannot tell a fake one from a live one.
        fake = "sk-" + ("abcdef0123456789" * 2)
        self.srv.fail_body = "invalid api key: " + fake
        self.srv.fail_times = 1
        self.start("json_toolcall")
        self.post({"model": "m", "messages": []})
        got = self.lines()[0]["upstream_error"]
        self.assertNotIn(fake, got, "the key reached disk: %r" % got)
        self.assertIn("<redacted>", got)
        self.assertIn("invalid api key", got,
                      "redact the credential, keep the diagnosis — that is why "
                      "this field exists at all")

    def test_a_bearer_token_in_an_error_body_is_redacted(self):
        token = "abc123def456" + "ghi789jkl"        # runtime-built, see the test above
        self.srv.fail_body = "rejected header Authorization: Bearer " + token
        self.srv.fail_times = 1
        self.start("json_toolcall")
        self.post({"model": "m", "messages": []})
        got = self.lines()[0]["upstream_error"]
        self.assertNotIn(token, got, "the token reached disk: %r" % got)
        self.assertIn("rejected header", got)

    def test_an_ordinary_error_body_is_not_touched(self):
        """Over-redaction would undo D04's whole lesson. A refusal that carries no
        credential must survive byte-for-byte."""
        self.srv.fail_body = "context_length_exceeded: 412000 > 400000"
        self.srv.fail_times = 1
        self.start("json_toolcall")
        self.post({"model": "m", "messages": []})
        # The field holds the provider's whole raw body, not the extracted message.
        self.assertEqual(
            self.lines()[0]["upstream_error"],
            json.dumps({"error": {"message": "context_length_exceeded: 412000 > 400000"}}))


class ItRefusesToSpendRetriesOnADeterministic400(ProxyCase):
    """A 400 THAT RETRYING CANNOT FIX MUST NOT ENTER THE BURST PATH.

    The class above rides out a linkapi burst because those 400s are transient
    and minute-scale. These are not. `deepseek-v4-flash` is a REASONING model:
    probed against CloseRouter 2026-08-26 with max_tokens=4 it answers HTTP 400
    "the output token limit was exhausted by model reasoning before an answer
    was produced; increase max_completion_tokens/max_output_tokens", and a
    second probe confirmed the mechanism (8 output tokens requested, all 8
    returned as reasoning_tokens, content empty). The v38 dead run's single 400
    is the sibling: 220 KB of request and an SSE body reading "The
    `reasoning_content` in the thinking mode must be passed back to the API."

    Twelve retries cannot help either one - they need a different request. So
    the class is NAMED in the row with the request's max_tokens beside it, the
    provider is asked exactly once, and the tuning is left to a launcher
    decision made with the receipt in hand.
    """

    RELAY = ("The `reasoning_content` in the thinking mode must be passed back "
             "to the API.")
    BUDGET = ("the output token limit was exhausted by model reasoning before an "
              "answer was produced; increase max_completion_tokens/max_output_tokens")

    def refuse(self, message):
        self.srv.fail_body = message
        self.srv.fail_times = 99
        self.start_retrying("json_toolcall", attempts=6, delay_ms="10")
        return self.post({"model": "m", "messages": [], "max_tokens": 32768})

    def test_the_dead_runs_400_is_asked_exactly_once(self):
        code, _ = self.refuse(self.RELAY)
        self.assertEqual(code, 400, "a genuine failure must reach the client")
        rows = self.lines(1)
        self.assertEqual(len(self.srv.seen), 1,
                         "a deterministic 400 was retried: %d upstream calls"
                         % len(self.srv.seen))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["upstream_refusal_class"],
                         "REASONING_CONTENT_NOT_RELAYED")

    def test_the_output_budget_400_is_asked_exactly_once(self):
        code, _ = self.refuse(self.BUDGET)
        self.assertEqual(code, 400)
        rows = self.lines(1)
        self.assertEqual(len(self.srv.seen), 1)
        self.assertEqual(rows[0]["upstream_refusal_class"],
                         "OUTPUT_BUDGET_EXHAUSTED_BY_REASONING")

    def test_the_row_carries_the_max_tokens_that_caused_it(self):
        """The fix for this is a bigger budget, so the row must say what it was.
        This file changes no default: it names the failure and surfaces it."""
        self.refuse(self.BUDGET)
        rows = self.lines(1)
        self.assertEqual(rows[0]["request_max_tokens"], 32768)

    def test_the_provider_s_own_words_still_reach_the_client_and_the_ledger(self):
        code, body = self.refuse(self.RELAY)
        self.assertEqual(code, 400)
        self.assertIn("reasoning_content", body.decode("utf-8", "replace"))
        self.assertIn("reasoning_content", self.lines(1)[0]["upstream_error"])

    def test_a_transient_400_still_rides_out_the_burst(self):
        """The whole point of the split: patience is still spent where it works."""
        self.srv.fail_body = "Upstream request failed"
        self.srv.fail_times = 3
        self.start_retrying("json_toolcall", attempts=6, delay_ms="10")
        code, _ = self.post({"model": "m", "messages": []})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.lines(4)), 4)

    def test_a_transient_400_gets_no_class_key_at_all(self):
        """A clean ledger keeps the exact shape every previous run wrote."""
        self.srv.fail_body = "Rate limit exceeded for org-42"
        self.srv.fail_times = 1
        self.start("json_toolcall")
        self.post({"model": "m", "messages": []})
        self.assertNotIn("upstream_refusal_class", self.lines(1)[0])


class ItCanRestoreTheProviderAlias(ProxyCase):
    """The whole 177,000-token ceiling was a MODEL-ID PARSING artifact.

    qwen-code sizes the context window from the model id. Verified against its
    own normalize() + table: "deepseek-v4-flash" matches /^deepseek-v4/ and gets
    1,000,000 input tokens; "[SP]deepseek-v4-flash" normalizes to
    "[sp]deepseek-v4-flash", matches nothing, and falls back to the 200,000
    default -- from which the 177,000 hard limit follows. linkapi's alias has to
    carry the [SP] prefix, and qwen-code has to not see it.

    So the proxy sends qwen-code's clean id upstream as the aliased one:
    UPSTREAM_MODEL replaces the request's model field on the way out. The rest
    of the body is untouched, and the recorded row still names what answered.
    """

    def start_rewriting(self, mode, upstream_model):
        self.srv.mode = mode
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log, LISTEN_PORT=str(self.px_port),
                   UPSTREAM_MODEL=upstream_model)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def test_the_provider_sees_the_aliased_id(self):
        self.start_rewriting("json_toolcall", "[SP]deepseek-v4-flash")
        self.post({"model": "deepseek-v4-flash", "messages": []})
        self.assertEqual(self.srv.seen[0]["body"]["model"], "[SP]deepseek-v4-flash")

    def test_the_row_still_records_what_the_client_asked_for(self):
        self.start_rewriting("json_toolcall", "[SP]deepseek-v4-flash")
        self.post({"model": "deepseek-v4-flash", "messages": []})
        rec = self.lines()[0]
        self.assertEqual(rec["requested_model"], "deepseek-v4-flash")
        self.assertEqual(rec["sent_model"], "[SP]deepseek-v4-flash")
        self.assertEqual(rec["returned_model"], "DeepSeek-V4-Flash")

    def test_nothing_else_in_the_body_is_touched(self):
        self.start_rewriting("json_toolcall", "[SP]deepseek-v4-flash")
        self.post({"model": "deepseek-v4-flash", "messages": [{"role": "user",
                   "content": "hi"}], "tools": [1, 2], "stream": False})
        body = self.srv.seen[0]["body"]
        self.assertEqual(body["tools"], [1, 2])
        self.assertEqual(body["messages"][0]["content"], "hi")
        self.assertIs(body["stream"], False)

    def test_without_the_env_var_the_model_is_left_alone(self):
        """Default must stay a pass-through proxy."""
        self.start("json_toolcall")
        self.post({"model": "deepseek-v4-flash", "messages": []})
        self.assertEqual(self.srv.seen[0]["body"]["model"], "deepseek-v4-flash")


class ItReservesPaidBudgetBeforeForwarding(ProxyCase):

    def test_restart_reconciles_completed_rows_as_a_reservation_lower_bound(self):
        """A stale-but-valid state cannot undercount paid sends already in JSONL."""
        pathlib.Path(self.log).write_text(
            '\n'.join(json.dumps({"run_tag": "controlled-run", "request_bytes": 11,
                                  "status": 200}) for _ in range(2)) + '\n',
            encoding="utf-8")
        self.start_budgeted(UPSTREAM_MAX_UPSTREAM_ATTEMPTS=2)
        status, _body = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.srv.seen, [])
        state = self.budget()
        self.assertEqual((state["attempts_charged"], state["request_bytes"]), (2, 22))
        self.assertEqual((state["verdict"], state["reason"]),
                         ("EXCEEDED", "MAX_UPSTREAM_ATTEMPTS"))

    def test_missing_state_before_the_first_completed_row_is_unknown(self):
        """Loss after a charged send but before JSONL is indistinguishable from fresh."""
        self.start_budgeted(bootstrap=False)
        code, _body = self.post()
        self.assertEqual(code, 503)
        self.assertEqual(self.srv.seen, [])
        self.assertFalse(os.path.exists(self.budget_state),
                         "the proxy must never recreate paid state at zero")

    def test_unbounded_or_unstable_budget_reason_is_unknown_before_forward(self):
        self.start_budgeted()
        row = self.budget(); row["reason"] = "x" * 10000
        pathlib.Path(self.budget_state).write_text(json.dumps(row) + "\n", encoding="utf-8")
        status, _body = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.srv.seen, [])

    def test_oversized_otherwise_valid_budget_object_is_unknown_before_forward(self):
        self.start_budgeted()
        with pathlib.Path(self.budget_state).open("a") as target:
            target.write(" " * (1024 * 1024 + 1))
        status, _body = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.srv.seen, [])

    def test_deeply_nested_budget_object_is_unknown_before_forward(self):
        self.start_budgeted()
        pathlib.Path(self.budget_state).write_text("[" * 2000 + "]" * 2000,
                                                   encoding="utf-8")
        status, _body = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.srv.seen, [])

    def test_two_racing_requests_cannot_both_take_the_last_attempt(self):
        """Moving reservation after urlopen lets both paid sends pass a cap of one."""
        self.start_budgeted(UPSTREAM_MAX_UPSTREAM_ATTEMPTS=1)
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.post())) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(len(self.srv.seen), 1)
        self.assertEqual(self.budget()["attempts_charged"], 1)
        self.assertEqual(self.budget()["verdict"], "EXCEEDED")
        self.assertEqual(self.budget()["reason"], "MAX_UPSTREAM_ATTEMPTS")

    def test_each_retry_reserves_bytes_and_attempts_before_transmission(self):
        """Counting only a logical request makes billed retries invisible."""
        self.srv.fail_times = 5
        self.start_budgeted(
            UPSTREAM_RETRY_MAX=5, UPSTREAM_RETRY_BASE_MS=1,
            UPSTREAM_MAX_UPSTREAM_ATTEMPTS=2)
        self.post({"model": "DeepSeek-V4-Flash", "messages": []})
        state = self.budget()
        self.assertEqual(len(self.srv.seen), 2)
        self.assertEqual(state["attempts_charged"], 2)
        self.assertEqual(state["request_bytes"], 2 * len(json.dumps(
            {"model": "DeepSeek-V4-Flash", "messages": []}).encode()))
        self.assertEqual((state["verdict"], state["reason"]),
                         ("EXCEEDED", "MAX_UPSTREAM_ATTEMPTS"))

    def test_request_that_would_cross_byte_cap_is_never_forwarded(self):
        """Checking completed JSONL after the send crosses the paid byte cap."""
        body = {"model": "DeepSeek-V4-Flash", "messages": [{"content": "x" * 400}]}
        self.start_budgeted(UPSTREAM_MAX_REQUEST_BYTES=32)
        self.post(body)
        self.assertEqual(self.srv.seen, [])
        self.assertEqual(self.budget()["request_bytes"], 0)
        self.assertEqual(self.budget()["reason"], "MAX_REQUEST_BYTES")

    def test_only_exact_complete_success_resets_consecutive_failures(self):
        """A malformed stream or wrong identity is a provider failure despite HTTP 200."""
        self.start_budgeted(mode="malformed_sse")
        self.post()
        self.assertEqual(self.budget()["consecutive_provider_failures"], 1)

    def test_exact_json_success_resets_prior_provider_failure(self):
        """The reset is earned by a 2xx response carrying the expected identity."""
        self.srv.fail_times = 1
        self.start_budgeted(UPSTREAM_RETRY_MAX=1, UPSTREAM_RETRY_BASE_MS=1)
        self.post()
        # The proxy updates the completed-attempt state after relaying the
        # response bytes.  Observe that asynchronous durability boundary
        # boundedly instead of racing the handler's finally block.
        deadline = time.time() + 5
        while self.budget()["consecutive_provider_failures"] != 0 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.budget()["consecutive_provider_failures"], 0)

    def test_missing_or_corrupt_existing_budget_state_never_restarts_at_zero(self):
        """After evidence exists, loss of reservation state is unknown, not free."""
        pathlib.Path(self.log).write_text('{"run_tag":"controlled-run","status":200}\n')
        self.start_budgeted(bootstrap=False)
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.srv.seen, [])
        self.assertFalse(pathlib.Path(self.budget_state).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
