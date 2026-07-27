#!/usr/bin/env bash
# gate.sh -- universal eval gate over a case benchmark.
#
# Usage:
#   ci/gate.sh <case-dir> <min-score> [artifact-path]
#
# Two modes:
#   * WITHOUT an artifact the gate checks the HARNESS ONLY: it runs
#     <case-dir>/benchmark.py --self-test and passes iff the self-test
#     passes.  There is no agent artifact to score, so <min-score> is
#     validated but NOT applied -- the gate says so explicitly.
#   * WITH an artifact it runs
#         python3 <case-dir>/benchmark.py <artifact> --score-only
#     prints the stable line
#         benchmark score: <N> (min <MIN>)
#     and exits 0 when N >= MIN, 1 otherwise (float comparison via python3).
#
# <min-score> uses the SAME SCALE as the case benchmark prints with
# --score-only (0-100 for the analytics rubric, 0-1 for F1/recall cases).
#
# Usable locally, from a git hook, or from Jenkins (see ci/Jenkinsfile).
# Works from any cwd -- paths resolve relative to where you invoke it.
# Needs only bash + coreutils + python3.
set -u

usage() {
  cat >&2 <<'EOF'
usage: ci/gate.sh <case-dir> <min-score> [artifact-path]

  <case-dir>       a case directory containing benchmark.py
                   (e.g. cases/analytics-meeting)
  <min-score>      minimal passing score, same scale as the benchmark's
                   --score-only output (e.g. 80 for rubric, 0.8 for F1)
  [artifact-path]  the agent-produced artifact to score; when omitted the
                   gate runs benchmark.py --self-test (harness-only check)

exit codes: 0 = gate passed, 1 = gate failed, 2 = usage / setup error
EOF
  exit 2
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  usage
fi

CASE_DIR="$1"
MIN_SCORE="$2"
ARTIFACT="${3-}"
BENCHMARK="$CASE_DIR/benchmark.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "gate: error: python3 not found on PATH" >&2
  exit 2
fi
if [ ! -d "$CASE_DIR" ]; then
  echo "gate: error: case dir not found: $CASE_DIR" >&2
  exit 2
fi
if [ ! -f "$BENCHMARK" ]; then
  echo "gate: error: no benchmark.py in case dir: $BENCHMARK" >&2
  exit 2
fi
if ! python3 -c 'import sys; float(sys.argv[1])' "$MIN_SCORE" 2>/dev/null; then
  echo "gate: error: min-score is not a number: $MIN_SCORE" >&2
  exit 2
fi

# ------------------------------------------- harness-only mode (no artifact)
if [ -z "$ARTIFACT" ]; then
  echo "gate: no artifact given -- gating the HARNESS ONLY" \
       "(benchmark.py --self-test; min-score $MIN_SCORE is not applied)"
  if python3 "$BENCHMARK" --self-test; then
    echo "gate: PASS (harness self-test ok; no agent artifact was scored)"
    exit 0
  fi
  echo "gate: FAIL (harness self-test failed -- fix the benchmark/case" \
       "before trusting any score)" >&2
  exit 1
fi

# ------------------------------------------------ artifact mode (real gate)
if [ ! -f "$ARTIFACT" ]; then
  echo "gate: error: artifact not found: $ARTIFACT" >&2
  exit 2
fi

# Benchmarks print exactly one number with --score-only; take the last
# non-empty stdout line so a stray warning above it cannot break the gate.
SCORE_RAW="$(python3 "$BENCHMARK" "$ARTIFACT" --score-only 2>&1)" || true
SCORE="$(printf '%s\n' "$SCORE_RAW" | awk 'NF {last = $0} END {print last}')"

if ! python3 -c 'import sys; float(sys.argv[1])' "$SCORE" 2>/dev/null; then
  echo "gate: error: benchmark did not produce a numeric score" >&2
  echo "----- benchmark output -----" >&2
  printf '%s\n' "$SCORE_RAW" >&2
  echo "----------------------------" >&2
  exit 2
fi

echo "benchmark score: $SCORE (min $MIN_SCORE)"
if python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)' \
    "$SCORE" "$MIN_SCORE"; then
  echo "gate: PASS"
  exit 0
fi
echo "gate: FAIL (score $SCORE < min $MIN_SCORE)" >&2
exit 1
