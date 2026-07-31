#!/usr/bin/env bash
# smoke.sh — prove the rig ACTUALLY WORKS before spending on a batch.
#
#   ./smoke.sh v8                        # expects the bundled tools to be invoked
#   SMOKE_EXPECT_TOOLS=0 ./smoke.sh v5   # an arm that ships no analysis tools
#
# Why this exists, and why it checks the TRAJECTORY rather than the outcome:
#
# On 2026-07-31 four metered slice cells were spent measuring arm v7, whose headline
# feature is two analysis tools. Every run looked healthy — reports delivered, judge
# consulted, rows written, one arm even "won". Only afterwards did a trajectory check
# show `logstat.py` and `logjoin.py` had been invoked ZERO times: SKILL.md documented
# `python3 tools/logstat.py`, but `tools/` lives in the SKILL directory while the
# model's cwd is the WORKING directory, so the command could never resolve.
# `citecheck.py` had shipped that way since v5 and nobody had noticed.
#
# Worse, the first smoke run found a v7 run scoring diagnosis=ok / judge_found=True
# that had never called the `skill` tool at all — a row labelled arm=v7 produced by a
# run that never loaded v7. Across 36 recorded runs, 3 (8%) were skill-less.
#
# So: a green report is NOT evidence the thing under test ran. This asserts what the
# run actually DID, on ONE ~16KB tier-0 corpus — the cheapest real run there is.
# Because the failure is intermittent, it retries: one dud must not condemn an arm,
# and one pass must not be trusted. Failed provider calls are free and never recorded.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARM="${1:?usage: smoke.sh <arm>}"
CASE="${SMOKE_CASE:-cap-multiline-stitching}"
# Whether to REQUIRE that the bundled analysis tools were invoked.
#
# Default OFF, deliberately. Tool *executability* is now guaranteed for free and
# deterministically by tools/tests/test_documented_commands_run.py, which builds both
# real layouts and actually runs every command SKILL.md documents. Whether the model
# then CHOOSES to run one is a behavioural question about the corpus, not a fault in
# the rig: on the 16KB tier-0 smoke corpus (one file, nine lines) reading the file
# directly is the correct move, and logstat would be pure overhead. Gating on it here
# would fail a perfectly healthy arm.
#
# Set to 1 when smoking a REAL slice (SMOKE_CASE=D05), where a corpus large enough to
# need the tools makes non-use a genuine signal.
EXPECT_TOOLS="${SMOKE_EXPECT_TOOLS:-0}"
TRIES="${SMOKE_TRIES:-3}"
CASES="${SHERLOCK_CASES:-$HERE/cases}"
RESULTS="$(mktemp "${TMPDIR:-/tmp}/smoke-results-XXXXXX.jsonl")"

red()  { printf '\033[31m%s\033[0m\n' "$1" >&2; }
grn()  { printf '\033[32m%s\033[0m\n' "$1"; }
fail() { red "✗ SMOKE FAIL: $1"; exit 1; }

[ -d "$CASES/$CASE" ] || fail "no smoke case at $CASES/$CASE (run micro.py --out cases)"
echo "▶ smoke: $ARM × $CASE  (expect_tools=$EXPECT_TOOLS, up to $TRIES attempt(s))"

