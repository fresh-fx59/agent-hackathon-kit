#!/usr/bin/env python3
"""Tests for the accounting half of the lane audit — the line that lied.

THE BUG THESE EXIST FOR. On the v38 full run (2026-08-26, 2h42m, real money)
`lane-substitutions.json` said

    {"accepted_calls": 0, "discarded_substitutions": 0, "prompt_tokens": 0, ...}

and the console printed

    ℹ lane cost: 0 accepted calls, 0 discarded substitutions
      (0.0% of 0 billed answers, 0 prompt tokens paid for nothing)

while the SAME run's abort marker recorded 278 calls / 27,773,863 prompt tokens
and the ledger held 463 rows. `audit_ledger` initialised the summary to zeros
and then returned the live guard's reason before reading a single row. The one
number that decides whether to spend money again was a zero, on the exact run
where it mattered.

So these tests run against THE REAL LEDGER, not a synthetic stand-in: 463 rows
from that run, pruned to the fields the audit reads (the body-capture and
stream-telemetry columns are dropped; every audited value is verbatim). A
fixture that cannot reproduce the observed zeros is not proof.

    python3 measure/tests/test_lane_cost.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
AUDIT = os.path.join(MEASURE, "lane-audit.py")
FIXTURES = os.path.join(HERE, "fixtures")
REAL_LEDGER = os.path.join(FIXTURES, "v38-full-run.upstream.jsonl")
REAL_ABORT = os.path.join(FIXTURES, "v38-full-run.upstream.abort.json")

sys.path.insert(0, MEASURE)
from lane_guard import (ACCOUNTING_TERMS, DISCARD_BYTES_PER_TOKEN,  # noqa: E402
                        _accounting_gaps, audit_ledger,  # noqa: E402
                        deterministic_refusal)

# The figures of record, counted from the paid run's own ledger.
ROWS = 463
ACCEPTED_ROWS = 280          # rows that were not discarded substitutions
BILLED_CALLS = 463           # linkapi bills a flat 0.05 CNY per CALL
USAGE_CALLS = 278            # rows that carry a provider usage block
PROMPT_TOKENS = 27773863
CACHED_TOKENS = 15948528
DISCARDED = 183
DISCARDED_BYTES = 62773646
IDENTITY = "[次]deepseek-v4-flash"


def run_audit(*args):
    proc = subprocess.run([sys.executable, AUDIT] + list(args),
                          capture_output=True, text=True)
    return proc


class RealLedgerAccounting(unittest.TestCase):
    """The recorded 463-row ledger + the abort marker it really shipped with."""

    def audit(self, **kw):
        summary = {}
        breach = audit_ledger(REAL_LEDGER, expected_identity=IDENTITY,
                              abort_path=REAL_ABORT, summary=summary, **kw)
        return breach, summary

    def test_live_guard_reason_still_wins(self):
        breach, _ = self.audit()
        self.assertEqual(breach[0], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_counts_are_the_real_ones_not_zeros(self):
        _, s = self.audit()
        self.assertTrue(s["complete"], s.get("incomplete_reason"))
        self.assertEqual(s["ledger_rows"], ROWS)
        self.assertEqual(s["accepted_rows"], ACCEPTED_ROWS)
        self.assertEqual(s["accepted_calls"], USAGE_CALLS)
        self.assertEqual(s["billed_calls"], BILLED_CALLS)
        self.assertEqual(s["prompt_tokens"], PROMPT_TOKENS)
        self.assertEqual(s["cached_tokens"], CACHED_TOKENS)
        self.assertEqual(s["discarded_substitutions"], DISCARDED)
        self.assertEqual(s["discarded_request_bytes"], DISCARDED_BYTES)

    def test_abort_marker_agrees_with_the_ledger(self):
        """The marker's own cache_observed is the cross-check."""
        marker = json.load(open(REAL_ABORT, encoding="utf-8"))
        _, s = self.audit()
        self.assertEqual(marker["cache_observed"]["prompt_tokens"], s["prompt_tokens"])
        self.assertEqual(marker["cache_observed"]["cached_tokens"], s["cached_tokens"])
        self.assertEqual(marker["cache_observed"]["calls"], s["accepted_calls"])

    def test_discarded_tokens_are_zero_but_the_waste_is_not(self):
        """usage is null on an aborted stream, so bytes are the only measure."""
        _, s = self.audit()
        self.assertEqual(s["discarded_prompt_tokens"], 0)
        self.assertEqual(s["discarded_prompt_tokens_estimated"],
                         DISCARDED_BYTES // DISCARD_BYTES_PER_TOKEN)
        self.assertGreater(s["discarded_prompt_tokens_estimated"], 15000000)

    def test_estimate_is_never_merged_into_a_measurement(self):
        _, s = self.audit()
        self.assertNotEqual(s["discarded_prompt_tokens"],
                            s["discarded_prompt_tokens_estimated"])
        self.assertEqual(s["estimate_bytes_per_token"], DISCARD_BYTES_PER_TOKEN)
        self.assertIn("estimate", s["estimate_basis"].lower())

    def test_every_accounting_term_is_present_and_numeric(self):
        """A term that can vanish is a term nobody has to maintain."""
        _, s = self.audit()
        for term in ACCOUNTING_TERMS:
            self.assertIn(term, s)
            self.assertIsInstance(s[term], (int, float), term)

    def test_every_discarded_model_is_named_separately(self):
        """182 answered as the alias, 1 as the DATED id — a returned-side name
        the provider cannot be asked for. Both are money, neither is merged."""
        _, s = self.audit()
        self.assertEqual(s["discarded_by_model"],
                         {"deepseek-v4-pro": 182, "deepseek-v4-pro-0813": 1})
        self.assertEqual(sum(s["discarded_by_model"].values()), DISCARDED)

    def test_the_finished_ledger_alone_shows_no_breach(self):
        """WHY THE LIVE GUARD IS NOT A SECOND LINE. Every wrong-model answer in
        this run was DISCARDED and re-issued, so the after-the-fact walk over
        the same 463 rows finds nothing to refuse: the reason exists only
        because the proxy observed it with the run alive. Recorded so the two
        readings stay distinguishable in the artifact."""
        _, s = self.audit()
        self.assertEqual(s["abort_marker_reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertEqual(s["ledger_verdict"], "")

    def test_the_one_deterministic_400_is_counted(self):
        _, s = self.audit()
        self.assertEqual(s["refused_calls"], 1)


class PrintedLine(unittest.TestCase):
    """Assert on what a HUMAN reads. The printed line is the thing that lied."""

    def test_real_run_prints_the_true_figures(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "s.json")
            proc = run_audit("--ledger", REAL_LEDGER, "--abort", REAL_ABORT,
                             "--expected", IDENTITY, "--summary-json", out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("RETURNED_MODEL_FAMILY_MISMATCH", proc.stdout)
        line = [l for l in proc.stderr.splitlines() if "lane cost" in l]
        self.assertEqual(len(line), 1, proc.stderr)
        line = line[0]
        for number in ("280", "183", "463", "27773863", "62773646"):
            self.assertIn(number, line, line)
        self.assertNotIn("0 accepted", line)
        self.assertNotIn("of 0 billed", line)

    def test_measured_and_estimated_are_different_words(self):
        proc = run_audit("--ledger", REAL_LEDGER, "--abort", REAL_ABORT,
                         "--expected", IDENTITY)
        line = [l for l in proc.stderr.splitlines() if "lane cost" in l][0]
        self.assertIn("MEASURED", line)
        self.assertIn("ESTIMATED", line)
        # the estimate must be marked as not a provider number
        self.assertIn("not a provider number", line)

    def test_summary_json_is_no_longer_all_zeros(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "lane-substitutions.json")
            run_audit("--ledger", REAL_LEDGER, "--abort", REAL_ABORT,
                      "--expected", IDENTITY, "--summary-json", out)
            written = json.load(open(out, encoding="utf-8"))
        self.assertEqual(written["prompt_tokens"], PROMPT_TOKENS)
        self.assertEqual(written["discarded_substitutions"], DISCARDED)
        self.assertTrue(written["complete"])


class UnreadableLedger(unittest.TestCase):
    """Absence of proof is not zero cost — the same fail-closed rule as the rest."""

    def test_missing_ledger_with_abort_marker_says_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = {}
            breach = audit_ledger(os.path.join(tmp, "nope.jsonl"),
                                  expected_identity=IDENTITY,
                                  abort_path=REAL_ABORT, summary=summary)
        self.assertEqual(breach[0], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertFalse(summary["complete"])
        self.assertTrue(summary["incomplete_reason"])
        self.assertIn("nope.jsonl", summary["incomplete_reason"])

    def test_no_zero_cost_line_is_printed_when_nothing_was_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.jsonl")
            proc = run_audit("--ledger", missing, "--abort", REAL_ABORT,
                             "--expected", IDENTITY)
        self.assertNotIn("0 accepted calls", proc.stderr)
        self.assertIn("NOT MEASURED", proc.stderr)
        self.assertIn("nope.jsonl", proc.stderr)

    def test_garbage_rows_are_counted_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"requested_model": "m", "returned_model": "m",
                                     "status": 200, "request_bytes": 100,
                                     "usage": {"prompt_tokens": 10}}) + "\n")
                fh.write("{not json\n")
            summary = {}
            audit_ledger(path, expected_identity="m", summary=summary,
                         cache_guard=False)
        self.assertEqual(summary["ledger_rows"], 2)
        self.assertEqual(summary["unaccountable_rows"], 1)
        self.assertFalse(summary["complete"])

    def test_incomplete_accounting_alone_is_a_nonzero_exit(self):
        """THE GATE IS WIRED TO THE EXIT CODE, not only to the console.

        In practice an unreadable ledger also trips a stronger reason
        (LEDGER_MISSING / LEDGER_MALFORMED), so this gate is the DEFENSIVE one:
        it catches an accounting that came back uncomputed while the verdict
        walk was happy. It is asserted by driving lane-audit.py's own main()
        with exactly that pair, because a term that is computed and printed but
        absent from the exit code is the defect class this lane keeps shipping.
        """
        spec = importlib.util.spec_from_file_location("lane_audit_under_test",
                                                      AUDIT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def no_breach_but_no_numbers(ledger, **kw):
            kw["summary"].update({"complete": False,
                                  "incomplete_reason": "counted nothing at all"})
            return None

        module.audit_ledger = no_breach_but_no_numbers
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            module.sys.stderr = err
            try:
                rc = module.main(["--ledger", "nowhere.jsonl", "--expected", "m"])
            finally:
                module.sys.stderr = sys.stderr
        self.assertEqual(rc, 1)
        self.assertIn("LANE_ACCOUNTING_INCOMPLETE", out.getvalue())
        self.assertIn("counted nothing at all", err.getvalue())
        self.assertIn("NOT MEASURED", err.getvalue())

    def test_every_accounting_term_is_watched_by_the_gate(self):
        """Every NAMED term is checked; drop one and the summary goes incomplete."""
        for term in ACCOUNTING_TERMS:
            summary = dict.fromkeys(ACCOUNTING_TERMS, 0)
            del summary[term]
            self.assertEqual(_accounting_gaps(summary), (term,), term)
        self.assertEqual(_accounting_gaps(dict.fromkeys(ACCOUNTING_TERMS, 0)), ())


class ProviderReportedCost(unittest.TestCase):
    """CloseRouter returns usage.cost in USD. A payer's number beats an estimate."""

    def ledger(self, tmp, *usages):
        path = os.path.join(tmp, "l.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for usage in usages:
                fh.write(json.dumps({"requested_model": "m", "returned_model": "m",
                                     "status": 200, "request_bytes": 100,
                                     "usage": usage}) + "\n")
        return path

    def test_cost_accumulates_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.ledger(tmp,
                               {"prompt_tokens": 10, "cost": 0.25},
                               {"prompt_tokens": 10, "cost": 0.5})
            summary = {}
            audit_ledger(path, expected_identity="m", summary=summary,
                         cache_guard=False)
        self.assertAlmostEqual(summary["cost_usd_reported"], 0.75, places=6)
        self.assertEqual(summary["cost_usd_reported_calls"], 2)

    def test_cost_untouched_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.ledger(tmp, {"prompt_tokens": 10})
            summary = {}
            audit_ledger(path, expected_identity="m", summary=summary,
                         cache_guard=False)
        self.assertEqual(summary["cost_usd_reported"], 0.0)
        self.assertEqual(summary["cost_usd_reported_calls"], 0)

    def test_reported_cost_is_printed_when_the_provider_gave_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.ledger(tmp, {"prompt_tokens": 10, "cost": 0.25})
            proc = run_audit("--ledger", path, "--expected", "m",
                             "--no-cache-guard")
        self.assertIn("provider-reported", proc.stderr)
        self.assertIn("0.25", proc.stderr)

    def test_real_run_reports_no_provider_cost(self):
        """linkapi sends no usage.cost, so this must stay 0 and say nothing."""
        summary = {}
        audit_ledger(REAL_LEDGER, expected_identity=IDENTITY,
                     abort_path=REAL_ABORT, summary=summary)
        self.assertEqual(summary["cost_usd_reported_calls"], 0)


class DeterministicRefusal(unittest.TestCase):
    """A 400 that retrying cannot fix must be its own named class.

    Both strings below were produced by the real provider, not invented:
    the first by a probe against CloseRouter on 2026-08-26 (max_tokens=4), the
    second by the dead v38 run itself — its ONE 400, 220 KB of request, whose
    SSE body reads verbatim "The `reasoning_content` in the thinking mode must
    be passed back to the API."
    """
    BUDGET = ("the output token limit was exhausted by model reasoning before "
              "an answer was produced; increase max_completion_tokens/"
              "max_output_tokens")
    RELAY = ("The `reasoning_content` in the thinking mode must be passed back "
             "to the API.")

    def test_output_budget_class(self):
        self.assertEqual(deterministic_refusal(400, self.BUDGET),
                         "OUTPUT_BUDGET_EXHAUSTED_BY_REASONING")

    def test_reasoning_relay_class(self):
        self.assertEqual(deterministic_refusal(400, self.RELAY),
                         "REASONING_CONTENT_NOT_RELAYED")

    def test_the_dead_runs_own_400_body_classifies(self):
        body = json.dumps({"error": {"message": self.RELAY,
                                     "type": "upstream_error", "code": None}})
        self.assertEqual(deterministic_refusal(400, body),
                         "REASONING_CONTENT_NOT_RELAYED")

    def test_a_transient_burst_400_is_not_this_class(self):
        for text in ("Upstream request failed", "Rate limit exceeded",
                     "invalid request", ""):
            self.assertIsNone(deterministic_refusal(400, text), text)

    def test_only_400_is_classified(self):
        self.assertIsNone(deterministic_refusal(503, self.BUDGET))
        self.assertIsNone(deterministic_refusal(None, self.BUDGET))

    def test_the_class_reaches_the_audit_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"requested_model": "m", "returned_model": "m",
                                     "status": 200, "request_bytes": 10,
                                     "usage": {"prompt_tokens": 10}}) + "\n")
                fh.write(json.dumps({
                    "requested_model": "m", "returned_model": None,
                    "status": 400, "request_bytes": 220345,
                    "request_max_tokens": 32768, "usage": None,
                    "upstream_refusal_class": "REASONING_CONTENT_NOT_RELAYED",
                }) + "\n")
            summary = {}
            audit_ledger(path, expected_identity="m", summary=summary,
                         cache_guard=False)
            proc = run_audit("--ledger", path, "--expected", "m",
                             "--no-cache-guard")
        self.assertEqual(summary["provider_refusals"],
                         {"REASONING_CONTENT_NOT_RELAYED": 1})
        self.assertEqual(summary["refused_calls"], 1)
        self.assertIn("REASONING_CONTENT_NOT_RELAYED", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
