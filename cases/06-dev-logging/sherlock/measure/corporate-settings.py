#!/usr/bin/env python3
"""The interactive corporate lane's qwen settings — and the proof they fit 262,000.

WHY A TOOL AND NOT A DOCUMENTED SNIPPET. The corporate harness runs `qwen`
INTERACTIVELY (operator, 2026-08-27). There is no launcher there, so none of the
`SHERLOCK_*` variables the headless lane depends on exist: every budget has to
arrive through `settings.json`. Two things then go wrong silently, and this file
exists to stop both.

  1. A MISSPELLED KEY IS NOT AN ERROR — it is ignored. `contentGenerator` moved
     to `model.generationConfig`, `skipStartupContext` lives under `model`, the
     core-tool allow-list is `tools.core` and NOT `tools.coreTools`, and the
     compaction knob is `model.chatCompression.maxRecentFilesToRetain`. Every one
     of those was READ out of the installed bundle, and `verify-bundle` re-reads
     it, so a key that the target does not consume fails here instead of on a
     paid run.
  2. "IT SHOULD FIT" IS NOT A PROOF. `prove` derives the worst reachable request
     from the target's OWN constants and refuses a profile that could breach the
     gate. The numbers below come from qwen-code 0.22.0:

        outputClampMargin(W)   = max(10_000, round(0.05 * W))
        clamp(ceiling, W, p)   = min(ceiling, max(4_000, W - p - margin(W)))
        computeThresholds(W)   : effective = W - 20_000
                                 auto      = min(0.85*W, effective - 13_000)
                                 hard      = min(W, max(effective - 3_000,
                                                        auto + 3_000))

     The clamp runs on EVERY request (chunk-T6XLJRQY.js, right before `params`
     is built), so it — not our discipline — is what bounds the wire. At
     W = 262,000: margin 13,100, hard 239,000, and the largest prompt qwen will
     ever send carries at most 9,900 output tokens with it: 248,900 total.

     SUMMARY_RESERVE is `COMPACT_MAX_OUTPUT_TOKENS = 20,000`, which is why
     max_tokens is 20,000 and not the 6,700 the CloseRouter lane needed: at
     6,700 the compaction summary is cut at `finish_reason=length` and the run
     dies with COMPRESSION_FAILED_EMPTY_SUMMARY. That is exactly how r5 ended.
"""
import argparse
import json
import os
import sys

# ── the target's own constants, read from qwen-code 0.22.0 ──────────────────
SUMMARY_RESERVE = 20000        # COMPACT_MAX_OUTPUT_TOKENS
AUTOCOMPACT_BUFFER = 13000
HARD_BUFFER = 3000
DEFAULT_PCT = 0.85
MIN_CLAMPED_OUTPUT_TOKENS = 4000

GATE = 262000                  # the corporate per-request ceiling
MAX_TOKENS = SUMMARY_RESERVE   # so a compaction summary can finish

#: THE CEILING IS NOT SELF-ENFORCING, and this constant is the only thing that
#: makes it enforceable at all. Read out of chunk-T6XLJRQY.js on 2026-08-27:
#:
#:   shouldForceFromHard = !exactRoute && isHardTier
#:                         && hardRescueFailureCount < MAX_CONSECUTIVE_FAILURES(3)
#:   if (exactRoute || isHardTier && !shouldForceFromHard) compressionInfo = NOOP
#:   ... shouldStopAfterHardRescue(false, ...) === false  ->  THE PROMPT IS SENT
#:
#: with the log line «hard-tier rescue skipped after N failed attempts; relying
#: on reactive overflow recovery». So after three failed hard-tier rescues qwen
#: sends whatever it has, and `hard` (= W - 23,000) is a nudge, not a wall. That
#: is how r6 reached 334,339 tokens against this 262,000 ceiling.
#:
#: `model.sessionTokenLimit` is the precise enforcement: before each turn, if the
#: PROVIDER'S OWN reported prompt_tokens from the previous response exceeds it,
#: the turn is refused (`session_token_limit_exceeded`) and the session stops. It
#: is a real number, not an estimate — and it is one turn late by construction,
#: so it stops steady growth and cannot stop a single turn that balloons on its
#: own tool output. Hence the content caps below matter as much as this does.
SESSION_TOKEN_LIMIT = 230000

