#!/usr/bin/env python3
"""Provider-free contract for the proxy's live, concurrent request snapshot."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PROXY = pathlib.Path(__file__).resolve().parents[2] / "measure" / "upstream-log-proxy.py"


class _BlockedUpstream(BaseHTTPRequestHandler):
    received = []
    lock = threading.Lock()
    arrived = threading.Event()
    release = [threading.Event(), threading.Event()]

    def log_message(self, *args):
        pass

    def do_POST(self):
        with self.lock:
            slot = len(self.received)
            self.received.append(self.rfile.read(int(self.headers["Content-Length"])))
            if len(self.received) == 2:
                self.arrived.set()
        self.release[slot].wait(10)
        body = json.dumps({"model": "returned-fixture", "choices": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UpstreamInflightTests(unittest.TestCase):
    def setUp(self):
        _BlockedUpstream.received = []
        _BlockedUpstream.arrived.clear()
        for event in _BlockedUpstream.release:
            event.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _BlockedUpstream)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        import socket
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); self.port = sock.getsockname()[1]; sock.close()
        self.inflight = self.root / "upstream-inflight.json"
        self.receipts = self.root / "upstream.jsonl"
        env = os.environ | {"UPSTREAM_BASE": "http://127.0.0.1:%d/v1" % self.upstream.server_port,
                            "UPSTREAM_LOG": str(self.receipts), "UPSTREAM_INFLIGHT": str(self.inflight),
                            "LISTEN_PORT": str(self.port), "RUN_TAG": "fixture-run", "RUN_ATTEMPT": "7",
                            "UPSTREAM_MODEL": "[SP]fixture", "NO_PROXY": "*", "no_proxy": "*"}
        self.proxy = subprocess.Popen([sys.executable, str(PROXY)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_for(lambda: self._get("/healthz") == b'{"ok":true}')

    def tearDown(self):
        for event in _BlockedUpstream.release:
            event.set()
        self.proxy.terminate(); self.proxy.wait(timeout=5)
        self.upstream.shutdown(); self.upstream.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        return urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=2).read()

    def _post(self):
        request = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % self.port,
            data=b'{"model":"client-fixture","messages":[]}', method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret-token"})
        return urllib.request.urlopen(request, timeout=15).read()

    def _wait_for(self, predicate):
        for _ in range(100):
            try:
                if predicate(): return
            except Exception:
                pass
            time.sleep(.03)
        self.fail("timed out waiting for condition")

    def test_inflight_tracks_each_concurrent_request_until_its_own_completion(self):
        first = threading.Thread(target=self._post); second = threading.Thread(target=self._post)
        first.start(); second.start()
        self.assertTrue(_BlockedUpstream.arrived.wait(5))
        self._wait_for(lambda: len(json.loads(self.inflight.read_text())["requests"]) == 2)
        requests = json.loads(self.inflight.read_text())["requests"]
        self.assertEqual({row["run_tag"] for row in requests.values()}, {"fixture-run"})
        self.assertEqual({row["attempt"] for row in requests.values()}, {"7"})
        self.assertTrue(all(row["request_bytes"] > 2 and row["pid"] and row["proxy_instance"] for row in requests.values()))
        _BlockedUpstream.release[0].set()
        self._wait_for(lambda: len(json.loads(self.inflight.read_text())["requests"]) == 1)
        _BlockedUpstream.release[1].set(); first.join(5); second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self._wait_for(lambda: not self.inflight.exists())
        receipt = json.loads(self.receipts.read_text().splitlines()[-1])
        self.assertEqual(receipt["requested_model"], "client-fixture")
        self.assertEqual(receipt["returned_model"], "returned-fixture")
        self.assertNotIn("test-secret-token", self.receipts.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
