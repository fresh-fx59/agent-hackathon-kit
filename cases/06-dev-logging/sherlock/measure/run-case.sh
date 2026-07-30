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

# Validate the arm BEFORE creating the run dir: a missing skill must fail with no
# trace, not leave behind an empty orphan run dir.
if [ "$ARM" != "none" ]; then
  [ -f "$SKILLS/$ARM/SKILL.md" ] || { echo "✗ no skill at $SKILLS/$ARM/SKILL.md" >&2; exit 1; }
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$RUNS/$STAMP-$CASE_ID-$ARM"
mkdir -p "$RUN_DIR" || { echo "✗ cannot create $RUN_DIR" >&2; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/runcase-XXXXXX")"
trap 'rm -rf "$W"' EXIT        # the SCRATCH dir goes; the RUN dir stays.
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

if [ "$ARM" != "none" ]; then
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $CASE_DIR.
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
if final is None:
    print("  ✗ no final result record — NOT recorded"); sys.exit(2)

text = final.get("result") or ""
if not text.strip():
    print("  ✗ empty result — NOT recorded"); sys.exit(2)

# Same guard as run-bench.sh: qwen reports some provider failures as a SUCCESSFUL
# record whose result is the error text. Two such rows polluted a ledger on 2026-07-28.
if text.lstrip().startswith("[API Error") or ("[API Error" in text and len(text) < 400):
    print("  ✗ provider/run error, NOT recorded: %s" % text[:160].replace("\n", " "))
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
