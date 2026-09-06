set -euo pipefail
REPO=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock
INPUTS=/home/claude-developer/hack/sherlock-final-inputs-20260906
PY=/run/current-system/sw/bin/python3
QWEN=/home/claude-developer/.local/lib/node_modules/@qwen-code/qwen-code/cli-entry.js
DEV_HOME=/home/claude-developer
DEV_PATH=/home/claude-developer/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin
WITH_SECRET=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
LIFECYCLE=$REPO/eval/bench/lifecycle-supervisor.py
TARGET=/home/claude-developer/hack/sherlock-v50-qualification-20260906-r5
HARNESS=/home/claude-developer/hack/sherlock-v50-harness-20260906-r11
TARGET_NONCES=/home/claude-developer/hack/sherlock-paid-admission-nonces
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cd "$REPO"
test "$(sha256sum "$LIFECYCLE" | awk '{print $1}')" = 235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907
test "$(sha256sum "$REPO/measure/interactive-drive.py" | awk '{print $1}')" = c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0
test "$(readlink -f /home/claude-developer/.local/bin/qwen)" = "$QWEN"
test "$(sudo -u claude-developer env HOME="$DEV_HOME" PATH="$DEV_PATH" qwen --version)" = 0.22.0
test -s "$TARGET/probe-manifest.json"
test -s "$HARNESS/harness-acceptance.json"


ADMISSION=/home/claude-developer/hack/sherlock-v50-winevtx-admission-20260906-r2
FULL=/home/claude-developer/hack/sherlock-v50-winevtx-full-20260906-r2
TARGET_TRACE="$TARGET/probe-work/runs/target-contract-probe"
test ! -e "$ADMISSION"
test ! -e "$FULL"

