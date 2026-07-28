#!/usr/bin/env bash
# Benchmark run against the 649 MB / 26-format / 11-planted-defect corpus.
#
#   run-bench.sh <none|v1|v2|v3>
#
# Why this exists, separately from eval/run.sh:
#   * the five A/B datasets are all SINGLE-FILE, so they cannot demonstrate the
#     coverage discipline that is the design's central claim (28 files here);
#   * the 100/73/18 and 79/79 figures were measured on a different model — this
#     puts the same corpus in front of DeepSeek-V4-Flash, the corporate model;
#   * it yields the organizers' «≥50 % дефектов» number against a real answer key.
#
# Writes to its OWN ledger (runs-bench.jsonl) so it never races the A/B ledger.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$(cd "$HERE/../.." && pwd)/skills"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
LEDGER="$HERE/runs-bench.jsonl"

ARM="${1:?usage: run-bench.sh <none|v1|v2|v3>}"
CORPUS="${SHERLOCK_CORPUS:?set SHERLOCK_CORPUS to the 649MB corpus dir}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-2700}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY}"

[ -d "$CORPUS" ] || { echo "✗ corpus not found: $CORPUS" >&2; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/bench-XXXXXX")"
trap 'rm -rf "$W"' EXIT
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

if [ "$ARM" != "none" ]; then
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $CORPUS.
Найди ВСЕ проблемы и инциденты, определи корневую причину каждой и предложи,
что делать. Ссылайся на конкретные строки в формате файл:строка."

echo "▶ bench arm=$ARM  corpus=$(du -sh "$CORPUS" | cut -f1)  model=$MODEL"
START=$(date +%s)
# key via environment, never argv (visible in ps; this box has a guest account)
( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
  timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$MODEL" \
    --approval-mode yolo -p "$PROMPT" --output-format json </dev/null \
) >"$W/out.json" 2>"$W/err.txt"
ELAPSED=$(( $(date +%s) - START ))
[ -s "$W/out.json" ] || { echo "  ✗ no output"; sed -n '1,8p' "$W/err.txt"; exit 1; }

python3 - "$W/out.json" "$ARM" "$ELAPSED" "$CORPUS" "$LEDGER" <<'PY'
import json, os, re, sys
out, arm, elapsed, corpus, ledger = sys.argv[1:6]
d = json.load(open(out)); d = d if isinstance(d, list) else [d]
final = next((r for r in d if r.get("type") == "result"), None)
sysr  = next((r for r in d if r.get("type") == "system"), {})
if final is None:
    print("  ✗ no final result record"); sys.exit(1)
t = final.get("result") or ""
if final.get("is_error") or t.lstrip().startswith("[API Error") or ("[API Error" in t and len(t) < 400):
    print("  ✗ provider/run error, NOT recorded:", (final.get("error") or t)[:160]); sys.exit(2)

# coverage: how many of the corpus's files does the answer actually name?
names = []
for root, _, fs in os.walk(corpus):
    for f in fs:
        names.append(f)
cited = {f for f in set(names) if f in t}
u = final.get("usage") or {}
rec = {"arm": arm, "model": sysr.get("model"), "turns": final.get("num_turns"),
       "duration_s": int(elapsed), "input_tokens": u.get("input_tokens"),
       "output_tokens": u.get("output_tokens"), "answer_chars": len(t),
       "files_in_corpus": len(set(names)), "files_cited": len(cited),
       "line_refs": len(re.findall(r":\d+", t)), "dataset": "bench649",
       "answer": t}
with open(ledger, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("  ✓ turns=%s %ss in/out=%s/%s chars=%d files_cited=%d/%d line_refs=%d"
      % (rec["turns"], elapsed, rec["input_tokens"], rec["output_tokens"],
         rec["answer_chars"], rec["files_cited"], rec["files_in_corpus"], rec["line_refs"]))
PY
