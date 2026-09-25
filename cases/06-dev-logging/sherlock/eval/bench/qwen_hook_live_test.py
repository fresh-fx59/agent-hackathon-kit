#!/usr/bin/env python3
"""Live pinned-Qwen Stop-hook test against a local stub OpenAI endpoint (no model cost).

Spec 2026-09-25 item 2. The stub answers every /chat/completions call with one
fixed assistant message and no tool call, so Qwen reaches Stop in one turn.
The Stop hook is ``stop-hook-log.py`` wrapping ``sh -c 'sleep N; echo allow'``
with timeout T written in the unit the pinned Qwen version reads.

    qwen_hook_live_test.py --qwen <qwen bin> --sleep 2 --timeout-s 10 --out <dir>

Prints one JSON summary: qwen exit, wall time, hook rows (start/end/kill),
the stub request count, and when Qwen exited relative to the hook start.
"""
import argparse
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
WRAPPER = HERE / "stop-hook-log.py"
REPLY = "STUB-REPLY: nothing to do."


def launcher():
    spec = importlib.util.spec_from_file_location("launcher", HERE / "run-v52-gpt55-comparison.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


class Stub(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "stub", "object": "model"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try: req = json.loads(raw or b"{}")
        except ValueError: req = {}
        Stub.requests.append({"t": time.time(), "path": self.path, "stream": bool(req.get("stream")),
                              "n_messages": len(req.get("messages") or [])})
        base = {"id": "chatcmpl-stub", "created": int(time.time()), "model": req.get("model", "stub")}
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if req.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            chunks = [dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {"role": "assistant", "content": REPLY}, "finish_reason": None}]),
                      dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]),
                      dict(base, object="chat.completion.chunk", choices=[], usage=usage)]
            for c in chunks:
                self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            return
        body = json.dumps(dict(base, object="chat.completion", usage=usage, choices=[
            {"index": 0, "message": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}])).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qwen", required=True)
    ap.add_argument("--sleep", type=float, required=True)
    ap.add_argument("--timeout-s", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default="Say hi.")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--hook", help="override the wrapped hook command (default sleep+allow)")
    a = ap.parse_args()
    L = launcher()
    out = Path(a.out); (out / ".qwen").mkdir(parents=True); (out / "home").mkdir()
    qwen_cmd = ["node", a.qwen] if a.qwen.endswith(".js") else [a.qwen]
    version = subprocess.check_output(qwen_cmd + ["--version"], text=True).strip()
    unit = L.hook_timeout_unit(version)  # refuses unknown versions
    inner = a.hook or "sleep %s; echo '{\"decision\":\"allow\",\"reason\":\"live-test\"}'" % a.sleep
    settings = {"general": {"enableAutoUpdate": False},
                "hooks": {"Stop": [{"hooks": [{"type": "command",
                                                "command": 'python3 "%s" sh -c "%s"' % (WRAPPER, inner.replace('"', '\\"')),
                                                "timeout": L.hook_timeout_value(version, a.timeout_s)}]}]},
                "model": {"generationConfig": {"timeout": 660000, "maxRetries": 0}},
                "tools": {"core": ["read_file", "run_shell_command"]}}
    (out / ".qwen/settings.json").write_text(json.dumps(settings, indent=1))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log = out / "stop-hook.jsonl"
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TMPDIR", "TZ")}
    env.update(HOME=str(out / "home"), OPENAI_BASE_URL="http://127.0.0.1:%d/v1" % srv.server_port,
               OPENAI_API_KEY="stub", NO_PROXY="127.0.0.1,localhost", SHERLOCK_STOP_HOOK_LOG=str(log))
    argv = qwen_cmd + ["--auth-type", "openai", "--model", "stub", "--output-format", "json"]
    if a.debug: argv.append("--debug")
    t0 = time.time()
    with open(out / "qwen-output.json", "wb") as so, open(out / "qwen-stderr.log", "wb") as se:
        rc = subprocess.call(argv + [a.prompt], cwd=out, stdin=subprocess.DEVNULL, stdout=so, stderr=se, env=env)
    t1 = time.time()
    time.sleep(max(0.0, min(a.sleep - (t1 - t0), 100)) + 1)  # let an orphaned hook finish/log
    srv.shutdown()
    rows = [json.loads(x) for x in log.read_text().splitlines()] if log.is_file() else []
    start = next((r for r in rows if r.get("event") == "start"), None)
    import datetime
    hs = datetime.datetime.fromisoformat(start["ts"]).timestamp() if start else None
    try: result = json.loads((out / "qwen-output.json").read_text())[-1]
    except Exception: result = None
    summary = {"qwen_version": version, "unit": unit, "sleep_s": a.sleep, "timeout_s": a.timeout_s,
               "settings_timeout": settings["hooks"]["Stop"][0]["hooks"][0]["timeout"],
               "qwen_exit": rc, "qwen_wall_s": round(t1 - t0, 2),
               "qwen_exit_after_hook_start_s": round(t1 - hs, 2) if hs else None,
               "stub_requests": len(Stub.requests),
               "result": {k: result.get(k) for k in ("type", "subtype", "is_error")} if isinstance(result, dict) else None,
               "hook_rows": [{k: r.get(k) for k in ("event", "decision", "exit_code", "duration_ms", "signal", "elapsed_ms")} for r in rows]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
