#!/usr/bin/env bash
# gate.sh — promote a change through four tiers, cheapest first.
#
#   gate.sh 0 v6         # floor:   capability micro-corpora, tiny and cheap
#   gate.sh 1 v6 D11     # iterate: the one slice being fixed
#   gate.sh 2 v6         # regress: ALL slices — mandatory before acceptance
#   gate.sh 3 v6         # accept:  the full 649MB corpus (metered)
#
# Tier 0 is the capability floor (Task 7): green at tier 0 proves nothing about the
# corpus — these hand-written corpora are even easier than a slice, which is itself
# easier than the full 649MB corpus. RED at tier 0 is the useful signal. Tier 2 exists
# because partial runs miss interaction effects: fixing D11's coverage
# by widening a search instruction can silently blow D03's context budget. NO CHANGE
# IS ACCEPTED ON A TIER-1 PASS ALONE, and only a tier-3 number may be quoted as a
# benchmark result — a slice is an easier task than the corpus.
set -uo pipefail
shopt -s nullglob   # an unmatched "$CASES"/D* or "$CASES"/cap-* must expand to
                    # NOTHING, not the literal glob string — otherwise tier 0's
                    # or tier 2's loop "runs" once over a non-directory and
                    # reports PASS on zero real cases.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER="${1:?usage: gate.sh <0|1|2|3> <arm> [case_id]}"
ARM="${2:?usage: gate.sh <0|1|2|3> <arm> [case_id]}"
ONLY="${3:-}"
CASES="${SHERLOCK_CASES:-$HERE/cases}"
RESULTS="${SHERLOCK_RESULTS:-$HERE/results.jsonl}"

run_one() {
  local case_dir="$1" out rd last
  out="$("$HERE/run-case.sh" "$case_dir" "$ARM")" || { echo "$out"; return 1; }
  echo "$out"
  # run-case.sh's contract (its final print, see run-case.sh) is: on success the
  # LAST stdout line ends " -> <run_dir>". Parse it explicitly and validate the
  # result rather than trusting `${out##*-> }` blindly — if that contract is ever
  # violated (format change, empty output), fail loudly here instead of handing
  # report-case.py a garbled or empty --run path.
  last="${out##*$'\n'}"
  case "$last" in
    *' -> '*) rd="${last##*' -> '}" ;;
    *) echo "gate.sh: no run dir (' -> ' marker) in run-case.sh output: $last" >&2
       return 1 ;;
  esac
  [ -d "$rd" ] || { echo "gate.sh: run dir not found: $rd" >&2; return 1; }
  python3 "$HERE/report-case.py" --case "$case_dir" --run "$rd" --tier "$TIER" \
    --results "$RESULTS"
}

case "$TIER" in
  0) rc=0 n=0
     for c in "$CASES"/cap-*; do [ -d "$c" ] || continue; n=$((n + 1)); run_one "$c" || rc=1; done
     # Same trustworthy-MANDATORY-gate rule as tier 2 below: zero matching
     # micro-corpora (stale SHERLOCK_CASES, or gate.sh run before micro.py has
     # populated cases/) must fail loudly, not report PASS on nothing run.
     [ "$n" -gt 0 ] || { echo "gate.sh: tier 0 found 0 cases in $CASES" >&2; exit 1; }
     exit $rc ;;
  1) [ -n "$ONLY" ] || { echo "tier 1 needs a case id" >&2; exit 1; }
     [ -d "$CASES/$ONLY" ] || { echo "gate.sh: tier 1 case not found: $CASES/$ONLY" >&2; exit 1; }
     run_one "$CASES/$ONLY" ;;
  2) rc=0 n=0
     for c in "$CASES"/D*; do [ -d "$c" ] || continue; n=$((n + 1)); run_one "$c" || rc=1; done
     # A trustworthy MANDATORY gate must never report PASS on zero real cases: a
     # stale SHERLOCK_CASES, or running tier 2 before slice.py has populated
     # cases/, must fail loudly and distinctly from "all cases passed".
     [ "$n" -gt 0 ] || { echo "gate.sh: tier 2 found 0 cases in $CASES" >&2; exit 1; }
     exit $rc ;;
  3) : "${SHERLOCK_CORPUS:?tier 3 needs SHERLOCK_CORPUS}"
     echo "tier 3: run eval/bench/run-bench.sh $ARM against the full corpus," \
          "then score with eval/score.py. Only this number is quotable." >&2
     exit 0 ;;
  *) echo "bad tier: $TIER" >&2; exit 1 ;;
esac
