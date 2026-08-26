#!/usr/bin/env python3
"""EXHAUSTING THE SUBSTITUTION CAP MUST NOT KILL THE RUN — advance the route.

WHY THIS FILE EXISTS. Measured on the 463-row v38 ledger
(`20260826T132832Z-v38.upstream.jsonl`): 183 of 463 answers (40 %) were the
wrong model, and the per-call retry histogram is a clean geometric tail — 98
calls needed 1 retry, then 46, 19, 10, 2, 1, 1, 1, 1, 1, 1, 1, and ONE call
needed 13 against a cap of 12. That single call ended a 2 h 42 m run and 23.15
CNY of work with a 192-byte report, after 280 good calls.

Raising the cap buys one order of magnitude and costs nothing until the provider
drifts again: a lottery ticket, not a fix. The fix is that exhaustion stops
being fatal — when a provider will not serve the model we asked for, CHANGE
PROVIDER, on the live run, using the hot-swappable route file.

WHAT IS NOT WEAKENED. Not one wrong-model byte reaches the client on any path,
on any route, ever; that is asserted on the client bytes in every case here.
And with no fallback list configured the end state is today's, exactly: the
lane trips as RETURNED_MODEL_FAMILY_MISMATCH, the client is refused, and no
new artifact appears.

Everything here runs against STUB upstreams. No metered tokens.

    python3 measure/tests/test_route_advance.py
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
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(MEASURE))))
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")
AUDIT = os.path.join(MEASURE, "lane-audit.py")
SWAP = os.path.join(REPO, "hack", "swap-upstream-route.sh")
sys.path.insert(0, MEASURE)
sys.path.insert(0, HERE)
from lane_guard import (ROUTE_ADVANCE_CHECKS,  # noqa: E402
                        audit_ledger)
from test_substitution_retry import Stub, free_port                # noqa: E402

# Route 1 is "linkapi": the floating alias, and the identity it is judged on.
A_MODEL = "deepseek-v4-flash-0731"
PRO = "deepseek-v4-pro"
# Route 2 is "CloseRouter": a DIFFERENT base, a DIFFERENT model id and a
# DIFFERENT expected identity, all in one write. model_family keeps the vendor
# prefix, so same_family('deepseek/deepseek-v4-flash-0731',
# 'deepseek-v4-flash-0731') is False — a new base with an old identity would
# trip the lane on call one. That is exactly why the three move as a unit.
B_MODEL = "deepseek/deepseek-v4-flash-0731"


def _route(base, model, expected, **extra):
    row = {"schema": 1, "base": base, "model": model,
           "expected_returned_identity": expected}
    row.update(extra)
    return row


class Base(unittest.TestCase):
    """Two stub providers, one route file, one fallback list."""

    def setUp(self):
        self.up = []
        for script in ([PRO], [B_MODEL]):
            port = free_port()
            srv = HTTPServer(("127.0.0.1", port), Stub)
            srv.seen = 0
            srv.script = list(script)
            srv.stream = True
            srv.anonymous_head = False
            srv.prompt_tokens = 1000
            srv.cached_tokens = 800
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.up.append(srv)
        self.a, self.b = self.up
        self.a_base = "http://127.0.0.1:%d/v1" % self.a.server_port
        self.b_base = "http://127.0.0.1:%d/v1" % self.b.server_port
        self.tmp = tempfile.mkdtemp(prefix="route-advance.")
        self.log = os.path.join(self.tmp, "trace.upstream.jsonl")
        self.abort = os.path.join(self.tmp, "trace.upstream.abort.json")
        self.route_file = os.path.join(self.tmp, "trace.upstream.route.json")
        self.advances = os.path.join(self.tmp, "trace.upstream.route-advances.jsonl")
        self.fallbacks_file = os.path.join(self.tmp, "fallbacks.json")
        self.write_route(_route(self.a_base, A_MODEL, A_MODEL, generation=0))
        self.px_port = free_port()
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream:
                    stream.close()
        for srv in self.up:
            srv.shutdown()
            srv.server_close()

    # ---------------------------------------------------------------- helpers
    def write_route(self, row):
        with open(self.route_file, "w", encoding="utf-8") as fh:
            json.dump(row, fh, sort_keys=True)
            fh.write("\n")

    def write_fallbacks(self, rows):
        with open(self.fallbacks_file, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
            fh.write("\n")

    def start(self, retries="2", fallbacks=None, expect_exit=False, **extra):
        env = dict(os.environ,
                   UPSTREAM_BASE=self.a_base, UPSTREAM_LOG=self.log,
                   LISTEN_PORT=str(self.px_port),
                   UPSTREAM_LANE_ABORT=self.abort,
                   UPSTREAM_EXPECTED_RETURNED_IDENTITY=A_MODEL,
                   UPSTREAM_SUBSTITUTION_RETRY_MAX=retries,
                   UPSTREAM_ROUTE_FILE=self.route_file,
                   UPSTREAM_ROUTE_ADVANCES=self.advances,
                   RUN_TAG="route-advance-test")
        if fallbacks is not None:
            self.write_fallbacks(fallbacks)
            env["UPSTREAM_ROUTE_FALLBACKS"] = self.fallbacks_file
        env.update(extra)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        if expect_exit:
            out, err = self.proc.communicate(timeout=30)
            self.proc = None
            return err.decode("utf-8", "replace")
        for _ in range(200):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port,
                        timeout=1) as r:
                    r.read()
                return ""
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def call(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": "deepseek-v4-flash", "messages": [],
                             "stream": True}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.getcode(), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def rows(self, expected=None):
        for _ in range(300):
            try:
                with open(self.log, encoding="utf-8") as fh:
                    rows = [json.loads(line) for line in fh if line.strip()]
            except (OSError, ValueError):
                rows = []
            if expected is None or len(rows) >= expected:
                return rows
            time.sleep(0.02)
        return rows

    def events(self, expected=None):
        for _ in range(300):
            try:
                with open(self.advances, encoding="utf-8") as fh:
                    rows = [json.loads(line) for line in fh if line.strip()]
            except (OSError, ValueError):
                rows = []
            if expected is None or len(rows) >= expected:
                return rows
            time.sleep(0.02)
        return rows

    def marker(self):
        for _ in range(300):
            if os.path.exists(self.abort):
                try:
                    with open(self.abort, encoding="utf-8") as fh:
                        return json.load(fh)
                except (OSError, ValueError):
                    pass
            time.sleep(0.02)
        self.fail("no abort marker at %s" % self.abort)

    def live_route(self):
        with open(self.route_file, encoding="utf-8") as fh:
            return json.load(fh)


class TheAdvance(Base):
    """Requirement 1: the run HEALS. A exhausts, B answers, the client is served."""

    def test_exhaustion_advances_and_the_request_completes_on_route_two(self):
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)])
        status, body = self.call()

        # THE WHOLE POINT: a 200 carrying route 2's answer, from a request that
        # would have been a dead run five minutes ago.
        self.assertEqual(status, 200)
        self.assertIn(b"answer from " + B_MODEL.encode(), body)
        # AND NOT ONE WRONG-MODEL BYTE, on either route.
        self.assertNotIn(PRO.encode(), body)
        self.assertEqual(self.a.seen, 3, "route 1 did not spend its whole cap")
        self.assertEqual(self.b.seen, 1, "the request was not re-issued on route 2")

        rows = self.rows(5)
        calls = [r for r in rows if not r.get("event")]
        self.assertEqual(len(calls), 4)
        discards = [r for r in calls if r.get("discarded_substitution")]
        self.assertEqual(len(discards), 3)
        self.assertEqual([r["substitution_attempt"] for r in discards], [1, 2, 3])
        self.assertEqual([r["substitution_retry_exhausted"] for r in discards],
                         [False, False, True])
        # BOTH GENERATIONS ARE IN THE LEDGER. That is what makes a post-hoc
        # audit able to say which model answered which part of the report.
        self.assertEqual([r["route_generation"] for r in discards], [0, 0, 0])
        self.assertEqual(calls[-1]["route_generation"], 1)
        self.assertEqual(calls[-1]["route_base"], self.b_base)
        self.assertEqual(calls[-1]["route_expected_identity"], B_MODEL)
        self.assertEqual(calls[-1]["returned_model"], B_MODEL)
        self.assertFalse(os.path.exists(self.abort), "the lane tripped anyway")

        # The live route file really moved, atomically, generation+1.
        live = self.live_route()
        self.assertEqual(live["base"], self.b_base)
        self.assertEqual(live["model"], B_MODEL)
        self.assertEqual(live["expected_returned_identity"], B_MODEL)
        self.assertEqual(live["generation"], 1)

    def test_the_advance_is_recorded_in_both_places(self):
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)])
        self.assertEqual(self.call()[0], 200)
        events = self.events(1)
        self.assertEqual([e["event"] for e in events], ["route_advance"])
        event = events[0]
        self.assertEqual(event["reason"], "SUBSTITUTION_RETRY_EXHAUSTED")
        self.assertEqual(event["exhausted_attempts"], 3)
        self.assertEqual(event["from"]["base"], self.a_base)
        self.assertEqual(event["from"]["expected_identity"], A_MODEL)
        self.assertEqual(event["from"]["generation"], 0)
        self.assertEqual(event["to"]["base"], self.b_base)
        self.assertEqual(event["to"]["model"], B_MODEL)
        self.assertEqual(event["to"]["expected_identity"], B_MODEL)
        self.assertEqual(event["to"]["generation"], 1)
        self.assertTrue(event["request_id"])
        self.assertTrue(event["observed_at"])
        # An invisible failover is a lie about the measurement: the ledger
        # carries the same event, so a reader of the ledger alone still sees it.
        ledger = [r for r in self.rows(4) if r.get("event") == "route_advance"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["to"]["base"], self.b_base)

    def test_fresh_retry_budget_on_the_new_route(self):
        """Route 2 gets its OWN cap, not route 1's leftovers."""
        self.b.script = [PRO, PRO, B_MODEL]
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)])
        status, body = self.call()
        self.assertEqual(status, 200)
        self.assertIn(b"answer from " + B_MODEL.encode(), body)
        self.assertNotIn(PRO.encode(), body)
        self.assertEqual(self.b.seen, 3)


