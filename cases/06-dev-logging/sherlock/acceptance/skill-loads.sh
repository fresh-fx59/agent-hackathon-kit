#!/usr/bin/env bash
# Canary: does SKILL.md actually reach the model in headless `qwen -p`?
#
# Runs BOTH configurations, because a guard that only exercises the passing case
# cannot catch the regression it exists for — which is exactly the mistake that
# made this file necessary. Expected outcome:
#   with    --approval-mode yolo -> canary present
#   without --approval-mode yolo -> canary ABSENT (qwen declines the "skill"
#                                   permission, so SKILL.md never loads)
# Any A/B measured without the flag therefore compares two skill-less arms, and
# whatever difference it shows is variance.
#
#   with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ./acceptance/skill-loads.sh
set -uo pipefail
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY}"
W="$(mktemp -d "${TMPDIR:-/tmp}/canary-XXXXXX")"; trap 'rm -rf "$W"' EXIT

mkdir -p "$W/home/skills/logcanary"
cat > "$W/home/skills/logcanary/SKILL.md" <<'EOF'
---
name: logcanary
description: Используй этот навык всегда, когда речь идёт о логах, ошибках сервиса, инцидентах, падениях, или когда просят посмотреть лог-файл. Анализ логов и диагностика.
---
# Анализ логов

При анализе логов ВСЕГДА заверши свой ответ ровно этой строкой на отдельной строке:
CANARY-TOKEN-7Q2X-LOADED
EOF
printf 'Jul 28 10:00:01 host app[1]: ERROR db timeout\nJul 28 10:00:02 host app[1]: ERROR retry failed\n' > "$W/test.log"

cat > "$W/check.py" <<'EOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    d = d if isinstance(d, list) else [d]
    t = "".join(str(r.get("result") or "") for r in d)
except Exception:
    t = ""
print("yes" if "CANARY-TOKEN-7Q2X-LOADED" in t else "no")
EOF

run_canary() {                     # $1 = yolo | noyolo  -> echoes yes/no
  local extra=()
  [ "$1" = "yolo" ] && extra=(--approval-mode yolo)
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
  ( cd "$W" && QWEN_HOME="$W/home" OPENAI_API_KEY="$SHERLOCK_API_KEY" \
    OPENAI_BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}" \
    timeout "${SHERLOCK_TIMEOUT:-300}" "$QWEN" --auth-type openai \
      --model "${SHERLOCK_MODEL:-[次]deepseek-v4-flash}" ${extra[@]+"${extra[@]}"} \
      -p "Посмотри лог $W/test.log — что случилось?" --output-format json </dev/null \
  ) > "$W/out-$1.json" 2>"$W/err-$1.txt"
  python3 "$W/check.py" "$W/out-$1.json"
}

WITH=$(run_canary yolo)
WITHOUT=$(run_canary noyolo)
echo "  with    --approval-mode yolo : canary=$WITH  (expected yes)"
echo "  without --approval-mode yolo : canary=$WITHOUT  (expected no)"

if [ "$WITH" = "yes" ] && [ "$WITHOUT" = "no" ]; then
  printf '\033[32m✓ confirmed: SKILL.md loads ONLY with --approval-mode yolo.\n'
  printf '  Any A/B measured without the flag compares two skill-less arms.\033[0m\n'
  exit 0
fi
if [ "$WITH" != "yes" ]; then
  printf '\033[31m✗ canary absent even WITH yolo — SKILL.md is not reaching the model at all;\n'
  printf '  every A/B number is void until this is fixed.\033[0m\n'
  exit 1
fi
printf '\033[33m! canary present WITHOUT yolo too — the permission gate changed in this qwen\n'
printf '  version. Good news, but re-read the docs before trusting old conclusions.\033[0m\n'
exit 0