#: WITHDRAWN, 2026-08-27, and the operator's instinct is what sent me to check.
#:
#: `tools.toolOutputBatchBudget` does cap the SUM of one turn's tool responses
#: (`enforceFunctionResponseBudget` in `sendMessageStream`, verified live). But
#: the two truncation paths in this codebase are NOT equally safe, and only
#: reading them tells you which:
#:
#:   * PER-CALL truncation (`truncateToolOutputThreshold`, default 25,000 chars /
#:     1,000 lines) goes through `truncateAndSaveToFile`, which WRITES THE FULL
#:     OUTPUT TO A FILE, hands the model the absolute path, keeps the head and the
#:     tail, and marks the cut with «... [CONTENT TRUNCATED] ...». Nothing is
#:     lost: the evidence is on disk and addressable.
#:   * THE SEND-BOUNDARY budget goes through `fitText`, which names a persisted
#:     artifact ONLY when the entry carries `persistedOutputFiles` — and the
#:     send-boundary call constructs its entry inline (`callId: "send-boundary"`,
#:     `toolName: "tool-response-batch"`) with none. So the model is told «Tool
#:     output truncated.» and gets head (1/5) + tail (4/5) with NO path to the
#:     rest.
#:
#: For an arm whose entire product is COVERAGE — every worklist row accounted for,
#: every finding backed by a citable line — an unrecoverable hole in the middle of
#: a tool result is exactly the failure we cannot detect afterwards. So this lever
#: is not used. A turn that would blow the budget is better stopped by
#: SESSION_TOKEN_LIMIT, which loses no evidence at all: it ends the session and
#: the next staged session re-reads what it needs from `work/`.
TOOL_OUTPUT_BATCH_BUDGET = None

#: The tools sherlock actually uses. Everything else is schema weight: the 35
#: `computer_use__*` tools alone measured 13,748 prompt tokens on r6, and
#: cron/goal/MCP/artifact another 9,159. `list_directory` is off by default and
#: is re-enabled precisely BY appearing in this allow-list.
CORE_TOOLS = [
    "read_file", "write_file", "edit", "grep_search", "glob",
    "run_shell_command", "todo_write", "list_directory", "skill", "agent",
]

#: `project` stays enabled — that is where the sherlock skill is installed. The
#: other three levels are the 18,066-token catalogue r6 paid for on every call.
DISABLED_SKILL_LEVELS = ["bundled", "extension", "user"]


def profile(window=GATE, max_tokens=MAX_TOKENS):  # noqa: C901
    return {
        "model": {
            "generationConfig": {
                "contextWindowSize": window,
                "samplingParams": {"max_tokens": max_tokens},
            },
            # The startup context block is corpus-independent bytes on call one.
            "skipStartupContext": True,
            # A compaction that re-attaches recent FILE CONTENTS re-imports the
            # very tool output the truncation below just bounded.
            "chatCompression": {"maxRecentFilesToRetain": 0},
            # The backstop. See SESSION_TOKEN_LIMIT above for why a backstop is
            # needed at all.
            "sessionTokenLimit": SESSION_TOKEN_LIMIT,
        },
        # A SCHEDULING KNOB, NOT A SAFETY KNOB — corrected 2026-08-27. It moves
        # `auto` and `warn` earlier, so compaction fires sooner and the TYPICAL
        # prompt is smaller. It cannot move `hard`: in computeThresholds,
        # `auto <= effectiveWindow - 13000`, so `auto + 3000 <= W - 30000`, which
        # is always below `hardEdge = W - 23000`. Verified across pct from 0.01
        # to 1.0 — `hard` stays 239,000 at W = 262,000 for every one of them.
        "context": {"autoCompactThreshold": 0.7},
        "tools": {
            "core": list(CORE_TOOLS),
            # r6's largest single tool result was 35,795 characters of a map
            # that ALREADY EXISTED ON DISK. 8,000 chars ~ 2,500 tokens.
            # LEFT AT THE STOCK 25,000 chars / 1,000 lines, DELIBERATELY —
            # reversed 2026-08-27 after the operator questioned an 8,000-char cap.
            # Two reasons, and the second one invalidates a number I quoted.
            #
            # 1. A cap does not delete tokens, it RESCHEDULES them. This arm
            #    already paginates its own files: r6's peak request holds
            #    `read_file work/worklist.tsv` at offsets 0/60/116/173 and
            #    work/map.txt at six offsets, each result landing on exactly
            #    25,060-25,063 characters — the stock cap, hit dead on, followed
            #    by a request for the next page. Lower the cap and you get MORE
            #    pages, not less history. My «−52,933 tokens» estimate assumed the
            #    cut content simply vanishes; it does not, and the estimate was
            #    optimistic.
            # 2. The truncation is safe but not free: the full output IS persisted
            #    and named, so the model can and does come back for it.
            #
            # The real fix is to stop the arm needing the whole file — a batch
            # worklist interface and a map INDEX — which changes what is
            # requested instead of chopping what comes back.
        },
        "mcp": {"excluded": []},
        "skills": {"disabledLevels": list(DISABLED_SKILL_LEVELS)},
    }


