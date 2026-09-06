set -euo pipefail
REPO=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock
INPUTS=/home/claude-developer/hack/sherlock-final-inputs-20260906
PY=/run/current-system/sw/bin/python3
QWEN=/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js
DEV_HOME=/home/claude-developer
DEV_PATH=/home/claude-developer/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin
WITH_SECRET=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
LIFECYCLE=$REPO/eval/bench/lifecycle-supervisor.py
TARGET=/home/claude-developer/hack/sherlock-v50-qualification-20260906-r3
HARNESS=/home/claude-developer/hack/sherlock-v50-harness-20260906-r9
TARGET_NONCES=/home/claude-developer/hack/sherlock-paid-admission-nonces

cd "$REPO"
test "$(sha256sum "$LIFECYCLE" | awk '{print $1}')" = 235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907
test "$(sha256sum "$REPO/measure/interactive-drive.py" | awk '{print $1}')" = c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0
test "$(readlink -f /home/claude-developer/.local/bin/qwen)" = "$QWEN"
test "$(sudo -u claude-developer env HOME="$DEV_HOME" PATH="$DEV_PATH" qwen --version)" = 0.22.0
test ! -e "$TARGET"
test ! -e "$HARNESS"

sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$PY" "$REPO/eval/bench/target-contract-probe.py" prepare \
  --root "$TARGET" \
  --source-corpus "$INPUTS/winevtx/corpus" \
  --provider-base-url https://api.neuraldeep.ru/v1 \
  --route neuraldeep \
  --secret-ref NEURALDEEP_API_KEY \
  --requested-model deepseek-v4-flash \
  --expected-returned-identity deepseek-v4-flash \
  --identity-mode alias_unresolved \
  --qwen-bin "$QWEN" \
  --arm v50 --package-version v50 \
  --rate-snapshot "$INPUTS/probe-rate-snapshot.json" \
  --operator-monitored --json | tee "$TARGET.prepare.json"
sha256sum "$TARGET/probe-manifest.json"
