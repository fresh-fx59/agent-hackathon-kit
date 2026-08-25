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
elif [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; then
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
if [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; then
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
MAX_OUT="${SHERLOCK_MAX_OUTPUT_TOKENS:-32768}"
case "$MAX_OUT" in *[!0-9]*|'') echo "✗ invalid SHERLOCK_MAX_OUTPUT_TOKENS" >&2; exit 1 ;; esac
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
if [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; then
  case "$REQUEST_TIMEOUT_MS:$MAX_RETRIES" in
    *[!0-9:]*|:*|*:) echo "✗ invalid v30 request timeout or retry count" >&2; exit 1 ;;
  esac
  mkdir -p "$W/.qwen"
  MEMORY_JSON=''
  if [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; then
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
if { [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; } && [ -n "${SHERLOCK_SEED_WORK:-}" ]; then
  PROMPT="$PROMPT

Продолжи расследование из сохранённого checkpoint в $W/work. Сначала прочитай
work/checkpoint.json. Не повторяй MAP и TRIAGE, если state=ready_for_synthesis.
Используй новый безопасный путь корпуса $RUN_CORPUS и work/path-map.tsv.
Сразу собери work/report.md, затем выполни triagecheck и citecheck и исправь
только ошибки проверки. Последний ответ должен дословно повторять work/report.md."
fi

if [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; then
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
python3 - "$TRACE/run-inputs.json" "$CORPUS_SOURCE" "$RUN_CORPUS" "$PROMPT_SHA" "$ARM_COMMIT" "$ARM" <<'PY'
import json, sys
target, corpus_source, staged_root, prompt_sha, arm_commit, arm = sys.argv[1:]
with open(target, "w", encoding="utf-8") as fh:
    json.dump({"schema": 1, "arm": arm,
               "corpus_source": corpus_source, "staged_root": staged_root,
               "prompt_sha256": prompt_sha, "prompt_file": "prompt-sent.txt",
               "arm_commit": arm_commit}, fh, ensure_ascii=False, sort_keys=True)
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
if [ "$ARM" = "v30" ] || [ "$ARM" = "v31" ] || [ "$ARM" = "v32" ] || [ "$ARM" = "v33" ] || [ "$ARM" = "v34" ] || [ "$ARM" = "v35" ] || [ "$ARM" = "v36" ] || [ "$ARM" = "v37" ]; then
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
  python3 - "$W/out.json" "$TRACE/work/report.md" "$W/.resume-reason" <<'PY'
import json, os, re, sys
report_path, reason_path = sys.argv[2], sys.argv[3]

def note(reason):
    try:
        with open(reason_path, "w", encoding="utf-8") as fh: fh.write(reason + "\n")
    except OSError:
        pass

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
        note("broken_stream")
        print(match.group(1))
        sys.exit(0)
    sys.exit(1)
if not isinstance(rows, list):
    rows = [rows]
final = next((r for r in rows if isinstance(r, dict) and r.get("type") == "result"), {})
text = final.get("result") or ""
broken = (not final or final.get("is_error") or text.lstrip().startswith("[API Error")
          or ("[API Error" in text and len(text) < 400))
# A CLEAN STOP THAT DELIVERED NOTHING IS ALSO A REASON TO CONTINUE. Judge it on
# the artifact and on whether the model actually did anything, never on the
# prose — a plan announced in the final message reads exactly like a report.
stats = final.get("stats") if isinstance(final.get("stats"), dict) else {}
tools = stats.get("tools") if isinstance(stats.get("tools"), dict) else {}
tool_calls = tools.get("totalCalls")
try:
    has_report = os.path.getsize(report_path) > 0
except OSError:
    has_report = False
# WIDENED after v35 r2. The first cut of this only resumed a stall with ZERO
# tool calls, on the theory that a run which did work and still delivered
# nothing was failing for a reason a retry would not fix. That theory was
# wrong, and it excluded the exact shape of the v34 r1 failure: 12 tool calls,
# every reference file read, and then the report written as chat prose. The
# session on disk holds ALL of that work, so resuming it is strictly cheaper
# and strictly more likely to deliver than starting a fresh paid run from
# nothing — and the attempt count already bounds the downside.
#
# So the rule is the artifact, not the effort: NO REPORT ⇒ RESUMABLE. The two
# shapes stay distinguishable in attempts.jsonl because they need different
# diagnoses — `stalled_no_tool_calls` means the model never started, which
# points at the skill's first-turn instruction; `no_deliverable` means it
# worked and did not write, which points at the write-back step.
stalled = (not broken) and not has_report
if not broken and not stalled:
    sys.exit(1)
if broken:
    note("broken_stream")
else:
    note("stalled_no_tool_calls" if tool_calls == 0 else "no_deliverable")
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
  ATTEMPT_REASON="$(cat "$W/.resume-reason" 2>/dev/null || echo broken_stream)"
  BACKOFF=$((RESUME_BACKOFF_S * (2 ** (RESUME_ATTEMPTS - 1))))
  state_event RECOVERY_DECIDED --run-tag "$STAMP" --phase QWEN_RUNNING --dataset "$DATASET" --arm "$ARM" \
    --trace-dir "$TRACE" --attempt "$RESUME_ATTEMPTS" --session-id "$RESUME_SESSION" --reason "$ATTEMPT_REASON" \
    --upstream-log "$TRACE.upstream.jsonl" --inflight-path "$TRACE/upstream-inflight.json"
  echo "  ⚠ $ATTEMPT_REASON; preserving session $RESUME_SESSION and retrying in ${BACKOFF}s (attempt $RESUME_ATTEMPTS/$RESUME_MAX_ATTEMPTS)" >&2
  sleep "$BACKOFF"
  if run_qwen "$RESUME_ATTEMPTS" --resume "$RESUME_SESSION" -p "The previous attempt ended without delivering the report. Continue the same investigation from saved state. Do not restart mapping and do not describe what you are about to do: call the tools, write the verdicts back, and write work/report.md. Your final message must be the report itself."; then QWEN_RC=0; else QWEN_RC=$?; fi
done
python3 - "$TRACE/recovery.json" "$RESUME_ATTEMPTS" "$RESUME_SESSION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"resume_attempts": int(sys.argv[2]), "session_id": sys.argv[3]}, fh)
    fh.write("\n")
PY
save_trace
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
if [ ! -f "$TRACE/candidate.json" ] || [ "${QWEN_RC:-2}" -ne 0 ]; then
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
