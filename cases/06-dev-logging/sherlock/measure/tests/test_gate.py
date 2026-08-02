#!/usr/bin/env python3
"""Tests for gate.sh — the four-tier promotion rule (shell orchestration).

gate.sh resolves run-case.sh and report-case.py via "$HERE/..." (its OWN script
directory), never PATH, so a stub on PATH cannot intercept them. These tests copy
the real gate.sh into a scratch dir alongside stub run-case.sh / report-case.py —
the same technique test_run_case.py uses for a stub qwen, one layer up the chain.
No network, no SHERLOCK_API_KEY / JUDGE_API_KEY needed: the stubs never call out.

The ZeroCasesNegativeControl class is the negative control for the bug this suite
exists to catch: with no nullglob + zero-count guard, an unmatched "$CASES"/D*
leaves `c` as the literal glob string, `[ -d "$c" ]` is false, the loop body never
runs, `rc` stays its initial 0, and tier 2 — the MANDATORY accept/reject gate —
reports PASS on zero real cases run.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
GATE_SRC = os.path.join(MEASURE, "gate.sh")

# Mirrors run-case.sh's real contract: success prints ONE line ending
# " -> <run_dir>" and creates that dir; failure prints an error line (no arrow)
# to stdout+stderr and exits non-zero, creating NO run dir. Which case ids fail
# is controlled by $FAIL_CASES (space-separated), so one test can make exactly
# one case fail while the rest still run.
RUN_CASE_STUB = r"""#!/usr/bin/env bash
set -uo pipefail
case_dir="$1"; arm="$2"
case_id="$(basename "$case_dir")"
echo "$case_id" >> "$STUB_LOG_DIR/invoked.log"
for f in ${FAIL_CASES:-}; do
  if [ "$f" = "$case_id" ]; then
    echo "  FAIL stub-forced: $case_id" >&2
    echo "  FAIL stub-forced: $case_id"
    exit 1
  fi
