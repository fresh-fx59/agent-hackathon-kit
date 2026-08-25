#!/usr/bin/env python3
"""Defect 2 of the 2026-08-25 v36 audit: a dead stream is ridden to the end.

MEASURED on sherlock-winevtx-runs-v36-full-r1. 20 of 185 calls returned HTTP 200
with completion_tokens == 0. NONE of them was empty on the wire — every one is a
well-formed SSE stream ending in `data: [DONE]`. Three signatures:

  * 12 carry one usage-only chunk with "choices":[] and nothing else;
  *  5 are the model answering with an empty string, finish_reason "stop";
  *  3 carry a PROVIDER REFUSAL spliced into the 200 body:
     data: {"error":{"message":"Concurrency limit exceeded for account, please
     retry later","type":"rate_limit_error"}}

Cost: 2,094,389 prompt tokens billed for zero output (11.3% of the run) and
2,927 s of wall clock. Two calls ran 311 s and 502 s.

TWO root causes, and neither is "the provider is flaky":

  1. `urlopen(req, timeout=1800)` sets a PER-SOCKET-READ deadline, not a call
     budget, and `for raw in resp:` never checks elapsed time. A stream that
     sends nothing is waited on for up to half an hour. The ~126 s client
     timeout everyone assumed was capping this never applies — the proxy owns
     the upstream socket.
  2. `_scan_obj` never looks at a top-level `error` key, so a rate-limit refusal
     is recorded as a clean 200 with `upstream_error: null`. The run's ledger
     said "0 non-200s" while the provider had refused three times.

DELIBERATELY NOT FIXED BY BLACKLIST. `returned_model == "[SP]deepseek-v4-flash"`
predicted the empty result 11 times out of 11 (and `null` 3 of 3), which is a
perfect signal — but keying the abort on it would be a special case that moves
the cliff the moment the provider picks a different alias. The deadline below
catches every signature, including ones not yet seen; the identity is recorded
as a diagnostic, never as a trigger.

`ttft_ms` and `content_events` are recorded because the run could not answer
"how long does a HEALTHY call take to first token?" — so the default here is an
upper bound from the data we have, and the next run can tighten it from measurement.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROXY = pathlib.Path(__file__).resolve().parents[2] / "measure" / "upstream-log-proxy.py"

USAGE_ONLY = (b'data: {"id":"","object":"chat.completion.chunk","model":'
              b'"[SP]deepseek-v4-flash","choices":[],"usage":'
              b'{"prompt_tokens":143105,"completion_tokens":0}}\n\n'
              b'data: [DONE]\n\n')
REFUSAL = (b'data: {"error":{"message":"Concurrency limit exceeded for account,'
           b' please retry later","type":"rate_limit_error"}}\n\n'
           b'data: {"id":"","object":"chat.completion.chunk","model":"",'
           b'"choices":[],"usage":{"prompt_tokens":158118,"completion_tokens":0}}\n\n'
           b'data: [DONE]\n\n')
GOOD = (b'data: {"id":"x","model":"deepseek-v4-flash-0731","choices":'
        b'[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"id":"x","model":"deepseek-v4-flash-0731","choices":'
        b'[{"delta":{"content":"!"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\n'
        b'data: [DONE]\n\n')


class Upstream(BaseHTTPRequestHandler):
    """Serves whatever `Upstream.script` says, with an optional silent gap."""
    script = (0.0, GOOD)

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        delay, body = self.script
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        # The whole point: headers land at once, the body does not.
        time.sleep(delay)
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except Exception:
            pass


class ProxyCase(unittest.TestCase):
    ENV = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        sock = socket.socket(); sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]; sock.close()
        self.receipts = self.root / "upstream.jsonl"
        env = os.environ | {
            "UPSTREAM_BASE": "http://127.0.0.1:%d/v1" % self.upstream.server_port,
            "UPSTREAM_LOG": str(self.receipts),
            "LISTEN_PORT": str(self.port),
            "RUN_TAG": "fixture-run", "RUN_ATTEMPT": "1",
            "NO_PROXY": "*", "no_proxy": "*"}
        env.update(self.ENV)
        self.proxy = subprocess.Popen([sys.executable, str(PROXY)], env=env,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self._wait(lambda: urllib.request.urlopen(
            "http://127.0.0.1:%d/healthz" % self.port, timeout=2).read() == b'{"ok":true}')

    def tearDown(self):
        self.proxy.terminate(); self.proxy.wait(timeout=5)
        self.upstream.shutdown(); self.upstream.server_close()
        self.tmp.cleanup()

    def _wait(self, predicate):
        for _ in range(200):
            try:
                if predicate(): return
            except Exception:
                pass
            time.sleep(.03)
        self.fail("timed out waiting for condition")

    def post(self, timeout=30):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.port,
            data=b'{"model":"client-fixture","messages":[],"stream":true}',
            method="POST", headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer test-token"})
        started = time.time()
        try:
            urllib.request.urlopen(req, timeout=timeout).read()
        except Exception:
            pass
        return time.time() - started

    def receipt(self):
        self._wait(lambda: self.receipts.exists() and self.receipts.read_text().strip())
        return json.loads(self.receipts.read_text().splitlines()[-1])


class TestFirstTokenDeadline(ProxyCase):
    ENV = {"UPSTREAM_FIRST_TOKEN_MS": "800"}

    def test_a_silent_stream_is_cut_at_the_deadline(self):
        """A stream with no content must not be ridden to the socket timeout."""
        Upstream.script = (6.0, USAGE_ONLY)
        elapsed = self.post()
        self.assertLess(elapsed, 4.0,
                        "a dead stream took %.1fs; the deadline was 0.8s" % elapsed)
        self.assertEqual(self.receipt().get("upstream_error"),
                         "first_token_deadline_exceeded")

    def test_a_healthy_stream_is_never_cut(self):
        """The deadline must not kill a slow but living call."""
        Upstream.script = (0.2, GOOD)
        self.post()
        row = self.receipt()
        self.assertIsNone(row.get("upstream_error"), row)
        self.assertEqual((row.get("usage") or {}).get("completion_tokens"), 2)

    def test_ttft_and_content_events_are_recorded(self):
        """The run could not answer 'how slow is healthy?'. Now it can."""
        Upstream.script = (0.2, GOOD)
        self.post()
        row = self.receipt()
        self.assertGreater(row.get("content_events") or 0, 0, row)
        self.assertIsInstance(row.get("ttft_ms"), int, row)


class TestDeadlineOffByDefault(ProxyCase):
    def test_pass_through_when_unset(self):
        """Same rule as UPSTREAM_RETRY_MAX: the proxy stays a pass-through."""
        Upstream.script = (0.0, USAGE_ONLY)
        self.post()
        self.assertIsNone(self.receipt().get("upstream_error"))


class TestProviderErrorInsideA200(ProxyCase):
    def test_a_refusal_spliced_into_a_200_is_recorded_as_an_error(self):
        """The v36 ledger said '0 non-200s' while the provider refused 3 times."""
        Upstream.script = (0.0, REFUSAL)
        self.post()
        row = self.receipt()
        self.assertEqual(row.get("status"), 200)
        self.assertTrue(row.get("upstream_error"),
                        "a rate_limit_error in the body must not read as clean")
        self.assertIn("rate_limit", (row.get("upstream_error") or "").lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
