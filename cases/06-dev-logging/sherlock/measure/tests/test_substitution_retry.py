#!/usr/bin/env python3
"""The wrong-model answer is DISCARDED and re-asked — never tolerated, never relayed.

WHY THIS FILE EXISTS. `[次]deepseek-v4-flash` substitutes a `deepseek-v4-pro`
answer on roughly 2 % of calls (measured 2026-08-26: 1 in 51 paid calls). The
lane guard aborted the run on that call, correctly — but at 2 % a 180-call run
meets a substitution with ~97 % probability, so a strict per-call abort means
the run can never finish and ~50 good calls are burnt every attempt.

Relaxing the guard into a tolerance is not the fix: a report partly built on a
model we did not request is invalid, and a threshold only moves the cliff. The
fix is that the proxy throws the wrong-model response away and re-issues the
request. Bounded, fail-closed, and audited: every discard is its own ledger row
flagged `discarded_substitution`, none of them count as a call, and when the cap
is spent the lane trips exactly as before.

EVERY REAL CALL ON THIS LANE IS A STREAM (all 51 rows of the v38 ledger, the
substituted one included), so the streaming case is the case. It works by
holding the head of the stream back until the first `data:` chunk names the
model — measured ttft on that run was 0 ms, so the hold costs nothing.

Everything here runs against a STUB upstream. No metered tokens.

    python3 measure/tests/test_substitution_retry.py
"""
import json
import os
import re
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
AUDIT = os.path.join(MEASURE, "lane-audit.py")
LANE_SH = os.path.join(MEASURE, "upstream-lane.sh")
sys.path.insert(0, MEASURE)
from lane_guard import audit_ledger                                # noqa: E402

PINNED = "[SP]deepseek-v4-flash"
FLASH = "deepseek-v4-flash-0731"
PRO = "deepseek-v4-pro"


class Stub(BaseHTTPRequestHandler):
    """Plays the provider. `server.script` names the model per call, last repeats."""

    def log_message(self, *a):
        pass

    def _model(self):
        script = self.server.script
        return script[min(self.server.seen - 1, len(script) - 1)]

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen += 1
        model = self._model()
        usage = {"prompt_tokens": self.server.prompt_tokens,
                 "prompt_tokens_details": {"cached_tokens": self.server.cached_tokens}}
        if not self.server.stream:
            payload = json.dumps({
                "model": model, "usage": usage,
                "choices": [{"message": {"role": "assistant",
                                         "content": "answer from %s" % model}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = []
        if self.server.anonymous_head:
            # A provider that says nothing about the model in its first chunk.
            chunks.append({"choices": [{"delta": {"content": "thinking"}}]})
        chunks.append({"model": model,
                       "choices": [{"delta": {"role": "assistant",
                                              "content": "answer from %s" % model}}]})
        chunks.append({"model": model, "usage": usage,
                       "choices": [{"delta": {}, "finish_reason": "stop"}]})
        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):

    def setUp(self):
        self.up_port = free_port()
        self.srv = HTTPServer(("127.0.0.1", self.up_port), Stub)
        self.srv.seen = 0
        self.srv.script = [FLASH]
        self.srv.stream = True
        self.srv.anonymous_head = False
        self.srv.prompt_tokens = 1000
        self.srv.cached_tokens = 800
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "upstream.jsonl")
        self.abort = os.path.join(self.tmp, "upstream.abort.json")
        self.px_port = free_port()
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream:
                    stream.close()
        self.srv.shutdown()
        self.srv.server_close()

    def start(self, retries="2", **extra):
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log, LISTEN_PORT=str(self.px_port),
                   UPSTREAM_LANE_ABORT=self.abort,
                   UPSTREAM_EXPECTED_RETURNED_IDENTITY=PINNED,
                   UPSTREAM_SUBSTITUTION_RETRY_MAX=retries,
                   RUN_TAG="substitution-test")
        env.update(extra)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(200):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def call(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": "deepseek-v4-flash", "messages": [],
                             "stream": True}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.getcode(), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def rows(self, expected=None):
        """Ledger rows, waiting for `expected` of them.

        A STREAMED row is recorded in the relay's `finally`, i.e. AFTER the
        last byte has already reached the client, so reading the ledger the
        instant a call returns is a race the test loses under load. Same reason
        `marker()` polls.
        """
        for _ in range(200):
            try:
                with open(self.log, encoding="utf-8") as fh:
                    rows = [json.loads(line) for line in fh if line.strip()]
            except (OSError, ValueError):
                rows = []
            if expected is None or len(rows) >= expected:
                return rows
            time.sleep(0.02)
        return rows

    def marker(self, expected_reason=None):
        """The abort marker, waited for.

        On the pre-retry path the lane trips AFTER the client already has its
        200, so the marker can be a few milliseconds behind the response.
        """
        for _ in range(200):
            if os.path.exists(self.abort):
                try:
                    with open(self.abort, encoding="utf-8") as fh:
                        return json.load(fh)
                except (OSError, ValueError):
                    pass
            time.sleep(0.02)
        self.fail("no abort marker at %s" % self.abort)


class TheRetry(Base):
    """Requirement 1: the client gets the FLASH answer and the run lives."""

    def _one_pro_then_flash(self):
        self.srv.script = [PRO, FLASH]
        self.start()
        status, body = self.call()
        self.assertEqual(status, 200)
        # THE WHOLE POINT: the flash answer, and not a byte of the pro one.
        self.assertIn(b"answer from " + FLASH.encode(), body)
        self.assertNotIn(PRO.encode(), body)
        self.assertEqual(self.srv.seen, 2, "the request was not re-issued")
        rows = self.rows(2)
        self.assertFalse(os.path.exists(self.abort), "the run was aborted anyway")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["returned_model"], PRO)
        self.assertIs(rows[0]["discarded_substitution"], True)
        self.assertEqual(rows[0]["substitution_attempt"], 1)
        self.assertIs(rows[0]["substitution_retry_exhausted"], False)
        self.assertEqual(rows[1]["returned_model"], FLASH)
        self.assertNotIn("discarded_substitution", rows[1],
                         "a clean row must keep the ledger shape it always had")

    def test_a_streamed_substitution_is_discarded_and_re_asked(self):
        self.srv.stream = True
        self._one_pro_then_flash()

    def test_a_whole_body_substitution_is_discarded_and_re_asked(self):
        self.srv.stream = False
        self._one_pro_then_flash()

    def test_two_substitutions_in_a_row_still_land_on_flash(self):
        self.srv.script = [PRO, PRO, FLASH]
        self.start(retries="2")
        status, body = self.call()
        self.assertEqual(status, 200)
        self.assertIn(b"answer from " + FLASH.encode(), body)
        self.assertEqual(self.srv.seen, 3)
        rows = self.rows(3)
        self.assertFalse(os.path.exists(self.abort))
        self.assertEqual([r.get("discarded_substitution") for r in rows],
                         [True, True, None])

    def test_a_clean_lane_never_re_asks(self):
        self.srv.script = [FLASH, "deepseek-v4-flash", "DeepSeek-V4-Flash-0731"]
        self.start()
        for _ in range(3):
            self.assertEqual(self.call()[0], 200)
        self.assertEqual(self.srv.seen, 3, "a healthy call was re-issued")
        self.assertEqual([r.get("discarded_substitution") for r in self.rows(3)],
                         [None, None, None])