done
rd="$STUB_LOG_DIR/$case_id-run"
mkdir -p "$rd"
echo "  OK $case_id/$arm  1s  chars=2100  -> $rd"
"""

# Mirrors report-case.py's contract closely enough to test gate.sh's orchestration:
# takes the same 4 flags, appends one row to --results, prints a summary line.
REPORT_CASE_STUB = r"""#!/usr/bin/env python3
import argparse, json, os
ap = argparse.ArgumentParser()
ap.add_argument("--case", required=True)
ap.add_argument("--run", required=True)
ap.add_argument("--tier", required=True)
ap.add_argument("--results", required=True)
a = ap.parse_args()
case_id = os.path.basename(a.case.rstrip("/"))
with open(a.results, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"case_id": case_id, "run_dir": a.run, "tier": a.tier}) + "\n")
print("  %s stub-reported -> %s" % (case_id, a.run))
"""


def _chmod_x(p):
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class GateHarness(unittest.TestCase):
    """Common scaffold: a scratch gate dir with the REAL gate.sh + stubs, and a
    scratch cases dir wired in via SHERLOCK_CASES (the same env-indirection
    gate.sh already supports)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gate-test-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

        gate_dir = os.path.join(self.d, "gatedir")
        os.makedirs(gate_dir)
        shutil.copy(GATE_SRC, os.path.join(gate_dir, "gate.sh"))
        _chmod_x(os.path.join(gate_dir, "gate.sh"))
        with open(os.path.join(gate_dir, "run-case.sh"), "w", encoding="utf-8") as fh:
            fh.write(RUN_CASE_STUB)
        _chmod_x(os.path.join(gate_dir, "run-case.sh"))
        with open(os.path.join(gate_dir, "report-case.py"), "w", encoding="utf-8") as fh:
            fh.write(REPORT_CASE_STUB)
        # The spend guard shells out to burned-since.py. Without it the command
        # fails, `burned` is empty, `${burned:-0}` reads 0 and the guard is
        # ALWAYS OPEN — the exact failure its own docstring warns about. Ship the
        # real file so these tests exercise the real guard.
        shutil.copy(os.path.join(MEASURE, "burned-since.py"),
                    os.path.join(gate_dir, "burned-since.py"))
        self.gate_dir = gate_dir
        self.gate = os.path.join(gate_dir, "gate.sh")

        self.cases = os.path.join(self.d, "cases")
        self.results = os.path.join(self.d, "results.jsonl")
        self.stub_log_dir = os.path.join(self.d, "stublog")
        os.makedirs(self.stub_log_dir)

    def make_cases(self, *ids):
        os.makedirs(self.cases, exist_ok=True)
        for cid in ids:
            os.makedirs(os.path.join(self.cases, cid))

    def run_gate(self, *args, fail_cases=""):
        env = dict(os.environ)
        env["SHERLOCK_CASES"] = self.cases
        env["SHERLOCK_RESULTS"] = self.results
        env["STUB_LOG_DIR"] = self.stub_log_dir
        env["FAIL_CASES"] = fail_cases
        env.update(self._extra_env)
        return subprocess.run([self.gate, *args], env=env,
                               capture_output=True, text=True, timeout=30)

    _extra_env = {}

    def seed_burn(self, arm, input_tokens, run_dir="29990101T000000Z-D01-x"):
        """A run that spent tokens and recorded nothing — what burned.jsonl is for."""
        with open(os.path.join(self.gate_dir, "burned.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({"arm": arm, "input_tokens": input_tokens,
                                 "reason": "test", "run_dir": run_dir}) + "\n")

    def invoked(self):
        p = os.path.join(self.stub_log_dir, "invoked.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return fh.read().split()

    def results_rows(self):
        if not os.path.exists(self.results):
            return []
        with open(self.results, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]


class TheSpendGuardActuallyStops(GateHarness):
    """gate.sh ran unguarded on 2026-08-01 and 5,896,031 tokens died invisibly.

    A guard is only a guard if something proves it closes. It caps the BURN, not
    the retry count, because a provider burst fails every attempt for minutes —
    counting retries would let a bad lane bill for as long as it stays bad.
    """

    def test_it_refuses_to_launch_once_the_cap_is_passed(self):
        self.make_cases("D01", "D02")
        self.seed_burn("v6", 9_000_000)
        self._extra_env = {"BURN_CAP_TOKENS": "1000000"}
        p = self.run_gate("2", "v6")
        self.assertEqual(self.invoked(), [], "a capped gate still spent money")
        self.assertIn("GUARD", p.stderr)

    def test_burn_under_the_cap_runs_normally(self):
        self.make_cases("D01")
        self.seed_burn("v6", 10_000)
        self._extra_env = {"BURN_CAP_TOKENS": "1000000"}
        self.run_gate("2", "v6")
        self.assertEqual(self.invoked(), ["D01"])

    def test_another_arms_burn_does_not_stop_this_one(self):
        self.make_cases("D01")
        self.seed_burn("v12", 9_000_000)
        self._extra_env = {"BURN_CAP_TOKENS": "1000000"}
        self.run_gate("2", "v6")
        self.assertEqual(self.invoked(), ["D01"])

    def test_a_missing_helper_closes_the_gate_instead_of_opening_it(self):
        """Fail CLOSED. An empty reading must never be read as zero burn."""
        os.remove(os.path.join(self.gate_dir, "burned-since.py"))
        self.make_cases("D01")
        self._extra_env = {"BURN_CAP_TOKENS": "1000000"}
        p = self.run_gate("2", "v6")
        self.assertEqual(self.invoked(), [],
                         "the guard could not measure burn and spent anyway")
        self.assertIn("GUARD", p.stderr)


class Tier0AllPass(GateHarness):
    """Tier 0's loop and glob ("$CASES"/cap-*) mirror tier 2's exactly, just for
    the micro-corpus id prefix — confirm the happy path dispatches the same way."""

    def test_exit_zero_and_every_case_reported_when_all_pass(self):
        self.make_cases("cap-multiline-stitching", "cap-gz-decompression")
        r = self.run_gate("0", "v6")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(self.invoked()),
                          ["cap-gz-decompression", "cap-multiline-stitching"])
        self.assertEqual({row["case_id"] for row in self.results_rows()},
                          {"cap-gz-decompression", "cap-multiline-stitching"})


class Tier0ZeroCasesNegativeControl(GateHarness):
    """THE negative control for tier 0, mirroring Tier2ZeroCasesNegativeControl:
    the same nullglob + explicit count-check pattern must guard tier 0 too, or
    a stale SHERLOCK_CASES / running gate.sh before micro.py has populated
    cases/ would silently report PASS on zero capability corpora run."""

    def test_zero_matching_cases_is_a_hard_failure_not_a_silent_pass(self):
        os.makedirs(self.cases, exist_ok=True)  # exists, but has no cap-* subdirs
        r = self.run_gate("0", "v6")
        self.assertNotEqual(r.returncode, 0,
                             "tier 0 reported success with ZERO cases run — "
                             "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn("0 cases", r.stderr)
        self.assertEqual(self.invoked(), [], "no case should have been invoked")
        self.assertEqual(self.results_rows(), [], "no row should have been written")


class Tier2AllPass(GateHarness):
    def test_exit_zero_and_every_case_reported_when_all_pass(self):
        self.make_cases("D01", "D02")
        r = self.run_gate("2", "v6")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(self.invoked()), ["D01", "D02"])
        self.assertEqual({row["case_id"] for row in self.results_rows()}, {"D01", "D02"})


