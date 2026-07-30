#!/usr/bin/env python3
"""measure.py — deterministic verdicts from a captured run. No LLM, no network.

Answers the question the judge cannot: when a defect was missed, was the evidence
never opened (a COVERAGE failure — fix the tools or the file-selection guidance),
or was it read and not connected (a REASONING failure — fix synthesis)?

Three-valued on purpose. A shell read (`sed -n`, `grep`) often cannot be resolved to
a line range; reporting that as "not reached" would manufacture coverage failures
that never happened. Unresolvable reads of a proof file are `unknown`.

Field names follow the OTel GenAI attribute vocabulary where they correspond, so we
do not invent private terminology. We deliberately do NOT depend on the OTel SDK
(pip dependency; AGENTS.md R1 is stdlib-only).
"""
import json
import os
import re

READ_TOOLS = {"read_file", "read_many_files", "glob", "search_file_content",
              "grep_search", "list_directory"}
SHELL_TOOLS = {"run_shell_command", "shell", "bash"}

_RESULT_RANGE = re.compile(r"Read lines (\d+)-(\d+) of \d+ from (\S+)")
_PATHISH = re.compile(r"(/[^\s'\"]+|[\w./-]+\.(?:log|json|txt|plog|gz|out))")


def _blocks(rec, kind):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


def read_events(stream_path):
    """Every tool call that could have put log bytes in front of the model."""
    events = []
    pending = None
    with open(stream_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue

            for b in _blocks(rec, "tool_use"):
                name = b.get("name") or ""
                inp = b.get("input") or {}
                ev = {"tool": name, "file": None, "line_start": None,
                      "line_end": None, "range_known": False,
                      "raw": json.dumps(inp, ensure_ascii=False)[:400]}
                if name in READ_TOOLS:
                    ev["file"] = inp.get("file_path") or inp.get("path") or inp.get("absolute_path")
                    off, lim = inp.get("offset"), inp.get("limit")
                    if isinstance(off, int) and isinstance(lim, int) and lim > 0:
                        ev["line_start"] = off + 1
                        ev["line_end"] = off + lim
                        ev["range_known"] = True
                elif name in SHELL_TOOLS:
                    cmd = inp.get("command") or ""
                    ev["raw"] = cmd[:400]
                    m = _PATHISH.search(cmd)
                    if m:
                        ev["file"] = m.group(1)
                if ev["file"] or ev["tool"]:
                    events.append(ev)
                    pending = ev

            for b in _blocks(rec, "tool_result"):
                content = b.get("content")
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                m = _RESULT_RANGE.search(text or "")
                if m and pending is not None:
                    # The result is authoritative: it reports what was ACTUALLY read.
                    pending["line_start"] = int(m.group(1))
                    pending["line_end"] = int(m.group(2))
                    pending["range_known"] = True
                    pending["file"] = m.group(3)
    return events


def _tail(path):
    """Compare on the trailing path: an absolute tool path like /c/apps/api.log
    carries a synthetic single-segment mount root ("/c") that the corpus-relative
    proof path (e.g. "apps/api.log") never has, so drop just that one segment.
    Relative paths (already corpus-relative) pass through unchanged."""
    p = (path or "").replace("\\", "/")
    if p.startswith("/"):
        p = "/".join(p.split("/")[2:])
    return p


def _same_file(event_path, proof_rel):
    e, p = _tail(event_path), _tail(proof_rel)
    return e.endswith(p) or p.endswith(os.path.basename(e)) and os.path.basename(e) == os.path.basename(p)


def proof_reach(events, proof_locations):
    reached, not_reached, unknown = [], [], []
    for pr in proof_locations:
        state = "not_reached"
        for ev in events:
            if not ev.get("file") or not _same_file(ev["file"], pr["file"]):
                continue
            if not ev["range_known"]:
                state = "unknown"
                continue
            if ev["line_start"] <= pr["line_end"] and ev["line_end"] >= pr["line_start"]:
                state = "reached"
                break
        {"reached": reached, "not_reached": not_reached, "unknown": unknown}[state].append(
            "%s:%d-%d" % (pr["file"], pr["line_start"], pr["line_end"]))

    files_opened = sorted({_tail(e["file"]) for e in events if e.get("file")})
    files_with_proofs = sorted({os.path.basename(p["file"]) for p in proof_locations})
    verdict = "reached" if reached else ("unknown" if unknown else "not_reached")
    return {"reached": reached, "not_reached": not_reached, "unknown": unknown,
            "files_opened": files_opened, "files_with_proofs": files_with_proofs,
            "verdict": verdict}