# One attempt: run, then assert what actually happened. Returns 0 only if everything
# holds. Prints the reason on failure so a persistent fault is readable.
attempt_once() {
  : > "$RESULTS"
  SHERLOCK_RESULTS="$RESULTS" bash "$HERE/gate.sh" 1 "$ARM" "$CASE" \
    || { red "   · the run itself failed (provider or harness)"; return 1; }

  [ -s "$RESULTS" ] || { red "   · no row written — report-case.py produced nothing"; return 1; }

  RUN_DIR="$(python3 -c "
import json
line = [l for l in open('$RESULTS', encoding='utf-8') if l.strip()][-1]
print(json.loads(line).get('run_dir',''))" 2>/dev/null)"
  [ -n "$RUN_DIR" ] && [ -d "$RUN_DIR" ] || { red "   · row has no usable run_dir"; return 1; }

  python3 - "$RESULTS" "$ARM" "$RUN_DIR" "$EXPECT_TOOLS" <<'PY' || return 1
import json, os, sys
res, arm, run, expect_tools = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"

def bad(msg):
    print("   \033[31m· %s\033[0m" % msg, file=sys.stderr); sys.exit(1)

rows = [json.loads(l) for l in open(res, encoding="utf-8") if l.strip()]
if len(rows) != 1:
    bad("expected exactly 1 row, got %d" % len(rows))
r = rows[0]

# 1. the row carries everything a later comparison depends on
required = ["case_id", "arm", "model", "tier", "diagnosis", "judge_found",
            "files_opened", "proofs_reached", "tool_calls", "report_chars", "run_dir"]
missing = [k for k in required if k not in r]
if missing:
    bad("row is missing %s — a number that cannot be traced" % missing)
if r["arm"] != arm:
    bad("row says arm=%r, expected %r" % (r["arm"], arm))
if not r.get("model"):
    bad("row does not name the model — two providers would look alike")
if r.get("judge_stub"):
    bad("judge was STUBBED; this is not a measurement")
if r["diagnosis"] not in ("ok", "coverage", "reasoning", "collapse", "inconclusive"):
    bad("unknown diagnosis %r" % r["diagnosis"])

# 2. what the run actually did
recs = [json.loads(l) for l in open(os.path.join(run, "stream.jsonl"), encoding="utf-8")
        if l.strip()]
calls, errs = [], 0
for rec in recs:
    for b in (rec.get("message") or {}).get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use":
            calls.append(b.get("name"))
        if b.get("type") == "tool_result" and b.get("is_error"):
            errs += 1

# 3. the arm was REALLY in effect — 8% of runs silently skip the skill entirely
if arm != "none" and "skill" not in calls:
    bad("the `skill` tool was never called — this run was SKILL-LESS, so the row "
        "would be labelled %s while measuring no skill at all" % arm)
if calls and errs == len(calls):
    bad("every tool call errored (%d/%d)" % (errs, len(calls)))

# 4. the bundled tools actually RAN (the check whose absence cost four cells)
blob = open(os.path.join(run, "stream.jsonl"), encoding="utf-8").read()
hits = sum(blob.count(t) for t in ("logstat.py", "logjoin.py", "citecheck.py"))
if expect_tools and hits == 0:
    bad("arm %s ships analysis tools and NONE were invoked, so a green report here "
        "would measure the arm WITHOUT its headline feature. Two known causes, and "
        "they need different fixes: (a) the documented path does not resolve from the "
        "model's cwd — check with tools/tests/test_documented_commands_run.py, which "
        "is free; (b) the path resolves fine but the model chooses not to run it, "
        "which is what v9 showed — it took the manual ls/wc block sitting adjacent in "
        "step 1 instead. Read the trajectory before changing anything." % arm)

print("   \033[32m· row ok: diagnosis=%s judge_found=%s model=%s\033[0m"
      % (r["diagnosis"], r["judge_found"], r["model"]))
print("   \033[32m· trajectory ok: %d tool calls (%d errored), skill loaded\033[0m"
      % (len(calls), errs))
# Reported, not gated (see EXPECT_TOOLS): on a nine-line corpus, NOT reaching for
# logstat is correct behaviour. Printed so a surprising zero on a LARGE corpus is
# visible rather than silent.
print("   · bundled-tool references in the trajectory: %d%s"
      % (hits, "" if hits else "  (expected 0 on a tier-0 corpus)"))
PY
  return 0
}

attempt=0
while : ; do
  attempt=$((attempt + 1))
  if [ "$attempt" -gt "$TRIES" ]; then
    fail "no clean run in $TRIES attempts — the reasons above are the fault to fix"
  fi
  echo "── attempt $attempt/$TRIES"
  if attempt_once; then
    grn "✓ SMOKE PASS — $ARM × $CASE on attempt $attempt. Safe to run the batch."
    echo "  (smoke row went to $RESULTS, NOT the real ledger)"
    exit 0
  fi
done
