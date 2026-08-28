#!/usr/bin/env python3
"""A sealed trace must be replayable with the working tree deleted (fix 10).

WHERE THIS LIVES AND WHY. The code under test is `eval/bench/run-bench.sh` and
its new helper `eval/bench/seal-trace.py` — the run-sealing path. Its other
end-to-end runner tests (`measure/tests/test_run_bench.py`) live here, and this
file is in their idiom, so it sits next to them rather than in `tools/tests`,
which tests the skill's own gates.

THE FOUR DEFECTS, all found in the trace of the paid run `20260827T173511Z-v41`,
whose own `replay.sh` promises "Re-validate this run with NO reconstruction and
no access to the original corpus":

  1. `gate-tools/` sealed the grader's SCRIPTS and not its DATA. There was no
     `reference/` in the trace at all, and `citecheck` reads its enum EXTENSION
     table as `<tools>/../reference/enum-tables.tsv`, so a replay graded with
     whatever table the repo happened to hold. Fix 5 has since added 28
     Status/SubStatus rows to that file, which widens the divergence.
  2. `status.json` said `phase: FINISHED_UNCHECKED` while `gates.json` said
     `verdict: clean`. The gates ran and passed; nothing wrote the phase.
  3. `upstream-inflight.json` and its `.lock` were left in the sealed trace, and
     `status.json` still pointed `inflight_path` at them.
  4. `.sherlock/active.json` said `"active": true` and pointed `skill_root` at
     the LIVE `skills/v41` checkout and `corpus` outside the trace. A replay
     resolving tools that way re-validates today's code, not the run.

The load-bearing test is `TestReplayReproducesTheRecordedVerdict`: it seals a
synthetic run inside a throwaway mini-checkout, DELETES that checkout's skill
tree and renames the source corpus away, then runs the trace's own `replay.sh`
and requires the replayed gate exits to equal the recorded ones. Nothing checked
this before, which is why four defects could accumulate behind a promise.
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
RUNNER = SHERLOCK / "eval" / "bench" / "run-bench.sh"
SEALER = SHERLOCK / "eval" / "bench" / "seal-trace.py"
ARM = "v42"

# Two names need renaming, so the staged tree is a genuinely different shape
# from the source — the reason the staged copy has to be sealed at all.
FIXTURE_FILES = {
    "Microsoft-Windows-PowerShell%4Operational.jsonl": '{"event":"one"}\n',
    "System.jsonl": '{"event":"three"}\n',
}

# The active marker the model's own tooling writes, in the exact shape the v41
# trace carried: live skill root, corpus outside the trace, active TRUE.
FAKE_QWEN = """#!/usr/bin/env python3
import json, os, pathlib
root = pathlib.Path.cwd()
(root / 'work').mkdir(exist_ok=True)
(root / 'work/report.md').write_text('# report\\n\\nrendered/System.jsonl:1\\n')
(root / 'work/worklist.tsv').write_text('# id\\tverdict\\nA001\\tD thing\\n')
(root / 'work/rules.tsv').write_text('# rule\\n')
(root / '.sherlock').mkdir(exist_ok=True)
(root / '.sherlock/active.json').write_text(json.dumps({
    "active": True, "corpus": os.environ["FIXTURE_CORPUS"], "mode": "single",
    "out": str(root / 'work'), "skill_root": os.environ["FIXTURE_SKILL_ROOT"],
    "version": 36, "worklists": ["worklist.tsv"], "workspace": str(root)}))
print(json.dumps([{'type':'result','session_id':
    '44444444-4444-4444-4444-444444444444','is_error':False,
    'num_turns':2,'result':'done'}]))
