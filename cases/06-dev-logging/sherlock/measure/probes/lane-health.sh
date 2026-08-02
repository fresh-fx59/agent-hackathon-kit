#!/usr/bin/env bash
# lane-health.sh — is the provider lane fit for a metered run RIGHT NOW?
#
#   SHERLOCK_API_KEY=... bash probes/lane-health.sh            # 3 sizes x 2 reps
#   PROBE_REPS=3 PROBE_SIZES_KB="150 300 450" bash probes/lane-health.sh
#   PROBE_BASE_URL=http://127.0.0.1:PORT/v1 bash probes/lane-health.sh   # dry run
#   PROBE_SHAPE=plain|agentic|history       # request shape (see below)
#   PROBE_TOOLS=25                          # how many tool definitions to carry
#
# WHY THIS EXISTS. The old probe sends a ~1 KB single-turn request. On 2026-08-02
# it returned 3/3 healthy and the metered run it gated immediately ran at a
# **46 % 400-rate**, because real turns carry 200–450 KB of accumulated context.
# A probe that does not measure at the size you are about to send measures
# nothing you care about. → [[metered-retries-need-a-spend-cap]]
#
# WHAT IT MEASURES, and why it is by size. Pooled over 429 logged calls the
# failure rate looks flat in request size — but that pools two regimes. Within a
# run: on a HEALTHY lane 444 KB requests ran 244 calls at 0 % failure; on a
# DEGRADED lane >=250 KB failed 52.9 % and 56.2 % against 30.8 % and 21.1 % below
# it. So size is not a cliff, it is a multiplier whose strength moves with
# provider health — and the only way to know today's multiplier is to measure it.
#
# Exit 0 = fit to run. Exit 1 = degraded, do not start a batch. Exit 2 = dead.
set -uo pipefail

BASE_URL="${PROBE_BASE_URL:-${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
SIZES_KB="${PROBE_SIZES_KB:-100 250 400}"
REPS="${PROBE_REPS:-2}"
# A batch is worth starting at <=10 % and never at >=35 %; between the two it is
# the operator's call, so say so rather than guessing on their behalf.
OK_PCT="${PROBE_OK_PCT:-10}"
BAD_PCT="${PROBE_BAD_PCT:-35}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY (use with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ...)}"

# A real turn STREAMS and carries a tools block. A probe that sends neither is
# measuring a different request than the one you are about to pay for — which is
# how a 3/3 "healthy" verdict gated a run that then failed 46 % of its calls.
SHAPE="${PROBE_SHAPE:-history}"
NTOOLS="${PROBE_TOOLS:-25}"
export PROBE_URL="$BASE_URL" PROBE_MODEL="$MODEL" PROBE_SIZES="$SIZES_KB" \
       PROBE_N="$REPS" PROBE_OK="$OK_PCT" PROBE_BAD="$BAD_PCT" PROBE_SHAPE="$SHAPE" \
       PROBE_TOOLS="$NTOOLS"

python3 - <<'PY'
import json, os, sys, time, urllib.request

url = os.environ["PROBE_URL"].rstrip("/") + "/chat/completions"
model = os.environ["PROBE_MODEL"]
key = os.environ["SHERLOCK_API_KEY"]
sizes = [int(s) for s in os.environ["PROBE_SIZES"].split()]
reps = int(os.environ["PROBE_N"])
ok_pct, bad_pct = float(os.environ["PROBE_OK"]), float(os.environ["PROBE_BAD"])

# Realistic filler: a log line, because that is what a real turn is carrying.
# A block of one repeated character is not the same request to a tokenizer.
LINE = ('2026-07-28 11:05:12.771 DEBUG 1 --- [http-nio-8080-exec-7] '
        'c.a.catalog.repo.VendorRefLookupRepository: executing SELECT c.sku_id, '
        "c.title FROM catalog_items c WHERE c.attrs ->> 'vendor_ref' = ? "
        'took 1188ms rows=3 [traceId=77e67d2af33d409ea6266fd3592eff40]\n')


shape = os.environ.get("PROBE_SHAPE", "agentic")

ntools = int(os.environ.get("PROBE_TOOLS", "25"))

