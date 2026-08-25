#!/usr/bin/env python3
"""Tests for the lane-integrity guards — the two things v37 did not notice.

The v37 full run burned 180 metered calls while linkapi quietly answered 93 of
them as `deepseek-v4-pro-0813` instead of the flash model the run had committed
to. The substitution split the provider cache pool and the prompt-cache hit
rate fell 68.1 % -> 28.0 % (fresh prompt tokens 5.92M -> 13.38M). Every gate
the harness owned said the run was fine; a human found it days later by diffing
the upstream ledger.

Two guards, tested here:
  * a returned model from a different FAMILY aborts the lane, on the call;
  * a cumulative prompt-cache rate under 50 % after 20 calls aborts the lane.
And, just as importantly, tested here: neither guard reads its own blind spot
as a pass. Absent, empty and malformed ledgers are breaches.

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
from lane_guard import (audit_ledger, cache_breach, cache_tokens,  # noqa: E402
                        model_family, same_family)

PINNED = "[SP]deepseek-v4-flash-0731"


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
        self.assertEqual(model_family("[FREE]deepseek-v4-flash-0731"), "deepseek-v4-flash")
        self.assertTrue(same_family("[SP]deepseek-v4-flash", "[FREE]deepseek-v4-flash-2026-07-31"))

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

    def test_healthy_runs_do_not_trip(self):
        for cached_pct in (68.1, 73.2, 74.0, 88.1):     # every measured healthy run
            self.assertIsNone(cache_breach(180, 1000000, int(cached_pct * 10000)),
                              cached_pct)

    def test_the_floor_is_not_reached_before_the_call_count(self):
        self.assertIsNone(cache_breach(19, 19000, 0))
        self.assertIsNotNone(cache_breach(20, 20000, 0))


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

    def test_cache_collapse_after_twenty_calls_is_a_breach(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(20)])
        reason, detail = self.audit()
        self.assertEqual(reason, "PROMPT_CACHE_COLLAPSE")
        self.assertIn("28.0%", detail)

    def test_seventy_percent_passes(self):
        warm = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 700}})
        write_ledger(self.ledger, [warm for _ in range(40)])
        self.assertIsNone(self.audit())

    def test_nineteen_cold_calls_are_not_yet_a_breach(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(19)])
        self.assertIsNone(self.audit())

    def test_the_disable_switch_works(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(50)])
        self.assertEqual(self.audit()[0], "PROMPT_CACHE_COLLAPSE")
        self.assertIsNone(self.audit(cache_guard=False))

    def test_thresholds_are_configurable(self):
        rows = [row(usage={"prompt_tokens": 1000,
                           "prompt_tokens_details": {"cached_tokens": 600}})
                for _ in range(25)]
        write_ledger(self.ledger, rows)
        self.assertIsNone(self.audit())
        self.assertEqual(self.audit(min_rate=0.9)[0], "PROMPT_CACHE_COLLAPSE")
        self.assertIsNone(self.audit(min_rate=0.9, min_calls=26))

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

    def test_missing_ledger_exits_one(self):
        done = self.run_audit()
        self.assertEqual((done.returncode, done.stdout.strip()), (1, "LEDGER_MISSING"))

    def test_disable_switch_on_the_cli(self):
        cold = row(usage={"prompt_tokens": 1000,
                          "prompt_tokens_details": {"cached_tokens": 280}})
        write_ledger(self.ledger, [cold for _ in range(25)])
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
        self.assertEqual(status, 503)
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
        self.assertEqual(self.call()[0], 503)

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