"""


def sealer_module():
    spec = importlib.util.spec_from_file_location("sherlock_seal_trace", SEALER)
    module = importlib.util.module_from_spec(spec)
    old = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


SEAL = sealer_module()


def seal(*argv):
    return subprocess.run([sys.executable, str(SEALER)] + list(argv),
                          text=True, capture_output=True, timeout=120)


class SealedRun:
    """One real run of run-bench.sh inside a throwaway mini-checkout.

    `repo/eval` and `repo/measure` are symlinks to the real ones; `repo/skills`
    holds a COPY of the arm. So the skill tree the run resolved can be deleted
    afterwards without touching the repository — which is the only honest way to
    prove a replay does not reach back into it.
    """

    def __init__(self, root: Path):
        self.root = root
        self.corpus = root / "corpus"
        self.runs = root / "runs"
        self.repo = root / "repo"
        self.skills = self.repo / "skills"

    def build(self):
        self.corpus.mkdir(parents=True)
        for name, body in FIXTURE_FILES.items():
            (self.corpus / name).write_text(body, encoding="utf-8")
        prompt = self.root / "prompt.txt"
        prompt.write_text("Investigate {CORPUS} thoroughly.", encoding="utf-8")
        self.repo.mkdir()
        (self.repo / "eval").symlink_to(SHERLOCK / "eval")
        (self.repo / "measure").symlink_to(SHERLOCK / "measure")
        self.skills.mkdir()
        shutil.copytree(SHERLOCK / "skills" / ARM, self.skills / ARM)
        fake = self.root / "fake-qwen"
        fake.write_text(FAKE_QWEN, encoding="utf-8")
        fake.chmod(0o755)
        env = dict(os.environ)
        env.update({
            "SHERLOCK_API_KEY": "dummy",
            "SHERLOCK_CORPUS": str(self.corpus),
            "SHERLOCK_PROMPT_FILE": str(prompt),
            "SHERLOCK_DATASET": "fixture",
            "SHERLOCK_UPSTREAM_LOG": "0",
            "SHERLOCK_RESUME_MAX_ATTEMPTS": "0",
            "QWEN_BIN": str(fake),
            "BENCH_RUNS": str(self.runs),
            "FIXTURE_CORPUS": str(self.corpus),
            "FIXTURE_SKILL_ROOT": str(self.skills / ARM),
        })
        self.run = subprocess.run(
            ["bash", str(self.repo / "eval" / "bench" / "run-bench.sh"), ARM],
            env=env, text=True, capture_output=True, timeout=600)
        traces = [p for p in self.runs.iterdir() if p.is_dir()]
        assert len(traces) == 1, traces
        self.trace = traces[0]
        return self


class TraceCase(unittest.TestCase):
    """One sealed run shared by the assertions that only read it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="seal-run-")
        cls.sealed = SealedRun(Path(cls._tmp)).build()
        cls.trace = cls.sealed.trace

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def gates(self):
        return json.loads((self.trace / "gates.json").read_text(encoding="utf-8"))

    def status(self):
        return json.loads((self.trace / "status.json").read_text(encoding="utf-8"))

    def events(self):
        return [json.loads(line) for line
                in (self.trace / "status-events.jsonl").read_text().splitlines()
                if line.strip()]


# ---------------------------------------------------------------------------
# 1. THE WHOLE GRADER
# ---------------------------------------------------------------------------
class TestGraderDataIsSealed(TraceCase):
    def test_v41_defect_the_enum_table_was_missing_from_the_trace(self):
        """`find . -name enum-tables.tsv` in the v41 trace returned nothing."""
        table = self.trace / "reference" / "enum-tables.tsv"
        self.assertTrue(table.is_file(),
                        "citecheck reads <tools>/../reference/enum-tables.tsv; "
                        "without it a replay grades with a different table")
        source = SHERLOCK / "skills" / ARM / "reference" / "enum-tables.tsv"
        self.assertEqual(table.read_bytes(), source.read_bytes())

    def test_every_reference_file_a_gate_reads_travelled(self):
        required = SEAL.derive_required(str(self.trace / "gate-tools"))
        self.assertTrue(required, "no reference reads derived — the audit would "
                                  "be vacuous")
        for name in sorted(required):
            self.assertTrue((self.trace / "reference" / name).is_file(), name)

    def test_the_profiles_fixes_2_to_5_added_are_among_them(self):
        required = SEAL.derive_required(str(self.trace / "gate-tools"))
        for name in ("enum-tables.tsv", "report-contract.corporate.json",
                     "population-scope.json", "logon-failure-reason.json"):
            self.assertIn(name, required)

    def test_the_audit_is_clean_on_a_sealed_trace(self):
        self.assertEqual(SEAL.audit(str(self.trace)), [])


