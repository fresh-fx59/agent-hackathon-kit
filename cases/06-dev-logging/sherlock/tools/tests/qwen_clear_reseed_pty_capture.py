#!/usr/bin/env python3
"""Capture an installed Qwen PTY session across ``/clear`` and ``/sherlock``.

This is deliberately a provider-free source-proof fixture.  It runs Qwen against a
localhost OpenAI-compatible SSE server, keeps every hook stdin and HTTP body, and
does not use the lifecycle supervisor or a Sherlock corpus.
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import pty
import select
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)


RECORDER = r'''#!/usr/bin/env python3
import os, pathlib, sys, time
root = pathlib.Path(sys.argv[1]); event = sys.argv[2]
raw = sys.stdin.buffer.read()
root.mkdir(mode=0o700, parents=True, exist_ok=True)
stamp = '%020d' % time.time_ns()
path = root / (stamp + '-' + event + '.json')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'wb') as out: out.write(raw)
print('{}')
'''


def event_name(row):
    return row.get("hook_event_name") or row.get("event")


def event_source(row):
    return row.get("source") or row.get("session_start_source")


def assess_boundary(rows, reseed):
    """Return the narrow proof required before staged driver selection."""
    prompts = [(i, row) for i, row in enumerate(rows)
               if event_name(row) == "UserPromptSubmit"]
    reseeds = [(i, row) for i, row in prompts if row.get("prompt") == reseed]
    if len(reseeds) != 1:
        return {"passed": False, "reason": "RESEED_PROMPT_NOT_EXACTLY_ONCE"}
    reseed_i, reseed_row = reseeds[0]
    clear_starts = [(i, row) for i, row in enumerate(rows)
                    if i < reseed_i and event_name(row) == "SessionStart"
                    and event_source(row) == "clear"]
    if len(clear_starts) != 1:
        return {"passed": False, "reason": "CLEAR_SESSION_START_MISSING"}
    clear_i, clear = clear_starts[0]
    preceding = [(i, row) for i, row in prompts if i < clear_i]
    if not preceding:
        return {"passed": False, "reason": "PRE_CLEAR_PROMPT_MISSING"}
    pre_i, pre = preceding[-1]
    before_id, after_id = pre.get("session_id"), reseed_row.get("session_id")
    if not before_id or not after_id or before_id == after_id:
        return {"passed": False, "reason": "CLEAR_SESSION_ID_NOT_TRANSITIONED"}
    if clear.get("session_id") != after_id:
        return {"passed": False, "reason": "CLEAR_SESSION_ID_NOT_RESEED_SESSION"}
    return {"passed": True, "reason": None, "pre_clear_session_id": before_id,
            "post_clear_session_id": after_id, "clear_event_index": clear_i,
            "reseed_event_index": reseed_i}


def read_until(fd, transcript, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.15)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if chunk:
                transcript.write(chunk); transcript.flush()
        if predicate():
            return True
    return predicate()


def type_line(fd, text):
    os.write(fd, text.encode("utf-8") + b"\r")


def wait_for_request_quiet(fd, transcript, requests, minimum, timeout, quiet_s=1.5):
    """Drain Qwen's follow-up memory turn before sending the next PTY line."""
    deadline = time.monotonic() + timeout
    seen = len(requests); changed = time.monotonic()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.15)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if chunk:
                transcript.write(chunk); transcript.flush()
        if len(requests) != seen:
            seen, changed = len(requests), time.monotonic()
        if seen >= minimum and time.monotonic() - changed >= quiet_s:
            return True
    return False


def load_rows(hooks):
    rows = []
    for path in sorted(hooks.glob("*.json")):
        raw = path.read_bytes()
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            row = {"raw_invalid_json": True}
        row.update(capture_file=path.name, input_sha256=sha(raw),
                   captured_at_ns=int(path.name.split("-", 1)[0]))
        rows.append(row)
    return rows


