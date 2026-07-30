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

# Tool taxonomy. Split by what the tool can actually put in front of the model:
# a FILE read delivers bytes of one named file; a DIRECTORY-scoped tool searches a
# subtree and we can never resolve which bytes of which file came back.
FILE_READ_TOOLS = {"read_file", "read_many_files"}
DIR_SCAN_TOOLS = {"glob", "search_file_content", "grep_search", "list_directory"}
SHELL_TOOLS = {"run_shell_command", "shell", "bash"}
READ_TOOLS = FILE_READ_TOOLS | DIR_SCAN_TOOLS

# case.json is the ANSWER (title, root_cause, proof_locations), not corpus. Reading it
# is not evidence coverage — and under the pre-2026-07-30 layout it was the first
# thing the model read. It must never inflate files_opened.
NON_CORPUS_BASENAMES = {"case.json"}

# An open upper bound: "this read went to the end of a file whose length we could not
# resolve". Reading a whole file reads every line in it, so any proof inside it was
# definitively put in front of the model.
OPEN_END = float("inf")

_PATHISH = re.compile(r"(/[^\s'\"]+|[\w./-]+\.(?:log|json|txt|plog|gz|out))")


def _blocks(rec, kind):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


def _result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def read_events(stream_path):
    """Every tool call that could have put log bytes in front of the model.

    Written against the OBSERVED stream (tests/fixtures/real-stream-excerpt.jsonl,
    captured verbatim from run 20260730T195412Z), not an assumed one:

    * a `tool_use` block carries its correlation id in `id`; the matching
      `tool_result` block carries it in `tool_use_id`, plus `is_error` and a
      `content` string. Pairing is BY ID — one assistant message routinely emits two
      `tool_use` blocks at once (record 11: read_file + run_shell_command), and the
      old "last pending" slot attributed both results to the second call.
    * the CLI never emits "Read lines X-Y of Z from <path>". `grep -c "Read lines"`
      over the captured stream returns 0. That string was invented, so the branch
      that parsed it was dead and the range it was supposed to supply never arrived.
      What a read actually returns is the file's CONTENT, so the range is derived
      from the result body instead.
    """
    events = []
    by_id = {}
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
                ev = {"tool": name, "kind": "other", "file": None, "dir": None,
                      "line_start": None, "line_end": None, "range_known": False,
                      "tool_use_id": b.get("id"),
                      "raw": json.dumps(inp, ensure_ascii=False)[:400]}
                if name in DIR_SCAN_TOOLS:
                    # The target is a DIRECTORY. Resolving it as a file (the old code
                    # did) means _same_file never matches and every proof under it is
                    # reported not_reached — manufacturing exactly the coverage
                    # failures the three-valued verdict exists to prevent.
                    ev["kind"] = "dir"
                    ev["dir"] = (inp.get("path") or inp.get("directory")
                                 or inp.get("dir") or inp.get("absolute_path"))
                elif name in FILE_READ_TOOLS:
                    ev["kind"] = "file"
                    ev["file"] = inp.get("file_path") or inp.get("path") or inp.get("absolute_path")
                    off, lim = inp.get("offset"), inp.get("limit")
                    if isinstance(off, int) and isinstance(lim, int) and lim > 0:
                        ev["line_start"] = off + 1
                        ev["line_end"] = off + lim
                        ev["range_known"] = True
                    elif ev["file"]:
                        # NO offset and NO limit is the natural call on a small file,
                        # and it reads the WHOLE file — every line in it. Treating
                        # that as "range unknown" (the old behaviour) downgraded the
                        # commonest successful read to `unknown`. The upper bound
                        # stays open until a result body pins the real line count.
                        ev["line_start"] = 1
                        ev["line_end"] = OPEN_END
                        ev["range_known"] = True
                        ev["whole_file"] = True
                elif name in SHELL_TOOLS:
                    ev["kind"] = "file"
                    cmd = inp.get("command") or ""
                    ev["raw"] = cmd[:400]
                    m = _PATHISH.search(cmd)
                    if m:
                        ev["file"] = m.group(1)
                events.append(ev)
                if ev["tool_use_id"]:
                    by_id[ev["tool_use_id"]] = ev

            for b in _blocks(rec, "tool_result"):
                ev = by_id.get(b.get("tool_use_id"))
                if ev is None:
                    continue
                text = _result_text(b)
                ev["result_chars"] = len(text or "")
                if b.get("is_error"):
                    # A failed read put NO bytes in front of the model. Do not let it
                    # count as reached; do not call it not_reached either — the file
                    # was aimed at, so `unknown` is the honest state.
                    ev["range_known"] = False
                    ev["line_start"] = ev["line_end"] = None
                    continue
                if ev.get("whole_file") and text is not None:
                    # The result body IS what was read. Its line count is the real
                    # upper bound, so a CLI-truncated read cannot claim the tail.
                    ev["line_end"] = max(1, len(text.splitlines()))
    return events