class TestRequiredListIsDerivedNotHardcoded(unittest.TestCase):
    """A hardcoded list is how this defect comes back: fixes 2-5 added three
    profiles and nobody remembered the sealer. The list is read out of the
    sealed gate SOURCE, so a profile a LATER fix adds travels on its own."""

    def test_a_new_profile_read_the_normal_way_is_required_automatically(self):
        with tempfile.TemporaryDirectory() as raw:
            tools = Path(raw) / "tools"
            tools.mkdir()
            (tools / "futurecheck.py").write_text(
                "import os\n"
                "TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))\n"
                "FUTURE_FILE = 'fix-99-profile.json'\n"
                "def path():\n"
                "    reference_dir = os.path.join(TOOLS_DIR, '..', 'reference')\n"
                "    return os.path.join(reference_dir, FUTURE_FILE)\n",
                encoding="utf-8")
            required = SEAL.derive_required(str(tools))
            self.assertIn("fix-99-profile.json", required)
            self.assertEqual(required["fix-99-profile.json"], {"futurecheck.py"})

    def test_a_direct_join_on_the_literal_reference_dir_is_required_too(self):
        with tempfile.TemporaryDirectory() as raw:
            tools = Path(raw) / "tools"
            tools.mkdir()
            (tools / "reportcheck.py").write_text(
                "import os\n"
                "BASE = os.path.dirname(__file__)\n"
                "C = os.path.join(BASE, '..', 'reference', 'report-contract.corporate.json')\n",
                encoding="utf-8")
            self.assertIn("report-contract.corporate.json",
                          SEAL.derive_required(str(tools)))

    def test_a_marker_filename_that_is_not_reference_data_is_not_required(self):
        """stopcheck has MARKER_FILE = 'active.json'; it is not grader data.
        A name-shaped heuristic would demand it and fail every seal."""
        with tempfile.TemporaryDirectory() as raw:
            tools = Path(raw) / "tools"
            tools.mkdir()
            (tools / "stopcheck.py").write_text(
                "import os\n"
                "MARKER_DIR = '.sherlock'\n"
                "MARKER_FILE = 'active.json'\n"
                "def p(ws):\n"
                "    return os.path.join(ws, MARKER_DIR, MARKER_FILE)\n",
                encoding="utf-8")
            self.assertEqual(SEAL.derive_required(str(tools)), {})


