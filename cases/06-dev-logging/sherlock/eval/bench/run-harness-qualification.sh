#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SHERLOCK="$(CDPATH= cd -- "$HERE/../.." && pwd -P)"

die() { printf '%s\n' "$1" >&2; exit 2; }

[[ $# -eq 1 ]] || die "usage: run-harness-qualification.sh ABSOLUTE_NEW_OUTPUT_ROOT"
OUTPUT=$1
[[ "$OUTPUT" = /* ]] || die "output root must be absolute"
[[ -n "${SHERLOCK_API_KEY:-}" ]] || die "SHERLOCK_API_KEY is required"
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || die "output root must not exist"
PARENT=$(dirname -- "$OUTPUT")
[[ -d "$PARENT" && ! -L "$PARENT" ]] || die "output parent must be a real directory"

TOOL="$HERE/harness-qualification.py"
[[ -f "$TOOL" ]] || die "qualification tool missing"
TEST_MODE=${SHERLOCK_HARNESS_TEST_MODE:-0}
if [[ "$TEST_MODE" == 1 ]]; then
  CONTROLLER=${SHERLOCK_HARNESS_CONTROLLER:?test controller required}
  AUDITOR=${SHERLOCK_HARNESS_AUDITOR:?test auditor required}
  [[ "$CONTROLLER" = /* && -x "$CONTROLLER" && "$AUDITOR" = /* && -x "$AUDITOR" ]] || die "invalid test stand-in"
else
  [[ -z "${SHERLOCK_HARNESS_CONTROLLER:-}" && -z "${SHERLOCK_HARNESS_AUDITOR:-}" ]] || die "stand-ins require explicit test mode"
  CONTROLLER="$HERE/bench-controller.sh"
  AUDITOR="$TOOL"
fi

mkdir -m 700 -- "$OUTPUT"
EVENTS="$OUTPUT/stage-events.jsonl"
MATRIX="$OUTPUT/fault-matrix.json"
CORPUS="$OUTPUT/generated-probe-corpus"
RUNS="$OUTPUT/runs"
mkdir -m 700 -- "$CORPUS" "$RUNS"
python3 - "$CORPUS" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
rows = [
    {"id":"probe-001","prompt":"Summarize the supplied local fixture.","expected":"fixture"},
    {"id":"probe-002","prompt":"Return the cited local identifier.","expected":"probe-002"},
]
(root / "probes.jsonl").write_text("".join(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n" for x in rows))
PY

export SHERLOCK_BASE_URL="http://127.0.0.1:8317/v1"
export SHERLOCK_MODEL="gpt-5.5"
export SHERLOCK_LANE="subscription"
export SHERLOCK_PROVIDER="cliproxyapi"
export SHERLOCK_SKILL_ROOT="$SHERLOCK/skills/v44"
export SHERLOCK_REPORT_GATE="$SHERLOCK/skills/v44/tools/reportcheck.py"
export SHERLOCK_CITATION_GATE="$SHERLOCK/skills/v44/tools/citecheck.py"
export SHERLOCK_STATE_GATE="$SHERLOCK/skills/v44/tools/statecheck.py"
export SHERLOCK_TRIAGE_GATE="$SHERLOCK/skills/v44/tools/triagecheck.py"
export SHERLOCK_CORPUS="$CORPUS"
export BENCH_RUNS="$RUNS"

python3 - "$OUTPUT/controller-environment.json" "$SHERLOCK" "$CORPUS" <<'PY'
import json, pathlib, sys
out, root, corpus = map(pathlib.Path, sys.argv[1:])
row = {
 "SHERLOCK_BASE_URL":"http://127.0.0.1:8317/v1", "SHERLOCK_MODEL":"gpt-5.5",
 "SHERLOCK_LANE":"subscription", "SHERLOCK_PROVIDER":"cliproxyapi",
 "SHERLOCK_SKILL_ROOT":str(root / "skills/v44"),
 "SHERLOCK_REPORT_GATE":str(root / "skills/v44/tools/reportcheck.py"),
 "SHERLOCK_CITATION_GATE":str(root / "skills/v44/tools/citecheck.py"),
 "SHERLOCK_STATE_GATE":str(root / "skills/v44/tools/statecheck.py"),
 "SHERLOCK_TRIAGE_GATE":str(root / "skills/v44/tools/triagecheck.py"),
 "SHERLOCK_CORPUS":str(corpus), "SHERLOCK_API_KEY_PRESENT":True,
}
out.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
PY

event() {
  python3 - "$EVENTS" "$1" <<'PY'
import json, pathlib, sys
with pathlib.Path(sys.argv[1]).open("a") as handle:
    handle.write(json.dumps({"schema":1,"stage":sys.argv[2]}, sort_keys=True, separators=(",", ":")) + "\n")
PY
}

event matrix
if [[ -n "${SHERLOCK_HARNESS_FIXTURES:-}" ]]; then
  python3 "$TOOL" matrix --fixtures "$SHERLOCK_HARNESS_FIXTURES" --output "$MATRIX" >/dev/null
else
  python3 "$TOOL" matrix --output "$MATRIX" >/dev/null
fi

event controller
if [[ "$TEST_MODE" == 1 ]]; then
  STAGE=controller "$CONTROLLER"
else
  "$CONTROLLER"
fi

event audit
if [[ "$TEST_MODE" == 1 ]]; then
  STAGE=audit "$AUDITOR"
else
  TRACE=${SHERLOCK_HARNESS_TRACE:-}
  if [[ -z "$TRACE" ]]; then
    TRACE=$(python3 - "$RUNS" <<'PY'
import os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1])
found = []
for path in root.iterdir():
    try:
        mode = path.lstat().st_mode
    except OSError:
        continue
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode) and (path / "harness-qualification-input.json").is_file():
        found.append(path)
if len(found) != 1:
    raise SystemExit("expected exactly one controller qualification trace")
print(found[0])
PY
)
  fi
  [[ "$TRACE" = /* && -d "$TRACE" && ! -L "$TRACE" ]] || die "controller did not supply an absolute trace"
  python3 "$AUDITOR" audit --trace "$TRACE" --matrix "$MATRIX" --output "$OUTPUT/harness-acceptance.json" >/dev/null
fi
