#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROBE = ROOT / "measure" / "probes" / "lane-health.sh"
MANIFEST = ROOT / "eval" / "bench" / "run-manifest.py"
spec = importlib.util.spec_from_file_location("run_manifest", MANIFEST)
run_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_manifest)


class FakeProvider:
    def __init__(self, mode="healthy"):
        self.mode = mode
        mode = mode

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length", "0"))
                self.rfile.read(n)
                if mode == "non200":
                    body = b'{"error":{"message":"dummy provider failure"}}'
                    self.send_response(503)
                elif mode == "malformed":
                    body = b'data: {"model":"DeepSeek-V4-Flash"}\ndata: not-json\n'
                    self.send_response(200)
                elif mode == "malformed_json":
                    body = b"not-json"
                    self.send_response(200)
                else:
                    model = "Wrong-Model" if mode == "wrong" else "DeepSeek-V4-Flash"
                    body = ("data: " + json.dumps({"model": model}) +
                            "\ndata: [DONE]\n").encode()
                    self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class LaneHealthReceiptTests(unittest.TestCase):
    def run_probe(self, mode="healthy", **extra):
        provider = FakeProvider(mode)
        self.addCleanup(provider.close)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        receipt = pathlib.Path(temp.name) / "health.json"
        env = dict(os.environ)
        env.update(SHERLOCK_API_KEY="dummy-secret", PROBE_BASE_URL=provider.url,
                   PROBE_REPS="1", PROBE_SIZES_KB="100 250 400",
                   PROBE_RECEIPT_PATH=str(receipt), PROBE_ENDPOINT_LABEL="local-test",
                   PROBE_LANE="local-lane", PROBE_PROVIDER="local-provider")
        env.update(extra)
        proc = subprocess.run(["bash", str(PROBE)], env=env, text=True,
                              capture_output=True, timeout=30)
        return proc, receipt

    def test_healthy_receipt_satisfies_manifest_health_contract(self):
        proc, receipt = self.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(receipt.exists(), proc.stdout + proc.stderr)
        row = json.loads(receipt.read_text())
        self.assertEqual(row["verdict"], "HEALTHY")
        run_manifest.validate_health(str(receipt), "local-lane", "local-provider",
                                     "[SP]deepseek-v4-flash-0731", "DeepSeek-V4-Flash")
        self.assertEqual(set(row["sizes_kb"]), {100, 250, 400})

    def test_wrong_identity_malformed_sse_and_non200_are_not_healthy(self):
        for mode in ("wrong", "malformed", "malformed_json", "non200"):
            with self.subTest(mode=mode):
                proc, receipt = self.run_probe(mode)
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(receipt.exists(), proc.stdout + proc.stderr)
                row = json.loads(receipt.read_text())
                self.assertNotEqual(row["verdict"], "HEALTHY")
                if mode == "malformed_json":
                    self.assertEqual(row["history"][0]["error_code"], "MALFORMED_JSON")

    def test_receipt_keeps_lane_and_provider_identities_distinct(self):
        _proc, receipt = self.run_probe()
        row = json.loads(receipt.read_text())
        self.assertEqual(row["lane"], "local-lane")
        self.assertEqual(row["provider"], "local-provider")

    def test_receipt_is_secret_safe_and_replaced_atomically(self):
        proc, receipt = self.run_probe()
        self.assertEqual(proc.returncode, 0)
        raw = receipt.read_text()
        self.assertNotIn("dummy-secret", raw)
        self.assertNotIn("ok", raw)
        self.assertEqual(list(receipt.parent.glob("health.json.*")), [])
        self.assertEqual(json.loads(raw)["schema"], 1)

    def test_stale_receipt_is_rejected_by_manifest_validator(self):
        _proc, receipt = self.run_probe()
        row = json.loads(receipt.read_text())
        row["checked_at"] = "2020-01-01T00:00:00Z"
        row["expires_at"] = "2020-01-01T00:15:00Z"
        receipt.write_text(json.dumps(row))
        with self.assertRaises(run_manifest.ManifestError):
            run_manifest.validate_health(str(receipt), "local-lane", "local-provider",
                                         "[SP]deepseek-v4-flash-0731", "DeepSeek-V4-Flash")

    def test_bad_config_fails_without_traceback_or_receipt_claim(self):
        proc, receipt = self.run_probe(PROBE_SIZES_KB="100 nope 400")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr + proc.stdout)
        self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
