#!/usr/bin/env bash
# Run every measurement-rig test. No pip, no network, no LLM.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RC=0
for t in "$HERE"/test_*.py; do
  echo "=== $(basename "$t")"
  python3 "$t" 2>&1 | tail -3
  [ "${PIPESTATUS[0]}" -eq 0 ] || RC=1
done
if [ $RC -eq 0 ]; then printf '\033[32m✓ measure: all suites green\033[0m\n';
else printf '\033[31m✗ measure: a suite failed\033[0m\n'; fi
exit $RC
