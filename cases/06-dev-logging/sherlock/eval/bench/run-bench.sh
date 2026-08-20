#!/usr/bin/env bash
# Benchmark run against the 649 MB / 26-format / 11-planted-defect corpus.
#
#   run-bench.sh <none|v1|v2|v3>
#
# Why this exists, separately from eval/run.sh:
#   * the five A/B datasets are all SINGLE-FILE, so they cannot demonstrate the
#     coverage discipline that is the design's central claim (28 files here);
#   * the 100/73/18 and 79/79 figures were measured on a different model — this
#     puts the same corpus in front of DeepSeek-V4-Flash, the corporate model;
#   * it yields the organizers' «≥50 % дефектов» number against a real answer key.
#
# Produces a sealed candidate; validate-run.py exclusively owns accepted ledger rows.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$(cd "$HERE/../.." && pwd)/skills"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
ARM="${1:-unknown}"
CORPUS="${SHERLOCK_CORPUS:-}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-2700}"
RUNS="${BENCH_RUNS:-$HERE/runs}"; mkdir -p "$RUNS"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$ARM"
TRACE="$RUNS/$STAMP"
DATASET="${SHERLOCK_DATASET:-bench649}"
MEASURE_DIR="$(cd "$HERE/../../measure" && pwd)"
STATE_TOOL="$MEASURE_DIR/run_state.py"
ATTEMPT_FILE="$TRACE/current-attempt"
W=""
TERMINAL_WRITTEN=0
state_set() { python3 "$STATE_TOOL" set "$TRACE/status.json" "$@"; }
state_event() { python3 "$STATE_TOOL" event "$TRACE/status-events.jsonl" "$@"; }
mkdir -p "$TRACE"
state_set --run-tag "$STAMP" --phase STAGING --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE"
state_event STAGING --run-tag "$STAMP" --phase STAGING --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE"
save_trace() {
  [ -n "$W" ] || return 0
  mkdir -p "$TRACE"
  if [ -n "${LANE_PROXY_PID:-}" ]; then
    kill "$LANE_PROXY_PID" 2>/dev/null || true
    wait "$LANE_PROXY_PID" 2>/dev/null || true
    unset LANE_PROXY_PID
  fi
  if [ -f "$W/out.json" ]; then cp "$W/out.json" "$TRACE/out.json" || return 1; fi
  for partial in "$W"/out-attempt-*.json; do [ -f "$partial" ] && cp "$partial" "$TRACE/$(basename "$partial")"; done
  for partial in "$W"/err-attempt-*.txt "$W"/exit-attempt-*.txt; do [ -f "$partial" ] && cp "$partial" "$TRACE/$(basename "$partial")"; done
  [ -f "$W/attempts.jsonl" ] && cp "$W/attempts.jsonl" "$TRACE/attempts.jsonl"
  [ -f "$W/incomplete.json" ] && cp "$W/incomplete.json" "$TRACE/incomplete.json"
  [ -f "$W/err.txt" ] && cp "$W/err.txt" "$TRACE/err.txt"
  if [ -f "$W/.qwen/settings.json" ]; then
    cp "$W/.qwen/settings.json" "$TRACE/qwen-settings.json" || return 1
  else
    printf '{}\n' > "$TRACE/qwen-settings.json" || return 1
  fi
  if [ -d "$W/work" ]; then
    [ ! -e "$TRACE/work" ] || return 1
    work_copy="$(mktemp -d "$TRACE/.work.XXXXXX")" || return 1
    cp -r "$W/work/." "$work_copy/" || return 1
    mv "$work_copy" "$TRACE/work" || return 1
  fi
  if [ -f "$W/.sherlock/active.json" ]; then
    mkdir -p "$TRACE/.sherlock"
    python3 - "$W/.sherlock/active.json" "$TRACE/.sherlock/active.json" "$TRACE" "$CORPUS" "$SKILLS/$ARM" <<'PY'
import json, os, sys, tempfile
source, target, trace, corpus, skill = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    row = json.load(handle)
row.update({"workspace": os.path.realpath(trace), "out": os.path.realpath(os.path.join(trace, "work")),
            "corpus": os.path.realpath(corpus), "skill_root": os.path.realpath(skill)})
directory = os.path.dirname(target)
fd, temporary = tempfile.mkstemp(prefix=".active.", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(row, handle, ensure_ascii=False, sort_keys=True); handle.write("\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, target); os.chmod(target, 0o600)
    directory_fd = os.open(directory, os.O_RDONLY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY
  fi
  [ -n "${QWEN_HOME:-}" ] && [ -d "$QWEN_HOME" ] && cp -r "$QWEN_HOME" "$TRACE/qwen-home"
  if [ -f "$TRACE.upstream.jsonl" ]; then
    cp "$TRACE.upstream.jsonl" "$TRACE/upstream-completed.jsonl" || return 1
  else
    : > "$TRACE/upstream-completed.jsonl"
  fi
  sync || return 1
  python3 - "$TRACE" "$STAMP" <<'PY' || return 1
import json, os, sys, tempfile
trace, run_tag = sys.argv[1:]
out = os.path.join(trace, "out.json")
try:
    with open(out, encoding="utf-8") as handle: stream = json.load(handle)
except (OSError, ValueError, TypeError):
    sys.exit(0)
rows = stream if isinstance(stream, list) else [stream]
results = [(i, row) for i, row in enumerate(rows)
           if isinstance(row, dict) and row.get("type") == "result"]
if len(results) != 1 or results[0][0] != len(rows) - 1:
    sys.exit(0)
final = results[0][1]; text = final.get("result") or ""
errored = (final.get("is_error") is True or text.lstrip().startswith("[API Error")
           or ("[API Error" in text and len(text) < 400))
usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}
candidate = {"schema": 1, "run_tag": run_tag, "result_stream": "out.json",
             "work_root": "work", "artifact": "work/report.md",
             "upstream_completed": "upstream-completed.jsonl",
             "transport": {"exit_code": None, "status": "error" if errored else "success",
                           "duration_s": None},
             "usage": {"turns": None if errored else final.get("num_turns"),
                       "input_tokens": None if errored else usage.get("input_tokens"),
                       "output_tokens": None if errored else usage.get("output_tokens")}}
fd, temporary = tempfile.mkstemp(prefix=".candidate.", dir=trace)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(candidate, handle, ensure_ascii=False, sort_keys=True); handle.write("\n")
        handle.flush(); os.fsync(handle.fileno())
    os.link(temporary, os.path.join(trace, "candidate.json"), follow_symlinks=False)
    directory = os.open(trace, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY
  rm -rf "$W"
  W=""
}
on_exit() {
  local rc="$1"
  trap - EXIT
  if [ "$TERMINAL_WRITTEN" = 0 ]; then
    state_set --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" --exit-code "$rc"
    state_event RUN_FAILED --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" --exit-code "$rc"
  fi
  save_trace
  return "$rc"
}
trap 'on_exit $?' EXIT
[ "$ARM" != unknown ] || { echo "usage: run-bench.sh <none|v1|v2|v3>" >&2; exit 2; }
[ -d "$CORPUS" ] || { echo "✗ corpus not found: $CORPUS" >&2; exit 1; }
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY}"
W="$(mktemp -d "${TMPDIR:-/tmp}/bench-XXXXXX")"
# Qwen Code only grants file tools access to its project workspace. Giving the
# prompt an absolute corpus path outside that workspace produces a one-turn
# refusal, even under yolo. Stage a private read-only-in-practice copy instead
# of a symlink: a symlink resolves outside the boundary and is denied again.
RUN_CORPUS="$W/corpus"
mkdir -p "$RUN_CORPUS"
cp -a "$CORPUS/." "$RUN_CORPUS/"
# The trajectory is the ONLY way to tell "never opened the file" from "opened it
# and closed it wrongly" from "found it and discarded it" — and that is exactly
# the question every arm since v5 exists to answer. It used to be deleted on
# exit, so five runs in a row were unreadable. Keep it next to the ledger.
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

# STATE THE CONTEXT WINDOW OUTRIGHT, same as run-case.sh. This is the runner on
# the 649 MB corpus, so a 177,000-token ceiling hurts here most of all.
# → measure/run-case.sh for why the default is 400,000 and not 1,048,576.
CTX_WINDOW="${SHERLOCK_CONTEXT_WINDOW:-400000}"
# Qwen's `agent` tool launches a subagent that does not inherit the project
# `.qwen/skills/` directory. The result is a one-turn no-skill answer from the
# empty runner directory. This exact failure is characterised in run-case.sh;
# keep the target bench on the same, skill-loaded execution path.
EXCLUDE_JSON=''
if [ "${SHERLOCK_ALLOW_SUBAGENT:-0}" != "1" ]; then
  EXCLUDE_JSON=', "tools": { "exclude": ["agent"] }'
fi
if [ "$CTX_WINDOW" != "0" ]; then
  mkdir -p "$W/.qwen"
  printf '{ "model": { "generationConfig": { "contextWindowSize": %s } }%s }\n' \
    "$CTX_WINDOW" "$EXCLUDE_JSON" > "$W/.qwen/settings.json"
elif [ -n "$EXCLUDE_JSON" ]; then
  mkdir -p "$W/.qwen"
  printf '{ "tools": { "exclude": ["agent"] } }\n' > "$W/.qwen/settings.json"
fi

# Seal the exact target settings before the target can observe or mutate them.
python3 - "$W/.qwen/settings.json" "$TRACE/qwen-settings-pre.json" <<'PY' || exit 1
import os, sys, tempfile
source, target = sys.argv[1:]
try:
    with open(source, "rb") as handle: data = handle.read()
except FileNotFoundError:
    data = b"{}\n"
directory = os.path.dirname(target)
fd, temporary = tempfile.mkstemp(prefix=".qwen-settings-pre.", dir=directory)
try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    os.link(temporary, target, follow_symlinks=False)
    directory_fd = os.open(directory, os.O_RDONLY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY

if [ "$ARM" != "none" ]; then
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

# THE PROMPT IS A PROPERTY OF THE CORPUS, NOT OF THE RUNNER.
# It was hard-coded to «Продакшн деградировал» — a production-outage RCA. Pointed
# at an intrusion corpus that asks the model the wrong question and then scores
# the answer, which is a defect the numbers cannot show. Resolution order:
#   1. $SHERLOCK_PROMPT_FILE           — explicit, wins
#   2. $HERE/prompts/$DATASET.txt      — per-corpus, committed next to the key
#   3. the historical outage prompt    — kept ONLY for dataset bench649
PROMPT_FILE="${SHERLOCK_PROMPT_FILE:-$HERE/prompts/$DATASET.txt}"
if [ -f "$PROMPT_FILE" ]; then
  PROMPT="$(cat "$PROMPT_FILE")"
  PROMPT="${PROMPT//\$CORPUS/$RUN_CORPUS}"
  PROMPT="$(printf '%s' "$PROMPT" | sed "s|{CORPUS}|$RUN_CORPUS|g")"
elif [ "$DATASET" = "bench649" ]; then
  PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $RUN_CORPUS.
Найди ВСЕ проблемы и инциденты, определи корневую причину каждой и предложи,
что делать. Ссылайся на конкретные строки в формате файл:строка."
else
  echo "✗ dataset=$DATASET has no prompt: expected $PROMPT_FILE" >&2
  echo "  A corpus without its own question would be scored against an answer" >&2
  echo "  to a different question. Write the prompt file first." >&2
  exit 1
fi

# THE SAME UPSTREAM LANE AS run-case.sh. This runner used to talk to linkapi
# directly, which cost it two things: no row could be attributed to an upstream
# (the alias fans out to identities ~19x apart on tool-calling), and the CLI was
# handed `[SP]deepseek-v4-flash`, whose bracket prefix defeats qwen-code's own
# model-id table and pins the context window to 200,000 — the "177,000-token
# ceiling". On the 649 MB corpus that is the runner where it hurts most.
. "$MEASURE_DIR/upstream-lane.sh"
upstream_lane_start "$BASE_URL" "$TRACE.upstream.jsonl" "$STAMP" "$MODEL" \
  "$TRACE/upstream-inflight.json" "$ATTEMPT_FILE"
BASE_URL="$LANE_BASE_URL"
CLIENT_MODEL="$LANE_CLIENT_MODEL"

echo "▶ bench arm=$ARM  dataset=$DATASET  corpus=$(du -sh "$CORPUS" | cut -f1)  model=$MODEL"
START=$(date +%s)
# A stream can break after the agent has already mapped most of the corpus. Keep
# its QWEN_HOME and resume the same session with bounded exponential backoff;
# never replace useful mid-session work with a fresh, empty investigation.
RESUME_MAX_ATTEMPTS="${SHERLOCK_RESUME_MAX_ATTEMPTS:-2}"
RESUME_BACKOFF_S="${SHERLOCK_RESUME_BACKOFF_S:-15}"
RESUME_ATTEMPTS=0
RESUME_SESSION=""
LAST_SESSION=""
ATTEMPT_REASON=""

session_from_output() {
  python3 - "$W/out.json" <<'PY'
import re, sys
try:
    raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    sys.exit(1)
match = re.search(r'"session_id"\s*:\s*"([0-9a-f-]{16,})"', raw)
if match:
    print(match.group(1))
    sys.exit(0)
sys.exit(1)
PY
}

run_qwen() {
  local attempt="$1"
  shift
  local session="" parsed_session="" started rc finished
  [ "${1:-}" = "--resume" ] && session="${2:-}"
  printf '%s\n' "$attempt" > "$ATTEMPT_FILE"
  started="$(date +%s)"
  state_set --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$attempt" --session-id "$session" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  state_event QWEN_RUNNING --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$attempt" --session-id "$session" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  state_event ATTEMPT_STARTED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$attempt" --session-id "$session" --reason "$ATTEMPT_REASON" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  # key via environment, never argv (visible in ps; this box has a guest account)
  ( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$CLIENT_MODEL" \
      --approval-mode yolo "$@" --output-format json </dev/null \
  ) >"$W/out.json" 2>"$W/err.txt"
  local rc=$?
  finished="$(date +%s)"
  # A resume must never overwrite the diagnostic from the attempt that failed.
  cp "$W/out.json" "$W/out-attempt-$attempt.json"
  cp "$W/err.txt" "$W/err-attempt-$attempt.txt"
  printf '%s\n' "$rc" > "$W/exit-attempt-$attempt.txt"
  parsed_session="$(session_from_output || true)"
  [ -n "$parsed_session" ] && session="$parsed_session"
  [ -n "$session" ] && LAST_SESSION="$session"
  printf '{"attempt":%s,"session_id":"%s","exit_code":%s,"duration_s":%s,"output_bytes":%s,"stderr_bytes":%s}\n' \
    "$attempt" "$session" "$rc" "$((finished - started))" "$(wc -c < "$W/out.json")" "$(wc -c < "$W/err.txt")" \
    >> "$W/attempts.jsonl"
  state_event ATTEMPT_FINISHED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$attempt" --session-id "$session" --exit-code "$rc" \
    --reason "$ATTEMPT_REASON" --duration-s "$((finished - started))" --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  return "$rc"
}

broken_session() {
  python3 - "$W/out.json" <<'PY'
import json, re, sys
try:
    raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    sys.exit(1)
try:
    rows = json.loads(raw)
except ValueError:
    # A broken provider can leave Qwen with a partial JSON array. Its system
    # record already has the saved session id, so resume it instead of discarding
    # all prior work because the final record is not parseable.
    match = re.search(r'"session_id"\s*:\s*"([0-9a-f-]{16,})"', raw)
    if match:
        print(match.group(1))
        sys.exit(0)
    sys.exit(1)
if not isinstance(rows, list):
    rows = [rows]
final = next((r for r in rows if isinstance(r, dict) and r.get("type") == "result"), {})
text = final.get("result") or ""
broken = (not final or final.get("is_error") or text.lstrip().startswith("[API Error")
          or ("[API Error" in text and len(text) < 400))
if not broken:
    sys.exit(1)
session = final.get("session_id") or next(
    (r.get("session_id") for r in rows if isinstance(r, dict) and r.get("session_id")), "")
if session:
    print(session)
    sys.exit(0)
sys.exit(1)
PY
}

run_qwen 0 -p "$PROMPT" || true
while RESUME_SESSION="$(broken_session)" \
  && [ "$RESUME_ATTEMPTS" -lt "$RESUME_MAX_ATTEMPTS" ]; do
  RESUME_ATTEMPTS=$((RESUME_ATTEMPTS + 1))
  ATTEMPT_REASON="broken_stream"
  BACKOFF=$((RESUME_BACKOFF_S * (2 ** (RESUME_ATTEMPTS - 1))))
  state_event RECOVERY_DECIDED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$RESUME_ATTEMPTS" --session-id "$RESUME_SESSION" --reason broken_stream \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  echo "  ⚠ stream failed; preserving session $RESUME_SESSION and retrying in ${BACKOFF}s (attempt $RESUME_ATTEMPTS/$RESUME_MAX_ATTEMPTS)" >&2
  sleep "$BACKOFF"
  run_qwen "$RESUME_ATTEMPTS" --resume "$RESUME_SESSION" -p "The previous provider stream failed. Continue the same investigation from saved state. Do not restart mapping; finish the unresolved worklist and deliver the report." || true
done
python3 - "$TRACE/recovery.json" "$RESUME_ATTEMPTS" "$RESUME_SESSION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"resume_attempts": int(sys.argv[2]), "session_id": sys.argv[3]}, fh)
    fh.write("\n")
PY
save_trace
[ -f "$TRACE/candidate.json" ] && RC=0 || RC=2
if [ "$RC" -eq 0 ]; then
  state_set --run-tag "$STAMP" --phase FINISHED_UNCHECKED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --attempt "$RESUME_ATTEMPTS" --session-id "${LAST_SESSION:-$RESUME_SESSION}" --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  state_event FINISHED_UNCHECKED --run-tag "$STAMP" --phase FINISHED_UNCHECKED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --attempt "$RESUME_ATTEMPTS" --session-id "${LAST_SESSION:-$RESUME_SESSION}" --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  TERMINAL_WRITTEN=1
else
  state_set --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --attempt "$RESUME_ATTEMPTS" --exit-code "$RC" --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  state_event RUN_FAILED --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --attempt "$RESUME_ATTEMPTS" --exit-code "$RC" --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  TERMINAL_WRITTEN=1
fi
# the CLI's own stderr is the only clue when the run produced nothing
[ "$RC" -ne 0 ] && [ -f "$TRACE/err.txt" ] && sed -n '1,8p' "$TRACE/err.txt"
exit "$RC"
