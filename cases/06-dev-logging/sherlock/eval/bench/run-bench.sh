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
# >>> ARM VERSION GATE >>>
# THE INPUT GATE for the arm name (docs/conventions.md): the version is turned
# into a NUMBER once, here, and every downstream decision is a `>=` against it.
# Before this, six decisions — the run timeout, the settings shape, the agent
# install, the seed path, the brief and the skill copy — each carried its own
# literal chain of ten `[ "$ARM" = "vNN" ]` tests. Adding an arm meant editing
# all of them, and forgetting one FAILED SILENTLY: the missed site fell through
# to the pre-v30 branch, so the run would take a 2700-second timeout or an older
# settings block while every other site believed it was a modern arm.
#
# `none` and `unknown` are documented arm names (see the usage line above; ARM
# itself defaults to `unknown`), so they resolve to 0 and simply compare false.
# ANYTHING ELSE ABORTS. That is deliberate and it is the fix-9 lesson: a `case`
# glob whose no-match branch answers "false" silently disarms every check built
# on it, which is exactly how the generation-window guard nearly shipped dead.
arm_num() {
  case "${1-}" in
    none|unknown) printf '0\n' ;;
    v[0-9]|v[0-9][0-9]|v[0-9][0-9][0-9]) printf '%s\n' "${1#v}" ;;
    *) printf 'run-bench.sh: unusable arm %s - expected none, unknown or v<number>\n' \
         "${1:-<unset>}" >&2; exit 2 ;;
  esac
}
arm_ge() {   # arm_ge <arm> <floor> - true when <arm> is v<floor> or newer
  _arm_n="$(arm_num "$1")" || exit 2
  [ "$_arm_n" -ge "$2" ]
}
# <<< ARM VERSION GATE <<<
arm_num "$ARM" >/dev/null   # abort now, not at the first branch
# >>> INTERACTIVE LANE >>>
# THE CORPORATE HARNESS RUNS QWEN INTERACTIVELY (operator, 2026-08-27), so the
# acceptance gate has to run THAT, not `qwen -p` (CLAUDE.md: a gate must run the
# exact target). Nothing else about the lane changes: the same proxy, the same
# ledger, the same settings snapshot, the same gates and the same cost
# accounting — only the way the child is invoked, and who types `/clear`.
#   SHERLOCK_INTERACTIVE=1  drive a real pty session, staged by the arm
# Requires v40 or newer: without checkpoint.py's stage machine there is no
# boundary to drive, and a driver with nothing to wait for would report a
# STAGE_TIMEOUT on a healthy run.
INTERACTIVE="${SHERLOCK_INTERACTIVE:-0}"
case "$INTERACTIVE" in
  0|1) ;;
  *) echo "✗ SHERLOCK_INTERACTIVE must be 0 or 1, got '$INTERACTIVE'" >&2; exit 2 ;;
esac
if [ "$INTERACTIVE" = "1" ] && ! arm_ge "$ARM" 40; then
  echo "✗ SHERLOCK_INTERACTIVE=1 needs v40 or newer (the stage machine); got $ARM" >&2
  exit 2
fi
# <<< INTERACTIVE LANE <<<
CORPUS="${SHERLOCK_CORPUS:-}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
# The ALIAS on purpose — do NOT "pin" a dated snapshot here (PR #77 did; it
# broke the lane). Measured 2026-08-26: GET https://linkapi.ai/v1/models lists
# 130 models and exactly four deepseek-v4 ids — [SP]deepseek-v4-flash,
# [SP]deepseek-v4-pro, and their [次] twins. No dated id is routable:
# `[SP]deepseek-v4-flash-0731` answered HTTP 503
# {"error":{"code":"model_not_found","message":"No available channel for model
# [SP]deepseek-v4-flash-0731 under group auto (distributor)"}} on all 13 calls
# of the v38 launch, zero billed usage. `-0731` is a value the provider RETURNS,
# never one you can SEND. The only defence against provider substitution is the
# returned-side family check in measure/lane_guard.py — see measure/upstream-lane.sh job 1.
MODEL="${SHERLOCK_MODEL:-[次]deepseek-v4-flash}"
# THE IDENTITY THE LANE GUARD CHECKS AGAINST, and why it defaults to $MODEL.
# It used to default to EMPTY, and an empty expected id turned the family check
# OFF. `SHERLOCK_EXPECTED_RETURNED_IDENTITY` is only *required* under
# SHERLOCK_REQUIRE_ATTRIBUTION, which defaults to 0, and the paid launcher that
# produced the v37 incident (sherlock-paid-v37-full-r1.sh) runs under `env -i`
# and sets neither. So on the exact run that got substituted, the only live
# guard was the cache one. The run always knows which id it asked for; that is
# the id it must be answered as. The v37 ledger's first substituted row is row 2
# of 180 - with this on, that incident costs one call instead of the whole run.
EXPECTED_RETURNED_IDENTITY="${SHERLOCK_EXPECTED_RETURNED_IDENTITY:-$MODEL}"
export SHERLOCK_EXPECTED_RETURNED_IDENTITY="$EXPECTED_RETURNED_IDENTITY"
if [ -n "${SHERLOCK_TIMEOUT+x}" ]; then
  TIMEOUT="$SHERLOCK_TIMEOUT"
elif arm_ge "$ARM" 30; then
  TIMEOUT=5400
else
  TIMEOUT=2700
