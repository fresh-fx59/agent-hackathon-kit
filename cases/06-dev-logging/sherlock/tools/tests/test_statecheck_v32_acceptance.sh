#!/usr/bin/env bash
# v32 acceptance gate for statecheck: the report that passed EVERY content gate
# and still missed the corpus's only intrusion artefact must now FAIL, and the
# failure must name the line it never mentioned.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(cd "$HERE/../.." && pwd)/skills/v32/tools"
CORPUS="${SHERLOCK_TEST_CORPUS:-/home/claude-developer/hack/sherlock-winevtx-corpus-20260821}"
REPORT="${SHERLOCK_TEST_FN_REPORT:-/home/claude-developer/hack/sherlock-v31-r2-validate/work/report.md}"
[ -d "$CORPUS" ] || { echo "skip: no corpus at $CORPUS"; exit 0; }
[ -r "$REPORT" ] || { echo "skip: no false-negative report at $REPORT"; exit 0; }
OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT
python3 "$TOOLS/statecheck.py" --corpus "$CORPUS" --report "$REPORT" >"$OUT" 2>&1
rc=$?
fail=0
if [ "$rc" -eq 1 ]; then echo "✓ the false-negative report exits 1"
else echo "✗ exit $rc — the known false negative still passes"; fail=1; fi
if grep -q 'System.jsonl:263' "$OUT"; then echo "✓ the failure names System.jsonl:263"
else echo "✗ System.jsonl:263 not named in the failure"; fail=1; fi
if grep -q 'Firewall.*:445' "$OUT"; then echo "✓ the 3proxy firewall group is demanded too"
else echo "✗ the user-SID firewall group was swallowed by the platform group"; fail=1; fi
n=$(grep -c 'UNACCOUNTED' "$OUT" || true)
if [ "$n" -le 20 ]; then echo "✓ census stays answerable ($n unaccounted groups)"
else echo "✗ census demands $n groups — unanswerable, not a gate"; fail=1; fi
exit $fail
