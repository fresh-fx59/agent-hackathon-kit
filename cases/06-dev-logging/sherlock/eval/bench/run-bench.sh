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
LEDGER="${BENCH_LEDGER:-$HERE/runs-bench.jsonl}"

ARM="${1:?usage: run-bench.sh <none|v1|v2|v3>}"
CORPUS="${SHERLOCK_CORPUS:?set SHERLOCK_CORPUS to the 649MB corpus dir}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-2700}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY}"

[ -d "$CORPUS" ] || { echo "✗ corpus not found: $CORPUS" >&2; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/bench-XXXXXX")"
# The trajectory is the ONLY way to tell "never opened the file" from "opened it
# and closed it wrongly" from "found it and discarded it" — and that is exactly
# the question every arm since v5 exists to answer. It used to be deleted on
# exit, so five runs in a row were unreadable. Keep it next to the ledger.
RUNS="${BENCH_RUNS:-$HERE/runs}"; mkdir -p "$RUNS"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$ARM"
TRACE="$RUNS/$STAMP"
save_trace() {
  mkdir -p "$TRACE"
  [ -f "$W/out.json" ] && cp "$W/out.json" "$TRACE/out.json"
  for partial in "$W"/out-attempt-*.json; do
    [ -f "$partial" ] && cp "$partial" "$TRACE/$(basename "$partial")"
  done
  [ -f "$W/err.txt" ]  && cp "$W/err.txt"  "$TRACE/err.txt"
  # whatever the run wrote for itself (v11 keeps its worklist verdicts here)
  [ -d "$W/work" ] && cp -r "$W/work" "$TRACE/work"
  # A malformed upstream SSE must not erase the only resumable session state.
  [ -d "$QWEN_HOME" ] && cp -r "$QWEN_HOME" "$TRACE/qwen-home"
  rm -rf "$W"
  [ -n "${LANE_PROXY_PID:-}" ] && kill "$LANE_PROXY_PID" 2>/dev/null
}
trap save_trace EXIT
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

# STATE THE CONTEXT WINDOW OUTRIGHT, same as run-case.sh. This is the runner on
# the 649 MB corpus, so a 177,000-token ceiling hurts here most of all.
# → measure/run-case.sh for why the default is 400,000 and not 1,048,576.
CTX_WINDOW="${SHERLOCK_CONTEXT_WINDOW:-400000}"
if [ "$CTX_WINDOW" != "0" ]; then
  mkdir -p "$W/.qwen"
  printf '{ "model": { "generationConfig": { "contextWindowSize": %s } } }\n' \
    "$CTX_WINDOW" > "$W/.qwen/settings.json"
fi

if [ "$ARM" != "none" ]; then
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

# THE PROMPT IS A PROPERTY OF THE CORPUS, NOT OF THE RUNNER.
# It was hard-coded to «Продакшн деградировал» — a production-outage RCA. Pointed
# at an intrusion corpus that asks the model the wrong question and then scores
# the answer, which is a defect the numbers cannot show. Resolution order:
#   1. $SHERLOCK_PROMPT_FILE           — explicit, wins
#   2. $HERE/prompts/$DATASET.txt      — per-corpus, committed next to the key
#   3. the historical outage prompt    — kept ONLY for dataset bench649
DATASET="${SHERLOCK_DATASET:-bench649}"
PROMPT_FILE="${SHERLOCK_PROMPT_FILE:-$HERE/prompts/$DATASET.txt}"
if [ -f "$PROMPT_FILE" ]; then
  PROMPT="$(cat "$PROMPT_FILE")"
  PROMPT="${PROMPT//\$CORPUS/$CORPUS}"
  PROMPT="$(printf '%s' "$PROMPT" | sed "s|{CORPUS}|$CORPUS|g")"
elif [ "$DATASET" = "bench649" ]; then
  PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $CORPUS.
Найди ВСЕ проблемы и инциденты, определи корневую причину каждой и предложи,
что делать. Ссылайся на конкретные строки в формате файл:строка."
else
  echo "✗ dataset=$DATASET has no prompt: expected $PROMPT_FILE" >&2
  echo "  A corpus without its own question would be scored against an answer" >&2
  echo "  to a different question. Write the prompt file first." >&2
  exit 1
fi

# THE SAME UPSTREAM LANE AS run-case.sh. This runner used to talk to linkapi
# directly, which cost it two things: no row could be attributed to an upstream
# (the alias fans out to identities ~19x apart on tool-calling), and the CLI was
# handed `[SP]deepseek-v4-flash`, whose bracket prefix defeats qwen-code's own
# model-id table and pins the context window to 200,000 — the "177,000-token
# ceiling". On the 649 MB corpus that is the runner where it hurts most.
MEASURE_DIR="$(cd "$HERE/../../measure" && pwd)"
. "$MEASURE_DIR/upstream-lane.sh"
upstream_lane_start "$BASE_URL" "$TRACE.upstream.jsonl" "$STAMP" "$MODEL"
BASE_URL="$LANE_BASE_URL"
CLIENT_MODEL="$LANE_CLIENT_MODEL"

echo "▶ bench arm=$ARM  dataset=$DATASET  corpus=$(du -sh "$CORPUS" | cut -f1)  model=$MODEL"
START=$(date +%s)
# A stream can break after the agent has already mapped most of the corpus. Keep
# its QWEN_HOME and resume the same session with bounded exponential backoff;
# never replace useful mid-session work with a fresh, empty investigation.
RESUME_MAX_ATTEMPTS="${SHERLOCK_RESUME_MAX_ATTEMPTS:-2}"
RESUME_BACKOFF_S="${SHERLOCK_RESUME_BACKOFF_S:-15}"
RESUME_ATTEMPTS=0
RESUME_SESSION=""

run_qwen() {
  # key via environment, never argv (visible in ps; this box has a guest account)
  ( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$CLIENT_MODEL" \
      --approval-mode yolo "$@" --output-format json </dev/null \
  ) >"$W/out.json" 2>"$W/err.txt"
}

broken_session() {
  python3 - "$W/out.json" <<'PY'
import json, sys
try:
    rows = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(1)
if not isinstance(rows, list):
    rows = [rows]
final = next((r for r in rows if isinstance(r, dict) and r.get("type") == "result"), {})
text = final.get("result") or ""
broken = (not final or final.get("is_error") or text.lstrip().startswith("[API Error")
          or ("[API Error" in text and len(text) < 400))
if not broken:
    sys.exit(1)
session = final.get("session_id") or next(
    (r.get("session_id") for r in rows if isinstance(r, dict) and r.get("session_id")), "")
if session:
    print(session)
    sys.exit(0)
sys.exit(1)
PY
}

run_qwen -p "$PROMPT"
while RESUME_SESSION="$(broken_session)" \
  && [ "$RESUME_ATTEMPTS" -lt "$RESUME_MAX_ATTEMPTS" ]; do
  mv "$W/out.json" "$W/out-attempt-$RESUME_ATTEMPTS.json"
  RESUME_ATTEMPTS=$((RESUME_ATTEMPTS + 1))
  BACKOFF=$((RESUME_BACKOFF_S * (2 ** (RESUME_ATTEMPTS - 1))))
  echo "  ⚠ stream failed; preserving session $RESUME_SESSION and retrying in ${BACKOFF}s (attempt $RESUME_ATTEMPTS/$RESUME_MAX_ATTEMPTS)" >&2
  sleep "$BACKOFF"
  run_qwen --resume "$RESUME_SESSION" -p "The previous provider stream failed. Continue the same investigation from saved state. Do not restart mapping; finish the unresolved worklist and deliver the report."
done
mkdir -p "$TRACE"
python3 - "$TRACE/recovery.json" "$RESUME_ATTEMPTS" "$RESUME_SESSION" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"resume_attempts": int(sys.argv[2]), "session_id": sys.argv[3]}, fh)
    fh.write("\n")