fi
# ── DECLARED BUDGETS ────────────────────────────────────────────────────────
# A BUDGET THAT CAN END A PAID RUN IS PART OF THE EXPERIMENT. It is chosen here,
# passed on the qwen command line, and written into run-inputs.json — never
# inherited from a tool's default, and never discovered afterwards by reading a
# vendor bundle. Three runs have now been ended by a limit; on
# 20260826T224846Z-v39 the limit was invisible in every artifact the run made.
#
# MEASURED on that run (r2), attempt 0 — 2,944 s, 153 upstream calls, 42.3 %
# cache, $0.085443, reaching work/checkpoint.json
# {"state":"ready_for_synthesis","resolved":262,"total":262} WITHOUT the report:
#   * 99 top-level assistant + 78 top-level user rows => 177 main-loop turns;
#   * 78 top-level tool dispatches (50 run_shell_command, 24 read_file,
#     2 agent, 1 glob, 1 grep_search);
#   * one `agent` subagent ran 125 assistant turns and terminated on qwen-code
#     0.22.0's hard-coded FORK_DEFAULT_MAX_TURNS = 200 (chunk-WZDM44SB.js).
#     That constant has NO flag, NO setting and NO env var, so it cannot be
#     declared — only known, and kept out of the run's own verdict.
# qwen's documented default for `model.maxSessionTurns` is -1 (unlimited), so
# the main session was never capped; sizing below is ~3-5x the measurement
# because the wall-clock budget is the real backstop. These budgets exist to
# stop a runaway loop, not to ration a working run.
MAX_SESSION_TURNS="${SHERLOCK_MAX_SESSION_TURNS-600}"   # 3.4x the 177 measured
MAX_TOOL_CALLS="${SHERLOCK_MAX_TOOL_CALLS-400}"         # 5.1x the 78 measured
# Strictly INSIDE $TIMEOUT, so qwen aborts itself with exit 55 and leaves the
# session on disk instead of being SIGKILLed by `timeout` with nothing to resume.
MAX_WALL_TIME_S="${SHERLOCK_MAX_WALL_TIME_S-$(( TIMEOUT > 600 ? TIMEOUT - 300 : TIMEOUT ))}"
# qwen-code 0.22.0: default 50, hard ceiling 500 (workflow-2FCMBTBZ.js).
WORKFLOW_AGENT_MAX_TURNS="${SHERLOCK_WORKFLOW_AGENT_MAX_TURNS-200}"
budget_check() {
  case "$2" in
    ''|*[!0-9]*) echo "✗ $1 must be a positive integer (got '$2')" >&2; exit 1 ;;
  esac
  [ "$2" -gt 0 ] || { echo "✗ $1 must be > 0 (got '$2')" >&2; exit 1; }
}
budget_check SHERLOCK_MAX_SESSION_TURNS "$MAX_SESSION_TURNS"
budget_check SHERLOCK_MAX_TOOL_CALLS "$MAX_TOOL_CALLS"
budget_check SHERLOCK_MAX_WALL_TIME_S "$MAX_WALL_TIME_S"
budget_check SHERLOCK_WORKFLOW_AGENT_MAX_TURNS "$WORKFLOW_AGENT_MAX_TURNS"
export QWEN_CODE_WORKFLOW_AGENT_MAX_TURNS="$WORKFLOW_AGENT_MAX_TURNS"

# CONFIRM THE FLAG AGAINST THE BINARY, NOT THE DOCS. A flag qwen does not accept
# would kill every future run instantly — worse than the bug this fixes — and
# `--help` on 0.22.0 does not even list these three, so the help text cannot be
# the check. yargs runs strict, so an unknown flag is a PARSE error: adding a
# guaranteed-unknown sentinel makes the probe fail before any model call, and
# the flag under test is accepted iff it is absent from `Unknown arguments:`.
# The probe runs with no key and no base URL, so it cannot spend money even if
# the parse were to succeed.
# It runs in a THROWAWAY directory, too. The probe still launches the binary, and
# a binary launched in the repo checkout writes there: the first cut of this left
# a `work/` tree of stub output inside the working copy and very nearly committed
# it. A parse-only probe has no business seeing the run's cwd.
qwen_flag_preflight() {
  local flag="$1" value="$2" out unknown probe_dir
  probe_dir="$(mktemp -d)" || return 0
  out="$(cd "$probe_dir" && env -u OPENAI_API_KEY -u OPENAI_BASE_URL "$QWEN" "$flag" "$value" \
          --sherlock-flag-probe-sentinel 1 -p x 2>&1 </dev/null || true)"
  rm -rf "$probe_dir"
  unknown="$(printf '%s\n' "$out" | grep 'Unknown argument' || true)"
  if [ -n "$unknown" ] && printf '%s' "$unknown" | grep -q -- "${flag#--}"; then
    echo "✗ installed qwen does not accept $flag — refusing to launch" >&2
    printf '  %s\n' "$unknown" >&2
    exit 1
  fi
}
if [ -x "$QWEN" ] || command -v "$QWEN" >/dev/null 2>&1; then
  qwen_flag_preflight --max-session-turns "$MAX_SESSION_TURNS"
  qwen_flag_preflight --max-tool-calls "$MAX_TOOL_CALLS"
  qwen_flag_preflight --max-wall-time "${MAX_WALL_TIME_S}s"
fi
echo "▶ budgets: --max-session-turns $MAX_SESSION_TURNS  --max-wall-time ${MAX_WALL_TIME_S}s  --max-tool-calls $MAX_TOOL_CALLS  QWEN_CODE_WORKFLOW_AGENT_MAX_TURNS=$WORKFLOW_AGENT_MAX_TURNS  (outer timeout ${TIMEOUT}s)"
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
    # LANE_PROXY_PID_WAS outlives the kill. The RC-5 audit below is gated on
    # "was there a lane on this run?", and it used to read LANE_PROXY_PID -
    # which THIS function had already unset, so on every healthy run the first
    # clause was false, the audit never ran, and LEDGER_MISSING / LEDGER_EMPTY /
    # LEDGER_MALFORMED / RETURNED_MODEL_UNKNOWN / the post-hoc cache check were
    # dead code. The only time it fired was when the live guard had already
    # written a marker, i.e. it could only re-read a verdict someone else made.
    LANE_PROXY_PID_WAS="$LANE_PROXY_PID"
    kill "$LANE_PROXY_PID" 2>/dev/null || true
    wait "$LANE_PROXY_PID" 2>/dev/null || true
    unset LANE_PROXY_PID
  fi
  if [ -f "$W/out.json" ]; then cp "$W/out.json" "$TRACE/out.json" || return 1; fi
  for partial in "$W"/out-attempt-*.json; do [ -f "$partial" ] && cp "$partial" "$TRACE/$(basename "$partial")"; done
  for partial in "$W"/err-attempt-*.txt "$W"/exit-attempt-*.txt; do [ -f "$partial" ] && cp "$partial" "$TRACE/$(basename "$partial")"; done
  [ -f "$W/attempts.jsonl" ] && cp "$W/attempts.jsonl" "$TRACE/attempts.jsonl"
  [ -f "$W/classifications.jsonl" ] && cp "$W/classifications.jsonl" "$TRACE/classifications.jsonl"
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
  python3 - "$TRACE" "$STAMP" "${QWEN_RC:-}" "$(( $(date +%s) - START ))" <<'PY' || return 1
