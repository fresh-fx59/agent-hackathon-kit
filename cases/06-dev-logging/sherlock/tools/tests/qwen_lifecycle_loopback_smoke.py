#!/usr/bin/env python3
"""Installed Qwen lifecycle-hook smoke against a loopback SSE provider.

This is an integration artifact, not a unit test.  It never loads a Sherlock
skill or a provider credential: the server returns one scripted shell tool call.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "eval/bench/lifecycle-supervisor.py"


def write_once(path, value):
    """Persist a capture once, flushing it before the observed process runs."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # fdopen closes the descriptor on the normal and exceptional paths.
        raise


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qwen_outcome(stdout):
    """Extract the installed client's reported tool result and denial, if any."""
    try:
        records = json.loads(stdout)
    except json.JSONDecodeError:
        return {"parsed": False}
    result = next((row for row in records if row.get("type") == "result"), {})
    tool_errors = []
    for row in records:
        message = row.get("message", {})
        if row.get("type") != "user" or message.get("role") != "user":
            continue
        for content in message.get("content", []):
            if content.get("type") == "tool_result":
                tool_errors.append({
                    "tool_use_id": content.get("tool_use_id"),
                    "is_error": content.get("is_error"),
                    "content": content.get("content"),
                })
    return {
        "parsed": True,
        "permission_denials": result.get("permission_denials", []),
        "tool_results": tool_errors,
        "tool_stats": result.get("stats", {}).get("tools", {}),
    }


