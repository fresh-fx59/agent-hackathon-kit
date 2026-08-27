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


def profile(window=GATE, max_tokens=MAX_TOKENS):
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
        },
        # Compact earlier than 0.85 so a summary has room while the session is
        # still small. Kept as a fraction: it is one, in the target's schema.
        "context": {"autoCompactThreshold": 0.7},
        "tools": {
            "core": list(CORE_TOOLS),
            # r6's largest single tool result was 35,795 characters of a map
            # that ALREADY EXISTED ON DISK. 8,000 chars ~ 2,500 tokens.
            "truncateToolOutputThreshold": 8000,
            "truncateToolOutputLines": 200,
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


def prove(window, max_tokens, gate):
    """The worst reachable `prompt + max_tokens`, and why."""
    lines = []
    problems = []
    auto, hard = thresholds(window)
    lines.append("declared window W = %d" % window)
    lines.append("margin(W) = max(10000, 0.05*W) = %d" % margin(window))
    lines.append("auto-compaction fires at %d, the hard edge is %d"
                 % (int(auto), int(hard)))
    if window > gate:
        problems.append("the declared window %d is itself above the gate %d — a "
                        "prompt that large is refused before any output"
                        % (window, gate))
    if max_tokens < SUMMARY_RESERVE:
        problems.append("max_tokens %d is below the %d qwen reserves for a "
                        "compaction summary: the summary is cut at "
                        "finish_reason=length and the session dies with "
                        "COMPRESSION_FAILED_EMPTY_SUMMARY (measured, r5)"
                        % (max_tokens, SUMMARY_RESERVE))
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
    return lines, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("emit", "prove", "verify-bundle"))
    ap.add_argument("--gate", type=int, default=GATE)
    ap.add_argument("--window", type=int, default=None,
                    help="declared context window (default: the gate itself)")
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--bundle", default=None,
                    help="the installed @qwen-code/qwen-code directory")
    ap.add_argument("--extra-key", action="append", default=[],
                    help="also require this dotted key (used by the tests)")
    args = ap.parse_args()
    window = args.window if args.window is not None else args.gate

    if args.command == "emit":
        print(json.dumps(profile(window, args.max_tokens), indent=2,
                         sort_keys=True))
        return 0

    if args.command == "prove":
        lines, problems = prove(window, args.max_tokens, args.gate)
        for line in lines:
            print(line)
        for why in problems:
            print("✗ %s" % why)
        if problems:
            return 1
        print("✓ every request this profile can send fits the gate")
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
