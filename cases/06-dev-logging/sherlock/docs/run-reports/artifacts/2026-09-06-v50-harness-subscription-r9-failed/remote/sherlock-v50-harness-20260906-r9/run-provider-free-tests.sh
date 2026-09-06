#!/usr/bin/env bash
set -euo pipefail
OUT=/home/claude-developer/hack/sherlock-v50-harness-20260906-r9/suite-results
ROOT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock
REPO=/home/claude-developer/hack/wt-v42
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="$REPO"
python3 "$ROOT/tools/tests/test_run_manifest.py" >"$OUT/run-manifest.out" 2>&1
python3 "$ROOT/tools/tests/test_run_state.py" >"$OUT/run-state.out" 2>&1
python3 "$ROOT/tools/tests/test_run_verdict.py" >"$OUT/run-verdict.out" 2>&1
python3 - "$OUT" "/home/claude-developer/hack/sherlock-v50-harness-20260906-r9/provider-free-tests.json" <<'PY'
import hashlib, json, pathlib, sys
root, target = map(pathlib.Path, sys.argv[1:])
suites=[]
for path in sorted(root.glob("*.out")):
    suites.append({"name":path.stem,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"exit_code":0})
target.write_text(json.dumps({"schema":1,"provider_free":True,"failed":0,"suites":suites},sort_keys=True,separators=(",",":"))+"\n")
PY