PY
ELAPSED=$(( $(date +%s) - START ))

python3 - "$W/out.json" "$ARM" "$ELAPSED" "$CORPUS" "$LEDGER" "$TRACE" "$MODEL" \
         "$W" "$MEASURE_DIR" "$DATASET" <<'PY'
import importlib.util, json, os, re, sys
out, arm, elapsed, corpus, ledger, trace, model, workroot, measure, dataset = sys.argv[1:11]

# ONE definition of "what the run produced", shared with score-bench.py. A second
# copy of this rule is how one measurement becomes two incomparable scales.
_spec = importlib.util.spec_from_file_location(
    "deliverable", os.path.join(measure, "deliverable.py"))
D = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(D)

# ── A run delivers on TWO channels; this ledger used to see one ───────────────
# 2026-08-02 (`runs/20260802T221034Z-v11`): citecheck green 45/45, «Теперь
# финальный шаг — вывести отчёт полностью», `read_file(work/report.md)`, stop.
# Final message 101 chars beside a complete 19,991-char report — 18,758,431
# input tokens, no row. A tool result is not the `result` record, and no wording
# fixes that: every phrasing of "output the report" is satisfiable by a tool the
# model already has (two edits tried, `ebf39ca` and `6490599`, both failed).
apath = os.path.join(workroot, "work", "report.md")
art = ""
if os.path.isfile(apath):
    with open(apath, encoding="utf-8", errors="replace") as fh:
        art = fh.read()

