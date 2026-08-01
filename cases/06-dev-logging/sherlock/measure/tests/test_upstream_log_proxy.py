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

    def lines(self):
        for _ in range(100):                       # the write is off the hot path
            if os.path.exists(self.log):
                with open(self.log, encoding="utf-8") as fh:
                    if fh.read().strip():
                        break
            time.sleep(0.05)
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