def terminate_group(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--qwen", type=pathlib.Path, required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--reseed", default="RESEED: use the toy Sherlock skill")
    args = parser.parse_args()
    root = args.output.resolve(); root.mkdir(mode=0o700, parents=True)
    work = root / "workspace"; work.mkdir(mode=0o700)
    home = root / "home"; home.mkdir(mode=0o700)
    hooks = root / "raw-hooks"; hooks.mkdir(mode=0o700)
    catalogue = root / "skill-catalogue" / "sherlock"; catalogue.mkdir(mode=0o700, parents=True)
    (catalogue / "SKILL.md").write_text("---\nname: sherlock\ndescription: toy isolated skill\n---\nUse toy evidence only.\n")
    recorder = root / "record_hook.py"; recorder.write_text(RECORDER); recorder.chmod(0o700)
    command = "%s %s %s" % (sys.executable, recorder, hooks)
    settings = {"skills": {"directories": ["../skill-catalogue"]}, "hooks": {}}
    for event in ("SessionStart", "UserPromptSubmit"):
        settings["hooks"][event] = [{"hooks": [{"type": "command", "command": command + " " + event,
                                                     "timeout": 10000}]}]
    qwen_dir = work / ".qwen"; qwen_dir.mkdir()
    (qwen_dir / "settings.json").write_text(json.dumps(settings, sort_keys=True) + "\n")
    requests = []
    actions = []

    def send_stage(fd, label, text):
        actions.append({"label": label, "text": text, "sent_at_ns": time.time_ns()})
        type_line(fd, text)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            index = len(requests); requests.append(raw)
            atomic(root / ("request-%02d.json" % index), raw)
            safe_headers = {k: v for k, v in self.headers.items() if k.lower() != "authorization"}
            atomic(root / ("request-%02d.headers.json" % index),
                   (json.dumps(safe_headers, sort_keys=True) + "\n").encode())
            row = {"id": "clear-%d" % index, "object": "chat.completion.chunk",
                   "created": int(time.time()), "model": "mock",
                   "choices": [{"index": 0, "delta": {"role": "assistant", "content": "mock turn %d" % index},
                                "finish_reason": None}]}
            finish = {"id": "clear-%d" % index, "object": "chat.completion.chunk",
                      "created": int(time.time()), "model": "mock",
                      "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            sse = b"data: " + json.dumps(row).encode() + b"\n\ndata: " + json.dumps(finish).encode() + b"\n\ndata: [DONE]\n\n"
            atomic(root / ("response-%02d.sse" % index), sse)
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(sse))); self.end_headers(); self.wfile.write(sse)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TERM")}
    env.update(HOME=str(home), QWEN_HOME=str(home), OPENAI_API_KEY="loopback-only",
               OPENAI_BASE_URL="http://127.0.0.1:%d/v1" % server.server_port,
               NO_PROXY="127.0.0.1,localhost", TERM="xterm-256color")
    argv = [args.node, str(args.qwen), "--auth-type", "openai", "--model", "mock", "--approval-mode", "yolo"]
    atomic(root / "argv.json", (json.dumps(argv) + "\n").encode())
    atomic(root / "qwen-identity.json", (json.dumps({"path": str(args.qwen), "sha256": sha(args.qwen.read_bytes())}, indent=2) + "\n").encode())
    started = time.time_ns(); pid, fd = pty.fork()
    if pid == 0:
        os.chdir(work); os.execvpe(argv[0], argv, env)
    transcript = open(root / "pty-transcript.raw", "wb")
    result = {"started_at_ns": started, "provider": "loopback-only", "qwen_sha256": sha(args.qwen.read_bytes()),
              "qwen_path": str(args.qwen), "argv": argv}
    try:
        start_ok = read_until(fd, transcript, lambda: len(load_rows(hooks)) >= 1, 20)
        send_stage(fd, "initial", "INITIAL: establish parent session")
        initial_ok = read_until(fd, transcript, lambda: len(requests) >= 1, 25)
        initial_done = wait_for_request_quiet(fd, transcript, requests, 1, 15)
        send_stage(fd, "clear", "/clear")
        clear_ok = read_until(fd, transcript, lambda: any(event_name(x) == "SessionStart" and event_source(x) == "clear" for x in load_rows(hooks)), 25)
        # The client has published SessionStart(clear), but needs one repaint
        # tick before its input component accepts the next slash command.
        time.sleep(0.35)
        send_stage(fd, "skill", "/sherlock")
        # A custom skill may inject local context for the *next* prompt rather
        # than produce a provider request.  The raw terminal and staged-send
        # records are the witness that it was entered; this source-proof does
        # not assert a model-turn count for it.
        skill_hook_observed = read_until(
            fd, transcript,
            lambda: any(event_name(x) == "UserPromptSubmit" and x.get("submitted_prompt") == "/sherlock"
                        for x in load_rows(hooks)), 12)
        time.sleep(0.5)
        send_stage(fd, "reseed", args.reseed)
        reseed_hook_ok = read_until(fd, transcript,
                                    lambda: any(event_name(x) == "UserPromptSubmit" and x.get("prompt") == args.reseed
                                                for x in load_rows(hooks)), 25)
        reseed_ok = read_until(fd, transcript, lambda: any(args.reseed.encode() in raw for raw in requests), 30)
        rows = load_rows(hooks); proof = assess_boundary(rows, args.reseed)
        result.update(start_hook_observed=start_ok, initial_request_observed=initial_ok,
                      initial_completion_observed=initial_done, clear_hook_observed=clear_ok,
                      skill_hook_observed=skill_hook_observed,
                      reseed_hook_observed=reseed_hook_ok, reseed_request_observed=reseed_ok,
                      request_count_at_reseed_proof=len(requests), hook_rows=rows, stage_actions=actions,
                      boundary_proof=proof)
        result["passed"] = all((start_ok, initial_ok, initial_done, clear_ok, skill_hook_observed,
                                reseed_hook_ok, reseed_ok, proof["passed"]))
        if requests:
            parsed = [json.loads(raw) for raw in requests]
            result["request_message_counts"] = [len(x.get("messages", [])) for x in parsed]
    except BaseException as exc:
        result.update(passed=False, exception_type=type(exc).__name__, exception=str(exc), hook_rows=load_rows(hooks), request_count=len(requests))
    finally:
        transcript.flush(); transcript.close(); terminate_group(pid)
        try: _, status = os.waitpid(pid, 0)
        except ChildProcessError: status = None
        result["child_wait_status"] = status
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        # Qwen can issue a memory-maintenance request after the response that
        # establishes the reseed proof.  Record the terminal count only after
        # its PTY group and the server are both stopped; never report a stale
        # lower count from the middle of the capture.
        result["request_count"] = len(requests)
        result["request_message_counts"] = [len(json.loads(raw).get("messages", [])) for raw in requests]
        result["finished_at_ns"] = time.time_ns()
        atomic(root / "stage-actions.json", (json.dumps(actions, indent=2) + "\n").encode())
        atomic(root / "result.json", (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