raw = ""
try:
    with open(out, encoding="utf-8") as fh:
        raw = fh.read()
except OSError:
    pass
try:
    d = json.loads(raw) if raw.strip() else []
except ValueError:
    d = []
d = d if isinstance(d, list) else [d]
final = next((r for r in d if isinstance(r, dict) and r.get("type") == "result"), None)
sysr  = next((r for r in d if isinstance(r, dict) and r.get("type") == "system"), {})

t = (final or {}).get("result") or ""
broke = (final is None or final.get("is_error")
         or t.lstrip().startswith("[API Error")
         or ("[API Error" in t and len(t) < 400))
artifact_only = False
if broke:
    # A killed or provider-errored run. It used to leave NO row at all, and ~33 %
    # of this project's spend has bought exactly that. If a finished report
    # survived, detection is still answerable — the COST is not, so every cost
    # field stays null and never 0. → [[eval-must-measure-cost-not-just-quality]]
    why = (final or {}).get("error") or t or "no final result record"
    if not art.strip():
        print("  ✗ run produced neither an answer nor a report, NOT recorded:",
              str(why)[:160])
        sys.exit(2)
    print("  ⚠ run failed (%s) but work/report.md survived — ARTIFACT-ONLY row, "
          "cost unknown" % str(why)[:80])
    t, artifact_only = "", True

deliverable = D.compose(t, art)
delivered_in = D.channel(t, art)

# Coverage: how many of the corpus's files does the run actually name?
# Matched on the RELATIVE PATH, not the basename. Two files here are both called
# `syslog`, so a basename count reported 30 files in a 31-file corpus and called
# one citation two. Counted over BOTH channels — reading it off the final
# message alone is what scored the collapsed run "0 of 31 files cited".
rels = set()
for root, _, fs in os.walk(corpus):
    for f in fs:
        rels.add(os.path.relpath(os.path.join(root, f), corpus).replace(os.sep, "/"))
cited = {r for r in rels if r in deliverable}
u = (final or {}).get("usage") or {}
cost = (lambda k: None) if artifact_only else (lambda k: u.get(k))
# `model` = what the PROVIDER was asked for (the alias); `client_model` = the
# id the CLI reported, which is the one it sized its context window from.
rec = {"arm": arm, "model": model, "client_model": sysr.get("model"),
       "turns": None if artifact_only else (final or {}).get("num_turns"),
       "duration_s": int(elapsed), "input_tokens": cost("input_tokens"),
       "output_tokens": cost("output_tokens"), "answer_chars": len(t),
       "artifact_chars": len(art), "deliverable_chars": len(deliverable),
       "delivered_in": delivered_in, "artifact_only": artifact_only,
       "files_in_corpus": len(rels), "files_cited": len(cited),
       "cited_files": sorted(cited),
       "line_refs": len(re.findall(r":\d+", deliverable)),
       # was the literal "bench649" on EVERY row, whatever corpus ran. A run
       # against an intrusion corpus filed under the dev corpus is a number
       # attributed to the wrong evidence.
       "dataset": dataset, "corpus_dir": corpus,
       "trace_dir": trace, "answer": t, "artifact": art}
with open(ledger, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("  ✓ turns=%s %ss in/out=%s/%s delivered_in=%s msg=%d file=%d "
      "files_cited=%d/%d line_refs=%d"
      % (rec["turns"], elapsed, rec["input_tokens"], rec["output_tokens"],
         delivered_in, rec["answer_chars"], rec["artifact_chars"],
         rec["files_cited"], rec["files_in_corpus"], rec["line_refs"]))
if delivered_in == "file":
    print("  ⚠ DELIVERY: the report is in work/report.md, not in the final message")
print("  trajectory: %s" % trace)
PY
RC=$?
# the CLI's own stderr is the only clue when the run produced nothing
[ "$RC" -ne 0 ] && [ -f "$W/err.txt" ] && sed -n '1,8p' "$W/err.txt"
exit "$RC"