class Tier2OneFailsRestStillRun(GateHarness):
    def test_continues_past_a_failure_and_exits_nonzero_overall(self):
        self.make_cases("D01", "D02", "D03")
        r = self.run_gate("2", "v6", fail_cases="D02")
        # Overall gate must reflect the failure...
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        # ...but D02 failing must NOT stop D01/D03 from running (the whole point
        # of tier 2 is to see every slice, not stop at the first miss).
        self.assertEqual(sorted(self.invoked()), ["D01", "D02", "D03"])
        # report-case.py is only reached for cases run-case.sh actually succeeded on.
        self.assertEqual({row["case_id"] for row in self.results_rows()}, {"D01", "D03"})


class Tier2ZeroCasesNegativeControl(GateHarness):
    """THE negative control for Important-1: tier 2 must hard-fail, distinctly
    from a real all-pass, when SHERLOCK_CASES matches nothing — e.g. a stale env
    var, or running tier 2 before slice.py has populated cases/."""

    def test_zero_matching_cases_is_a_hard_failure_not_a_silent_pass(self):
        os.makedirs(self.cases, exist_ok=True)  # exists, but has no D* subdirs
        r = self.run_gate("2", "v6")
        self.assertNotEqual(r.returncode, 0,
                             "tier 2 reported success with ZERO cases run — "
                             "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn("0 cases", r.stderr)
        self.assertEqual(self.invoked(), [], "no case should have been invoked")
        self.assertEqual(self.results_rows(), [], "no row should have been written")


class Tier3MeasuresNothing(GateHarness):
    """Important-6: tier 3 is a hand-driven run of run-bench.sh, not something this
    script performs — so it must exit NON-ZERO. Exiting 0 having measured nothing is
    the same silent-PASS class already fixed for tiers 0/1/2, and tier 3 is the ONLY
    tier whose number may be quoted as a benchmark result."""

    def test_tier_three_exits_nonzero_and_says_nothing_was_measured(self):
        os.makedirs(self.cases, exist_ok=True)
        env_corpus = os.path.join(self.d, "corpus")
        os.makedirs(env_corpus, exist_ok=True)
        env = dict(os.environ)
        env.update({"SHERLOCK_CASES": self.cases, "SHERLOCK_RESULTS": self.results,
                    "STUB_LOG_DIR": self.stub_log_dir, "FAIL_CASES": "",
                    "SHERLOCK_CORPUS": env_corpus})
        r = subprocess.run([self.gate, "3", "v6"], env=env, capture_output=True,
                           text=True, timeout=30)
        self.assertNotEqual(r.returncode, 0,
                            "tier 3 reported success having measured nothing")
        self.assertIn("NOTHING was measured", r.stderr)
        self.assertEqual(self.invoked(), [])
        self.assertEqual(self.results_rows(), [])


class Tier1MissingCaseDir(GateHarness):
    def test_named_case_not_found_is_a_hard_failure(self):
        os.makedirs(self.cases, exist_ok=True)
        r = self.run_gate("1", "v6", "D99")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("D99", r.stderr)
        self.assertEqual(self.invoked(), [])


class TheGateHasASpendGuard(unittest.TestCase):
    """gate.sh had none. one-defect.sh did, but it refuses an already-recorded
    cell, so every D04 rep on 2026-08-01 ran unguarded and 5,896,031 tokens died
    without appearing in results.jsonl. Cap the BURN, not the retry count."""

    def test_burned_since_counts_only_this_arm_after_this_stamp(self):
        import subprocess as sp
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            rows = [
                {"arm": "vX", "input_tokens": 100, "run_dir": "/r/20260801T100000Z-a-vX"},
                {"arm": "vX", "input_tokens": 700, "run_dir": "/r/20260801T230000Z-a-vX"},
                {"arm": "vY", "input_tokens": 900, "run_dir": "/r/20260801T230000Z-a-vY"},
            ]
            script = os.path.join(d, "burned-since.py")
            with open(os.path.join(here, "burned-since.py"), encoding="utf-8") as fh:
                src = fh.read()
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(src)
            with open(os.path.join(d, "burned.jsonl"), "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            out = sp.run([sys.executable, script, "vX", "20260801T200000Z"],
                         capture_output=True, text=True)
            # only vX, only after the stamp: the 700 row
            self.assertEqual(out.stdout.strip(), "700")

    def test_a_missing_burn_ledger_reads_as_zero_not_as_an_error(self):
        """An empty guard must fail OPEN loudly, never crash the gate."""
        import subprocess as sp
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "burned-since.py")
            with open(os.path.join(here, "burned-since.py"), encoding="utf-8") as fh:
                src = fh.read()
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(src)
            out = sp.run([sys.executable, script, "vX", "20260801T000000Z"],
                         capture_output=True, text=True)
            self.assertEqual(out.returncode, 0)
            self.assertEqual(out.stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