class EveryRouteExhausts(Base):
    """Requirement 4: no fallback left ⇒ trip with the ORIGINAL reason code."""

    def test_trips_with_the_original_reason_and_keeps_the_history(self):
        self.b.script = [PRO]
        status, body = None, None
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)])
        status, body = self.call()
        self.assertEqual(status, 403)
        self.assertNotIn(PRO.encode(), body)
        marker = self.marker()
        self.assertEqual(marker["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertEqual(self.a.seen, 3)
        self.assertEqual(self.b.seen, 3)
        events = self.events(2)
        self.assertEqual([e["event"] for e in events],
                         ["route_advance", "route_advance_blocked"])
        self.assertEqual(events[1]["blocked_reason"], "FALLBACK_LIST_EXHAUSTED")
        counters = events[1]["counters"]
        self.assertEqual(counters["advances_attempted"], 2)
        self.assertEqual(counters["advances_performed"], 1)
        self.assertEqual(counters["advances_blocked"], 1)
        self.assertEqual(counters["routes_exhausted"], 2)
        self.assertEqual(counters["requests_refused"], 1)

    def test_a_route_is_never_returned_to(self):
        """Route 1 is walked past, once, and never asked again."""
        self.b.script = [PRO]
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)])
        self.assertEqual(self.call()[0], 403)
        self.events(2)
        self.assertEqual(self.a.seen, 3, "route 1 was asked again after the advance")


