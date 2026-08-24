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
if [ -n "${SHERLOCK_TIMEOUT+x}" ]; then
  TIMEOUT="$SHERLOCK_TIMEOUT"
elif [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; then
  TIMEOUT=5400
else
  TIMEOUT=2700
fi
RUNS="${BENCH_RUNS:-$HERE/runs}"
CONTROLLED=0
if [ -n "${SHERLOCK_RUN_TAG:-}" ] || [ -n "${SHERLOCK_TRACE:-}" ]; then
  [ -n "${SHERLOCK_RUN_TAG:-}" ] && [ -n "${SHERLOCK_TRACE:-}" ] || {
    echo "✗ controlled run requires both SHERLOCK_RUN_TAG and SHERLOCK_TRACE" >&2
    exit 2
  }
  CONTROLLED=1
  STAMP="$SHERLOCK_RUN_TAG"
  TRACE="$SHERLOCK_TRACE"
  python3 - "$RUNS" "$STAMP" "$TRACE" <<'PY' || exit 2
import os, re, stat, sys
runs, tag, trace = sys.argv[1:]
if not os.path.isabs(runs) or not os.path.isabs(trace):
    raise SystemExit("controlled run paths must be absolute")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", tag) or tag in (".", ".."):
    raise SystemExit("invalid controlled run tag")
if os.path.realpath(runs) != os.path.normpath(runs):
    raise SystemExit("BENCH_RUNS must be canonical and contain no symlink component")
expected = os.path.join(runs, tag)
if trace != expected or os.path.realpath(trace) != trace:
    raise SystemExit("controlled trace is not canonical BENCH_RUNS/tag")
try:
    mode = os.lstat(trace).st_mode
except OSError as exc:
    raise SystemExit("controlled trace missing: %s" % exc)
if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
    raise SystemExit("controlled trace must be a no-symlink directory")
if os.listdir(trace) != ["run-manifest.json"]:
    raise SystemExit("controlled trace collision before launch")
manifest = os.lstat(os.path.join(trace, "run-manifest.json")).st_mode
if not stat.S_ISREG(manifest) or stat.S_ISLNK(manifest):
    raise SystemExit("run-manifest.json must be a regular no-symlink file")
PY
else
  mkdir -p "$RUNS"
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$ARM"
  TRACE="$RUNS/$STAMP"
  mkdir -p "$TRACE"
fi
DATASET="${SHERLOCK_DATASET:-bench649}"
MEASURE_DIR="$(cd "$HERE/../../measure" && pwd)"
STATE_TOOL="$MEASURE_DIR/run_state.py"
ATTEMPT_FILE="$TRACE/current-attempt"
W=""
TERMINAL_WRITTEN=0
PROOF_PID="" PROOF_START="" PROOF_PGID="" PROOF_BOOT="" PROOF_COMMAND=""
state_set() {
  if [ "$CONTROLLED" = 1 ]; then
    python3 "$STATE_TOOL" set "$TRACE/status.json" "$@" --pid "$PROOF_PID" \
      --process-start-ticks "$PROOF_START" --pgid "$PROOF_PGID" \
      --boot-id-sha256 "$PROOF_BOOT" --command-sha256 "$PROOF_COMMAND"
  else
    python3 "$STATE_TOOL" set "$TRACE/status.json" "$@"
  fi
}
state_event() {
  if [ "$CONTROLLED" = 1 ]; then
    python3 "$STATE_TOOL" event "$TRACE/status-events.jsonl" "$@" --pid "$PROOF_PID" \
      --process-start-ticks "$PROOF_START" --pgid "$PROOF_PGID" \
      --boot-id-sha256 "$PROOF_BOOT" --command-sha256 "$PROOF_COMMAND"
  else
    python3 "$STATE_TOOL" event "$TRACE/status-events.jsonl" "$@"
  fi
}
if [ "$CONTROLLED" = 1 ]; then
  python3 - "$TRACE/.runner-ready" <<'PY' || exit 2
import os, sys
path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try: os.fsync(fd)
finally: os.close(fd)
directory = os.open(os.path.dirname(path), os.O_RDONLY)
try: os.fsync(directory)
finally: os.close(directory)
PY
  proof_path="$TRACE/controller-process.json"
  proof_wait=0
  while [ ! -f "$proof_path" ] && [ "$proof_wait" -lt 300 ]; do
    sleep 0.1
    proof_wait=$((proof_wait + 1))
  done
  [ -f "$proof_path" ] || { echo "✗ controller process proof was not supplied" >&2; exit 2; }
  proof_values="$(python3 - "$proof_path" <<'PY'
import json, re, stat, sys
path = sys.argv[1]
mode = __import__('os').lstat(path).st_mode
if not stat.S_ISREG(mode) or stat.S_ISLNK(mode): raise SystemExit(1)
with open(path, encoding="utf-8") as handle: row = json.load(handle)
fields = {"pid", "process_start_ticks", "pgid", "boot_id_sha256", "command_sha256"}
if set(row) != fields: raise SystemExit(1)
if any(type(row[name]) is not int or row[name] <= 0 for name in ("pid", "process_start_ticks", "pgid")):
    raise SystemExit(1)
if row["pid"] != row["pgid"]: raise SystemExit(1)
if any(not isinstance(row[name], str) or not re.fullmatch(r"[0-9a-f]{64}", row[name])
       for name in ("boot_id_sha256", "command_sha256")): raise SystemExit(1)
print(row["pid"], row["process_start_ticks"], row["pgid"], row["boot_id_sha256"], row["command_sha256"])
PY
)" || { echo "✗ invalid controller process proof" >&2; exit 2; }
  read -r PROOF_PID PROOF_START PROOF_PGID PROOF_BOOT PROOF_COMMAND <<EOF
