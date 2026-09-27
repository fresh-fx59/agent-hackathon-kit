#!/usr/bin/env python3
"""v63 — trusted stage recompute + repeating repair-phase loop. Offline,
no model, no network, no paid API calls: every process here is a local
fixture stub."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
SHERLOCK = BENCH.parents[1]
PACKAGE = SHERLOCK / "skills" / "v63"
CORPUS = SHERLOCK / "tools/tests/fixtures/v60-smalltest/corpus"
FAKE_WORKER = HERE / "fixtures" / "fake_repair_worker.py"
FAKE_GATE = HERE / "fixtures" / "fake_gate_always_fails.py"
sys.path.insert(0, str(BENCH))
import repair_loop  # noqa: E402


def make_spawn(work_dir, pid_log):
    def spawn(phase_id):
        r = subprocess.run([sys.executable, str(FAKE_WORKER), phase_id,
                            str(work_dir), str(pid_log)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return phase_id
    return spawn


def read_pid_log(pid_log):
    rows = []
    for line in Path(pid_log).read_text(encoding="utf-8").splitlines():
        phase, pid, ts = line.split()
        rows.append((phase, int(pid), float(ts)))
    return rows


class TrustedStageIgnoresForgedCheckpoint(unittest.TestCase):
    def test_forged_stage_done_does_not_short_circuit_draft(self):
        """report.md missing, checkpoint.json says stage=done (forged) ->
        trusted_stage must still say 'draft', never 'done'."""
        root = Path(tempfile.mkdtemp(prefix="trust-"))
        work = root / "work"
        work.mkdir()
        (work / "checkpoint.json").write_text('{"stage": "done"}', encoding="utf-8")
        stage = repair_loop.trusted_stage(work, CORPUS, PACKAGE, root)
        self.assertEqual(stage, "draft")

    def test_forged_stage_done_does_not_short_circuit_repair(self):
        """report.md exists (real content) but the trusted gate fails ->
        trusted_stage must say 'repair', even though checkpoint.json's own
        stage field claims 'done'."""
        root = Path(tempfile.mkdtemp(prefix="trust-"))
        work = root / "work"
        work.mkdir()
        (work / "report.md").write_text("## Находки\nsomething real\n", encoding="utf-8")
        (work / "checkpoint.json").write_text('{"stage": "done"}', encoding="utf-8")
        gate = [sys.executable, str(FAKE_GATE)]
        stage = repair_loop.trusted_stage(work, CORPUS, PACKAGE, root, gate_argv=gate)
        self.assertEqual(stage, "repair")

    def test_default_gate_argv_wires_to_hostgate(self):
        """Production default must be hostgate.py, the trusted phase gate
        run outside the sandbox -- not some ad hoc check."""
        argv = repair_loop.default_gate_argv("/w", "/c", PACKAGE, "/r")
        self.assertIn("hostgate.py", " ".join(argv))


class RepeatingRepairLoopE2E(unittest.TestCase):
    """Free (no paid API): a fake spawn_phase stands in for a real qwen-run
    process launch, a fake always-fail gate stands in for hostgate.py."""

    def test_seeded_at_draft_spawns_fresh_processes_and_caps_at_max_rounds(self):
        root = Path(tempfile.mkdtemp(prefix="loop-"))
        work = root / "work"
        pid_log = root / "pids.log"
        # seed at stage=draft: no report.md yet
        gate = [sys.executable, str(FAKE_GATE)]
        result = repair_loop.run_repair_loop(
            make_spawn(work, pid_log), work_dir=work, corpus_dir=CORPUS,
            package_dir=PACKAGE, run_dir=root, max_repair_rounds=2, gate_argv=gate)

        rows = read_pid_log(pid_log)
        phases = [r[0] for r in rows]
        # a phase boundary happened after draft: repair phases were launched
        self.assertEqual(phases, ["draft", "repair", "repair-2"])
        # every phase is a GENUINELY FRESH process, not a reused one
        pids = [r[1] for r in rows]
        self.assertEqual(len(pids), len(set(pids)), "pids must all differ: %r" % pids)
        # the loop stopped at the declared cap, not because the forged
        # checkpoint.json said "done" after the very first phase
        self.assertEqual(result["repair_rounds"], 2)
        self.assertEqual(result["final_stage"], "repair",
                         "the fake gate never passes -- must not have been "
                         "fooled into reporting 'done' by the forged checkpoint")
        self.assertEqual(len(result["pids"]), 3)

    def test_a_passing_gate_stops_the_loop_early(self):
        """Sanity check on the other side: once the trusted gate genuinely
        passes, the loop must stop instead of burning the full cap."""
        root = Path(tempfile.mkdtemp(prefix="loop-ok-"))
        work = root / "work"
        pid_log = root / "pids.log"
        work.mkdir()

        calls = {"n": 0}

        def spawn(phase_id):
            calls["n"] += 1
            r = subprocess.run([sys.executable, str(FAKE_WORKER), phase_id,
                                str(work), str(pid_log)], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr
            return phase_id

        # gate passes on the SECOND call only (i.e. after one repair round)
        gate_script = root / "gate_pass_second.py"
        gate_script.write_text(
            "import sys, pathlib\n"
            "f = pathlib.Path(%r)\n"
            "n = int(f.read_text()) if f.exists() else 0\n"
            "f.write_text(str(n + 1))\n"
            "sys.exit(0 if n >= 1 else 1)\n" % str(root / "gate_calls.txt"),
            encoding="utf-8")

        result = repair_loop.run_repair_loop(
            spawn, work_dir=work, corpus_dir=CORPUS, package_dir=PACKAGE, run_dir=root,
            max_repair_rounds=2, gate_argv=[sys.executable, str(gate_script)])
        self.assertEqual(result["final_stage"], "done")
        self.assertEqual(result["repair_rounds"], 1,
                         "must stop as soon as the trusted gate passes, not "
                         "burn the full round cap")


if __name__ == "__main__":
    unittest.main()