class TheRunLevelCap(Base):
    """Requirement: a flapping provider cannot walk the whole list per call."""

    def test_advance_max_zero_trips_without_walking(self):
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)],
                   UPSTREAM_ROUTE_ADVANCE_MAX="0")
        status, body = self.call()
        self.assertEqual(status, 403)
        self.assertNotIn(PRO.encode(), body)
        self.assertEqual(self.marker()["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertEqual(self.b.seen, 0, "the cap did not hold")
        self.assertEqual(self.live_route()["generation"], 0)
        events = self.events(1)
        self.assertEqual(events[0]["event"], "route_advance_blocked")
        self.assertEqual(events[0]["blocked_reason"], "ADVANCE_CAP_REACHED")

    def test_the_cap_is_per_run_not_per_request(self):
        """One advance allowed; the SECOND client request cannot buy another."""
        self.b.script = [PRO]
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL),
                              _route(self.b_base, B_MODEL, B_MODEL)],
                   UPSTREAM_ROUTE_ADVANCE_MAX="1")
        self.assertEqual(self.call()[0], 403)
        events = self.events(2)
        self.assertEqual([e["event"] for e in events],
                         ["route_advance", "route_advance_blocked"])
        self.assertEqual(events[1]["blocked_reason"], "ADVANCE_CAP_REACHED")


