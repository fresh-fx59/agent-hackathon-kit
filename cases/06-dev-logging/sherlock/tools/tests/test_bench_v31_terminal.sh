#!/usr/bin/env bash
# v31 regression: a nonzero Qwen exit must never become a successful run just
# because the stream contained parseable JSON (v30 exited 0 on any candidate).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$HERE/../../eval/bench/run-bench.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/corpus"
printf '2026-01-01 test\n' > "$TMP/corpus/sample.log"
cat > "$TMP/fake-qwen" <<'SH'
#!/usr/bin/env bash
printf '[{"type":"result","session_id":"33333333-3333-3333-3333-333333333333","is_error":false,"num_turns":2,"result":"PROVEN: sample.log:1 — `2026-01-01 test`"}]\n'
exit 3
SH
chmod +x "$TMP/fake-qwen"

set +e
SHERLOCK_API_KEY=dummy \
SHERLOCK_CORPUS="$TMP/corpus" \
SHERLOCK_UPSTREAM_LOG=0 \
SHERLOCK_RESUME_MAX_ATTEMPTS=0 \
QWEN_BIN="$TMP/fake-qwen" \
BENCH_RUNS="$TMP/runs" \
BENCH_LEDGER="$TMP/ledger.jsonl" \
bash "$RUNNER" none > "$TMP/out.log" 2>&1
RC=$?
set -e

test "$RC" -ne 0 || { echo "FAIL: runner exited 0 after a nonzero Qwen exit"; exit 1; }
TRACE="$(find "$TMP/runs" -mindepth 1 -maxdepth 1 -type d | head -1)"
python3 - "$TRACE" <<'PY'
import json, sys
from pathlib import Path
trace = Path(sys.argv[1])
status = json.loads((trace / "status.json").read_text())
assert status["phase"] == "RUN_FAILED", status
attempts = [json.loads(line) for line in (trace / "attempts.jsonl").read_text().splitlines() if line.strip()]
assert attempts[-1]["exit_code"] == 3, attempts
PY
echo "✓ v31 terminal: nonzero Qwen exit fails the run"
