#!/usr/bin/env python3
"""Qwen PreToolUse admission gate for durable Sherlock stage handoffs."""
import json
import os
import re
import sys
import time


def _sibling(name, _here=os.path.dirname(os.path.abspath(__file__))):
    """v55: load a sibling tool BY PATH under a path+identity-unique module name.

    Bare `import ccindex` resolves through sys.path and the process-wide
    sys.modules cache, so a process that has touched another package copy (the
    hook's stopcheck vs the expected-root finalize, a trusted tree vs the model's
    tree, or a test that loaded a since-deleted package) silently binds this
    package's gate to a DIFFERENT package's checker. Never use sys.path here.
    """
    import hashlib as _hl
    import importlib.util as _iu
    path = os.path.join(_here, name + ".py")
    st = os.stat(path)
    ident = "%s|%d|%d|%d" % (path, st.st_ino, st.st_size, st.st_mtime_ns)
    key = "sherlock_sib_%s_%s" % (_hl.sha256(ident.encode()).hexdigest()[:16], name)
    mod = sys.modules.get(key)
    if mod is None:
        spec = _iu.spec_from_file_location(key, path)
        mod = _iu.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return mod


stopcheck = _sibling("stopcheck")
MAX_REASON = 220


def output(allowed, reason=None):
    specific = {"hookEventName": "PreToolUse"}
    result = {"continue": bool(allowed), "hookSpecificOutput": specific}
    if not allowed:
        reason = (reason or "Sherlock stage boundary cannot be verified.")[:MAX_REASON]
        specific.update(permissionDecision="deny", permissionDecisionReason=reason)
        result["stopReason"] = reason
    return result


INDEX_DIRNAME = "index"
INDEX_ROOT_ENV = "SHERLOCK_INDEX_ROOT"
WRITE_TOOLS = ("write_file", "replace", "edit", "WriteFile", "Edit")
PATH_KEYS = ("file_path", "path", "absolute_path")
SHELL_WRITE_RE = re.compile(r"(?:^|[\s;&|(])(?:rm|mv|cp|chmod|chown|touch|tee|truncate|ln|dd|"
                            r"install|rsync|shred|unlink|mkdir|rmdir)\b|>|\bsed\s+-i")
INDEX_DENY = "Sherlock data index is read-only: only tools/buildindex.py writes it."


def index_dirs(workspace):
    out = [os.path.realpath(os.path.join(workspace, INDEX_DIRNAME))]
    env = os.environ.get(INDEX_ROOT_ENV)
    if env:
        out.append(os.path.realpath(env))
    return out


def _inside_any(path, dirs):
    real = os.path.realpath(path)
    return any(real == d or real.startswith(d + os.sep) for d in dirs)


def index_write_denied(event, workspace):
    """v53 item 1: the model may not write the load-time data index."""
    tool = event.get("tool_name") or ""
    inp = event.get("tool_input") or {}
    if not isinstance(inp, dict):
        return None
    dirs = index_dirs(workspace)
    if tool in WRITE_TOOLS:
        for key in PATH_KEYS:
            value = inp.get(key)
            if isinstance(value, str) and value and _inside_any(
                    os.path.join(workspace, value), dirs):
                return INDEX_DENY
        return None
    if tool == "run_shell_command":
        cmd = inp.get("command")
        if isinstance(cmd, str) and ("verdict-hmac" in cmd or "SHERLOCK_VERDICT_KEY" in cmd):
            return INDEX_DENY
        if not isinstance(cmd, str) or "buildindex.py" in cmd:
            return None
        names = [re.escape(d) for d in dirs] + [r"(?:\./)?" + INDEX_DIRNAME]
        ref = re.compile(r"(?:^|[\s'\"=:])(?:%s)(?:/|\s|$|['\"])" % "|".join(names))
        if ref.search(cmd) and SHELL_WRITE_RE.search(cmd):
            return INDEX_DENY
    return None


def evaluate(event, workspace, deadline):
    if event.get("hook_event_name") != "PreToolUse":
        raise stopcheck.ActiveStateError("Sherlock: boundary hook received a non-PreToolUse event.")
    denied = index_write_denied(event, workspace)
    if denied:
        return output(False, denied)
    marker, _marker_path, _why = stopcheck.load_marker(workspace, deadline)
    if marker is None:
        return output(True)
    out_dir = stopcheck.safe_dir(marker.get("out") or "", workspace)
    if not out_dir:
        raise stopcheck.ActiveStateError("Sherlock: active marker points outside the workspace.")
    lists, authority = stopcheck.authoritative_worklists(out_dir, workspace, deadline)
    receipt = stopcheck._stage_pause_decision(event, out_dir, lists, authority,
                                               deadline, consume=False)
    if receipt is None:
        return output(True)
    session = event.get("session_id")
    if receipt["state"] == "pending":
        return output(False, "Sherlock stage handoff pending: end this turn; wait for Stop and a fresh /clear session before tools.")
    if receipt.get("stop_session_id") == session:
        return output(False, "Sherlock stage handoff was consumed in this session: wait for a fresh /clear session before tools.")
    return output(True)


def main():
    deadline = time.monotonic() + stopcheck.TOTAL_TIMEOUT
    watchdog = stopcheck.arm_watchdog(deadline - time.monotonic())
    try:
        event = stopcheck.read_hook_input()
        workspace = stopcheck.resolve_workspace(event)
        if workspace is None:
            result = output(False, "Sherlock: cannot determine workspace for stage boundary.")
        else:
            try:
                result = evaluate(event, workspace, deadline)
            except (stopcheck.ActiveStateError, stopcheck.DeadlineExceeded) as exc:
                result = output(False, str(exc))
            except Exception as exc:  # fail closed: active receipt must not be bypassed
                result = output(False, "Sherlock: stage boundary verification failed (%s)." % type(exc).__name__)
    finally:
        stopcheck.disarm_watchdog(watchdog)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
