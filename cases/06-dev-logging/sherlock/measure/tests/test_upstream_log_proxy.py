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


if __name__ == "__main__":
    unittest.main(verbosity=2)
