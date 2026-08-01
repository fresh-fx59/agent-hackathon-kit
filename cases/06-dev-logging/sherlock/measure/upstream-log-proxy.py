#!/usr/bin/env python3
"""Name the model that actually answered, one JSONL line per request.

`[SP]deepseek-v4-flash` is an ALIAS, not a model. Measured 2026-08-01 over 40
byte-identical requests carrying tool-call history, it answered as two distinct
identities that are ~19x apart on whether they emit a tool call at all:

    deepseek-v4-flash (lower)    1/21 tool calls =  4.8 %
    DeepSeek-V4-Flash (caps)    17/19 tool calls = 89.5 %

The arm under test IS a tool-execution mechanism (`logmap.py`, `citecheck`), so
which upstream a turn lands on can decide whether the arm runs — independent of
the skill. And qwen-code stamps the REQUESTED alias on every assistant message,
never the returned name, so no recorded row could be attributed to an upstream.
That is not fixable by re-reading runs; it has to be captured as it happens.

`run-case.sh` already takes SHERLOCK_BASE_URL from the environment, so this is a
URL swap rather than a harness change:

    UPSTREAM_BASE=https://linkapi.ai/v1 UPSTREAM_LOG=…/upstream.jsonl \
    LISTEN_PORT=8791 python3 measure/upstream-log-proxy.py &
    SHERLOCK_BASE_URL=http://127.0.0.1:8791/v1 bash measure/one-defect.sh v11 D04

It is deliberately dumb: it forwards bytes, it never rewrites a request, and it
never logs the Authorization header. If it cannot parse a response it records
`returned_model: null` rather than guessing — an unmeasured value is null, never
a default. → measure/probes/upstream-split.sh
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://linkapi.ai/v1").rstrip("/")
UPSTREAM_LOG = os.environ.get("UPSTREAM_LOG", "upstream.jsonl")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8791"))
RUN_TAG = os.environ.get("RUN_TAG", "")

_BASE_PATH = urlsplit(UPSTREAM_BASE).path.rstrip("/")     # e.g. "/v1"
_LOG_LOCK = threading.Lock()

# Hop-by-hop headers are per-connection and must not be relayed (RFC 7230 §6.1).
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host",
        "content-length", "accept-encoding"}


def record(**row):
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if RUN_TAG:
        row["run_tag"] = RUN_TAG
    line = json.dumps(row, ensure_ascii=False)
    with _LOG_LOCK:                       # ThreadingHTTPServer ⇒ concurrent turns
        with open(UPSTREAM_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _scan_obj(obj, state):
    """Pull the two things we care about out of one response object."""
    if not isinstance(obj, dict):
        return
    if obj.get("model") and not state["returned_model"]:
        state["returned_model"] = obj["model"]
    for ch in obj.get("choices") or []:
        if not isinstance(ch, dict):
            continue
        for key in ("message", "delta"):
            part = ch.get(key)
            if isinstance(part, dict) and part.get("tool_calls"):
                state["tool_call"] = True


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "upstream-log-proxy/1"

    def log_message(self, *a):
        pass                                        # the JSONL is the log

    def do_GET(self):
        if self.path.rstrip("/") in ("/healthz", "/health"):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._relay(b"")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._relay(self.rfile.read(n) if n else b"")

    # ------------------------------------------------------------------
    def _upstream_url(self):
        path = self.path
        if _BASE_PATH and path.startswith(_BASE_PATH):
            path = path[len(_BASE_PATH):]
        return UPSTREAM_BASE + (path if path.startswith("/") else "/" + path)

    def _relay(self, body):
        t0 = time.time()
        requested = None
        try:
            requested = (json.loads(body or b"{}") or {}).get("model")
        except Exception:
            pass

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in _HOP}
        # identity so the body stays scannable; the client gets it un-encoded,
        # which is what every OpenAI-compatible client already accepts.
        headers["Accept-Encoding"] = "identity"
        req = urllib.request.Request(self._upstream_url(), data=body or None,
                                     headers=headers, method=self.command)
        state = {"returned_model": None, "tool_call": False}
        status = None
        try:
            resp = urllib.request.urlopen(req, timeout=1800)
            status = resp.getcode()
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
        except Exception as e:                       # DNS, TLS, refused, timeout
            record(requested_model=requested, returned_model=None,
                   tool_call=False, status=None, error=str(e)[:300],
                   duration_ms=int((time.time() - t0) * 1000),
                   path=self.path, stream=False)
            self.send_response(502)
            payload = json.dumps({"error": {"message": "proxy: %s" % e}}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        ctype = (resp.headers.get("Content-Type") or "")
        streaming = "text/event-stream" in ctype.lower()
        try:
            if streaming:
                self._pump_stream(resp, state)
            else:
                self._pump_whole(resp, status, state)
        finally:
            record(requested_model=requested,
                   returned_model=state["returned_model"],
                   tool_call=state["tool_call"], status=status,
                   duration_ms=int((time.time() - t0) * 1000),
                   path=self.path, stream=streaming)

    def _relay_headers(self, resp, status, extra=()):
        self.send_response(status)
        for k, v in resp.headers.items():
            if k.lower() in _HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        for k, v in extra:
            self.send_header(k, v)

    def _pump_whole(self, resp, status, state):
        payload = resp.read()
        try:
            _scan_obj(json.loads(payload.decode("utf-8", "replace")), state)
        except Exception:
            pass
        self._relay_headers(resp, status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _pump_stream(self, resp, state):
        # No Content-Length is known up front and chunked framing is one more
        # thing to get wrong, so the response ends at connection close.
        self._relay_headers(resp, 200, extra=[("Connection", "close")])
        self.end_headers()
        self.close_connection = True
        for raw in resp:
            self.wfile.write(raw)
            self.wfile.flush()
            line = raw.strip()
            if line.startswith(b"data:"):
                chunk = line[5:].strip()
                if chunk and chunk != b"[DONE]":
                    try:
                        _scan_obj(json.loads(chunk.decode("utf-8", "replace")), state)
                    except Exception:
                        pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Proxy)
    srv.daemon_threads = True
    sys.stderr.write("upstream-log-proxy: 127.0.0.1:%d -> %s (log %s)\n"
                     % (LISTEN_PORT, UPSTREAM_BASE, UPSTREAM_LOG))
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
