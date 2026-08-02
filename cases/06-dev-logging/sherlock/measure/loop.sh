#!/usr/bin/env bash
# Walk the defects cheapest-first on one arm, STOPPING at the first non-green cell.
# The operator's method: fix one, then move on — so a red cell must halt the spend,
# not be papered over by continuing to the next.
set -u
ARM="${1:?usage: loop.sh <arm> [cases...]}"; shift
CASES="${*:-D11 D09 D06 D01 D02 D04 D08 D07 D03 D05}"
M=/home/claude-developer/hack/agent-hackathon-kit/cases/06-dev-logging/sherlock/measure
D=/tmp/claude-1000/-home-claude-developer-personal-os/e5add82d-4ab8-4af3-8d36-bf04f8e98c24/scratchpad/one-defect.sh
green() {
  python3 - "$M/results.jsonl" "$1" "$ARM" <<'PY'
import json,sys
p,cid,arm=sys.argv[1:4]
seen=[]
for l in open(p,encoding="utf-8"):
    l=l.strip()
    if not l: continue
    r=json.loads(l)
    if r.get("case_id")==cid and r.get("arm")==arm and str(r.get("tier"))=="1":
        seen.append(r.get("diagnosis"))
if not seen: sys.exit(2)
sys.exit(0 if "ok" in seen else 1)
PY
}
for c in $CASES; do
  echo "######## $c x $ARM  $(date -u +%H:%M:%SZ)"
  bash "$D" "$ARM" "$c"; rc=$?
  if [ $rc -eq 20 ]; then echo "@@@@ SPEND GUARD tripped on $c — stopping"; exit 20; fi
  if green "$c"; then echo "@@@@ $c GREEN"; else
    echo "@@@@ $c NOT GREEN — halting the loop so it can be fixed before more spend"; exit 1
  fi
done
echo "@@@@ ALL REQUESTED CASES GREEN"