def key_paths(row, prefix=""):
    for key, value in row.items():
        path = prefix + key
        yield path
        if isinstance(value, dict):
            for sub in key_paths(value, path + "."):
                yield sub


def margin(window):
    return max(10000, int(round(0.05 * window)))


def clamp(ceiling, window, prompt):
    room = window - prompt - margin(window)
    return min(ceiling, max(MIN_CLAMPED_OUTPUT_TOKENS, room))


def thresholds(window):
    effective = max(0, window - SUMMARY_RESERVE)
    proportional = DEFAULT_PCT * window
    ceiling = effective - AUTOCOMPACT_BUFFER
    auto = min(proportional, ceiling) if ceiling > 0 else proportional
    hard = min(window, max(effective - HARD_BUFFER, auto + HARD_BUFFER))
    return auto, hard


#: The measured generation-only throughput of the CloseRouter rehearsal lane
#: (r3's 142 good calls: 142,661 completion tokens in 1,164,091 ms of
#: generation). Restated from measure/lane_guard.py so this CLI can be run on
#: its own; the launcher passes lane_guard's value explicitly.
OUTPUT_TOKENS_PER_S = 122.6
#: The largest TTFT this project has ever recorded (the v38 run). NOT the 6.4 s
#: average — the average is not what kills a run.
TTFT_RESERVE_S = 35

#: THE REFUSAL THE LAUNCHER GREPS FOR. A constraint conflict must never be
#: resolved by silently taking the smaller number, which is exactly what
#: produced run 20260827T173511Z-v41.
CONFLICT = "the generation window and the compaction reserve disagree"


def fitting_output_tokens(generation_window_s, tokens_per_s=OUTPUT_TOKENS_PER_S,
                          ttft_reserve_s=TTFT_RESERVE_S):
    """The largest output budget a lane with this clock can actually deliver."""
    if not generation_window_s:
        return 0
    usable = float(generation_window_s) - float(ttft_reserve_s)
    if usable <= 0:
        return 0
    return int(usable * float(tokens_per_s))


def window_for_reserve(tokens_per_s=OUTPUT_TOKENS_PER_S,
                       ttft_reserve_s=TTFT_RESERVE_S):
    """The generation window a full compaction summary would need, in seconds."""
    return SUMMARY_RESERVE / float(tokens_per_s) + float(ttft_reserve_s)


def compaction_reachable(window, max_tokens, session_token_limit):
    """Can this session ever ASK for a compaction summary?

    qwen fires auto-compaction at `auto` (see `thresholds`). `sessionTokenLimit`
    refuses the next turn once the provider's OWN reported prompt_tokens passes
    it, so the largest prompt that can still be sent is
    `session_token_limit + max_tokens` — one whole output budget on top of the
    last measured prompt. If that total cannot reach `auto`, the session ends
    before compaction is ever requested, and a budget below the reserve is then
    a fact about a code path that cannot execute rather than a suppressed check.

    THIS IS THE ONLY RESOLUTION THAT IS NOT A SUPPRESSION, and it is a property
    of the settings, not a preference. No declared limit means reachable.
    """
    auto, _hard = thresholds(window)
    if not session_token_limit:
        return True
    return session_token_limit + max_tokens > auto


