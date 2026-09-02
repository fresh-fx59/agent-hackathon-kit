#!/usr/bin/env python3
"""REGRESSION LOCK — the run's settings must pin the binary by disabling
qwen-code's own auto-update.

WHY. qwen-code 0.22.0 checks npm at TUI startup and STAGES a newer version
for the next launch. Found by accident on 2026-09-02: a throwaway $HOME
probe printed «Update successful! The new version will be used on your next
run.» and left `$HOME/.qwen/updates/npm/*/active.json` =
{"version": "0.22.3", "baseVersion": "0.22.0"}.

WHY IT MATTERS HERE, not in general. This harness relaunches the CLI across
a multi-hour run, and `interactive-drive.py` decides whether the target is
busy by reading the TUI's OWN FOOTER STRINGS (see FOOTER_ANCHOR /
FOOTER_BUSY_HINTS). A staged update swaps the binary mid-run, so a purely
cosmetic footer change in 0.22.3 would silently re-open the clear-swallow
bug that cost five acceptance runs — and the measurement would in any case
be two halves of one run on two different builds.

PROVEN on the real binary, red-then-green, before this lock was written
(`/home/claude-developer/probe/updprobe.py`, two throwaway $HOMEs, 90s of
interactive TUI each): default settings staged 0.22.3 and printed the update
line; `general.enableAutoUpdate: false` staged nothing and printed nothing.
The `-p` non-interactive path never staged at all, so the check is TUI-side
— which is exactly the path this harness drives.

This test locks only the SETTINGS side, which is what lives in the
repository. The behavioural half is the probe above, quoted in the vault
note's Timeline for 2026-09-02.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.normpath(os.path.join(HERE, ".."))
SETTINGS = os.path.join(MEASURE, "corporate-settings.py")

ARGS = ["emit-run", "--window", "262000", "--max-tokens", "20000",
        "--session-token-limit", "230000", "--timeout", "900000",
        "--max-retries", "0", "--skill-directory", "/opt/sherlock-arm"]


def main():
    proc = subprocess.run([sys.executable, SETTINGS] + ARGS,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL test_no_autoupdate_v44")
        print("  ✗ emit-run failed: %s" % (proc.stderr[-400:],))
        return 1
    row = json.loads(proc.stdout)

    failures = []
    general = row.get("general")
    if not isinstance(general, dict):
        failures.append("emit-run wrote no `general` block at all, so nothing "
                        "disables the auto-update that stages 0.22.3")
    else:
        got = general.get("enableAutoUpdate", "<<missing>>")
        if got is not False:
            failures.append(
                "general.enableAutoUpdate is %r, must be exactly False — "
                "anything else lets qwen stage a new version mid-run and "
                "change the footer strings the busy detector reads" % (got,))

    # The value must be the JSON literal `false`, not a truthy-ish stand-in:
    # qwen reads `settings.general?.enableAutoUpdate` and only an explicit
    # false disables the check (the source default is enabled).
    if '"enableAutoUpdate": false' not in json.dumps(row, indent=1):
        failures.append("the emitted JSON does not contain "
                        '`"enableAutoUpdate": false` verbatim')

    if failures:
        print("FAIL test_no_autoupdate_v44")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("PASS test_no_autoupdate_v44 "
          "(emit-run pins the binary: general.enableAutoUpdate=false)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
