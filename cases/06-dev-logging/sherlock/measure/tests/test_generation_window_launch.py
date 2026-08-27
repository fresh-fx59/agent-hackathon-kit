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
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_declared_budgets import Rig                              # noqa: E402
from lane_guard import fitting_max_output_tokens                   # noqa: E402

CR_WINDOW_S = "90"
CR_TOKENS_PER_S = "122.6"
FITTING = fitting_max_output_tokens(90, 122.6, 35)                  # 6743
# The stub qwen writes this, so the rig's run "delivers" and does not spend 45 s
# on resume attempts. The exit code of a stubbed run is NOT the subject here
# (the lane audit correctly calls a stub run's absent ledger a breach); what
# matters is whether the launch check let the run START.
DELIVERS = {"QWEN_STUB_REPORT": "# stub\n\napps/api.log:1 ok\n"}
REFUSAL = "cannot be delivered inside this lane's generation window"


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
        self.launched({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                       "SHERLOCK_OUTPUT_TOKENS_PER_S": CR_TOKENS_PER_S,
                       "SHERLOCK_MAX_OUTPUT_TOKENS": "6700"})
        self.assertEqual(self.inputs()["generation_window"]["max_output_tokens"], 6700)

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
        """Unset, on a lane WITH a window, must not fall back to 32,768."""
        self.launched({"SHERLOCK_GENERATION_WINDOW_S": CR_WINDOW_S,
                       "SHERLOCK_OUTPUT_TOKENS_PER_S": CR_TOKENS_PER_S})
        window = self.inputs()["generation_window"]
        self.assertEqual(window["max_output_tokens"], FITTING)
        self.assertEqual(window["fitting_max_output_tokens"], FITTING)
        self.assertNotEqual(window["max_output_tokens"], 32768)

    def test_the_window_is_recorded_beside_the_fix_8_budgets(self):
        self.go(dict(DELIVERS,
                     SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                     SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                     SHERLOCK_MAX_OUTPUT_TOKENS="6700"))
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
        self.go(dict(DELIVERS, SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S))
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
        for var in ("SHERLOCK_GENERATION_WINDOW_S", "SHERLOCK_OUTPUT_TOKENS_PER_S"):
            self.assertIn(var, allow, "%s is scrubbed by the paid launcher" % var)


if __name__ == "__main__":
    unittest.main()
