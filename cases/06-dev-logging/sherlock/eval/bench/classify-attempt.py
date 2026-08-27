#!/usr/bin/env python3
"""Classify one finished qwen attempt: is it resumable, and WHY did it end?

    classify-attempt.py OUT_JSON REPORT_PATH REASON_PATH EXIT_CODE ERR_TXT SIGNALS_JSON

stdout = the session id, when the attempt is resumable.
exit 0 = resumable, exit 1 = nothing to resume.

This used to be a heredoc inside `run-bench.sh` called `broken_session`, and it
got two things wrong on run 20260826T224846Z-v39 — the best run the bench has
ever produced, 153 calls, 42.3 % cache, $0.085443, `work/checkpoint.json` at
`ready_for_synthesis` with 262/262 resolved, one step from the report.

1. IT READ A SUBAGENT'S OUTCOME AS THE RUN'S. It took the FIRST `type: result`
   row. In that trace row 118 of 432 is a subagent's terminal row
   (`error.message: "MAX_TURNS"`, `num_turns: 0`) — rows 114-117 all carry
   `parent_tool_use_id: call_00_ZQ5JwwwiUGOcRZfUprWq2806` and row 119 is the
   parent's `tool_result` for that very call. The RUN's own outcome is row 431:
   `subtype: success`, `is_error: false`, `num_turns: 36`. A `result` row that
   is not the last row belongs to a subagent; the run's outcome is the LAST one.

2. IT CALLED A BUDGET OVERRUN `broken_stream`. Nothing was broken — not the
   transport, not the stream, not the provider. Two runs have now looked like
   transport problems because of that name. A budget that ends a run gets its
   own name here: `budget_exhausted_turns` (qwen exit 53 /
   `FatalTurnLimitedError`, or a run-level `error.message: MAX_TURNS`),
   `budget_exhausted_walltime` and `budget_exhausted_tool_calls` (exit 55 /
   `FatalBudgetExceededError`, told apart by the flag qwen names in its own
   abort message). The session is still on disk, so a budget overrun stays
   resumable and keeps the same resume machinery — only the label changes.

Every signal the verdict is made from is written to SIGNALS_JSON. A term that
is computed and then reaches neither the exit code nor an artifact is this
project's signature defect; there is a test that pins the exact key set.
"""
import json
import os
import re
import sys

SESSION_RE = re.compile(r'"session_id"\s*:\s*"([0-9a-f-]{16,})"')

# qwen-code 0.22.0 exit codes (chunk-A5F2YNO6.js): FatalTurnLimitedError -> 53,
# FatalBudgetExceededError -> 55.
EXIT_TURN_LIMIT = 53
EXIT_BUDGET = 55
BUDGET_REASON = {"turns": "budget_exhausted_turns",
                 "walltime": "budget_exhausted_walltime",
                 "tool_calls": "budget_exhausted_tool_calls",
                 "unspecified": "budget_exhausted_unspecified"}


def main(argv):
    out_path, report_path, reason_path, raw_exit, err_path, signals_path = argv[1:7]

    signals = {"result_rows_total": 0, "subagent_result_rows": 0,
               "run_result_row_index": None, "parse_failed": False,
               "exit_code": None, "budget_kind": "", "api_error": False,
               "tool_calls": None, "has_report": False, "reason": "",
               "session_id": "", "resumable": False}
    try:
        signals["exit_code"] = int(raw_exit)
    except (TypeError, ValueError):
        signals["exit_code"] = None

    def finish(resumable, reason="", session=""):
        signals["resumable"] = bool(resumable)
        signals["reason"] = reason
        signals["session_id"] = session
        try:
            with open(signals_path, "w", encoding="utf-8") as fh:
                json.dump(signals, fh, ensure_ascii=False, sort_keys=True)
                fh.write("\n")
        except OSError:
            pass
        if reason:
            try:
                with open(reason_path, "w", encoding="utf-8") as fh:
                    fh.write(reason + "\n")
            except OSError:
                pass
        if resumable and session:
            sys.stdout.write(session + "\n")
            return 0
        return 1

    try:
        raw = open(out_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return finish(False)
    try:
        stderr_text = open(err_path, encoding="utf-8", errors="replace").read()
    except OSError:
        stderr_text = ""
    try:
        signals["has_report"] = os.path.getsize(report_path) > 0
    except OSError:
        signals["has_report"] = False

    # WHICH BUDGET, FROM QWEN'S OWN ABORT MESSAGE. Exit 55 covers both the
    # wall-clock and the tool-call budget; qwen names the flag it enforced
    # ("... exceeded (--max-wall-time)."), so read that rather than guess.
    if signals["exit_code"] == EXIT_TURN_LIMIT:
        signals["budget_kind"] = "turns"
    elif signals["exit_code"] == EXIT_BUDGET:
        if "--max-wall-time" in stderr_text:
            signals["budget_kind"] = "walltime"
        elif "--max-tool-calls" in stderr_text:
            signals["budget_kind"] = "tool_calls"
        else:
            signals["budget_kind"] = "unspecified"

    try:
        rows = json.loads(raw)
    except ValueError:
        # A broken provider can leave qwen with a partial JSON array. Its system
        # record already has the saved session id, so resume it instead of
        # discarding all prior work because the final record is unparseable.
        signals["parse_failed"] = True
        match = SESSION_RE.search(raw)
        if match:
            return finish(True, "broken_stream", match.group(1))
        return finish(False)

    if not isinstance(rows, list):
        rows = [rows]
    results = [(i, r) for i, r in enumerate(rows)
               if isinstance(r, dict) and r.get("type") == "result"]
    signals["result_rows_total"] = len(results)
    if results:
        signals["run_result_row_index"] = results[-1][0]
        signals["subagent_result_rows"] = len(results) - 1
    final = results[-1][1] if results else {}

    text = final.get("result") or ""
    signals["api_error"] = bool(
        text.lstrip().startswith("[API Error")
        or ("[API Error" in text and len(text) < 400))
    error = final.get("error") if isinstance(final.get("error"), dict) else {}
    if not signals["budget_kind"] and str(error.get("message") or "") == "MAX_TURNS":
        signals["budget_kind"] = "turns"

    stats = final.get("stats") if isinstance(final.get("stats"), dict) else {}
    tools = stats.get("tools") if isinstance(stats.get("tools"), dict) else {}
    signals["tool_calls"] = tools.get("totalCalls")

    session = final.get("session_id") or next(
        (r.get("session_id") for r in rows
         if isinstance(r, dict) and r.get("session_id")), "")

    # A run that delivered the artifact is finished, whatever ended it.
    if signals["has_report"]:
        return finish(False)

    if signals["budget_kind"]:
        return finish(True, BUDGET_REASON[signals["budget_kind"]], session)

    broken = (not final) or bool(final.get("is_error")) or signals["api_error"]
    if broken:
        return finish(True, "broken_stream", session)

    # A CLEAN STOP THAT DELIVERED NOTHING IS ALSO A REASON TO CONTINUE. Judge it
    # on the artifact and on whether the model actually did anything, never on
    # the prose — a plan announced in the final message reads exactly like a
    # report. The two shapes stay distinguishable because they need different
    # diagnoses: `stalled_no_tool_calls` means the model never started, which
    # points at the skill's first-turn instruction; `no_deliverable` means it
    # worked and did not write, which points at the write-back step.
    reason = "stalled_no_tool_calls" if signals["tool_calls"] == 0 else "no_deliverable"
    return finish(True, reason, session)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