def budget_conflict(window, max_tokens, gate=GATE, generation_window_s=None,
                    tokens_per_s=OUTPUT_TOKENS_PER_S,
                    ttft_reserve_s=TTFT_RESERVE_S,
                    session_token_limit=SESSION_TOKEN_LIMIT):
    """The two output-budget constraints, judged TOGETHER. (lines, problems).

    THE DEFECT THIS EXISTS TO STOP. Run 20260827T173511Z-v41 had exactly two
    constraints on `max_tokens` and they disagreed:

      CONSTRAINT 1  the provider's generation clock. CloseRouter cuts any
                    generation at 90 s, TTFT included, and bills the truncated
                    answer — its own `upstream_timeout` payload at 90,341-90,416
                    ms across nine independent r3 calls. At 122.6 tok/s with 35 s
                    reserved for the first token, 6,743 tokens fit.
      CONSTRAINT 2  qwen's compaction reserve, COMPACT_MAX_OUTPUT_TOKENS =
                    20,000. Below it a compaction summary is cut at
                    `finish_reason=length` and the run reasons on from a
                    truncated memory (four times on v41) or dies outright with
                    COMPRESSION_FAILED_EMPTY_SUMMARY (r5).

    6,743 < 20,000, so no number satisfies both. The harness took 6,700 and
    launched. THAT is the bug, and it is not fixed by choosing the other number:
    the honest outcome is that this lane cannot safely run this workload, unless
    the settings make compaction genuinely unreachable.
    """
    lines = []
    problems = []
    fitting = fitting_output_tokens(generation_window_s, tokens_per_s,
                                    ttft_reserve_s)
    if generation_window_s:
        lines.append(
            "CONSTRAINT 1 — the provider's generation clock: a %gs window at %g "
            "tok/s with %gs reserved for the first token delivers at most %d "
            "output tokens." % (generation_window_s, tokens_per_s,
                                ttft_reserve_s, fitting))
    else:
        lines.append("CONSTRAINT 1 — the provider's generation clock: this lane "
                     "declares none, so no clock bounds the output budget.")
    lines.append(
        "CONSTRAINT 2 — qwen's compaction reserve: COMPACT_MAX_OUTPUT_TOKENS = "
        "%d, the budget a compaction summary needs to finish. Below it the "
        "summary is cut at finish_reason=length." % SUMMARY_RESERVE)
    lines.append("declared max_tokens = %d" % max_tokens)
    if generation_window_s and max_tokens > fitting:
        problems.append(
            "CONSTRAINT 1 LOSES: max_tokens %d cannot finish inside the lane's "
            "%gs generation window (%d fits) — the provider cuts the answer and "
            "bills it" % (max_tokens, generation_window_s, fitting))
    if max_tokens < SUMMARY_RESERVE:
        auto, _hard = thresholds(window)
        needed_window = window_for_reserve(tokens_per_s, ttft_reserve_s)
        largest = (session_token_limit + max_tokens) if session_token_limit else 0
        if not compaction_reachable(window, max_tokens, session_token_limit):
            lines.append(
                "RESOLVED, NOT SUPPRESSED: max_tokens %d is below the %d "
                "compaction reserve, but model.sessionTokenLimit %d ends the "
                "session first — the largest prompt that can still be sent is "
                "%d + %d = %d, and qwen only fires auto-compaction at %d. "
                "Compaction is unreachable on these settings, so the reserve is "
                "never asked for. This is a fact about the configuration, not a "
                "waived check: raise the limit above %d and this refuses again."
                % (max_tokens, SUMMARY_RESERVE, session_token_limit,
                   session_token_limit, max_tokens, largest, int(auto),
                   int(auto) - max_tokens))
        elif generation_window_s:
            problems.append(
                "%s — CONSTRAINT 1 allows at most %d output tokens on a %gs "
                "window, CONSTRAINT 2 needs %d, and %d < %d, so NO max_tokens "
                "satisfies both. Carrying the reserve would need a %ds window at "
                "%g tok/s; this lane declares %g. The harness took the smaller "
                "number silently once (run 20260827T173511Z-v41, max_tokens "
                "6700) and clipped four of its own compaction and state-snapshot "
                "summaries. Either run this workload on a lane without a %gs "
                "clock, or declare a model.sessionTokenLimit of at most %d so "
                "the session ends before compaction can fire."
                % (CONFLICT, fitting, generation_window_s, SUMMARY_RESERVE,
                   fitting, SUMMARY_RESERVE, int(window_for_reserve(
                       tokens_per_s, ttft_reserve_s)), tokens_per_s,
                   generation_window_s, generation_window_s,
                   int(auto) - max_tokens))
            lines.append("a %ds window would be needed to carry the %d-token "
                         "reserve (%.1f s of generation + %gs first-token "
                         "reserve)"
                         % (int(needed_window), SUMMARY_RESERVE,
                            SUMMARY_RESERVE / float(tokens_per_s),
                            ttft_reserve_s))
        else:
            problems.append(
                "max_tokens %d is below the %d qwen reserves for a compaction "
                "summary: the summary is cut at finish_reason=length and the "
                "session dies with COMPRESSION_FAILED_EMPTY_SUMMARY (measured, "
                "r5). Declare a model.sessionTokenLimit of at most %d if this "
                "session genuinely never compacts."
                % (max_tokens, SUMMARY_RESERVE, int(auto) - max_tokens))
    if session_token_limit and session_token_limit + max_tokens > gate:
        problems.append(
            "model.sessionTokenLimit %d plus the output budget %d exceeds the "
            "gate %d — the backstop itself permits an illegal request"
            % (session_token_limit, max_tokens, gate))
    return lines, problems


