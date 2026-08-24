#!/usr/bin/env bash
# run-case.sh — run one arm against one case and KEEP the evidence.
#
#   run-case.sh <case-dir> <arm>
#
# The one thing this does that run-bench.sh did not: `--output-format stream-json`
# instead of `json`, teed to a run directory that is NEVER deleted. Every previous
# measurement discarded the step-by-step record, which is why we could report a
# score but never a cause.
set -uo pipefail

CASE_DIR="${1:?usage: run-case.sh <case-dir> <arm>}"
ARM="${2:?usage: run-case.sh <case-dir> <arm>}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
SKILLS="${SHERLOCK_SKILLS:-$(cd "$HERE/.." && pwd)/skills}"
RUNS="${SHERLOCK_RUNS:-$HERE/runs}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-2700}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY (use with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ...)}"

[ -d "$CASE_DIR" ] || { echo "✗ no such case: $CASE_DIR" >&2; exit 1; }
# Canonicalize BEFORE it goes into the prompt: qwen runs after `cd "$W"` into the
# scratch dir, so a relative CASE_DIR would describe a path relative to the wrong
# cwd. The model then answers plausibly about nothing, the guard below never fires
# (it's a normal success record), and a wrong-but-recorded measurement lands in the
# ledger — the exact failure class the guard exists to prevent, by a route it can't see.
CASE_DIR="$(cd "$CASE_DIR" && pwd)"
CASE_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["case_id"])' \
  "$CASE_DIR/case.json")" || { echo "✗ unreadable case.json" >&2; exit 1; }

# CRITICAL-1. The model is pointed at the CORPUS, never at the case root: case.json
# holds the title, the root cause and every proof_location. In the captured run
# 20260730T195412Z the model's 12th record was a read_file on case.json and its 13th
# returned the root cause — before it had opened a single log line. Every number that
# layout produced measured "can the model read a JSON file".
PROMPT_DIR="$CASE_DIR/corpus"
[ -d "$PROMPT_DIR" ] || { echo "✗ no corpus dir: $PROMPT_DIR (rebuild with slice.py/micro.py)" >&2; exit 1; }
# The guard, so this can never silently regress: whatever directory we are about to
# name in the prompt must not contain the answer. Checked on the resolved path, not
# on the variable, so a symlinked or re-pointed PROMPT_DIR is caught too.
PROMPT_DIR="$(cd "$PROMPT_DIR" && pwd)"
if [ -e "$PROMPT_DIR/case.json" ]; then
  echo "✗ REFUSING: $PROMPT_DIR contains case.json — the prompt directory must never" >&2
  echo "  hold the answer key (title/root_cause/proof_locations). See CRITICAL-1." >&2
  exit 1
fi

# Validate the arm BEFORE creating the run dir: a missing skill must fail with no
# trace, not leave behind an empty orphan run dir.
if [ "$ARM" != "none" ]; then
  [ -f "$SKILLS/$ARM/SKILL.md" ] || { echo "✗ no skill at $SKILLS/$ARM/SKILL.md" >&2; exit 1; }
fi

