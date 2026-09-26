#!/usr/bin/env python3
"""hostgate — the v56 trusted phase gate, run by the harness OUTSIDE the sandbox.

    python3 hostgate.py --work RUN/work/work --corpus TRUSTED/corpus \
        --package TRUSTED/v56 --run-dir RUN [finalize flags…]

Order (each stage must pass):
  1. finalize.py   reportcheck, citecheck, statecheck, triagecheck (deterministic)
  2. journalcheck  checkpoint journal vs receipts in the captured streams (G1)
  3. judge.py      Opus-on-subscription: do conclusions follow from evidence (G4)

The judge runs only when 1 and 2 pass (it costs a subscription call), unless
--judge-always. Its result goes to RUN/control/judge/<phase>.json — outside the
workspace the agent can write. judge_unavailable is a FAIL (fail closed).
Exit 0 = all pass, 1 = a stage failed, 2 = usage.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(argv, timeout):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout[-4000:], r.stderr[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--package", default=os.path.dirname(HERE))
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--phase", default="repair")
    ap.add_argument("--judge-always", action="store_true")
    ap.add_argument("--judge-timeout", type=float, default=900)
    ap.add_argument("--judge-policy", choices=("conclusions", "strict"),
                    default="conclusions")
    a, finalize_extra = ap.parse_known_args(argv)
    py = sys.executable
    tools = os.path.join(a.package, "tools")
    out = {"schema": 1, "tool": "sherlock-v56/hostgate.py", "phase": a.phase,
           "stages": {}}

    rc, so, se = run([py, os.path.join(tools, "finalize.py"), "--work", a.work,
                      "--corpus", a.corpus, "--package", a.package] + finalize_extra,
                     timeout=900)
    out["stages"]["finalize"] = {"exit": rc, "stdout_tail": so[-1500:],
                                 "stderr_tail": se[-800:]}

    streams = sorted(glob.glob(os.path.join(a.run_dir, "out", "*", "stream.jsonl")))
    if streams:
        cmd = [py, os.path.join(tools, "journalcheck.py"), "--work", a.work, "--json"]
        for s in streams:
            cmd += ["--stream", s]
        jrc, jso, jse = run(cmd, timeout=300)
        try:
            detail = json.loads(jso)
        except ValueError:
            detail = {"stdout_tail": jso[-1500:], "stderr_tail": jse[-800:]}
        out["stages"]["journalcheck"] = {"exit": jrc, "result": detail}
    else:
        out["stages"]["journalcheck"] = {"exit": 1, "result": {
            "violations": [{"code": "no_stream",
                            "detail": "no out/*/stream.jsonl under the run dir"}]}}

    deterministic_ok = all(v["exit"] == 0 for v in out["stages"].values())
    if deterministic_ok or a.judge_always:
        jout = os.path.join(a.run_dir, "control", "judge", "%s.json" % a.phase)
        jrc, jso, jse = run([py, os.path.join(tools, "judge.py"),
                             "--report", os.path.join(a.work, "report.md"),
                             "--corpus", a.corpus, "--out", jout,
                             "--timeout", str(a.judge_timeout),
                             "--policy", a.judge_policy],
                            timeout=a.judge_timeout + 120)
        out["stages"]["judge"] = {"exit": jrc, "result_file": jout,
                                  "stdout_tail": jso[-2500:], "stderr_tail": jse[-500:]}
    else:
        out["stages"]["judge"] = {"exit": None,
                                  "skipped": "deterministic gates failed"}

    out["pass"] = all(v.get("exit") == 0 for v in out["stages"].values())
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
