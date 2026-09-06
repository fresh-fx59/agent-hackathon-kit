#!/bin/sh
set -eu
: "${MONITOR_SCRIPT:?FAIL CLOSED: set dedicated-review-monitor.py path}"
: "${RUN_ROOT:?FAIL CLOSED: set exact terminal run root}"
: "${HELPER_PATH:?FAIL CLOSED: set final pinned lifecycle helper path}"
: "${MONITOR_DIR:?FAIL CLOSED: set monitor artifact directory}"
: "${TERRA_READINESS_ARTIFACT:?FAIL CLOSED: set Terra readiness artifact path}"
test -s "$TERRA_READINESS_ARTIFACT"; test -f "$MONITOR_SCRIPT"; test "$(sha256sum "$MONITOR_SCRIPT" | awk '{print $1}')" = "dcf42026de753649a169d6499077e2f1f2570b7c9af5ed104fd7d97ccc3a388e"; test -d "$RUN_ROOT"; test -d "$MONITOR_DIR"
test "$(sha256sum "$HELPER_PATH" | awk '{print $1}')" = "235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907"
exec python3 "$MONITOR_SCRIPT" audit --run-root "$RUN_ROOT" --monitor-dir "$MONITOR_DIR" --helper "$HELPER_PATH"