class TheWriterFails(Base):
    """A writer that cannot write is a trip, never a silent stay-put.

    The advance goes through hack/swap-upstream-route.sh - the ONE writer, with
    the atomic rename, the 0600-before-rename and the refusal to lower a
    generation. If it is missing or fails, the honest outcome is the original
    trip: pretending the route changed would judge the next call against an
    identity the file does not hold.
    """

    def test_a_failing_writer_blocks_the_advance_and_trips(self):
        broken = os.path.join(self.tmp, "broken-writer.sh")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho 'refusing to LOWER generation' >&2\nexit 1\n")
        os.chmod(broken, 0o755)
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)],
                   UPSTREAM_ROUTE_SWAP=broken)
        status, body = self.call()
        self.assertEqual(status, 403)
        self.assertNotIn(PRO.encode(), body)
        self.assertEqual(self.marker()["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertEqual(self.live_route()["generation"], 0)
        self.assertEqual(self.b.seen, 0)
        events = self.events(1)
        self.assertEqual(events[0]["event"], "route_advance_blocked")
        self.assertEqual(events[0]["blocked_reason"], "WRITER_FAILED")
        self.assertIn("writer exited 1", events[0]["writer_detail"])
        self.assertEqual(events[0]["counters"]["advances_performed"], 0)
        self.assertEqual(events[0]["counters"]["requests_refused"], 1)

    def test_a_missing_writer_blocks_the_advance_and_trips(self):
        self.start(fallbacks=[_route(self.b_base, B_MODEL, B_MODEL)],
                   UPSTREAM_ROUTE_SWAP="/nonexistent/swap-upstream-route.sh")
        self.assertEqual(self.call()[0], 403)
        events = self.events(1)
        self.assertEqual(events[0]["blocked_reason"], "WRITER_FAILED")
        self.assertIn("no executable writer", events[0]["writer_detail"])


class NoFallbacks(Base):
    """Requirement 4, the other half: absent list ⇒ today's behaviour, exactly."""

    def _exhaust(self, **extra):
        self.start(**extra)
        status, body = self.call()
        self.assertEqual(status, 403)
        self.assertNotIn(PRO.encode(), body)
        self.assertEqual(self.marker()["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertEqual(self.a.seen, 3)
        self.assertEqual(self.b.seen, 0)
        self.assertEqual(self.live_route()["generation"], 0)
        # NO NEW ARTIFACT. A run that does not use the feature must leave the
        # exact file set every previous run left.
        self.assertFalse(os.path.exists(self.advances),
                         "an unused feature wrote an artifact")
        rows = self.rows(3)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.get("event") for r in rows], [None, None, None])

    def test_absent_fallback_list_is_todays_behaviour(self):
        self._exhaust()

    def test_empty_fallback_list_is_todays_behaviour(self):
        self._exhaust(fallbacks=[])


class BadFallbackList(Base):
    """A fallback list is validated AT STARTUP, not twenty minutes in."""

    def _refuses(self, rows, needle):
        err = self.start(fallbacks=rows, expect_exit=True)
        self.assertIn(needle, err)

    def test_a_fallback_carrying_a_credential_is_refused(self):
        self._refuses([_route(self.b_base, B_MODEL, B_MODEL, api_key="sk-x")],
                      "ROUTE_CARRIES_CREDENTIAL")

    def test_a_fallback_with_no_identity_is_refused(self):
        self._refuses([{"schema": 1, "base": self.b_base, "model": B_MODEL}],
                      "ROUTE_FIELD_MISSING")

    def test_a_fallback_with_a_non_http_base_is_refused(self):
        self._refuses([_route("file:///etc/passwd", B_MODEL, B_MODEL)],
                      "ROUTE_BASE_INVALID")

    def test_a_fallback_naming_its_own_generation_is_refused(self):
        self._refuses([_route(self.b_base, B_MODEL, B_MODEL, generation=7)],
                      "FALLBACK_CARRIES_GENERATION")

    def test_a_fallback_list_that_is_not_a_list_is_refused(self):
        self._refuses(_route(self.b_base, B_MODEL, B_MODEL),
                      "must be a JSON array")

    def test_fallbacks_without_a_route_file_are_refused(self):
        self.write_fallbacks([_route(self.b_base, B_MODEL, B_MODEL)])
        env = dict(os.environ, UPSTREAM_BASE=self.a_base, UPSTREAM_LOG=self.log,
                   LISTEN_PORT=str(self.px_port),
                   UPSTREAM_ROUTE_FALLBACKS=self.fallbacks_file)
        env.pop("UPSTREAM_ROUTE_FILE", None)
        out = subprocess.run([sys.executable, PROXY], env=env,
                             capture_output=True, timeout=30)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("UPSTREAM_ROUTE_FILE", out.stderr.decode())


class TheAudit(unittest.TestCase):
    """The finished artifacts must not be able to hide a failover."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="route-advance-audit.")
        self.ledger = os.path.join(self.tmp, "trace.upstream.jsonl")
        self.advances = os.path.join(self.tmp, "trace.upstream.route-advances.jsonl")

    def write_ledger(self, rows):
        with open(self.ledger, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def write_advances(self, rows):
        with open(self.advances, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def row(self, identity, returned, generation, base="http://a/v1"):
        return {"requested_model": "alias", "returned_model": returned,
                "status": 200, "route_expected_identity": identity,
                "route_base": base, "route_generation": generation,
                "usage": {"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 900}}}

    def counters(self, **over):
        row = {"advances_attempted": 1, "advances_performed": 1,
               "advances_blocked": 0, "routes_exhausted": 1,
               "requests_refused": 0}
        row.update(over)
        return row

    def advance_event(self, **over):
        row = {"event": "route_advance", "reason": "SUBSTITUTION_RETRY_EXHAUSTED",
               "from": {"base": "http://a/v1", "model": A_MODEL,
                        "expected_identity": A_MODEL, "generation": 0},
               "to": {"base": "http://b/v1", "model": B_MODEL,
                      "expected_identity": B_MODEL, "generation": 1},
               "counters": self.counters()}
        row.update(over)
        return row

    def audit(self, **kw):
        kw.setdefault("advances_path", self.advances)
        kw.setdefault("expected_identity", "")
        return audit_ledger(self.ledger, **kw)

    # -------------------------------------------------------------- accepting
    def test_a_multi_route_ledger_is_accepted(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event()])
        summary = {}
        self.assertIsNone(self.audit(summary=summary))
        self.assertEqual(summary["route_span"], 2)
        self.assertEqual(summary["route_advances"], 1)
        self.assertEqual(summary["route_advances_blocked"], 0)
        # Every named check ran and every one of them passed - the accounting
        # the MULTI-ROUTE line prints is the accounting the verdict used.
        self.assertEqual(set(summary["route_advance_checks"]),
                         set(ROUTE_ADVANCE_CHECKS))
        self.assertEqual(sum(summary["route_advance_checks"].values()), 0)

    def test_a_substitution_on_route_two_is_still_caught(self):
        """Each route is judged on ITS OWN identity — a swap is not an amnesty."""
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, PRO, 1, "http://b/v1")])
        self.write_advances([self.advance_event()])
        breach = self.audit()
        self.assertIsNotNone(breach)
        self.assertEqual(breach[0], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_a_substitution_on_route_one_is_still_caught(self):
        self.write_ledger([self.row(A_MODEL, PRO, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.assertEqual(self.audit()[0], "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_no_advances_path_is_todays_behaviour(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0)])
        self.assertIsNone(audit_ledger(self.ledger, expected_identity=""))

    def test_a_missing_history_with_one_route_is_clean(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0)])
        self.assertIsNone(self.audit())

    # ------------------------------------------- every named counter blocks
    def test_advance_history_unreadable_blocks(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0)])
        with open(self.advances, "w", encoding="utf-8") as fh:
            fh.write("{not json\n")
        breach = self.audit()
        self.assertEqual(breach[0], "ROUTE_ADVANCE_HISTORY_UNREADABLE")

    def test_counters_not_a_dict_blocks(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event(counters="nope")])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_HISTORY_UNREADABLE")

    def test_advance_not_in_ledger_blocks(self):
        """We advanced and the ledger shows one route: the failover is invisible."""
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0)])
        self.write_advances([self.advance_event()])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_UNRECORDED")

    def test_advances_performed_mismatch_blocks(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event(
            counters=self.counters(advances_performed=2, advances_attempted=2,
                                   routes_exhausted=2))])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_COUNTERS_INCONSISTENT")

    def test_advances_attempted_lt_performed_blocks(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event(
            counters=self.counters(advances_attempted=0))])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_COUNTERS_INCONSISTENT")

    def test_routes_exhausted_lt_attempted_blocks(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event(
            counters=self.counters(routes_exhausted=0))])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_COUNTERS_INCONSISTENT")

    def test_advances_blocked_mismatch_blocks(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event(
            counters=self.counters(advances_blocked=3))])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_COUNTERS_INCONSISTENT")

    def test_requests_refused_missing_blocks(self):
        """A blocked advance means a client WAS refused. Zero refusals is a lie."""
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([
            self.advance_event(),
            self.advance_event(event="route_advance_blocked",
                               blocked_reason="FALLBACK_LIST_EXHAUSTED",
                               counters=self.counters(advances_attempted=2,
                                                      advances_blocked=1,
                                                      routes_exhausted=2,
                                                      requests_refused=0))])
        self.assertEqual(self.audit()[0], "ROUTE_ADVANCE_COUNTERS_INCONSISTENT")

    def test_a_correct_blocked_history_is_clean(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([
            self.advance_event(),
            self.advance_event(event="route_advance_blocked",
                               blocked_reason="FALLBACK_LIST_EXHAUSTED",
                               counters=self.counters(advances_attempted=2,
                                                      advances_blocked=1,
                                                      routes_exhausted=2,
                                                      requests_refused=1))])
        self.assertIsNone(self.audit())

    def test_every_named_check_is_in_the_sum(self):
        """The dict of named counters IS the verdict — no key may be decorative."""
        self.assertEqual(sorted(ROUTE_ADVANCE_CHECKS), [
            "advance_history_unreadable", "advance_not_in_ledger",
            "advances_attempted_lt_performed", "advances_blocked_mismatch",
            "advances_performed_mismatch", "requests_refused_missing",
            "routes_exhausted_lt_attempted"])

    def test_an_event_row_is_not_a_call(self):
        """The advance row lives in the ledger; it attributes no model."""
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.advance_event(returned_model=None),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event()])
        self.assertIsNone(self.audit())

    def test_an_event_row_that_names_a_model_is_malformed(self):
        """`event` must never become a way to slip a call past the check."""
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.advance_event(returned_model=PRO),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event()])
        self.assertEqual(self.audit()[0], "LEDGER_MALFORMED")

    # ------------------------------------------------------- the loud line
    def test_the_operator_cannot_miss_a_multi_route_run(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0),
                           self.row(B_MODEL, B_MODEL, 1, "http://b/v1")])
        self.write_advances([self.advance_event()])
        out = subprocess.run(
            [sys.executable, AUDIT, "--ledger", self.ledger,
             "--advances", self.advances, "--no-cache-guard",
             "--expected", A_MODEL], capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr.decode())
        err = out.stderr.decode()
        self.assertIn("MULTI-ROUTE RUN", err)
        self.assertIn("2 routes", err)
        self.assertIn(A_MODEL, err)
        self.assertIn(B_MODEL, err)

    def test_a_single_route_run_says_nothing_about_routes(self):
        self.write_ledger([self.row(A_MODEL, A_MODEL, 0)])
        out = subprocess.run(
            [sys.executable, AUDIT, "--ledger", self.ledger,
             "--no-cache-guard", "--expected", A_MODEL],
            capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr.decode())
        self.assertNotIn("MULTI-ROUTE", out.stderr.decode())


class TheWriter(unittest.TestCase):
    """The advance uses the ONE writer, not a second weaker one."""

    def test_the_default_writer_path_resolves(self):
        self.assertTrue(os.access(SWAP, os.X_OK), SWAP)

    def test_the_proxy_defaults_to_it(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,sys;"
             "spec=importlib.util.spec_from_file_location('p', %r);"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
             "print(m.UPSTREAM_ROUTE_SWAP)" % PROXY],
            capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr.decode())
        self.assertEqual(out.stdout.decode().strip(), SWAP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
