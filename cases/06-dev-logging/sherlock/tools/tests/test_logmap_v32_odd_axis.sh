#!/usr/bin/env bash
# v32 regression: the singleton anomaly inside an otherwise boring template group
# must earn its own worklist row, citing the MINORITY record's line.
#
# The v31 failure this locks down: 16 EventID-7045 records in System.jsonl fall
# between RARE_MAX_N=5 and RATE_MIN_N=60, so the group was dropped outright; and
# the one value that separates the intruder's install from the 15 platform ones
# is a 46-character SID, past VALUE_MAX=24, so it was never even extracted.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(cd "$HERE/../.." && pwd)/skills/v32/tools"
CORPUS="${SHERLOCK_TEST_CORPUS:-/home/claude-developer/hack/sherlock-winevtx-corpus-20260821}"
[ -d "$CORPUS" ] || { echo "skip: no corpus at $CORPUS"; exit 0; }
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
WORK="$OUT/work"; mkdir -p "$WORK"
CP="$OUT/corpus"; cp -r "$CORPUS" "$CP"
python3 "$TOOLS/stage-corpus.py" --map "$WORK/path-map.tsv" "$CP" >/dev/null || exit 1
python3 "$TOOLS/logmap.py" "$CP" --out "$WORK" >/dev/null 2>&1 || {
  echo "✗ logmap failed"; exit 1; }
W="$WORK/worklist.tsv"
fail=0
if grep -q 'System.jsonl:263' "$W"; then
  echo "✓ 3proxy service install surfaced (System.jsonl:263)"
else
  echo "✗ System.jsonl:263 absent — the 7045 minority SID did not earn a row"; fail=1
fi
if grep -qE 'Firewall[^[:space:]]*\.jsonl:44[34]' "$W"; then
  echo "✓ 3proxy firewall allow rule surfaced"
else
  echo "✗ firewall allow rule at line 443/444 absent"; fail=1
fi
n=$(grep -c . "$W" 2>/dev/null || echo 0)
if [ "$n" -gt 400 ]; then
  echo "✗ worklist inflated to $n rows — the new axes are too loud"; fail=1
else
  echo "✓ worklist size $n rows (cap 400)"
fi
exit $fail