import json, os, sys, tempfile
trace, run_tag, raw_rc, raw_duration = sys.argv[1:]
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
# An UNMEASURED value is null; a value we hold is never null. Both halves of
# that rule were broken here. `exit_code` and `duration_s` were hard-coded None
# while the runner held both — the named v30 r4 defect, which v31 only worked
# around by teaching validate-run.py to read attempts.jsonl instead of fixing
# the source. And `usage` was nulled out on error, so the v34 r2 run that burned
# 9,901,649 input tokens before dying recorded {null, null, null}: a refused run
# is NOT free, and a metered provider makes that a bill nobody can reconstruct.
# Record the numbers unconditionally and put the failure in its own field.
def _int_or_none(value):
    try: return int(value)
    except (TypeError, ValueError): return None

# `stats` is what Qwen already computed and we threw away: per-tool call counts,
# per-identity token splits, TTFB, and files.totalLinesAdded — which is a
# single-field alarm for exactly the v34 r1 failure (model wrote NOTHING to
# disk, yet the run exited 0). Counters and tool names only, no file contents.
candidate = {"schema": 1, "run_tag": run_tag, "result_stream": "out.json",
             "work_root": "work", "artifact": "work/report.md",
             "upstream_completed": "upstream-completed.jsonl",
             "transport": {"exit_code": _int_or_none(raw_rc),
                           "status": "error" if errored else "success",
                           "duration_s": _int_or_none(raw_duration)},
             "stats": final.get("stats"),
             "usage": {"turns": final.get("num_turns"),
                       "input_tokens": usage.get("input_tokens"),
                       "output_tokens": usage.get("output_tokens"),
                       "errored": errored}}
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
  # PERSIST THE TREE THE MODEL WAS ACTUALLY SCORED AGAINST. The staged corpus
  # is not the source corpus: `stage-corpus.py` renames every path containing
  # `%` or whitespace under a `rendered/` prefix (138 of 143 files on winevtx),
  # so a citation like `rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl:147`
  # resolves in the staged tree and NOWHERE in the original. This directory used
  # to die with $W, and reconstructing it from work/path-map.tsv is both fragile
  # and exactly the step that produced a false "the model fabricated its
  # citations" verdict on a run whose report was in fact clean.
  #
  # Hardlinks, not copies: $W and $TRACE are on the same filesystem, so this
  # costs 143 directory entries (~4 KB) instead of 90 MB. The links share inodes
  # with the pristine corpus, so the source must be read-only for the guarantee
  # to hold — hence the post-run digest below, which is the receipt that the
  # scored bytes are still the bytes that were scored.
  if [ -d "$W/corpus" ]; then
    cp -al "$W/corpus" "$TRACE/staged-corpus" 2>/dev/null \
      || cp -a "$W/corpus" "$TRACE/staged-corpus" \
      || echo "  ⚠ could not persist the staged corpus" >&2
    if [ -d "$TRACE/staged-corpus" ]; then
      ( cd "$TRACE/staged-corpus" && find . -type f -print0 | sort -z \
          | xargs -0 -r sha256sum ) > "$TRACE/staged-corpus.sha256" 2>/dev/null \
        || echo "  ⚠ could not digest the staged corpus" >&2
    fi
  fi

  # RECORD THE GATES' OWN VERDICTS. Without this, "the model said it was clean"
  # and "it is clean" are indistinguishable in the artifacts: the only surviving
  # gate output is a tool result inside the trajectory, i.e. model-side text.
  # Both the exit code AND the parsed --json payload are kept, because the exit
  # code alone lies: citecheck's --ledger branch returns early on the ledger
  # numbers and skips the blocking-defect check entirely, so a report with
  # blocking citation defects exits 0. Deriving `verdict` from `blocking` is what
  # validate-run.py already does, and it makes the exit-code bug visible instead
  # of authoritative. The tool sha is recorded because a verdict from this arm's
  # citecheck is not a verdict from the next arm's.
  # KEEP THE TOOLS THAT DID THE GRADING. `replay.sh` must not point into $W,
  # which is about to be deleted, and it must not point at the repo either: the
  # repo moves under you, and a verdict from this arm's citecheck is not a
  # verdict from a later one. The arm snapshots its skill at run start, so this
  # copy is the exact grader — and the digests above plus these tools make a
  # trace self-contained for re-validation.
  ARM_TOOLS="$(dirname "$(ls "$W"/.qwen/skills/*/tools/citecheck.py 2>/dev/null | head -1)" 2>/dev/null)"
  if [ -n "$ARM_TOOLS" ] && [ -d "$ARM_TOOLS" ]; then
    cp -a "$ARM_TOOLS" "$TRACE/gate-tools" 2>/dev/null \
      || echo "  ⚠ could not persist the gate tools" >&2
  fi
  if [ -d "$W/corpus" ] && [ -s "$W/work/report.md" ]; then
    GATE_TOOLS="$ARM_TOOLS"
    if [ -n "$GATE_TOOLS" ] && [ -d "$GATE_TOOLS" ]; then
      python3 - "$TRACE/gates.json" "$GATE_TOOLS" "$W" <<'PY' || echo "  ⚠ gate recording failed" >&2
import hashlib, json, os, subprocess, sys
target, tools, workspace = sys.argv[1:]
def sha(path):
    try:
        with open(path, "rb") as fh: return hashlib.sha256(fh.read()).hexdigest()
    except OSError: return None
gates = {
    "citecheck": ["citecheck.py", "work/report.md", "--corpus", "corpus",
                  "--require-quote", "--ledger", "work/worklist.tsv", "--json"],
    "triagecheck": ["triagecheck.py", "--worklist", "work/worklist.tsv",
                    "--rules", "work/rules.tsv", "--corpus", "corpus", "--json"],
    "statecheck": ["statecheck.py", "--corpus", "corpus",
                   "--report", "work/report.md", "--json"],
}
out = {"schema": 1, "verdict": "clean", "gates": {}}
for name, argv in gates.items():
    script = os.path.join(tools, argv[0])
    command = ["python3", script] + argv[1:]
    row = {"argv": command, "tool_sha256": sha(script)}
    try:
        done = subprocess.run(command, cwd=workspace, capture_output=True,
                              text=True, timeout=1800)
        row["exit_code"] = done.returncode
        row["stderr_tail"] = (done.stderr or "")[-2000:]
        # PRETTY JSON IS STILL JSON. This scanned line by line and json.loads()ed
        # a single line, but citecheck prints json.dumps(..., indent=1), so its
        # first line is a bare "{" and every parse raised — leaving blocking=null
        # on all three gates of the v36 winevtx run and making the both-signals
        # check below dead code. Take the last balanced object in the stream, so
        # a human render printed before the JSON does not matter.
        payload = None
        text = done.stdout or ""
        for start in range(len(text)):
            if text[start] != "{":
                continue
            try:
                candidate, _end = json.JSONDecoder().raw_decode(text[start:])
            except ValueError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:                      # one object per line, older gates
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try: payload = json.loads(line)
                    except ValueError: continue
        row["json"] = payload
        blocking = None
        if isinstance(payload, dict):
            for key in ("blocking", "blocking_defects"):
                if isinstance(payload.get(key), int): blocking = payload[key]; break
        row["blocking"] = blocking
        # A gate is clean only if BOTH signals say so. An exit 0 with blocking>0
        # is the citecheck --ledger bug; a non-zero exit with no json is a crash.
        if done.returncode != 0 or (blocking or 0) > 0: out["verdict"] = "blocking"
    except Exception as error:                       # a crashed gate is not a pass
        row["exit_code"] = None; row["error"] = repr(error)[:500]
        out["verdict"] = "blocking"
    out["gates"][name] = row
with open(target, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1, sort_keys=True); fh.write("\n")
print("  gates.json verdict=%s" % out["verdict"])
PY
    else
      echo "  ⚠ no gate tools found under $W/.qwen/skills/*/tools — gates.json not written" >&2
    fi
  fi

  # REPLAY. Ten lines that remove the human error this whole change exists for:
  # pointing --corpus at the original corpus instead of the staged one. A future
  # validator runs this script and nothing else.
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Re-validate this run with NO reconstruction and no access to the\n'
    printf '# original corpus. Generated by run-bench.sh at %s.\n' "$STAMP"
    printf '# arm=%s dataset=%s\n' "$ARM" "$DATASET"
    printf 'set -u\n'
    printf 'HERE="$(cd "$(dirname "$0")" && pwd -P)"\n'
    printf 'C="$HERE/staged-corpus"   # the tree the model was scored against\n'
    printf 'T="$HERE/gate-tools"      # the exact grader this run used\n'
    printf 'T="${SHERLOCK_GATE_TOOLS:-$T}"\n'
    printf '[ -d "$C" ] || { echo "no staged-corpus in this trace" >&2; exit 2; }\n'
    printf '[ -d "$T" ] || { echo "no gate-tools in this trace; set SHERLOCK_GATE_TOOLS" >&2; exit 2; }\n'
    printf 'cd "$HERE" || exit 2\n'
    printf 'python3 "$T/citecheck.py" work/report.md --corpus "$C" --require-quote --ledger work/worklist.tsv; echo "citecheck rc=$?"\n'
    printf 'python3 "$T/triagecheck.py" --worklist work/worklist.tsv --rules work/rules.tsv --corpus "$C"; echo "triagecheck rc=$?"\n'
    printf 'python3 "$T/statecheck.py" --corpus "$C" --report work/report.md; echo "statecheck rc=$?"\n'
    printf 'echo "recorded verdicts:"; python3 -c %s 2>/dev/null || true\n' "'import json;d=json.load(open(\"gates.json\"));print(d[\"verdict\"], {k:v.get(\"exit_code\") for k,v in d[\"gates\"].items()})'"
  } > "$TRACE/replay.sh"
  chmod +x "$TRACE/replay.sh"

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
if arm_ge "$ARM" 30; then
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
# → measure/run-case.sh for the history of this number.
# MEASURED 2026-08-24: the provider's REAL context window on this lane is
# 262,000 tokens, not 400,000 and not DeepSeek-V4-Flash's advertised 1,048,576.
# A 400,000 window put Qwen's auto-compaction threshold at 0.85 x 400,000 =
# 340,000 tokens, so compaction could NEVER fire before the provider refused
# the request: the fatal v34 r2 request was 828,403 bytes = ~242,000 tokens at
# the measured 3.42 bytes/token, under the fake ceiling and over the real one.
# The default is now 200,000 - deliberately BELOW the real 262,000 so that
# prompt + max_tokens still fits (200,000 + 32,768 = 232,768 < 262,000) and
# compaction fires at 0.85 x 200,000 = 170,000 tokens, with real headroom left.
# Raise it only with a new measurement of the provider's limit; 0 writes nothing.
CTX_WINDOW="${SHERLOCK_CONTEXT_WINDOW:-200000}"
# CLAMP THE OUTPUT BUDGET TOO. Unclamped, qwen-code auto-escalates max_tokens
# (`shouldEscalateMaxOutputTokens`, with a 64K floor) so `prompt + max_tokens`
# overflows the provider window even when the prompt alone fits - the documented
# cause of an empty HTTP 200 (vllm#3851). Setting `samplingParams.max_tokens`
# makes `hasUserMaxTokensOverride` true in qwen-code 0.21.1 and disables that
# escalation; the value is sealed into the settings snapshot, so it is auditable
# per run rather than living in an env var nobody records. 32,768 covers the
# largest report we have ever produced (53,435 bytes ~= 15.6k tokens) twice over.
# 0 writes nothing and restores the old auto-escalating behaviour.
# AND IT MUST FIT THE PROVIDER'S GENERATION WINDOW (fix 9). CloseRouter cuts a
# generation at 90 s - TTFT included - and bills the HTTP 200 it hands back
# with a gateway error chunk in it. Nine of run 20260827T005241Z-v39's ten dead
# calls died at 90,341-90,416 ms, a 75 ms spread across nine independent calls:
# a hard ceiling, not jitter. Measured on that run's own 142 good calls, the
# lane generates 122.6 tokens/s excluding TTFT, so a 32,768-token budget is
# FIVE TIMES more than it can ever deliver and any long turn is guaranteed to
# die. The budget is therefore DERIVED from the window, never chosen by taste:
#   floor((window - ttft_reserve) x tokens_per_s)  =  floor(55 x 122.6) = 6743
# The reserve is 35 s, the largest TTFT this project has ever recorded (v38) -
# not the 6.4 s average, because the average is not what kills a run.
# A lane that declares NO window (-1, the default) skips all of this, so
# linkapi and the free lanes behave exactly as they did before.
# ONE PYTHON GATE, because a shell `case` glob cannot tell a number from `.`,
# `-` or `e` — and every one of those would be read by the arithmetic below as
# "this lane declares no window", DISARMING the check on the exact run that
# asked for it. It also supplies the measured defaults straight out of
# measure/lane_guard.py rather than repeating 122.6 and 35 here: a second copy
# of a measurement is a second thing to forget when the measurement is redone,
# and that is this project's signature defect.
# Each value arrives with a `=` marker when the variable was SET, so an
# UNSET variable (take the default) stays distinguishable from one set to an
# empty or whitespace-only string (a typo, which must fail the run — the same
# rule fix 8 applies to every other budget).
GEN_VARS="$(python3 - "$MEASURE_DIR" \
                     "${SHERLOCK_GENERATION_WINDOW_S+=}${SHERLOCK_GENERATION_WINDOW_S-}" \
                     "${SHERLOCK_OUTPUT_TOKENS_PER_S+=}${SHERLOCK_OUTPUT_TOKENS_PER_S-}" \
                     "${SHERLOCK_TTFT_RESERVE_S+=}${SHERLOCK_TTFT_RESERVE_S-}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from lane_guard import (GENERATION_WINDOW_TOKENS_PER_S,
                        GENERATION_WINDOW_TTFT_RESERVE_S,
                        fitting_max_output_tokens)


