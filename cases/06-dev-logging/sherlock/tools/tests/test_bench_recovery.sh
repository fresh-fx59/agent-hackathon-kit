#!/usr/bin/env bash
# Provider-free regression: a broken stream must resume its saved Qwen session.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$HERE/../../eval/bench/run-bench.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/corpus"
printf '2026-01-01 test\n' > "$TMP/corpus/sample.log"
cat > "$TMP/fake-qwen" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
SESSION="11111111-1111-1111-1111-111111111111"
if [[ " $* " == *" --resume $SESSION "* ]]; then
  cat <<'JSON'
[{"type":"result","session_id":"11111111-1111-1111-1111-111111111111","is_error":false,"num_turns":2,"result":"PROVEN: sample.log:1 — `2026-01-01 test`"}]
JSON
else
  test -f "$PWD/corpus/sample.log"
  [[ "$*" == *"$PWD/corpus"* ]]
  mkdir -p "$QWEN_HOME/saved"
  printf state > "$QWEN_HOME/saved/$SESSION"
  # Simulates a provider disconnect while Qwen is emitting its JSON envelope.
  printf '[{"type":"system","session_id":"11111111-1111-1111-1111-111111111111"},BROKEN\n'
fi
SH
chmod +x "$TMP/fake-qwen"

SHERLOCK_API_KEY=dummy \
SHERLOCK_CORPUS="$TMP/corpus" \
SHERLOCK_UPSTREAM_LOG=0 \
SHERLOCK_RESUME_MAX_ATTEMPTS=1 \
SHERLOCK_RESUME_BACKOFF_S=0 \
QWEN_BIN="$TMP/fake-qwen" \
BENCH_RUNS="$TMP/runs" \
BENCH_LEDGER="$TMP/ledger.jsonl" \
bash "$RUNNER" none > "$TMP/out.log" 2>&1

TRACE="$(find "$TMP/runs" -mindepth 1 -maxdepth 1 -type d | head -1)"
test -f "$TRACE/out-attempt-0.json"
test -f "$TRACE/err-attempt-0.txt"
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tools"]["exclude"])' "$TRACE/qwen-settings.json")" = "['agent']"
test -f "$TRACE/qwen-home/saved/11111111-1111-1111-1111-111111111111"
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["resume_attempts"])' "$TRACE/recovery.json")" = 1
test "$(wc -l < "$TRACE/attempts.jsonl")" = 2
test "$(wc -l < "$TMP/ledger.jsonl")" = 1
rg -q 'stream failed; preserving session 11111111-1111-1111-1111-111111111111' "$TMP/out.log"
echo 'ok: preserves session and resumes after malformed stream'