class TestSealFailsClosed(unittest.TestCase):
    def _trace_with_gate(self, root, source_text, reference=None):
        trace = root / "trace"
        tools = trace / "gate-tools"
        tools.mkdir(parents=True)
        (tools / "citecheck.py").write_text(source_text, encoding="utf-8")
        if reference is not None:
            (trace / "reference").mkdir()
            for name, body in reference.items():
                (trace / "reference" / name).write_text(body, encoding="utf-8")
        return trace

    GATE = ("import os\n"
            "TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))\n"
            "ENUM_TABLE_FILE = 'enum-tables.tsv'\n"
            "def t():\n"
            "    reference_dir = os.path.join(TOOLS_DIR, '..', 'reference')\n"
            "    return os.path.join(reference_dir, ENUM_TABLE_FILE)\n")

    def test_audit_fails_when_a_gate_reads_a_file_the_sealer_did_not_copy(self):
        with tempfile.TemporaryDirectory() as raw:
            trace = self._trace_with_gate(Path(raw), self.GATE, reference={})
            problems = SEAL.audit(str(trace))
            self.assertTrue(any("enum-tables.tsv" in p for p in problems), problems)
            done = seal("audit", "--trace", str(trace))
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("did not copy", done.stderr)

    def test_audit_fails_when_no_reference_directory_was_sealed_at_all(self):
        """The v41 defect exactly: gate-tools present, reference absent."""
        with tempfile.TemporaryDirectory() as raw:
            trace = self._trace_with_gate(Path(raw), self.GATE)
            problems = SEAL.audit(str(trace))
            self.assertTrue(any("reference/ was not sealed" in p for p in problems),
                            problems)

    def test_grader_fails_closed_when_the_reference_data_is_missing(self):
        with tempfile.TemporaryDirectory() as raw:
            arm = Path(raw) / "skill" / "tools"
            arm.mkdir(parents=True)
            (arm / "citecheck.py").write_text(self.GATE, encoding="utf-8")
            trace = Path(raw) / "trace"
            trace.mkdir()
            done = seal("grader", "--trace", str(trace), "--arm-tools", str(arm))
            self.assertEqual(done.returncode, 1, done.stdout)
            self.assertIn("reference directory not found", done.stderr)
            self.assertFalse((trace / "reference").exists())

    def test_grader_fails_closed_when_a_reference_file_cannot_be_copied(self):
        with tempfile.TemporaryDirectory() as raw:
            skill = Path(raw) / "skill"
            (skill / "tools").mkdir(parents=True)
            (skill / "tools" / "citecheck.py").write_text(self.GATE, encoding="utf-8")
            (skill / "reference").mkdir()
            table = skill / "reference" / "enum-tables.tsv"
            table.write_text("a\tb\n", encoding="utf-8")
            table.chmod(0)
            trace = Path(raw) / "trace"
            trace.mkdir()
            try:
                done = seal("grader", "--trace", str(trace),
                            "--arm-tools", str(skill / "tools"))
                if os.geteuid() == 0:
                    self.skipTest("root can read a 0000 file")
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("could not copy", done.stderr)
            finally:
                table.chmod(0o644)

    def test_grader_fails_closed_when_the_reference_directory_is_empty(self):
        with tempfile.TemporaryDirectory() as raw:
            skill = Path(raw) / "skill"
            (skill / "tools").mkdir(parents=True)
            (skill / "tools" / "citecheck.py").write_text(self.GATE, encoding="utf-8")
            (skill / "reference").mkdir()
            trace = Path(raw) / "trace"
            trace.mkdir()
            done = seal("grader", "--trace", str(trace),
                        "--arm-tools", str(skill / "tools"))
            self.assertEqual(done.returncode, 1, done.stdout)
            self.assertIn("empty", done.stderr)

    def test_the_runner_refuses_a_run_whose_trace_could_not_be_sealed(self):
        """seal-failure.json must reach the exit-code ladder as its own RC."""
        source = RUNNER.read_text(encoding="utf-8")
        start = source.index('GATE_VERDICT=""')
        end = source.index("fi", source.index("  RC=0")) + 2
        block = source[start:end]
        self.assertIn("seal-failure.json", block,
                      "an unsealable trace must fail the run, not warn")
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw)
            (trace / "candidate.json").write_text("{}")
            (trace / "work").mkdir()
            (trace / "work" / "report.md").write_text("a report")
            (trace / "gates.json").write_text(json.dumps({"verdict": "clean",
                                                          "gates": {}}))
            (trace / "seal-failure.json").write_text('{"schema": 1}')
            script = 'TRACE=%s\nQWEN_RC=0\n%s\necho "RC=$RC"\n' % (trace, block)
            done = subprocess.run(["bash", "-c", script], text=True,
                                  capture_output=True)
            self.assertIn("RC=6", done.stdout, done.stdout + done.stderr)


