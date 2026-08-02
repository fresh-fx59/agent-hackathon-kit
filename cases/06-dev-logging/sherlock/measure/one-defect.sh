#!/usr/bin/env bash
# one-defect.sh <arm> <case_id> — run ONE defect slice on ONE arm, with a SPEND GUARD.
#
# Why this exists rather than reusing slice-pairs.sh: on 2026-07-31 the batch driver
# burned ~2.3M input tokens on a metered provider across 12 retries and recorded ZERO
# rows, because a retry COUNT assumes failures are independent and these are not — a
# linkapi burst fails every attempt for minutes. This driver caps the *spend*, not just
# the attempts, and reports what it burned either way.
#
#   MAX_TRIES        attempts before giving up            (default 4)
#   MAX_CONSEC_FAIL  consecutive provider errors -> abort (default 3)
#   BURN_CAP_TOKENS  cumulative input tokens on FAILED, never-recorded runs -> abort
#                    (default 1_500_000)
#
# Exit: 0 recorded, 10 already recorded (no spend), 20 gave up (guard tripped).
set -u

ARM="${1:?usage: one-defect.sh <arm> <case_id>}"
CASE="${2:?usage: one-defect.sh <arm> <case_id>}"

M=/home/claude-developer/hack/agent-hackathon-kit/cases/06-dev-logging/sherlock/measure
SEC=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
RESULTS="$M/results.jsonl"
MAX_TRIES="${MAX_TRIES:-4}"
MAX_CONSEC_FAIL="${MAX_CONSEC_FAIL:-3}"
BURN_CAP_TOKENS="${BURN_CAP_TOKENS:-1500000}"
cd "$M" || exit 1

recorded() {
  [ -f "$RESULTS" ] || return 1
  python3 - "$RESULTS" "$CASE" "$ARM" <<'PY'
import json,sys
p,cid,arm=sys.argv[1:4]
for l in open(p,encoding="utf-8"):
    l=l.strip()
    if not l: continue
    r=json.loads(l)
    if r.get("case_id")==cid and r.get("arm")==arm and str(r.get("tier"))=="1":
        sys.exit(0)
sys.exit(1)
PY
}

# Input tokens spent by runs that produced a final record but were NOT recorded.
# Counted from the run dirs themselves, never from a summary.
burned() {
  python3 - "$M/runs" "$CASE" "$ARM" <<'PY'
import json,os,sys,glob
runs,cid,arm=sys.argv[1:4]
tot=0
for d in glob.glob(os.path.join(runs,"*-%s-%s"%(cid,arm))):
    if os.path.exists(os.path.join(d,"meta.json")):   # recorded -> real cost, not burn
        continue
    sp=os.path.join(d,"stream.jsonl")
    if not os.path.exists(sp): continue
    final=None
    for line in open(sp,encoding="utf-8",errors="replace"):
        line=line.strip()
        if not line: continue
        try: r=json.loads(line)
        except ValueError: continue
        if r.get("type")=="result": final=r
    if final:
        tot += (final.get("usage") or {}).get("input_tokens") or 0
print(tot)
PY
}

if recorded; then
  echo "== $CASE x $ARM already recorded — no tokens spent"
  exit 10
fi

consec=0
for try in $(seq 1 "$MAX_TRIES"); do
  b=$(burned)
  if [ "$b" -ge "$BURN_CAP_TOKENS" ]; then
    echo "!! SPEND GUARD: ${b} input tokens already burned on unrecorded $CASE/$ARM runs (cap $BURN_CAP_TOKENS) — stopping"
    exit 20
  fi
  echo "== $CASE x $ARM  try $try/$MAX_TRIES  burned so far: ${b} tok  $(date -u +%H:%M:%SZ)"

  timeout 2400 $SEC eval_linkapi_key --env SHERLOCK_API_KEY -- \
    $SEC eval_broker_api_key --env JUDGE_API_KEY -- \
    env SHERLOCK_BASE_URL=https://linkapi.ai/v1 \
        SHERLOCK_MODEL='[SP]deepseek-v4-flash' \
        JUDGE_BASE_URL=http://127.0.0.1:8317/v1 JUDGE_MODEL=gpt-5.5 \
        bash gate.sh 1 "$ARM" "$CASE" 2>&1 | tail -4

  if recorded; then
    echo "   -> RECORDED"
    python3 - "$RESULTS" "$CASE" "$ARM" <<'PY'
import json,sys
p,cid,arm=sys.argv[1:4]
row=None
for l in open(p,encoding="utf-8"):
    l=l.strip()
    if not l: continue
    r=json.loads(l)
    if r.get("case_id")==cid and r.get("arm")==arm and str(r.get("tier"))=="1": row=r
if row:
    print("   %s/%s  diagnosis=%s  judge_found=%s  %ss  in=%s  turns=%s"
          % (row["case_id"], row["arm"], row["diagnosis"], row["judge_found"],
             row.get("duration_s"), format(row.get("input_tokens") or 0, ","), row.get("turns")))
    print("   GREEN" if row["diagnosis"] == "ok" else "   NOT GREEN -> %s" % (row.get("why") or row.get("collapse_reason") or "")[:300])
PY
    exit 0
  fi

  consec=$((consec+1))
  if [ "$consec" -ge "$MAX_CONSEC_FAIL" ]; then
    echo "!! $consec consecutive provider failures — aborting rather than burning more (burned: $(burned) tok)"
    exit 20
  fi
  echo "   -> not recorded, backing off $((consec*45))s"
  sleep $((consec*45))
done

echo "!! gave up after $MAX_TRIES tries (burned: $(burned) tok)"
exit 20