def number(name, marked, default):
    """A NUMBER OR AN ABORT. Never a silent fallback: reading an unparseable
    window as "no window" is how a guard disarms itself, and a blank one is a
    typo rather than a decision."""
    if not marked.startswith("="):
        return default                      # the variable was never set
    raw = marked[1:]
    try:
        return float(raw)
    except ValueError:
        sys.stderr.write("\u2717 %s must be a number (got %r)\n" % (name, raw))
        raise SystemExit(1)


window = number("SHERLOCK_GENERATION_WINDOW_S", sys.argv[2], -1.0)
rate = number("SHERLOCK_OUTPUT_TOKENS_PER_S", sys.argv[3],
              GENERATION_WINDOW_TOKENS_PER_S)
reserve = number("SHERLOCK_TTFT_RESERVE_S", sys.argv[4],
                 float(GENERATION_WINDOW_TTFT_RESERVE_S))
print("GEN_WINDOW_S=%s" % ("%g" % window))
print("OUTPUT_TOKENS_PER_S=%s" % ("%g" % rate))
print("TTFT_RESERVE_S=%s" % ("%g" % reserve))
print("GEN_FITTING=%d" % fitting_max_output_tokens(window, rate, reserve))
PY
)" || { echo "✗ refusing to launch: the generation window could not be read" >&2; exit 1; }
eval "$GEN_VARS"
case "${GEN_FITTING:-}" in ''|*[!0-9]*) echo "✗ could not derive the fitting output budget" >&2; exit 1 ;; esac
# The DEFAULT comes from the window when there is one. It is a default, not a
# clamp: an explicitly-set value is refused below rather than quietly shrunk,
# so the number in the launcher always matches the number on the wire.
if [ -n "${SHERLOCK_MAX_OUTPUT_TOKENS:-}" ]; then
  MAX_OUT="$SHERLOCK_MAX_OUTPUT_TOKENS"