# ---------------------------------------------------------------------------
# 2. A TERMINAL PHASE
# ---------------------------------------------------------------------------
class TestTerminalPhase(TraceCase):
    def test_v41_defect_the_phase_never_reached_a_terminal_state(self):
        """v41: phase FINISHED_UNCHECKED, gates.json verdict clean."""
        status = self.status()
        self.assertIn(status["phase"], ("ACCEPTED", "REJECTED"), status)
        self.assertNotEqual(status["phase"], "FINISHED_UNCHECKED",
                            "the gates ran; the phase must say what they found")

    def test_the_phase_agrees_with_the_recorded_verdict(self):
        expected = "ACCEPTED" if self.gates()["verdict"] == "clean" else "REJECTED"
        self.assertEqual(self.status()["phase"], expected)

    def test_the_terminal_phase_is_also_the_last_event(self):
        events = [row["event"] for row in self.events()]
        self.assertEqual(events[-1], self.status()["phase"], events)
        self.assertNotIn("FINISHED_UNCHECKED", events,
                         "no gates.json is the ONLY thing FINISHED_UNCHECKED "
                         "may mean")

    def test_the_phase_vocabulary_is_the_declared_one(self):
        declared = (SHERLOCK / "eval" / "bench" / "bench-status.py").read_text(
            encoding="utf-8")
        phases = declared[declared.index("PHASES = {"):declared.index("TERMINAL = {")]
        for name in ("ACCEPTED", "REJECTED", "FINISHED_UNCHECKED"):
            self.assertIn('"%s"' % name, phases)

    def test_finished_unchecked_is_reachable_only_without_gates(self):
        source = RUNNER.read_text(encoding="utf-8")
        block = source[source.index("TERMINAL_PHASE=") - 400:]
        block = block[:block.index("TERMINAL_WRITTEN=1")]
        self.assertIn('if [ -f "$TRACE/gates.json" ]; then', block)
        self.assertIn("TERMINAL_PHASE=FINISHED_UNCHECKED", block)
        # and the assignment is in the branch where gates.json is ABSENT
        gated = block[block.index('if [ -f "$TRACE/gates.json" ]'):]
        self.assertLess(gated.index("TERMINAL_PHASE=ACCEPTED"),
                        gated.index("TERMINAL_PHASE=FINISHED_UNCHECKED"))


# ---------------------------------------------------------------------------
# 3. LIVE-RUN ARTEFACTS
# ---------------------------------------------------------------------------
class TestInflightArtefactsAreRemoved(TraceCase):
    def test_v41_defect_a_stale_inflight_file_stayed_in_the_sealed_trace(self):
        for name in ("upstream-inflight.json", "upstream-inflight.json.lock"):
            self.assertFalse((self.trace / name).exists(), name)

    def test_no_terminal_status_points_at_the_marker_any_more(self):
        self.assertIsNone(self.status()["inflight_path"],
                          "a path to a file we deleted is worse than no path")


