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

Part B (docs/specs/2026-09-27-replay-journalcheck-and-repair-loop-spec.md, kit side):
this file is NOT one of the tested skills/vNN trees — those must not be edited in
place (bump-on-change rule). It is a separate, versioned harness location
(measure/citebench/harness/v1/) that citebench.py stages as its own trusted dir
and points the gate argv at, while --package still resolves to the real,
untouched skill package (v62/v63/…) for finalize.py/journalcheck.py/judge.py.

Every stage's FULL stdout/stderr is written to
RUN/control/gates/<phase>/<stage>.{stdout,stderr} (no truncation) with a
sha256 digest + byte count recorded per stage; a short tail (last 300 chars)
stays in the JSON for quick reading. This replaces the old stdout[-4000:]/
stderr[-2000:] (and per-stage 1500/800/2500/500) truncation that hid the
finalize/journalcheck/judge output behind a 2000-char wall (r3 postmortem).

Item 3 (retried-attempt streams): qwen-run renames a retried attempt's stream
to stream.a1.jsonl (runner.py:367). The old glob "out/*/stream.jsonl" missed
it, giving a false receipt_unprinted for a receipt printed before a transport
retry. Now globs "out/*/stream*.jsonl".

Part A: journalcheck runs from THIS harness dir (journalcheck.py next to this file,
loading the package's own checks via --tools). If RUN/control/trusted/seed/manifest.json
exists ([{file, sha256}], staged by citebench.py from the seed run's seal), each entry
is passed as a pinned --prior-stream/--prior-sha256 pair.

`blocking` = {finalize: sum of finalize's gates[*].parsed_blocking (1 if unparseable
and finalize failed), journalcheck: n}. qwen-run's no_progress rule sums it.
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TAIL = 300


def run(argv, timeout):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout


def _write_full(run_dir, phase, stage, out, err):
    """Write full stdout/stderr to RUN/control/gates/<phase>/<stage>.{stdout,stderr}.
    Returns the stage record fields: exit is set by the caller."""
    d = os.path.join(run_dir, "control", "gates", phase)
    os.makedirs(d, exist_ok=True)
    stdout_rel = os.path.join("control", "gates", phase, stage + ".stdout")
    stderr_rel = os.path.join("control", "gates", phase, stage + ".stderr")
    with open(os.path.join(run_dir, stdout_rel), "w") as fh:
        fh.write(out)
    with open(os.path.join(run_dir, stderr_rel), "w") as fh:
        fh.write(err)
    return {
        "stdout_file": stdout_rel, "stdout_bytes": len(out.encode()),
        "stdout_sha256": hashlib.sha256(out.encode()).hexdigest(), "stdout_tail": out[-TAIL:],
        "stderr_file": stderr_rel, "stderr_bytes": len(err.encode()),
        "stderr_sha256": hashlib.sha256(err.encode()).hexdigest(), "stderr_tail": err[-TAIL:],
    }


def _finalize_blocking(rc, stdout):
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        gates = obj.get("gates") if isinstance(obj, dict) else None
        if isinstance(gates, dict):
            vals = [g.get("parsed_blocking") for g in gates.values() if isinstance(g, dict)]
            n = sum(v for v in vals if isinstance(v, int) and not isinstance(v, bool))
            return n if (n or rc == 0) else 1
    return 0 if rc == 0 else 1


def _seed_priors(run_dir):
    seed = os.path.join(run_dir, "control", "trusted", "seed")
    man = os.path.join(seed, "manifest.json")
    if not os.path.isfile(man):
        return []
    with open(man, encoding="utf-8") as fh:
        entries = json.load(fh)
    return [(os.path.join(seed, e["file"]), e["sha256"]) for e in entries]


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
    out = {"schema": 2, "tool": "citebench-harness-v1/hostgate.py", "phase": a.phase,
           "stages": {}, "blocking": {}}

    rc, so, se = run([py, os.path.join(tools, "finalize.py"), "--work", a.work,
                      "--corpus", a.corpus, "--package", a.package] + finalize_extra,
                     timeout=900)
    out["stages"]["finalize"] = dict(_write_full(a.run_dir, a.phase, "finalize", so, se), exit=rc)
    out["blocking"]["finalize"] = _finalize_blocking(rc, so)

    streams = sorted(glob.glob(os.path.join(a.run_dir, "out", "*", "stream*.jsonl")))
    if streams:
        cmd = [py, os.path.join(HERE, "journalcheck.py"), "--tools", tools,
               "--work", a.work, "--json"]
        for s in streams:
            cmd += ["--stream", s]
        for path, sha in _seed_priors(a.run_dir):
            cmd += ["--prior-stream", path, "--prior-sha256", sha]
        jrc, jso, jse = run(cmd, timeout=300)
        rec = dict(_write_full(a.run_dir, a.phase, "journalcheck", jso, jse), exit=jrc)
        try:
            detail = json.loads(jso)
            rec["result"] = detail
            if isinstance(detail, dict) and isinstance(detail.get("violations"), list):
                out["blocking"]["journalcheck"] = len(detail["violations"])
            elif jrc != 0:
                out["blocking"]["journalcheck"] = 1
        except ValueError:
            rec["result"] = {"stdout_tail": jso[-TAIL:], "stderr_tail": jse[-TAIL:]}
        out["stages"]["journalcheck"] = rec
    else:
        rec = dict(_write_full(a.run_dir, a.phase, "journalcheck", "", ""), exit=1)
        rec["result"] = {"violations": [{"code": "no_stream",
                                         "detail": "no out/*/stream*.jsonl under the run dir"}]}
        out["stages"]["journalcheck"] = rec
        out["blocking"]["journalcheck"] = 1

    deterministic_ok = all(v["exit"] == 0 for v in out["stages"].values())
    if deterministic_ok or a.judge_always:
        jout = os.path.join(a.run_dir, "control", "judge", "%s.json" % a.phase)
        jrc, jso, jse = run([py, os.path.join(tools, "judge.py"),
                             "--report", os.path.join(a.work, "report.md"),
                             "--corpus", a.corpus, "--out", jout,
                             "--timeout", str(a.judge_timeout),
                             "--policy", a.judge_policy],
                            timeout=a.judge_timeout + 120)
        out["stages"]["judge"] = dict(_write_full(a.run_dir, a.phase, "judge", jso, jse),
                                      exit=jrc, result_file=jout)
    else:
        out["stages"]["judge"] = {"exit": None,
                                  "skipped": "deterministic gates failed"}

    out["pass"] = all(v.get("exit") == 0 for v in out["stages"].values())
    summary = "hostgate phase=%s pass=%s stages=%s" % (
        a.phase, out["pass"], ",".join("%s:%s" % (k, v.get("exit")) for k, v in out["stages"].items()))
    print(summary)
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