def _norm(path):
    return (path or "").replace("\\", "/")


def _same_file(event_path, proof_rel):
    """Directory-qualified match: the event path must equal the proof-relative
    path, or end with it at a path-separator boundary. A shared basename alone
    (e.g. syslog/node-a/syslog vs syslog/node-b/syslog — two different hosts'
    logs, one of them a red-herring proof) must NEVER count as the same file;
    that would manufacture exactly the coverage error this module exists to
    prevent."""
    e, p = _norm(event_path), _norm(proof_rel)
    if not e or not p:
        return False
    return e == p or e.endswith("/" + p)


def _relative_name(file_path, proof_locations):
    """Resolve an event's file to the exact corpus-relative string of whichever
    proof location it matches, so files_opened and files_with_proofs are
    directly comparable regardless of how deep the absolute sandbox path runs
    (a real run nests proofs many directories under a case root, not just the
    fixture's single-segment "/c/" mount). Files that match no proof location
    are diagnostic-only and fall back to their bare basename."""
    fp = _norm(file_path)
    for pr in proof_locations:
        pf = _norm(pr["file"])
        if fp == pf or fp.endswith("/" + pf):
            return pf
    return os.path.basename(fp)


def _dir_scan_covers(dir_path, proof_rel):
    """Can a directory-scoped search have surfaced this proof?

    `search_file_content` / `glob` / `list_directory` are how an agent normally finds
    a log line, and they report a SUBTREE, never which bytes of which file came back.
    We know the absolute directory that was scanned; the proof location is
    corpus-RELATIVE. There is no sound way to prove a corpus-relative path is *not*
    under an absolute sandbox directory, so the only honest answer is "cannot
    exclude" — which the three-valued design spells `unknown`, never `not_reached`.

    This can only ever soften not_reached to unknown. It can never produce `reached`:
    a directory scan is not evidence that the proof lines were displayed."""
    return bool(_norm(dir_path)) and bool(_norm(proof_rel))


def proof_reach(events, proof_locations):
    reached, not_reached, unknown = [], [], []
    dir_events = [e for e in events if e.get("kind") == "dir" and e.get("dir")]
    for pr in proof_locations:
        state = "not_reached"
        for ev in events:
            if ev.get("kind") != "file" or not ev.get("file"):
                continue
            if not _same_file(ev["file"], pr["file"]):
                continue
            if not ev["range_known"]:
                state = "unknown"
                continue
            if ev["line_start"] <= pr["line_end"] and ev["line_end"] >= pr["line_start"]:
                state = "reached"
                break
        if state == "not_reached" and any(_dir_scan_covers(e["dir"], pr["file"])
                                          for e in dir_events):
            state = "unknown"
        {"reached": reached, "not_reached": not_reached, "unknown": unknown}[state].append(
            "%s:%d-%d" % (pr["file"], pr["line_start"], pr["line_end"]))

    # files_opened counts CORPUS FILES ONLY (Important-8). A directory is not a file
    # (list_directory used to land here), and case.json is the answer key, not
    # evidence — a live row read 3 for a case whose corpus held one log file.
    files_opened = sorted({
        name for name in (_relative_name(e["file"], proof_locations)
                          for e in events if e.get("kind") == "file" and e.get("file"))
        if os.path.basename(name) not in NON_CORPUS_BASENAMES})
    files_with_proofs = sorted({_norm(p["file"]) for p in proof_locations})
    verdict = "reached" if reached else ("unknown" if unknown else "not_reached")
    return {"reached": reached, "not_reached": not_reached, "unknown": unknown,
            "files_opened": files_opened, "files_with_proofs": files_with_proofs,
            "verdict": verdict}