elif [ "$GEN_FITTING" != "0" ]; then
  MAX_OUT="$GEN_FITTING"
else
  MAX_OUT=32768
fi
case "$MAX_OUT" in *[!0-9]*|'') echo "✗ invalid SHERLOCK_MAX_OUTPUT_TOKENS" >&2; exit 1 ;; esac
# REFUSE AN IMPOSSIBLE LAUNCH BEFORE SPENDING MONEY. Names all four numbers and
# the value that would fit; silent on a lane that declares no window.
if ! python3 - "$MEASURE_DIR" "$MAX_OUT" "$OUTPUT_TOKENS_PER_S" "$TTFT_RESERVE_S" "$GEN_WINDOW_S" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from lane_guard import generation_window_refusal
why = generation_window_refusal(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
if why:
    sys.stderr.write("\u2717 %s\n" % why)
    raise SystemExit(1)
PY
then
  echo "✗ refusing to launch: the output budget does not fit the lane's generation window" >&2
  exit 1
fi
if [ "$GEN_FITTING" != "0" ]; then
  echo "▶ generation window: ${GEN_WINDOW_S}s at ${OUTPUT_TOKENS_PER_S} tok/s with ${TTFT_RESERVE_S}s reserved for the first token ⇒ max_tokens $MAX_OUT (fits $GEN_FITTING)"
else
  echo "▶ generation window: none declared for this lane (SHERLOCK_GENERATION_WINDOW_S=$GEN_WINDOW_S) ⇒ max_tokens $MAX_OUT unchecked"
fi
SAMPLING_JSON=''
if [ "$MAX_OUT" != "0" ]; then
  SAMPLING_JSON=", \"samplingParams\": { \"max_tokens\": $MAX_OUT }"
fi

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
if arm_ge "$ARM" 30; then
  case "$REQUEST_TIMEOUT_MS:$MAX_RETRIES" in
    *[!0-9:]*|:*|*:) echo "✗ invalid v30 request timeout or retry count" >&2; exit 1 ;;
  esac
  mkdir -p "$W/.qwen"
  MEMORY_JSON=''
  if arm_ge "$ARM" 31; then
    MEMORY_JSON=', "memory": { "enableManagedAutoMemory": false, "enableDreams": false }, "model_fallback": { "enabled": false }'
  fi
  printf '{ "model": { "generationConfig": { "contextWindowSize": %s%s, "timeout": %s, "maxRetries": %s } }%s%s }\n' \
    "$CTX_WINDOW" "$SAMPLING_JSON" "$REQUEST_TIMEOUT_MS" "$MAX_RETRIES" "$EXCLUDE_JSON" "$MEMORY_JSON" > "$W/.qwen/settings.json"
elif [ "$CTX_WINDOW" != "0" ]; then
  mkdir -p "$W/.qwen"
  printf '{ "model": { "generationConfig": { "contextWindowSize": %s%s } }%s }\n' \
    "$CTX_WINDOW" "$SAMPLING_JSON" "$EXCLUDE_JSON" > "$W/.qwen/settings.json"
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
if arm_ge "$ARM" 30 && [ -n "${SHERLOCK_SEED_WORK:-}" ]; then
  PROMPT="$PROMPT

