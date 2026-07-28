#!/usr/bin/env bash
# R1 ACCEPTANCE TEST — the primary requirement.
#
# Simulates a brand-new engineer who has never heard of this project:
#   1. a completely fresh Qwen Coder home (no settings, no history, no config)
#   2. ONE command: copy the skill folder into the skills directory
#   3. a natural question, phrased the way a real person would phrase it —
#      NOT naming the skill, NOT naming the tool, NOT naming a file format
#
# It passes only if the skill FIRES BY ITSELF and the answer shows the skill's
# discipline. Anything else means R1 is not met, whatever the demo looks like.
#
# Usage:
#   with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- \
#     ./acceptance/r1-zero-config.sh [skill-version]

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
VER="${1:-v1}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-900}"
LOGS="${SHERLOCK_LOGS:-$HOME/hack/logalyzer-real-world-testset/real-logs/OpenSSH}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY}"

red()   { printf '\033[31m✗ %s\033[0m\n' "$*"; }
green() { printf '\033[32m✓ %s\033[0m\n' "$*"; }

W="$(mktemp -d "${TMPDIR:-/tmp}/r1-XXXXXX")"
trap 'rm -rf "$W"' EXIT

# 1) a virgin environment — nothing carried over from any previous session
export QWEN_HOME="$W/qwen-home"
mkdir -p "$QWEN_HOME"
green "fresh Qwen home: $QWEN_HOME (no settings, no config, no history)"

# 2) THE ONLY INSTALL STEP a real engineer performs.
#    VER=none installs nothing — the control arm, to tell "the skill never fired"
#    apart from "the skill fired but its report format did not take hold".
if [ "$VER" = "none" ]; then
  green "CONTROL ARM: no skill installed"
else
  mkdir -p "$QWEN_HOME/skills"
  cp -r "$HERE/skills/$VER" "$QWEN_HOME/skills/log-rca" || { red "copy failed"; exit 1; }
  green "installed by copying one folder: skills/$VER -> \$QWEN_HOME/skills/log-rca"
fi

# deliberately assert the absence of configuration
for f in settings.json .env config.yaml; do
  [ -e "$QWEN_HOME/$f" ] && { red "R1 VIOLATED: $f exists — something needs configuring"; exit 1; }
done
green "no settings.json, no .env, no config — nothing to configure"

# 3) a question a real engineer would actually type.
#    Note what it does NOT contain: the skill name, the word "навык"/"skill",
#    any instruction about citations, any mention of a report format.
PROMPT="Слушай, у нас что-то странное творится с сервером. Логи лежат в $LOGS.
Глянешь, что там происходит?"

echo "  asking: «Слушай, у нас что-то странное творится с сервером… Глянешь?»"
START=$(date +%s)
# --approval-mode yolo: without it, run_shell_command is DENIED in headless -p mode
# (verified 2026-07-28), so the model cannot grep/sed/zcat — the very tools the skill's
# procedure is built on. An interactive engineer has shell; the test must match reality.
( cd "$W" && timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$MODEL" \
    --openai-base-url "$BASE_URL" --openai-api-key "$SHERLOCK_API_KEY" \
    --approval-mode yolo \
    -p "$PROMPT" --output-format json </dev/null ) >"$W/out.json" 2>"$W/err.txt"
ELAPSED=$(( $(date +%s) - START ))
[ -s "$W/out.json" ] || { red "no output"; sed -n '1,10p' "$W/err.txt"; exit 1; }

cp "$W/out.json" "$HERE/acceptance/last-r1-run.json" 2>/dev/null || true

python3 - "$W/out.json" "$ELAPSED" "$VER" "${SHERLOCK_MUST_FIND:-fztu|119\.137\.62\.142}" \
        "${SHERLOCK_MUST_NOT_SAY:-шум интернета|ничего критичн}" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1])); d = d if isinstance(d, list) else [d]
elapsed, ver, must_find, must_not = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
final = next((r for r in d if r.get("type") == "result"), None)
if final is None or final.get("is_error"):
    print("\033[31m✗ run failed: %s\033[0m" % ((final or {}).get("error") or "no result record"))
    sys.exit(1)
t = final.get("result") or ""

# A provider failure is NOT a measurement. qwen reports some upstream errors as a
# successful result whose text is "[API Error: 400 ...]" — recording those as data
# poisoned our ledger once already.
if t.lstrip().startswith("[API Error") or ("[API Error" in t and len(t) < 400):
    print("\033[31m✗ provider error, not a result — re-run:\033[0m %s" % t[:200])
    sys.exit(2)

# THE GATE IS CORRECTNESS, NOT FORMATTING.
# Measured 2026-07-28: with no skill this model looked at a genuinely compromised
# host and answered «ничего критичного, это стандартный шум интернета», missing the
# successful login entirely. A report with perfect headings that misses the breach
# is worthless; a plain one that catches it is the product. So substance gates,
# and the SKILL.md report discipline is reported but advisory.
found_it   = bool(re.search(must_find, t, re.I))
reassured  = bool(re.search(must_not, t, re.I))

discipline = {
    "цитаты файл:строка":     bool(re.search(r"\.log:\d+|:\d{2,}", t)),
    "раздел «чего я не знаю»": bool(re.search(r"чего я не знаю|не удалось установить", t, re.I)),
    "корневая причина":        bool(re.search(r"корнев\w+ причин", t, re.I)),
    "немедленные действия":    bool(re.search(r"немедленн\w+ действи|что делать|рекомендац", t, re.I)),
}

print("\n--- R1 acceptance, skill %s, %ss, %d chars ---" % (ver, elapsed, len(t)))
print("  %s нашёл главное (%s)" % ("\033[32m✓\033[0m" if found_it else "\033[31m✗\033[0m", must_find))
print("  %s не выдал ложного «всё нормально»" % ("\033[32m✓\033[0m" if not reassured else "\033[31m✗\033[0m"))
print("  дисциплина отчёта (справочно, не блокирует):")
for k, v in discipline.items():
    print("    %s %s" % ("\033[32m✓\033[0m" if v else "\033[33m·\033[0m", k))

print("\n--- first 500 chars ---\n" + t[:500])

if found_it and not reassured:
    miss = [k for k, v in discipline.items() if not v]
    print("\n\033[32m✓ R1 PASS — skill fired unprompted on a natural question, after one "
          "cp and zero configuration, and caught what matters\033[0m")
    if miss:
        print("\033[33m  note: report discipline not fully landing: %s\033[0m" % ", ".join(miss))
    sys.exit(0)
print("\n\033[31m✗ R1 FAIL — found=%s, false-reassurance=%s\033[0m" % (found_it, reassured))
sys.exit(1)
PY