sudo -u claude-developer mkdir -m 700 "$ADMISSION"
sudo -u claude-developer cp "$HARNESS/harness-acceptance.json" "$ADMISSION/harness-acceptance.json"
sudo -u claude-developer cp "$TARGET_TRACE/target-contract-receipt.json" "$ADMISSION/target-contract-receipt.json"
sudo -u claude-developer cp "$TARGET_TRACE/target-contract-receipt.json.sha256" "$ADMISSION/target-contract-receipt.json.sha256"
sudo -u claude-developer cp "$TARGET/target-profile.json" "$ADMISSION/target-profile.json"
sudo -u claude-developer cp "$TARGET/corporate-settings.json" "$ADMISSION/corporate-settings.json"
sudo -u claude-developer cp "$HARNESS/probe-budget.json" "$ADMISSION/full-run-budget.json"
sudo -u claude-developer chmod 600 "$ADMISSION"/*

sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$PY" - "$INPUTS/winevtx" "$TARGET/target-profile.json" "$LIFECYCLE" \
  "$REPO/measure/interactive-drive.py" "$ADMISSION/full-input-package.json" <<'PY'
import hashlib,json,os,pathlib,sys
source,profile_path,helper_path,driver_path,out=map(pathlib.Path,sys.argv[1:])
profile=json.loads(profile_path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def files(root):
    return [{"path":str(path.relative_to(root)),"bytes":path.stat().st_size,"sha256":sha(path)}
            for path in sorted(root.rglob('*')) if path.is_file() and not path.is_symlink()]
row={
  "schema":1,
  "dataset":"winevtx-final",
  "package_version":profile["package_version"],
  "package_sha256":profile["package_sha256"],
  "lifecycle_helper_sha256":sha(helper_path),
  "interactive_driver_sha256":sha(driver_path),
  "corpus":{"inventory_sha256":sha(source/"inventory-key.json"),
            "files":files(source/"corpus")},
  "prompt":{"sha256":sha(source/"prompt.txt")},
  "provenance":{"separate_from_corpus":True,"files":files(source/"provenance")},
  "cold_start":{"prior_findings":False,"prior_worklists":False,
                "prior_checkpoints":False,"prior_pattern_cards":False},
  "comparison":{"status":"not_requested","undeclared_differences":[]},
}
data=(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'wb') as handle:
    handle.write(data); handle.flush(); os.fsync(handle.fileno())
PY

sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$PY" "$REPO/eval/bench/paid-admission.py" prepare \
  --root "$ADMISSION" --accept-alias-identity-risk --json \
  | tee "$ADMISSION/prepare-result.json"

PAID_MANIFEST="$ADMISSION/paid-admission-manifest.json"
PAID_APPROVAL=$(sha256sum "$PAID_MANIFEST" | awk '{print $1}')
test "$PAID_APPROVAL" = "$($PY - "$ADMISSION/prepare-result.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8'))['manifest_sha256'])
PY
)"
printf 'full paid approval sha256: %s\n' "$PAID_APPROVAL"

RUN_ROOT="$FULL"
export RUN_ROOT
. "$SCRIPT_DIR/v50-owned-review-monitor-r1.sh"
review_monitor_start

sudo -u claude-developer mkdir -m 700 "$FULL" "$FULL/controller" "$FULL/runs" "$FULL/home"
read -r CONTEXT MAXOUT SESSION REQUEST_MS <<EOF
$($PY - "$ADMISSION/full-run-budget.json" "$ADMISSION/target-profile.json" <<'PY'
import json,sys
budget=json.load(open(sys.argv[1],encoding='utf-8'))
profile=json.load(open(sys.argv[2],encoding='utf-8'))
assert budget['schema']==2 and budget['execution_mode']=='operator_monitored'
assert budget['max_output_tokens']==profile['max_output_tokens']
assert budget['session_token_limit']==profile['session_token_limit']
print(budget['context_window'],profile['max_output_tokens'],profile['session_token_limit'],budget['request_timeout_ms'])
PY
)
EOF
test "$REQUEST_MS" = 600000
FREE_TEST_COMMAND="$PY $REPO/tools/tests/test_run_manifest.py && $PY $REPO/tools/tests/test_run_state.py && $PY $REPO/tools/tests/test_run_verdict.py"

if sudo -u claude-developer env -i \
  HOME="$DEV_HOME" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "$WITH_SECRET" neuraldeep_api_key --env SHERLOCK_API_KEY -- \
  env \
    HOME="$FULL/home" PATH="$DEV_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory \
    GIT_CONFIG_VALUE_0=/home/claude-developer/hack/wt-v42 \
    SHERLOCK_CONTROLLER_ROOT="$FULL/controller" \
    BENCH_RUNS="$FULL/runs" \
    SHERLOCK_LEDGER="$FULL/quality.jsonl" \
    SHERLOCK_FREE_TEST_COMMAND="$FREE_TEST_COMMAND" \
    SHERLOCK_HEALTH_COMMAND="$REPO/measure/probes/lane-health.sh" \
    SHERLOCK_TARGET_COMMAND="$REPO/eval/bench/run-bench.sh v50" \
    SHERLOCK_MANIFEST_TOOL="$REPO/eval/bench/run-manifest.py" \
    SHERLOCK_PAID_ADMISSION_TOOL="$REPO/eval/bench/paid-admission.py" \
    SHERLOCK_PAID_ADMISSION_MANIFEST="$PAID_MANIFEST" \
    SHERLOCK_OPERATOR_APPROVED_FULL="$PAID_APPROVAL" \
    SHERLOCK_RUN_BUDGET="$ADMISSION/full-run-budget.json" \
    SHERLOCK_OPERATOR_MONITORED_MODE=1 SHERLOCK_TIMEOUT=0 \
    SHERLOCK_CORPUS="$INPUTS/winevtx/corpus" \
    SHERLOCK_ANSWER_KEY="$INPUTS/winevtx/inventory-key.json" \
    SHERLOCK_DATASET=winevtx-final \
    SHERLOCK_RENDERER="$REPO/skills/v50/tools/ingest.py" \
    SHERLOCK_ARM=v50 SHERLOCK_PACKAGE_VERSION=v50 \
    SHERLOCK_PROMPT_FILE="$INPUTS/winevtx/prompt.txt" \
    SHERLOCK_SKILL_ROOT="$TARGET/runtime-package" \
    SHERLOCK_SCORER="$REPO/eval/bench/score-bench.py" \
    SHERLOCK_REPORT_CHECKER="$TARGET/runtime-package/tools/reportcheck.py" \
    SHERLOCK_STATE_CHECKER="$TARGET/runtime-package/tools/statecheck.py" \
    SHERLOCK_TRIAGE_CHECKER="$TARGET/runtime-package/tools/triagecheck.py" \
    SHERLOCK_STOP_CHECKER="$TARGET/runtime-package/tools/stopcheck.py" \
    SHERLOCK_CITATION_CHECKER="$TARGET/runtime-package/tools/citecheck.py" \
    QWEN_BIN="$QWEN" SHERLOCK_TARGET_VERSION=0.22.0 \
    SHERLOCK_CONTEXT_WINDOW="$CONTEXT" \
    SHERLOCK_MAX_OUTPUT_TOKENS="$MAXOUT" \
    SHERLOCK_SESSION_TOKEN_LIMIT="$SESSION" \
    SHERLOCK_REQUEST_TIMEOUT_MS="$REQUEST_MS" \
    SHERLOCK_GENERATION_WINDOW_S=3600 SHERLOCK_OUTPUT_TOKENS_PER_S=20 \
    SHERLOCK_TTFT_RESERVE_S=120 SHERLOCK_MAX_RETRIES=0 \
    SHERLOCK_RESUME_MAX_ATTEMPTS=0 SHERLOCK_ALLOW_SUBAGENT=0 \
    SHERLOCK_TARGET_AUTOCOMPACT=0 SHERLOCK_INTERACTIVE=1 \
    SHERLOCK_MAX_SESSION_TURNS=-1 SHERLOCK_MAX_TOOL_CALLS=-1 \
    SHERLOCK_MAX_WALL_TIME_S=-1 SHERLOCK_WORKFLOW_AGENT_MAX_TURNS=200 \
    SHERLOCK_CACHE_GUARD=0 SHERLOCK_UPSTREAM_RETRY=0 \
    SHERLOCK_SUBSTITUTION_RETRY=0 \
  bash "$REPO/eval/bench/bench-controller.sh" >"$FULL.launch.stdout" 2>"$FULL.launch.stderr"; then
  CONTROLLER_RC=0
else
  CONTROLLER_RC=$?
fi
review_monitor_finish "$CONTROLLER_RC"