Продолжи расследование из сохранённого checkpoint в $W/work. Сначала прочитай
work/checkpoint.json. Не повторяй MAP и TRIAGE, если state=ready_for_synthesis.
Используй новый безопасный путь корпуса $RUN_CORPUS и work/path-map.tsv.
Сразу собери work/report.md, затем выполни triagecheck и citecheck и исправь
только ошибки проверки. Последний ответ должен дословно повторять work/report.md."
fi

if arm_ge "$ARM" 31; then
  # r4 answered in one request with stats.skills.totalCalls == 0. Name the skill.
  PROMPT="/sherlock

$PROMPT"
fi

# WHAT WAS ACTUALLY SENT. The prompt is assembled here with $RUN_CORPUS
# interpolated into it, and until now it was written NOWHERE in the trace — it
# survived only as model-side text inside the trajectory jsonl. A run whose
# exact input cannot be read back is a run whose result cannot be reproduced or
# even argued about, which is the same defect class as the trajectory that used
# to be deleted on exit. One file, written before the model can influence it.
mkdir -p "$TRACE"
printf '%s' "$PROMPT" > "$TRACE/prompt-sent.txt"
PROMPT_SHA="$(sha256sum "$TRACE/prompt-sent.txt" | cut -d" " -f1)"
# And NAME THE CORPUS. `grep -rl` over a finished trace found the source corpus
# path in no artifact at all: `status.json` carries `dataset: winevtx` and
# nothing else, so post-hoc validation had to guess which directory the run was
# scored against — and guessing wrong produces a confident false accusation
# that the model fabricated its citations.
CORPUS_SOURCE="$(cd "$CORPUS" && pwd -P)"
ARM_COMMIT="$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
# NOTE the absence of a dataset field: test_bench_dataset_truth.py guards
# against this runner writing accepted-ledger fields, and the dataset already
# lives in status.json. One writer per fact.
# AND THE BUDGETS. A future reader must be able to answer "what limits was this
# run under?" from the trace alone — `arm_commit` and `prompt_sha256` already
# make the inputs reproducible, and a cap that can end the run is an input.
# `fork_subagent_max_turns` is qwen-code 0.22.0's hard-coded
# FORK_DEFAULT_MAX_TURNS: not ours to choose, still ours to record, because it
# is what terminated a subagent on run 20260826T224846Z-v39.
# AND THE GENERATION WINDOW, beside them (fix 9). A window is not one of
# `budgets`: those are all positive integers the launcher chose, and this is a
# measured property of the PROVIDER plus the arithmetic derived from it. It
# lives in its own object so the numbers that produced max_output_tokens are on
# disk next to the value itself - "why 6743?" has to be answerable from the
# trace alone, and -1 records "this lane declares no window" explicitly rather
# than by the absence of a key.
python3 - "$TRACE/run-inputs.json" "$CORPUS_SOURCE" "$RUN_CORPUS" "$PROMPT_SHA" "$ARM_COMMIT" "$ARM" \
  "$MAX_SESSION_TURNS" "$MAX_WALL_TIME_S" "$MAX_TOOL_CALLS" "$WORKFLOW_AGENT_MAX_TURNS" "$TIMEOUT" \
  "$GEN_WINDOW_S" "$OUTPUT_TOKENS_PER_S" "$TTFT_RESERVE_S" "$MAX_OUT" "$GEN_FITTING" <<'PY'
import json, sys
(target, corpus_source, staged_root, prompt_sha, arm_commit, arm,
 turns, wall_s, tool_calls, workflow_turns, outer_timeout,
 window_s, tokens_per_s, ttft_reserve_s, max_out, fitting) = sys.argv[1:]
with open(target, "w", encoding="utf-8") as fh:
    json.dump({"schema": 1, "arm": arm,
               "corpus_source": corpus_source, "staged_root": staged_root,
               "prompt_sha256": prompt_sha, "prompt_file": "prompt-sent.txt",
               "arm_commit": arm_commit,
               "budgets": {"max_session_turns": int(turns),
                           "max_wall_time_seconds": int(wall_s),
                           "max_tool_calls": int(tool_calls),
                           "workflow_agent_max_turns": int(workflow_turns),
                           "outer_timeout_seconds": int(outer_timeout),
                           "fork_subagent_max_turns": 200},
               "generation_window": {
                   "generation_window_seconds": float(window_s),
                   "output_tokens_per_second": float(tokens_per_s),
                   "ttft_reserve_seconds": float(ttft_reserve_s),
                   "max_output_tokens": int(max_out),
                   "fitting_max_output_tokens": int(fitting)}},
              fh, ensure_ascii=False, sort_keys=True)
    fh.write("\n")
PY

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
if arm_ge "$ARM" 30; then
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
      --approval-mode yolo \
      --max-session-turns "$MAX_SESSION_TURNS" \
      --max-wall-time "${MAX_WALL_TIME_S}s" \
      --max-tool-calls "$MAX_TOOL_CALLS" \
      "$@" --output-format json </dev/null \
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