# Second-resolution alone is not unique enough: two runs started in the same second
# (a tier-2 loop over fast/refused cases) would share a run dir and the second would
# overwrite the first — in a rig whose one promise is that the run dir is NEVER
# deleted. A short random suffix makes each capture its own directory.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
RUN_DIR="$RUNS/$STAMP-$CASE_ID-$ARM"
mkdir -p "$RUN_DIR" || { echo "✗ cannot create $RUN_DIR" >&2; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/runcase-XXXXXX")"
PROXY_PID=""
# the SCRATCH dir goes; the RUN dir stays. The proxy must not outlive the run.
trap 'rm -rf "$W"; [ -n "${PROXY_PID:-}" ] && kill "$PROXY_PID" 2>/dev/null' EXIT

# NAME THE MODEL THAT ACTUALLY ANSWERS, and do not let the CLI parse the routing
# prefix as part of the model name. Both live in the shared lane, because
# run-bench.sh needs exactly the same wire and drifted for weeks without it.
# Opt out with SHERLOCK_UPSTREAM_LOG=0. → measure/upstream-lane.sh
. "$HERE/upstream-lane.sh"
upstream_lane_start "$BASE_URL" "$RUN_DIR/upstream.jsonl" \
                    "$STAMP-$CASE_ID-$ARM" "$MODEL"
BASE_URL="$LANE_BASE_URL"
CLIENT_MODEL="$LANE_CLIENT_MODEL"
PROXY_PID="$LANE_PROXY_PID"

export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

# STATE THE CONTEXT WINDOW OUTRIGHT — belt and braces on the 177,000 ceiling.
# Stripping the `[SP]` prefix above fixes the limit only via a three-link
# inference (strip => qwen-code's table matches => 1M), and it deliberately does
# NOT happen when the proxy is down. This says the number instead, which
# qwen-code honours regardless of the model id. Verified on 0.21.1 against a
# local provider: the same 312,713-token prompt is refused with
# `hard limit: 177000` under the prefixed id and sent once this is set.
#
# SUPERSEDED 2026-08-24 (kept for the reasoning): the old default was 400,000,
# chosen because the arm's procedure needs ~250k (SKILL.md's mandated
# per-candidate windows and citation re-reads) and because cost is Σ(context)
# over turns, so an unbounded window is an unbounded bill. That number was a
# guess at the provider's ceiling, and the guess was wrong - see below.
# MEASURED 2026-08-24: the provider's REAL context window on this lane is
# 262,000 tokens, not 400,000 and not DeepSeek-V4-Flash's advertised 1,048,576.
# A 400,000 window put Qwen's auto-compaction threshold at 0.85 x 400,000 =
# 340,000 tokens, so compaction could NEVER fire before the provider refused
# the request: the fatal v34 r2 request was 828,403 bytes = ~242,000 tokens at
# the measured 3.42 bytes/token, under the fake ceiling and over the real one.
# The default is now 200,000 - deliberately BELOW the real 262,000 so that
# prompt + max_tokens still fits (200,000 + 32,768 = 232,768 < 262,000) and
# compaction fires at 0.85 x 200,000 = 170,000 tokens, with real headroom left.
# Raise it only with a new measurement of the provider's limit; 0 writes nothing.
CTX_WINDOW="${SHERLOCK_CONTEXT_WINDOW:-200000}"
# CLAMP THE OUTPUT BUDGET TOO. Unclamped, qwen-code auto-escalates max_tokens
# (`shouldEscalateMaxOutputTokens`, with a 64K floor) so `prompt + max_tokens`
# overflows the provider window even when the prompt alone fits - the documented
# cause of an empty HTTP 200 (vllm#3851). Setting `samplingParams.max_tokens`
# makes `hasUserMaxTokensOverride` true in qwen-code 0.21.1 and disables that
# escalation; the value is sealed into the settings snapshot, so it is auditable
# per run rather than living in an env var nobody records. 32,768 covers the
# largest report we have ever produced (53,435 bytes ~= 15.6k tokens) twice over.
# 0 writes nothing and restores the old auto-escalating behaviour.
MAX_OUT="${SHERLOCK_MAX_OUTPUT_TOKENS:-32768}"
case "$MAX_OUT" in *[!0-9]*|'') echo "✗ invalid SHERLOCK_MAX_OUTPUT_TOKENS" >&2; exit 1 ;; esac
SAMPLING_JSON=''
if [ "$MAX_OUT" != "0" ]; then
  SAMPLING_JSON=", \"samplingParams\": { \"max_tokens\": $MAX_OUT }"
fi


