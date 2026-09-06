set -euo pipefail
REPO=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock
INPUTS=/home/claude-developer/hack/sherlock-final-inputs-20260906/independent
PY=/run/current-system/sw/bin/python3
QWEN=/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js
DEV_HOME=/home/claude-developer
DEV_PATH=/home/claude-developer/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin
WITH_SECRET=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
LIFECYCLE=$REPO/eval/bench/lifecycle-supervisor.py
TARGET=/home/claude-developer/hack/sherlock-v50-independent-qualification-20260906-r1
HARNESS=/home/claude-developer/hack/sherlock-v50-harness-20260906-r1
TARGET_NONCES=/home/claude-developer/hack/sherlock-paid-admission-nonces

cd "$REPO"
test "$(sha256sum "$LIFECYCLE" | awk '{print $1}')" = 861593a235903f6de92c53a0b27d9bde5888aaead2d06acf97f3c3529c0fc2cb
test "$(sha256sum "$REPO/measure/interactive-drive.py" | awk '{print $1}')" = c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0
test "$(readlink -f /home/claude-developer/.local/bin/qwen)" = "$QWEN"
test "$(sudo -u claude-developer env HOME="$DEV_HOME" PATH="$DEV_PATH" qwen --version)" = 0.22.0
test -s "$TARGET/probe-manifest.json"
test -s "$HARNESS/harness-acceptance.json"


PROBE_MANIFEST="$TARGET/probe-manifest.json"
PROBE_APPROVAL=$(sha256sum "$PROBE_MANIFEST" | awk '{print $1}')
printf 'target probe approval sha256: %s\n' "$PROBE_APPROVAL"

sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$WITH_SECRET" neuraldeep_api_key --env NEURALDEEP_API_KEY -- \
  "$PY" "$REPO/eval/bench/target-contract-probe.py" run \
  --manifest "$PROBE_MANIFEST" \
  --operator-approved-probe "$PROBE_APPROVAL" \
  --nonce-root "$TARGET_NONCES" --json >"$TARGET.launch.stdout" 2>"$TARGET.launch.stderr"
