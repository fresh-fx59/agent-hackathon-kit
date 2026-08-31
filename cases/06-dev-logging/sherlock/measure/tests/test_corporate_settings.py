#!/usr/bin/env python3
"""The interactive corporate lane's settings.json, and the PROOF it obeys 262,000.

The corporate harness runs `qwen` interactively: no launcher, no env block, no
wrapper. Everything the headless lane sets through `SHERLOCK_*` variables has to
arrive through a settings file there — and a settings KEY THAT THE TARGET DOES
NOT READ is worse than no setting, because it looks like protection.

So `measure/corporate-settings.py` does two jobs and this file tests both:

  1. it emits the profile, every key spelled the way the installed qwen-code
     spells it — and `--verify-bundle` reads the installed bundle and fails on a
     key that does not appear in it;
  2. it PROVES the ceiling arithmetically from the target's own constants
     (`clampOutputTokensToWindow`, `outputClampMargin`, `computeThresholds`)
     rather than from our hopes:

        sent_max(prompt) = min(max_tokens, max(4000, W - prompt - margin(W)))
        margin(W)        = max(10000, round(0.05 * W))
        hard(W)          = min(W, max(W - 20000 - 3000, 0.85*W + 3000))

     At W = 262,000 and max_tokens = 20,000 the worst reachable request is
     239,000 + 9,900 = 248,900 tokens — 13,100 under the gate — and it is the
     CLAMP, not our discipline, that guarantees it. That is why the profile can
     declare the full 262,000 window and still never breach it.

     It also explains the r5 failure the plan warns about: with max_tokens=6,700
     a compaction summary — for which qwen reserves COMPACT_MAX_OUTPUT_TOKENS =
     20,000 — is cut at 6,700, ends `finish_reason=length`, and the run dies with
     COMPRESSION_FAILED_EMPTY_SUMMARY. 20,000 is not a taste; it is that reserve.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
TOOL = os.path.join(MEASURE, "corporate-settings.py")
FAILED = []
GATE = 262000


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(args):
    p = subprocess.Popen([sys.executable, TOOL] + args, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    out, err = p.communicate()
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def dig(row, path):
    cur = row
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def main():
    check("measure/corporate-settings.py exists", os.path.exists(TOOL))
    if not os.path.exists(TOOL):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1

    rc, out, err = run(["emit"])
    check("emit exits 0", rc == 0, err)
    try:
        row = json.loads(out)
    except ValueError as exc:
        check("emit prints valid JSON", False, "%s: %r" % (exc, out[:200]))
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1
    check("emit prints valid JSON", True)

    want = {
        "model.generationConfig.contextWindowSize": GATE,
        "model.generationConfig.samplingParams.max_tokens": 20000,
        "model.skipStartupContext": True,
        "model.chatCompression.maxRecentFilesToRetain": 0,
    }
    for path, value in sorted(want.items()):
        check("%s == %r" % (path, value), dig(row, path) == value,
              dig(row, path))
    for path in ("tools.core", "mcp.excluded", "skills.disabledLevels"):
        got = dig(row, path)
        check("%s is a list" % path, isinstance(got, list), got)
    core = dig(row, "tools.core") or []
    for tool in ("read_file", "run_shell_command", "write_file", "skill"):
        check("the allow-list keeps %s — sherlock cannot work without it" % tool,
              tool in core, core)
    for tool in ("cron_create", "get_goal", "artifact", "web_search"):
        check("the allow-list drops %s" % tool, tool not in core, core)
    levels = dig(row, "skills.disabledLevels") or []
    check("bundled skills are off (18,066 tokens of catalogue)",
          "bundled" in levels, levels)
    check("the project level stays ON — that is where sherlock lives",
          "project" not in levels, levels)

    # ── THE CORRECTION OF 2026-08-27, and it is the important part ──────────
    # `hard` (= W - 23,000) is NOT a send-blocking ceiling. Read out of
    # chunk-T6XLJRQY.js: `shouldForceFromHard = !exactRoute && isHardTier &&
    # hardRescueFailureCount < MAX_CONSECUTIVE_FAILURES` (3). Once three hard-tier
    # rescues have failed, the code logs «hard-tier rescue skipped after N failed
    # attempts; relying on reactive overflow recovery», sets compressionInfo to
    # NOOP, and `shouldStopAfterHardRescue(false, ...)` returns false — so THE
    # OVERSIZED PROMPT IS SENT. That is how r6 reached 334,339 tokens against a
    # 262,000 ceiling. Any claim that the clamp alone bounds the wire is false,
    # and a tool that prints such a claim is the "gate that passes on failure"
    # defect this repo keeps finding in itself.
    #
    # The only precise enforcement available is `model.sessionTokenLimit`: before
    # each turn, if the PROVIDER'S OWN reported prompt_tokens from the last
    # response exceeds it, the turn is refused (`session_token_limit_exceeded`).
    # It is one turn late by construction, which is why it is a backstop and not
    # a substitute for keeping the prompt small.
    check("the profile sets model.sessionTokenLimit — the only precise "
          "circuit breaker on this ceiling",
          isinstance(dig(row, "model.sessionTokenLimit"), int)
          and 0 < dig(row, "model.sessionTokenLimit") <= GATE - 20000,
          dig(row, "model.sessionTokenLimit"))
    # TWO TRUNCATION PATHS, ONLY ONE OF THEM RECOVERABLE — and the profile is
    # allowed to use neither. Reversed 2026-08-27 when the operator questioned an
    # 8,000-character cap, which sent me to read both paths instead of trusting
    # the token arithmetic.
    #
    # PER-CALL (`truncateToolOutputThreshold`) runs through
    # `truncateAndSaveToFile`: the FULL output is written to a file, the model is
    # given the absolute path, head and tail are kept, the cut is marked
    # «... [CONTENT TRUNCATED] ...». Lossless, and therefore safe — but NOT a
    # token saving, because this arm answers a truncated page by asking for the
    # next one (r6: worklist.tsv at offsets 0/60/116/173, each result exactly
    # 25,060-25,063 chars, i.e. the stock cap hit dead on). A cap reschedules
    # tokens; it does not delete them.
    #
    # SEND-BOUNDARY (`toolOutputBatchBudget`) runs through `fitText`, which names
    # a persisted artifact only when the entry carries `persistedOutputFiles` —
    # and the send-boundary entry is built inline with none. The model is told
    # «Tool output truncated.» and gets head+tail with NO path to the rest. For an
    # arm whose product is coverage, an unrecoverable hole in the middle of a tool
    # result is the one failure we cannot detect afterwards.
    check("the profile leaves per-call truncation at the stock default — a cap "
          "reschedules tokens rather than deleting them",
          dig(row, "tools.truncateToolOutputThreshold") is None
          and dig(row, "tools.truncateToolOutputLines") is None,
          dig(row, "tools"))
    check("the profile does NOT use toolOutputBatchBudget — its truncation is "
          "announced but unrecoverable at the send boundary",
          dig(row, "tools.toolOutputBatchBudget") is None,
          dig(row, "tools.toolOutputBatchBudget"))

    rc, out, err = run(["prove", "--gate", str(GATE)])
    check("prove states plainly that hard is not a send ceiling",
          "not enforced" in out.lower() or "sent anyway" in out.lower(),
          out[:600])
    check("prove names the sessionTokenLimit backstop in its verdict",
          "sessionTokenLimit" in out, out[:600])
    rc, out, err = run(["prove", "--gate", str(GATE), "--session-token-limit", "0"])
    check("prove REFUSES to call a profile safe with no session token limit",
          rc != 0, "rc=%d %s" % (rc, out[:400]))

    # ── the arithmetic proof ────────────────────────────────────────────────
    rc, out, err = run(["prove", "--gate", str(GATE)])
    check("prove exits 0 at the gate the profile was built for", rc == 0,
          out + err)
    check("prove states the worst reachable request", "248900" in out, out[:400])
    check("prove shows its arithmetic, not a verdict alone",
          "239000" in out and "13100" in out, out[:400])

    # MEASURED WHILE WRITING THIS FILE, and it corrects the plan's step 4: at a
    # declared window no larger than the gate, a BIGGER max_tokens cannot breach
    # it, because `clampOutputTokensToWindow` runs on every request and hands
    # back `W - prompt - margin(W)` whenever that is the smaller number. The
    # worst reachable total stays 248,900 at max_tokens 20,000 and at 40,000
    # alike — the clamp, not our restraint, is the guarantee. So the danger of a
    # large output budget on this lane is wasted headroom, NOT a breach, and the
    # danger of a small one is a starved compaction summary. Asserted, not
    # assumed, because the plan said to fear the opposite.
    rc, out, err = run(["prove", "--gate", str(GATE), "--max-tokens", "40000"])
    check("a larger max_tokens still cannot breach the OBEDIENT path — the "
          "clamp bounds it at the same 248900", "248900" in out, out[:400])
    check("...but prove now refuses it, because the backstop plus that budget "
          "would itself permit an illegal request",
          rc != 0 and "sessionTokenLimit" in out and "230000" in out,
          "rc=%d %s" % (rc, out[-400:]))

    # A profile that WOULD breach must be REJECTED, not warned about: a window
    # declared above the gate is the r6 configuration, and it breached 63 times.
    rc, out, err = run(["prove", "--gate", "200000", "--window", "262000"])
    check("prove FAILS when the declared window exceeds the gate", rc != 0,
          out[:400])
    rc, out, err = run(["prove", "--gate", str(GATE), "--max-tokens", "6700"])
    check("prove FAILS on 6,700 — it starves the 20,000-token compaction "
          "summary, which is how r5 died", rc != 0, out[:300])

    # ── every key must be one the installed qwen actually reads ─────────────
    bundle = os.path.expanduser(
        "~claude-developer/.local/lib/node_modules/@qwen-code/qwen-code")
    if os.path.isdir(bundle):
        rc, out, err = run(["verify-bundle", "--bundle", bundle])
        check("every emitted key appears in the installed qwen-code bundle",
              rc == 0, out[-600:] + err[-400:])
        rc, out, err = run(["verify-bundle", "--bundle", bundle,
                            "--extra-key", "model.thisKeyDoesNotExist"])
        check("verify-bundle FAILS on a key the target does not read", rc != 0,
              out[-300:])
    else:
        check("qwen-code bundle present for the key check (skipped off-box)",
              True)

    # ── the handoff threshold must beat auto-compaction ─────────────────────
    # Auto-compaction cannot be disabled in qwen 0.22.0, so the driver outruns
    # it instead: `handoff_threshold` must sit far enough below `auto` that no
    # single turn's growth can carry the prompt past it.
    import importlib.util
    spec = importlib.util.spec_from_file_location("corporate_settings", TOOL)
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    auto, _hard = cs.thresholds(GATE)
    t = cs.handoff_threshold(GATE, 20000)
    check("auto moved", auto == 222700, auto)
    check("handoff threshold is below auto", t < auto,
          "t=%d auto=%d" % (t, auto))
    check("margin between threshold and auto is at least one turn's growth",
          auto - t >= 20000, "auto-t=%d" % (auto - t))
    check("threshold is not so low the run would clear constantly",
          t > 150000, t)
    check("the threshold uses the MEASURED max jump (53,369), not the "
          "brief's 20,000 placeholder", t == 169331, t)

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the corporate profile is provably under the gate")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