def prove(window, max_tokens, gate, generation_window_s=None,
          output_tokens_per_s=OUTPUT_TOKENS_PER_S,
          ttft_reserve_s=TTFT_RESERVE_S,
          session_token_limit=SESSION_TOKEN_LIMIT):
    """The worst reachable `prompt + max_tokens`, and why.

    `generation_window_s` exists for ONE reason and it is not a bypass. The
    corporate lane has no generation clock, so 20,000 is right there. The
    CloseRouter rehearsal lane cuts any generation at 90 seconds — TTFT included
    — and bills the truncated answer, so on THAT lane a 20,000-token budget
    (163 s at the measured 122.6 tok/s) guarantees cut turns. The two
    constraints genuinely conflict, and the conflict is a FACT about the
    rehearsal provider, not something to be voted on.

    So when a generation window is declared, the budget is DERIVED from it and
    the consequence is PRINTED rather than hidden: a compaction summary cannot
    complete on that lane, therefore compaction firing at all is a failure of
    the staging, not a survivable event. The gate arithmetic is unaffected — a
    smaller output budget can only lower the worst total.
    """
    # THE FIX-7 REGRESSION, NAMED. This used to be two separate checks: a
    # `if generation_window_s:` branch that printed the compaction consequence
    # as a "CONSEQUENCE, STATED" NOTE, and a reserve check written
    # `if max_tokens < SUMMARY_RESERVE and not generation_window_s`. Declaring a
    # generation window therefore DISARMED the one check that exists to stop a
    # budget below the compaction reserve — so `prove` would have passed the
    # exact configuration that clipped four of run 20260827T173511Z-v41's own
    # state snapshots, had anything asked it. Both are now one call, and a
    # conflict is a PROBLEM (which blocks) rather than a note.
    lines, problems = budget_conflict(
        window, max_tokens, gate, generation_window_s, output_tokens_per_s,
        ttft_reserve_s, session_token_limit)
    auto, hard = thresholds(window)
    lines.append("declared window W = %d" % window)
    lines.append("margin(W) = max(10000, 0.05*W) = %d" % margin(window))
    lines.append("auto-compaction fires at %d, the hard edge is %d"
                 % (int(auto), int(hard)))
    if window > gate:
        problems.append("the declared window %d is itself above the gate %d — a "
                        "prompt that large is refused before any output"
                        % (window, gate))
    # Walk every prompt size qwen can actually send, at the resolution that
    # matters — the clamp is piecewise linear, so the edges are what count.
    worst = 0
    worst_at = None
    probe = sorted(set([0, 1000, int(auto), int(auto) - 1, int(auto) + 1,
                        int(hard) - 1, int(hard), window - margin(window),
                        window - margin(window) - MIN_CLAMPED_OUTPUT_TOKENS]
                       + list(range(0, int(hard) + 1, 1000))))
    for prompt in probe:
        if prompt < 0 or prompt > hard:
            continue
        total = prompt + clamp(max_tokens, window, prompt)
        if total > worst:
            worst, worst_at = total, prompt
    lines.append("worst reachable request: prompt %d + output %d = %d"
                 % (worst_at, worst - worst_at, worst))
    lines.append("headroom under the gate %d: %d" % (gate, gate - worst))
    if worst > gate:
        problems.append("the worst reachable request %d EXCEEDS the gate %d "
                        "(at prompt %d)" % (worst, gate, worst_at))
    # THE HONEST PART. Everything above assumes qwen never sends a prompt above
    # `hard`. It does: after three failed hard-tier rescues the rescue is skipped
    # and the oversized prompt is SENT ANYWAY (chunk-T6XLJRQY.js —
    # `isHardTier && !shouldForceFromHard` => NOOP, and shouldStopAfterHardRescue
    # returns false). r6 sent 334,339 tokens that way. So this arithmetic bounds
    # the OBEDIENT path only, and saying otherwise would make this tool the
    # thing it exists to prevent.
    lines.append("")
    lines.append("CAVEAT, NOT A FOOTNOTE: `hard` = %d is NOT ENFORCED. After 3 "
                 "failed hard-tier rescues qwen skips the rescue and the "
                 "oversized prompt is sent anyway — r6 sent 334,339 tokens that "
                 "way. The arithmetic above bounds the obedient path only."
                 % int(hard))
    if not session_token_limit:
        problems.append("no model.sessionTokenLimit is declared, so nothing "
                        "precisely enforces the %d-token ceiling: the only exact "
                        "check qwen has is sessionTokenLimit, which refuses a "
                        "turn when the PROVIDER'S reported prompt_tokens already "
                        "exceeds it" % gate)
    else:
        lines.append("backstop: model.sessionTokenLimit %d refuses the next turn "
                     "once the provider reports more than that, so %d + %d = %d "
                     "is the worst request that can follow a measured one"
                     % (session_token_limit, session_token_limit, max_tokens,
                        session_token_limit + max_tokens))
    return lines, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command",
                    choices=("emit", "prove", "verify-bundle", "check-budget"))
    ap.add_argument("--gate", type=int, default=GATE)
    ap.add_argument("--window", type=int, default=None,
                    help="declared context window (default: the gate itself)")
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--bundle", default=None,
                    help="the installed @qwen-code/qwen-code directory")
    ap.add_argument("--session-token-limit", type=int, default=SESSION_TOKEN_LIMIT,
                    help="model.sessionTokenLimit; 0 declares none, and `prove` "
                         "then REFUSES the profile, because without it nothing "
                         "precisely enforces the ceiling")
    ap.add_argument("--generation-window-s", type=float, default=None,
                    help="the provider's hard generation clock, if it has one "
                         "(CloseRouter: 90). Derives the output budget and "
                         "states the compaction consequence out loud.")
    ap.add_argument("--output-tokens-per-s", type=float,
                    default=OUTPUT_TOKENS_PER_S,
                    help="the lane's MEASURED generation-only throughput")
    ap.add_argument("--ttft-reserve-s", type=float, default=TTFT_RESERVE_S,
                    help="seconds reserved for the first token (the largest "
                         "TTFT ever recorded here, not the average)")
    ap.add_argument("--extra-key", action="append", default=[],
                    help="also require this dotted key (used by the tests)")
    args = ap.parse_args()
    window = args.window if args.window is not None else args.gate

    if args.command == "emit":
        print(json.dumps(profile(window, args.max_tokens), indent=2,
                         sort_keys=True))
        return 0

    if args.command == "check-budget":
        # THE LAUNCH GATE. Narrow on purpose: it judges the two output-budget
        # constraints against each other and nothing else, so it can be called
        # by any launcher on any lane — including one with no generation clock
        # and no declared session limit — without demanding the whole corporate
        # profile. `prove` remains the full arithmetic proof.
        lines, problems = budget_conflict(
            window, args.max_tokens, args.gate, args.generation_window_s,
            args.output_tokens_per_s, args.ttft_reserve_s,
            args.session_token_limit)
        for line in lines:
            print(line)
        for why in problems:
            print("\u2717 %s" % why)
        if problems:
            return 1
        print("\u2713 the output budget %d satisfies every declared constraint"
              % args.max_tokens)
        return 0

    if args.command == "prove":
        lines, problems = prove(window, args.max_tokens, args.gate,
                                args.generation_window_s,
                                session_token_limit=args.session_token_limit)
        for line in lines:
            print(line)
        for why in problems:
            print("✗ %s" % why)
        if problems:
            return 1
        print("✓ every request the OBEDIENT path can send fits the gate, and "
              "model.sessionTokenLimit backstops the rest")
        return 0

    bundle = args.bundle
    if not bundle or not os.path.isdir(bundle):
        sys.stderr.write("✗ --bundle must be the installed qwen-code directory\n")
        return 2
    text = []
    for dirpath, dirnames, filenames in os.walk(bundle):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for name in filenames:
            if name.endswith(".js"):
                text.append(open(os.path.join(dirpath, name), encoding="utf-8",
                                 errors="replace").read())
    haystack = "\n".join(text)
    missing = []
    paths = list(key_paths(profile(window, args.max_tokens))) + args.extra_key
    for path in paths:
        leaf = path.rsplit(".", 1)[-1]
        # A settings key reaches the code as its own identifier, so the leaf is
        # what must appear. Checking the dotted path would pass on nothing.
        if leaf not in haystack:
            missing.append(path)
    for path in sorted(missing):
        print("✗ %s — the installed qwen-code never reads this key" % path)
    print("checked %d keys against %d bytes of bundle"
          % (len(paths), len(haystack)))
    if missing:
        return 1
    print("✓ every key in the profile is one the target actually reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