# qwen-code 0.21.1 offers ~25 core tools on every turn. A probe carrying ONE is
# not carrying what the run carries.
TOOL_NAMES = ["read_file", "list_directory", "grep_search", "glob", "edit",
              "write_file", "run_shell_command", "todo_write", "web_fetch",
              "skill", "notebook_edit", "zoom_image", "record_artifact",
              "read_mcp_resource", "list_agents", "task_stop", "send_message",
              "enter_worktree", "exit_worktree", "cron_create", "cron_list",
              "cron_delete", "loop_wakeup", "create_sub_session", "monitor"]

TOOLS = [{"type": "function", "function": {
    "name": n, "description": "Tool %s used by the agent loop" % n,
    "parameters": {"type": "object", "properties": {
        "file_path": {"type": "string"}, "offset": {"type": "integer"},
        "limit": {"type": "integer"}}, "required": []}}}
    for n in TOOL_NAMES[:ntools]]


def body_of(kb):
    filler, i = [], 0
    while sum(map(len, filler)) < kb * 1024:
        filler.append("%06d " % i + LINE)
        i += 1
    text = "".join(filler)
    msgs = [{"role": "user", "content": text + "\nReply with one word: ok"}]
    if shape == "history":
        # What a REAL turn 5 looks like: assistant tool_calls and tool results
        # interleaved. On 2026-08-02 a run failed 9 of 12 calls at 143-171 KB
        # while this probe passed 6/6 at the SAME sizes two minutes later — the
        # only remaining difference was this history and the tool count.
        head = text[: len(text) // 2]
        tail = text[len(text) // 2:]
        msgs = [{"role": "user", "content": head}]
        for i in range(4):
            cid = "call_%d" % i
            msgs.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": cid, "type": "function", "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"file_path": "/c/apps/api.log",
                                             "offset": i * 100, "limit": 100})}}]})
            msgs.append({"role": "tool", "tool_call_id": cid,
                         "content": tail[i * (len(tail) // 5):(i + 1) * (len(tail) // 5)]})
        msgs.append({"role": "user", "content": "Reply with one word: ok"})
    b = {"model": model, "max_tokens": 16, "messages": msgs}
    if shape in ("agentic", "history"):
        b["stream"] = True
        b["tools"] = TOOLS
    return json.dumps(b).encode("utf-8")


rows, total_bytes = [], 0
for kb in sizes:
    payload = body_of(kb)
    for r in range(reps):
        t0 = time.time()
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
            if shape == "agentic":
                name = None
                for ln in raw.decode("utf-8", "replace").splitlines():
                    if ln.startswith("data: ") and '"model"' in ln:
                        try:
                            name = json.loads(ln[6:]).get("model")
                        except ValueError:
                            pass
                        if name:
                            break
            else:
                name = json.loads(raw).get("model")
            rows.append((kb, 200, time.time() - t0, name))
        except Exception as e:
            code = getattr(e, "code", None) or type(e).__name__
            rows.append((kb, code, time.time() - t0, None))
        total_bytes += len(payload)

print("shape=%s tools=%d" % (shape, len(TOOLS)))
print("size   ok/total   median s   returned")
worst = 0.0
for kb in sizes:
    g = [r for r in rows if r[0] == kb]
    good = [r for r in g if r[1] == 200]
    fail = 100.0 * (len(g) - len(good)) / len(g)
    worst = max(worst, fail)
    med = sorted(r[2] for r in g)[len(g) // 2]
    names = sorted({r[3] for r in good if r[3]})
    print("%4dK  %d/%-8d %6.1f     %s" % (kb, len(good), len(g), med,
                                          ",".join(names) or "-"))
print("\nuploaded %.2f MB across %d calls; worst-size fail rate %.1f%%"
      % (total_bytes / 1048576, len(rows), worst))

if worst >= bad_pct:
    print("VERDICT: DEGRADED — do not start a metered batch")
    sys.exit(1)
if worst <= ok_pct:
    print("VERDICT: HEALTHY — fit to run")
    sys.exit(0)
print("VERDICT: MARGINAL (%.1f%%) — operator's call" % worst)
sys.exit(1)
PY
