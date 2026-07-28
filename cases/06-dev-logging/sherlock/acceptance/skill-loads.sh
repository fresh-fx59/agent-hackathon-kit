#!/usr/bin/env bash
# Canary: prove SKILL.md actually reaches the model in headless `qwen -p`.
#
# Exists because an agent reported "qwen -p denies the skill tool, so SKILL.md
# never loads", which would have invalidated every A/B number we have. It does
# load: skills are injected as context when their description matches, which is
# independent of any explicit skill-invocation tool being available.
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
( cd "$W" && QWEN_HOME="$W/home" OPENAI_API_KEY="$SHERLOCK_API_KEY" \
  OPENAI_BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}" \
  timeout "${SHERLOCK_TIMEOUT:-300}" "$QWEN" --auth-type openai \
    --model "${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}" --approval-mode yolo \
    -p "Посмотри лог $W/test.log — что случилось?" --output-format json </dev/null \
) > "$W/out.json" 2>"$W/err.txt"
python3 - "$W/out.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d = d if isinstance(d, list) else [d]
f = next((r for r in d if r.get("type") == "result"), None)
t = (f or {}).get("result") or ""
if "CANARY-TOKEN-7Q2X-LOADED" in t:
    print("\033[32m✓ SKILL.md loads in headless -p — A/B measurements are valid\033[0m"); sys.exit(0)
print("\033[31m✗ canary ABSENT — SKILL.md did not reach the model; every A/B number is void\033[0m")
print(t[-300:]); sys.exit(1)
PY
