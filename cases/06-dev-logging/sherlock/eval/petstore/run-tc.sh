#!/usr/bin/env bash
# Run one of the organizers' own test cases against the organizers' own input pack.
#
#   run-tc.sh <tc01|tc03|tc05> <none|v1|v2|v3|v4> [label]
#
# Why this exists, separately from eval/run.sh and eval/bench/run-bench.sh:
#   * A2.1 in the pack's acceptance_criteria.md sets the ≥50 % bar explicitly
#     "на pet-project" — i.e. on THIS pack, not on a corpus we built ourselves.
#     Until 2026-07-29 we had never run against it, so the one number the
#     organizers actually name was unmeasured.
#   * TC-01/TC-03/TC-05 have DIFFERENT questions, so they cannot share the one
#     generic prompt eval/run.sh uses. Each prompt below is a plain restatement
#     of that test case's «Вход», nothing more — no hints about the expected
#     answer, or the test would grade our prompt instead of the skill.
#   * TC-05 is a NEGATIVE test: the correct behaviour is to raise nothing. A
#     runner that only scores "did it find things" would score it backwards.
#
# Env: SHERLOCK_API_KEY (required), SHERLOCK_BASE_URL, SHERLOCK_MODEL,
#      SHERLOCK_TIMEOUT, SHERLOCK_KNOWLEDGE (optional dir of pattern cards to
#      pre-seed, used to measure the self-learning loop on TC-03).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$(cd "$HERE/../.." && pwd)/skills"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
LEDGER="$HERE/runs-petstore.jsonl"
PACK="${SHERLOCK_PACK:-$HOME/hack/petstore-pack/petstore_input_pack}"

TC="${1:?usage: run-tc.sh <tc01|tc03|tc05> <arm> [label]}"
ARM="${2:?usage: run-tc.sh <tc01|tc03|tc05> <arm> [label]}"
LABEL="${3:-$ARM}"
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
TIMEOUT="${SHERLOCK_TIMEOUT:-900}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY (use with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ...)}"

[ -d "$PACK" ] || { echo "✗ pack not found: $PACK" >&2; exit 1; }
[ -x "$QWEN" ] || { echo "✗ qwen not found at $QWEN" >&2; exit 1; }

case "$TC" in
  tc01) PROMPT="Разберись, что произошло с заказом по correlation_id = c-8f3a2b91-4d7c-11ee-b962-0242ac120002.
Логи лежат в $PACK/logs, исходный код сервисов — в $PACK/repo, архитектура и SDD — в $PACK/docs.
Определи корневую причину, укажи конкретный файл и метод в repo/, и предложи исправление.
Ссылайся на конкретные строки в формате файл:строка." ;;
  tc03) PROMPT="Разберись с инцидентом в notification-service: $PACK/logs/second_incident_notification.log.
Остальные логи лежат в $PACK/logs, исходный код сервисов — в $PACK/repo, архитектура и SDD — в $PACK/docs.
Определи корневую причину и предложи исправление.
Ссылайся на конкретные строки в формате файл:строка." ;;
  tc05) PROMPT="Проверь заказ ord-c88d1e2f по логам в $PACK/logs.
Исходный код сервисов — в $PACK/repo, архитектура и SDD — в $PACK/docs.
Что с ним произошло? Есть ли основания заводить инцидент или дефект?
Ссылайся на конкретные строки в формате файл:строка." ;;
  *) echo "✗ unknown test case: $TC (want tc01|tc03|tc05)" >&2; exit 1 ;;
esac

W="$(mktemp -d "${TMPDIR:-/tmp}/petstore-XXXXXX")"
trap 'rm -rf "$W"' EXIT
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"
export QWEN_CODE_SUPPRESS_YOLO_WARNING=1

# --approval-mode yolo is REQUIRED for BOTH arms: without it qwen declines the
# "skill" permission AND run_shell_command, so both arms would run skill-less and
# any measured difference would be variance. See acceptance/skill-loads.sh.
if [ "$ARM" != "none" ]; then
  [ -f "$SKILLS/$ARM/SKILL.md" ] || { echo "✗ no skill at $SKILLS/$ARM/SKILL.md" >&2; exit 1; }
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
  # optional: pre-seed confirmed pattern cards to measure knowledge reuse
  if [ -n "${SHERLOCK_KNOWLEDGE:-}" ] && [ -d "$SHERLOCK_KNOWLEDGE" ]; then
    mkdir -p "$W/.qwen/skills/log-rca/knowledge/patterns"
    cp "$SHERLOCK_KNOWLEDGE"/*.md "$W/.qwen/skills/log-rca/knowledge/patterns/" 2>/dev/null
    LABEL="$LABEL+kb"
  fi
fi

echo "▶ petstore $TC / arm=$LABEL / $MODEL"
START=$(date +%s)
# key via environment, never argv (argv is world-readable in ps; guest account exists)
( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
  timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$MODEL" \
    --approval-mode yolo -p "$PROMPT" --output-format json </dev/null \
) >"$W/out.json" 2>"$W/err.txt"
ELAPSED=$(( $(date +%s) - START ))
[ -s "$W/out.json" ] || { echo "  ✗ no output"; sed -n '1,8p' "$W/err.txt"; exit 1; }

python3 - "$W/out.json" "$TC" "$ARM" "$LABEL" "$ELAPSED" "$PACK" "$LEDGER" <<'PY'
import json, os, re, sys
out, tc, arm, label, elapsed, pack, ledger = sys.argv[1:8]
d = json.load(open(out)); d = d if isinstance(d, list) else [d]
final = next((r for r in d if r.get("type") == "result"), None)
sysr  = next((r for r in d if r.get("type") == "system"), {})
if final is None:
    print("  ✗ no final result record"); sys.exit(1)
t = final.get("result") or ""
# A failed run is NOT a measurement — refuse to record provider errors as data.
if final.get("is_error") or t.lstrip().startswith("[API Error") or ("[API Error" in t and len(t) < 400):
    print("  ✗ provider/run error, NOT recorded:", (final.get("error") or t)[:160]); sys.exit(2)

names = []
for root, _, fs in os.walk(pack):
    names.extend(fs)
cited = {f for f in set(names) if f in t}
u = final.get("usage") or {}
rec = {"tc": tc, "arm": arm, "label": label, "model": sysr.get("model"),
       "turns": final.get("num_turns"), "duration_s": int(elapsed),
       "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
       "answer_chars": len(t), "files_in_pack": len(set(names)),
       "files_cited": len(cited), "line_refs": len(re.findall(r":\d+", t)),
       "answer": t}
with open(ledger, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("  ✓ turns=%s %ss in/out=%s/%s chars=%d files_cited=%d line_refs=%d"
      % (rec["turns"], elapsed, rec["input_tokens"], rec["output_tokens"],
         rec["answer_chars"], rec["files_cited"], rec["line_refs"]))
PY
