#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SHERLOCK="$(CDPATH= cd -- "$HERE/../.." && pwd -P)"

die() { printf '%s\n' "$1" >&2; exit 2; }
[[ $# -eq 1 ]] || die "usage: run-harness-qualification.sh ABSOLUTE_NEW_OUTPUT_ROOT"
OUTPUT=$1
[[ "$OUTPUT" = /* ]] || die "output root must be absolute"
[[ -n "${SHERLOCK_API_KEY:-}" ]] || die "SHERLOCK_API_KEY is required"

TOOL="$HERE/harness-qualification.py"
CONTROLLER="$HERE/bench-controller.sh"
RUNNER="$HERE/run-bench.sh"
MANIFEST="$HERE/run-manifest.py"
HEALTH="$SHERLOCK/measure/probes/lane-health.sh"
SETTINGS_TOOL="$SHERLOCK/measure/corporate-settings.py"
QWEN_PATH="$(command -v qwen || true)"
PYTHON_PATH="$(command -v python3 || true)"
[[ -f "$TOOL" && -x "$CONTROLLER" && -x "$RUNNER" && -f "$MANIFEST" && -f "$SETTINGS_TOOL" ]] || die "fixed qualification tools missing"
[[ -x "$HEALTH" && -n "$QWEN_PATH" ]] || die "fixed health tool or qwen missing"
QWEN_PATH="$(python3 - "$QWEN_PATH" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1]).resolve(strict=True)
if not path.is_absolute() or not path.is_file(): raise SystemExit(2)
print(path)
PY
)" || die "qwen must resolve to a real absolute executable"

# Reject every ancestor alias, then create the fresh leaf through a held parent fd.
python3 - "$OUTPUT" <<'PY' || exit 2
import os, pathlib, stat, sys
target = pathlib.Path(sys.argv[1])
if not target.is_absolute() or os.path.lexists(target): raise SystemExit("output root must be fresh")
parts = target.parts
current = pathlib.Path(parts[0])
for part in parts[1:-1]:
    current /= part
    info = current.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit("output ancestor must be a real directory")
parent = target.parent
if parent.resolve(strict=True) != parent: raise SystemExit("output parent alias rejected")
flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(parent, flags)
try:
    os.mkdir(target.name, 0o700, dir_fd=fd)
    os.fsync(fd)
finally:
    os.close(fd)
PY

EVENTS="$OUTPUT/stage-events.jsonl"
MATRIX="$OUTPUT/fault-matrix.json"
CORPUS="$OUTPUT/generated-probe-corpus"
RUNS="$OUTPUT/runs"
CONTROLLER_ROOT="$OUTPUT/controller"
SUITES="$OUTPUT/suite-results"
ANSWER="$OUTPUT/answer-key.json"
PROMPT="$OUTPUT/prompt.txt"
TOOL_SCHEMA="$OUTPUT/tool-schema.json"
ARM="$OUTPUT/arm.json"
RENDERER="$OUTPUT/render-corpus.py"
TARGET_PROFILE="$OUTPUT/target-profile.json"
FREE_TEST="$OUTPUT/run-provider-free-tests.sh"
SETTINGS="$OUTPUT/corporate-settings.json"
BUDGET="$OUTPUT/probe-budget.json"
INPUT_PACKAGE="$OUTPUT/input-package.json"
TARGET_HOME="$OUTPUT/target-home"
mkdir -m 700 -- "$CORPUS" "$RUNS" "$SUITES" "$TARGET_HOME"

REPO_ROOT="$(git -C "$SHERLOCK" rev-parse --show-toplevel)" || die "repository root unavailable"
COMMIT="$(git -C "$SHERLOCK" rev-parse HEAD)" || die "repository identity unavailable"
TREE="$(git -C "$SHERLOCK" rev-parse HEAD^{tree})" || die "repository tree unavailable"
DIRTY="$(git -C "$SHERLOCK" status --porcelain --untracked-files=no)"
printf '%s\n' "$COMMIT" > "$OUTPUT/implementation-commit.txt"
printf '%s\n' "$DIRTY" > "$OUTPUT/implementation-dirty.txt"
"$QWEN_PATH" --version > "$OUTPUT/qwen-version.txt" || die "qwen version unavailable"
python3 "$SETTINGS_TOOL" emit-run --window 262000 --max-tokens 32000 \
  --session-token-limit 230000 --timeout 900000 --max-retries 0 \
  --skill-directory "$TARGET_HOME/.qwen/skills" --exclude-tool agent \
  --no-auto-compact > "$SETTINGS" || die "corporate settings generation failed"
python3 - "$CORPUS" "$ANSWER" "$PROMPT" "$TOOL_SCHEMA" "$ARM" "$RENDERER" "$TARGET_PROFILE" "$COMMIT" "$TREE" "$SHERLOCK" "$SETTINGS" "$BUDGET" "$INPUT_PACKAGE" "$QWEN_PATH" <<'PY'
import hashlib, json, os, pathlib, stat, sys
corpus, answer, prompt, schema, arm, renderer, profile = map(pathlib.Path, sys.argv[1:8])
commit, tree, sherlock_text = sys.argv[8:11]; sherlock = pathlib.Path(sherlock_text)
settings, budget, package, qwen = map(pathlib.Path, sys.argv[11:15])
event = {"Event":{"System":{
    "Provider":{"#attributes":{"Name":"Service Control Manager"}},
    "EventID":{"#attributes":{"Qualifiers":16384},"#text":7045},
    "TimeCreated":{"#attributes":{"SystemTime":"2026-09-04T00:00:00Z"}},
    "Security":{"#attributes":{"UserID":"S-1-5-18"}}},
    "EventData":{"ServiceName":"SherlockQualificationHealthy",
                 "ImagePath":"C:\\Windows\\System32\\svchost.exe -k qualification"}}}
data = (json.dumps(event,sort_keys=True,separators=(",",":"))+"\n").encode()
sample = corpus / "System.jsonl"; sample.write_bytes(data)
key = {"dataset":"harness-qualification","files":[{"path":sample.name,
       "on_disk_bytes":len(data),"lines":data.count(b'\n'),
       "sha256":hashlib.sha256(data).hexdigest()}],
       "defects":[{"id":"HQ-001","proof_locations":[{"file":sample.name,
       "line_start":1,"line_end":1}]}]}
answer.write_text(json.dumps(key,sort_keys=True,separators=(",",":"))+"\n")
prompt.write_text("Investigate the bounded local corpus and produce the v44 report.\n")
tool_rows=[]
for path in sorted((sherlock/'skills/v44/tools').rglob('*')):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode): raise SystemExit('tool schema tree contains symlink')
    if stat.S_ISDIR(info.st_mode): continue
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise SystemExit('unsafe tool schema file')
    raw=path.read_bytes()
    tool_rows.append({"path":path.relative_to(sherlock/'skills/v44/tools').as_posix(),
                      "bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
if not tool_rows or len(tool_rows) > 4096: raise SystemExit('tool schema tree is empty or too large')
schema.write_text(json.dumps({"schema":1,"files":tool_rows},sort_keys=True,separators=(",",":"))+"\n")
arm.write_text(json.dumps({"schema":1,"arm":"v44","commit":commit,"tree":tree},
                          sort_keys=True,separators=(",",":"))+"\n")
renderer.write_text("#!/usr/bin/env python3\nimport pathlib,sys\nsys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())\n")
renderer.chmod(0o700)
sha=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
gates={name:sha(sherlock/'skills/v44/tools'/file) for name,file in {
       'reportcheck':'reportcheck.py','citecheck':'citecheck.py',
       'statecheck':'statecheck.py','triagecheck':'triagecheck.py'}.items()}
profile_row={"schema":1,"provider_base_url":"http://127.0.0.1:8317/v1",
 "route":"/chat/completions","secret_ref":"SHERLOCK_API_KEY",
 "requested_model":"gpt-5.5","expected_returned_identity":"gpt-5.5",
 "identity_mode":"provider_pinned_version","temperature":0,"top_p":1,
 "max_output_tokens":32000,"session_token_limit":230000,"cache":{"enabled":True},
 "interactive":{"enabled":True},"qwen":{"cli":str(qwen)},"limits":{"requests":512},
 "settings_sha256":sha(settings),"system_prompt_sha256":sha(prompt),
 "skill_sha256":sha(sherlock/'skills/v44/SKILL.md'),"tool_schema_sha256":sha(schema),
 "gate_sha256":gates,"lane_guard":{"enabled":True}}
profile.write_text(json.dumps(profile_row,sort_keys=True,separators=(",",":"))+"\n")
budget_row={"schema":1,"max_upstream_attempts":512,"max_request_bytes":536870912,
            "max_wall_seconds":4500,"max_consecutive_provider_failures":3,
            "context_window":262000,"max_output_tokens":32000,
            "session_token_limit":230000,"request_timeout_ms":900000}
budget.write_text(json.dumps(budget_row,sort_keys=True,separators=(",",":"))+"\n")
package_row={"schema":1,"arm":"v44","implementation_commit":commit,
             "implementation_tree":tree,"corpus_sha256":sha(sample),
             "answer_key_sha256":sha(answer),"prompt_sha256":sha(prompt),
             "settings_sha256":sha(settings),"tool_schema_sha256":sha(schema),
             "target_profile_sha256":sha(profile),"probe_budget_sha256":sha(budget),
             "qwen_sha256":sha(qwen),"gate_sha256":gates}
package.write_text(json.dumps(package_row,sort_keys=True,separators=(",",":"))+"\n")
PY

cat > "$FREE_TEST" <<EOF
#!/usr/bin/env bash
set -euo pipefail
OUT=$(printf '%q' "$SUITES")
ROOT=$(printf '%q' "$SHERLOCK")
REPO=$(printf '%q' "$REPO_ROOT")
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="\$REPO"
python3 "\$ROOT/tools/tests/test_run_manifest.py" >"\$OUT/run-manifest.out" 2>&1
python3 "\$ROOT/tools/tests/test_run_state.py" >"\$OUT/run-state.out" 2>&1
python3 "\$ROOT/tools/tests/test_run_verdict.py" >"\$OUT/run-verdict.out" 2>&1
python3 - "\$OUT" "$OUTPUT/provider-free-tests.json" <<'PY'
import hashlib, json, pathlib, sys
root, target = map(pathlib.Path, sys.argv[1:])
suites=[]
for path in sorted(root.glob("*.out")):
    suites.append({"name":path.stem,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"exit_code":0})
target.write_text(json.dumps({"schema":1,"provider_free":True,"failed":0,"suites":suites},sort_keys=True,separators=(",",":"))+"\n")
PY
EOF
chmod 700 "$FREE_TEST"

event() {
  python3 - "$EVENTS" "$1" <<'PY'
import json, pathlib, sys
with pathlib.Path(sys.argv[1]).open("a") as handle:
    handle.write(json.dumps({"schema":1,"stage":sys.argv[2]},sort_keys=True,separators=(",",":"))+"\n")
PY
}

event matrix
python3 "$TOOL" matrix --output "$MATRIX" >/dev/null

event controller
CONTROLLER_ENV=(
  "PATH=$(dirname "$PYTHON_PATH"):/usr/bin:/bin:/usr/sbin:/sbin" "HOME=$TARGET_HOME" "SHERLOCK_API_KEY=$SHERLOCK_API_KEY"
  "GIT_CONFIG_COUNT=1" "GIT_CONFIG_KEY_0=safe.directory" "GIT_CONFIG_VALUE_0=$REPO_ROOT"
  "SHERLOCK_CONTROLLER_ROOT=$CONTROLLER_ROOT" "BENCH_RUNS=$RUNS"
  "SHERLOCK_FREE_TEST_COMMAND=$(printf '%q' "$FREE_TEST")"
  "SHERLOCK_HEALTH_COMMAND=$(printf '%q' "$HEALTH")"
  "SHERLOCK_TARGET_COMMAND=$(printf '%q' "$RUNNER") v44"
  "SHERLOCK_MANIFEST_TOOL=$MANIFEST" "SHERLOCK_CORPUS=$CORPUS"
  "SHERLOCK_ANSWER_KEY=$ANSWER" "SHERLOCK_DATASET=harness-qualification"
  "SHERLOCK_RENDERER=$RENDERER" "SHERLOCK_TARGET_PROFILE=$TARGET_PROFILE"
  "SHERLOCK_SETTINGS=$SETTINGS" "SHERLOCK_INPUT_PACKAGE=$INPUT_PACKAGE"
  "SHERLOCK_PROBE_BUDGET=$BUDGET"
  "SHERLOCK_ARM=v44" "SHERLOCK_PROMPT_FILE=$PROMPT"
  "SHERLOCK_SKILL_ROOT=$SHERLOCK/skills/v44" "SHERLOCK_SCORER=$HERE/score-bench.py"
  "SHERLOCK_REPORT_CHECKER=$SHERLOCK/skills/v44/tools/reportcheck.py"
  "SHERLOCK_STATE_CHECKER=$SHERLOCK/skills/v44/tools/statecheck.py"
  "SHERLOCK_TRIAGE_CHECKER=$SHERLOCK/skills/v44/tools/triagecheck.py"
  "SHERLOCK_STOP_CHECKER=$SHERLOCK/skills/v44/tools/stopcheck.py"
  "SHERLOCK_CITATION_CHECKER=$SHERLOCK/skills/v44/tools/citecheck.py"
  "SHERLOCK_BASE_URL=http://127.0.0.1:8317/v1" "SHERLOCK_MODEL=gpt-5.5"
  "SHERLOCK_EXPECTED_RETURNED_IDENTITY=gpt-5.5" "SHERLOCK_PROVIDER=cliproxyapi"
  "SHERLOCK_LANE=subscription" "SHERLOCK_TARGET_VERSION=$(tr -d '\r\n' < "$OUTPUT/qwen-version.txt")"
  "QWEN_BIN=$QWEN_PATH" "SHERLOCK_CONTEXT_WINDOW=262000" "SHERLOCK_MAX_OUTPUT_TOKENS=32000"
  "SHERLOCK_GENERATION_WINDOW_S=3600" "SHERLOCK_OUTPUT_TOKENS_PER_S=20"
  "SHERLOCK_TTFT_RESERVE_S=120" "SHERLOCK_SESSION_TOKEN_LIMIT=230000"
  "SHERLOCK_REQUEST_TIMEOUT_MS=900000" "SHERLOCK_TIMEOUT=4200"
  "SHERLOCK_MAX_RETRIES=0" "SHERLOCK_RESUME_MAX_ATTEMPTS=0"
  "SHERLOCK_ALLOW_SUBAGENT=0" "SHERLOCK_TARGET_AUTOCOMPACT=0"
  "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS=512" "SHERLOCK_BUDGET_MAX_REQUEST_BYTES=536870912"
  "SHERLOCK_BUDGET_MAX_WALL_SECONDS=4500" "SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES=3"
)
env -i "${CONTROLLER_ENV[@]}" "$CONTROLLER"

TRACE="$(python3 - "$RUNS" <<'PY'
import pathlib, stat, sys
root=pathlib.Path(sys.argv[1]); found=[]
for path in root.iterdir():
    mode=path.lstat().st_mode
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode): found.append(path)
if len(found) != 1: raise SystemExit("expected exactly one controller trace")
print(found[0])
PY
)" || die "controller did not produce exactly one trace"

python3 - "$OUTPUT/harness-qualification-input.json" "$TRACE" "$MATRIX" "$QWEN_PATH" <<'PY'
import json, os, pathlib, sys
target, trace, matrix, qwen = map(pathlib.Path, sys.argv[1:])
row={"schema":2,"free_run_id":trace.name,"trace":str(trace),"matrix":str(matrix),
     "qwen_binary":str(qwen),"free_model_observations":{"requested":"gpt-5.5",
     "sent":"gpt-5.5","returned":["gpt-5.5"]}}
data=json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()+b"\n"
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"wb") as handle: handle.write(data); handle.flush(); os.fsync(handle.fileno())
parent=os.open(target.parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
try: os.fsync(parent)
finally: os.close(parent)
PY

event audit
python3 "$TOOL" audit --trace "$TRACE" --matrix "$MATRIX" --output "$OUTPUT/harness-acceptance.json" >/dev/null
