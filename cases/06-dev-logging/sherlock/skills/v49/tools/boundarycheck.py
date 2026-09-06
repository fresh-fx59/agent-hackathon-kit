#!/usr/bin/env python3
"""Qwen PreToolUse admission gate for durable Sherlock stage handoffs."""
import json
import os
import sys
import time

import stopcheck


MAX_REASON = 220


def output(allowed, reason=None):
    specific = {"hookEventName": "PreToolUse"}
    result = {"continue": bool(allowed), "hookSpecificOutput": specific}
    if not allowed:
        reason = (reason or "Sherlock stage boundary cannot be verified.")[:MAX_REASON]
        specific.update(permissionDecision="deny", permissionDecisionReason=reason)
        result["stopReason"] = reason
    return result


def evaluate(event, workspace, deadline):
    if event.get("hook_event_name") != "PreToolUse":
        raise stopcheck.ActiveStateError("Sherlock: boundary hook received a non-PreToolUse event.")
    marker, _marker_path, _why = stopcheck.load_marker(workspace, deadline)
    if marker is None:
        return output(True)
    out_dir = stopcheck.safe_dir(marker.get("out") or "", workspace)
    if not out_dir:
        raise stopcheck.ActiveStateError("Sherlock: active marker points outside the workspace.")
    lists = stopcheck.manifest_worklists(marker, out_dir, deadline)
    receipt = stopcheck._stage_pause_decision(event, out_dir, lists, deadline, consume=False)
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