class FailClosed(Base):
    """Requirement 2: the cap is spent -> abort as today, and never relay it."""

    def test_the_cap_trips_the_lane_and_no_wrong_model_body_is_returned(self):
        self.srv.script = [PRO]                       # every attempt substitutes
        self.start(retries="2")
        status, body = self.call()
        self.assertEqual(self.srv.seen, 3, "cap=2 must allow 1 + 2 attempts, no more")
        # 403 is the lane refusal, byte-for-byte the status the guard already
        # used. The client learns the lane is dead; it never learns what pro said.
        self.assertEqual(status, 403)
        self.assertIn(b"RETURNED_MODEL_FAMILY_MISMATCH", body)
        self.assertNotIn(b"answer from", body)
        self.assertNotIn(PRO.encode(), body)
        marker = self.marker()
        self.assertEqual(marker["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn(PRO, marker["detail"])
        rows = self.rows(3)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["substitution_retry_exhausted"] for r in rows],
                         [False, False, True])
        # …and the lane is shut: the next call never reaches the provider.
        self.assertEqual(self.call()[0], 403)
        self.assertEqual(self.srv.seen, 3)

    def test_the_default_is_the_old_abort_on_the_first_substitution(self):
        """Off unless a runner asks — the same rule as UPSTREAM_RETRY_MAX."""
        self.srv.script = [PRO]
        self.start(retries="0")
        self.assertEqual(self.srv.seen, 0)
        status, _ = self.call()
        self.assertEqual(self.srv.seen, 1, "retry ran with the switch at 0")
        self.assertIn(status, (200, 403))
        self.assertEqual(self.marker()["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_a_stream_that_never_names_a_model_is_still_delivered_whole(self):
        """Holding is a delay, never a loss — and never a retry on no evidence."""
        self.srv.script = [None]
        self.srv.stream = True
        self.start(retries="2", UPSTREAM_SUBSTITUTION_HOLD_MS="200")
        status, body = self.call()
        self.assertEqual(status, 200)
        self.assertIn(b"answer from None", body)
        self.assertEqual(self.srv.seen, 1)
        self.assertFalse(os.path.exists(self.abort))

    def test_the_head_is_held_only_until_the_model_is_named(self):
        """A first chunk with no model must not stop the retry working."""
        self.srv.anonymous_head = True
        self.srv.script = [PRO, FLASH]
        self.start(retries="2")
        status, body = self.call()
        self.assertEqual(status, 200)
        self.assertNotIn(PRO.encode(), body)
        self.assertIn(b"answer from " + FLASH.encode(), body)
        # The held preamble is delivered too: the client sees the whole stream.
        self.assertIn(b"thinking", body)
        self.assertEqual(self.srv.seen, 2)


class Accounting(Base):
    """Requirement 3: a discard is money, not a measurement."""

    def test_discards_do_not_inflate_the_call_count_or_the_cache_rate(self):
        summary = {}
        rows = []
        # 30 accepted calls at a healthy 80 %, and 4 discarded pro answers with
        # NO cache hit at all — the exact shape that drags a naive rate down.
        for _ in range(30):
            rows.append({"requested_model": PINNED, "returned_model": FLASH,
                         "status": 200,
                         "usage": {"prompt_tokens": 1000,
                                   "prompt_tokens_details": {"cached_tokens": 800}}})
        for _ in range(4):
            rows.append({"requested_model": PINNED, "returned_model": PRO,
                         "status": 200, "discarded_substitution": True,
                         "substitution_attempt": 1,
                         "usage": {"prompt_tokens": 20000,
                                   "prompt_tokens_details": {"cached_tokens": 0}}})
        ledger = os.path.join(self.tmp, "mixed.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        self.assertIsNone(audit_ledger(ledger, expected_identity=PINNED,
                                       summary=summary))
        self.assertEqual(summary["accepted_calls"], 30)
        self.assertEqual(summary["prompt_tokens"], 30000)
        self.assertEqual(summary["cached_tokens"], 24000)   # 80.0 %, unpolluted
        self.assertEqual(summary["discarded_substitutions"], 4)
        self.assertEqual(summary["discarded_prompt_tokens"], 80000)
        self.assertEqual(summary["discarded_by_model"], {PRO: 4})
        # Counted in, the rate would be 24000/110000 = 21.8 % — below the 35 %
        # floor. The guard must not fire on money the arm never saw.
        self.assertLess(24000 / 110000.0, 0.35)

    def test_the_flag_cannot_hide_a_good_row(self):
        """Fail closed: `discarded_substitution` is honoured only on a real one."""
        ledger = os.path.join(self.tmp, "liar.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"requested_model": PINNED, "returned_model": FLASH,
                                 "status": 200, "discarded_substitution": True}) + "\n")
        breach = audit_ledger(ledger, expected_identity=PINNED)
        self.assertEqual(breach[0], "LEDGER_MALFORMED")
        self.assertIn("not a way to hide a row", breach[1])

    def test_lane_audit_writes_the_discard_bill_into_an_artifact(self):
        self.srv.script = [PRO, FLASH]
        self.start()
        self.assertEqual(self.call()[0], 200)
        self.rows(2)                       # the accepted row lands after the body
        target = os.path.join(self.tmp, "lane-substitutions.json")
        done = subprocess.run([sys.executable, AUDIT, "--ledger", self.log,
                               "--expected", PINNED, "--no-cache-guard",
                               "--summary-json", target],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(target, encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["discarded_substitutions"], 1)
        self.assertEqual(summary["accepted_calls"], 1)
        self.assertIn("1 discarded substitutions", done.stderr)
        self.assertIn("⚠", done.stderr)


class Wiring(unittest.TestCase):
    """`SHERLOCK_UPSTREAM_RETRY=0` must not be able to switch this off."""

    def test_upstream_lane_turns_the_substitution_retry_on(self):
        with open(LANE_SH, encoding="utf-8") as fh:
            source = fh.read()
        found = re.findall(
            r'UPSTREAM_SUBSTITUTION_RETRY_MAX="\$\{SHERLOCK_SUBSTITUTION_RETRY:-(\d+)\}"',
            source)
        self.assertEqual(len(found), 2,
                         "both proxy launch paths must set the substitution retry")
        for default in found:
            self.assertGreater(int(default), 0, "the lane default disables the retry")

    def test_the_switch_is_not_the_provider_error_retry(self):
        """They are different failures; the paid launchers set the other to 0."""
        with open(PROXY, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('os.environ.get("UPSTREAM_SUBSTITUTION_RETRY_MAX", "0")', source)
        block = source[source.index("UPSTREAM_SUBSTITUTION_RETRY_MAX = int"):]
        self.assertNotIn("UPSTREAM_RETRY_MAX", block.split("\n\n")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