class TestInflightRemovalKeepsCrashRecovery(unittest.TestCase):
    """A sealed run is told from a live one two ways. STRUCTURALLY: `inert` runs
    from save_trace, after the lane proxy is killed and reaped and after
    upstream-completed.jsonl is written, so the run is over by construction —
    a run that dies WITHOUT sealing never reaches this code and keeps its
    marker. MECHANICALLY, so the rule does not rest on the caller being right:
    the lock must be free, and no listed request's pid may still exist."""

    def test_a_finished_run_loses_its_marker(self):
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw)
            (trace / "upstream-inflight.json").write_text(
                json.dumps({"requests": {}}))
            (trace / "upstream-inflight.json.lock").write_text("")
            self.assertEqual(SEAL.drop_inflight(str(trace)), "removed")
            self.assertFalse((trace / "upstream-inflight.json").exists())
            self.assertFalse((trace / "upstream-inflight.json.lock").exists())

    def test_a_live_request_refuses_the_seal(self):
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw)
            (trace / "upstream-inflight.json").write_text(json.dumps(
                {"requests": {"r1": {"pid": os.getpid(), "started_at": "x"}}}))
            done = seal("inert", "--trace", str(trace))
            self.assertEqual(done.returncode, 1, done.stdout)
            self.assertIn("is alive", done.stderr)
            self.assertTrue((trace / "upstream-inflight.json").exists(),
                            "a running run's marker is crash-recovery evidence")

    def test_a_held_lock_refuses_the_seal(self):
        import fcntl
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw)
            (trace / "upstream-inflight.json").write_text(
                json.dumps({"requests": {}}))
            lock = trace / "upstream-inflight.json.lock"
            lock.write_text("")
            holder = os.open(str(lock), os.O_RDWR)
            try:
                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                done = seal("inert", "--trace", str(trace))
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("still live", done.stderr)
                self.assertTrue((trace / "upstream-inflight.json").exists())
            finally:
                os.close(holder)

    def test_a_dead_pid_does_not_block_the_seal(self):
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw)
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()
            (trace / "upstream-inflight.json").write_text(json.dumps(
                {"requests": {"r1": {"pid": dead.pid, "started_at": "x"}}}))
            self.assertEqual(SEAL.drop_inflight(str(trace)), "removed")


# ---------------------------------------------------------------------------
# 4. INERTNESS
# ---------------------------------------------------------------------------
class TestTraceIsInert(TraceCase):
    def marker(self):
        return json.loads((self.trace / ".sherlock" / "active.json")
                          .read_text(encoding="utf-8"))

    def test_the_marker_was_written_live_the_way_v41_wrote_it(self):
        """Guards the fixture: if the run stopped producing a marker at all,
        every assertion below would pass vacuously."""
        self.assertTrue((self.trace / ".sherlock" / "active.json").is_file())

    def test_v41_defect_active_true_on_a_finished_run(self):
        self.assertIs(self.marker()["active"], False)
        self.assertIs(self.marker().get("sealed"), True)

    def test_v41_defect_skill_root_pointed_at_the_live_checkout(self):
        row = self.marker()
        live = self.sealed.skills / ARM
        for key in ("skill_root", "corpus", "out", "workspace"):
            resolved = Path(os.path.realpath(row[key]))
            self.assertEqual(
                os.path.commonpath([resolved, Path(os.path.realpath(self.trace))]),
                os.path.realpath(self.trace),
                "%s escapes the trace: %s" % (key, row[key]))
            self.assertNotEqual(resolved, Path(os.path.realpath(live)))

    def test_the_sealed_skill_root_really_resolves_the_sealed_grader(self):
        """skill_root/../reference is how a gate finds its data; inside a trace
        the tools directory is `gate-tools` and the data is `reference`, so the
        trace itself is the skill root."""
        row = self.marker()
        self.assertTrue((Path(row["skill_root"]) / "reference"
                         / "enum-tables.tsv").is_file())
        self.assertTrue((Path(row["skill_root"]) / "gate-tools"
                         / "citecheck.py").is_file())
        self.assertTrue(Path(row["corpus"]).is_dir())

    def test_the_sealed_marker_is_inert_to_the_gate_that_reads_it(self):
        """stopcheck is the tool that acts on the marker. `active: false` must
        mean "no live session here" to IT, not just to a human reader."""
        spec = importlib.util.spec_from_file_location(
            "sherlock_sealed_stopcheck", self.trace / "gate-tools" / "stopcheck.py")
        stopcheck = importlib.util.module_from_spec(spec)
        old = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            spec.loader.exec_module(stopcheck)
        finally:
            sys.dont_write_bytecode = old
        data, _marker, _ = stopcheck.load_marker(str(self.trace))
        self.assertIsNone(data, "a sealed trace must not present a live session")

    def test_the_validator_rearms_its_own_authority_copy(self):
        """Inertness must not break validate-run.py, which rebuilds a LIVE
        authority workspace from the trace's marker and runs stopcheck against
        it. It overrides workspace/out/corpus/skill_root there; it now has to
        override `active` too."""
        source = (SHERLOCK / "eval" / "bench" / "validate-run.py").read_text(
            encoding="utf-8")
        self.assertIn('marker.update({"active": True', source)

    def test_replay_refuses_a_gate_tools_override_that_leaves_the_trace(self):
        env = dict(os.environ)
        env["SHERLOCK_GATE_TOOLS"] = str(SHERLOCK / "skills" / ARM / "tools")
        done = subprocess.run(["bash", str(self.trace / "replay.sh")], env=env,
                              text=True, capture_output=True, timeout=600)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("points outside this trace", done.stderr)

    def test_replay_refuses_a_trace_whose_grader_data_is_missing(self):
        """The pre-fix trace shape. Silently grading with the repo's table is
        the behaviour being removed, so absence must REFUSE."""
        with tempfile.TemporaryDirectory() as raw:
            copy = Path(raw) / "trace"
            shutil.copytree(self.trace, copy, symlinks=True)
            shutil.rmtree(copy / "reference")
            done = subprocess.run(["bash", str(copy / "replay.sh")], text=True,
                                  capture_output=True, timeout=600)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("no reference/ in this trace", done.stderr)


