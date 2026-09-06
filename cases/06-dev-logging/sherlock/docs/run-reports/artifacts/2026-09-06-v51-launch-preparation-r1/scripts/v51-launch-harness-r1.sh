#!/bin/bash
set -euo pipefail
REPO=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock
PY=/run/current-system/sw/bin/python3
QWEN=/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js
DEV_HOME=/home/claude-developer
DEV_PATH=/home/claude-developer/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin
WITH_SECRET=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
LIFECYCLE=$REPO/eval/bench/lifecycle-supervisor.py
TARGET=/home/claude-developer/hack/sherlock-v51-qualification-20260906-r1
HARNESS=/home/claude-developer/hack/sherlock-v51-harness-20260906-r1
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cd "$REPO"
test "$(sha256sum "$LIFECYCLE" | awk '{print $1}')" = 235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907
test "$(sha256sum "$REPO/measure/interactive-drive.py" | awk '{print $1}')" = c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0
test "$(readlink -f /home/claude-developer/.local/bin/qwen)" = "$QWEN"
test "$(sudo -u claude-developer env HOME="$DEV_HOME" PATH="$DEV_PATH" qwen --version)" = 0.22.0
test -s "$TARGET/probe-manifest.json"

RUN_ROOT="$HARNESS"
export RUN_ROOT
. "$SCRIPT_DIR/v51-owned-review-monitor-r1.sh"
review_monitor_start

if sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$WITH_SECRET" eval_broker_api_key --env SHERLOCK_API_KEY -- \
  bash "$REPO/eval/bench/run-harness-qualification.sh" \
  "$HARNESS" --target-input "$TARGET" >"$HARNESS.launch.stdout" 2>"$HARNESS.launch.stderr"; then
  CONTROLLER_RC=0
else
  CONTROLLER_RC=$?
fi
review_monitor_finish "$CONTROLLER_RC"
