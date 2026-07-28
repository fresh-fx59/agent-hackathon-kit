#!/usr/bin/env bash
# The whole experiment, sequentially (wall-clock must not be distorted by
# contention), arms interleaved (so API load / time of day hits both equally).
#
#   knowledge/measure/batch-kb.sh [reps]
#
# Cells:
#   OpenSSH cold — инцидент №1: пустая база знаний
#   Linux   cold — контроль: другой корпус, пустая база (сложность корпуса)
#   Linux   warm — инцидент №2: та же карточка уже подтверждена
#
# The Linux cold↔warm pair is the honest comparison; OpenSSH cold → Linux warm
# is the naive "incident #1 → incident #2" framing the case statement asks for.
# Both are reported.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS="${LOGS_ROOT:-$HOME/hack/logalyzer-real-world-testset/real-logs}"
REPS="${1:-3}"

for rep in $(seq 1 "$REPS"); do
  for cell in "OpenSSH cold" "Linux cold" "Linux warm"; do
    set -- $cell
    "$HERE/run-kb.sh" "$LOGS/$1" "$2" "$rep"
    sleep 3
  done
done
echo "── готово: $REPS повтор(а/ов) × 3 ячейки"
