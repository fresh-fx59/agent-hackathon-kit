#!/usr/bin/env python3
"""reconcile.py — does the ledger say what the trajectory actually did?

Every number in `results.jsonl` is a SUMMARY. This tool re-derives the same facts
from the raw `stream.jsonl` of each run and prints the two side by side, so a claim
can be checked against the thing it claims about instead of being trusted.

It exists because narration drifted from ground truth repeatedly on 2026-07-31/08-01:

- «10 of 41 tool calls errored» was read as breakage. It was the citecheck gate
  exiting non-zero to say "not finished" — i.e. the mechanism WORKING. A raw error
  count cannot tell those apart; this tool separates them.
- `diagnosis=collapse` was read as "found nothing". D04 had in fact completed the
  investigation — 36 citecheck runs to zero errors, 38 verified citations — and
  failed only to paste it into the final message. Opposite fix, same label.
- `tool_calls` in the ledger and the real `tool_use` count are computed by different
  code paths and had never been compared.
- An arm can score `ok` having never run its own headline tool, which is how four
  metered cells were once spent measuring a feature that never executed.

Usage:
    python3 reconcile.py                 # every row in results.jsonl
    python3 reconcile.py --arm v11       # one arm
    python3 reconcile.py --json          # machine-readable
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# A run is only evidence of the ARM if the arm's own machinery ran. Keyed by the
# tool the arm ships as its mandatory first step.
ARM_MAP_TOOL = {"v8": "logstat.py", "v9": "logstat.py", "v10": "logstat.py",
                "v11": "logmap.py"}


def _final_and_calls(stream_path):
    """Re-derive from the raw stream: the result record, and every tool_use."""
    final, calls, errors = None, [], []
    skill_seen = False
    report_writes = []
    with open(stream_path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if rec.get("type") == "result":
                final = rec
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            for part in msg.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    inp = part.get("input") or {}
                    cmd = str(inp.get("command") or "")
                    calls.append({"name": part.get("name"), "cmd": cmd})
                    # "Was the skill loaded?" must be answered by the LOAD ITSELF —
                    # a `skill` tool_use naming it. Searching the trajectory text for
                    # "sherlock" or "log-rca" looks equivalent and is not: the corpus
                    # path contains ".../sherlock/measure/cases/...", so that substring
                    # is present in EVERY run and the check silently always passes.
                    # It did, and it produced a confidently wrong "the skill WAS
                    # loaded" on a run where it never was — which then argued against
                    # the correct fix.
                    if part.get("name") == "skill":
                        skill_seen = True
                    tgt = str(inp.get("file_path") or inp.get("path") or "")
                    if "report.md" in tgt:
                        body = inp.get("content") or inp.get("new_string") or ""
                        if body:
                            report_writes.append(len(body))
                elif part.get("type") == "tool_result" and part.get("is_error"):
                    errors.append(str(part.get("content") or "")[:4000])
    return final, calls, errors, skill_seen, report_writes


def audit(run_dir, row):
    """Compare one ledger row against its own trajectory."""
    stream = os.path.join(run_dir, "stream.jsonl")
    # meta.json says HOW the arm was delivered. Absent (older rows) => tool-only.
    meta = {}
    _mp = os.path.join(run_dir, "meta.json")
    if os.path.exists(_mp):
        try:
            meta = json.load(open(_mp, encoding="utf-8")) or {}
        except ValueError:
            meta = {}
    if not os.path.exists(stream):
        return {"run_dir": run_dir, "error": "no stream.jsonl"}

    final, calls, errors, skill_seen, report_writes = _final_and_calls(stream)
    arm = row.get("arm") or ""
    map_tool = ARM_MAP_TOOL.get(arm, "")

    def used(needle):
        return sum(1 for c in calls if needle in (c["cmd"] or ""))

    # The distinction the raw error count destroys: citecheck exiting non-zero is
    # the stopping condition REFUSING, which is the tool doing its job. Anything
    # else is a genuine failure.
    gate_errors = sum(1 for e in errors if "citecheck" in e)
    real_errors = len(errors) - gate_errors

    usage = (final or {}).get("usage") or {}
    derived = {
        "skill_loaded": bool(skill_seen or meta.get("skill_delivery") == "injected"),
        "skill_delivery": meta.get("skill_delivery") or "tool-only",
        "map_tool_ran": used(map_tool) if map_tool else None,
        "citecheck_ran": used("citecheck"),
        "logjoin_ran": used("logjoin"),
        "subagent_spawned": sum(1 for c in calls if c["name"] == "agent"),
        "tool_calls": len(calls),
        "errored_results": len(errors),
        "gate_refusals": gate_errors,
        "real_errors": real_errors,
        "final_msg_chars": len((final or {}).get("result") or ""),
        "turns": (final or {}).get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "biggest_report_write": max(report_writes) if report_writes else 0,
    }

    claimed = {
        "diagnosis": row.get("diagnosis"),
        "judge_found": row.get("judge_found"),
        "report_chars": row.get("report_chars"),
        "tool_calls": row.get("tool_calls"),
        "turns": row.get("turns"),
        "input_tokens": row.get("input_tokens"),
    }

    flags = []
    # A pass that did not use the arm's own mechanism is not evidence for the arm.
    if map_tool and derived["map_tool_ran"] == 0:
        flags.append("MAP-TOOL-NEVER-RAN")
    # "injected" means the runner put the arm's text in the prompt, so the arm
    # WAS in context even though no `skill` tool_use exists to point at.
    if not skill_seen and meta.get("skill_delivery") != "injected":
        flags.append("SKILL-NEVER-LOADED")
    if derived["subagent_spawned"]:
        flags.append("SUBAGENT-SPAWNED")
    # The D04 shape: real work done, nothing delivered. `collapse` alone reads as
    # "found nothing", which points at the wrong fix entirely.
    if row.get("diagnosis") == "collapse" and derived["biggest_report_write"] > 2000:
        flags.append("DELIVERY-FAILURE-NOT-DETECTION")
    if row.get("diagnosis") == "ok" and map_tool and derived["map_tool_ran"] == 0:
        flags.append("GREEN-WITHOUT-THE-MECHANISM")
    for key in ("tool_calls", "turns", "input_tokens"):
        c, d = claimed.get(key), derived.get(key)
        if c is not None and d is not None and c != d:
            flags.append("MISMATCH:%s ledger=%s trajectory=%s" % (key, c, d))
    if claimed["report_chars"] is not None and \
            claimed["report_chars"] != derived["final_msg_chars"]:
        flags.append("MISMATCH:report_chars ledger=%s final_msg=%s"
                     % (claimed["report_chars"], derived["final_msg_chars"]))

    return {"case_id": row.get("case_id"), "arm": arm, "run_dir": run_dir,
            "claimed": claimed, "derived": derived, "flags": flags}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(HERE, "results.jsonl"))
    ap.add_argument("--arm", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.results):
        print("no ledger at %s" % a.results, file=sys.stderr)
        return 2

    out = []
    for line in open(a.results, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if a.arm and row.get("arm") != a.arm:
            continue
        rd = row.get("run_dir") or ""
        if not os.path.isdir(rd):
            out.append({"case_id": row.get("case_id"), "arm": row.get("arm"),
                        "flags": ["RUN-DIR-GONE"], "run_dir": rd})
            continue
        out.append(audit(rd, row))

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("%-5s %-5s %-11s %-6s %-5s %-5s %-6s %-7s %-6s  %s"
          % ("case", "arm", "diagnosis", "map", "cite", "sub", "calls",
             "gate/err", "final", "flags"))
    print("-" * 116)
    bad = 0
    for r in out:
        if r.get("error") or "RUN-DIR-GONE" in (r.get("flags") or []):
            print("%-5s %-5s  %s" % (r.get("case_id"), r.get("arm"),
                                     r.get("error") or "run dir gone"))
            continue
        d, c = r["derived"], r["claimed"]
        if r["flags"]:
            bad += 1
        print("%-5s %-5s %-11s %-6s %-5s %-5s %-6s %-7s %-6s  %s"
              % (r["case_id"], r["arm"], c["diagnosis"], d["map_tool_ran"],
                 d["citecheck_ran"], d["subagent_spawned"], d["tool_calls"],
                 "%d/%d" % (d["gate_refusals"], d["real_errors"]),
                 d["final_msg_chars"], "; ".join(r["flags"]) or "-"))
    print("-" * 116)
    print("%d rows, %d carrying at least one flag" % (len(out), bad))
    print("map = arm's mandatory first tool · cite = citecheck invocations · "
          "sub = subagents spawned")
    print("gate/err = citecheck REFUSALS (the mechanism working) / genuine errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
