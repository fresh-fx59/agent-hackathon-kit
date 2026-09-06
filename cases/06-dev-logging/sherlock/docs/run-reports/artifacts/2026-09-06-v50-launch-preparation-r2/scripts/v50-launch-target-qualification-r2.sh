#!/bin/sh
set -eu

: "${TERRA_READINESS_ARTIFACT:?FAIL CLOSED: set Terra readiness artifact path}"
: "${MONITOR_SCRIPT:?FAIL CLOSED: set dedicated-review-monitor.py path}"
: "${HELPER_PATH:?FAIL CLOSED: set final pinned lifecycle helper path}"
: "${REVIEW_PROMPT:?FAIL CLOSED: set pinned reviewer prompt path}"
: "${REVIEW_COMMAND_JSON:?FAIL CLOSED: set pinned reviewer command JSON path}"
: "${MONITOR_DIR:?FAIL CLOSED: set monitor artifact directory}"
: "${RUN_ROOT:?FAIL CLOSED: set exact fresh run root before controller launch}"

test -s "$TERRA_READINESS_ARTIFACT"
test -x "$MONITOR_SCRIPT"
test -s "$REVIEW_PROMPT"; test "$(sha256sum "$REVIEW_PROMPT" | awk '{print $1}')" = "c73b76c53d06aec57fb63ff0e27fb3a8f9157d17e80a555700152a4ab59f1856"
test -s "$REVIEW_COMMAND_JSON"
test -d "$MONITOR_DIR"
test ! -e "$RUN_ROOT"
test "$(sha256sum "$HELPER_PATH" | awk '{print $1}')" = "235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907"

exec "$MONITOR_SCRIPT" watch --run-root "$RUN_ROOT" --helper "$HELPER_PATH" --prompt "$REVIEW_PROMPT" --review-command-json "$REVIEW_COMMAND_JSON" --monitor-dir "$MONITOR_DIR"
