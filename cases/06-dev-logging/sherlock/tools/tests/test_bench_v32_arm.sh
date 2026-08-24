#!/usr/bin/env bash
# v32 regression: the arm must load the skill explicitly and run with Qwen's
# managed auto-memory disabled. r4 loaded no skill and spent 252,029 input
# tokens inside managed-auto-memory-extractor.
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
# The prompt must invoke the skill explicitly, by name.
[[ "$*" == *"/sherlock"* ]] || { echo "prompt does not invoke /sherlock" >&2; exit 9; }
printf '[{"type":"result","session_id":"44444444-4444-4444-4444-444444444444","is_error":false,"num_turns":2,"result":"PROVEN: sample.log:1 — `2026-01-01 test`"}]\n'
SH
chmod +x "$TMP/fake-qwen"

SHERLOCK_API_KEY=dummy \
SHERLOCK_CORPUS="$TMP/corpus" \
SHERLOCK_UPSTREAM_LOG=0 \
QWEN_BIN="$TMP/fake-qwen" \
BENCH_RUNS="$TMP/runs" \
BENCH_LEDGER="$TMP/ledger.jsonl" \
bash "$RUNNER" v32 > "$TMP/out.log" 2>&1

TRACE="$(find "$TMP/runs" -mindepth 1 -maxdepth 1 -type d | head -1)"
python3 - "$TRACE" <<'PY'
import json, sys
from pathlib import Path
trace = Path(sys.argv[1])
sealed = json.loads((trace / "qwen-settings-pre.json").read_text())
assert sealed["memory"]["enableManagedAutoMemory"] is False, sealed
assert sealed["model"]["generationConfig"]["maxRetries"] == 0, sealed
assert sealed["tools"]["exclude"] == ["agent"], sealed
status = json.loads((trace / "status.json").read_text())
assert status["arm"] == "v32", status
PY
echo "✓ v32 arm: explicit skill call, managed auto-memory off"
