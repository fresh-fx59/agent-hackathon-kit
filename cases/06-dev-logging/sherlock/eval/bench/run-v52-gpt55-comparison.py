#!/usr/bin/env python3
"""Sealed, one-shot GPT-5.5 comparison run for the immutable v52 Winevtx input.

This is intentionally a diagnostic launcher, not a second benchmark harness.
It admits exactly one explicitly approved manifest, owns the broker credential in
localhost proxy file-mode, and gives Qwen a dummy token.  The proxy is the only
process permitted to receive SHERLOCK_API_KEY.
"""
import argparse
import atexit
import re
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
# Model under test is bound in the manifest. Only these ids are admitted; each
# carries a label so a run is never mistaken for a 1:1 comparison with r4.
ALLOWED_MODELS = {
    "gpt-5.5": {"family": "openai-gpt", "comparable_to_gpt55_r4": True},
    "claude-opus-5": {"family": "anthropic-claude", "comparable_to_gpt55_r4": False},
}
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

SKILL_NAME = "sherlock"
SKILLS_ROOT = "skills-root"
STOP_HOOK_WRAPPER = Path(__file__).resolve().with_name("stop-hook-log.py")
STOP_HOOK_COMMAND = ('python3 "$SHERLOCK_STOP_HOOK_WRAPPER" '
                     'python3 "$QWEN_SKILL_ROOT/tools/stopcheck.py"')
PREFLIGHT_DEAD_BASE = "http://127.0.0.1:9/v1"
PREFLIGHT_TIMEOUT_S = 90
# Selectable immutable skill packages. v52 stays selectable unchanged; v53 adds
# the load-time data index (built here, never inside the Stop hook).
PACKAGES = ("v52", "v53")
PACKAGE = "v52"
INDEXED_PACKAGES = ("v53",)
# Spec 2026-09-24 item 3: Qwen Stop-hook timeout set explicitly, in seconds
# (Qwen reads values < 1000 as seconds). 280 s worst measured at 1 GB x ~2.
STOP_HOOK_TIMEOUT_S = 600
# Spec item 8: whole-run wall clock (r5 stage 2 alone took 74 min).
MAX_WALL_SECONDS = 21600
# Spec item 7: 3 identical Stop-hook blocks -> terminal stop_hook_loop.
STOP_HOOK_LOOP_LIMIT = 3
PERMISSION_DENIED_RE = re.compile(r"permission was declined[^\n\"]{0,200}", re.I)

class Refusal(RuntimeError): pass

class TerminalFailure(Refusal):
    """A post-contact failure whose terminal status is part of the evidence."""
    def __init__(self, status, message, details=None):
        super().__init__(message)
        self.status = status
        self.details = details or {}

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
    required = [root / "prompt.txt", root / ".qwen/settings.json", root / skill_dir(), root / "corpus"]
    if not all(p.exists() for p in required): raise Refusal("prepared root lacks %s prompt/settings/skills" % PACKAGE)
    settings = read_json(required[1])
    model = settings.get("model", {})
    cfg = model.get("generationConfig", {})
    if (cfg.get("contextWindowSize") != 262000 or model.get("sessionTokenLimit") != 230000
            or cfg.get("reasoning") is not False or cfg.get("extra_body", {}).get("thinking", {}).get("type") != "disabled"
            or cfg.get("samplingParams", {}).get("max_tokens") != 20000):
        raise Refusal("settings are not the v52 r3 comparison settings")
    qwen_client_policy(cfg)
    # Qwen discovers <directory>/<name>/SKILL.md, so the directory must be a
    # PARENT whose only entry is the sherlock link to the untouched skills/v52.
    dirs = settings.get("skills", {}).get("directories")
    if not isinstance(dirs, list) or len(dirs) != 1 or dirs[0] != str(root / SKILLS_ROOT):
        raise Refusal("settings skill directory is not the staged %s parent" % SKILLS_ROOT)
    check_skill_root(root)
    hook = settings.get("hooks", {}).get("Stop", [{}])[0].get("hooks", [{}])[0].get("command", "")
    if hook != STOP_HOOK_COMMAND:
        raise Refusal("settings Stop hook is not the logged v52 hook wrapper")
    if stop_hook_timeout(settings) != STOP_HOOK_TIMEOUT_S:
        raise Refusal("settings Stop hook timeout must be %d s" % STOP_HOOK_TIMEOUT_S)
    # Bind every pre-existing prepared artifact except mutable control/log dirs.
    rows = []
    for name in ("prompt.txt", ".qwen/settings.json", skill_dir()):
        p = root / name
        if p.is_file(): rows.append({"path": name, "kind": "file", "sha256": file_digest(p)})
        else: rows.extend({"path": name + "/" + row["path"], **{k:v for k,v in row.items() if k != "path"}}
                          for row in tree_inventory(p))
    link = root / SKILLS_ROOT / SKILL_NAME
    rows.append({"path": SKILLS_ROOT + "/" + SKILL_NAME, "kind": "symlink", "target": os.readlink(link)})
    corpus = root / "corpus"
    if not corpus.is_symlink(): raise Refusal("corpus must be an immutable symlink")
    rows.append({"path": "corpus", "kind": "symlink", "target": os.readlink(corpus)})
    target = corpus.resolve()
    if not target.is_dir(): raise Refusal("corpus target is unavailable")
    rows.extend({"path": "corpus/" + row["path"], **{k:v for k,v in row.items() if k != "path"}}
                for row in tree_inventory(target))
    return root, rows

