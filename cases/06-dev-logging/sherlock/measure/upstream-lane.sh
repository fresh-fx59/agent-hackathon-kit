#!/usr/bin/env bash
# upstream-lane.sh — put the logging proxy between a qwen-code run and the provider.
#
#   . "$HERE/upstream-lane.sh"
#   upstream_lane_start <upstream_base> <log_path> <run_tag> <model>
#   # then use: $LANE_BASE_URL  $LANE_CLIENT_MODEL  $LANE_PROXY_PID
#
# Sourced, not executed, because it hands three values back to the caller.
#
# WHY THIS IS SHARED. It used to be inline in run-case.sh only, and run-bench.sh
# kept talking to the provider directly — no attribution on any bench row, and,
# after 2026-08-02, still the 177,000-token ceiling that run-case.sh had escaped.
# Two runners, one wire: this is the wire.
#
# It does three things, and the third is the one people forget:
#
#  1. ATTRIBUTION. `[SP]deepseek-v4-flash` is an ALIAS: over 40 byte-identical
#     requests it answered as two identities ~19x apart on whether they emit a
#     tool call. qwen-code stamps only the REQUESTED name, so without a
#     pass-through no recorded row can ever be attributed to an upstream.
#
#  2. THE MODEL-ID SPLIT. qwen-code sizes its context window from the model id
#     STRING. Its own normalize() turns "[SP]deepseek-v4-flash" into
#     "[sp]deepseek-v4-flash", which matches nothing in its table, so it falls
#     back to DEFAULT_TOKEN_LIMIT = 200,000 — and the "177,000 hard limit" error
#     follows from that. The same table gives the clean id /^deepseek-v4/ =>
#     1,000,000. Verified by running qwen-code's own normalize() against its own
#     table. The provider needs the prefix to route; qwen-code must not see it.
#     So: the CLI gets the clean id, the proxy restores the alias on the way out.
#
#  3. RIDING OUT A BURST. linkapi's 400s are transient and minute-scale, and are
#     NOT explained by request size or shape — both were controlled for on
#     2026-08-02 and 12/12 interleaved calls succeeded at the size and shape that
#     had failed minutes before. What kills runs is that qwen-code's own retry
#     budget is SHORTER than a burst: D11 took 4 x 400 at 143 KB then a 200, then
#     5 x 400 at 171 KB and the run ended — 98,515 tokens billed, no row. The
#     proxy waits longer than the client will, and records every attempt because
#     every retry re-uploads the context and is therefore billed.
#
#  4. A FALLBACK THAT CANNOT MAKE THINGS WORSE. If the proxy does not come up,
#     the caller gets the DIRECT url and the ALIASED id — because with nothing in
#     the path to restore the prefix, a stripped id is a 404. Never abort a
#     metered run over a local helper; say so loudly instead. The ABSENCE of the
#     log is the signal: unmeasured is null, never a guess.
#
# Opt out entirely with SHERLOCK_UPSTREAM_LOG=0.