# ---------------------------------------------------------------------------
# 5. THE POINT: DOES A REPLAY REPRODUCE THE RECORDED VERDICT?
# ---------------------------------------------------------------------------
class TestReplayReproducesTheRecordedVerdict(unittest.TestCase):
    def test_replay_without_the_live_skill_tree_matches_gates_json(self):
        with tempfile.TemporaryDirectory() as raw:
            sealed = SealedRun(Path(raw)).build()
            trace = sealed.trace
            recorded = json.loads((trace / "gates.json").read_text(encoding="utf-8"))

            # TAKE AWAY EVERYTHING OUTSIDE THE TRACE. The skill tree this run
            # resolved its tools through is deleted; the source corpus is
            # renamed away and made unreadable. Any path back into the working
            # tree is now a hard failure rather than a silent success.
            shutil.rmtree(sealed.skills)
            gone = Path(raw) / "corpus-gone"
            sealed.corpus.rename(gone)
            gone.chmod(0)
            try:
                done = subprocess.run(["bash", str(trace / "replay.sh")],
                                      text=True, capture_output=True, timeout=900,
                                      cwd="/")
            finally:
                gone.chmod(0o755)

            self.assertNotIn("refusing", done.stderr)
            self.assertIn("replay reproduced the recorded gate exits", done.stdout,
                          done.stdout[-3000:] + done.stderr[-3000:])
            self.assertEqual(done.returncode, 0, done.stderr[-3000:])

            # And the exits really are the recorded ones, read independently of
            # replay.sh's own comparison.
            for name in ("citecheck", "triagecheck", "statecheck"):
                self.assertIn("%s rc=%s" % (name, recorded["gates"][name]["exit_code"]),
                              done.stdout)

    def test_a_diverging_replay_is_a_failure_not_a_success(self):
        """The comparison must be able to say no. Rewrite a recorded exit and
        the same replay has to fail."""
        with tempfile.TemporaryDirectory() as raw:
            sealed = SealedRun(Path(raw)).build()
            trace = sealed.trace
            gates = json.loads((trace / "gates.json").read_text(encoding="utf-8"))
            gates["gates"]["citecheck"]["exit_code"] = 99
            (trace / "gates.json").write_text(json.dumps(gates), encoding="utf-8")
            done = subprocess.run(["bash", str(trace / "replay.sh")], text=True,
                                  capture_output=True, timeout=900)
            self.assertEqual(done.returncode, 3, done.stdout[-2000:])
            self.assertIn("REPLAY DIVERGED", done.stdout)
            self.assertIn("citecheck recorded=99", done.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
