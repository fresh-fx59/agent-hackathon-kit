#!/usr/bin/env bash
# run-case.sh — run one arm against one case and KEEP the evidence.
#
#   run-case.sh <case-dir> <arm>
#
# The one thing this does that run-bench.sh did not: `--output-format stream-json`
# instead of `json`, teed to a run directory that is NEVER deleted. Every previous
# measurement discarded the step-by-step record, which is why we could report a
# score but never a cause.
set -uo pipefail

CASE_DIR="${1:?usage: run-case.sh <case-dir> <arm>}"
ARM="${2:?usage: run-case.sh <case-dir> <arm>}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
SKILLS="${SHERLOCK_SKILLS:-$(cd "$HERE/.." && pwd)/skills}"
RUNS="${SHERLOCK_RUNS:-$HERE/runs}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-2700}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY (use with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ...)}"

[ -d "$CASE_DIR" ] || { echo "✗ no such case: $CASE_DIR" >&2; exit 1; }
# Canonicalize BEFORE it goes into the prompt: qwen runs after `cd "$W"` into the
# scratch dir, so a relative CASE_DIR would describe a path relative to the wrong
# cwd. The model then answers plausibly about nothing, the guard below never fires
# (it's a normal success record), and a wrong-but-recorded measurement lands in the
# ledger — the exact failure class the guard exists to prevent, by a route it can't see.
CASE_DIR="$(cd "$CASE_DIR" && pwd)"
CASE_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["case_id"])' \
  "$CASE_DIR/case.json")" || { echo "✗ unreadable case.json" >&2; exit 1; }

# CRITICAL-1. The model is pointed at the CORPUS, never at the case root: case.json
# holds the title, the root cause and every proof_location. In the captured run
# 20260730T195412Z the model's 12th record was a read_file on case.json and its 13th
# returned the root cause — before it had opened a single log line. Every number that
# layout produced measured "can the model read a JSON file".
PROMPT_DIR="$CASE_DIR/corpus"
[ -d "$PROMPT_DIR" ] || { echo "✗ no corpus dir: $PROMPT_DIR (rebuild with slice.py/micro.py)" >&2; exit 1; }
# The guard, so this can never silently regress: whatever directory we are about to
# name in the prompt must not contain the answer. Checked on the resolved path, not
# on the variable, so a symlinked or re-pointed PROMPT_DIR is caught too.
PROMPT_DIR="$(cd "$PROMPT_DIR" && pwd)"
if [ -e "$PROMPT_DIR/case.json" ]; then
  echo "✗ REFUSING: $PROMPT_DIR contains case.json — the prompt directory must never" >&2
  echo "  hold the answer key (title/root_cause/proof_locations). See CRITICAL-1." >&2
  exit 1
fi

# Validate the arm BEFORE creating the run dir: a missing skill must fail with no
# trace, not leave behind an empty orphan run dir.
if [ "$ARM" != "none" ]; then
  [ -f "$SKILLS/$ARM/SKILL.md" ] || { echo "✗ no skill at $SKILLS/$ARM/SKILL.md" >&2; exit 1; }
fi

# Second-resolution alone is not unique enough: two runs started in the same second
# (a tier-2 loop over fast/refused cases) would share a run dir and the second would
# overwrite the first — in a rig whose one promise is that the run dir is NEVER
# deleted. A short random suffix makes each capture its own directory.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
RUN_DIR="$RUNS/$STAMP-$CASE_ID-$ARM"
mkdir -p "$RUN_DIR" || { echo "✗ cannot create $RUN_DIR" >&2; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/runcase-XXXXXX")"
trap 'rm -rf "$W"' EXIT        # the SCRATCH dir goes; the RUN dir stays.
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

if [ "$ARM" != "none" ]; then
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $PROMPT_DIR.
Найди ВСЕ проблемы и инциденты, определи корневую причину каждой и предложи,
что делать. Ссылайся на конкретные строки в формате файл:строка."

START=$(date +%s)
# The key travels by ENVIRONMENT, never argv: /proc/<pid>/cmdline is world-readable.
( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    QWEN_CODE_SUPPRESS_YOLO_WARNING=1 \
    timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$MODEL" \
      --approval-mode yolo -p "$PROMPT" --output-format stream-json </dev/null \
) > "$RUN_DIR/stream.jsonl" 2> "$RUN_DIR/stderr.txt"
RC=$?
ELAPSED=$(( $(date +%s) - START ))

python3 - "$RUN_DIR" "$CASE_ID" "$ARM" "$ELAPSED" "$RC" "$MODEL" <<'PY'
import json, sys, os
run_dir, case_id, arm, elapsed, rc, model = sys.argv[1:7]
final = None
for line in open(os.path.join(run_dir, "stream.jsonl"), encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("type") == "result":
        final = r
# CRITICAL-4. A non-zero exit from qwen — including timeout's 124 — means the run did
# not complete. Whatever partial text landed in the stream is not a measurement. This
# was captured into meta.json as `exit_code` and then ignored, so a timed-out run was
# recorded as a normal row.
if int(rc) != 0:
    print("  ✗ runner exited %s (timeout/crash), NOT recorded" % rc); sys.exit(4)

if final is None:
    print("  ✗ no final result record — NOT recorded"); sys.exit(2)

text = final.get("result") or ""
if not text.strip():
    print("  ✗ empty result — NOT recorded"); sys.exit(2)

# CRITICAL-4. Same guard as run-bench.sh:61, whose FIRST check is is_error — dropped
# here, so a record with is_error true, >=400 chars and no leading "[API Error" was
# refused there and recorded here. The provider's own flag outranks any text
# heuristic: text matching is the fallback for providers that report a failure as a
# successful record (two such rows polluted a ledger on 2026-07-28), not the primary.
if final.get("is_error") or text.lstrip().startswith("[API Error") \
        or ("[API Error" in text and len(text) < 400):
    print("  ✗ provider/run error, NOT recorded: %s"
          % str(final.get("error") or text)[:160].replace("\n", " "))
    sys.exit(3)

with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as fh:
    fh.write(text)
u = final.get("usage") or {}
meta = {"case_id": case_id, "arm": arm, "model": model,
        "started_at": os.path.basename(run_dir).split("-")[0],
        "duration_s": int(elapsed), "exit_code": int(rc),
        "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
        "answer_chars": len(text), "turns": final.get("num_turns")}
with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
    json.dump(meta, fh, ensure_ascii=False, indent=2)
print("  ✓ %s/%s  %ss  chars=%d  -> %s" % (case_id, arm, elapsed, len(text), run_dir))
PY
