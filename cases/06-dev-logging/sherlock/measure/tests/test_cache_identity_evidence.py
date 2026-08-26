#!/usr/bin/env python3
"""Fix 7 — the cache floor may not overrule direct identity evidence.

WHY THIS FILE EXISTS. On 2026-08-26 the v39 CloseRouter run died at call 30 of a
perfectly healthy lane: 30 billed calls, HTTP 200 x30, finish_reason tool_calls
x30, and `returned_model == route_expected_identity ==
deepseek/deepseek-v4-flash-0731` on every single row. Its cumulative prompt-cache
hit rate was 16.4 % (399,232 / 2,427,380) against a 35 % floor, so the LIVE proxy
guard tripped PROMPT_CACHE_COLLAPSE, call 31 got `403 proxy: lane aborted`, and a
paid run was over.

The cache rate is a PROXY SIGNAL for a substitution that cannot be seen
directly (the v37 shape: floating alias, billed rows naming no model at all).
Here the DIRECT signal was present and unanimous. A proxy signal must never
overrule the direct measurement it stands in for.

The floor is NOT lowered and there is no per-provider threshold table — that
moves the cliff instead of removing it. What changes is WHICH CALLS the floor is
allowed to judge.

THE ASSERTION WHOSE ABSENCE COST THE RUN is `TheLiveProxyGuard
.test_a_zero_percent_cache_identity_confirmed_lane_is_never_403ed`: nothing
drove the real proxy past its call floor on an identity-confirmed lane and
checked that the client still got a 200.
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
FIXTURE = os.path.join(HERE, "fixtures",
                       "v39-closerouter-30-calls.upstream.jsonl")
sys.path.insert(0, MEASURE)
from lane_guard import (CACHE_JUDGEMENT_TERMS, DEFAULT_CACHE_MIN_CALLS,  # noqa: E402
                        DEFAULT_CACHE_MIN_RATE, audit_ledger, cache_cost_fact,
                        cache_judgement, cache_terms, cache_terms_gaps,
                        note_cache_call)

# The v39 run's own identity and its own numbers, verbatim from the fixture.
V39_IDENTITY = "deepseek/deepseek-v4-flash-0731"
V39_PROMPT_TOKENS = 2427380
V39_CACHED_TOKENS = 399232
V39_CALLS = 30
V39_CALLS_WITH_ANY_HIT = 7
PINNED = "[SP]deepseek-v4-flash-0731"


def row(**over):
    base = {"requested_model": "deepseek-v4-flash",
            "returned_model": "deepseek-v4-flash-0731",
            "status": 200,
            "usage": {"prompt_tokens": 1000,
                      "prompt_tokens_details": {"cached_tokens": 0}}}
    base.update(over)
    return base


def write_ledger(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for one in rows:
            fh.write(json.dumps(one) + "\n")


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ------------------------------------------------- the real 30-row v39 ledger
class TheRealClosERouterLedger(unittest.TestCase):
    """The committed fixture IS the run that died. Values verbatim."""

    def test_the_fixture_still_holds_the_measured_numbers(self):
        with open(FIXTURE, encoding="utf-8") as source:
            rows = [json.loads(line) for line in source if line.strip()]
        self.assertEqual(len(rows), V39_CALLS)
        self.assertEqual({r["status"] for r in rows}, {200})
        self.assertEqual({r["finish_reason"] for r in rows}, {"tool_calls"})
        self.assertEqual({r["stream_complete"] for r in rows}, {True})
        self.assertEqual({r["upstream_error"] for r in rows}, {None})
        self.assertEqual({r["returned_model"] for r in rows}, {V39_IDENTITY})
        self.assertEqual({r["route_expected_identity"] for r in rows},
                         {V39_IDENTITY})
        self.assertFalse(any("discarded_substitution" in r for r in rows))
        prompt = sum(r["usage"]["prompt_tokens"] for r in rows)
        cached = sum(r["usage"]["prompt_tokens_details"]["cached_tokens"]
                     for r in rows)
        self.assertEqual((prompt, cached), (V39_PROMPT_TOKENS, V39_CACHED_TOKENS))
        hits = sum(1 for r in rows
                   if r["usage"]["prompt_tokens_details"]["cached_tokens"])
        # 7 of 30, with a monotonically growing prompt inside one conversation:
        # a split cache pool halves hits across two models, a gateway that does
        # not cache most calls looks exactly like this.
        self.assertEqual(hits, V39_CALLS_WITH_ANY_HIT)
        self.assertLess(cached / prompt, DEFAULT_CACHE_MIN_RATE)
        self.assertGreaterEqual(len(rows), DEFAULT_CACHE_MIN_CALLS)

    def test_the_run_that_died_now_audits_clean(self):
        summary = {}
        self.assertIsNone(audit_ledger(FIXTURE, expected_identity=V39_IDENTITY,
                                       summary=summary))
        terms = summary["cache_judgement"]
        self.assertEqual(terms["identity_confirmed_calls"], V39_CALLS)
        self.assertEqual(terms["identity_unconfirmed_calls"], 0)
        self.assertEqual(terms["identity_confirmed_prompt_tokens"],
                         V39_PROMPT_TOKENS)
        self.assertEqual(terms["identity_confirmed_cached_tokens"],
                         V39_CACHED_TOKENS)

    def test_it_audits_clean_on_the_rows_own_route_identity_alone(self):
        # No --expected at all: every row carries the identity it was sent
        # under, which is better evidence than a flag passed afterwards.
        self.assertIsNone(audit_ledger(FIXTURE))

    def test_the_cost_fact_names_the_real_numbers_loudly(self):
        summary = {}
        audit_ledger(FIXTURE, expected_identity=V39_IDENTITY, summary=summary)
        line = cache_cost_fact(summary["cache_judgement"])
        self.assertIn("16.4%", line)
        self.assertIn(str(V39_CACHED_TOKENS), line)
        self.assertIn(str(V39_PROMPT_TOKENS), line)
        self.assertIn("30 identity-confirmed", line)
        self.assertIn("0 unconfirmed", line)
        self.assertIn("REAL MONEY", line)
        self.assertIn("NOT a breach", line)

    def test_the_cli_exits_zero_and_prints_the_cost_fact(self):
        done = subprocess.run([sys.executable, AUDIT, "--ledger", FIXTURE,
                               "--expected", V39_IDENTITY],
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, ""))
        self.assertIn("prompt-cache COST FACT", done.stderr)
        self.assertIn("16.4%", done.stderr)
        self.assertIn("REAL MONEY", done.stderr)

    def test_the_printed_floor_is_the_floor_the_cli_was_given(self):
        """The quiet half of the line, and the plumbing behind it.

        A cost fact that always names the DEFAULT floor would be a printed-only
        number: it would still read "35.0%" on a run judged against another one.
        At a 10 % floor the v39 run's 16.4 % is comfortably above it, so the
        line drops the REAL MONEY clause and downgrades to the ℹ prefix — which
        is the only test of that branch.
        """
        done = subprocess.run([sys.executable, AUDIT, "--ledger", FIXTURE,
                               "--expected", V39_IDENTITY,
                               "--cache-min-rate", "0.10"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertIn("the 10.0% floor", done.stderr)
        self.assertNotIn("35.0% floor", done.stderr)
        self.assertIn("ℹ prompt-cache COST FACT", done.stderr)
        self.assertNotIn("REAL MONEY", done.stderr)


# ------------------------------------------- the rule, at the level of the terms
class TheFloorsJurisdiction(unittest.TestCase):

    def terms(self, confirmed=(0, 0, 0), unconfirmed=(0, 0, 0)):
        out = cache_terms()
        (out["identity_confirmed_calls"], out["identity_confirmed_prompt_tokens"],
         out["identity_confirmed_cached_tokens"]) = confirmed
        (out["identity_unconfirmed_calls"],
         out["identity_unconfirmed_prompt_tokens"],
         out["identity_unconfirmed_cached_tokens"]) = unconfirmed
        return out

    def test_a_zero_percent_confirmed_only_lane_is_not_a_breach(self):
        self.assertIsNone(cache_judgement(self.terms(confirmed=(500, 5000000, 0))))

    def test_a_zero_percent_unconfirmed_lane_is_still_a_breach(self):
        reason, detail = cache_judgement(self.terms(unconfirmed=(500, 5000000, 0)))
        self.assertEqual(reason, "PROMPT_CACHE_COLLAPSE")
        self.assertIn("0.0%", detail)

    def test_confirmed_calls_never_dilute_the_unconfirmed_verdict(self):
        # The hole a naive "judge the totals" fix leaves: a provider could bury
        # a cold substituted pool under a warm confirmed one.
        mixed = self.terms(confirmed=(DEFAULT_CACHE_MIN_CALLS, 1000000, 990000),
                           unconfirmed=(DEFAULT_CACHE_MIN_CALLS, 1000000, 0))
        self.assertEqual(cache_judgement(mixed)[0], "PROMPT_CACHE_COLLAPSE")
        self.assertIn("%d billed call(s) whose model identity was NOT"
                      % DEFAULT_CACHE_MIN_CALLS, cache_judgement(mixed)[1])

    def test_the_call_floor_applies_to_the_unconfirmed_bucket(self):
        n = DEFAULT_CACHE_MIN_CALLS
        self.assertIsNone(cache_judgement(
            self.terms(confirmed=(500, 5000000, 0),
                       unconfirmed=(n - 1, (n - 1) * 1000, 0))))
        self.assertEqual(cache_judgement(
            self.terms(confirmed=(500, 5000000, 0),
                       unconfirmed=(n, n * 1000, 0)))[0],
            "PROMPT_CACHE_COLLAPSE")

    def test_the_thirty_five_percent_floor_is_untouched(self):
        # No nudge, no per-provider table. Exactly on the floor passes; one
        # token below does not.
        n, tokens = DEFAULT_CACHE_MIN_CALLS, 1000000
        self.assertEqual(DEFAULT_CACHE_MIN_RATE, 0.35)
        exact = int(DEFAULT_CACHE_MIN_RATE * tokens)
        self.assertIsNone(cache_judgement(
            self.terms(unconfirmed=(n, tokens, exact))))
        self.assertIsNotNone(cache_judgement(
            self.terms(unconfirmed=(n, tokens, exact - 1))))

    def test_note_cache_call_books_into_exactly_one_bucket(self):
        terms = cache_terms()
        note_cache_call(terms, 100, 10, True)
        note_cache_call(terms, 200, 0, False)
        self.assertEqual(terms, {
            "identity_confirmed_calls": 1,
            "identity_confirmed_prompt_tokens": 100,
            "identity_confirmed_cached_tokens": 10,
            "identity_unconfirmed_calls": 1,
            "identity_unconfirmed_prompt_tokens": 200,
            "identity_unconfirmed_cached_tokens": 0})


# ------------------------------------- every named term reaches the exit code
class EveryTermReachesTheVerdict(unittest.TestCase):
    """This project's signature defect: a term computed and printed but absent
    from the exit code and unasserted. Each of the six is checked here."""

    def test_all_six_terms_are_named(self):
        self.assertEqual(set(CACHE_JUDGEMENT_TERMS), {
            "identity_confirmed_calls", "identity_confirmed_prompt_tokens",
            "identity_confirmed_cached_tokens", "identity_unconfirmed_calls",
            "identity_unconfirmed_prompt_tokens",
            "identity_unconfirmed_cached_tokens"})

    def test_a_missing_term_is_a_breach_not_a_clean_run(self):
        for term in CACHE_JUDGEMENT_TERMS:
            terms = cache_terms()
            del terms[term]
            self.assertEqual(cache_terms_gaps(terms), (term,), term)
            reason, detail = cache_judgement(terms)
            self.assertEqual(reason, "CACHE_TERMS_INCOMPLETE", term)
            self.assertIn(term, detail)

    def test_a_non_int_term_is_a_breach_too(self):
        for term in CACHE_JUDGEMENT_TERMS:
            for value in (None, "0", 0.0, True):
                terms = cache_terms()
                terms[term] = value
                self.assertEqual(cache_judgement(terms)[0],
                                 "CACHE_TERMS_INCOMPLETE", (term, value))

    def test_each_term_changes_something_the_verdict_or_the_line_can_see(self):
        for term in CACHE_JUDGEMENT_TERMS:
            terms = cache_terms()
            terms["identity_unconfirmed_calls"] = DEFAULT_CACHE_MIN_CALLS
            terms["identity_unconfirmed_prompt_tokens"] = 1000000
            base_verdict = cache_judgement(terms)
            base_line = cache_cost_fact(terms)
            terms[term] += 1000000
            self.assertNotEqual((base_verdict, base_line),
                                (cache_judgement(terms), cache_cost_fact(terms)),
                                "%s reaches neither the verdict nor the line" % term)

    def test_the_cost_fact_refuses_to_print_numbers_it_never_measured(self):
        terms = cache_terms()
        del terms["identity_confirmed_calls"]
        self.assertIn("NOT MEASURED", cache_cost_fact(terms))


# ---------------------------------- nothing that used to fire may stop firing
class TheNonWeakeningPaths(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = os.path.join(self.tmp, "run.upstream.jsonl")

    def audit(self, **kw):
        kw.setdefault("expected_identity", PINNED)
        return audit_ledger(self.ledger, **kw)

    def test_a_wrong_family_row_is_still_an_immediate_breach(self):
        rows = [row() for _ in range(10)]
        rows[4] = row(returned_model="deepseek-v4-pro-0813")
        write_ledger(self.ledger, rows)
        reason, detail = self.audit()
        self.assertEqual(reason, "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("row 5", detail)

    def test_a_ledger_where_no_row_ever_named_a_model_is_still_unknown(self):
        write_ledger(self.ledger, [row(returned_model=None) for _ in range(3)])
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_UNKNOWN")

    def test_the_v37_shape_still_collapses(self):
        """Billed rows that name NO model, at a low rate. The floor's whole job.

        One row names the model so the ledger is not RETURNED_MODEL_UNKNOWN —
        exactly the real v37 ledger, where 28 of 180 rows carried no usable
        attribution at all.
        """
        rows = [row(returned_model=None,
                    usage={"prompt_tokens": 1000,
                           "prompt_tokens_details": {"cached_tokens": 280}})
                for _ in range(DEFAULT_CACHE_MIN_CALLS)]
        rows.insert(0, row(usage={"prompt_tokens": 1000,
                                  "prompt_tokens_details": {"cached_tokens": 900}}))
        write_ledger(self.ledger, rows)
        reason, detail = self.audit()
        self.assertEqual(reason, "PROMPT_CACHE_COLLAPSE")
        self.assertIn("28.0%", detail)
        self.assertIn("1 identity-confirmed", detail)

    def test_a_lane_with_no_declared_identity_is_judged_exactly_as_before(self):
        # --no-identity-check means nothing confirms anything, so every billed
        # call is unconfirmed and the floor keeps full jurisdiction.
        write_ledger(self.ledger,
                     [row(usage={"prompt_tokens": 1000,
                                 "prompt_tokens_details": {"cached_tokens": 280}})
                      for _ in range(DEFAULT_CACHE_MIN_CALLS)])
        self.assertEqual(
            self.audit(expected_identity="", identity_check=False)[0],
            "PROMPT_CACHE_COLLAPSE")

    def test_a_family_mismatch_still_outranks_a_cache_collapse(self):
        write_ledger(self.ledger,
                     [row(returned_model="deepseek-v4-pro-0813",
                          usage={"prompt_tokens": 1000,
                                 "prompt_tokens_details": {"cached_tokens": 0}})
                      for _ in range(DEFAULT_CACHE_MIN_CALLS)])
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_the_disable_switch_still_works(self):
        write_ledger(self.ledger,
                     [row(returned_model=None,
                          usage={"prompt_tokens": 1000,
                                 "prompt_tokens_details": {"cached_tokens": 0}})
                      for _ in range(DEFAULT_CACHE_MIN_CALLS)]
                     + [row()])
        self.assertEqual(self.audit()[0], "PROMPT_CACHE_COLLAPSE")
        self.assertIsNone(self.audit(cache_guard=False))


# ------------------------------------------------- THE LIVE PROXY. THE POINT.
class Stub(BaseHTTPRequestHandler):
    """Plays the provider. The server object carries the script."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.server.seen += 1
        model = self.server.models[min(self.server.seen - 1,
                                       len(self.server.models) - 1)]
        payload = json.dumps({
            "model": model,
            "usage": {"prompt_tokens": self.server.prompt_tokens,
                      "prompt_tokens_details": {
                          "cached_tokens": self.server.cached_tokens}},
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TheLiveProxyGuard(unittest.TestCase):
    """The live guard is what killed the run. An auditor-only fix fixes nothing."""

    MIN_CALLS = 5

    def setUp(self):
        self.up_port = free_port()
        self.srv = HTTPServer(("127.0.0.1", self.up_port), Stub)
        self.srv.seen = 0
        self.srv.models = [V39_IDENTITY]
        self.srv.prompt_tokens = 45636          # the v39 run's first prompt
        self.srv.cached_tokens = 0              # 0 %: worse than the real 16.4 %
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
                   UPSTREAM_EXPECTED_RETURNED_IDENTITY=V39_IDENTITY,
                   UPSTREAM_CACHE_MIN_CALLS=str(self.MIN_CALLS),
                   RUN_TAG="fix7-test")
        env.update(extra)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        for _ in range(200):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port,
                        timeout=1) as response:
                    response.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def call(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": "deepseek-v4-flash-0731",
                             "messages": []}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.getcode(), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def rows(self, expect):
        """The ledger rows, once at least `expect` of them have landed."""
        rows = []
        for _ in range(200):
            if os.path.exists(self.log):
                with open(self.log, encoding="utf-8") as source:
                    rows = [json.loads(line) for line in source if line.strip()]
                if len(rows) >= expect:
                    return rows
            time.sleep(0.05)
        self.fail("only %d of %d ledger rows landed" % (len(rows), expect))

    def marker(self):
        for _ in range(150):
            if os.path.exists(self.abort):
                with open(self.abort, encoding="utf-8") as fh:
                    return json.load(fh)
            time.sleep(0.02)
        self.fail("no abort marker at %s" % self.abort)

    def test_a_zero_percent_cache_identity_confirmed_lane_is_never_403ed(self):
        """THE MISSING ASSERTION. This is the run that died, in miniature.

        0 % cache — worse than the real 16.4 % — for four times the call floor,
        every call naming the exact expected identity. Not one 403, no abort
        marker, and the provider saw every call.
        """
        self.start()
        calls = self.MIN_CALLS * 4
        for index in range(calls):
            status, body = self.call()
            self.assertEqual(status, 200, "call %d: %r" % (index + 1, body[:200]))
        self.assertFalse(os.path.exists(self.abort),
                         "the lane aborted on an identity-confirmed run")
        self.assertEqual(self.srv.seen, calls,
                         "the proxy stopped forwarding to the provider")
        rows = self.rows(calls)
        self.assertEqual(len(rows), calls)
        self.assertEqual({r["returned_model"] for r in rows}, {V39_IDENTITY})
        self.assertEqual({r["status"] for r in rows}, {200})

    def test_the_live_lane_prints_the_cost_fact_instead_of_dying(self):
        self.start()
        for _ in range(self.MIN_CALLS):
            self.assertEqual(self.call()[0], 200)
        # The ledger row, and the observation that prints the line, are written
        # AFTER the client's bytes go out — so wait for the rows, not for luck.
        self.rows(self.MIN_CALLS)
        self.proc.terminate()
        self.proc.wait(timeout=10)
        err = (self.proc.stderr.read() or b"").decode("utf-8", "replace")
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()
        self.proc = None
        self.assertIn("prompt-cache COST FACT", err)
        self.assertIn("0.0% hit rate", err)
        self.assertIn("%d identity-confirmed" % self.MIN_CALLS, err)
        self.assertIn("REAL MONEY", err)

    def test_an_unnamed_model_lane_still_aborts_live_on_the_cache_floor(self):
        """The v37 shape, live: billed 2xx rows that name no model at all."""
        self.srv.models = [V39_IDENTITY, None]     # row 1 names it, the rest do not
        self.srv.cached_tokens = 280
        self.srv.prompt_tokens = 1000
        self.start()
        for index in range(self.MIN_CALLS + 1):
            self.assertEqual(self.call()[0], 200, index)
        marker = self.marker()
        self.assertEqual(marker["reason"], "PROMPT_CACHE_COLLAPSE")
        self.assertIn("28.0%", marker["detail"])
        self.assertIn("identity was NOT directly confirmed", marker["detail"])
        self.assertEqual(self.call()[0], 403)

    def test_a_wrong_family_still_aborts_live_on_that_call(self):
        self.srv.models = [V39_IDENTITY, "deepseek/deepseek-v4-pro-0813"]
        self.start()
        self.assertEqual(self.call()[0], 200)
        self.assertEqual(self.call()[0], 200)       # the offending call relays
        marker = self.marker()
        self.assertEqual(marker["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        status, body = self.call()
        self.assertEqual(status, 403)
        self.assertIn(b"RETURNED_MODEL_FAMILY_MISMATCH", body)

    def test_the_abort_marker_still_carries_the_lane_totals(self):
        self.srv.models = [V39_IDENTITY, None]
        self.srv.cached_tokens = 0
        self.srv.prompt_tokens = 1000
        self.start()
        for _ in range(self.MIN_CALLS + 1):
            self.assertEqual(self.call()[0], 200)
        observed = self.marker()["cache_observed"]
        self.assertEqual(observed["calls"], self.MIN_CALLS + 1)
        self.assertEqual(observed["prompt_tokens"], (self.MIN_CALLS + 1) * 1000)
        self.assertEqual(observed["identity_confirmed_calls"], 1)
        self.assertEqual(observed["identity_unconfirmed_calls"], self.MIN_CALLS)

    def test_the_disable_switch_still_works_live(self):
        self.srv.models = [None]
        self.srv.cached_tokens = 0
        self.start(UPSTREAM_CACHE_GUARD="0")
        for _ in range(self.MIN_CALLS * 2):
            self.assertEqual(self.call()[0], 200)
        self.assertFalse(os.path.exists(self.abort))


if __name__ == "__main__":
    unittest.main(verbosity=2)