# AND TAKE AWAY THE SUBAGENT. `reconcile.py --arm v11` over the nine recorded
# rows: 4 carry SKILL-NEVER-LOADED and THREE OF THOSE FOUR also carry
# SUBAGENT-SPAWNED — D09 rep1 (first tool call was `agent`, 109-char final
# message), D01, D04 rep2. Not one subagent run loaded the arm; every
# skill-loaded subagent-free row is `ok`. CORRECTED 2026-08-24: this is NOT
# because the subagent fails to inherit `.qwen/skills/` — measured today on
# 0.21.1, a `general-purpose` subagent DOES see it and DOES call `skill`
# successfully (23 skills listed, including this project's own; `skill` is
# absent from EXCLUDED_TOOLS_FOR_SUBAGENTS and the child Config is
# `Object.create(parentConfig)`, so targetDir and the skill manager are
# inherited). The v11 failure was some other fan-out pathology — a headless
# `qwen -p` fan-out losing the report is still plausible, but it was never
# isolated further because the runner just removed the option instead.
# `excludeTools` is supported and the tool is named `agent`; verified on 0.21.1
# that the init record then lists 60 tools instead of 61, `skill` still present.
# This CHANGES WHAT A RUN DOES — meta records it, so rows from either side of
# this change are never pooled. Kept excluded by default so this bench holds
# fan-out as one deliberately-fixed variable, not because skill delivery is
# known to break under it. SHERLOCK_ALLOW_SUBAGENT=1 gives it back for a
# measured control arm.
SUBAGENT_AVAILABLE=true
EXCLUDE_JSON=''
if [ "${SHERLOCK_ALLOW_SUBAGENT:-0}" != "1" ]; then
  SUBAGENT_AVAILABLE=false
  EXCLUDE_JSON=', "tools": { "exclude": ["agent"] }'
fi
if [ "$CTX_WINDOW" != "0" ]; then
  mkdir -p "$W/.qwen"
  printf '{ "model": { "generationConfig": { "contextWindowSize": %s%s } }%s }\n' \
    "$CTX_WINDOW" "$SAMPLING_JSON" "$EXCLUDE_JSON" > "$W/.qwen/settings.json"
elif [ -n "$EXCLUDE_JSON" ]; then
  mkdir -p "$W/.qwen"
  printf '{ "tools": { "exclude": ["agent"] } }\n' > "$W/.qwen/settings.json"
fi

if [ "$ARM" != "none" ]; then
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $PROMPT_DIR.
Найди ВСЕ проблемы и инциденты, определи корневую причину каждой и предложи,
что делать. Ссылайся на конкретные строки в формате файл:строка."

# THE ARM MUST NOT BE A COIN FLIP. 4 of 9 recorded v11 rows carry
# SKILL-NEVER-LOADED — 44 %, not the 8 % we had on record. The prompt never
# named the skill, so the model had to discover .qwen/skills/ by itself and
# then choose to call the `skill` tool; when it didn't, the row measured the
# base model while being labelled with the arm. An arm measured on barely half
# its runs is not measured at all.
#
# So the prompt NAMES the skill and tells the model to load it. Deliberately a
# POINTER, not the text: pasting SKILL.md into the prompt would remove the very
# behaviour under test (progressive disclosure — SKILL.md pulls reference/*.md
# on demand) and would measure a different system. Naming it removes the
# DISCOVERY problem while leaving the loading behaviour intact, so
# `skill_loaded` still means what it always meant: the model called the tool.
#
# NOTE for comparisons: `skill_delivery` records "named" vs "tool-only" so rows
# from either side of 2026-08-01 are never silently pooled.
SKILL_DELIVERY="tool-only"
if [ "$ARM" != "none" ] && [ -f "$W/.qwen/skills/log-rca/SKILL.md" ]; then
  PROMPT="$PROMPT

Решай эту задачу С ПОМОЩЬЮ НАВЫКА log-rca (Sherlock). Он уже установлен:
ПЕРВЫМ ДЕЙСТВИЕМ загрузи его инструментом skill (skill: log-rca) и дальше
следуй его процедуре. Его инструменты лежат в .qwen/skills/log-rca/tools/."
  SKILL_DELIVERY="named"
fi

