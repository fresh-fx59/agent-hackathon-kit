#!/usr/bin/env python3
"""SHERLOCK_INTERACTIVE=1 changes ONE thing: who types. Not the measurements.

The acceptance gate for the corporate lane has to be an interactive run
(CLAUDE.md: a gate must run the exact target). The temptation is a second
launcher — and that is how two lanes stop being comparable. So the interactive
arm lives inside run-bench.sh and inherits the proxy, the ledger, the settings
snapshot, the lane guard, the gates and the cost accounting unchanged; only the
child invocation differs.

What is asserted here:
  * the flag is validated AT THE BOUNDARY and refuses an arm with no stage
    machine — a driver with no boundary to wait for reports STAGE_TIMEOUT on a
    perfectly healthy run, which is the worst kind of false negative;
  * the interactive path does NOT resume. Interactively the stage loop is the
    recovery, and `--resume` would hand a fresh session someone else's history —
    exactly the accumulation being fixed;
  * it still writes attempts.jsonl, the exit file and out.json, because a run
    measured differently cannot be compared with r6.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(MEASURE)
RUNNER = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def launch(arm, value):
    """Run the runner far enough to hit the boundary check, never further."""
    env = dict(os.environ)
    env["SHERLOCK_INTERACTIVE"] = value
    env.pop("SHERLOCK_CORPUS", None)
    p = subprocess.Popen(["bash", RUNNER, arm], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env,
                         cwd=tempfile.mkdtemp(prefix="wiring-"))
    out, err = p.communicate(timeout=90)
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def main():
    text = open(RUNNER, encoding="utf-8").read()
    body = re.search(r"run_qwen_interactive\(\) \{(.*?)\n\}", text, re.S)
    check("run-bench.sh defines run_qwen_interactive", body is not None)
    if body:
        fn = body.group(1)
        check("it drives measure/interactive-drive.py",
              "interactive-drive.py" in fn)
        check("it never passes --resume — the stage loop is the recovery",
              "--resume" not in fn)
        check("it never passes -p — an interactive session is typed into",
              not re.search(r"(?<!\S)-p(?!\S)", fn))
        for artifact in ("attempts.jsonl", "out.json", "exit-attempt-0.txt",
                         "ATTEMPT_FINISHED"):
            check("it still writes %s, so the run stays comparable" % artifact,
                  artifact in fn)
        check("it keeps the same credential path as the headless arm",
              "OPENAI_API_KEY" in fn and "OPENAI_BASE_URL" in fn)
        check("it is bounded by the same run timeout", '"$TIMEOUT"' in fn)

    check("the interactive branch skips the resume loop",
          re.search(r'if \[ "\$INTERACTIVE" = "1" \]; then\s*\n\s*if run_qwen_interactive',
                    text) is not None)

    rc, out, err = launch("v40", "2")
    check("an SHERLOCK_INTERACTIVE that is not 0 or 1 aborts",
          rc == 2 and "SHERLOCK_INTERACTIVE" in err, "rc=%d %s" % (rc, err[:200]))
    rc, out, err = launch("v39", "1")
    check("interactive on a pre-v40 arm aborts — v39 has no stage machine",
          rc == 2 and "v40" in err, "rc=%d %s" % (rc, err[:200]))
    rc, out, err = launch("v40", "0")
    check("the headless default is untouched (it fails later, on the corpus)",
          rc != 2 or "SHERLOCK_INTERACTIVE" not in err,
          "rc=%d %s" % (rc, err[:200]))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ one lane, two ways of typing into it")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
