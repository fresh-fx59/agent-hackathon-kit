#!/usr/bin/env bash
# upstream-split.sh [n] — is `[SP]deepseek-v4-flash` one model, or several?
#
# Why this exists: on 2026-08-01 the metered loop was paused on the belief that linkapi
# was in a "sustained multi-turn burst". A probe showed the burst had cleared hours
# earlier — and, incidentally, that the alias answers under TWO identities that differ
# in whether they call tools AT ALL. Since the arm under test IS a tool-execution
# mechanism, that is a validity threat, not trivia.
#
# The request is deliberately shaped like a real trajectory mid-run: a system prompt, a
# `tools` block, and four completed rounds of assistant `tool_calls` + `tool` results.
# A bare single-turn probe cannot see this (documented: single-turn does not predict
# multi-turn), which is why the earlier 4/4 "healthy" probes were misleading.
#
# Every request is BYTE-IDENTICAL, so any split in the responses is the provider's.
#
#   bash upstream-split.sh 20 | tee upstream-split.out | sort | uniq -c | sort -rn
#
# Output: one TSV line per call — returned_model \t TOOLCALL|prose \t reasoning|-
set -euo pipefail

N="${1:-20}"
SEC=/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh
SECRET="${SHERLOCK_SECRET:-eval_linkapi_key}"
URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}/chat/completions"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
CURL=/home/claude-developer/personal-os/.claude/skills/secret-use/secret-curl.sh

d="$(mktemp -d)"; trap 'rm -rf "$d"' EXIT INT TERM HUP

MODEL="$MODEL" python3 - > "$d/body.json" <<'PY'
import json, os
msgs = [
    {"role": "system", "content": "You are a log analyst. Use the read_file tool to inspect logs."},
    {"role": "user", "content": "Investigate the error spike in catalog-svc.log."},
]
for i in range(4):
    cid = f"call_{i:024d}"
    msgs.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {
            "name": "read_file",
            "arguments": json.dumps({"path": "catalog-svc.log", "offset": 149196 + i * 20, "limit": 20})}}]})
    msgs.append({"role": "tool", "tool_call_id": cid, "content":
        "2026-07-20T11:%02d:00Z ERROR catalog-svc upstream timeout status=503 latency_ms=30012\n" % (i * 7) * 15})
msgs.append({"role": "user", "content": "Continue. What is the next line you would read? One sentence."})
print(json.dumps({
    "model": os.environ["MODEL"], "max_tokens": 80, "messages": msgs,
    "tools": [{"type": "function", "function": {
        "name": "read_file", "description": "Read lines from a file",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["path"]}}}]}))
PY

for _ in $(seq 1 "$N"); do
  "$CURL" "$SECRET" 'Authorization: Bearer %s' \
      -s -X POST "$URL" -H 'Content-Type: application/json' -d @"$d/body.json" \
    | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("ERR\tunparseable\t-"); raise SystemExit
if "choices" not in d:
    print("FAIL\t%s\t-" % json.dumps(d)[:120]); raise SystemExit
m = d["choices"][0]["message"]
print("%s\t%s\t%s" % (d.get("model"),
                      "TOOLCALL" if m.get("tool_calls") else "prose",
                      "reasoning" if m.get("reasoning_content") else "-"))
'
done
