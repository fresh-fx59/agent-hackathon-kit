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
  if [ "${OMIT_RESUME_SESSION:-0}" = 1 ]; then
    printf '[{"type":"result","is_error":false,"num_turns":2,"result":"PROVEN: sample.log:1"}]\n'
    exit 0
  fi
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
test "$(wc -l < "$TRACE/attempts.jsonl" | tr -d '[:space:]')" = 2
test "$(wc -l < "$TMP/ledger.jsonl" | tr -d '[:space:]')" = 1
rg -q 'stream failed; preserving session 11111111-1111-1111-1111-111111111111' "$TMP/out.log"
test -f "$TRACE/status.json"
test -f "$TRACE/status-events.jsonl"
python3 - "$TRACE" <<'PY'
import json, pathlib, sys
trace = pathlib.Path(sys.argv[1])
status = json.loads((trace / "status.json").read_text())
assert status["phase"] == "FINISHED_UNCHECKED", status
events = [json.loads(line) for line in (trace / "status-events.jsonl").read_text().splitlines()]
names = [row["event"] for row in events]
for event in ("STAGING", "QWEN_RUNNING", "VERIFYING", "FINISHED_UNCHECKED"):
    assert event in names, names
assert names.index("STAGING") < names.index("QWEN_RUNNING") < names.index("VERIFYING") < names.index("FINISHED_UNCHECKED"), names
attempts = [row for row in events if row["event"] == "ATTEMPT_FINISHED"]
assert len(attempts) == 2, attempts
assert attempts[0]["attempt"] == 0 and attempts[0]["exit_code"] == "0", attempts
assert attempts[1]["attempt"] == 1 and attempts[1]["session_id"] == "11111111-1111-1111-1111-111111111111", attempts
assert all(row["duration_s"] is not None and row["upstream_log"] for row in attempts), attempts
assert any(row["event"] == "RECOVERY_DECIDED" and row["reason"] == "broken_stream" for row in events), events
PY
OMIT_RESUME_SESSION=1 SHERLOCK_API_KEY=dummy SHERLOCK_CORPUS="$TMP/corpus" SHERLOCK_UPSTREAM_LOG=0 \
SHERLOCK_RESUME_MAX_ATTEMPTS=1 SHERLOCK_RESUME_BACKOFF_S=0 QWEN_BIN="$TMP/fake-qwen" \
BENCH_RUNS="$TMP/runs-omitted-session" BENCH_LEDGER="$TMP/ledger-omitted-session.jsonl" \
bash "$RUNNER" none > /dev/null 2>&1
OMITTED_TRACE="$(find "$TMP/runs-omitted-session" -mindepth 1 -maxdepth 1 -type d | head -1)"
python3 - "$OMITTED_TRACE" <<'PY'
import json, pathlib, sys
trace = pathlib.Path(sys.argv[1]); session = "11111111-1111-1111-1111-111111111111"
events = [json.loads(line) for line in (trace / "status-events.jsonl").read_text().splitlines()]
resumed = next(row for row in events if row["event"] == "ATTEMPT_FINISHED" and row["attempt"] == 1)
assert resumed["session_id"] == session, resumed
assert json.loads((trace / "status.json").read_text())["session_id"] == session
PY
cat > "$TMP/fake-qwen-success" <<'SH'
#!/usr/bin/env bash
printf '[{"type":"result","session_id":"22222222-2222-2222-2222-222222222222","is_error":false,"num_turns":1,"result":"PROVEN: sample.log:1"}]\n'
SH
chmod +x "$TMP/fake-qwen-success"
SHERLOCK_API_KEY=dummy SHERLOCK_CORPUS="$TMP/corpus" SHERLOCK_UPSTREAM_LOG=0 \
QWEN_BIN="$TMP/fake-qwen-success" BENCH_RUNS="$TMP/runs-success" BENCH_LEDGER="$TMP/ledger-success.jsonl" \
bash "$RUNNER" none > /dev/null 2>&1
SUCCESS_TRACE="$(find "$TMP/runs-success" -mindepth 1 -maxdepth 1 -type d | head -1)"
python3 - "$SUCCESS_TRACE" <<'PY'
import json, pathlib, sys
trace = pathlib.Path(sys.argv[1]); status = json.loads((trace / "status.json").read_text())
events = [json.loads(line) for line in (trace / "status-events.jsonl").read_text().splitlines()]
assert status["phase"] == "FINISHED_UNCHECKED" and status["session_id"].startswith("2222"), status
assert [row["event"] for row in events].count("FINISHED_UNCHECKED") == 1, events
assert next(row for row in events if row["event"] == "ATTEMPT_FINISHED")["session_id"].startswith("2222")
PY
set +e
SHERLOCK_API_KEY=dummy SHERLOCK_CORPUS="$TMP/corpus" SHERLOCK_UPSTREAM_LOG=0 SHERLOCK_DATASET=missing \
QWEN_BIN="$TMP/fake-qwen-success" BENCH_RUNS="$TMP/runs-missing" BENCH_LEDGER="$TMP/ledger-missing.jsonl" \
bash "$RUNNER" none > /dev/null 2>&1
MISSING_RC=$?
set -e
test "$MISSING_RC" = 1
MISSING_TRACE="$(find "$TMP/runs-missing" -mindepth 1 -maxdepth 1 -type d | head -1)"
python3 - "$MISSING_TRACE" <<'PY'
import json, pathlib, sys
trace = pathlib.Path(sys.argv[1]); status = json.loads((trace / "status.json").read_text())
events = [json.loads(line) for line in (trace / "status-events.jsonl").read_text().splitlines()]
assert status["phase"] == "RUN_FAILED" and status["exit_code"] == "1", status
assert [row["event"] for row in events].count("RUN_FAILED") == 1, events
PY
echo 'ok: preserves session and resumes after malformed stream'
