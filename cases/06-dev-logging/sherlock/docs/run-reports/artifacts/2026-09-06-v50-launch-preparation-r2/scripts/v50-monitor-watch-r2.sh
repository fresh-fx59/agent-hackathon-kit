#!/bin/sh
set -eu
: "${MONITOR_SCRIPT:?FAIL CLOSED: set dedicated-review-monitor.py path}"
: "${RUN_ROOT:?FAIL CLOSED: set exact fresh controller run root}"
: "${HELPER_PATH:?FAIL CLOSED: set final pinned lifecycle helper path}"
: "${REVIEW_PROMPT:?FAIL CLOSED: set pinned reviewer prompt path}"
: "${REVIEW_COMMAND_JSON:?FAIL CLOSED: set pinned reviewer command JSON path}"
: "${MONITOR_DIR:?FAIL CLOSED: set monitor artifact directory}"
: "${TERRA_READINESS_ARTIFACT:?FAIL CLOSED: set Terra readiness artifact path}"
test -s "$TERRA_READINESS_ARTIFACT"; test -f "$MONITOR_SCRIPT"; test "$(sha256sum "$MONITOR_SCRIPT" | awk '{print $1}')" = "c6cd28b188b4b6eab5797998e138fe988d0e34e7272d9ac7ed7a42dee6b3816d"; test ! -e "$RUN_ROOT"; test -s "$REVIEW_PROMPT"; test "$(sha256sum "$REVIEW_PROMPT" | awk '{print $1}')" = "c73b76c53d06aec57fb63ff0e27fb3a8f9157d17e80a555700152a4ab59f1856"; test -s "$REVIEW_COMMAND_JSON"; test "$(sha256sum "$REVIEW_COMMAND_JSON" | awk '{print $1}')" = "231c83332933f9d66371f96f37f9243c64e7e4279a6ef1aa7e6f36e7b5c66f73"; test ! -e "$MONITOR_DIR"
test "$(sha256sum "$HELPER_PATH" | awk '{print $1}')" = "235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907"
exec python3 "$MONITOR_SCRIPT" watch --run-root "$RUN_ROOT" --helper "$HELPER_PATH" --prompt "$REVIEW_PROMPT" --review-command-json "$REVIEW_COMMAND_JSON" --monitor-dir "$MONITOR_DIR"
