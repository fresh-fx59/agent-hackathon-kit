#!/usr/bin/env python3
"""The arm gate is ONE comparison, not eight hand-maintained version chains.

MEASURED on 737de8f: `eval/bench/run-bench.sh` decided six different things with
a literal chain of ten `[ "$ARM" = "vNN" ]` tests each — the timeout, the seed
path, the settings shape, the agent install, and so on. Adding v40 means editing
every chain, and the failure mode of forgetting one is SILENT: the missed site
takes the pre-v30 branch, so the run gets a 2700-second timeout or the older
settings block while everything else believes it is running a modern arm. That
is precisely the input-gate defect docs/conventions.md names — constrain the
input once at the boundary instead of re-disambiguating it downstream.

The fix is `arm_num` / `arm_ge`. This file asserts three things:
  1. the chains are gone from the runner;
  2. `arm_ge` answers correctly, INCLUDING across the 9→10 digit boundary where
     a string comparison would put v9 above v39;
  3. a malformed arm ABORTS rather than quietly answering "false" — the fix-9
     lesson: a `case` glob that returns "not matched" silently disarms the check
     that depends on it.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
RUNNER = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def helper_source():
    """The real function block out of the real runner, by marker."""
    text = open(RUNNER, encoding="utf-8").read()
    m = re.search(r"^# >>> ARM VERSION GATE >>>$(.*?)^# <<< ARM VERSION GATE <<<$",
                  text, re.M | re.S)
    return m.group(1) if m else None


def bash(body, args):
    p = subprocess.Popen(["bash", "-c", body + "\n" + args],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    return p.returncode, out.decode().strip(), err.decode().strip()


def main():
    text = open(RUNNER, encoding="utf-8").read()
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    chains = [ln for ln in code
              if len(re.findall(r'\[ "\$ARM" = "v[0-9]+" \]', ln)) >= 2]
    check("run-bench.sh carries no multi-version $ARM chain",
          not chains, "%d line(s), first: %s" % (len(chains),
                                                 chains[0][:90] if chains else ""))

    body = helper_source()
    check("run-bench.sh defines the gate between ARM VERSION GATE markers",
          body is not None)
    if body is None:
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1

    # `none` and `unknown` are DOCUMENTED arms (run-bench.sh's own usage line,
    # and ARM defaults to `unknown`): they must answer false, never abort.
    for arm, floor, want in [("v40", 30, True), ("v39", 30, True),
                             ("v30", 30, True), ("v29", 30, False),
                             ("v9", 30, False), ("v9", 10, False),
                             ("v10", 10, True), ("v40", 31, True),
                             ("v30", 31, False), ("v1", 1, True),
                             ("none", 30, False), ("unknown", 30, False),
                             ("none", 1, False)]:
        rc, out, err = bash(body, 'arm_ge "%s" %d && echo YES || echo NO'
                            % (arm, floor))
        got = (out.splitlines()[-1] if out else "") == "YES"
        check("arm_ge %s %d -> %s" % (arm, floor, want), got == want,
              "got %r rc=%d %s" % (out, rc, err))

    # v9 vs v39 is the case a lexical test gets wrong: "v9" > "v39" as strings.
    rc, out, _ = bash(body, 'arm_num v9; arm_num v39')
    check("arm_num returns integers, so 9 and 39 order numerically",
          out.split() == ["9", "39"], out)

    for bad in ["", "v", "v4x", "40", "v-1", "v 40", "vv40", "v40 "]:
        rc, out, err = bash(body, 'arm_ge "%s" 30; echo rc=$?' % bad)
        aborted = "rc=0" not in out and (err.strip() != "")
        check("a malformed arm %r aborts loudly instead of answering false"
              % bad, aborted, "out=%r err=%r" % (out, err))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ one numeric arm gate, fail-loud on a malformed arm")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
