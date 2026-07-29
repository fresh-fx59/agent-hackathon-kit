#!/usr/bin/env bash
# Run every tool test. No pip, no network, no LLM — the suite must pass on a
# laptop with nothing installed but python3.
#
#   ./tests/run.sh
#
# Exit 0 = all green.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RC=0
for t in "$HERE"/test_*.py; do
  echo "=== $(basename "$t")"
  python3 "$t" 2>&1 | tail -3
  [ "${PIPESTATUS[0]}" -eq 0 ] || RC=1
done
if [ $RC -eq 0 ]; then printf '\033[32m✓ tools: all suites green\033[0m\n';
else printf '\033[31m✗ tools: a suite failed\033[0m\n'; fi
exit $RC
