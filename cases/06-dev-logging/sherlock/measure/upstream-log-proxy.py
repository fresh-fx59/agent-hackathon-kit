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
import re
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
# THE 177,000-TOKEN CEILING WAS A MODEL-ID PARSING ARTIFACT, not a real limit.
# qwen-code sizes the context window from the model id, and its own normalize()
# turns "[SP]deepseek-v4-flash" into "[sp]deepseek-v4-flash", which matches no
# entry in its table and falls back to DEFAULT_TOKEN_LIMIT = 200,000 — from
# which the 177,000 hard limit follows. Drop the prefix and the same table gives
# /^deepseek-v4/ => 1,000,000. Verified by running qwen-code's own normalize().
# linkapi needs the prefix; qwen-code must not see it. So qwen-code is given the
# clean id and this restores the alias on the way out.
UPSTREAM_MODEL = os.environ.get("UPSTREAM_MODEL", "")
# RIDE OUT A PROVIDER BURST. linkapi's 400s are transient and minute-scale —
# measured 2026-08-02, and NOT explained by request size or shape (both
# controlled for, interleaved, 12/12 succeeded at the size and shape that had
# just failed). The problem is that qwen-code's own retry budget is SHORTER than
# a burst: D11 took 4 × 400 at 143 KB then a 200, then 5 × 400 at 171 KB and the
# run was over — 98,515 tokens billed, no row. This proxy is the only thing in
# the path that can wait longer than the client will.
# Off by default (0) so the proxy stays a pass-through unless a runner asks.
UPSTREAM_RETRY_MAX = int(os.environ.get("UPSTREAM_RETRY_MAX", "0") or 0)
UPSTREAM_RETRY_BASE_MS = int(os.environ.get("UPSTREAM_RETRY_BASE_MS", "2000") or 2000)
# Only statuses that are transient on this lane. A 401/404/422 is a real defect
# in the request and retrying it just burns the context again for nothing.
_RETRYABLE = {400, 408, 429, 500, 502, 503, 504}

_BASE_PATH = urlsplit(UPSTREAM_BASE).path.rstrip("/")     # e.g. "/v1"
_LOG_LOCK = threading.Lock()

# Hop-by-hop headers are per-connection and must not be relayed (RFC 7230 §6.1).
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host",
        "content-length", "accept-encoding"}


# A credential must never reach the ledger. This proxy already never logs the
# Authorization header, but `upstream_error` carries the provider's own words, and a
# provider that quotes the caller's key back in a refusal would write it here —
# verbatim, durable, in every run directory. Scrubbing happens in record() rather
# than at each call site so any field added later is covered without being
# remembered. The patterns are deliberately narrow: over-redaction would undo the
# reason this field exists, which is that 60 failures on D04 were bare integers.
_SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<redacted>"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1<redacted>"),
    (re.compile(r"(?i)\b(api[-_]?key|token|secret)(\"?\s*[:=]\s*\"?)"
                r"[A-Za-z0-9._~+/=-]{16,}"), r"\1\2<redacted>"),
)


def _scrub(value):
    if not isinstance(value, str):
        return value
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def record(**row):
    row = {k: _scrub(v) for k, v in row.items()}
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if RUN_TAG:
        row["run_tag"] = RUN_TAG
    line = json.dumps(row, ensure_ascii=False)
    with _LOG_LOCK:                       # ThreadingHTTPServer ⇒ concurrent turns
        with open(UPSTREAM_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# How much of an upstream error body is kept. 300 chars is enough for every
# provider message seen on this lane ("Upstream request failed", a rate-limit
# notice, a context-length refusal) and short enough that a body which echoes
# part of the REQUEST cannot drag corpus lines into the log. It lands in the run
# dir next to the trajectory, and it never contains a header.
_ERR_CHARS = 300


def _err_text(payload):
    """The provider's own words for a refusal, truncated.

    Sixty 400s were recorded on D04 as a bare integer while this very body was
    read and thrown away in the retry path. "Rate limit exceeded" and "invalid
    request" are indistinguishable in a status column, and three separate
    theories about these failures were argued from counts because of it.
    """
    if not payload:
        return None
    return payload.decode("utf-8", "replace").strip()[:_ERR_CHARS] or None


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
        requested = sent = None
        if UPSTREAM_MODEL and body:
            # Rewrite ONLY the model field, and only when the body parses as the
            # object we expect. A proxy that reformats a request it did not
            # fully understand is a proxy that corrupts one silently.
            try:
                obj = json.loads(body)
                if isinstance(obj, dict) and obj.get("model"):
                    requested = obj["model"]
                    obj["model"] = UPSTREAM_MODEL
                    sent = UPSTREAM_MODEL
                    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass
        if requested is None:
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
        state = {"returned_model": None, "tool_call": False, "error": None}
        status = None
        attempt = 0
        while True:
            attempt += 1
            t_try = time.time()
            try:
                resp = urllib.request.urlopen(req, timeout=1800)
                status = resp.getcode()
                break
            except urllib.error.HTTPError as e:
                resp, status = e, e.code
                if (status in _RETRYABLE and attempt <= UPSTREAM_RETRY_MAX):
                    # EVERY attempt is recorded, because every attempt re-uploads
                    # the whole context and is therefore billed. An invisible
                    # retry is the "failed calls are free" mistake all over again.
                    # Read the body BEFORE recording — it was already being read
                    # and discarded here, which is why every 400 in this ledger
                    # is a bare status code with no reason attached.
                    try:
                        why = _err_text(e.read())
                    except Exception:
                        why = None
                    record(requested_model=requested, returned_model=None,
                           tool_call=False, status=status, attempt=attempt,
                           duration_ms=int((time.time() - t_try) * 1000),
                           sent_model=sent, request_bytes=len(body),
                           path=self.path, stream=False, upstream_error=why)
                    time.sleep(min(60.0, (UPSTREAM_RETRY_BASE_MS / 1000.0)
                                   * (2 ** (attempt - 1))))
                    req = urllib.request.Request(self._upstream_url(),
                                                 data=body or None,
                                                 headers=headers,
                                                 method=self.command)
                    continue
                break
            except Exception as e:                   # DNS, TLS, refused, timeout
                record(requested_model=requested, returned_model=None,
                       tool_call=False, status=None, error=str(e)[:300],
                       attempt=attempt,
                       duration_ms=int((time.time() - t0) * 1000), sent_model=sent,
                       request_bytes=len(body), path=self.path, stream=False)
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
                   returned_model=state["returned_model"], attempt=attempt,
                   tool_call=state["tool_call"], status=status,
                   duration_ms=int((time.time() - t0) * 1000), sent_model=sent,
                   request_bytes=len(body), path=self.path, stream=streaming,
                   upstream_error=state["error"])

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
        # The body is read exactly once and then relayed verbatim, so recording
        # the reason cannot consume the client's copy — a proxy that logs why and
        # hands the client an empty 400 has moved the blindness, not removed it.
        if status and status >= 400:
            state["error"] = _err_text(payload)
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
