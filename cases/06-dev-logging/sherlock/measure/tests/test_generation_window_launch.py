#!/usr/bin/env python3
"""The runner must REFUSE to launch a configuration the provider cannot deliver.

Three paid runs in a row were cut by CloseRouter's 90-second upstream
generation timeout while the harness asked for 32,768 output tokens — five
times more than the lane can produce inside its own window. The refusal has to
happen at startup, BEFORE any money is spent, and it has to name every number
it judged on plus the value that would have fitted, because a refusal that
does not say what to set instead just moves the operator's problem.

Two rules this file pins hard:
  * the value is DERIVED, never clamped. A user-supplied budget that does not
    fit is refused and explained, so the number in the launcher always matches
    the number on the wire;
  * a lane that declares NO generation window skips the check entirely.
    linkapi and the free lanes must behave exactly as they did before fix 9.

The rig (stub qwen, stub provider, throwaway corpus) is fix 8's, reused rather
than re-cut: it already knows how to launch run-bench.sh without spending money.

WHAT FIX 7 CHANGED HERE, AND WHY THIS FILE HAD TO MOVE WITH IT.
When this file was written, the generation window was the ONLY constraint on the
output budget, so "a budget that fits" meant "a budget that fits the clock", and
these cases launched a 90 s lane at max_tokens 6700 to prove it. That is exactly
the configuration run 20260827T173511Z-v41 shipped, and it clipped FOUR of its
own compaction and state-snapshot summaries at that number: qwen reserves
COMPACT_MAX_OUTPUT_TOKENS = 20,000 for a compaction summary, and a 90 s window
at 122.6 tok/s can only deliver 6,743. Fix 7 judges both constraints together
and REFUSES when they cannot both hold, so the four cases below that launched a
90 s lane no longer launch — correctly.

Their intent is unchanged and still worth pinning: a budget that fits must still
launch, an unset budget on a windowed lane must still be DERIVED and not fall
back to 32,768, and the window must still be recorded beside fix 8's budgets. So
each of them now declares the escape the refusal itself names — a
`model.sessionTokenLimit` low enough that the session ends before qwen can ever
fire auto-compaction, which makes the reserve a constraint on a code path that
cannot execute. Nothing here is weakened: the refusal is asserted directly in
test_the_conflict_still_refuses_a_budget_below_the_reserve, and every launching
case asserts that NEITHER refusal appeared.
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_declared_budgets import Rig                              # noqa: E402
from lane_guard import fitting_max_output_tokens                   # noqa: E402

# corporate-settings.py owns the auto-compaction threshold, and this file must
# not carry a second copy of it (see
# test_the_shell_defaults_are_the_measured_constants_not_a_second_copy for the
# same rule applied to the shell). The hyphen in the filename is why this is an
# importlib load and not an import statement.
_SPEC = importlib.util.spec_from_file_location(
    "corporate_settings",
    os.path.join(os.path.dirname(HERE), "corporate-settings.py"))
corporate_settings = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(corporate_settings)

CR_WINDOW_S = "90"
CR_TOKENS_PER_S = "122.6"
FITTING = fitting_max_output_tokens(90, 122.6, 35)                  # 6743
#: run-bench.sh's own default for SHERLOCK_CONTEXT_WINDOW. The threshold qwen
#: auto-compacts at is a function of the declared context window, so the escape
#: below has to be computed against the window the rig actually launches with.
DEFAULT_CONTEXT_WINDOW = 200000


def unreachable_compaction_limit(max_tokens, window=DEFAULT_CONTEXT_WINDOW):
    """The largest model.sessionTokenLimit that makes compaction UNREACHABLE.

    FIX 7'S OWN NAMED ESCAPE, computed rather than pasted. `sessionTokenLimit`
    refuses the next turn once the provider's reported prompt_tokens passes it,
    so the largest prompt still sendable is `limit + max_tokens`; if that cannot
    reach qwen's auto-compaction threshold, the session ends before a compaction
    summary is ever requested and the 20,000-token reserve constrains a code
    path that cannot execute. One token higher and fix 7 refuses again — which
    test_the_escape_is_a_boundary_not_a_switch asserts.
    """
    auto, _hard = corporate_settings.thresholds(window)
    return int(auto) - max_tokens
# The stub qwen writes this, so the rig's run "delivers" and does not spend 45 s
# on resume attempts. The exit code of a stubbed run is NOT the subject here
# (the lane audit correctly calls a stub run's absent ledger a breach); what
# matters is whether the launch check let the run START.
DELIVERS = {"QWEN_STUB_REPORT": "# stub\n\napps/api.log:1 ok\n"}
REFUSAL = "cannot be delivered inside this lane's generation window"
#: Fix 7's refusal, which is a DIFFERENT refusal and must be asserted against
#: separately: a launch blocked by the constraint conflict would otherwise read
#: here as a launch blocked by the window.
CONFLICT = "the generation window and the compaction reserve disagree"


class TheRunnerRefusesAnImpossibleOutputBudget(Rig, unittest.TestCase):

    def launched(self, env):
        """Run the rig and return argv. Asserts the launch check let it start.

        A launch that got as far as invoking qwen is a launch the window check
        did not block, which is the whole question these cases ask. The rig's
        own exit code depends on a stub provider and a stub binary and is
        deliberately not asserted here.
        """
        argv, p = self.go(dict(DELIVERS, **env))
        self.assertNotIn(REFUSAL, p.stderr,
                         "the window check refused a launch that fits")
        self.assertNotIn(CONFLICT, p.stderr,
                         "fix 7's conflict check refused a launch that fits")
        self.assertTrue(argv, "qwen was never launched:\n%s" % p.stderr[-2000:])
        return argv, p

    def test_the_r3_configuration_is_refused_before_any_call_is_made(self):
        argv, p = self.go({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                           "SHERLOCK_OUTPUT_TOKENS_PER_S": CR_TOKENS_PER_S,
                           "SHERLOCK_MAX_OUTPUT_TOKENS": "32768"})
        self.assertNotEqual(p.returncode, 0,
                            "a five-times-impossible budget was accepted")
        self.assertEqual(argv, [], "qwen was launched anyway")
        for number in ("32768", "122.6", "35", "90", str(FITTING)):
            self.assertIn(number, p.stderr,
                          "the refusal never names %s:\n%s" % (number, p.stderr[-2000:]))

    def test_the_refusal_says_what_would_fit(self):
        _argv, p = self.go({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                            "SHERLOCK_OUTPUT_TOKENS_PER_S": CR_TOKENS_PER_S,
                            "SHERLOCK_MAX_OUTPUT_TOKENS": "32768"})
        self.assertIn("SHERLOCK_MAX_OUTPUT_TOKENS", p.stderr)
        self.assertIn(str(FITTING), p.stderr)

    def test_a_budget_that_fits_launches(self):
        """CHANGED BY FIX 7 — the rule is the same, "fits" is not.

        This used to be `max_tokens 6700` on a 90 s lane with nothing else
        declared, and it asserted that it launched. That is the v41
        configuration: 6,700 fits the clock and is BELOW qwen's 20,000-token
        compaction reserve, so the run clipped four of its own summaries. Fix 7
        refuses it, so the assertion "it launches" now belongs to a budget that
        fits BOTH constraints — here by the escape the refusal itself names.
        """
        self.launched({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                       "SHERLOCK_OUTPUT_TOKENS_PER_S": CR_TOKENS_PER_S,
                       "SHERLOCK_MAX_OUTPUT_TOKENS": "6700",
                       "SHERLOCK_SESSION_TOKEN_LIMIT":
                           str(unreachable_compaction_limit(6700))})
        self.assertEqual(self.inputs()["generation_window"]["max_output_tokens"], 6700)

    def test_the_conflict_still_refuses_a_budget_below_the_reserve(self):
        """THE OTHER HALF OF THE CASE ABOVE, so "fits" cannot drift back.

        Identical to it except that the session limit is absent. Without a
        settings key making compaction unreachable, 6,700 on a 90 s lane is the
        exact v41 configuration and must NOT launch.
        """
        argv, p = self.go(dict(DELIVERS,
                               SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                               SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                               SHERLOCK_MAX_OUTPUT_TOKENS="6700"))
        self.assertNotEqual(p.returncode, 0, "the v41 budget launched again")
        self.assertEqual(argv, [], "qwen was launched anyway")
        self.assertIn(CONFLICT, p.stderr, p.stderr[-2000:])

    def test_the_escape_is_a_boundary_not_a_switch(self):
        """One token above the boundary and compaction is reachable again.

        The escape is a FACT about the settings, not a waiver, so it must fail
        the moment the settings stop making compaction unreachable. Declaring
        one token more than `unreachable_compaction_limit` puts the largest
        sendable prompt back over qwen's auto-compaction threshold.
        """
        argv, p = self.go(dict(DELIVERS,
                               SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                               SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                               SHERLOCK_MAX_OUTPUT_TOKENS="6700",
                               SHERLOCK_SESSION_TOKEN_LIMIT=str(
                                   unreachable_compaction_limit(6700) + 1)))
        self.assertNotEqual(p.returncode, 0,
                            "a session limit that does not stop compaction "
                            "was accepted as though it did")
        self.assertEqual(argv, [])
        self.assertIn(CONFLICT, p.stderr, p.stderr[-2000:])

    def test_an_undeclared_window_skips_the_check_entirely(self):
        """THE linkapi GUARANTEE. 32,768 output tokens with no declared window
        must launch exactly as it did before fix 9 existed."""
        self.launched({"SHERLOCK_MAX_OUTPUT_TOKENS": "32768"})
        window = self.inputs()["generation_window"]
        self.assertEqual(window["generation_window_seconds"], -1)
        self.assertEqual(window["max_output_tokens"], 32768)
        self.assertEqual(window["fitting_max_output_tokens"], 0)

    def test_an_explicitly_disabled_window_also_skips_the_check(self):
        self.launched({"SHERLOCK_GENERATION_WINDOW_S": "-1",
                       "SHERLOCK_MAX_OUTPUT_TOKENS": "32768"})
        self.assertEqual(
            self.inputs()["generation_window"]["max_output_tokens"], 32768)

    def test_a_declared_window_derives_the_default_budget(self):
        """Unset, on a lane WITH a window, must not fall back to 32,768.

        CHANGED BY FIX 7: only the environment. The derived default on this lane
        is 6,743, which is below the 20,000-token compaction reserve, so the
        launch is now refused unless the settings make compaction unreachable.
        The assertion — that the budget is DERIVED from the window and is not
        32,768 — is exactly the one this case always made.
        """
        self.launched({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                       "SHERLOCK_OUTPUT_TOKENS_PER_S": CR_TOKENS_PER_S,
                       "SHERLOCK_SESSION_TOKEN_LIMIT":
                           str(unreachable_compaction_limit(FITTING))})
        window = self.inputs()["generation_window"]
        self.assertEqual(window["max_output_tokens"], FITTING)
        self.assertEqual(window["fitting_max_output_tokens"], FITTING)
        self.assertNotEqual(window["max_output_tokens"], 32768)

    def test_the_window_is_recorded_beside_the_fix_8_budgets(self):
        """CHANGED BY FIX 7: the session limit is declared so the run reaches
        the point where it writes run-inputs.json at all (this case used to
        ERROR with FileNotFoundError, because the launch was refused before the
        trace was written). The recorded shape asserted here is unchanged, plus
        the new output_budget object that must carry the same number."""
        limit = unreachable_compaction_limit(6700)
        self.go(dict(DELIVERS,
                     SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                     SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                     SHERLOCK_MAX_OUTPUT_TOKENS="6700",
                     SHERLOCK_SESSION_TOKEN_LIMIT=str(limit)))
        inputs = self.inputs()
        self.assertIsInstance(inputs.get("budgets"), dict,
                              "fix 8's budgets object was disturbed")
        window = inputs["generation_window"]
        self.assertEqual(set(window),
                         {"generation_window_seconds", "output_tokens_per_second",
                          "ttft_reserve_seconds", "max_output_tokens",
                          "fitting_max_output_tokens"})
        self.assertEqual(window["generation_window_seconds"], 90)
        self.assertEqual(window["output_tokens_per_second"], 122.6)
        self.assertEqual(window["ttft_reserve_seconds"], 35)
        self.assertEqual(window["max_output_tokens"], 6700)
        self.assertEqual(window["fitting_max_output_tokens"], FITTING)
        # Fix 7's second constraint, recorded beside the first: the escape that
        # let this launch must be legible in the run's own artefacts, or the
        # ground for accepting a sub-reserve budget is unrecoverable later.
        budget = inputs["output_budget"]
        self.assertEqual(budget["session_token_limit"], limit)
        self.assertEqual(budget["max_output_tokens"], 6700)
        self.assertEqual(budget["compaction_summary_reserve"],
                         corporate_settings.SUMMARY_RESERVE)

    def test_a_non_numeric_window_fails_the_run_rather_than_being_ignored(self):
        """Silently reading `ninety` as "no window" would disarm the check on
        the exact run that asked for it."""
        _argv, p = self.go({"SHERLOCK_GENERATION_WINDOW_S": "ninety"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("SHERLOCK_GENERATION_WINDOW_S", p.stderr)

    def test_a_window_that_only_LOOKS_numeric_fails_the_run(self):
        """THE HOLE THIS CAUGHT IN REVIEW. A shell `case` glob cannot tell a
        number from `.`, `-` or `e`, and the arithmetic reads every unparseable
        value as "this lane declares no window" — so `.` would have DISARMED
        the check on exactly the run that asked for it, silently."""
        for raw in (".", "-", "e", "+", "1.2.3", "--90", " "):
            with self.subTest(raw=raw):
                _argv, p = self.go({"SHERLOCK_GENERATION_WINDOW_S": raw,
                                    "SHERLOCK_MAX_OUTPUT_TOKENS": "32768"})
                self.assertNotEqual(p.returncode, 0,
                                    "%r was read as no-window at all" % raw)
                self.assertIn("SHERLOCK_GENERATION_WINDOW_S", p.stderr)

    def test_the_shell_defaults_are_the_measured_constants_not_a_second_copy(self):
        """A second copy of a measurement is a second thing to forget when the
        measurement is redone. The runner must read 122.6 and 35 out of
        lane_guard.py, not carry its own literals."""
        from lane_guard import (GENERATION_WINDOW_TOKENS_PER_S,
                                GENERATION_WINDOW_TTFT_RESERVE_S)
        # CHANGED BY FIX 7: the session limit only. The derived budget on this
        # lane is 6,743, below the compaction reserve, so without the escape the
        # run is refused and never writes run-inputs.json — which is how this
        # case failed after fix 7 (FileNotFoundError, not an assertion).
        self.go(dict(DELIVERS, SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                     SHERLOCK_SESSION_TOKEN_LIMIT=str(
                         unreachable_compaction_limit(FITTING))))
        window = self.inputs()["generation_window"]
        self.assertEqual(window["output_tokens_per_second"],
                         GENERATION_WINDOW_TOKENS_PER_S)
        self.assertEqual(window["ttft_reserve_seconds"],
                         float(GENERATION_WINDOW_TTFT_RESERVE_S))
        runner = os.path.join(os.path.dirname(HERE), os.pardir,
                              "eval", "bench", "run-bench.sh")
        with open(os.path.abspath(runner), encoding="utf-8") as fh:
            text = fh.read()
        block = text[text.index("GEN_VARS="):text.index("SAMPLING_JSON=")]
        self.assertNotIn("122.6", block, "the throughput is copied into shell")
        self.assertNotIn(":-35}", block, "the TTFT reserve is copied into shell")

    def test_a_non_numeric_throughput_fails_the_run(self):
        _argv, p = self.go({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                            "SHERLOCK_OUTPUT_TOKENS_PER_S": "fast"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("SHERLOCK_OUTPUT_TOKENS_PER_S", p.stderr)

    def test_the_window_reaches_the_proxy(self):
        """The proxy is what names the cut in the ledger. If the variable does
        not reach it, the run is cut and cannot say so."""
        _argv, p = self.go(dict(DELIVERS,
                                SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                                SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S))
        self.assertIn("generation window", (p.stdout + p.stderr).lower(),
                      "the run log never names the declared window")


class ThePaidLauncherAllowsTheNewVariablesThrough(unittest.TestCase):
    """bench-controller.sh scrubs the child environment to an ALLOWLIST.

    A new SHERLOCK_* variable that is not on it is silently dropped, which
    would leave the paid run with no declared window at all — the check
    disarmed on exactly the launcher it was written for.
    """
    CONTROLLER = os.path.join(os.path.dirname(HERE), os.pardir,
                              "eval", "bench", "bench-controller.sh")

    def test_both_new_variables_are_on_the_target_allowlist(self):
        with open(os.path.abspath(self.CONTROLLER), encoding="utf-8") as fh:
            text = fh.read()
        start = text.index("TARGET_ENV_ALLOW")
        allow = text[start:text.index("}", start)]
        for var in ("SHERLOCK_GENERATION_WINDOW_S", "SHERLOCK_OUTPUT_TOKENS_PER_S",
                    # ADDED IN FIX 7'S REPAIR. Fix 7 made this variable the ONLY
                    # honest way past a constraint conflict, and left it off the
                    # allowlist — so on the paid launcher it was scrubbed, the
                    # escape could never be taken, and every lane with a 90 s
                    # clock was simply unrunnable. Exactly the defect this class
                    # was written to catch, one fix later.
                    "SHERLOCK_SESSION_TOKEN_LIMIT"):
            self.assertIn(var, allow, "%s is scrubbed by the paid launcher" % var)


if __name__ == "__main__":
    unittest.main()
