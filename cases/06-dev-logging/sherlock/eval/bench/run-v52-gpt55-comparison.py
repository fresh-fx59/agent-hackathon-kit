#!/usr/bin/env python3
"""Sealed, one-shot GPT-5.5 comparison run for the immutable v52 Winevtx input.

This is intentionally a diagnostic launcher, not a second benchmark harness.
It admits exactly one explicitly approved manifest, owns the broker credential in
localhost proxy file-mode, and gives Qwen a dummy token.  The proxy is the only
process permitted to receive SHERLOCK_API_KEY.
"""
import argparse
import signal
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.request

SCHEMA = 1
MODEL = "gpt-5.5"
QWEN_VERSION = "0.22.0"
WATCHDOG_SECONDS = 600
QWEN_REQUEST_TIMEOUT_MS = 660000
QWEN_MAX_RETRIES = 0
FINALIZER_GATES = ("reportcheck", "citecheck", "triagecheck", "statecheck")
FINALIZER_INPUTS = ("report", "worklist", "rules")
STRICT_BUFFER_RETRY_MAX = 2
TRANSPORT = {"mode": "strict_buffer_retry",
             "retry_max": STRICT_BUFFER_RETRY_MAX,
             "deadline_seconds": WATCHDOG_SECONDS}

class Refusal(RuntimeError): pass

