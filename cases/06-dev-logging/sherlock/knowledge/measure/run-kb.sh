#!/usr/bin/env bash
# Measure the self-improvement loop: does a confirmed pattern card make the NEXT
# incident faster, at equal quality?
#
#   knowledge/measure/run-kb.sh <dataset-dir> <cold|warm> [rep]
#
# Both arms get the IDENTICAL skill text — skills/v1/SKILL.md with
# knowledge/SKILL-SECTION.md appended — and the identical prompt. The ONLY
# variable is whether knowledge/patterns/ contains confirmed cards:
#
#   cold : knowledge/ present, patterns/ EMPTY      (incident #1 — nothing learned yet)
#   warm : knowledge/ present, patterns/ SEEDED     (incident #2 — cards confirmed)
#
# That isolates the cards. Adding the section text to both arms is deliberate:
# otherwise we would be measuring "more prose in the prompt", not "reused knowledge".
#
# Base skill is v2 by default (SHERLOCK_BASE_SKILL overrides).
# Deliberately does NOT touch eval/run.sh, eval/batch.sh, eval/report.py or
# skills/* — another session owns those. It only reads skills/v1.
#
# Env: SHERLOCK_API_KEY (required), SHERLOCK_BASE_URL, SHERLOCK_MODEL, SHERLOCK_TIMEOUT.

set -uo pipefail

KNOW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # …/sherlock/knowledge
ROOT="$(cd "$KNOW/.." && pwd)"                              # …/sherlock
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
LEDGER="$KNOW/measure/runs.jsonl"

DATASET="${1:?usage: run-kb.sh <dataset-dir> <cold|warm> [rep]}"
ARM="${2:?usage: run-kb.sh <dataset-dir> <cold|warm> [rep]}"
REP="${3:-1}"

BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-900}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY via with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- …}"

[ -x "$QWEN" ]     || { echo "✗ qwen not found at $QWEN" >&2; exit 1; }
[ -d "$DATASET" ]  || { echo "✗ dataset not found: $DATASET" >&2; exit 1; }
case "$ARM" in cold|warm) ;; *) echo "✗ arm must be cold|warm" >&2; exit 2;; esac

DS_NAME="$(basename "$DATASET")"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sherlock-kb-XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

export QWEN_HOME="$RUN_DIR/qwen-home"
mkdir -p "$QWEN_HOME"

# MEASURED 2026-07-28: in `-p` mode the `skill` tool asks for confirmation, cannot
# prompt, and is DENIED — so SKILL.md is never loaded and the run silently
# degrades to a bare model. Two runs proved it (raw/Linux-warm-rep0.json:
# skill → declined, then eleven read_file calls straight down the corpus).
# `tools.approvalMode: yolo` is what an interactive engineer's session behaves
# like, and it is applied IDENTICALLY to both arms, so the A/B stays clean.
printf '{ "tools": { "approvalMode": "yolo" } }\n' >"$QWEN_HOME/settings.json"

# ---- build the skill: v1 + the self-improvement section, identical in both arms ----
SKILL_DIR="$RUN_DIR/.qwen/skills/log-rca"
mkdir -p "$SKILL_DIR/knowledge/patterns"
BASE_SKILL="${SHERLOCK_BASE_SKILL:-v4}"     # follow whatever version currently ships
python3 "$KNOW/measure/merge-skill.py" \
  "$ROOT/skills/$BASE_SKILL/SKILL.md" "$KNOW/SKILL-SECTION.md" >"$SKILL_DIR/SKILL.md"
[ -s "$SKILL_DIR/SKILL.md" ] || { echo "✗ merge produced an empty skill" >&2; exit 1; }

cp "$KNOW/README.md"   "$SKILL_DIR/knowledge/" 2>/dev/null
cp "$KNOW/patterns.py" "$SKILL_DIR/knowledge/" 2>/dev/null

# MEASURED 2026-07-28: whether the model calls the `skill` tool at all is a coin
# flip in `-p` mode (it did in raw/Linux-warm-rep0, did not in OpenSSH-cold-rep1).
# That nondeterminism is far larger than the effect being measured, so the
# procedure is ALSO pinned into a QWEN.md context file, which every qwen version
# loads unconditionally. This is the contingency the design spec already names
# (§8, open question 4). Identical in both arms — it changes how the procedure
# reaches the model, never what the model knows about the incident.
cp "$SKILL_DIR/SKILL.md" "$RUN_DIR/QWEN.md"

if [ "$ARM" = "warm" ]; then
  cp "$KNOW"/patterns/*.md "$SKILL_DIR/knowledge/patterns/"
  cp "$KNOW/REJECTED.md"   "$SKILL_DIR/knowledge/REJECTED.md"
else
  # incident #1: nothing has been learned yet. Structure exists, base is empty.
  printf '# Отклонённые карточки\n\n| id | fingerprint | дата | кто | почему отклонено |\n|---|---|---|---|---|\n' \
    >"$SKILL_DIR/knowledge/REJECTED.md"
fi
CARDS=$(ls -1 "$SKILL_DIR/knowledge/patterns" 2>/dev/null | wc -l)

# Identical to eval/run.sh's prompt — comparability with the existing ledger.
PROMPT="Проанализируй логи в каталоге $DATASET. Найди все проблемы и инциденты,
определи корневую причину каждой и предложи, что делать. Ссылайся на конкретные
строки в формате файл:строка."

echo "▶ $DS_NAME / arm=$ARM / rep=$REP / cards=$CARDS / $MODEL"
START=$(date +%s)
( cd "$RUN_DIR" && timeout "$TIMEOUT" "$QWEN" \
    --auth-type openai --model "$MODEL" \
    --openai-base-url "$BASE_URL" --openai-api-key "$SHERLOCK_API_KEY" \
    -p "$PROMPT" --output-format json </dev/null ) >"$RUN_DIR/out.json" 2>"$RUN_DIR/err.txt"
RC=$?
ELAPSED=$(( $(date +%s) - START ))
[ $RC -eq 124 ] && echo "  ✗ timeout after ${TIMEOUT}s"
[ -s "$RUN_DIR/out.json" ] || { echo "  ✗ no output"; sed -n '1,10p' "$RUN_DIR/err.txt"; exit 1; }

# keep the raw transcript: the tool-call trace is the only evidence of whether
# the agent actually opened the knowledge base, and RUN_DIR is about to vanish.
mkdir -p "$KNOW/measure/raw"
cp "$RUN_DIR/out.json" "$KNOW/measure/raw/$DS_NAME-$ARM-rep$REP.json"

python3 "$KNOW/measure/record.py" \
  "$RUN_DIR/out.json" "$DS_NAME" "$ARM" "$REP" "$ELAPSED" "$DATASET" "$CARDS" "$LEDGER"
