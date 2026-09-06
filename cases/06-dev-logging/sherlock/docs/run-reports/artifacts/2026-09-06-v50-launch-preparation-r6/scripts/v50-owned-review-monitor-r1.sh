#!/bin/sh
# Source this file from a lane launcher after RUN_ROOT is fixed and before the
# controller creates it.  The functions own one monitor PID and its exact lock.

review_monitor_cleanup() {
  trap - EXIT HUP INT TERM
  if [ -n "${REVIEW_MONITOR_PID:-}" ] && kill -0 "$REVIEW_MONITOR_PID" 2>/dev/null; then
    kill "$REVIEW_MONITOR_PID" 2>/dev/null || true
    wait "$REVIEW_MONITOR_PID" 2>/dev/null || true
  fi
  if [ -n "${MONITOR_LOCK:-}" ] && [ -f "$MONITOR_LOCK" ]; then
    python3 - "$MONITOR_LOCK" "${REVIEW_MONITOR_PID:-}" <<'PY' || true
import json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    row = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
if str(row.get("pid")) == sys.argv[2]:
    os.unlink(path)
PY
  fi
}

review_monitor_signal() {
  review_monitor_cleanup
  exit 128
}

review_monitor_start() {
  : "${RUN_ROOT:?FAIL CLOSED: set exact fresh controller run root}"
  : "${TERRA_READINESS_ARTIFACT:?FAIL CLOSED: set Terra readiness artifact path}"
  : "${MONITOR_SCRIPT:?FAIL CLOSED: set dedicated-review-monitor.py path}"
  : "${HELPER_PATH:?FAIL CLOSED: set final pinned lifecycle helper path}"
  : "${REVIEW_PROMPT:?FAIL CLOSED: set pinned reviewer prompt path}"
  : "${REVIEW_COMMAND_JSON:?FAIL CLOSED: set pinned reviewer command JSON path}"

  expected_monitor_dir="${RUN_ROOT}.monitor"
  expected_monitor_lock="$(dirname "$RUN_ROOT")/.$(basename "$RUN_ROOT").review-monitor.lock"
  if [ -n "${MONITOR_DIR:-}" ] && [ "$MONITOR_DIR" != "$expected_monitor_dir" ]; then
    echo "FAIL CLOSED: MONITOR_DIR must equal $expected_monitor_dir" >&2
    return 2
  fi
  if [ -n "${MONITOR_LOCK:-}" ] && [ "$MONITOR_LOCK" != "$expected_monitor_lock" ]; then
    echo "FAIL CLOSED: MONITOR_LOCK must equal $expected_monitor_lock" >&2
    return 2
  fi
  MONITOR_DIR=$expected_monitor_dir
  MONITOR_LOCK=$expected_monitor_lock
  export MONITOR_DIR MONITOR_LOCK

  test -s "$TERRA_READINESS_ARTIFACT"
  test -f "$MONITOR_SCRIPT"
  test "$(sha256sum "$MONITOR_SCRIPT" | awk '{print $1}')" = "dcf42026de753649a169d6499077e2f1f2570b7c9af5ed104fd7d97ccc3a388e"
  test -s "$REVIEW_PROMPT"
  test "$(sha256sum "$REVIEW_PROMPT" | awk '{print $1}')" = "c73b76c53d06aec57fb63ff0e27fb3a8f9157d17e80a555700152a4ab59f1856"
  test -s "$REVIEW_COMMAND_JSON"
  test "$(sha256sum "$REVIEW_COMMAND_JSON" | awk '{print $1}')" = "7a594ff51f8142ab7783fadaff5630385eb64cd49575f89a35f424d5c4625898"
  test "$(sha256sum "$HELPER_PATH" | awk '{print $1}')" = "235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907"
  test ! -e "$RUN_ROOT"
  test ! -e "$MONITOR_DIR"
  test ! -e "$MONITOR_LOCK"

  python3 "$MONITOR_SCRIPT" watch \
    --run-root "$RUN_ROOT" --helper "$HELPER_PATH" --prompt "$REVIEW_PROMPT" \
    --review-command-json "$REVIEW_COMMAND_JSON" --monitor-dir "$MONITOR_DIR" \
    >"${MONITOR_DIR}.watch.stdout" 2>"${MONITOR_DIR}.watch.stderr" &
  REVIEW_MONITOR_PID=$!
  export REVIEW_MONITOR_PID
  trap review_monitor_cleanup EXIT
  trap review_monitor_signal HUP INT TERM

  readiness_attempt=0
  while [ ! -d "$MONITOR_DIR" ] || [ ! -f "$MONITOR_LOCK" ]; do
    if ! kill -0 "$REVIEW_MONITOR_PID" 2>/dev/null; then
      wait "$REVIEW_MONITOR_PID" || true
      echo "FAIL CLOSED: review monitor exited before readiness" >&2
      return 2
    fi
    readiness_attempt=$((readiness_attempt + 1))
    if [ "$readiness_attempt" -ge 100 ]; then
      echo "FAIL CLOSED: review monitor readiness timeout" >&2
      return 2
    fi
    sleep 0.1
  done
  python3 - "$MONITOR_LOCK" "$REVIEW_MONITOR_PID" <<'PY'
import json, pathlib, sys
row = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if set(row) != {"schema", "pid", "started_at"} or row["schema"] != 1 or row["pid"] != int(sys.argv[2]):
    raise SystemExit("FAIL CLOSED: monitor lock does not bind the owned PID")
PY
  kill -0 "$REVIEW_MONITOR_PID"
  test ! -e "$RUN_ROOT"
}

review_monitor_finish() {
  controller_rc=$1
  receipt_count=0
  if [ -d "$RUN_ROOT/runs" ]; then
    for receipt in "$RUN_ROOT"/runs/*/lifecycle-receipt.json; do
      if [ -f "$receipt" ]; then
        receipt_count=$((receipt_count + 1))
      fi
    done
  fi
  if [ "$receipt_count" -ne 1 ]; then
    review_monitor_cleanup
    if [ "$controller_rc" -ne 0 ]; then
      return "$controller_rc"
    fi
    echo "FAIL CLOSED: controller exited without exactly one terminal receipt" >&2
    return 2
  fi

  wait_attempt=0
  while kill -0 "$REVIEW_MONITOR_PID" 2>/dev/null; do
    wait_attempt=$((wait_attempt + 1))
    if [ "$wait_attempt" -ge 100 ]; then
      echo "FAIL CLOSED: review monitor did not stop at terminal receipt" >&2
      review_monitor_cleanup
      return 2
    fi
    sleep 0.1
  done
  set +e
  wait "$REVIEW_MONITOR_PID"
  monitor_rc=$?
  set -e
  REVIEW_MONITOR_PID=
  trap - EXIT HUP INT TERM
  if [ "$monitor_rc" -ne 0 ]; then
    echo "FAIL CLOSED: review monitor exited $monitor_rc" >&2
    return 2
  fi
  python3 "$MONITOR_SCRIPT" audit --run-root "$RUN_ROOT" \
    --monitor-dir "$MONITOR_DIR" --helper "$HELPER_PATH" \
    >"${MONITOR_DIR}.audit.json"
  if [ "$controller_rc" -ne 0 ]; then
    return "$controller_rc"
  fi
}
