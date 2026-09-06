set -euo pipefail
REPO=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock
INPUTS=/home/claude-developer/hack/sherlock-final-inputs-20260906/independent
PY=/run/current-system/sw/bin/python3
QWEN=/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js
DEV_HOME=/home/claude-developer
DEV_PATH=/home/claude-developer/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin
WITH_SECRET=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
LIFECYCLE=$REPO/eval/bench/lifecycle-supervisor.py
TARGET=/home/claude-developer/hack/sherlock-v49-independent-qualification-20260906-r1
HARNESS=/home/claude-developer/hack/sherlock-v49-harness-20260906-r1
TARGET_NONCES=/home/claude-developer/hack/sherlock-paid-admission-nonces

cd "$REPO"
test "$(sha256sum "$LIFECYCLE" | awk '{print $1}')" = 0d6aacbb7ed0d5e2f665113154a79122b5e9f9072cc0ec721bc436c1da44c0ee
test "$(sha256sum "$REPO/measure/interactive-drive.py" | awk '{print $1}')" = 6095a501e5b7f338f82276eb309624c165ad108027c332a022592f89bcd83717
test "$(readlink -f /home/claude-developer/.local/bin/qwen)" = "$QWEN"
test "$(sudo -u claude-developer env HOME="$DEV_HOME" PATH="$DEV_PATH" qwen --version)" = 0.22.0
test ! -e "$TARGET"

sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$PY" "$REPO/eval/bench/target-contract-probe.py" prepare \
  --root "$TARGET" \
  --source-corpus "${INPUTS%/independent}/winevtx/corpus" \
  --provider-base-url https://api.neuraldeep.ru/v1 \
  --route neuraldeep \
  --secret-ref NEURALDEEP_API_KEY \
  --requested-model deepseek-v4-flash \
  --expected-returned-identity deepseek-v4-flash \
  --identity-mode alias_unresolved \
  --qwen-bin "$QWEN" \
  --arm v49 --package-version v49 \
  --rate-snapshot "${INPUTS%/independent}/probe-rate-snapshot.json" \
  --operator-monitored --json | tee "$TARGET.prepare.json"
sha256sum "$TARGET/probe-manifest.json"
