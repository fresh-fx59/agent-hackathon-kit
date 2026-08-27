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
        "tools.truncateToolOutputThreshold": 8000,
        "tools.truncateToolOutputLines": 200,
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
    check("a larger max_tokens still cannot breach — the clamp bounds it",
          rc == 0 and "248900" in out, out[:400])

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

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the corporate profile is provably under the gate")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
