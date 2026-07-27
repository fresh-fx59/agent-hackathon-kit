"""Shared plumbing for the mock corporate services (tracker/quality/forge/tms).

Stdlib only (Python >= 3.9).  Gives every mock the same three pieces:

  App    -- a tiny JSON route table with {param} path placeholders
  serve  -- start an App on a background thread (port=0 => ephemeral, for tests)
  Store  -- in-memory data seeded from an optional seed.json, with id counters

Handler contract (every route function):

    def get_issue(params, query, body):
        # params: dict of path placeholders          {"id": "TRK-1"}
        # query:  dict of query-string values        {"status": "open"}  (first value per key)
        # body:   parsed JSON request body or None
        return obj                  # -> 200 with JSON body
        return (201, obj)           # -> explicit status
        raise HttpError(404, "no such issue")   # -> {"error": "..."}

All mocks bind 127.0.0.1 only and answer GET /health -> {"ok": true} for free.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class HttpError(Exception):
    """Raise from a handler to return a JSON error response."""

    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status
        self.message = message


class App(object):
    """Route table: app.get('/issues/{id}', fn) etc."""

    def __init__(self, name="mock"):
        self.name = name
        self.routes = []  # list of (method, compiled_regex, fn)
        # Every mock exposes a health probe out of the box.
        self.get("/health", lambda params, query, body: {"ok": True})

    def route(self, method, pattern, fn):
        """Register fn for METHOD pattern; '{name}' segments become params."""
        parts = []
        for piece in re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", pattern):
            if piece.startswith("{") and piece.endswith("}"):
                parts.append("(?P<%s>[^/]+)" % piece[1:-1])
            else:
                parts.append(re.escape(piece))
        self.routes.append((method.upper(), re.compile("^" + "".join(parts) + "$"), fn))
        return fn

    def get(self, pattern, fn):
        return self.route("GET", pattern, fn)

    def post(self, pattern, fn):
        return self.route("POST", pattern, fn)

    def patch(self, pattern, fn):
        return self.route("PATCH", pattern, fn)

    def put(self, pattern, fn):
        return self.route("PUT", pattern, fn)

    def delete(self, pattern, fn):
        return self.route("DELETE", pattern, fn)

    def match(self, method, path):
        """Return (fn, params) for the first matching route, else None."""
        for route_method, regex, fn in self.routes:
            if route_method != method.upper():
                continue
            hit = regex.match(path)
            if hit:
                return fn, hit.groupdict()
        return None


def _make_handler_class(app):
    """Build a BaseHTTPRequestHandler subclass bound to one App."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-hackathon-kit/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass  # keep mock output quiet; the apps print their own one-liner

        def _write_json(self, status, obj):
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return None
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                raise HttpError(400, "invalid JSON body")

        def _dispatch(self, method):
            url = urlparse(self.path)
            found = app.match(method, url.path)
            if found is None:
                self._write_json(404, {"error": "not found: %s %s" % (method, url.path)})
                return
            fn, params = found
            # parse_qs gives lists; handlers almost always want single values
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                body = self._read_body()
                result = fn(params, query, body)
            except HttpError as exc:
                self._write_json(exc.status, {"error": exc.message})
                return
            except Exception as exc:  # bug in a handler -> visible 500
                self._write_json(500, {"error": "internal error: %s" % (exc,)})
                return
            if isinstance(result, tuple):
                status, obj = result
            else:
                status, obj = 200, result
            self._write_json(status, obj)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def serve(app, port=0, host="127.0.0.1"):
    """Start `app` on a daemon thread; return the running ThreadingHTTPServer.

    port=0 binds an ephemeral port (use in tests -- never hardcode 8801-8804
    there); read the real one from server.server_address[1].
    Call server.shutdown() when done.
    """
    httpd = ThreadingHTTPServer((host, port), _make_handler_class(app))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name=app.name + "-http")
    thread.daemon = True
    thread.start()
    return httpd


def run(app, default_port, port_env=None):
    """Blocking entry point for `python3 app.py`: bind, announce, serve forever.

    Respects the port override env var (e.g. TRACKER_PORT) when given.
    """
    port = default_port
    if port_env and os.environ.get(port_env):
        port = int(os.environ[port_env])
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler_class(app))
    httpd.daemon_threads = True
    print("[%s] listening on http://127.0.0.1:%d" % (app.name, httpd.server_address[1]),
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


class Store(object):
    """In-memory store: optional seed.json + per-name incrementing id counters."""

    def __init__(self, seed_path=None):
        self.data = {}
        self._counters = {}
        if seed_path and os.path.exists(seed_path):
            with open(seed_path, "r", encoding="utf-8") as fh:
                self.data = json.load(fh)

    def next_id(self, name, start=1):
        """Return the next integer id for counter `name` (first call -> start)."""
        value = self._counters.get(name, start - 1) + 1
        self._counters[name] = value
        return value

    def bump_counter(self, name, value):
        """Ensure next_id(name) returns at least value+1 (call after seeding)."""
        self._counters[name] = max(self._counters.get(name, 0), value)