# THE INTERACTIVE ARM OF THE SAME FUNCTION. It keeps every receipt run_qwen
# writes — attempts.jsonl, the exit file, the state events — because a run that
# is measured differently cannot be compared with r6. What it does NOT do is
# resume: interactively the stage loop IS the recovery, and a `--resume` would
# hand a fresh session someone else's history, which is the very thing being
# fixed.
#
# out.json is synthesised rather than faked. validate-run.py hashes that file and
# reads one `type: "result"` row from it, so the interactive path writes a real
# record of what happened — the driver's own event log plus the terminal rc —
# and leaves `result` EMPTY, because an interactive session has no final
# assistant message. The deliverable is work/report.md, which is exactly what
# the gates read. An empty `result` is honest; a copied report would be a forged
# transcript.
run_qwen_interactive() {
  local started rc finished
  printf '0\n' > "$ATTEMPT_FILE"
  started="$(date +%s)"
  state_set --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt 0 --session-id "" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  state_event QWEN_RUNNING --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt 0 --session-id "" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  state_event ATTEMPT_STARTED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt 0 --session-id "" --reason interactive \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  ( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    timeout "$TIMEOUT" python3 "$MEASURE_DIR/interactive-drive.py" \
      --work "$W/work" --cwd "$W" \
      --prompt "$PROMPT" \
      --transcript "$W/interactive-transcript.log" \
      --events "$W/interactive-events.jsonl" \
      --stage-budget-s "${SHERLOCK_STAGE_BUDGET_S:-5400}" \
      -- "$QWEN" --auth-type openai --model "$CLIENT_MODEL" \
         --approval-mode yolo \
         --max-session-turns "$MAX_SESSION_TURNS" \
         --max-tool-calls "$MAX_TOOL_CALLS" \
  ) >"$W/interactive-driver.log" 2>"$W/err.txt"
  rc=$?
  finished="$(date +%s)"
  python3 - "$W/out.json" "$W/interactive-events.jsonl" "$rc" "$((finished - started))" <<'PY2'
import json, sys
out, events, rc, duration = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rows = []
try:
    with open(events, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
except OSError:
    pass
json.dump([{"type": "interactive_events", "events": rows},
           {"type": "result", "result": "", "is_error": rc != 0,
            "exit_code": rc, "duration_s": duration,
            "interactive": True, "stages": [r.get("detail") for r in rows
                                            if r.get("event") == "stage_advanced"]}],
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
PY2
  cp "$W/out.json" "$W/out-attempt-0.json"
  cp "$W/err.txt" "$W/err-attempt-0.txt"
  printf '%s\n' "$rc" > "$W/exit-attempt-0.txt"
  printf '{"attempt":0,"session_id":"","exit_code":%s,"duration_s":%s,"output_bytes":%s,"stderr_bytes":%s,"interactive":true}\n' \
    "$rc" "$((finished - started))" "$(wc -c < "$W/out.json")" "$(wc -c < "$W/err.txt")" \
    >> "$W/attempts.jsonl"
  state_event ATTEMPT_FINISHED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt 0 --session-id "" --exit-code "$rc" \
    --reason interactive --duration-s "$((finished - started))" --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  return "$rc"
}

# WHY A CLEAN STOP CAN ALSO NEED A RESUME. v35 r1 on the paid corpus made ONE
# upstream call: HTTP 200, complete 1,542-event stream, `finish_reason: stop`,
# `tool_call: false`, 32,896 prompt / 3,521 completion tokens — every one of
# those completion tokens a `thoughts` token. The model narrated its plan ("Я
# проверю наличие инструментов навыка… Затем выполню пошаговое расследование")
# and stopped without calling a single tool. Nothing was broken: not the
# transport, not the stream, not the provider. But nothing was delivered
# either, and `broken_session` only looked for API errors, so the run had no
# second chance at a first turn that simply stalled.
#
# A stall is resumable for exactly the same reason a broken stream is — the
# session is on disk and the work is not lost — so it goes through the same
# machinery rather than a parallel path. The reason is written to a file
# because this function's stdout is the session id.
broken_session() {
  local rc="${1:-0}" out prc
  out="$(python3 "$HERE/classify-attempt.py" "$W/out.json" "$TRACE/work/report.md" \
          "$W/.resume-reason" "$rc" "$W/err.txt" "$W/.attempt-signals.json")"
  prc=$?
  # EVERY CLASSIFICATION IS KEPT, not just the last one. `.resume-reason` is
  # overwritten each loop; this file is the audit trail of how each attempt was
  # judged, and it is what makes "the harness reported the wrong cause"
  # falsifiable after the fact instead of a matter of memory.
  [ -f "$W/.attempt-signals.json" ] && cat "$W/.attempt-signals.json" >> "$W/classifications.jsonl"
  [ -n "$out" ] && printf '%s\n' "$out"
  return "$prc"
}

QWEN_RC=0
if [ "$INTERACTIVE" = "1" ]; then
  if run_qwen_interactive; then QWEN_RC=0; else QWEN_RC=$?; fi
else
if run_qwen 0 -p "$PROMPT"; then QWEN_RC=0; else QWEN_RC=$?; fi
while RESUME_SESSION="$(broken_session "$QWEN_RC")" \
  && [ "$RESUME_ATTEMPTS" -lt "$RESUME_MAX_ATTEMPTS" ]; do
  RESUME_ATTEMPTS=$((RESUME_ATTEMPTS + 1))
  ATTEMPT_REASON="$(cat "$W/.resume-reason" 2>/dev/null || echo broken_stream)"
  BACKOFF=$((RESUME_BACKOFF_S * (2 ** (RESUME_ATTEMPTS - 1))))
  state_event RECOVERY_DECIDED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$RESUME_ATTEMPTS" --session-id "$RESUME_SESSION" --reason "$ATTEMPT_REASON" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  echo "  ⚠ $ATTEMPT_REASON; preserving session $RESUME_SESSION and retrying in ${BACKOFF}s (attempt $RESUME_ATTEMPTS/$RESUME_MAX_ATTEMPTS)" >&2
  sleep "$BACKOFF"
  if run_qwen "$RESUME_ATTEMPTS" --resume "$RESUME_SESSION" -p "The previous attempt ended without delivering the report. Continue the same investigation from saved state. Do not restart mapping and do not describe what you are about to do: call the tools, write the verdicts back, and write work/report.md. Your final message must be the report itself."; then QWEN_RC=0; else QWEN_RC=$?; fi
done
fi   # SHERLOCK_INTERACTIVE
python3 - "$TRACE/recovery.json" "$RESUME_ATTEMPTS" "$RESUME_SESSION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"resume_attempts": int(sys.argv[2]), "session_id": sys.argv[3]}, fh)
    fh.write("\n")
PY
save_trace
# LANE INTEGRITY, AND WHY IT OUTRANKS EVERY OTHER VERDICT BELOW. The v37 full
# run scored, gated and reported normally while linkapi had answered 93 of its
# 180 calls as `deepseek-v4-pro-0813` instead of the flash model the run
# committed to. Every number that run produced describes a mixture of two
# models, and its prompt-cache hit rate had collapsed 68.1 % -> 28.0 % in plain
# sight of this harness. RC 2/3/4 all say something about the REPORT; this one
# says the run measured the wrong thing, so it is checked first and has its own
# code (5) — never confused with "transport failed" (2), "delivered nothing"
# (3) or "its own gates refused it" (4).
#
# The live guard in the proxy already aborts on the offending call, which is
# where the money is saved. This is the second reading, over the finished
# ledger, and it exists because the guard's own failure mode — a proxy that
# died, or never carried the expected identity — looks exactly like "nothing
# wrong". So absence of proof is a breach here: a missing, empty or malformed
# ledger is RC 5, not a pass. The verdict is written to an artifact because a
# diagnosis that lives only in stderr is a diagnosis nobody reads.
LANE_BREACH=""
LANE_DETAIL=""
# Built as an array, never as `${LANE_BREACH:+--reason "$LANE_BREACH"}`: that
# form keeps the quote characters literally and hands state_set a reason with
# quotes baked into it. Expanded at the call site with the ${a[@]+"${a[@]}"}
# guard, because bash 3.2 reads an empty array as unbound under `set -u`.
LANE_REASON_ARG=()
if [ -n "${LANE_PROXY_PID_WAS:-}" ] || [ -n "${LANE_PROXY_PID:-}" ] || [ -f "${LANE_ABORT_PATH:-}" ]; then
  LANE_AUDIT_ARGS=(--ledger "$TRACE.upstream.jsonl"
                   --abort "${LANE_ABORT_PATH:-}"
                   --expected "$EXPECTED_RETURNED_IDENTITY"
                   # Discarded wrong-model attempts are billed. The count has
                   # to land in an artifact, or a provider that starts
                   # substituting on half its calls just triples the bill in
                   # silence.
                   --summary-json "$TRACE/lane-substitutions.json"
                   # A run that CHANGED PROVIDER mid-flight is a different
                   # scientific object than one that did not. The history is
                   # checked against the ledger and the span is printed where
                   # nobody can miss it; absent file on a single-route run is
                   # simply nothing to say.
                   --advances "$TRACE.upstream.route-advances.jsonl")
  # Thresholds are NOT defaulted here. They live in measure/lane_guard.py, and a
  # second copy in shell is a second thing to forget when they move.
  [ -z "${SHERLOCK_CACHE_MIN_RATE:-}" ] || LANE_AUDIT_ARGS+=(--cache-min-rate "$SHERLOCK_CACHE_MIN_RATE")
  [ -z "${SHERLOCK_CACHE_MIN_CALLS:-}" ] || LANE_AUDIT_ARGS+=(--cache-min-calls "$SHERLOCK_CACHE_MIN_CALLS")
  if [ "${SHERLOCK_CACHE_GUARD:-1}" = "0" ]; then
    LANE_AUDIT_ARGS+=(--no-cache-guard)
  fi
  if LANE_BREACH="$(python3 "$MEASURE_DIR/lane-audit.py" "${LANE_AUDIT_ARGS[@]}" \
      2>"$TRACE/lane-integrity.txt")"; then
    LANE_BREACH=""
  else
    LANE_AUDIT_RC=$?
    # A tool that could not run is not a clean lane. Same rule as the gates:
    # unknown is not clean.
    if [ -z "$LANE_BREACH" ]; then
      LANE_BREACH="LANE_AUDIT_FAILED"
    fi
    LANE_DETAIL="$(sed -n '1,3p' "$TRACE/lane-integrity.txt" 2>/dev/null || true)"
    LANE_REASON_ARG=(--reason "$LANE_BREACH")
    echo "  rc=$LANE_AUDIT_RC $LANE_DETAIL" >&2
  fi
  python3 - "$TRACE/lane-integrity.json" "$LANE_BREACH" "$LANE_DETAIL" <<'PY'
import json, sys
target, breach, detail = sys.argv[1:4]
with open(target, "w", encoding="utf-8") as fh:
    json.dump({"schema": 1, "verdict": "breach" if breach else "clean",
               "reason": breach or None, "detail": detail or None},
              fh, ensure_ascii=False, sort_keys=True)
    fh.write("\n")
PY
fi
# A RUN THAT PRODUCED NO DELIVERABLE IS NOT A SUCCESS. v34 r1 exited 0 with
# phase FINISHED_UNCHECKED and no work/report.md at all: the model wrote its
# report as chat prose and nothing objected. `validate-run.py` has always had
# the check (`missing_deliverable`), but it is only reached through
# bench-controller.sh, and every paid launcher calls THIS script directly — so
# on the metered path it was dead code. The artifact test belongs here, on the
# only line that decides the exit code. Kept as its own RC (3) so
# "delivered nothing" is never confused with "transport failed" (2).
# RC=4 — DELIVERED BUT REFUSED. The v36 winevtx run exited 0 while gates.json
# said verdict=blocking: the file is computed a few hundred lines above, in this
# same process, and was never consulted. Any caller reading $? saw success on a
# report its own gates had rejected. Kept as its own code so "refused" is never
# confused with "transport failed" (2) or "delivered nothing" (3).
# A guard whose own error path is a false green is not a guard. An unreadable
# or malformed gates.json means the verdict is UNKNOWN, and unknown is not clean.
GATE_VERDICT=""
if [ -f "$TRACE/gates.json" ]; then
  GATE_VERDICT="$(python3 -c 'import json,sys
try:
    v = json.load(open(sys.argv[1])).get("verdict")
    print(v if isinstance(v, str) and v else "unreadable")
except Exception: print("unreadable")' "$TRACE/gates.json" 2>/dev/null || echo "unreadable")"
  [ -n "$GATE_VERDICT" ] || GATE_VERDICT="unreadable"
fi
if [ -n "$LANE_BREACH" ]; then
  echo "✗ lane integrity: $LANE_BREACH — this run did not measure the model it committed to" >&2
  RC=5
elif [ ! -f "$TRACE/candidate.json" ] || [ "${QWEN_RC:-2}" -ne 0 ]; then
  RC=2
elif [ ! -s "$TRACE/work/report.md" ]; then
  echo "✗ run exited 0 but produced no work/report.md — not a success" >&2
  RC=3
elif [ -n "$GATE_VERDICT" ] && [ "$GATE_VERDICT" != "clean" ]; then
  echo "✗ report delivered but its own gates say verdict=$GATE_VERDICT — not a success" >&2
  RC=4
else
  RC=0
fi
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
    --attempt "$RESUME_ATTEMPTS" --exit-code "$RC" ${LANE_REASON_ARG[@]+"${LANE_REASON_ARG[@]}"} \
    --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  state_event RUN_FAILED --run-tag "$STAMP" --phase RUN_FAILED --dataset "$DATASET" --arm "$ARM" --trace-dir "$TRACE" \
    --attempt "$RESUME_ATTEMPTS" --exit-code "$RC" ${LANE_REASON_ARG[@]+"${LANE_REASON_ARG[@]}"} \
    --upstream-log "$TRACE.upstream.jsonl" \
    --inflight-path "$TRACE/upstream-inflight.json"
  TERMINAL_WRITTEN=1
fi
# the CLI's own stderr is the only clue when the run produced nothing
[ "$RC" -ne 0 ] && [ -f "$TRACE/err.txt" ] && sed -n '1,8p' "$TRACE/err.txt"
exit "$RC"