# shellcheck disable=SC2034   # these are the helper's return values
upstream_lane_start() {
  local up_base="${1:?upstream_lane_start <upstream_base> <log> <tag> <model>}"
  local log_path="${2:?}" run_tag="${3:?}" model="${4:?}"
  local inflight_path="${5:-}" attempt_path="${6:-}"
  local here proxy port strict="${SHERLOCK_REQUIRE_ATTRIBUTION:-0}" budget_state=""
  local -a budget_env=()

  # Defaults are the safe ones, so every early return below is already correct.
  LANE_BASE_URL="$up_base"
  LANE_CLIENT_MODEL="$model"
  LANE_PROXY_PID=""

  if [ "${SHERLOCK_UPSTREAM_LOG:-1}" != "1" ]; then
    [ "$strict" != 1 ] || {
      echo "  ✗ upstream lane: attribution is required but logging is disabled" >&2
      return 1
    }
    return 0
  fi

  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  proxy="${UPSTREAM_LANE_PROXY:-$here/upstream-log-proxy.py}"
  if [ ! -f "$proxy" ]; then
    echo "  ⚠ upstream lane: no proxy at $proxy — running WITHOUT attribution" >&2
    [ "$strict" != 1 ] || return 1
    return 0
  fi

  port="$(python3 -c 'import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

# KEEP THE BODIES BY DEFAULT ON A REAL RUN. The ledger records rich metadata per
# call and no content, so "what exactly was sent, and what exactly came back?"
# was unanswerable after the fact: diagnosing an empty HTTP 200 meant reading the
# CLI's minified bundle, and the prompt as sent existed nowhere. Bodies live
# beside the ledger, gzipped, one file per request — see the proxy's REPLAYABLE
# TRACES note. They contain the full prompt, hence corpus log content, so they
# are exactly as sensitive as the corpus. Set SHERLOCK_UPSTREAM_BODIES=0 to opt
# out; the proxy writes nothing at all when UPSTREAM_BODY_DIR is empty.
  local body_dir=""
  if [ "${SHERLOCK_UPSTREAM_BODIES:-1}" = "1" ]; then
    body_dir="${log_path%.jsonl}.bodies"
    mkdir -p "$body_dir" 2>/dev/null || body_dir=""
  fi

  if [ "$strict" = 1 ]; then
    if [ -z "$inflight_path" ] || [ -z "${SHERLOCK_EXPECTED_RETURNED_IDENTITY:-}" ] || \
       [ -z "${SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS:-}" ] || \
       [ -z "${SHERLOCK_BUDGET_MAX_REQUEST_BYTES:-}" ] || \
       [ -z "${SHERLOCK_BUDGET_MAX_WALL_SECONDS:-}" ] || \
       [ -z "${SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES:-}" ]; then
      echo "  ✗ upstream lane: controlled attribution requires trace path, identity, and four caps" >&2
      return 1
    fi
    budget_state="$(dirname "$inflight_path")/upstream-budget-state.json"
    budget_env=(
      "UPSTREAM_BUDGET_STATE=$budget_state"
      "UPSTREAM_EXPECTED_RETURNED_IDENTITY=$SHERLOCK_EXPECTED_RETURNED_IDENTITY"
      "UPSTREAM_MAX_UPSTREAM_ATTEMPTS=$SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS"
      "UPSTREAM_MAX_REQUEST_BYTES=$SHERLOCK_BUDGET_MAX_REQUEST_BYTES"
      "UPSTREAM_MAX_WALL_SECONDS=$SHERLOCK_BUDGET_MAX_WALL_SECONDS"
      "UPSTREAM_MAX_CONSECUTIVE_PROVIDER_FAILURES=$SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES"
    )
  fi

  if [ "$strict" = 1 ]; then
    env UPSTREAM_BASE="$up_base" UPSTREAM_LOG="$log_path" UPSTREAM_INFLIGHT="$inflight_path" \
      RUN_TAG="$run_tag" RUN_ATTEMPT_FILE="$attempt_path" UPSTREAM_MODEL="$model" LISTEN_PORT="$port" \
      UPSTREAM_RETRY_MAX="${SHERLOCK_UPSTREAM_RETRY:-6}" \
      UPSTREAM_RETRY_BASE_MS="${SHERLOCK_UPSTREAM_RETRY_BASE_MS:-2000}" \
      UPSTREAM_BODY_DIR="$body_dir" "${budget_env[@]}" \
      python3 "$proxy" >/dev/null 2>>"${log_path%.jsonl}.proxy.err" &
  else
    # Bash 3.2 treats an empty-array expansion as unbound under `set -u`.
    # Keep the legacy path separate so the strict-only variables do not leak.
    env UPSTREAM_BASE="$up_base" UPSTREAM_LOG="$log_path" UPSTREAM_INFLIGHT="$inflight_path" \
      RUN_TAG="$run_tag" RUN_ATTEMPT_FILE="$attempt_path" UPSTREAM_MODEL="$model" LISTEN_PORT="$port" \
      UPSTREAM_RETRY_MAX="${SHERLOCK_UPSTREAM_RETRY:-6}" \
      UPSTREAM_RETRY_BASE_MS="${SHERLOCK_UPSTREAM_RETRY_BASE_MS:-2000}" \
      UPSTREAM_BODY_DIR="$body_dir" \
      python3 "$proxy" >/dev/null 2>>"${log_path%.jsonl}.proxy.err" &
  fi
  LANE_PROXY_PID=$!

  if python3 - "$port" <<'PY'
import sys, time, urllib.request
for _ in range(100):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/healthz" % sys.argv[1],
                                    timeout=1) as r:
            r.read()
        sys.exit(0)
    except Exception:
        time.sleep(0.05)
sys.exit(1)
PY
  then
    LANE_BASE_URL="http://127.0.0.1:$port/v1"
    # Strip a leading bracketed routing tag — `[SP]`, `[FREE]`, whatever the
    # provider prefixes next. Generic on purpose: the bug is "the client is shown
    # a routing tag it parses as part of the model name", not this one tag. An id
    # with no tag comes out unchanged and the rewrite is a no-op.
    LANE_CLIENT_MODEL="$(printf '%s' "$model" | sed -E 's/^\[[^]]+\]//')"
  else
    echo "  ⚠ upstream lane: proxy did not start — running WITHOUT attribution" >&2
    kill "$LANE_PROXY_PID" 2>/dev/null
    wait "$LANE_PROXY_PID" 2>/dev/null || true
    LANE_PROXY_PID=""
    [ "$strict" != 1 ] || return 1
  fi
  return 0
}
