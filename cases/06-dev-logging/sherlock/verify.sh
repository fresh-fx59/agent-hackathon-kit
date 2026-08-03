#!/usr/bin/env bash
# Sherlock I0 gate: prove the skill actually runs inside the real Qwen Coder CLI.
#
#   ./verify.sh                 # run the gate against the default dataset
#   ./verify.sh <log-dir>       # ...against a specific corpus
#
# Auth (pick one, checked in this order):
#   QWEN_AUTH=oauth   -> uses the interactive login you already did (default)
#   QWEN_AUTH=openai  -> uses SHERLOCK_BASE_URL + SHERLOCK_API_KEY + SHERLOCK_MODEL
#                        (e.g. the local cliproxyapi broker on 127.0.0.1:8317)
#
# Exit 0 = green. Anything else = the gate failed; do NOT start the next increment.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
LOGS="${1:-$HOME/hack/logalyzer-real-world-testset/real-logs/OpenSSH}"
WORK="${SHERLOCK_WORK:-$HERE/.verify-run}"
TIMEOUT="${SHERLOCK_TIMEOUT:-300}"
# Which skill version the gate measures. The default stays v1 ON PURPOSE: this is the
# I0 gate, and silently repointing it at a newer, unmeasured version would change what
# every past green result meant. Gate another arm explicitly: SHERLOCK_VER=v6 ./verify.sh
VER="${SHERLOCK_VER:-v1}"

red()   { printf '\033[31m✗ %s\033[0m\n' "$*"; }
green() { printf '\033[32m✓ %s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }

fail() { red "$*"; exit 1; }

# ---------------------------------------------------------------- preflight
[ -x "$QWEN" ] || fail "qwen CLI not found at $QWEN (set QWEN_BIN, or: npm i -g @qwen-code/qwen-code)"
green "qwen CLI: $("$QWEN" --version 2>&1 | tail -1)"

[ -d "$LOGS" ] || fail "log corpus not found: $LOGS"
NLINES=$(find "$LOGS" -type f -exec cat {} + 2>/dev/null | wc -l)
green "corpus: $LOGS ($(du -sh "$LOGS" 2>/dev/null | cut -f1), $NLINES lines)"

[ -f "$HERE/skills/$VER/SKILL.md" ] || fail "missing $HERE/skills/$VER/SKILL.md"

# ------------------------------------------------- install skill where qwen looks
# Qwen Code discovers skills from <project>/.qwen/skills/<name>/SKILL.md
mkdir -p "$WORK/.qwen/skills"
rm -rf "$WORK/.qwen/skills/log-rca"
cp -r "$HERE/skills/$VER" "$WORK/.qwen/skills/log-rca" || fail "could not install skill"
green "skill installed: $WORK/.qwen/skills/log-rca/SKILL.md"

# --------------------------------------------------------------------- auth
# THE KEY AND THE BASE URL TRAVEL BY ENVIRONMENT, NEVER ON ARGV. argv is world-readable in `ps`
# for the whole run (/proc/<pid>/cmdline is mode 444) and this box has a provisioned `hackmate`
# guest account, so `--openai-api-key "$SHERLOCK_API_KEY"` published the live broker key for the
# default 300 s. eval/run.sh:54-57 already proves qwen honours OPENAI_API_KEY / OPENAI_BASE_URL,
# and tools/fetch-logs.sh enforces exactly this rule for the stand password.
# `export` is a BUILTIN, so the value never becomes any process's argv. Note that `env KEY=val
# cmd` would NOT do — that puts the key on `env`'s own argv — and neither does
# `"${ARRAY[@]}" cmd`: bash recognises a command-prefix assignment syntactically, so an
# expanded one is treated as a command name («OPENAI_API_KEY=…: command not found»).
AUTH_ARGS=()
case "${QWEN_AUTH:-oauth}" in
  openai)
    : "${SHERLOCK_BASE_URL:?set SHERLOCK_BASE_URL (e.g. http://127.0.0.1:8317/v1)}"
    : "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY}"
    AUTH_ARGS=(--auth-type openai --model "${SHERLOCK_MODEL:-gpt-5.5}")
    export OPENAI_API_KEY="$SHERLOCK_API_KEY"
    export OPENAI_BASE_URL="$SHERLOCK_BASE_URL"
    info "auth: openai -> $SHERLOCK_BASE_URL (${SHERLOCK_MODEL:-gpt-5.5})"
    ;;
  *)
    AUTH_ARGS=(--auth-type qwen-oauth)
    info "auth: qwen-oauth (run 'qwen' once interactively if this fails)"
    ;;
esac

# ---------------------------------------------------------------- the gate
PROMPT="Проанализируй логи в каталоге $LOGS. Найди, что там произошло, определи
корневую причину и предложи что делать. Ссылайся на конкретные строки в формате
файл:строка."

OUT="$WORK/result.json"
info "running (timeout ${TIMEOUT}s)..."
START=$(date +%s)
# The key is already exported above; it reaches qwen through /proc/<pid>/environ, mode 400 and
# owner-only, and never appears in `ps`.
( cd "$WORK" && timeout "$TIMEOUT" "$QWEN" "${AUTH_ARGS[@]}" \
    -p "$PROMPT" --output-format json </dev/null ) >"$OUT" 2>"$WORK/stderr.txt"
RC=$?
ELAPSED=$(( $(date +%s) - START ))

[ $RC -eq 124 ] && fail "timed out after ${TIMEOUT}s"
[ -s "$OUT" ]   || { sed -n '1,20p' "$WORK/stderr.txt"; fail "no output produced (rc=$RC)"; }

# qwen reports fatal errors on the FINAL record (type=="result").
# Inner tool_result blocks also carry is_error, so never grep the whole file —
# a failed grep inside the agent's own loop is normal, not a gate failure.
python3 - "$OUT" <<'PY' || exit 1
import json, sys
d = json.load(open(sys.argv[1]))
d = d if isinstance(d, list) else [d]
final = next((r for r in d if r.get("type") == "result"), None)
if final is None:
    print("\033[31m✗ no final result record — run did not complete\033[0m"); sys.exit(1)
if final.get("is_error"):
    e = final.get("error") or {}
    print("\033[31m✗ qwen returned an error: %s\033[0m" % (e.get("message", e) or final.get("subtype")))
    sys.exit(1)
PY

# ------------------------------------------------------------ assert quality
python3 - "$OUT" "$ELAPSED" <<'PY'
import json, re, sys
path, elapsed = sys.argv[1], sys.argv[2]
d = json.load(open(path))
d = d if isinstance(d, list) else [d]
text = "\n".join(str(r.get("result") or r.get("text") or "") for r in d)

if len(text.strip()) < 200:
    print("\033[31m✗ answer too short to be an investigation (%d chars)\033[0m" % len(text))
    sys.exit(1)

# a real RCA cites evidence; a hand-wave does not
cites = re.findall(r"[\w./-]+\.(?:log|txt|jsonl|out|gz)(?::\d+)?", text)
print("\033[32m✓ answer: %d chars, %d evidence references, %ss\033[0m"
      % (len(text), len(cites), elapsed))
if not cites:
    print("\033[33m! no file:line references — SKILL.md step 6 is not landing\033[0m")
    sys.exit(1)
print("\n--- first 600 chars ---")
print(text[:600])
PY
RC=$?

echo
if [ $RC -eq 0 ]; then
  green "I0 GATE GREEN — the skill runs in the real Qwen Coder CLI. Proceed to I1."
else
  red "I0 GATE RED — fix before starting I1. Full output: $OUT"
fi
exit $RC
