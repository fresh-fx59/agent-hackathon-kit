#!/usr/bin/env python3
"""Provider-free Qwen 0.22 lifecycle regression for skill and slow-shell hooks.

Run only against a frozen lifecycle helper.  The local HTTP server scripts tool
calls and persists request/response bytes; it never contacts a model provider.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HELPER = ROOT / "eval/bench/lifecycle-supervisor.py"
INVALID_DIRECTORY_MODES = ("invalid-then-repaired", "invalid-no-batch")
BATCH_ONLY_MODE = "batch-no-execution-hooks"


def load(path):
    spec = importlib.util.spec_from_file_location("skill_guardian_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def once(path, value):
    data = value.encode() if isinstance(value, str) else value
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data); out.flush(); os.fsync(out.fileno())


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def terminate_process_group(process, pgid=None, grace_seconds=5):
    """Stop the direct-Qwen fixture group, including orphaned Node children."""
    try:
        group = os.getpgid(process.pid) if pgid is None else pgid
        os.killpg(group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(timeout=grace_seconds)


def run_case(qwen, output, helper_path, mode, registration_timeout_seconds=15, client_timeout_seconds=45):
    trace = output / mode; trace.mkdir(mode=0o700)
    workspace = trace / "workspace"; workspace.mkdir(mode=0o700)
    home = workspace / "home"; home.mkdir(mode=0o700)
    helper = load(helper_path)
    nonce, boot, key = (mode + "-nonce-20260906").ljust(20, "x"), helper.current_boot_id(), b"s" * 32
    observer = helper.init_segment(trace, nonce, boot, capability=key)
    helper.publish_observation(observer, nonce, boot, sequence=0,
                               monotonic_ns=time.monotonic_ns(), capability=key)
    skill_parent = workspace / "skills"; skill = skill_parent / "mockskill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: mockskill\ndescription: fixture only\n---\n# mock\n", encoding="utf-8")
    hook = (f'{sys.executable} {helper_path} hook --observer-dir "$SHERLOCK_OBSERVER_DIR" '
            '--workspace "$PWD" --nonce "$SHERLOCK_RUN_NONCE" --boot-id "$SHERLOCK_BOOT_ID"')
    if mode == "missing-hook":
        hook_events = ()
    elif mode in ("invalid-no-batch", "executed-no-batch"):
        # These are the two explicit no-batch negative controls.  The latter
        # still has a real execution pair, so batch accounting cannot be
        # inferred merely from Pre/Post completion.
        hook_events = ("PreToolUse", "PostToolUse", "PostToolUseFailure")
    elif mode == BATCH_ONLY_MODE:
        hook_events = ("PostToolBatch",)
    else:
        hook_events = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch")
    settings = {"skills": {"directories": ["skills"]}, "hooks": {
        event: [{"matcher": "*", "hooks": [{"type": "command", "command": hook,
                                                "timeout": 10000}]}]
        for event in hook_events}}
    (workspace / ".qwen").mkdir()
    settings_raw = (json.dumps(settings, sort_keys=True) + "\n").encode()
    (workspace / ".qwen/settings.json").write_bytes(settings_raw)
    once(trace / "settings.input.json", settings_raw)
    registered = threading.Event(); requests = []; dispatch_error = []; client = None; client_pgid = None

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"])); index = len(requests)
            requests.append(index); once(trace / f"request-{index}.json", raw)
            if mode == "invalid-then-repaired" and index in (0, 1):
                call_id = "call_invalid" if index == 0 else "call_repaired"
                directory = "/outside-fixture-workspace" if index == 0 else str(workspace)
                args = {"command": "printf repaired > repaired.txt", "directory": directory}
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": call_id,
                    "type": "function", "function": {"name": "run_shell_command", "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            elif mode == "invalid-no-batch" and index == 0:
                args = {"command": "printf rejected", "directory": "/outside-fixture-workspace"}
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_invalid",
                    "type": "function", "function": {"name": "run_shell_command", "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            elif mode == BATCH_ONLY_MODE and index == 0:
                args = {"command": "printf executed > batch-only.txt", "directory": str(workspace)}
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_executed_without_hooks",
                    "type": "function", "function": {"name": "run_shell_command", "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            elif index == 0:
                name = "skill" if mode in ("skill", "missing-hook") else "run_shell_command"
                args = {"skill": "mockskill"} if mode in ("skill", "missing-hook") else {"command": "sleep 0.6; printf slow > marker.txt"}
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_" + mode,
                    "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            else:
                # The second provider request is the post-tool boundary.  It
                # must see the completed pair rather than merely trusting that
                # the guardian stayed alive long enough to poll again.
                try:
                    helper.check_dispatch(observer, nonce, boot)
                except helper.LifecycleFault as exc:
                    dispatch_error.append({"reason": exc.reason, "detail": exc.detail})
                    self.send_error(503, exc.reason)
                    if client is not None:
                        terminate_process_group(client, client_pgid)
                    return
                delta, finish = {"role": "assistant", "content": "done"}, "stop"
            rows = [{"id": f"{mode}-{index}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "mock",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": f"{mode}-{index}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "mock",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
            body = b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in rows) + b"data: [DONE]\n\n"
            once(trace / f"response-{index}.sse", body)
            # Proxy semantics: make the provider call durable and register its
            # exact ID before Qwen can receive the response and begin either
            # prevalidation or execution.  An invalid directory still has an
            # expectation; only its PostToolBatch outcome may discharge it.
            if index == 0 and mode != "invalid-then-repaired":
                call_id = ("call_invalid" if mode == "invalid-no-batch" else
                           "call_executed_without_hooks" if mode == BATCH_ONLY_MODE else
                           "call_" + mode)
                helper.register_expected_tools(observer, nonce, boot, f"response-{index}", [call_id])
                registered.set()
            elif mode == "invalid-then-repaired" and index in (0, 1):
                call_id = "call_invalid" if index == 0 else "call_repaired"
                helper.register_expected_tools(observer, nonce, boot, f"response-{index}", [call_id])
                registered.set()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True); server_thread.start()
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "LANG", "LC_ALL", "TMPDIR")}
    env.update({"HOME": str(home), "QWEN_HOME": str(home), "OPENAI_API_KEY": "loopback-only",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "NO_PROXY": "127.0.0.1,localhost",
                "SHERLOCK_OBSERVER_DIR": str(observer), "SHERLOCK_RUN_NONCE": nonce,
                "SHERLOCK_BOOT_ID": boot, "SHERLOCK_LIFECYCLE_HELPER": str(helper_path)})
    argv = [str(qwen), "--auth-type", "openai", "--model", "mock", "--approval-mode", "yolo", "--output-format", "json", "-p", "Use exactly the supplied tool then finish."]
    resolved = qwen.resolve(strict=True)
    once(trace / "launch.json", json.dumps({"schema": 1, "argv": argv, "cwd": str(workspace),
        "qwen": {"path": str(resolved), "version": subprocess.check_output([str(resolved), "--version"], text=True).strip(), "sha256": digest(resolved)},
        "lifecycle_helper": {"path": str(helper_path.resolve()), "sha256": digest(helper_path)}, "provider": "loopback-only"}, sort_keys=True) + "\n")
    client = subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    client_pgid = os.getpgid(client.pid)
    guardian = None
    stdout = stderr = ""; exception = None; guardian_stdout = guardian_stderr = ""
    cleanup_errors = []
    try:
        if not registered.wait(registration_timeout_seconds): raise RuntimeError("completed SSE/tool expectation absent")
        guardian_argv = [sys.executable, str(helper_path), "guardian", "--observer-dir", str(observer), "--nonce", nonce, "--boot-id", boot, "--controller-pid", str(client.pid), "--controller-start-ticks", str(helper.process_start_ticks(client.pid)), "--controller-pgid", str(os.getpgid(client.pid)), "--interval", "0.02"]
        once(trace / "guardian-launch.json", json.dumps({"argv": guardian_argv}, sort_keys=True) + "\n")
        guardian = subprocess.Popen(guardian_argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = client.communicate(timeout=client_timeout_seconds)
    except BaseException as exc:
        exception = {"type": type(exc).__name__, "detail": str(exc)}
        terminate_process_group(client, client_pgid)
        try:
            stdout, stderr = client.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            terminate_process_group(client, client_pgid, grace_seconds=1)
            stdout, stderr = client.communicate(timeout=1)
    finally:
        guardian_alive_at_client_completion = guardian is not None and guardian.poll() is None
        try:
            if guardian is not None and guardian.poll() is None:
                guardian.terminate()
                try:
                    guardian.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    guardian.kill(); guardian.wait(timeout=5)
            if guardian is not None:
                guardian_stdout, guardian_stderr = guardian.communicate()
        except BaseException as exc:
            cleanup_errors.append({"target": "guardian", "type": type(exc).__name__, "detail": str(exc)})
        try:
            if client.poll() is None:
                terminate_process_group(client, client_pgid)
        except BaseException as exc:
            cleanup_errors.append({"target": "client", "type": type(exc).__name__, "detail": str(exc)})
        try:
            server.shutdown(); server.server_close(); server_thread.join()
        except BaseException as exc:
            cleanup_errors.append({"target": "server", "type": type(exc).__name__, "detail": str(exc)})
    once(trace / "stdout.json", stdout); once(trace / "stderr.txt", stderr)
    once(trace / "guardian.stdout.txt", guardian_stdout); once(trace / "guardian.stderr.txt", guardian_stderr)
    events = [json.loads(line) for line in (observer / "hook-events.jsonl").read_text().splitlines()] if (observer / "hook-events.jsonl").exists() else []
    starts = [json.loads(line) for line in (observer / "hook-starts.jsonl").read_text().splitlines()] if (observer / "hook-starts.jsonl").exists() else []
    batches = [json.loads(line) for line in (observer / "post-tool-batch-events.jsonl").read_text().splitlines()] if (observer / "post-tool-batch-events.jsonl").exists() else []
    rejections = json.loads((observer / "rejections.json").read_text()) if (observer / "rejections.json").exists() else {}
    fault = json.loads((observer / "fault.json").read_text())["reason"] if (observer / "fault.json").exists() else None
    decoded = []; correlations = []
    for event in events:
        try:
            payload = json.loads(base64.b64decode(event["input_base64"]))
            tool_name = payload["tool_name"]
            decoded.append((event["phase"], event["tool_use_id"], tool_name))
            correlations.append({"phase": event["phase"], "tool_name": tool_name,
                                 "provider_tool_call_id": payload.get("tool_call_id"),
                                 "qwen_tool_use_id": event["tool_use_id"]})
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            decoded.append(("INVALID", "", ""))
    expected_name = "skill" if mode in ("skill", "missing-hook") else "run_shell_command"
    pair_ids = {tool_id for phase, tool_id, name in decoded if name == expected_name and phase == "PreToolUse"}
    exact_pairs = all(sum(1 for phase, found, name in decoded if found == tool_id and name == expected_name and phase in ("PreToolUse", "PostToolUse")) == 2 for tool_id in pair_ids)
    result = {"mode": mode, "exit_code": client.returncode, "requests": len(requests), "fault": fault,
              "guardian_started": guardian is not None, "hook_starts": starts, "hook_events": events,
              "hook_pairs": decoded, "exact_pre_post_pair": bool(pair_ids) and exact_pairs,
              "expected_tool_name": expected_name, "dispatch_error": dispatch_error,
              "exception": exception, "guardian_alive_at_client_completion": guardian_alive_at_client_completion,
              "guardian_exit_code": guardian.returncode if guardian is not None else None, "cleanup_errors": cleanup_errors,
              "marker": (workspace / "marker.txt").read_text() if (workspace / "marker.txt").exists() else None,
              "successful_responses": len(list(trace.glob("response-*.sse"))),
              "lifecycle_helper_sha256": digest(helper_path), "correlations": correlations,
              "post_tool_batch_events": batches, "rejections": rejections}
    once(trace / "result.json", json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--qwen", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    args = parser.parse_args(); args.output.mkdir(mode=0o700)
    helper_path = args.helper.resolve(strict=True)
    rows = [run_case(args.qwen, args.output, helper_path, mode)
            for mode in ("skill", "slow-shell", "invalid-then-repaired")]
    missing = run_case(args.qwen, args.output, helper_path, "missing-hook")
    absent_batch = run_case(args.qwen, args.output, helper_path, "invalid-no-batch")
    executed_without_batch = run_case(args.qwen, args.output, helper_path, "executed-no-batch")
    batch_without_pairs = run_case(args.qwen, args.output, helper_path, BATCH_ONLY_MODE)
    positive_rows = rows[:2]
    def batch_has(row, provider_call_id):
        return any(provider_call_id in event.get("tool_call_ids", [])
                   for event in row["post_tool_batch_events"])
    ok = all(row["exit_code"] == 0 and row["requests"] == 2 and row["fault"] is None and row["guardian_started"] and row["guardian_alive_at_client_completion"] and row["hook_starts"] and row["hook_events"] and row["exact_pre_post_pair"] and batch_has(row, "call_" + row["mode"]) and not row["dispatch_error"] and row["exception"] is None for row in positive_rows)
    if rows[1]["marker"] != "slow": ok = False
    correction = rows[2]
    correction_ok = (correction["exit_code"] == 0 and correction["requests"] == 3 and
                     correction["fault"] is None and correction["exact_pre_post_pair"] and
                     not correction["dispatch_error"] and correction["exception"] is None and
                     correction["rejections"].get("rejected", {}).get("call_invalid", {}).get("execution_status") == "not_started" and
                     correction["rejections"].get("rejected", {}).get("call_invalid", {}).get("error_type") == "invalid_tool_params" and
                     any("call_invalid" in event.get("rejected_tool_call_ids", []) for event in correction["post_tool_batch_events"]) and
                     batch_has(correction, "call_repaired"))
    missing_ok = (missing["requests"] == 2 and missing["successful_responses"] == 1 and
                  missing["fault"] == "EXPECTED_TOOL_BATCH_MISSING" and bool(missing["dispatch_error"]))
    absent_batch_ok = (absent_batch["requests"] == 2 and absent_batch["successful_responses"] == 1 and
                       absent_batch["fault"] == "EXPECTED_TOOL_BATCH_MISSING" and bool(absent_batch["dispatch_error"]))
    executed_without_batch_ok = (executed_without_batch["requests"] == 2 and
                                 executed_without_batch["successful_responses"] == 1 and
                                 executed_without_batch["fault"] == "EXPECTED_TOOL_BATCH_MISSING" and
                                 executed_without_batch["exact_pre_post_pair"] and
                                 bool(executed_without_batch["dispatch_error"]))
    batch_without_pairs_ok = (batch_without_pairs["successful_responses"] == 1 and
                              batch_without_pairs["fault"] == "EXPECTED_TOOL_HOOK_MISSING")
    all_rows = rows + [missing, absent_batch, executed_without_batch, batch_without_pairs]
    ok = ok and correction_ok and missing_ok and absent_batch_ok and executed_without_batch_ok and batch_without_pairs_ok
    summary = {"lifecycle_helper_sha256": digest(helper_path), "successful_scenarios": [
        {"mode": row["mode"], "provider_to_qwen_ids": row["correlations"],
         "guardian_alive_at_client_completion": row["guardian_alive_at_client_completion"]} for row in positive_rows],
        "invalid_then_repaired": {"rejected_provider_id": "call_invalid",
                                   "rejection": correction["rejections"].get("rejected", {}).get("call_invalid"),
                                   "provider_to_qwen_ids": correction["correlations"],
                                   "guardian_alive_at_client_completion": correction["guardian_alive_at_client_completion"]},
        "missing_hook": {"dispatch_error": missing["dispatch_error"],
                         "successful_response_count": missing["successful_responses"],
                         "second_successful_response_absent": missing["successful_responses"] == 1},
        "absent_batch_rejection": {"dispatch_error": absent_batch["dispatch_error"],
                                     "second_successful_response_absent": absent_batch["successful_responses"] == 1},
        "executed_without_batch": {"dispatch_error": executed_without_batch["dispatch_error"],
                                     "provider_to_qwen_ids": executed_without_batch["correlations"],
                                     "second_successful_response_absent": executed_without_batch["successful_responses"] == 1},
        "batch_without_execution_hooks": {"fault": batch_without_pairs["fault"],
                                            "successful_response_count": batch_without_pairs["successful_responses"]} }
    record = {"scenarios": all_rows, "summary": summary, "pass": ok}
    once(args.output / "results.json", json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2)); return 0 if ok else 1
if __name__ == "__main__": raise SystemExit(main())