START=$(date +%s)
# The key travels by ENVIRONMENT, never argv: /proc/<pid>/cmdline is world-readable.
( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    QWEN_CODE_SUPPRESS_YOLO_WARNING=1 \
    timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$CLIENT_MODEL" \
      --approval-mode yolo -p "$PROMPT" --output-format stream-json </dev/null \
) > "$RUN_DIR/stream.jsonl" 2> "$RUN_DIR/stderr.txt"
RC=$?
ELAPSED=$(( $(date +%s) - START ))

# KEEP THE MODEL'S WORKING REPORT. D04 wrote an 18,186-char report naming the
# right root cause and then delivered 143 chars; the 18 KB sat in $W, which the
# EXIT trap deletes, so the only surviving proof that the model FOUND the defect
# was buried in the trajectory. This copy makes "found but not delivered" a
# measured number instead of an inference. It is EVIDENCE, NOT THE SCORE — the
# answer stays the final message, exactly as before.
for wr in "$W/work/report.md" "$W/report.md"; do
  [ -f "$wr" ] && { cp "$wr" "$RUN_DIR/working-report.md" 2>/dev/null; break; }
done

python3 - "$RUN_DIR" "$CASE_ID" "$ARM" "$ELAPSED" "$RC" "$MODEL" "$SKILL_DELIVERY" "$SUBAGENT_AVAILABLE" <<'PY'
import json, sys, os
run_dir, case_id, arm, elapsed, rc, model, skill_delivery, subagent = sys.argv[1:9]
final = None
for line in open(os.path.join(run_dir, "stream.jsonl"), encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("type") == "result":
        final = r
# THE BURN LEDGER. A refused run is NOT free: it died partway, so every turn
# before the failure was billed. run-case.sh correctly refuses to record it as a
# measurement — which is exactly why the spend was invisible. On 2026-08-01,
# 5,896,031 tokens across 6 dead runs never appeared anywhere, while
# results.jsonl showed only the 5,306,617 that survived. Same mistake as the
# 2.3M burn that created the spend cap. So: refusals go in burned.jsonl.
def burn(reason):
    try:
        u = (final or {}).get("usage") or {}
        rec = {"case_id": case_id, "arm": arm, "model": model, "reason": reason,
               "input_tokens": u.get("input_tokens"),
               "output_tokens": u.get("output_tokens"),
               "turns": (final or {}).get("num_turns"),
               "duration_s": int(elapsed), "exit_code": int(rc),
               "run_dir": run_dir}
        bp = os.path.join(os.path.dirname(os.path.dirname(run_dir.rstrip("/"))),
                          "burned.jsonl")
        with open(bp, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass          # a bookkeeping failure must never mask the real error


# CRITICAL-4. A non-zero exit from qwen — including timeout's 124 — means the run did
# not complete. Whatever partial text landed in the stream is not a measurement. This
# was captured into meta.json as `exit_code` and then ignored, so a timed-out run was
# recorded as a normal row.
if int(rc) != 0:
    print("  ✗ runner exited %s (timeout/crash), NOT recorded" % rc)
    burn("runner-exit-%s" % rc); sys.exit(4)

if final is None:
    print("  ✗ no final result record — NOT recorded"); burn("no-result"); sys.exit(2)

text = final.get("result") or ""
if not text.strip():
    print("  ✗ empty result — NOT recorded"); burn("empty-result"); sys.exit(2)

# CRITICAL-4. Same guard as run-bench.sh:61, whose FIRST check is is_error — dropped
# here, so a record with is_error true, >=400 chars and no leading "[API Error" was
# refused there and recorded here. The provider's own flag outranks any text
# heuristic: text matching is the fallback for providers that report a failure as a
# successful record (two such rows polluted a ledger on 2026-07-28), not the primary.
if final.get("is_error") or text.lstrip().startswith("[API Error") \
        or ("[API Error" in text and len(text) < 400):
    print("  ✗ provider/run error, NOT recorded: %s"
          % str(final.get("error") or text)[:160].replace("\n", " "))
    burn("provider-error"); sys.exit(3)

with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as fh:
    fh.write(text)
u = final.get("usage") or {}
meta = {"case_id": case_id, "arm": arm, "model": model,
        "started_at": os.path.basename(run_dir).split("-")[0],
        "duration_s": int(elapsed), "exit_code": int(rc),
        "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
        "answer_chars": len(text), "turns": final.get("num_turns"),
        "skill_delivery": skill_delivery,
        # whether the model COULD fan out. Rows on either side of this are
        # not comparable — the arm is a tool-execution mechanism.
        "subagent_available": subagent == "true",
        # the model's own working file: evidence that it found the defect even
        # when it failed to deliver it. Never the score. None = it wrote none.
        "artifact_chars": (len(open(os.path.join(run_dir, "working-report.md"),
                                    encoding="utf-8", errors="replace").read())
                           if os.path.exists(os.path.join(run_dir, "working-report.md"))
                           else None)}
with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
    json.dump(meta, fh, ensure_ascii=False, indent=2)
print("  ✓ %s/%s  %ss  chars=%d  -> %s" % (case_id, arm, elapsed, len(text), run_dir))
PY
