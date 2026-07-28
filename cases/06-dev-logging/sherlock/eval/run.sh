#!/usr/bin/env bash
# Sherlock eval runner — one measurement, appended to the run ledger.
#
#   eval/run.sh <dataset-dir> <skill-version|none> [label]
#
# Examples:
#   eval/run.sh ~/hack/logalyzer-real-world-testset/real-logs/OpenSSH none      # baseline
#   eval/run.sh ~/hack/logalyzer-real-world-testset/real-logs/OpenSSH v1        # with skill v1
#   eval/run.sh ~/hack/logalyzer-real-world-testset/real-logs/OpenSSH v2        # with skill v2
#
# Every run appends one JSON line to eval/runs.jsonl with:
#   dataset, arm, model, turns, duration_s, input/output tokens, answer chars,
#   distinct files cited, and the full answer text.
#
# Env (all have defaults; the secret is supplied by the caller via with-secret.sh):
#   SHERLOCK_BASE_URL   default https://linkapi.ai/v1
#   SHERLOCK_MODEL      default [SP]deepseek-v4-flash
#   SHERLOCK_API_KEY    required
#   SHERLOCK_TIMEOUT    default 900

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
LEDGER="$HERE/eval/runs.jsonl"

DATASET="${1:?usage: run.sh <dataset-dir> <skill-version|none> [label]}"
ARM="${2:?usage: run.sh <dataset-dir> <skill-version|none> [label]}"
LABEL="${3:-$ARM}"

BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-900}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY (use with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ...)}"

[ -x "$QWEN" ]   || { echo "✗ qwen not found at $QWEN" >&2; exit 1; }
[ -d "$DATASET" ] || { echo "✗ dataset not found: $DATASET" >&2; exit 1; }

DS_NAME="$(basename "$DATASET")"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sherlock-eval-XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

# --- isolate qwen completely: no user-level config leaks into a measurement ---
export QWEN_HOME="$RUN_DIR/qwen-home"
mkdir -p "$QWEN_HOME"

# --approval-mode yolo is REQUIRED and applies to BOTH arms. Without it, headless
# `-p` mode denies run_shell_command ("Matching deny rule: run_shell_command"), so
# the model cannot run grep/sed/zcat/wc — the very tools SKILL.md's procedure is
# built on — and every .gz dataset is unreadable for structural reasons that have
# nothing to do with the skill. Verified 2026-07-28 against the real CLI:
# with the flag, `zcat pg_vacuums.log.gz | wc -l` returns 32405; without it, refused.
#
# The API key and base URL are passed via the ENVIRONMENT, never on argv: argv is
# world-readable in `ps` for the whole run, and this box has a provisioned guest
# account. Verified: qwen honours OPENAI_API_KEY / OPENAI_BASE_URL.
ARGS=(--auth-type openai --model "$MODEL" --approval-mode yolo)
export QWEN_CODE_SUPPRESS_YOLO_WARNING=1

if [ "$ARM" = "none" ]; then
  # BASELINE: no skill, no context files, no hooks, no MCP. Bare model + its own tools.
  # NOTE: --safe-mode was here until 2026-07-28 and made the A/B invalid — in
  # qwen 0.21 it also blocks file reads outside the workspace, so every baseline
  # run answered "нет доступа к каталогу" instead of analysing anything. That
  # measured the sandbox, not the absence of the skill. Isolation is already
  # provided by QWEN_HOME above; both arms must get identical tool permissions.
  :
else
  SRC="$HERE/skills/$ARM"
  [ -f "$SRC/SKILL.md" ] || { echo "✗ no skill at $SRC/SKILL.md" >&2; exit 1; }
  mkdir -p "$RUN_DIR/.qwen/skills"
  cp -r "$SRC" "$RUN_DIR/.qwen/skills/sherlock"
fi

# Identical prompt for every arm — the ONLY variable is whether the skill is present.
PROMPT="Проанализируй логи в каталоге $DATASET. Найди все проблемы и инциденты,
определи корневую причину каждой и предложи, что делать. Ссылайся на конкретные
строки в формате файл:строка."

echo "▶ $DS_NAME / arm=$LABEL / $MODEL"
START=$(date +%s)
( cd "$RUN_DIR" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    timeout "$TIMEOUT" "$QWEN" "${ARGS[@]}" \
    -p "$PROMPT" --output-format json </dev/null ) >"$RUN_DIR/out.json" 2>"$RUN_DIR/err.txt"
RC=$?
ELAPSED=$(( $(date +%s) - START ))

if [ $RC -eq 124 ]; then echo "  ✗ timeout after ${TIMEOUT}s"; fi
[ -s "$RUN_DIR/out.json" ] || { echo "  ✗ no output"; sed -n '1,10p' "$RUN_DIR/err.txt"; exit 1; }

python3 - "$RUN_DIR/out.json" "$DS_NAME" "$LABEL" "$ARM" "$ELAPSED" "$DATASET" "$LEDGER" <<'PY'
import json, re, sys, os
out, ds, label, arm, elapsed, dataset, ledger = sys.argv[1:8]
d = json.load(open(out)); d = d if isinstance(d, list) else [d]
final = next((r for r in d if r.get("type") == "result"), None)
sysrec = next((r for r in d if r.get("type") == "system"), {})
if final is None:
    print("  ✗ no final result record"); sys.exit(1)
if final.get("is_error"):
    e = final.get("error") or {}
    print("  ✗ error:", e.get("message", e)); sys.exit(1)

text = final.get("result") or ""
u = final.get("usage") or {}

# HARD GUARD: a failed run is NOT a measurement.
# qwen reports some provider failures as a successful record whose "result" is the
# error text, e.g. "[API Error: 400 Error from provider (Console Go): Upstream
# request failed ...]" or a context-window abort. Two such rows landed in the
# ledger on 2026-07-28 and polluted the aggregates. Refuse to record them; the
# caller re-runs the cell instead.
if re.match(r"^\s*\[API Error", text) or ("[API Error" in text and len(text) < 2000):
    print("  ✗ RUN FAILED — provider/runtime error, NOT recorded: %s" % text[:200].replace("\n", " "))
    sys.exit(1)

# distinct source files actually cited — a coverage proxy, the dominant recall term
corpus = {f for f in os.listdir(dataset)} if os.path.isdir(dataset) else set()
cited = {f for f in corpus if f in text}
lineref = len(re.findall(r":\d+", text))

rec = {
    "dataset": ds, "arm": arm, "label": label,
    "model": sysrec.get("model"),
    "turns": final.get("num_turns"),
    "duration_s": int(elapsed),
    "api_s": round((final.get("duration_api_ms") or 0) / 1000, 1),
    "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
    "answer_chars": len(text),
    "files_in_corpus": len(corpus), "files_cited": len(cited),
    "line_refs": lineref,
    "answer": text,
}
with open(ledger, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print("  ✓ turns=%s  %ss  in/out=%s/%s  chars=%d  files_cited=%d/%d  line_refs=%d"
      % (rec["turns"], elapsed, rec["input_tokens"], rec["output_tokens"],
         rec["answer_chars"], rec["files_cited"], rec["files_in_corpus"], lineref))
PY
