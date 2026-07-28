#!/usr/bin/env bash
# Run a measurement matrix: every dataset x every arm, 2 at a time.
#
#   eval/batch.sh <arm> [<arm>...]        e.g. eval/batch.sh none v1
#
# Datasets are the fragile ones — the formats where the retired pipeline
# provably broke — not the evergreen passing ones.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T="${SHERLOCK_TESTSET:-$HOME/hack/logalyzer-real-world-testset/real-logs}"

DATASETS="${SHERLOCK_DATASETS:-Linux Proxifier postgres-gz nginx OpenSSH}"
ARMS=("$@")
[ ${#ARMS[@]} -gt 0 ] || { echo "usage: batch.sh <arm> [<arm>...]" >&2; exit 1; }

JOBS="${SHERLOCK_JOBS:-2}"
run_one() { "$HERE/run.sh" "$T/$1" "$2" "$2" >>"$HERE/batch.log" 2>&1; }

echo "=== batch $(date -u +%FT%TZ) arms=${ARMS[*]} datasets=$DATASETS ===" >>"$HERE/batch.log"
n=0
for arm in "${ARMS[@]}"; do
  for ds in $DATASETS; do
    [ -d "$T/$ds" ] || { echo "skip missing $ds" >>"$HERE/batch.log"; continue; }
    run_one "$ds" "$arm" &
    n=$((n+1))
    if [ $((n % JOBS)) -eq 0 ]; then wait; fi
  done
done
wait
echo "=== batch done $(date -u +%FT%TZ) ===" >>"$HERE/batch.log"
tail -40 "$HERE/batch.log"