def use_package(name):
    global PACKAGE
    if name not in PACKAGES:
        raise Refusal("package %r is not selectable; allowed: %s" % (name, ", ".join(PACKAGES)))
    PACKAGE = name

def skill_dir():
    return "skills/" + PACKAGE

def check_skill_root(root):
    parent = Path(root) / SKILLS_ROOT
    link = parent / SKILL_NAME
    if not parent.is_dir() or parent.is_symlink() or sorted(os.listdir(parent)) != [SKILL_NAME]:
        raise Refusal("%s must hold exactly one %s entry" % (SKILLS_ROOT, SKILL_NAME))
    if not link.is_symlink() or link.resolve() != (Path(root) / skill_dir()).resolve():
        raise Refusal("%s/%s must be a symlink to %s" % (SKILLS_ROOT, SKILL_NAME, skill_dir()))

def stage_harness_layout(root):
    """Harness-side staging; never writes inside the skill package."""
    root = Path(root).resolve()
    parent = root / SKILLS_ROOT
    parent.mkdir(mode=0o755, exist_ok=True)
    link = parent / SKILL_NAME
    if not link.is_symlink():
        link.symlink_to(Path("..") / "skills" / PACKAGE, target_is_directory=True)
    path = root / ".qwen/settings.json"
    settings = read_json(path)
    settings.setdefault("skills", {})["directories"] = [str(parent)]
    try:
        entry = settings["hooks"]["Stop"][0]["hooks"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise Refusal("settings lack the v52 Stop hook") from exc
    if entry.get("command") not in (STOP_HOOK_COMMAND, 'python3 "$QWEN_SKILL_ROOT/tools/stopcheck.py"'):
        raise Refusal("settings Stop hook is not the documented v52 hook")
    entry["command"] = STOP_HOOK_COMMAND
    entry["timeout"] = STOP_HOOK_TIMEOUT_S
    tmp = path.with_name(path.name + ".staging")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def stop_hook_timeout(settings):
    try: return settings["hooks"]["Stop"][0]["hooks"][0].get("timeout")
    except (KeyError, IndexError, TypeError, AttributeError): return None

def preflight_skill_list(qwen_cmd, run_root, control):
    """Prove Qwen lists the skill before any model contact (dead upstream)."""
    import tempfile
    home = Path(tempfile.mkdtemp(prefix="sherlock-preflight-"))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "TZ")}
    env.update({"HOME": str(home), "OPENAI_BASE_URL": PREFLIGHT_DEAD_BASE,
                "OPENAI_API_KEY": "preflight-dummy", "QWEN_SKILL_ROOT": str(run_root / skill_dir()),
                "SHERLOCK_STOP_HOOK_WRAPPER": str(STOP_HOOK_WRAPPER),
                "SHERLOCK_STOP_HOOK_LOG": str(home / "stop-hook.jsonl"),
                "NO_PROXY": "127.0.0.1,localhost", "PYTHONDONTWRITEBYTECODE": "1"})
    init = None
    proc = subprocess.Popen(qwen_cmd + ["--auth-type", "openai", "--model", MODEL,
                            "--output-format", "stream-json", "preflight"], cwd=run_root,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    import threading
    def kill():
        try: os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
    timer = threading.Timer(PREFLIGHT_TIMEOUT_S, kill); timer.start()
    try:
        for line in proc.stdout:
            try: event = json.loads(line)
            except ValueError: continue
            if isinstance(event, dict) and event.get("type") == "system":
                init = event; break
    finally:
        timer.cancel(); kill(); proc.stdout.close(); proc.wait()
        shutil.rmtree(home, ignore_errors=True)
    listed = sorted(init.get("slash_commands") or []) if init else None
    ok = bool(listed) and SKILL_NAME in listed
    create(Path(control, "skill-preflight.json"), canonical({
        "checked_at": now(), "upstream": PREFLIGHT_DEAD_BASE, "skill": SKILL_NAME,
        "init_event_seen": init is not None, "slash_commands": listed, "passed": ok}))
    if not ok:
        raise Refusal("preflight: skill %r is not in Qwen's skill list (%s)" % (
            SKILL_NAME, "no init event" if init is None else ", ".join(listed)))

MAX_STAGE_SESSIONS = 12
_CONTINUE = __import__("re").compile(r"^\s*2\)\s*(/sherlock\s.+?)\s*$", __import__("re").M)

def continuation_prompt(work, hook_log, since):
    """-> the exact `/sherlock ...` line when THIS session ended in an accepted handoff."""
    handoff = Path(work, "handoff.txt")
    if not handoff.is_file() or handoff.stat().st_mtime < since:
        return None
    last = None
    if Path(hook_log).is_file():
        for line in Path(hook_log).read_text(encoding="utf-8").splitlines():
            try: last = json.loads(line)
            except ValueError: continue
    if (not last or last.get("decision") != "allow" or "handoff accepted" not in (last.get("reason") or "")
            or str(last.get("ts", "")) < datetime.datetime.fromtimestamp(since, datetime.timezone.utc).isoformat()):
        return None
    m = _CONTINUE.search(handoff.read_text(encoding="utf-8"))
    return m.group(1) if m else None

def finalizer_diagnostics(work, control):
    """Name the missing step: skill invoked, work/ contents, Stop-hook decisions."""
    work = Path(work)
    listing = sorted(str(p.relative_to(work)) for p in work.rglob("*")) if work.is_dir() else None
    skill_calls = None
    if control is not None:
        try:
            events = []
            for out in sorted(Path(control).glob("qwen-output*.json")):
                loaded = json.loads(out.read_text(encoding="utf-8"))
                events.extend(loaded if isinstance(loaded, list) else [])
            skill_calls = 0
            for event in events:
                content = (event.get("message") or {}).get("content", []) if isinstance(event, dict) else []
                for item in content if isinstance(content, list) else []:
                    if (isinstance(item, dict) and item.get("type") == "tool_use"
                            and item.get("name") == "skill"
                            and SKILL_NAME in json.dumps(item.get("input"))):
                        skill_calls += 1
        except Exception:
            skill_calls = None
    decisions = None
    if control is not None:
        log = Path(control, "trace", "stop-hook.jsonl")
        if log.is_file():
            decisions = []
            for line in log.read_text(encoding="utf-8").splitlines():
                try: row = json.loads(line)
                except ValueError: continue
                decisions.append("%s:%s" % (row.get("decision"), row.get("reason")))
    missing = ("skill never invoked" if skill_calls == 0 else
               "finalize.py never ran" if listing is not None and "validation" not in listing
               else "no validation/*/metadata.json")
    return {"missing_step": missing, "skill_invocations": skill_calls,
            "work_contents": listing, "stop_hook_decisions": decisions}

def package_sha(inventory):
    """sha256 over the canonical inventory rows of the selected package tree."""
    prefix = skill_dir() + "/"
    return digest(canonical(sorted((r for r in inventory if r["path"].startswith(prefix)),
                                   key=lambda r: r["path"])))

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
    if (manifest.get("model") not in ALLOWED_MODELS or manifest.get("model") != MODEL
            or manifest.get("model_under_test") != dict(ALLOWED_MODELS[manifest["model"]], model=manifest["model"])
            or manifest.get("qwen_version") != QWEN_VERSION):
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
    extra = {k: v for k, v in extra.items() if v is not None or k == "qwen_exit"}
    row = {"schema": SCHEMA, "finished_at": now(), "status": status, **extra}
    try: create(Path(control, "run-terminal.json"), canonical(row))
    except FileExistsError: pass
    except OSError: return

def write_done(control):
    """run.done always follows run-terminal.json (spec item 8); idempotent."""
    try:
        term = Path(control, "run-terminal.json")
        status = read_json(term).get("status") if term.is_file() else None
        create(Path(control, "run.done"), canonical({"finished_at": now(), "status": status}))
    except (FileExistsError, OSError, ValueError): pass

def ensure_terminal(control, status, **extra):
    """atexit / signal path: never leave a run without terminal + run.done."""
    if control is None or not Path(control).is_dir(): return
    if not Path(control, "run-terminal.json").exists():
        terminal(control, status, **extra)
    write_done(control)

def normalize_reason(reason):
    return re.sub(r"\s+", " ", re.sub(r"\d+(\.\d+)?", "N", str(reason or ""))).strip()

def stop_hook_loop(log, since=None, limit=STOP_HOOK_LOOP_LIMIT):
    """Spec item 7: -> the repeated key when `limit` block rows share
    (normalized reason, report sha256, message sha256), else None."""
    log = Path(log)
    if not log.is_file(): return None
    seen = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try: row = json.loads(line)
        except ValueError: continue
        if not isinstance(row, dict) or row.get("decision") != "block": continue
        if since is not None:
            try:
                ts = datetime.datetime.fromisoformat(row["ts"]).timestamp()
                if ts < since: continue
            except (KeyError, TypeError, ValueError): pass
        key = (normalize_reason(row.get("reason")), row.get("report_sha256"), row.get("msg_sha256"))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= limit: return {"reason": key[0], "report_sha256": key[1],
                                       "msg_sha256": key[2], "count": seen[key]}
    return None

def permission_denials(path, session, trace):
    """Record Qwen non-interactive permission refusals as run-log events."""
    try: text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError: return 0
    hits = [m.group(0) for m in PERMISSION_DENIED_RE.finditer(text)]
    if hits:
        with open(Path(trace, "permission-denied.jsonl"), "a", encoding="utf-8") as fh:
            for i, h in enumerate(hits):
                fh.write(json.dumps({"event": "permission_denied", "session": session, "n": i,
                                     "source": Path(path).name, "recorded_at": now(),
                                     "excerpt": h}, ensure_ascii=False, sort_keys=True) + "\n")
    return len(hits)

def build_index(run_root, control, py=None):
    """Build the v53 data index at corpus load, outside the Stop hook."""
    tool = Path(run_root, skill_dir(), "tools", "buildindex.py")
    log = Path(control, "buildindex.log")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "TZ")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "SHERLOCK_INDEX_ROOT": str(Path(run_root, "index"))})
    started = time.time()
    with open(log, "wb") as fh:
        rc = subprocess.call([py or sys.executable, str(tool), "--corpus", str(Path(run_root, "corpus")),
                              "--index-root", str(Path(run_root, "index"))],
                             cwd=run_root, stdout=fh, stderr=subprocess.STDOUT, env=env)
    row = {"rc": rc, "elapsed_s": round(time.time() - started, 3), "log": log.name}
    create(Path(control, "buildindex.json"), canonical(row))
    if rc != 0:
        raise TerminalFailure("index_build_failed", "buildindex.py exited %d" % rc, row)
    return row

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

