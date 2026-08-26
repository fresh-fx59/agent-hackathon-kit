#!/usr/bin/env python3
"""Tests for the lane-integrity guards — the two things v37 did not notice.

The v37 full run burned 180 metered calls while linkapi quietly answered 93 of
them as `deepseek-v4-pro-0813` instead of the flash model the run had committed
to. The substitution split the provider cache pool and the prompt-cache hit
rate fell 68.1 % -> 28.0 % (fresh prompt tokens 5.92M -> 13.38M). Every gate
the harness owned said the run was fine; a human found it days later by diffing
the upstream ledger.

Two guards, tested here:
  * a returned model that is not the one the run asked for aborts the lane, on
    the call;
  * a cumulative prompt-cache rate under the floor aborts the lane.
And, just as importantly, tested here: neither guard reads its own blind spot
as a pass. Absent, empty and malformed ledgers are breaches, so is an empty
expected identity, and so is a usage field that is present but unreadable.

The floor tests below assert the CUMULATIVE rate at the call count the guard
actually fires on, replayed from the five real ledgers. They used to assert
each run's FINAL rate, which certified a margin the guard never sees.

Everything runs against a STUB upstream. No metered tokens.

    python3 measure/tests/test_lane_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")
AUDIT = os.path.join(MEASURE, "lane-audit.py")
sys.path.insert(0, MEASURE)
from lane_guard import (DEFAULT_CACHE_MIN_CALLS, DEFAULT_CACHE_MIN_RATE,  # noqa: E402
                        UsageUnreadable, audit_ledger, cache_breach,
                        cache_tokens, model_family, normalised_id, same_family)

PINNED = "[SP]deepseek-v4-flash-0731"

# CUMULATIVE cache rate at call 30, replayed on 2026-08-26 from the real
# ledgers with lane_guard.cache_tokens. Not the final rates: the guard never
# sees a final rate. See lane_guard.DEFAULT_CACHE_MIN_RATE for the full table.
CUMULATIVE_AT_30 = {"v36-full": 0.608, "v36-smoke": 0.694,
                    "v35-r2": 0.605, "v35-r3": 0.910}
BROKEN_V37_AT_30 = 0.144


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def row(**over):
    base = {"requested_model": "deepseek-v4-flash", "returned_model": "deepseek-v4-flash-0731",
            "status": 200, "usage": {"prompt_tokens": 1000,
                                     "prompt_tokens_details": {"cached_tokens": 800}}}
    base.update(over)
    return base


def write_ledger(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for one in rows:
            fh.write(json.dumps(one) + "\n")


# ---------------------------------------------------------------- family rule
class FamilyRule(unittest.TestCase):

    def test_pinned_id_and_its_alias_are_the_same_family(self):
        # The whole reason a plain string compare is wrong: fix 1 pins
        # `-0731`, and the provider names the same model both ways.
        self.assertTrue(same_family(PINNED, "deepseek-v4-flash"))
        self.assertTrue(same_family(PINNED, "deepseek-v4-flash-0731"))
        self.assertTrue(same_family("deepseek-v4-flash", "deepseek-v4-flash-0731"))

    def test_case_variant_normalises(self):
        # 6 of the 180 v37 calls came back in this casing. Display, not
        # substitution — it must not trip the guard.
        self.assertTrue(same_family(PINNED, "DeepSeek-V4-Flash-0731"))
        self.assertTrue(same_family(PINNED, "  DEEPSEEK-V4-FLASH  "))

    def test_pro_is_a_different_family_from_flash(self):
        # 93 of 180 calls. This is the failure the branch exists for.
        self.assertFalse(same_family(PINNED, "deepseek-v4-pro-0813"))
        self.assertFalse(same_family(PINNED, "DeepSeek-V4-Pro-0813"))

    def test_generation_marker_is_not_a_release_stamp(self):
        # `v4` must survive normalisation or every deepseek would be one family.
        self.assertEqual(model_family("deepseek-v4"), "deepseek-v4")
        self.assertFalse(same_family("deepseek-v4-flash", "deepseek-v5-flash"))

    def test_routing_tag_is_stripped_on_both_sides(self):
        """`[SP]` and `[FREE]` really are different tiers. They still collapse.

        Not an oversight, and re-decided on 2026-08-26 against the ledgers: the
        provider does not report the tag reliably. v36 sent
        `[SP]deepseek-v4-flash` on all 185 calls and answered
        `deepseek-v4-flash-0731` 119x, `DeepSeek-V4-Flash-0731` 23x,
        `deepseek-v4-flash` 29x and `[SP]deepseek-v4-flash` 11x — the same
        request, tagged on 11 rows and untagged on 171. Comparing tags would
        abort every real run on row 1 for a display difference. The tier is our
        own request string, recorded verbatim as `requested_model`/`sent_model`
        on every row, and it cannot change unless we change it; the model that
        ANSWERS is what the provider picks, and that is what this guards.
        """
        self.assertEqual(model_family("[FREE]deepseek-v4-flash-0731"), "deepseek-v4-flash")
        self.assertTrue(same_family("[SP]deepseek-v4-flash", "[FREE]deepseek-v4-flash-2026-07-31"))
        self.assertTrue(same_family(PINNED, "[FREE]deepseek-v4-flash-0731"))

    def test_every_leading_routing_tag_is_stripped_not_only_the_first(self):
        # `[SP][X]foo` used to normalise to `[x]foo`, i.e. a tag became part of
        # the model name and no id would ever match it again.
        self.assertEqual(normalised_id("[SP][X]foo"), "foo")
        self.assertEqual(model_family("[SP][X]deepseek-v4-flash-0731"), "deepseek-v4-flash")
        self.assertTrue(same_family(PINNED, "[SP][X]deepseek-v4-flash-0731"))

    def test_a_different_dated_snapshot_is_not_the_pinned_model(self):
        """The point of pinning `-0731` is to nail ONE snapshot.

        `same_family(PINNED, 'deepseek-v4-flash-1210')` used to be True: both
        stripped to `deepseek-v4-flash`. A provider rolling flash forward is a
        NEW cache pool, which is the v37 failure mode exactly — same split, same
        collapsed hit rate, and it would have sailed through the guard.
        """
        self.assertFalse(same_family(PINNED, "deepseek-v4-flash-1210"))
        self.assertFalse(same_family(PINNED, "deepseek-v4-flash-20261210"))
        self.assertFalse(same_family(PINNED, "deepseek-v4-flash-2026-12-10"))
        self.assertFalse(same_family(PINNED, "deepseek-v4-flash-latest"))
        # …while an UNPINNED expected id still accepts any snapshot, which is
        # what "unpinned" means.
        self.assertTrue(same_family("deepseek-v4-flash", "deepseek-v4-flash-1210"))

    def test_a_suffixed_variant_of_the_pinned_model_is_the_pinned_model(self):
        """A run that got what it asked for must not be killed for a suffix.

        Only one trailing stamp used to be stripped, so `-0731-preview` had
        family `deepseek-v4-flash-0731` and did not match `deepseek-v4-flash` —
        an abort on the right model.
        """
        for suffix in ("-preview", "-fp8", "-thinking", "-0731"):
            got = "deepseek-v4-flash-0731" + suffix
            self.assertTrue(same_family(PINNED, got), got)

    def test_real_world_model_ids_survive_normalisation(self):
        # Shapes this rule must not mangle: a dotted version, a parameter count,
        # a mixture-of-experts marker.
        for name in ("gpt-5.5", "qwen3-235b", "llama-3.1-405b", "mixtral-8x7b",
                     "gpt-oss-120b"):
            self.assertEqual(model_family(name), name)
            self.assertTrue(same_family(name, name))
        self.assertFalse(same_family("gpt-5.5", "gpt-5.6"))
        self.assertFalse(same_family("qwen3-235b", "qwen3-30b"))

    def test_a_three_digit_stamp_is_a_stamp_and_two_digits_are_not(self):
        # Pins `\d{3,8}` in _RELEASE_STAMP. The docstring argues for "3+ digits"
        # so that a generation marker (`v4`) is never read as a date; nothing
        # tested it, and `\d{4,8}` passed every other test in this file.
        self.assertEqual(model_family("some-model-731"), "some-model")
        self.assertEqual(model_family("some-model-0731"), "some-model")
        self.assertEqual(model_family("some-model-73"), "some-model-73")
        self.assertEqual(model_family("deepseek-v4"), "deepseek-v4")

    def test_two_unknown_ids_are_not_a_match(self):
        # Drop the "both must be known" guard and unknown == unknown becomes a
        # PASS — a lane with no identity on either side certifying itself.
        self.assertFalse(same_family("[SP]", "[FREE]"))
        self.assertFalse(same_family(None, None))
        self.assertFalse(same_family("", ""))
        self.assertFalse(same_family("   ", "   "))
        self.assertFalse(same_family(17, 17))

    def test_unknown_is_never_a_match(self):
        for bad in (None, "", "   ", "[SP]", 17, {}):
            self.assertIsNone(model_family(bad), bad)
            self.assertFalse(same_family(PINNED, bad), bad)
        self.assertFalse(same_family(None, "deepseek-v4-flash"))


# ----------------------------------------------------------- the cache formula
class CacheFormula(unittest.TestCase):

    def test_missing_details_counts_as_zero_cached_not_as_unknown(self):
        # 28 of the 180 v37 rows carry no prompt_tokens_details at all. If those
        # were skipped, a provider could hide a collapse by dropping the field.
        self.assertEqual(cache_tokens({"prompt_tokens": 500}), (500, 0))
        self.assertEqual(cache_tokens({"prompt_tokens": 500,
                                       "prompt_tokens_details": None}), (500, 0))

    def test_rows_that_billed_nothing_neither_help_nor_hurt(self):
        self.assertEqual(cache_tokens(None), (0, 0))
        self.assertEqual(cache_tokens({}), (0, 0))
        self.assertEqual(cache_tokens({"prompt_tokens": 0}), (0, 0))

    def test_cached_can_never_exceed_prompt(self):
        self.assertEqual(cache_tokens({"prompt_tokens": 100,
                                       "prompt_tokens_details": {"cached_tokens": 999}}),
                         (100, 100))

    def test_reproduces_the_v37_number(self):
        # The post-mortem recorded 28.0 %; the ledger's own totals are
        # 5,192,376 cached / 18,568,929 prompt = 27.96 %.
        detail = cache_breach(180, 18568929, 5192376)
        self.assertIsNotNone(detail)
        self.assertIn("28.0%", detail)

    def test_a_float_or_string_token_count_still_bills(self):
        """A provider that serialises `1000.0` must not bill zero.

        `type(prompt) is not int` used to return (0, 0) here, so every row of a
        run reporting float or string counts was scored as free: `calls` stayed
        0, the guard never reached its call floor, and a 0 %-cache run passed.
        """
        for value in (1000, 1000.0, "1000", " 1000 "):
            self.assertEqual(cache_tokens({"prompt_tokens": value}), (1000, 0), value)
        self.assertEqual(cache_tokens({"prompt_tokens": 1000.0,
                                       "prompt_tokens_details": {"cached_tokens": "800"}}),
                         (1000, 800))

    def test_a_present_but_unreadable_token_count_is_a_breach_not_a_free_row(self):
        for usage in ({"prompt_tokens": "lots"},
                      {"prompt_tokens": True},
                      {"prompt_tokens": 12.5},
                      {"prompt_tokens": -5},
                      {"prompt_tokens": {"n": 1}},
                      {"prompt_tokens": 1000,
                       "prompt_tokens_details": {"cached_tokens": "some"}}):
            with self.assertRaises(UsageUnreadable, msg=usage):
                cache_tokens(usage)

    def test_healthy_runs_do_not_trip_at_the_rate_the_guard_actually_sees(self):
        """The CUMULATIVE rate at the call floor, not the run's final rate.

        Every healthy ledger, replayed. At the old 20-call floor the worst of
        them was 54.8 %, i.e. 4.8 points above a 50 % floor — v36-smoke touches
        49.4 % on call 18. At 30 calls the worst is 60.5 %, 25.5 points above a
        35 % floor.
        """
        for run, rate in CUMULATIVE_AT_30.items():
            tokens = 1000000
            self.assertIsNone(
                cache_breach(DEFAULT_CACHE_MIN_CALLS, tokens, int(rate * tokens)),
                "%s at %.1f%% must not trip the %.0f%% floor"
                % (run, rate * 100, DEFAULT_CACHE_MIN_RATE * 100))
            self.assertGreater(rate - DEFAULT_CACHE_MIN_RATE, 0.20,
                               "%s has less than 20 points of margin" % run)

    def test_the_broken_run_is_still_caught_by_a_wide_margin(self):
        tokens = 1000000
        detail = cache_breach(DEFAULT_CACHE_MIN_CALLS, tokens,
                              int(BROKEN_V37_AT_30 * tokens))
        self.assertIsNotNone(detail)
        self.assertGreater(DEFAULT_CACHE_MIN_RATE - BROKEN_V37_AT_30, 0.20)

    def test_a_run_exactly_on_the_floor_passes_and_one_token_below_does_not(self):
        # Pins `rate >= min_rate`. `rate > min_rate` survived every other test:
        # nothing sat exactly on the boundary.
        tokens = 1000000
        exact = int(DEFAULT_CACHE_MIN_RATE * tokens)
        self.assertIsNone(cache_breach(DEFAULT_CACHE_MIN_CALLS, tokens, exact))
        self.assertIsNotNone(cache_breach(DEFAULT_CACHE_MIN_CALLS, tokens, exact - 1))
        self.assertIsNone(cache_breach(30, 1000, 500, min_rate=0.5))
        self.assertIsNotNone(cache_breach(30, 1000, 499, min_rate=0.5))

    def test_the_floor_is_not_reached_before_the_call_count(self):
        n = DEFAULT_CACHE_MIN_CALLS
        self.assertIsNone(cache_breach(n - 1, (n - 1) * 1000, 0))
        self.assertIsNotNone(cache_breach(n, n * 1000, 0))


# ------------------------------------------------------- the after-the-fact audit
class LedgerAudit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = os.path.join(self.tmp, "run.upstream.jsonl")

    def audit(self, **kw):
        kw.setdefault("expected_identity", PINNED)
        return audit_ledger(self.ledger, **kw)

    def test_same_family_run_is_clean(self):
        write_ledger(self.ledger, [row() for _ in range(30)])
        self.assertIsNone(self.audit())

    def test_pro_among_flash_is_a_breach(self):
        rows = [row() for _ in range(10)]
        rows[4] = row(returned_model="deepseek-v4-pro-0813")
        write_ledger(self.ledger, rows)
        reason, detail = self.audit()
        self.assertEqual(reason, "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("row 5", detail)

    def test_case_variant_run_is_clean(self):
        write_ledger(self.ledger, [row(returned_model="DeepSeek-V4-Flash-0731")
                                   for _ in range(25)])
        self.assertIsNone(self.audit())

    def test_missing_ledger_fails_closed(self):
        reason, detail = self.audit()
        self.assertEqual(reason, "LEDGER_MISSING")

    def test_empty_ledger_fails_closed(self):
        open(self.ledger, "w").close()
        self.assertEqual(self.audit()[0], "LEDGER_EMPTY")

    def test_unparseable_ledger_fails_closed(self):
        with open(self.ledger, "w", encoding="utf-8") as fh:
            fh.write('{"requested_model": "x"\n')
        self.assertEqual(self.audit()[0], "LEDGER_MALFORMED")

    def test_ledger_line_that_is_not_an_object_fails_closed(self):
        with open(self.ledger, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]\n")
        self.assertEqual(self.audit()[0], "LEDGER_MALFORMED")

    def test_row_without_the_attribution_field_fails_closed(self):
        bad = row()
        del bad["returned_model"]
        write_ledger(self.ledger, [row(), bad])
        self.assertEqual(self.audit()[0], "LEDGER_MALFORMED")

    def test_a_ledger_that_never_names_a_model_fails_closed(self):
        # The proxy was up, the calls succeeded, and not one response could be
        # parsed for a model id. That is not a clean run; it is an unmeasured
        # one, and unmeasured is the state v37 was in for days.
        write_ledger(self.ledger, [row(returned_model=None) for _ in range(5)])
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_UNKNOWN")

    def test_failed_calls_alone_do_not_trip_the_family_guard(self):
        # A provider burst is what the proxy exists to ride out. 400s name no
        # model and must not be read as a substitution — but a run that is ONLY
        # 400s still never measured an identity, so it is still not clean.
        write_ledger(self.ledger, [row(status=400, returned_model=None, usage=None)
                                   for _ in range(5)])
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_UNKNOWN")

    def test_cache_collapse_after_the_call_floor_is_a_breach(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(DEFAULT_CACHE_MIN_CALLS)])
        reason, detail = self.audit()
        self.assertEqual(reason, "PROMPT_CACHE_COLLAPSE")
        self.assertIn("28.0%", detail)

    def test_seventy_percent_passes(self):
        warm = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 700}})
        write_ledger(self.ledger, [warm for _ in range(40)])
        self.assertIsNone(self.audit())

    def test_one_call_short_of_the_floor_is_not_yet_a_breach(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(DEFAULT_CACHE_MIN_CALLS - 1)])
        self.assertIsNone(self.audit())

    def test_an_empty_expected_identity_is_a_breach_not_a_disabled_check(self):
        """Finding #2 of the 2026-08-26 review, end to end.

        `SHERLOCK_EXPECTED_RETURNED_IDENTITY` is only required under
        SHERLOCK_REQUIRE_ATTRIBUTION, which defaults to 0, and
        sherlock-paid-v37-full-r1.sh runs under `env -i` and sets neither. So on
        the one run that got substituted, an empty expected id had turned the
        family check off and only the cache guard was live.
        """
        write_ledger(self.ledger, [row(returned_model="deepseek-v4-pro-0813")
                                   for _ in range(5)])
        self.assertEqual(self.audit(expected_identity="")[0],
                         "EXPECTED_IDENTITY_UNKNOWN")
        self.assertEqual(self.audit(expected_identity=None)[0],
                         "EXPECTED_IDENTITY_UNKNOWN")
        self.assertEqual(self.audit(expected_identity="   ")[0],
                         "EXPECTED_IDENTITY_UNKNOWN")
        # …and the check it used to disable now fires on row 1.
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_a_lane_with_genuinely_no_identity_must_say_so_out_loud(self):
        write_ledger(self.ledger, [row() for _ in range(5)])
        self.assertIsNone(self.audit(expected_identity="", identity_check=False))

    def test_a_type_changed_prompt_tokens_is_a_breach_over_the_whole_ledger(self):
        # 50 rows at 0 % cache. As ints this is PROMPT_CACHE_COLLAPSE; as floats
        # it used to be a clean run with `calls == 0`.
        for value in (1000, 1000.0):
            write_ledger(self.ledger,
                         [row(usage={"prompt_tokens": value,
                                     "prompt_tokens_details": {"cached_tokens": 0}})
                          for _ in range(50)])
            self.assertEqual(self.audit()[0], "PROMPT_CACHE_COLLAPSE", value)
        write_ledger(self.ledger,
                     [row(usage={"prompt_tokens": "many"}) for _ in range(50)])
        reason, detail = self.audit()
        self.assertEqual(reason, "USAGE_UNREADABLE")
        self.assertIn("row 1", detail)

    def test_the_disable_switch_works(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(50)])
        self.assertEqual(self.audit()[0], "PROMPT_CACHE_COLLAPSE")
        self.assertIsNone(self.audit(cache_guard=False))

    def test_thresholds_are_configurable(self):
        rows = [row(usage={"prompt_tokens": 1000,
                           "prompt_tokens_details": {"cached_tokens": 600}})
                for _ in range(35)]
        write_ledger(self.ledger, rows)
        self.assertIsNone(self.audit())
        self.assertEqual(self.audit(min_rate=0.9)[0], "PROMPT_CACHE_COLLAPSE")
        self.assertIsNone(self.audit(min_rate=0.9, min_calls=36))

    def test_family_mismatch_outranks_a_cache_collapse(self):
        # Both are true on a substituted run; the model identity is the cause
        # and the cache rate is the symptom, so the cause is what gets reported.
        rows = [row(returned_model="deepseek-v4-pro-0813",
                    usage={"prompt_tokens": 1000,
                           "prompt_tokens_details": {"cached_tokens": 0}})
                for _ in range(30)]
        write_ledger(self.ledger, rows)
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_a_live_abort_marker_wins_over_the_ledger(self):
        write_ledger(self.ledger, [row() for _ in range(30)])
        marker = os.path.join(self.tmp, "run.upstream.abort.json")
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump({"reason": "PROMPT_CACHE_COLLAPSE", "detail": "28.0%"}, fh)
        self.assertEqual(self.audit(abort_path=marker)[0], "PROMPT_CACHE_COLLAPSE")

    def test_an_unreadable_abort_marker_fails_closed(self):
        write_ledger(self.ledger, [row() for _ in range(30)])
        marker = os.path.join(self.tmp, "run.upstream.abort.json")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(self.audit(abort_path=marker)[0], "LANE_ABORT_UNREADABLE")


class AuditCli(unittest.TestCase):
    """The CLI contract run-bench.sh depends on: rc 1 + reason code on stdout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = os.path.join(self.tmp, "run.upstream.jsonl")

    def run_audit(self, *extra):
        return subprocess.run(
            [sys.executable, AUDIT, "--ledger", self.ledger, "--expected", PINNED] + list(extra),
            capture_output=True, text=True)

    def test_clean_exits_zero_and_says_nothing(self):
        write_ledger(self.ledger, [row() for _ in range(25)])
        done = self.run_audit()
        self.assertEqual((done.returncode, done.stdout.strip()), (0, ""))

    def test_breach_exits_one_with_the_reason_code_on_stdout(self):
        write_ledger(self.ledger, [row(returned_model="deepseek-v4-pro-0813")])
        done = self.run_audit()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout.strip(), "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("lane integrity", done.stderr)

    def test_no_expected_id_on_the_cli_is_a_breach(self):
        # The launcher shape that caused #2: lane-audit.py with no --expected.
        write_ledger(self.ledger, [row(returned_model="deepseek-v4-pro-0813")
                                   for _ in range(30)])
        bare = subprocess.run([sys.executable, AUDIT, "--ledger", self.ledger],
                              capture_output=True, text=True)
        self.assertEqual((bare.returncode, bare.stdout.strip()),
                         (1, "EXPECTED_IDENTITY_UNKNOWN"))
        opted_out = subprocess.run(
            [sys.executable, AUDIT, "--ledger", self.ledger, "--no-identity-check"],
            capture_output=True, text=True)
        self.assertEqual(opted_out.returncode, 0)

    def test_missing_ledger_exits_one(self):
        done = self.run_audit()
        self.assertEqual((done.returncode, done.stdout.strip()), (1, "LEDGER_MISSING"))

    def test_disable_switch_on_the_cli(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(35)])
        self.assertEqual(self.run_audit().stdout.strip(), "PROMPT_CACHE_COLLAPSE")
        self.assertEqual(self.run_audit("--no-cache-guard").returncode, 0)

    def test_the_real_v37_ledger_would_have_been_refused(self):
        # Not a synthetic fixture: the shape of the run that actually happened.
        rows = [row(returned_model="deepseek-v4-flash-0731")]
        rows.append(row(returned_model="deepseek-v4-pro-0813"))
        write_ledger(self.ledger, rows)
        done = self.run_audit()
        self.assertEqual(done.stdout.strip(), "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("row 2", done.stderr)


# --------------------------------------------------- the live guard in the proxy
class Stub(BaseHTTPRequestHandler):
    """Plays the provider. The server object carries the script."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.server.seen += 1
        model = self.server.models[min(self.server.seen - 1, len(self.server.models) - 1)]
        payload = json.dumps({
            "model": model,
            "usage": {"prompt_tokens": self.server.prompt_tokens,
                      "prompt_tokens_details": {"cached_tokens": self.server.cached_tokens}},
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class LiveGuard(unittest.TestCase):
    """The point of the whole exercise: the run stops on the call, not after 180."""

    def setUp(self):
        self.up_port = free_port()
        self.srv = HTTPServer(("127.0.0.1", self.up_port), Stub)
        self.srv.seen = 0
        self.srv.models = ["deepseek-v4-flash-0731"]
        self.srv.prompt_tokens = 1000
        self.srv.cached_tokens = 800
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "upstream.jsonl")
        self.abort = os.path.join(self.tmp, "upstream.abort.json")
        self.px_port = free_port()
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream:
                    stream.close()
        self.srv.shutdown()
        self.srv.server_close()

    def start(self, **extra):
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log, LISTEN_PORT=str(self.px_port),
                   UPSTREAM_LANE_ABORT=self.abort,
                   UPSTREAM_EXPECTED_RETURNED_IDENTITY=PINNED,
                   RUN_TAG="lane-guard-test")
        env.update(extra)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(200):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def call(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": "deepseek-v4-flash", "messages": []}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.getcode(), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def marker(self):
        for _ in range(100):
            if os.path.exists(self.abort):
                with open(self.abort, encoding="utf-8") as fh:
                    return json.load(fh)
            time.sleep(0.02)
        self.fail("no abort marker at %s" % self.abort)

    def test_same_family_never_trips(self):
        self.srv.models = ["deepseek-v4-flash", "DeepSeek-V4-Flash-0731",
                           "deepseek-v4-flash-0731"]
        self.start()
        for _ in range(6):
            self.assertEqual(self.call()[0], 200)
        self.assertFalse(os.path.exists(self.abort))

    def test_a_substituted_model_aborts_the_lane_on_that_call(self):
        self.srv.models = ["deepseek-v4-flash-0731", "deepseek-v4-pro-0813"]
        self.start()
        self.assertEqual(self.call()[0], 200)
        self.assertEqual(self.call()[0], 200)        # the offending call still relays
        marker = self.marker()
        self.assertEqual(marker["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("deepseek-v4-pro-0813", marker["detail"])
        # …and every call after it is refused, which is where the money is.
        status, body = self.call()
        # 403, not 503: qwen-code retries every 5xx, treats 503 as a
        # Retry-After status, and lists it in FALLBACK_ELIGIBLE_STATUS_CODES.
        # A 503 refusal burned the client's retry budget and made the run's own
        # verdict read as "the provider is down".
        self.assertEqual(status, 403)
        self.assertIn(b"RETURNED_MODEL_FAMILY_MISMATCH", body)
        self.assertEqual(self.srv.seen, 2, "the provider was called after the abort")
        # The offending call is IN the ledger — an abort whose cause is the one
        # row the ledger lacks explains nothing.
        with open(self.log, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual([r["returned_model"] for r in rows],
                         ["deepseek-v4-flash-0731", "deepseek-v4-pro-0813"])

    def test_a_cache_collapse_aborts_after_the_configured_call_count(self):
        self.srv.cached_tokens = 280                 # 28.0 %, the v37 rate
        self.start(UPSTREAM_CACHE_MIN_CALLS="5")
        for index in range(5):
            self.assertEqual(self.call()[0], 200, index)
        marker = self.marker()
        self.assertEqual(marker["reason"], "PROMPT_CACHE_COLLAPSE")
        self.assertIn("28.0%", marker["detail"])
        self.assertEqual(self.call()[0], 403)

    def test_a_healthy_cache_rate_never_trips(self):
        self.srv.cached_tokens = 700                 # 70 %
        self.start(UPSTREAM_CACHE_MIN_CALLS="5")
        for _ in range(12):
            self.assertEqual(self.call()[0], 200)
        self.assertFalse(os.path.exists(self.abort))

    def test_the_disable_switch_works_live(self):
        self.srv.cached_tokens = 280
        self.start(UPSTREAM_CACHE_MIN_CALLS="5", UPSTREAM_CACHE_GUARD="0")
        for _ in range(12):
            self.assertEqual(self.call()[0], 200)
        self.assertFalse(os.path.exists(self.abort))

    def test_a_stale_marker_from_an_earlier_run_is_not_this_run_verdict(self):
        # upstream-lane.sh deletes it before launching the proxy; prove the
        # deletion is what makes a clean rerun possible under the same name.
        with open(self.abort, "w", encoding="utf-8") as fh:
            json.dump({"reason": "PROMPT_CACHE_COLLAPSE", "detail": "old"}, fh)
        subprocess.run(["rm", "-f", self.abort], check=True)
        self.start()
        self.assertEqual(self.call()[0], 200)
        self.assertFalse(os.path.exists(self.abort))

    def test_the_marker_exists_before_any_refusal_is_served(self):
        """The kill race: `save_trace` terminates the proxy the instant the CLI
        exits, and the CLI exits as soon as it is refused. Measured before the
        fix, 18 of 20 trials served the refusal and left NO abort.json — only an
        orphan `.upstream-lane-abort.XXXXXXXX` holding the complete row, killed
        inside the fsync between mkstemp and os.replace. Combined with the dead
        RC-5 audit the run then reported no lane verdict at all.
        """
        self.srv.models = ["deepseek-v4-pro-0813"]
        self.start()
        codes = []
        for _ in range(4):
            codes.append(self.call()[0])
            if codes[-1] >= 400:
                break
        self.assertGreaterEqual(codes[-1], 400, codes)
        # Read the marker with NO retry loop: by the time a refusal is on the
        # wire the file must already be there.
        self.assertTrue(os.path.exists(self.abort),
                        "a refusal was served with no abort marker on disk")
        with open(self.abort, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reason"],
                             "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_the_paid_budget_refusal_keeps_its_own_status(self):
        """Only the LANE refusal moved to 403. The paid-budget path is wired to
        MAX_CONSECUTIVE_PROVIDER_FAILURES accounting and the controller's
        polling, and this branch does not touch it."""
        with open(PROXY, encoding="utf-8") as fh:
            source = fh.read()
        budget = source[source.index("def _budget_refusal"):]
        self.assertIn("self.send_response(503)", budget.split("def ")[1])

    def test_the_abort_is_written_into_the_budget_state_the_controller_polls(self):
        state = os.path.join(self.tmp, "upstream-budget-state.json")
        with open(state, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "run_tag": "lane-guard-test",
                       "updated_at": "2026-08-26T00:00:00Z", "attempts_charged": 0,
                       "request_bytes": 0, "consecutive_provider_failures": 0,
                       "limits": {"max_upstream_attempts": 50,
                                  "max_request_bytes": 10000000,
                                  "max_wall_seconds": 600,
                                  "max_consecutive_provider_failures": 5},
                       "verdict": "WITHIN", "reason": None}, fh)
        self.srv.models = ["deepseek-v4-pro-0813"]
        self.start(UPSTREAM_BUDGET_STATE=state,
                   UPSTREAM_MAX_UPSTREAM_ATTEMPTS="50",
                   UPSTREAM_MAX_REQUEST_BYTES="10000000",
                   UPSTREAM_MAX_WALL_SECONDS="600",
                   UPSTREAM_MAX_CONSECUTIVE_PROVIDER_FAILURES="5")
        self.call()
        self.marker()
        for _ in range(100):
            with open(state, encoding="utf-8") as fh:
                row_ = json.load(fh)
            if row_["verdict"] == "EXCEEDED":
                break
            time.sleep(0.02)
        self.assertEqual(row_["verdict"], "EXCEEDED")
        self.assertEqual(row_["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