# --------------------------------------------------------------- report layer
SECTIONS = ["Что произошло", "Корневая причина", "Цепочка причин", "Улики",
            "Немедленные действия", "Исправление в коде", "Чего я не знаю", "ЗНАНИЯ"]

# SKILL.md forbids these outright: the user sees ONLY the final message, so a
# reference to an earlier one means no report was delivered at all.
BANNED = ["отчёт выше", "как я уже показал", "результаты приведены ранее",
          "см. предыдущее сообщение", "отчёт уже готов выше"]

# Measured basis: observed collapsed runs land at 106 and 157 chars (both were
# literally "the report is above"); observed genuine reports on the full corpus land
# at 12,872, 12,940, and 13,355 chars. Three orders of magnitude apart — 2000 sits
# safely in the gap, nowhere near either cluster.
MIN_REPORT_CHARS = 2000

# ...but that gap was measured on FULL-CORPUS reports, and applying it to a 4-line
# micro-corpus labels a short CORRECT report `collapse` (Important-7). There is
# simply less to write about nine hand-written lines. Measured basis for the micro
# floor: the one captured micro run wrote 6,771 chars about a 9-line corpus, and the
# collapsed cluster is still at ~150 — 600 sits between them with an order of
# magnitude of headroom on each side. This mattered most to the `none` baseline arm,
# whose short reports were scored `collapse` instead of counted as misses, flattering
# the skill it is supposed to be compared against.
MIN_REPORT_CHARS_BY_KIND = {"capability_micro": 600}


def min_report_chars(kind):
    return MIN_REPORT_CHARS_BY_KIND.get(kind or "", MIN_REPORT_CHARS)


def report_checks(report_text, kind=None):
    text = report_text or ""
    low = text.lower()
    floor = min_report_chars(kind)
    present = [s for s in SECTIONS if s.lower() in low]
    missing = [s for s in SECTIONS if s.lower() not in low]
    banned_hit = next((b for b in BANNED if b in low), None)
    collapsed, reason = False, None
    if banned_hit:
        collapsed, reason = True, "banned phrase: %s" % banned_hit
    elif len(text) < floor:
        collapsed, reason = True, "report is %d chars (< %d for kind %s)" % (
            len(text), floor, kind or "defect_slice")
    return {"sections_present": present, "sections_missing": missing,
            "has_knowledge_line": "знания:" in low, "min_chars": floor,
            "collapsed": collapsed, "collapse_reason": reason, "chars": len(text)}


def budget_profile(events):
    by_tool = {}
    for e in events:
        by_tool[e["tool"]] = by_tool.get(e["tool"], 0) + 1
    return {"tool_calls": len(events), "by_tool": by_tool}


def verdict(case, stream_path, report_text, judge_found):
    """Combine trajectory reach + report checks into one diagnosis.

    Reachable in this increment: "collapse", "ok", "reasoning", "inconclusive",
    "coverage" — exactly the branches below. "fabricated_evidence" (citing a
    proof location that was never actually reached, or a location that doesn't
    back the claim) is reserved for when citecheck.py is wired in a later
    increment; nothing in this module computes citation integrity yet, so this
    function can never return it.
    """
    events = read_events(stream_path)
    reach = proof_reach(events, case.get("proof_locations", []))
    report = report_checks(report_text, case.get("kind"))
    budget = budget_profile(events)

    # Order matters, and judge_found now comes FIRST (Important-7). If the judge —
    # reading the whole report — says the defect was identified, then a report WAS
    # delivered and "collapse" is a false label no matter how short it is. Below
    # that, a collapsed report means no investigation was delivered at all, so
    # neither "coverage" nor "reasoning" describes what happened.
    if judge_found:
        diagnosis = "ok"
    elif report["collapsed"]:
        diagnosis = "collapse"
    elif reach["verdict"] == "reached":
        diagnosis = "reasoning"
    elif reach["verdict"] == "unknown":
        diagnosis = "inconclusive"
    else:
        diagnosis = "coverage"

    return {"case_id": case.get("case_id"), "diagnosis": diagnosis,
            "judge_found": bool(judge_found), "requires": case.get("requires", ""),
            "reach": reach, "report": report, "budget": budget}
