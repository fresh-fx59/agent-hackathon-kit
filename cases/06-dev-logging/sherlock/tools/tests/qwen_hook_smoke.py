#!/usr/bin/env python3
"""Observe installed Qwen hooks using only a loopback mock provider.

No Sherlock skill or real credential is loaded. Raw inputs, outputs and hook
events stay in a fresh output root. This is runtime compatibility evidence.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


HOOK = '''import json, os, pathlib, sys, time
raw = sys.stdin.buffer.read()
root = pathlib.Path(__file__).parent
(root / ("hook-" + str(time.time_ns()) + ".json")).write_bytes(raw)
event = json.loads(raw)
mode = root.parent.name if root.name == "workspace" else root.name
if event.get("hook_event_name") == "PreToolUse" and mode == "deny":
 print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"mock explicit denial"}}))
elif event.get("hook_event_name") == "PreToolUse" and mode == "error":
 print("mock intentional crash", file=sys.stderr)
 sys.exit(7)
else:
 print("{}")
'''


def scenario(qwen, root, mode, relative_skill=False):
    work = root / mode
    work.mkdir()
    if relative_skill:
        case_root = work
        work = case_root / "workspace"
        work.mkdir()
        catalogue = case_root / "skill-catalogue" / "relative-smoke"
        catalogue.mkdir(parents=True)
        (catalogue / "SKILL.md").write_text("---\nname: relative-smoke\ndescription: RELATIVE_SKILL_DISCOVERY_20260906\n---\nA mock-only skill.\n")
    home = work / "home"
    home.mkdir()
    (work / "hook.py").write_text(HOOK)
    settings = {"hooks": {event: [{"matcher": "*", "hooks": [{
        "type": "command", "command": f'{sys.executable} "{work / "hook.py"}"',
        "timeout": 10000}]}] for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}}
    if relative_skill:
        settings["skills"] = {"directories": ["../skill-catalogue"],
                              "disabledLevels": ["project", "bundled", "extension"]}
    (work / ".qwen").mkdir()
    (work / ".qwen/settings.json").write_text(json.dumps(settings))
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            index = len(requests)
            requests.append(self.path)
            (work / f"request-{index}.json").write_bytes(raw)
            row = json.loads(raw)
            if index == 0:
                names = [tool.get("function", {}).get("name") for tool in row.get("tools", [])]
                name = "run_shell_command" if "run_shell_command" in names else next(
                    (name for name in names if name and "shell" in name), "run_shell_command")
                delta = {"role": "assistant", "tool_calls": [{"index": 0,
                    "id": "call_mock_hook", "type": "function", "function": {
                        "name": name, "arguments": json.dumps({"command": "printf mock > marker.txt"})}}]}
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "Mock compatibility test finished."}
                finish = "stop"
            chunks = [{"id": f"mock-{index}", "object": "chat.completion.chunk",
                       "created": int(time.time()), "model": "mock-hook", "choices": [
                           {"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": f"mock-{index}", "object": "chat.completion.chunk",
                       "created": int(time.time()), "model": "mock-hook", "choices": [
                           {"index": 0, "delta": {}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}]
            body = b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"
            (work / f"response-{index}.sse").write_bytes(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "LANG", "LC_ALL", "TMPDIR")}
    env.update(HOME=str(home), QWEN_HOME=str(home), OPENAI_API_KEY="mock-only",
               OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1", NO_PROXY="127.0.0.1,localhost")
    command = [str(qwen), "--auth-type", "openai", "--model", "mock-hook",
               "--approval-mode", "yolo", "--output-format", "json", "-p",
               "Use the shell once to create marker.txt, then finish."]
    (work / "launch.json").write_text(json.dumps({"argv": command, "cwd": str(work),
        "provider": env["OPENAI_BASE_URL"], "isolated_home": str(home)}, indent=2))
    started = time.monotonic()
    try:
        with (work / "stdout.json").open("wb") as out, (work / "stderr.txt").open("wb") as err:
            try:
                result = subprocess.run(command, cwd=work, env=env, stdout=out, stderr=err,
                                        stdin=subprocess.DEVNULL, timeout=45)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    hooks = [json.loads(path.read_text()) for path in sorted(work.glob("hook-*.json"))]
    result = {"mode": mode, "exit_code": code, "elapsed_s": time.monotonic() - started,
              "requests": len(requests), "marker_exists": (work / "marker.txt").exists(),
              "hooks": [{key: hook.get(key) for key in
                         ("hook_event_name", "session_id", "tool_name", "tool_use_id")} for hook in hooks]}
    if relative_skill:
        result["relative_skill_visible"] = "RELATIVE_SKILL_DISCOVERY_20260906" in (work / "request-0.json").read_text()
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--relative-skill", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700)
    results = [scenario(args.qwen, root, mode, args.relative_skill) for mode in ("allow", "deny", "error")]
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0 if (results[0]["marker_exists"] and not results[1]["marker_exists"]
                 and results[0]["hooks"] and results[1]["hooks"]
                 and (not args.relative_skill or all(row["relative_skill_visible"] for row in results))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
