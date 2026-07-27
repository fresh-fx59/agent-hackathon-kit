#!/usr/bin/env bash
# verify.sh -- full self-check for agent-hackathon-kit.
#
#   bash scripts/verify.sh
#
# 1. python3 >= 3.9 gate
# 2. run every test_*.py in the repo (fail fast)
# 3. boot the available mocks, poll /health, smoke every mcp/*_mcp.py
#    (initialize + tools/list must report >= 1 tool)
# 4. run each cases/*/benchmark.py --self-test
# 5. PASS/FAIL summary + proper exit code
#
# Mid-build friendly: anything not written yet prints a TODO line and is
# skipped instead of failing the run.  Needs only bash + python3 + coreutils.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOGDIR="$(mktemp -d)"
PASS_COUNT=0
FAIL_COUNT=0
TODO_COUNT=0
FAILED=0
MOCK_PIDS=()

say()  { printf '%s\n' "$*"; }
pass() { say "PASS  $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { say "FAIL  $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); FAILED=1; }
todo() { say "TODO  $*"; TODO_COUNT=$((TODO_COUNT + 1)); }

summary_and_exit() {
  say ""
  say "===== verify summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${TODO_COUNT} todo ====="
  if [ "$FAILED" -ne 0 ]; then
    say "RESULT: FAIL (logs in $LOGDIR)"
    exit 1
  fi
  say "RESULT: PASS"
  exit 0
}

cleanup() {
  for pid in ${MOCK_PIDS[@]+"${MOCK_PIDS[@]}"}; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  wait >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---------------------------------------------------------------- 1. python
say "== 1/4 python version gate =="
if ! command -v python3 >/dev/null 2>&1; then
  say "FATAL: python3 not found on PATH"
  exit 1
fi
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  pass "python3 >= 3.9 ($(python3 -V 2>&1))"
else
  say "FATAL: python3 >= 3.9 required, found $(python3 -V 2>&1)"
  exit 1
fi

# ------------------------------------------------------------- 2. unit tests
say ""
say "== 2/4 unit tests (every test_*.py, fail fast) =="
TEST_FILES="$(find . -path ./.git -prune -o -name 'test_*.py' -print | sort)"
if [ -z "$TEST_FILES" ]; then
  todo "no test_*.py files found yet"
else
  while IFS= read -r test_file; do
    log="$LOGDIR/$(basename "$test_file").log"
    if python3 "$test_file" >"$log" 2>&1; then
      pass "$test_file"
    else
      fail "$test_file"
      say "----- output of $test_file -----"
      cat "$log"
      say "--------------------------------"
      summary_and_exit
    fi
  done <<< "$TEST_FILES"
fi

# --------------------------------------------- 3. mocks up + MCP server smoke
say ""
say "== 3/4 mock services + MCP server smoke =="

MOCK_NAMES=(tracker quality forge tms)
MOCK_PORT_VARS=(TRACKER_PORT QUALITY_PORT FORGE_PORT TMS_PORT)
MOCK_URL_VARS=(TRACKER_URL QUALITY_URL FORGE_URL TMS_URL)
MOCK_DEFAULT_PORTS=(8801 8802 8803 8804)

wait_health() { # $1 = port -> 0 when /health answers {"ok": true} within 15s
  python3 - "$1" <<'PY'
import json, sys, time, urllib.request
port = sys.argv[1]
req = urllib.request.Request(
    "http://127.0.0.1:%s/health" % port,
    headers={"User-Agent": "agent-hackathon-kit/0.1"})
deadline = time.time() + 15
while time.time() < deadline:
    try:
        with urllib.request.urlopen(req, timeout=1) as resp:
            if json.load(resp).get("ok") is True:
                sys.exit(0)
    except Exception:
        pass
    time.sleep(0.3)
sys.exit(1)
PY
}

for i in 0 1 2 3; do
  name="${MOCK_NAMES[$i]}"
  port_var="${MOCK_PORT_VARS[$i]}"
  port="${!port_var:-${MOCK_DEFAULT_PORTS[$i]}}"
  app="mocks/$name/app.py"
  if [ ! -f "$app" ]; then
    todo "$app not written yet -- skipping mock boot"
    continue
  fi
  env "$port_var=$port" python3 "$app" >"$LOGDIR/mock-$name.log" 2>&1 &
  MOCK_PIDS+=("$!")
  if wait_health "$port"; then
    pass "mock $name healthy on 127.0.0.1:$port"
  else
    fail "mock $name did not answer /health on port $port (log: $LOGDIR/mock-$name.log)"
  fi
done

smoke_mcp() { # $1 = server script -> prints tool count, rc 0 when >= 1 tool
  printf '%s\n%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | timeout 15 python3 "$1" 2>>"$LOGDIR/mcp-smoke.log" \
    | python3 -c '
import json, sys
tools = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("id") == 2:
        tools = (msg.get("result") or {}).get("tools")
if not tools:
    sys.exit(1)
print(len(tools))
'
}

for i in 0 1 2 3; do
  name="${MOCK_NAMES[$i]}"
  url_var="${MOCK_URL_VARS[$i]}"
  port_var="${MOCK_PORT_VARS[$i]}"
  port="${!port_var:-${MOCK_DEFAULT_PORTS[$i]}}"
  server="mcp/${name}_mcp.py"
  if [ ! -f "$server" ]; then
    todo "$server not written yet -- skipping MCP smoke"
    continue
  fi
  export "$url_var=http://127.0.0.1:$port"
  if tool_count="$(smoke_mcp "$server")"; then
    pass "$server answers initialize + tools/list ($tool_count tool(s))"
  else
    fail "$server smoke failed (initialize/tools-list; log: $LOGDIR/mcp-smoke.log)"
  fi
  unset "$url_var"
done

# ------------------------------------------------------------- 4. benchmarks
say ""
say "== 4/4 case benchmarks (--self-test) =="
FOUND_BENCH=0
for bench in cases/*/benchmark.py; do
  [ -e "$bench" ] || continue
  FOUND_BENCH=1
  log="$LOGDIR/$(basename "$(dirname "$bench")")-benchmark.log"
  if (cd "$(dirname "$bench")" && python3 benchmark.py --self-test) >"$log" 2>&1; then
    pass "$bench --self-test"
  else
    fail "$bench --self-test (log: $log)"
  fi
done
if [ "$FOUND_BENCH" -eq 0 ]; then
  todo "no cases/*/benchmark.py written yet"
fi

summary_and_exit
