#!/usr/bin/env bash
# bootstrap.sh -- environment sanity check + quickstart pointers.
# The kit is stdlib-only on purpose: if this script passes, everything runs.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== agent-hackathon-kit bootstrap =="

if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: python3 not found on PATH -- install Python 3.9+ and retry."
  exit 1
fi

PYVER="$(python3 -V 2>&1)"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "OK:   $PYVER (>= 3.9 required)"
else
  echo "FAIL: $PYVER is too old -- Python >= 3.9 required."
  exit 1
fi

echo "OK:   no pip / venv / network install needed -- stdlib only."
echo ""
echo "Next steps (from $ROOT):"
echo "  1. bash scripts/verify.sh        # full self-check: tests + mocks + MCP smoke + benchmarks"
echo "  2. bash scripts/run_mocks.sh     # keep the 4 mock services running (ports 8801-8804)"
echo "  3. see mcp/configs/README.md     # wire the MCP servers into your agent client"
echo "  4. see docs/                     # strategy, 24h playbook, case intake worksheet"