$proof_values
EOF
fi
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
if [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; then
  mkdir -p "$W/work"
  if [ -n "${SHERLOCK_SEED_WORK:-}" ]; then
    [ -d "$SHERLOCK_SEED_WORK" ] && [ ! -L "$SHERLOCK_SEED_WORK" ] || {
      echo "✗ SHERLOCK_SEED_WORK must be a real directory" >&2
      exit 1
    }
    cp -a "$SHERLOCK_SEED_WORK/." "$W/work/" || exit 1
  fi
  python3 "$SKILLS/$ARM/tools/stage-corpus.py" "$RUN_CORPUS" \
    --map "$W/work/path-map.tsv" > "$TRACE/path-stage.json" || exit 1
  if [ -n "${SHERLOCK_SEED_WORK:-}" ]; then
    python3 "$SKILLS/$ARM/tools/checkpoint.py" init --work "$W/work" \
      > "$TRACE/checkpoint-pre.json" || exit 1
  fi
fi
# The trajectory is the ONLY way to tell "never opened the file" from "opened it
# and closed it wrongly" from "found it and discarded it" — and that is exactly
# the question every arm since v5 exists to answer. It used to be deleted on
# exit, so five runs in a row were unreadable. Keep it next to the ledger.
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

# STATE THE CONTEXT WINDOW OUTRIGHT, same as run-case.sh. This is the runner on
# the 649 MB corpus, so a 177,000-token ceiling hurts here most of all.
# → measure/run-case.sh for why the default is 400,000 and not 1,048,576.
CTX_WINDOW="${SHERLOCK_CONTEXT_WINDOW:-400000}"
REQUEST_TIMEOUT_MS="${SHERLOCK_REQUEST_TIMEOUT_MS:-900000}"
MAX_RETRIES="${SHERLOCK_MAX_RETRIES:-0}"
# CORRECTED 2026-08-24: a `general-purpose` subagent launched by the `agent`
# tool DOES see the project `.qwen/skills/` directory (qwen-code 0.21.1,
# measured on this box against the subscription broker) — it listed 23 skills
# including this project's own `sherlock` and successfully called `skill`.
# Source-side: `skill` is absent from EXCLUDED_TOOLS_FOR_SUBAGENTS, and the
# child's Config is `Object.create(parentConfig)`, so targetDir and the skill
# manager come from the parent. The old "does not inherit" claim above this
# line was wrong; see run-case.sh for what the v11 SUBAGENT-SPAWNED rows were
# actually evidence of. The real hazards are: an explicit subagent `tools:`
# allowlist that omits `skill` silently drops both the tool AND the catalogue
# (`willHaveSkillTool()`), and top-level subagents default to running in the
# BACKGROUND unless `run_in_background: false` is passed. `agent` still stays
# excluded here by default — not because fan-out is known to break skill
# delivery, but to hold fan-out as ONE deliberately-fixed variable in a
# reproducible bench arm rather than reopen it mid-series. Keep the target
# bench on the same, skill-loaded execution path; flip
# SHERLOCK_ALLOW_SUBAGENT=1 for a measured control arm.
EXCLUDE_JSON=''
if [ "${SHERLOCK_ALLOW_SUBAGENT:-0}" != "1" ]; then
  EXCLUDE_JSON=', "tools": { "exclude": ["agent"] }'
fi
if [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; then
  case "$REQUEST_TIMEOUT_MS:$MAX_RETRIES" in
    *[!0-9:]*|:*|*:) echo "✗ invalid v30 request timeout or retry count" >&2; exit 1 ;;
  esac
  mkdir -p "$W/.qwen"
  MEMORY_JSON=''
  if [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; then
    MEMORY_JSON=', "memory": { "enableManagedAutoMemory": false, "enableDreams": false }, "model_fallback": { "enabled": false }'
  fi
  printf '{ "model": { "generationConfig": { "contextWindowSize": %s, "timeout": %s, "maxRetries": %s } }%s%s }\n' \
    "$CTX_WINDOW" "$REQUEST_TIMEOUT_MS" "$MAX_RETRIES" "$EXCLUDE_JSON" "$MEMORY_JSON" > "$W/.qwen/settings.json"
elif [ "$CTX_WINDOW" != "0" ]; then
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
  export QWEN_SKILL_ROOT="$W/.qwen/skills/log-rca"
else
  unset QWEN_SKILL_ROOT
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
if { [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; } && [ -n "${SHERLOCK_SEED_WORK:-}" ]; then
  PROMPT="$PROMPT

Продолжи расследование из сохранённого checkpoint в $W/work. Сначала прочитай
work/checkpoint.json. Не повторяй MAP и TRIAGE, если state=ready_for_synthesis.
Используй новый безопасный путь корпуса $RUN_CORPUS и work/path-map.tsv.
Сразу собери work/report.md, затем выполни triagecheck и citecheck и исправь
только ошибки проверки. Последний ответ должен дословно повторять work/report.md."
fi

if [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; then
  # r4 answered in one request with stats.skills.totalCalls == 0. Name the skill.
  PROMPT="/sherlock

$PROMPT"
fi

# THE SAME UPSTREAM LANE AS run-case.sh. This runner used to talk to linkapi
# directly, which cost it two things: no row could be attributed to an upstream
# (the alias fans out to identities ~19x apart on tool-calling), and the CLI was
# handed `[SP]deepseek-v4-flash`, whose bracket prefix defeats qwen-code's own
# model-id table and pins the context window to 200,000 — the "177,000-token
# ceiling". On the 649 MB corpus that is the runner where it hurts most.
. "$MEASURE_DIR/upstream-lane.sh"
if ! upstream_lane_start "$BASE_URL" "$TRACE.upstream.jsonl" "$STAMP" "$MODEL" \
  "$TRACE/upstream-inflight.json" "$ATTEMPT_FILE"; then
  state_set --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --reason ATTRIBUTION_UNAVAILABLE --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  state_event RUN_FAILED --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --reason ATTRIBUTION_UNAVAILABLE --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  TERMINAL_WRITTEN=1
  exit 3
fi
BASE_URL="$LANE_BASE_URL"
CLIENT_MODEL="$LANE_CLIENT_MODEL"
if [ "$CONTROLLED" = 1 ]; then
  unset SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS \
    SHERLOCK_BUDGET_MAX_REQUEST_BYTES \
    SHERLOCK_BUDGET_MAX_WALL_SECONDS \
    SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES
fi

echo "▶ bench arm=$ARM  dataset=$DATASET  corpus=$(du -sh "$CORPUS" | cut -f1)  model=$MODEL"
START=$(date +%s)
# A stream can break after the agent has already mapped most of the corpus. Keep
# its QWEN_HOME and resume the same session with bounded exponential backoff;
# never replace useful mid-session work with a fresh, empty investigation.
if [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ]; then
  RESUME_MAX_ATTEMPTS="${SHERLOCK_RESUME_MAX_ATTEMPTS:-0}"
else
  RESUME_MAX_ATTEMPTS="${SHERLOCK_RESUME_MAX_ATTEMPTS:-2}"
fi
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

QWEN_RC=0
if run_qwen 0 -p "$PROMPT"; then QWEN_RC=0; else QWEN_RC=$?; fi
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
  if run_qwen "$RESUME_ATTEMPTS" --resume "$RESUME_SESSION" -p "The previous provider stream failed. Continue the same investigation from saved state. Do not restart mapping; finish the unresolved worklist and deliver the report."; then QWEN_RC=0; else QWEN_RC=$?; fi
done
python3 - "$TRACE/recovery.json" "$RESUME_ATTEMPTS" "$RESUME_SESSION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"resume_attempts": int(sys.argv[2]), "session_id": sys.argv[3]}, fh)
    fh.write("\n")
PY
save_trace
if [ -f "$TRACE/candidate.json" ] && [ "${QWEN_RC:-2}" -eq 0 ]; then RC=0; else RC=2; fi
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