def load_helper():
    spec = importlib.util.spec_from_file_location("lifecycle_loopback", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario(qwen, root, mode, helper):
    trace = root / mode
    trace.mkdir(mode=0o700)
    workspace = trace / "workspace"; workspace.mkdir(mode=0o700)
    home = workspace / "home"; home.mkdir(mode=0o700)
    nonce, boot, key = (mode + "-nonce-20260906").ljust(20, "x"), "loopback-boot", b"l" * 32
    observer = helper.init_segment(trace, nonce, boot, capability=key)
    if mode != "missing":
        observed = time.monotonic_ns()
        if mode == "stale": observed -= helper.MAX_OBSERVATION_AGE_NS + 1
        helper.publish_observation(observer, nonce, boot, sequence=0,
                                   monotonic_ns=observed, capability=key)
    if mode in ("missing", "stale"):
        # This is exactly the guardian's dispatch predicate.  Record its
        # durable fault before Qwen reaches the hook; the installed client must
        # receive an explicit denial instead of treating a helper crash as OK.
        try:
            helper.check_dispatch(observer, nonce, boot)
        except helper.LifecycleFault:
            pass
    if mode == "delete":
        (workspace / "work").mkdir()
        (workspace / "work" / "worklist.tsv").write_text("id\n", encoding="utf-8")
    command = (f'{sys.executable} {HELPER} hook --observer-dir "$SHERLOCK_OBSERVER_DIR" '
               f'--workspace "$PWD" --nonce "$SHERLOCK_RUN_NONCE" --boot-id "$SHERLOCK_BOOT_ID"')
    settings = {"hooks": {event: [{"matcher": "*", "hooks": [{
        "type": "command", "command": command, "timeout": 10000}]}]
        for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}}
    (workspace / ".qwen").mkdir()
    settings_bytes = (json.dumps(settings, sort_keys=True) + "\n").encode("utf-8")
    (workspace / ".qwen" / "settings.json").write_bytes(settings_bytes)
    write_once(trace / "settings.input.json", settings_bytes)
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            i = len(requests); requests.append(self.path)
            write_once(trace / f"request-{i}.json", request_body)
            if i == 0:
                delta = {"role":"assistant", "tool_calls":[{"index":0,"id":"call_loopback",
                    "type":"function", "function":{"name":"run_shell_command",
                    "arguments":json.dumps({"command": "rm work/worklist.tsv" if mode == "delete" else "printf lifecycle > marker.txt"})}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"role":"assistant","content":"loopback complete"}, "stop"
            rows = [
                {"id":f"loop-{i}","object":"chat.completion.chunk","created":int(time.time()),"model":"mock",
                 "choices":[{"index":0,"delta":delta,"finish_reason":None}]},
                {"id":f"loop-{i}","object":"chat.completion.chunk","created":int(time.time()),"model":"mock",
                 "choices":[{"index":0,"delta":{},"finish_reason":finish}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}},
            ]
            body = b"".join(b"data: "+json.dumps(x).encode()+b"\n\n" for x in rows)+b"data: [DONE]\n\n"
            write_once(trace / f"response-{i}.sse", body)
            self.send_response(200); self.send_header("Content-Type","text/event-stream")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    env = {k:v for k,v in os.environ.items() if k in ("PATH","LANG","LC_ALL","TMPDIR")}
    env.update({"HOME":str(home), "QWEN_HOME":str(home), "OPENAI_API_KEY":"loopback-only",
                "OPENAI_BASE_URL":f"http://127.0.0.1:{server.server_port}/v1", "NO_PROXY":"127.0.0.1,localhost",
                "SHERLOCK_OBSERVER_DIR":str(observer), "SHERLOCK_RUN_NONCE":nonce,
                "SHERLOCK_BOOT_ID":boot, "SHERLOCK_LIFECYCLE_HELPER":str(HELPER)})
    argv = [str(qwen), "--auth-type", "openai", "--model", "mock", "--approval-mode", "yolo",
            "--output-format", "json", "-p", "Use the shell once, then finish."]
    qwen_path = qwen.resolve(strict=True)
    qwen_version = subprocess.run([str(qwen_path), "--version"], check=True, capture_output=True,
                                  text=True).stdout.strip()
    write_once(trace / "launch.json", json.dumps({
        "schema": 1,
        "argv": argv,
        "cwd": str(workspace.resolve()),
        "qwen": {"path": str(qwen_path), "version": qwen_version, "sha256": sha256(qwen_path)},
        "lifecycle_helper": {"path": str(HELPER.resolve()), "sha256": sha256(HELPER)},
        "observer_dir": str(observer.resolve()),
        "provider": "loopback-only",
    }, sort_keys=True, separators=(",", ":")) + "\n")
    started=time.monotonic()
    try:
        completed=subprocess.run(argv,cwd=workspace,env=env,stdin=subprocess.DEVNULL,
                                 capture_output=True,text=True,timeout=45)
        exit_code=completed.returncode
        write_once(trace/"stdout.json", completed.stdout); write_once(trace/"stderr.txt", completed.stderr)
    except subprocess.TimeoutExpired as exc:
        exit_code=124; write_once(trace/"stdout.json", exc.stdout or ""); write_once(trace/"stderr.txt", exc.stderr or "")
    finally:
        server.shutdown(); server.server_close(); thread.join()
    fault = observer / "fault.json"
    events = observer / "hook-events.jsonl"
    outputs=[]
    if events.exists():
        outputs=[json.loads(line).get("output",{}) for line in events.read_text().splitlines()]
    result={"mode":mode,"exit_code":exit_code,"elapsed_s":time.monotonic()-started,
            "requests":len(requests),"marker_exists":(workspace/"marker.txt").exists(),
            "fault":json.loads(fault.read_text()).get("reason") if fault.exists() else None,
            "hook_outputs":outputs,
            "actual_qwen":qwen_outcome((trace / "stdout.json").read_text()),
            "evidence_removed": mode == "delete" and not (workspace / "work" / "worklist.tsv").exists(),
            "captures_present": all((trace / name).is_file() for name in (
                "launch.json", "settings.input.json", "stdout.json", "stderr.txt",
                "request-0.json", "response-0.sse", "request-1.json", "response-1.sse"))}
    if mode in ("missing", "stale"):
        actual = result["actual_qwen"]
        result["actual_qwen_denial"] = bool(actual.get("permission_denials")) and any(
            item.get("is_error") and f"terminal lifecycle fault: {result['fault']}" in str(item.get("content"))
            for item in actual.get("tool_results", []))
    write_once(trace/"result.json", json.dumps(result,indent=2)+"\n")
    return result


def guardian_probe(root, helper):
    """Run the real guardian against a disposable owned process group."""
    trace = root / "guardian"; trace.mkdir(mode=0o700)
    nonce, boot, key = "guardian-nonce-20260906", helper.current_boot_id(), b"g" * 32
    observer = helper.init_segment(trace, nonce, boot, capability=key)
    helper.publish_observation(observer, nonce, boot, sequence=0,
                               monotonic_ns=time.monotonic_ns() - helper.MAX_OBSERVATION_AGE_NS - 1,
                               capability=key)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    terminated_before_cleanup = False
    try:
        rc = helper.run_guardian(observer, nonce, boot, child.pid,
                                 helper.process_start_ticks(child.pid), os.getpgid(child.pid), interval_s=.01)
        child.wait(timeout=3)
        terminated_before_cleanup = child.returncode in (-signal.SIGTERM, -signal.SIGKILL)
    finally:
        if child.poll() is None:
            child.terminate(); child.wait(timeout=3)
    fault = json.loads((observer / "fault.json").read_text()).get("reason")
    result = {"guardian_exit_code": rc, "child_exit_code": child.returncode, "fault": fault,
              "terminated_before_cleanup": terminated_before_cleanup}
    write_once(trace / "result.json", json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen",type=Path,required=True); parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(); root=args.output.resolve(); root.mkdir(mode=0o700)
    helper=load_helper(); rows=[scenario(args.qwen,root,mode,helper) for mode in ("normal","missing","stale","delete")]
    guardian = guardian_probe(root, helper)
    write_once(root/"results.json", json.dumps({"scenarios": rows, "guardian": guardian},indent=2)+"\n")
    normal, *blocked=rows
    denied = [row for row in blocked if row["mode"] in ("missing", "stale")]
    deleted = next(row for row in blocked if row["mode"] == "delete")
    normal_events = [item.get("hookSpecificOutput", {}).get("hookEventName")
                     for item in normal["hook_outputs"]]
    ok=(normal["marker_exists"] and normal["fault"] is None and
        normal_events == ["PreToolUse", "PostToolUse"] and normal["captures_present"] and
        all(not row["marker_exists"] and row["fault"] and row.get("actual_qwen_denial")
            and row["captures_present"] for row in denied) and
        deleted.get("evidence_removed") and deleted["fault"] == "REGISTERED_EVIDENCE_MISSING" and
        deleted["captures_present"] and
        guardian["guardian_exit_code"] == 2 and guardian["fault"] == "OBSERVATION_STALE" and
        guardian.get("terminated_before_cleanup"))
    print(json.dumps({"scenarios": rows, "guardian": guardian},indent=2)); return 0 if ok else 1
if __name__ == "__main__": raise SystemExit(main())
