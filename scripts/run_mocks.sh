#!/usr/bin/env bash
# run_mocks.sh -- boot all four mock services (Ctrl-C to stop).
# Thin wrapper over mocks/run_all.py; port overrides: TRACKER_PORT,
# QUALITY_PORT, FORGE_PORT, TMS_PORT.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/mocks/run_all.py" "$@"
