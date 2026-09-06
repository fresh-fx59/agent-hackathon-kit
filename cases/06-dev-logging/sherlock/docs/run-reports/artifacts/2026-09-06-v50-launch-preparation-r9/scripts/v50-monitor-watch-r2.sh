#!/bin/sh
set -eu
: "${MONITOR_SCRIPT:?FAIL CLOSED: set dedicated-review-monitor.py path}"
: "${RUN_ROOT:?FAIL CLOSED: set exact fresh controller run root}"
: "${HELPER_PATH:?FAIL CLOSED: set final pinned lifecycle helper path}"
: "${REVIEW_PROMPT:?FAIL CLOSED: set pinned reviewer prompt path}"
: "${REVIEW_COMMAND_JSON:?FAIL CLOSED: set pinned reviewer command JSON path}"
: "${MONITOR_DIR:?FAIL CLOSED: set monitor artifact directory}"
: "${TERRA_READINESS_ARTIFACT:?FAIL CLOSED: set Terra readiness artifact path}"
test -s "$TERRA_READINESS_ARTIFACT"; test -f "$MONITOR_SCRIPT"; test "$(sha256sum "$MONITOR_SCRIPT" | awk '{print $1}')" = "374dd08450e95d195cf338540821a5475b19c3a0c5f4dea539df08861667ec5f"; test ! -e "$RUN_ROOT"; test -s "$REVIEW_PROMPT"; test "$(sha256sum "$REVIEW_PROMPT" | awk '{print $1}')" = "cd91a67a9128123934167ca2e9d65df5e7030688d72b016dc2ab084e8b420efb"; test -s "$REVIEW_COMMAND_JSON"; test "$(sha256sum "$REVIEW_COMMAND_JSON" | awk '{print $1}')" = "202791838b91a3375dcfc1df62c160592dfe56be37815e467f56cb9201e62b8e"; test ! -e "$MONITOR_DIR"
test "$(sha256sum "$HELPER_PATH" | awk '{print $1}')" = "235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907"
exec python3 "$MONITOR_SCRIPT" watch --run-root "$RUN_ROOT" --helper "$HELPER_PATH" --prompt "$REVIEW_PROMPT" --review-command-json "$REVIEW_COMMAND_JSON" --monitor-dir "$MONITOR_DIR"
