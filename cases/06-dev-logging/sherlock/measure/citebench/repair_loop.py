#!/usr/bin/env python3
"""v63 — a repeating repair-phase driver for the citebench replay harness.

THE RULE THIS ENFORCES: a phase-boundary decision must NEVER trust the
model-written `checkpoint.json` "stage" field directly (v55 had forged
checkpoint rows). `trusted_stage()` recomputes the stage from harness-side
tools only:

  1. `triagecheck.py`   — is the worklist actually fully resolved (if there is one)
  2. `checkpoint.is_placeholder` on `report.md` — is there a real draft yet
  3. `hostgate.py`       — the v56 trusted phase gate, run OUTSIDE the sandbox
                           (finalize.py's deterministic gates + journalcheck,
                           plus judge.py only when those already pass)

None of these read the checkpoint's own `stage` key. A run whose model wrote
`{"stage": "done"}` into checkpoint.json without ever producing a real report
recomputes to `draft` or `repair` all the same.

`run_repair_loop()` then drives draft -> repair* -> done, capping the number
of repair ROUNDS (fresh processes) at `max_repair_rounds`, mirroring the v63
skill's in-session check-round cap at the process-boundary level.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMEOUT = 600


def _load_checkpoint_module(package_dir):
    """Import the package's own checkpoint.py by path (never trust a stale
    copy on sys.path from a different skill version)."""
    package_dir = Path(package_dir)
    key = "sherlock_checkpoint_%s" % package_dir.name
    spec = importlib.util.spec_from_file_location(key, package_dir / "tools" / "checkpoint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_gate_argv(work_dir, corpus_dir, package_dir, run_dir):
    """The production trusted gate: hostgate.py, run by the harness OUTSIDE
    the sandbox. Exposed as a function (not inlined) so a test can assert the
    production wiring without having to execute the real gate."""
    package_dir = Path(package_dir)
    return [sys.executable, str(package_dir / "tools" / "hostgate.py"),
            "--work", str(work_dir), "--corpus", str(corpus_dir),
            "--package", str(package_dir), "--run-dir", str(run_dir)]


def trusted_stage(work_dir, corpus_dir, package_dir, run_dir, gate_argv=None,
                  timeout=DEFAULT_TIMEOUT):
    """Recompute the true stage. NEVER reads checkpoint.json's "stage" key."""
    work_dir = Path(work_dir)
    worklist = work_dir / "worklist.tsv"
    if worklist.is_file():
        rc = subprocess.run(
            [sys.executable, str(Path(package_dir) / "tools" / "triagecheck.py"),
             "--worklist", str(worklist), "--corpus", str(corpus_dir)],
            capture_output=True, text=True, timeout=timeout).returncode
        if rc != 0:
            return "triage"

    report = work_dir / "report.md"
    if not report.is_file():
        return "draft"
    text = report.read_text(encoding="utf-8", errors="replace")
    ckpt = _load_checkpoint_module(package_dir)
    if not text.strip() or ckpt.is_placeholder(text):
        return "draft"

    argv = gate_argv or default_gate_argv(work_dir, corpus_dir, package_dir, run_dir)
    rc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout).returncode
    return "done" if rc == 0 else "repair"


def run_repair_loop(spawn_phase, *, work_dir, corpus_dir, package_dir, run_dir,
                    max_repair_rounds=2, gate_argv=None, timeout=DEFAULT_TIMEOUT):
    """Drive draft -> repair* -> done.

    `spawn_phase(phase_id)` MUST launch a genuinely fresh process for that
    phase (never reuse one) and return an opaque identity for it (e.g. its
    pid) once the phase has finished. The decision to advance past a phase is
    ALWAYS `trusted_stage()` — a forged/tampered checkpoint.json changes
    nothing here, by construction: this function never reads that file.
    """
    def stage_now():
        return trusted_stage(work_dir, corpus_dir, package_dir, run_dir,
                             gate_argv=gate_argv, timeout=timeout)

    pids = []
    stage = stage_now()
    if stage == "draft":
        pids.append(spawn_phase("draft"))
        stage = stage_now()

    rounds = 0
    while stage == "repair" and rounds < max_repair_rounds:
        rounds += 1
        pids.append(spawn_phase("repair" if rounds == 1 else "repair-%d" % rounds))
        stage = stage_now()

    return {"final_stage": stage, "repair_rounds": rounds, "pids": pids}