class TerminalFailure(Refusal):
    """A post-contact failure whose terminal status is part of the evidence."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status

def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")
def digest(data): return hashlib.sha256(data).hexdigest()
def file_digest(path): return digest(Path(path).read_bytes())
def now(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
def mkdir_new(path):
    try: os.mkdir(path, 0o700)
    except FileExistsError as exc: raise Refusal("control root already exists") from exc
def create(path, data, mode=0o600):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "wb") as out:
        out.write(data); out.flush(); os.fsync(out.fileno())

def read_json(path):
    p = Path(path)
    st = os.lstat(p)
    if not p.is_file() or p.is_symlink(): raise Refusal("unsafe regular file: %s" % p)
    return json.loads(p.read_text(encoding="utf-8"))

def qwen_client_policy(generation_config):
    """Validate the Qwen boundary before it can open an upstream connection."""
    timeout = generation_config.get("timeout")
    if type(timeout) is not int or timeout < QWEN_REQUEST_TIMEOUT_MS:
        raise Refusal("settings Qwen timeout must be at least 660000 ms")
    max_retries = generation_config.get("maxRetries")
    if type(max_retries) is not int or max_retries != QWEN_MAX_RETRIES:
        raise Refusal("settings Qwen maxRetries must be 0")
    return {"request_timeout_ms": timeout, "max_retries": max_retries}

def tree_inventory(root):
    root = Path(root)
    rows = []
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc": continue
        rel = str(path.relative_to(root))
        st = os.lstat(path)
        if path.is_symlink():
            rows.append({"path": rel, "kind": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            rows.append({"path": rel, "kind": "file", "bytes": st.st_size,
                         "sha256": file_digest(path)})
        elif path.is_dir():
            continue
        else: raise Refusal("unsupported prepared input entry: %s" % path)
    return rows

def prepared_inventory(root):
    root = Path(root).resolve()
    required = [root / "prompt.txt", root / ".qwen/settings.json", root / "skills/v52", root / "corpus"]
    if not all(p.exists() for p in required): raise Refusal("prepared root lacks v52 prompt/settings/skills")
    settings = read_json(required[1])
    model = settings.get("model", {})
    cfg = model.get("generationConfig", {})
    if (cfg.get("contextWindowSize") != 262000 or model.get("sessionTokenLimit") != 230000
            or cfg.get("reasoning") is not False or cfg.get("extra_body", {}).get("thinking", {}).get("type") != "disabled"
            or cfg.get("samplingParams", {}).get("max_tokens") != 20000):
        raise Refusal("settings are not the v52 r3 comparison settings")
    qwen_client_policy(cfg)
    dirs = settings.get("skills", {}).get("directories")
    if not isinstance(dirs, list) or len(dirs) != 1 or not dirs[0].endswith("/skills/v52"):
        raise Refusal("settings skill directory is not v52")
    hook = settings.get("hooks", {}).get("Stop", [{}])[0].get("hooks", [{}])[0].get("command", "")
    if '$QWEN_SKILL_ROOT/tools/stopcheck.py' not in hook:
        raise Refusal("settings Stop hook is not the documented v52 hook")
    # Bind every pre-existing prepared artifact except mutable control/log dirs.
    rows = []
    for name in ("prompt.txt", ".qwen/settings.json", "skills/v52"):
        p = root / name
        if p.is_file(): rows.append({"path": name, "kind": "file", "sha256": file_digest(p)})
        else: rows.extend({"path": name + "/" + row["path"], **{k:v for k,v in row.items() if k != "path"}}
                          for row in tree_inventory(p))
    corpus = root / "corpus"
    if not corpus.is_symlink(): raise Refusal("corpus must be an immutable symlink")
    rows.append({"path": "corpus", "kind": "symlink", "target": os.readlink(corpus)})
    target = corpus.resolve()
    if not target.is_dir(): raise Refusal("corpus target is unavailable")
    rows.extend({"path": "corpus/" + row["path"], **{k:v for k,v in row.items() if k != "path"}}
                for row in tree_inventory(target))
    return root, rows

def manifest_sha(manifest):
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    return digest(canonical(unsigned))
def load_manifest(control):
    manifest = read_json(Path(control, "manifest.json"))
    if manifest.get("schema") != SCHEMA: raise Refusal("manifest schema")
    approved = manifest_sha(manifest)
    if approved != manifest.get("manifest_sha256"): raise Refusal("manifest hash")
    return manifest, approved

def validate_manifest(control, manifest):
    root = Path(manifest["prepared_root"])
    actual_root, actual = prepared_inventory(root)
    if str(actual_root) != manifest["prepared_root"] or actual != manifest["prepared_inventory"]:
        raise Refusal("prepared input changed after approval")
    for item in manifest["harness_files"]:
        if file_digest(item["path"]) != item["sha256"]: raise Refusal("harness bytes changed")
    if manifest.get("model") != MODEL or manifest.get("qwen_version") != QWEN_VERSION:
        raise Refusal("strict target identity")
    if manifest.get("watchdog_seconds") != WATCHDOG_SECONDS:
        raise Refusal("600-second watchdog missing")
    if manifest.get("transport") != TRANSPORT:
        raise Refusal("strict buffered transport binding missing")
    settings = read_json(root / ".qwen/settings.json")
    if manifest.get("qwen_client") != qwen_client_policy(
            settings.get("model", {}).get("generationConfig", {})):
        raise Refusal("Qwen client timeout/retry binding missing")
    if manifest.get("authorization_sha256") != digest(manifest.get("authorization", "").encode()):
        raise Refusal("authorization binding")

def terminal(control, status, **extra):
    row = {"schema": SCHEMA, "finished_at": now(), "status": status, **extra}
    try: create(Path(control, "run-terminal.json"), canonical(row))
    except FileExistsError: pass

def validate_qwen_output(path):
    """Require a clean Qwen protocol result, not merely a zero child exit."""
    try:
        events = read_json(path)
    except Exception as exc:
        raise TerminalFailure("qwen_protocol_failed", "Qwen output is unreadable") from exc
    if not isinstance(events, list) or not events:
        raise TerminalFailure("qwen_protocol_failed", "Qwen output lacks result events")
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        for item in message.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text" and "[API Error:" in item.get("text", ""):
                raise TerminalFailure("qwen_protocol_failed", "Qwen reported API stream error")
    result = events[-1]
    if (not isinstance(result, dict) or result.get("type") != "result"
            or result.get("subtype") != "success" or result.get("is_error") is not False):
        raise TerminalFailure("qwen_protocol_failed", "Qwen lacks a clean terminal result")

def validate_finalizer_receipt(work):
    """Require a clean finalizer receipt bound to the report it claims to validate."""
    receipts = sorted(Path(work, "validation").glob("*/metadata.json"))
    if not receipts:
        raise TerminalFailure("validation_failed", "finalizer receipt missing")
    try:
        receipt = read_json(receipts[-1])
    except Exception as exc:
        raise TerminalFailure("validation_failed", "finalizer receipt unreadable") from exc
    if receipt.get("verdict") != "clean" or receipt.get("inputs_changed_during_validation") is not False:
        raise TerminalFailure("validation_failed", "finalizer verdict is not clean")
    gates = receipt.get("gates")
    if (not isinstance(gates, dict) or set(gates) != set(FINALIZER_GATES)
            or any(not isinstance(gates[name], dict)
                   or gates[name].get("exit_code") != 0
                   or gates[name].get("parsed_blocking") != 0
                   for name in FINALIZER_GATES)):
        raise TerminalFailure("validation_failed", "finalizer gates are not clean")
    inputs_after = receipt.get("inputs_after", {})
    for name in FINALIZER_INPUTS:
        current = Path(work, name + (".md" if name == "report" else ".tsv"))
        expected = inputs_after.get(name, {}).get("sha256")
        if not isinstance(expected, str) or not current.is_file() or file_digest(current) != expected:
            raise TerminalFailure("validation_failed", "finalizer %s identity no longer matches" % name)

def validate_terminal_ledger(trace, strict_buffer_enabled=False):
    """Classify identity evidence separately from completed-call failures."""
    ledger = Path(trace, "upstream.jsonl")
    if not ledger.is_file():
        raise TerminalFailure("transport_failed", "proxy ledger missing")
    try:
        # The proxy appends atomically enough for supervision to ignore an
        # unfinished last line, as observe_trace already does.
        rows = [json.loads(line) for line in ledger.read_bytes().split(b"\n")[:-1] if line]
    except Exception as exc:
        raise TerminalFailure("transport_failed", "proxy ledger is malformed") from exc
    if not rows:
        raise TerminalFailure("transport_failed", "proxy ledger has no completed calls")
    failures = []
    strict_pending = {}
    for row in rows:
        status = row.get("status")
        successful = isinstance(status, int) and 200 <= status < 300
        withheld_missing_identity = (strict_buffer_enabled
                                     and row.get("strict_buffer_failure") is True
                                     and row.get("returned_model") is None)
        if successful and row.get("returned_model") != MODEL and not withheld_missing_identity:
            raise TerminalFailure("identity_failed", "missing or wrong returned model identity")
        failed = (not successful or row.get("upstream_error")
                  or (row.get("stream") and row.get("stream_complete") is not True))
        if failed and strict_buffer_enabled and row.get("strict_buffer_failure") is True:
            client_request_id = row.get("client_request_id")
            if isinstance(client_request_id, str) and client_request_id:
                strict_pending[client_request_id] = strict_pending.get(client_request_id, 0) + 1
                continue
            failures.append(row)
            continue
        if failed:
            failures.append(row)
            continue
        if strict_buffer_enabled and "strict_buffer_recovery" in row:
            recovered = row["strict_buffer_recovery"]
            client_request_id = row.get("client_request_id")
            pending = strict_pending.get(client_request_id, 0)
            if (not isinstance(client_request_id, str) or not client_request_id
                    or not isinstance(recovered, int) or isinstance(recovered, bool)
                    or recovered < 1 or pending < recovered):
                failures.append(row)
            else:
                pending -= recovered
                if pending:
                    strict_pending[client_request_id] = pending
                else:
                    del strict_pending[client_request_id]
    if strict_pending:
        raise TerminalFailure("transport_failed", "strict buffered attempts remain unrecovered")
    if failures:
        raise TerminalFailure("transport_failed", "%d failed ledger responses" % len(failures))
    return rows

def port_available(port):
    s = socket.socket();
    try: s.bind(("127.0.0.1", port))
    except OSError as exc: raise Refusal("localhost port unavailable: %s" % port) from exc
    finally: s.close()

def prepare(args):
    prepared, inventory = prepared_inventory(args.prepared_root)
    settings = read_json(prepared / ".qwen/settings.json")
    qwen_client = qwen_client_policy(settings.get("model", {}).get("generationConfig", {}))
    control = Path(args.control_root)
    mkdir_new(control)
    manifest = {"schema": SCHEMA, "created_at": now(), "prepared_root": str(prepared),
                "prepared_inventory": inventory, "harness_files": [{"path": str(Path(x).resolve()), "sha256": file_digest(x)} for x in [__file__, args.proxy, str(Path(args.proxy).with_name("lane_guard.py")), args.qwen]], "proxy": str(Path(args.proxy).resolve()), "qwen": str(Path(args.qwen).resolve()), "model": MODEL, "expected_returned_identity": MODEL,
                "qwen_version": QWEN_VERSION, "watchdog_seconds": WATCHDOG_SECONDS,
                "transport": TRANSPORT,
                "qwen_client": qwen_client,
                "upstream_base": args.upstream_base.rstrip("/"), "authorization": args.authorization,
                "authorization_sha256": digest(args.authorization.encode("utf-8")),
                "nonce": secrets.token_hex(32), "comparison": {"r3_qwen_skill_root": "unset_in_direct_launcher",
                "gpt55_qwen_skill_root": "set_by_launcher_to_prepared_skills_v52"}}
    # Hash the canonical manifest payload; the approval binds this exact value.
    manifest["manifest_sha256"] = manifest_sha(manifest)
    create(control / "manifest.json", canonical(manifest))
    print(json.dumps({"control_root": str(control), "manifest_sha256": manifest["manifest_sha256"],
                      "nonce": manifest["nonce"], "watchdog_seconds": WATCHDOG_SECONDS}, sort_keys=True))

def run(args):
    control = Path(args.control_root)
    proxy = None
    qwen = None
    def interrupted(signum, frame): raise Refusal("signal %s" % signum)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    qwen_rc = None
    try:
        manifest, approved = load_manifest(control)
        if args.approval != approved: raise Refusal("approval must equal manifest sha256")
        validate_manifest(control, manifest)
        if str(Path(args.proxy).resolve()) != manifest["proxy"] or str(Path(args.qwen).resolve()) != manifest["qwen"]: raise Refusal("executable path changed")
        if not os.environ.get("SHERLOCK_API_KEY_FILE"): raise Refusal("SHERLOCK_API_KEY_FILE absent (use secret wrapper --file-env)")
        if manifest["upstream_base"] != "http://127.0.0.1:8317/v1": raise Refusal("subscription route changed")
        qwen_cmd = (["node", args.qwen] if args.qwen.endswith(".js") else [args.qwen])
        qwen_version = subprocess.check_output(qwen_cmd + ["--version"], text=True, stderr=subprocess.STDOUT,
                                                timeout=20).strip()
        if qwen_version != QWEN_VERSION: raise Refusal("Qwen version is %r, need %s" % (qwen_version, QWEN_VERSION))
        port_available(args.listen_port)
        nonce_root = Path(args.nonce_root); nonce_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Durable O_EXCL admission happens only after all non-contact checks pass.
        create(nonce_root / (manifest["nonce"] + ".json"), canonical({"nonce": manifest["nonce"], "manifest_sha256": approved}))
        run_root = Path(manifest["prepared_root"])
        trace = control / "trace"; trace.mkdir(mode=0o700)
        key_file = Path(os.environ["SHERLOCK_API_KEY_FILE"])
        route = {"schema": 1, "base": manifest["upstream_base"], "model": MODEL,
                 "expected_returned_identity": MODEL, "key_file": str(key_file), "generation": 1}
        create(control / "proxy-route.json", canonical(route))
        penv = {"LISTEN_PORT": str(args.listen_port), "UPSTREAM_ROUTE_FILE": str(control / "proxy-route.json"),
                "UPSTREAM_LOG": str(trace / "upstream.jsonl"), "UPSTREAM_BODY_DIR": str(trace / "bodies"),
                "UPSTREAM_READ_TIMEOUT": str(WATCHDOG_SECONDS), "UPSTREAM_EXPECTED_RETURNED_IDENTITY": MODEL,
                "UPSTREAM_SUBSTITUTION_RETRY_MAX": "0", "UPSTREAM_RETRY_MAX": "0", "UPSTREAM_ROUTE_FALLBACKS": "", "UPSTREAM_CACHE_GUARD": "0", "UPSTREAM_STRICT_BUFFER_RETRY_MAX": str(STRICT_BUFFER_RETRY_MAX), "UPSTREAM_STRICT_BUFFER_DEADLINE_S": str(WATCHDOG_SECONDS), "UPSTREAM_INFLIGHT": str(trace / "inflight.json"), "UPSTREAM_LANE_ABORT": str(trace / "lane-abort.json")}
        env = os.environ.copy(); env.update(penv)
        proxy = subprocess.Popen([sys.executable, args.proxy], stdout=open(trace / "proxy.stdout", "wb"),
                                 stderr=open(trace / "proxy.stderr", "wb"), env=env, start_new_session=True)
        for _ in range(50):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % args.listen_port, timeout=1) as r:
                    if r.status == 200: break
            except Exception: time.sleep(.1)
        else: raise Refusal("proxy did not bind localhost")
        qenv = {"HOME": str(run_root / "home"), "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % args.listen_port,
                "OPENAI_API_KEY": "proxy-owned-credential", "QWEN_SKILL_ROOT": str(run_root / "skills/v52"),
                "NO_PROXY": "127.0.0.1,localhost"}
        fullenv = {k:v for k,v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "TZ")}
        fullenv.update(qenv); fullenv["PYTHONDONTWRITEBYTECODE"] = "1"
        output = open(control / "qwen-output.json", "wb"); err = open(control / "qwen-stderr.log", "wb")
        try:
            qwen = subprocess.Popen(qwen_cmd + ["--auth-type", "openai", "--model", MODEL,
                                      "--max-session-turns", "-1", "--max-tool-calls", "-1", "--openai-logging",
                                      "--openai-logging-dir", str(trace / "openai-logs"), "--output-format", "json",
                                      (run_root / "prompt.txt").read_text(encoding="utf-8")], cwd=run_root,
                                      stdin=subprocess.DEVNULL, stdout=output, stderr=err, env=fullenv, start_new_session=True)
            # Supervise the owned children: changed inputs or a dead proxy abort the Qwen process.
            create(control / "launch-receipt.json", canonical({"started_at": now(), "qwen_pid":qwen.pid, "proxy_pid":proxy.pid, "manifest_sha256":approved}))
            while qwen.poll() is None:
                if proxy.poll() is not None:
                    qwen.terminate(); raise Refusal("proxy exited during Qwen run")
                try: validate_manifest(control, manifest)
                except Exception:
                    qwen.terminate(); raise
                observe_trace(trace, strict_buffer_enabled=True)
                time.sleep(0.5)
            qwen_rc = qwen.returncode
            if proxy.poll() is not None: raise Refusal("proxy exited before run completion")
            if qwen_rc != 0:
                terminal(control, "qwen_failed", qwen_exit=qwen_rc,
                         manifest_sha256=approved, nonce=manifest["nonce"])
                return qwen_rc
            validate_manifest(control, manifest)
            observe_trace(trace, strict_buffer_enabled=True)
            validate_terminal_ledger(trace, strict_buffer_enabled=True)
            validate_qwen_output(control / "qwen-output.json")
            validate_finalizer_receipt(run_root / "work")
        finally: output.close(); err.close()
        terminal(control, "completed", qwen_exit=qwen_rc,
                 manifest_sha256=approved, nonce=manifest["nonce"])
        return qwen_rc
    except TerminalFailure as exc:
        terminal(control, exc.status, error="%s: %s" % (type(exc).__name__, exc),
                 qwen_exit=qwen_rc)
        print("launcher refusal: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:
        terminal(control, "precontact_refused" if proxy is None else "launcher_failed",
                 error="%s: %s" % (type(exc).__name__, exc))
        print("launcher refusal: %s" % exc, file=sys.stderr)
        return 2
    finally:
        for child in (qwen, proxy):
            if child is not None:
                try: os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait()

def observe_trace(trace, strict_buffer_enabled=False):
    if (trace / "lane-abort.json").exists(): raise Refusal("proxy lane abort")
    inflight = trace / "inflight.json"
    if inflight.exists():
        try: live = read_json(inflight)
        except FileNotFoundError: live = {}
        for row in live.get("requests", {}).values():
            start = datetime.datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")).timestamp()
            if time.time() - start > WATCHDOG_SECONDS: raise Refusal("600-second request watchdog")
    ledger = trace / "upstream.jsonl"
    if not ledger.exists(): return
    data = ledger.read_bytes()
    for line in data.split(b"\n")[:-1]:
        if not line: continue
        row = json.loads(line)
        status = row.get("status")
        if (isinstance(status, int) and 200 <= status < 300 and row.get("returned_model") != MODEL
                and not (strict_buffer_enabled and row.get("strict_buffer_failure") is True
                         and row.get("returned_model") is None)):
            raise TerminalFailure("identity_failed", "missing or wrong returned model identity")
        if row.get("body_capture_error") or row.get("body_request_truncated") or row.get("body_response_truncated"):
            raise Refusal("capture incomplete")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--prepared-root", required=True); a.add_argument("--control-root", required=True)
    a.add_argument("--qwen", required=True); a.add_argument("--proxy", required=True)
    a.add_argument("--upstream-base", required=True); a.add_argument("--authorization", required=True); a.set_defaults(fn=prepare)
    a = sub.add_parser("run")
    a.add_argument("--control-root", required=True); a.add_argument("--approval", required=True); a.add_argument("--nonce-root", required=True)
    a.add_argument("--qwen", required=True); a.add_argument("--proxy", required=True); a.add_argument("--listen-port", type=int, default=18795); a.set_defaults(fn=run)
    args = p.parse_args()
    try: result = args.fn(args)
    except Refusal as exc: print("launcher refusal: %s" % exc, file=sys.stderr); result = 2
    sys.exit(result or 0)
if __name__ == "__main__": main()
