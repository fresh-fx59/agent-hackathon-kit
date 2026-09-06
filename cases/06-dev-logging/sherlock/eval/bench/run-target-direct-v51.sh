#!/usr/bin/env bash
# Fresh, monitor-free corporate Qwen target-contract experiment.
# Authority: docs/2026-09-06-dedicated-review-monitor-spec.md explicitly limits
# the subscription reviewer to its subscription qualification lane.  This keeps
# target-contract-probe's sealed controller, lifecycle, process ownership,
# watchdog, captures, receipt, model-identity and nonce gates intact.
set -euo pipefail

: "${SHERLOCK_TARGET_ROOT:?fresh absolute target root required}"
: "${SHERLOCK_TARGET_NONCES:?fresh absolute nonce root required}"
: "${SHERLOCK_SOURCE_CORPUS:?fresh source corpus required}"
: "${SHERLOCK_RATE_SNAPSHOT:?fresh rate snapshot required}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/.." && pwd -P)"
PY="${PYTHON_BIN:-python3}"
QWEN_BIN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
WITH_SECRET="${WITH_SECRET:?sanctioned with-secret.sh path required}"
MODEL="${SHERLOCK_MODEL:-deepseek-v4-flash}"
EXPECTED_IDENTITY="${SHERLOCK_EXPECTED_IDENTITY:-$MODEL}"
IDENTITY_MODE="${SHERLOCK_IDENTITY_MODE:-alias_unresolved}"
PROVIDER_BASE_URL="${SHERLOCK_PROVIDER_BASE_URL:?corporate provider base URL required}"
ROUTE="${SHERLOCK_ROUTE:-/v1/chat/completions}"

case "$SHERLOCK_TARGET_ROOT" in /*) ;; *) echo 'target root must be absolute' >&2; exit 2;; esac
case "$SHERLOCK_TARGET_NONCES" in /*) ;; *) echo 'nonce root must be absolute' >&2; exit 2;; esac
test ! -e "$SHERLOCK_TARGET_ROOT" || { echo 'target root must be fresh' >&2; exit 2; }
test -d "$SHERLOCK_SOURCE_CORPUS" && test -f "$SHERLOCK_RATE_SNAPSHOT"
test -x "$QWEN_BIN" && test -x "$WITH_SECRET"

"$PY" "$HERE/target-contract-probe.py" prepare \
  --root "$SHERLOCK_TARGET_ROOT" --source-corpus "$SHERLOCK_SOURCE_CORPUS" \
  --provider-base-url "$PROVIDER_BASE_URL" --route "$ROUTE" \
  --secret-ref NEURALDEEP_API_KEY --requested-model "$MODEL" \
  --expected-returned-identity "$EXPECTED_IDENTITY" --identity-mode "$IDENTITY_MODE" \
  --qwen-bin "$QWEN_BIN" --arm v51 --package-version v51 \
  --rate-snapshot "$SHERLOCK_RATE_SNAPSHOT" --operator-monitored --json

MANIFEST="$SHERLOCK_TARGET_ROOT/probe-manifest.json"
APPROVAL="$(sha256sum "$MANIFEST" | awk '{print $1}')"
"$PY" "$HERE/target-contract-probe.py" authorize --manifest "$MANIFEST" \
  --operator-approved-probe "$APPROVAL" --nonce-root "$SHERLOCK_TARGET_NONCES"
"$PY" "$HERE/target-contract-probe.py" verify-launch --sealed-input "$SHERLOCK_TARGET_ROOT" \
  --operator-approved-probe "$APPROVAL" --nonce-root "$SHERLOCK_TARGET_NONCES" --record-start
"$WITH_SECRET" neuraldeep_api_key --env NEURALDEEP_API_KEY -- \
  "$PY" "$HERE/target-contract-probe.py" run --manifest "$MANIFEST" \
  --operator-approved-probe "$APPROVAL" --nonce-root "$SHERLOCK_TARGET_NONCES" --json
