#!/usr/bin/env python3
"""Trusted v65 gate for the investigate-to-draft boundary.

The investigate phase is successful only when the durable triage ledger passes,
the checkpoint records the triage handoff, and the captured stream shows the
specialist agent was actually invoked.  Prompt text alone is not evidence.
"""
import argparse
import json
import os
import subprocess
import sys


def _worklists(work):
    return [os.path.join(work, name) for name in ("worklist.tsv",)]


def _agent_seen(run_dir):
    root = os.path.join(run_dir, "out", "investigate")
    requested = set()
    successful = set()
    for base, _dirs, files in os.walk(root):
        for name in files:
            if not (name.startswith("stream") and name.endswith(".jsonl")):
                continue
            path = os.path.join(base, name)
            try:
                for line in open(path, encoding="utf-8"):
                    obj = json.loads(line)
                    def visit(value):
                        if isinstance(value, dict):
                            if (obj.get("type") == "assistant"
                                    and value.get("type") == "tool_use"
                                    and value.get("name") == "agent"
                                    and isinstance(value.get("input"), dict)
                                    and value["input"].get("subagent_type") == "sherlock-triage"
                                    and value["input"].get("run_in_background") is False):
                                if value.get("id"):
                                    requested.add(value["id"])
                            return any(visit(v) for v in value.values())
                        if isinstance(value, list):
                            return any(visit(v) for v in value)
                        return False
                    visit(obj)
                    if obj.get("type") == "user":
                        for item in obj.get("message", {}).get("content", []):
                            if (isinstance(item, dict)
                                    and item.get("type") == "tool_result"
                                    and item.get("is_error") is False
                                    and item.get("tool_use_id")):
                                successful.add(item["tool_use_id"])
            except (OSError, ValueError):
                continue
    return bool(requested & successful)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args(argv)
    result = {"schema": 1, "checks": {}, "pass": False}
    worklist = _worklists(a.work)[0]
    rules = os.path.join(a.work, "rules.tsv")
    triage = os.path.join(a.package, "tools", "triagecheck.py")
    cmd = [sys.executable, triage, "--worklist", worklist, "--rules", rules,
           "--corpus", a.corpus, "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    result["checks"]["triagecheck"] = {"exit": proc.returncode}
    verify = os.path.join(a.package, "tools", "checkpoint.py")
    if os.path.isfile(verify):
        vr = subprocess.run([sys.executable, verify, "verify", "--work", a.work],
                            capture_output=True, text=True, check=False)
        result["checks"]["checkpoint"] = {"exit": vr.returncode}
    else:
        result["checks"]["checkpoint"] = {"exit": 1, "error": "checkpoint tool missing"}
    try:
        result["checks"]["triagecheck"]["result"] = json.loads(proc.stdout)
    except ValueError:
        result["checks"]["triagecheck"]["stdout"] = proc.stdout[-500:]
    checkpoint = os.path.join(a.work, "checkpoint.json")
    try:
        row = json.load(open(checkpoint, encoding="utf-8"))
        receipt = row.get("pending_handoff") or {}
        consumed = (receipt.get("state") == "consumed"
                    and isinstance(receipt.get("stop_event_sha256"), str)
                    and len(receipt["stop_event_sha256"]) == 64
                    and bool(receipt.get("stop_session_id")))
        ok = (row.get("stage") == "draft"
              and receipt.get("from_stage") == "triage"
              and receipt.get("to_stage") == "draft"
              and (receipt.get("state") == "pending" or consumed))
        result["checks"]["handoff"] = {"pass": ok, "stage": row.get("stage"),
                                         "from_stage": receipt.get("from_stage"),
                                         "to_stage": receipt.get("to_stage"),
                                         "state": receipt.get("state")}
    except (OSError, ValueError):
        result["checks"]["handoff"] = {"pass": False, "error": "checkpoint missing"}
        ok = False
    seen = _agent_seen(a.run_dir)
    result["checks"]["agent"] = {"pass": seen}
    result["pass"] = (proc.returncode == 0
                       and result["checks"]["checkpoint"]["exit"] == 0
                       and ok and seen)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