def validate_finalizer_receipt(work, control=None):
    """Require a clean finalizer receipt bound to the report it claims to validate."""
    receipts = sorted(Path(work, "validation").glob("*/metadata.json"))
    if not receipts:
        info = finalizer_diagnostics(work, control)
        raise TerminalFailure("validation_failed", "finalizer receipt missing: %s; skill_invocations=%s; work=%s; stop_hook=%s" % (
            info["missing_step"], info["skill_invocations"], info["work_contents"],
            info["stop_hook_decisions"]), details=info)
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

QUOTA_TYPES = ("usage_limit_reached", "model_cooldown", "insufficient_quota")

def quota_signal(row):
    """-> dict naming provider/plan/reset when a ledger row is a quota 429, else None."""
    if not isinstance(row, dict) or row.get("status") != 429:
        return None
    raw = row.get("upstream_error")
    err = {}
    if isinstance(raw, dict):
        err = raw.get("error", raw)
    elif isinstance(raw, str):
        try:
            obj = json.loads(raw)
            err = obj.get("error", obj) if isinstance(obj, dict) else {}
        except ValueError:
            err = {}
    if not err and isinstance(raw, str):
        # The proxy clips upstream_error, so the JSON is often cut: salvage fields.
        import re
        for key in ("type", "code", "provider", "plan_type", "model"):
            m = re.search(r'"%s"\s*:\s*"([^"]*)"' % key, raw)
            if m: err[key] = m.group(1)
        for key in ("resets_at", "reset_seconds"):
            m = re.search(r'"%s"\s*:\s*(\d+)' % key, raw)
            if m: err[key] = int(m.group(1))
    kind = err.get("type") or err.get("code")
    if kind not in QUOTA_TYPES:
        text = raw if isinstance(raw, str) else json.dumps(raw)
        kind = next((t for t in QUOTA_TYPES if t in (text or "")), None)
        if kind is None:
            return None
    resets_at = err.get("resets_at")
    if resets_at is None and isinstance(err.get("reset_seconds"), (int, float)) and isinstance(row.get("ts_ms"), (int, float)):
        resets_at = int(row["ts_ms"] / 1000 + err["reset_seconds"])
    iso = (datetime.datetime.fromtimestamp(resets_at, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
           if isinstance(resets_at, (int, float)) else None)
    return {"kind": kind, "provider": err.get("provider"), "plan_type": err.get("plan_type"),
            "resets_at": resets_at, "resets_at_iso": iso, "model": err.get("model"),
            "request_id": row.get("request_id"), "ts": row.get("ts"), "route_base": row.get("route_base")}

def quota_failure(signals):
    """Merge quota rows: provider from any row, plan/reset from the most informative."""
    first = signals[0]
    provider = next((q["provider"] for q in signals if q.get("provider")), None)
    plan = next((q["plan_type"] for q in signals if q.get("plan_type")), None)
    resets = max((q["resets_at"] for q in signals if isinstance(q.get("resets_at"), (int, float))), default=None)
    iso = (datetime.datetime.fromtimestamp(resets, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
           if resets is not None else None)
    details = {"quota_rows": len(signals), "first_kind": first["kind"], "first_ts": first["ts"],
               "first_request_id": first["request_id"], "provider": provider, "plan_type": plan,
               "resets_at": resets, "resets_at_iso": iso, "route_base": first["route_base"],
               "kinds": sorted({q["kind"] for q in signals})}
    return TerminalFailure("quota_exhausted",
        "upstream quota exhausted: provider=%s plan_type=%s resets_at=%s (%s); %d quota 429 row(s), first %s at %s" % (
            provider, plan, iso, resets, len(signals), first["kind"], first["ts"]), details=details)

class QuotaWatch:
    """Incremental ledger tail: fires on the first quota 429 so no turns are wasted."""
    def __init__(self, trace):
        self.path = Path(trace, "upstream.jsonl"); self.offset = 0; self.partial = b""
        self.signals = []
    def poll(self):
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset); chunk = fh.read(); self.offset = fh.tell()
        except FileNotFoundError:
            return None
        data = self.partial + chunk
        lines = data.split(b"\n"); self.partial = lines.pop()
        for line in lines:
            if not line.strip(): continue
            try: row = json.loads(line)
            except ValueError: continue
            q = quota_signal(row)
            if q: self.signals.append(q)
        return quota_failure(self.signals) if self.signals else None

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
    quota = [q for q in (quota_signal(r) for r in rows) if q]
    if quota:
        raise quota_failure(quota)
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

def use_model(model):
    global MODEL
    if model not in ALLOWED_MODELS:
        raise Refusal("model %r is not admitted; allowed: %s" % (model, ", ".join(sorted(ALLOWED_MODELS))))
    MODEL = model

def prepare(args):
    use_model(getattr(args, "model", None) or "gpt-5.5")
    use_package(getattr(args, "package", None) or "v52")
    stage_harness_layout(args.prepared_root)
    prepared, inventory = prepared_inventory(args.prepared_root)
    settings = read_json(prepared / ".qwen/settings.json")
    qwen_client = qwen_client_policy(settings.get("model", {}).get("generationConfig", {}))
    control = Path(args.control_root)
    mkdir_new(control)
    manifest = {"schema": SCHEMA, "created_at": now(), "prepared_root": str(prepared),
                "prepared_inventory": inventory, "harness_files": [{"path": str(Path(x).resolve()), "sha256": file_digest(x)} for x in [__file__, args.proxy, str(Path(args.proxy).with_name("lane_guard.py")), str(STOP_HOOK_WRAPPER), args.qwen]], "proxy": str(Path(args.proxy).resolve()), "qwen": str(Path(args.qwen).resolve()), "model": MODEL, "expected_returned_identity": MODEL,
                "package": PACKAGE, "package_sha256": package_sha(inventory),
                "stop_hook_timeout_s": STOP_HOOK_TIMEOUT_S,
                "model_under_test": dict(ALLOWED_MODELS[MODEL], model=MODEL),
                "qwen_version": QWEN_VERSION, "watchdog_seconds": WATCHDOG_SECONDS,
                "transport": TRANSPORT,
                "qwen_client": qwen_client,
                "upstream_base": args.upstream_base.rstrip("/"), "authorization": args.authorization,
                "authorization_sha256": digest(args.authorization.encode("utf-8")),
                "nonce": secrets.token_hex(32), "comparison": {"r3_qwen_skill_root": "unset_in_direct_launcher",
                "gpt55_qwen_skill_root": "set_by_launcher_to_prepared_skills_" + PACKAGE}}
    # Hash the canonical manifest payload; the approval binds this exact value.
    manifest["manifest_sha256"] = manifest_sha(manifest)
    create(control / "manifest.json", canonical(manifest))
    print(json.dumps({"control_root": str(control), "manifest_sha256": manifest["manifest_sha256"],
                      "nonce": manifest["nonce"], "watchdog_seconds": WATCHDOG_SECONDS}, sort_keys=True))

def run(args):
    control = Path(args.control_root)
    proxy = None
    qwen = None
    def interrupted(signum, frame):
        raise TerminalFailure("interrupted", "signal %s" % signum, {"signal": signum})
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    atexit.register(ensure_terminal, control, "launcher_exited_without_terminal")
    run_started = time.time()
    max_wall = getattr(args, "max_wall_seconds", None) or MAX_WALL_SECONDS
    qwen_rc = None
    try:
        manifest, approved = load_manifest(control)
        use_model(manifest.get("model"))
        use_package(manifest.get("package", "v52"))
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
        preflight_skill_list(qwen_cmd, Path(manifest["prepared_root"]), control)
        validate_manifest(control, manifest)
        nonce_root = Path(args.nonce_root); nonce_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Durable O_EXCL admission happens only after all non-contact checks pass.
        create(nonce_root / (manifest["nonce"] + ".json"), canonical({"nonce": manifest["nonce"], "manifest_sha256": approved}))
        run_root = Path(manifest["prepared_root"])
        trace = control / "trace"; trace.mkdir(mode=0o700)
        if PACKAGE in INDEXED_PACKAGES:
            build_index(run_root, control)
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
                "OPENAI_API_KEY": "proxy-owned-credential", "QWEN_SKILL_ROOT": str(run_root / skill_dir()),
                "NO_PROXY": "127.0.0.1,localhost",
                "SHERLOCK_STOP_HOOK_WRAPPER": str(STOP_HOOK_WRAPPER),
                "SHERLOCK_STOP_HOOK_LOG": str(trace / "stop-hook.jsonl"),
                "SHERLOCK_STOP_HOOK_TIMEOUT_S": str(STOP_HOOK_TIMEOUT_S),
                "SHERLOCK_STOP_HOOK_DETAIL_DIR": str(trace / "stopcheck-detail"),
                "SHERLOCK_INDEX_ROOT": str(run_root / "index")}
        fullenv = {k:v for k,v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "TZ")}
        fullenv.update(qenv); fullenv["PYTHONDONTWRITEBYTECODE"] = "1"
        err = open(control / "qwen-stderr.log", "ab")
        quota_watch = QuotaWatch(trace)
        prompt = (run_root / "prompt.txt").read_text(encoding="utf-8")
        output_path = None
        try:
            # v52 ends a session at every stage boundary and asks the operator for
            # `/clear` + `/sherlock ПРОДОЛЖИ ...`. A fresh Qwen process is the clean
            # context; the harness types the exact continuation line from handoff.txt.
            for session in range(1, MAX_STAGE_SESSIONS + 1):
                output_path = control / ("qwen-output.json" if session == 1 else "qwen-output-s%d.json" % session)
                started = time.time()
                with open(output_path, "wb") as output:
                    qwen = subprocess.Popen(qwen_cmd + ["--auth-type", "openai", "--model", MODEL,
                                              "--max-session-turns", "-1", "--max-tool-calls", "-1", "--openai-logging",
                                              "--openai-logging-dir", str(trace / "openai-logs"), "--output-format", "json",
                                              prompt], cwd=run_root,
                                              stdin=subprocess.DEVNULL, stdout=output, stderr=err, env=fullenv, start_new_session=True)
                    if session == 1:
                        create(control / "launch-receipt.json", canonical({"started_at": now(), "qwen_pid":qwen.pid, "proxy_pid":proxy.pid, "manifest_sha256":approved}))
                    while qwen.poll() is None:
                        if proxy.poll() is not None:
                            qwen.terminate(); raise Refusal("proxy exited during Qwen run")
                        try: validate_manifest(control, manifest)
                        except Exception:
                            qwen.terminate(); raise
                        observe_trace(trace, strict_buffer_enabled=True)
                        quota = quota_watch.poll()
                        if quota is not None:
                            qwen.terminate(); raise quota
                        loop = stop_hook_loop(trace / "stop-hook.jsonl", since=run_started)
                        if loop is not None:
                            qwen.terminate()
                            raise TerminalFailure("stop_hook_loop", "%d identical Stop-hook blocks" % loop["count"], loop)
                        if time.time() - run_started > max_wall:
                            qwen.terminate()
                            raise TerminalFailure("wall_clock_exceeded", "run exceeded %d s" % max_wall,
                                                  {"max_wall_seconds": max_wall})
                        time.sleep(0.5)
                qwen_rc = qwen.returncode
                denied = permission_denials(output_path, session, trace)
                nxt = continuation_prompt(run_root / "work", trace / "stop-hook.jsonl", started) if qwen_rc == 0 else None
                with open(control / "sessions.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"session": session, "started_at": started, "finished_at": time.time(),
                                         "qwen_exit": qwen_rc, "output": output_path.name,
                                         "prompt_sha256": digest(prompt.encode("utf-8")),
                                         "handoff_continuation": nxt, "permission_denied": denied}, ensure_ascii=False, sort_keys=True) + "\n")
                if proxy.poll() is not None: raise Refusal("proxy exited before run completion")
                if qwen_rc != 0:
                    terminal(control, "qwen_failed", qwen_exit=qwen_rc, sessions=session,
                             manifest_sha256=approved, nonce=manifest["nonce"])
                    return qwen_rc
                validate_qwen_output(output_path)
                if nxt is None:
                    break
                prompt = nxt
            else:
                raise TerminalFailure("stage_limit", "still handing off after %d sessions" % MAX_STAGE_SESSIONS)
            validate_manifest(control, manifest)
            observe_trace(trace, strict_buffer_enabled=True)
            validate_terminal_ledger(trace, strict_buffer_enabled=True)
            validate_finalizer_receipt(run_root / "work", control)
        finally: err.close()
        terminal(control, "completed", qwen_exit=qwen_rc,
                 manifest_sha256=approved, nonce=manifest["nonce"])
        return qwen_rc
    except TerminalFailure as exc:
        terminal(control, exc.status, error="%s: %s" % (type(exc).__name__, exc),
                 qwen_exit=qwen_rc, diagnostics=exc.details or None)
        print("launcher refusal: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:
        terminal(control, "precontact_refused" if proxy is None else "launcher_failed",
                 error="%s: %s" % (type(exc).__name__, exc))
        print("launcher refusal: %s" % exc, file=sys.stderr)
        return 2
    except BaseException as exc:
        terminal(control, "interrupted", error="%s: %s" % (type(exc).__name__, exc))
        raise
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
        # run.done is written only after owned children are stopped.
        ensure_terminal(control, "launcher_exited_without_terminal")

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
    a.add_argument("--model", default="gpt-5.5", choices=sorted(ALLOWED_MODELS)); a.add_argument("--package", default="v52", choices=PACKAGES); a.add_argument("--upstream-base", required=True); a.add_argument("--authorization", required=True); a.set_defaults(fn=prepare)
    a = sub.add_parser("run")
    a.add_argument("--control-root", required=True); a.add_argument("--approval", required=True); a.add_argument("--nonce-root", required=True)
    a.add_argument("--qwen", required=True); a.add_argument("--proxy", required=True); a.add_argument("--listen-port", type=int, default=18795); a.add_argument("--max-wall-seconds", type=int, default=MAX_WALL_SECONDS); a.set_defaults(fn=run)
    args = p.parse_args()
    try: result = args.fn(args)
    except Refusal as exc: print("launcher refusal: %s" % exc, file=sys.stderr); result = 2
    sys.exit(result or 0)
if __name__ == "__main__": main()
