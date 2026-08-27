#!/usr/bin/env python3
"""An output budget the provider cannot deliver inside its own generation
window is not a budget — it is a guaranteed failure waiting for a long turn.

Run 20260827T005241Z-v39 (r3) is why this file exists. 152 upstream calls, zero
substitutions, 39.8 % cache, $0.101752, `complete: true` — and ten failed calls,
NINE of which died at 90,341-90,416 ms. A 75-millisecond spread across nine
independent calls is not jitter; it is a hard ceiling. Each one is an HTTP 200
carrying a gateway error chunk (`upstream_error_in_200:upstream_error`,
`finish_reason: error`); r2 caught the payload verbatim:

    {"error":{"code":"upstream_error","message":"upstream_timeout","status":502,
     "metadata":{"provider_name":"Deepseek"}}}

That is CloseRouter's own 90-second upstream generation timeout. qwen surfaces
it as `[API Error: ... Request timeout after 90s]`, whose "increase
contentGenerator.timeout" hint is a hard-coded string and not a config read, so
raising a client timeout cannot help.

Measured on r3's own 142 good calls (recomputed from the ledger for this fix):
142,661 completion tokens over 2,073,964 ms of wall clock, of which 909,873 ms
was time-to-first-token. 1,164,091 ms of actual generation => **122.55 tok/s**,
avg TTFT 6.4 s, largest completion actually returned 8,497 tokens. Against a
90 s ceiling that covers TTFT *plus* generation, a 32,768-token budget is five
times more than the lane can ever deliver.

So the budget is DERIVED, not chosen: floor((window - ttft_reserve) x tok/s),
with a 35 s reserve — the largest TTFT this project has ever recorded, because
the average is not what kills a run.

Four things are tested here.
  * The real r3 ledger: the nine ~90.4 s rows classify as
    GENERATION_WINDOW_EXCEEDED, get their own line in the cost report, and are
    NOT counted as clean accepted answers. The tenth failure (32,354 ms) is a
    different animal and must stay one.
  * The launch check: an impossible config aborts naming all four numbers and
    the value that would fit; a fitting one launches; a lane that declares NO
    window skips the check entirely, so linkapi and the free lanes are untouched.
  * The proxy names the cut in the ledger and does NOT spend burst retries on it.
  * Every named counter reaches the verdict or the printed line — this
    project's signature defect is a term computed and printed but absent from
    the exit code and unasserted by any test.
"""
import json
import os
import shutil
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
SHERLOCK = os.path.dirname(MEASURE)
AUDIT = os.path.join(MEASURE, "lane-audit.py")
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")
FIXTURE = os.path.join(HERE, "fixtures", "r3-generation-window.upstream.jsonl")
IDENTITY = "deepseek/deepseek-v4-flash-0731"

sys.path.insert(0, MEASURE)
from lane_guard import (ACCOUNTING_TERMS, GENERATION_WINDOW_EXCEEDED,  # noqa: E402
                        GENERATION_WINDOW_TTFT_RESERVE_S,
                        GENERATION_WINDOW_TOKENS_PER_S,
                        deterministic_refusal, fitting_max_output_tokens,
                        generation_window_exceeded, generation_window_refusal)

# The provider's measured window and throughput. Both are declared per lane;
# these are CloseRouter's, and they are what the r3 fixture was recorded under.
CR_WINDOW_S = 90
CR_TOKENS_PER_S = 122.6


def fixture_rows():
    with open(FIXTURE, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


class TheArithmeticIsTheBudget(unittest.TestCase):
    """No taste, no round numbers: (window - reserve) x throughput."""

    def test_the_reserve_is_the_largest_ttft_this_project_has_recorded(self):
        """35 s, not the 6.4 s average — the average is not what kills a run."""
        self.assertEqual(GENERATION_WINDOW_TTFT_RESERVE_S, 35)

    def test_the_declared_throughput_matches_the_r3_measurement(self):
        self.assertAlmostEqual(GENERATION_WINDOW_TOKENS_PER_S, 122.6, places=1)

    def test_the_fitting_budget_is_floor_window_minus_reserve_times_rate(self):
        # (90 - 35) x 122.6 = 6743.0
        self.assertEqual(
            fitting_max_output_tokens(CR_WINDOW_S, CR_TOKENS_PER_S, 35), 6743)

    def test_a_lane_with_no_window_has_no_derived_budget(self):
        for window in (None, "", 0, -1, "-1", "nonsense"):
            with self.subTest(window=window):
                self.assertEqual(
                    fitting_max_output_tokens(window, CR_TOKENS_PER_S, 35), 0)

    def test_the_r3_configuration_is_refused_and_names_every_number(self):
        why = generation_window_refusal(32768, CR_TOKENS_PER_S, 35, CR_WINDOW_S)
        self.assertIsNotNone(why, "32,768 tokens cannot fit a 90 s window")
        for number in ("32768", "122.6", "35", "90", "6743"):
            self.assertIn(number, why, "the refusal never names %s: %s" % (number, why))

    def test_the_derived_budget_is_accepted(self):
        self.assertIsNone(
            generation_window_refusal(6743, CR_TOKENS_PER_S, 35, CR_WINDOW_S))
        self.assertIsNone(
            generation_window_refusal(6700, CR_TOKENS_PER_S, 35, CR_WINDOW_S))

    def test_one_token_over_the_derived_budget_is_refused(self):
        self.assertIsNotNone(
            generation_window_refusal(6744, CR_TOKENS_PER_S, 35, CR_WINDOW_S))

    def test_an_undeclared_window_can_never_refuse_a_launch(self):
        """linkapi and the free lanes must behave exactly as they do today."""
        for window in (None, "", 0, -1, "-1", "nonsense"):
            with self.subTest(window=window):
                self.assertIsNone(
                    generation_window_refusal(32768, CR_TOKENS_PER_S, 35, window),
                    "an undeclared window refused a launch")
                self.assertIsNone(
                    generation_window_refusal(1000000, CR_TOKENS_PER_S, 35, window))

    def test_an_unusable_throughput_cannot_silently_pass_a_declared_window(self):
        """A declared window with no throughput cannot be checked, and
        unmeasured is not safe: it refuses rather than waving the launch through."""
        for rate in (None, "", 0, -1, "nonsense"):
            with self.subTest(rate=rate):
                self.assertIsNotNone(
                    generation_window_refusal(32768, rate, 35, CR_WINDOW_S))


class TheRowClassifierReadsTheProvidersClock(unittest.TestCase):

    def test_the_nine_r3_rows_are_named(self):
        named = [r for r in fixture_rows()
                 if generation_window_exceeded(r.get("upstream_error"),
                                               r.get("duration_ms"), CR_WINDOW_S)]
        self.assertEqual(len(named), 9)
        for row in named:
            self.assertGreaterEqual(row["duration_ms"], 90341)
            self.assertLessEqual(row["duration_ms"], 90416)

    def test_the_tenth_failure_is_not_a_clock_cut(self):
        """32,354 ms carries the SAME gateway error shape and is NOT the window.
        Matching on the message alone would mislabel it."""
        other = [r for r in fixture_rows() if r.get("duration_ms") == 32354]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["upstream_error"],
                         "upstream_error_in_200:upstream_error")
        self.assertFalse(generation_window_exceeded(
            other[0]["upstream_error"], other[0]["duration_ms"], CR_WINDOW_S))

    def test_no_good_row_is_ever_named(self):
        for row in fixture_rows():
            if row.get("finish_reason") != "error":
                self.assertFalse(generation_window_exceeded(
                    row.get("upstream_error"), row.get("duration_ms"), CR_WINDOW_S),
                    row.get("request_id"))

    def test_an_undeclared_window_names_nothing(self):
        for window in (None, "", 0, -1):
            with self.subTest(window=window):
                self.assertEqual(
                    [r for r in fixture_rows()
                     if generation_window_exceeded(r.get("upstream_error"),
                                                   r.get("duration_ms"), window)],
                    [])

    def test_a_clean_row_with_no_error_is_never_named_however_long(self):
        self.assertFalse(generation_window_exceeded(None, 900000, CR_WINDOW_S))
        self.assertFalse(generation_window_exceeded("", 900000, CR_WINDOW_S))

    def test_the_named_class_joins_the_deterministic_refusals(self):
        """It must NOT be retried as a transient burst: it is deterministic for
        a long turn, so it goes through the same door as fix 4's two 400s."""
        self.assertEqual(
            deterministic_refusal(200, "upstream_error_in_200:upstream_error",
                                  duration_ms=90377,
                                  generation_window_s=CR_WINDOW_S),
            GENERATION_WINDOW_EXCEEDED)
        self.assertEqual(
            deterministic_refusal(502, "upstream_timeout", duration_ms=90377,
                                  generation_window_s=CR_WINDOW_S),
            GENERATION_WINDOW_EXCEEDED)

    def test_fix_4s_two_refusals_still_answer_with_no_window_declared(self):
        self.assertEqual(
            deterministic_refusal(400, "the output token limit was exhausted by "
                                       "model reasoning before an answer"),
            "OUTPUT_BUDGET_EXHAUSTED_BY_REASONING")
        self.assertEqual(
            deterministic_refusal(400, "The `reasoning_content` in the thinking "
                                       "mode must be passed back to the API."),
            "REASONING_CONTENT_NOT_RELAYED")
        self.assertIsNone(deterministic_refusal(200, "upstream_error", 90377))


class TheCostLineShowsTheWastedCalls(unittest.TestCase):
    """These calls are BILLED — HTTP 200, tokens generated and thrown away."""

    def audit(self, *extra):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        out = os.path.join(d, "summary.json")
        p = subprocess.run(["python3", AUDIT, "--ledger", FIXTURE,
                            "--expected", IDENTITY, "--no-cache-guard",
                            "--summary-json", out, *extra],
                           capture_output=True, text=True, timeout=120)
        with open(out, encoding="utf-8") as fh:
            return p, json.load(fh)

    def test_the_nine_are_their_own_category_and_not_accepted_answers(self):
        p, summary = self.audit("--generation-window-s", str(CR_WINDOW_S))
        self.assertTrue(summary["complete"], summary.get("incomplete_reason"))
        self.assertEqual(summary["generation_window_exceeded_calls"], 9)
        self.assertEqual(summary["generation_window_s"], CR_WINDOW_S)
        # 16 rows: 9 clock cuts, 1 other failure, 6 clean answers. The clock
        # cuts are neither substitutions nor answers.
        self.assertEqual(summary["call_rows"], 16)
        self.assertEqual(summary["billed_calls"], 16)
        self.assertEqual(summary["discarded_substitutions"], 0)
        self.assertEqual(summary["accepted_rows"], 7)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_the_wasted_wall_clock_is_summed(self):
        _p, summary = self.audit("--generation-window-s", str(CR_WINDOW_S))
        expect = sum(r["duration_ms"] for r in fixture_rows()
                     if r.get("duration_ms", 0) >= 90341)
        self.assertEqual(summary["generation_window_exceeded_ms"], expect)
        self.assertEqual(expect, 813404)

    def test_each_cut_call_records_its_duration_and_its_max_tokens(self):
        """"I was cut by the provider's clock" must be answerable from the
        run's own artifacts, with the request budget that caused it."""
        _p, summary = self.audit("--generation-window-s", str(CR_WINDOW_S))
        detail = summary["generation_window_exceeded_detail"]
        self.assertEqual(len(detail), 9)
        for row in detail:
            self.assertEqual(set(row), {"duration_ms", "request_max_tokens"})
            self.assertGreaterEqual(row["duration_ms"], 90341)
            self.assertGreater(row["request_max_tokens"], 0)
        self.assertEqual(sorted({r["request_max_tokens"] for r in detail}),
                         [20000, 22920, 24082])

    def test_the_cost_line_names_them(self):
        p, _summary = self.audit("--generation-window-s", str(CR_WINDOW_S))
        self.assertIn("lane cost:", p.stderr)
        self.assertIn("generation window", p.stderr)
        self.assertIn("9", p.stderr)
        self.assertIn("90", p.stderr)

    def test_an_undeclared_window_leaves_the_accounting_exactly_as_today(self):
        p, summary = self.audit()
        self.assertTrue(summary["complete"], summary.get("incomplete_reason"))
        self.assertEqual(summary["generation_window_exceeded_calls"], 0)
        self.assertEqual(summary["generation_window_exceeded_ms"], 0)
        self.assertEqual(summary["generation_window_exceeded_detail"], [])
        self.assertEqual(summary["generation_window_s"], 0)
        self.assertEqual(summary["accepted_rows"], 16)
        self.assertNotIn("generation window", p.stderr)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_every_new_term_is_named_and_reaches_the_exit_code(self):
        """A term computed and printed but absent from the exit code is this
        project's signature defect. These live in ACCOUNTING_TERMS, so
        `_accounting_gaps` turns a missing one into LANE_ACCOUNTING_INCOMPLETE
        and a non-zero exit."""
        for term in ("generation_window_exceeded_calls",
                     "generation_window_exceeded_ms", "generation_window_s"):
            self.assertIn(term, ACCOUNTING_TERMS)

    def test_a_missing_term_makes_the_run_dirty_not_quietly_clean(self):
        from lane_guard import _accounting_gaps, _accounting_zero
        summary = _accounting_zero()
        del summary["generation_window_exceeded_calls"]
        self.assertIn("generation_window_exceeded_calls", _accounting_gaps(summary))


class Clock(BaseHTTPRequestHandler):
    """A provider that answers only after `self.server.delay_s`."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.hits += 1
        time.sleep(self.server.delay_s)
        if self.server.mode == "gateway_502":
            payload = json.dumps({"error": {"code": "upstream_error",
                                            "message": "upstream_timeout",
                                            "status": 502,
                                            "metadata": {"provider_name": "Deepseek"}}}
                                 ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        # The shape r3 actually recorded: HTTP 200, SSE, error chunk.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: ' + json.dumps(
            {"model": "DeepSeek-V4-Flash",
             "choices": [{"delta": {"content": "thinking"}}]}).encode() + b"\n\n")
        self.wfile.flush()
        self.wfile.write(b'data: ' + json.dumps(
            {"error": {"code": "upstream_error", "message": "upstream_timeout",
                       "status": 502}}).encode() + b"\n\n")
        self.wfile.flush()


class TheProxyNamesTheCutAndSpendsNoRetriesOnIt(unittest.TestCase):
    """Retrying a deterministic clock cut twelve times cannot help."""

    WINDOW_S = 0.4        # the real lane declares 90; a test cannot wait 90 s

    def free_port(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(self, mode, delay_s, window_s, retries=3):
        up = self.free_port()
        self.srv = HTTPServer(("127.0.0.1", up), Clock)
        self.srv.hits = 0
        self.srv.mode = mode
        self.srv.delay_s = delay_s
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.log = os.path.join(d, "upstream.jsonl")
        port = self.free_port()
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % up,
                   UPSTREAM_LOG=self.log, LISTEN_PORT=str(port),
                   UPSTREAM_RETRY_MAX=str(retries),
                   UPSTREAM_RETRY_BASE_MS="10")
        if window_s is not None:
            env["UPSTREAM_GENERATION_WINDOW_S"] = str(window_s)
        proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        for _ in range(200):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % port, timeout=1) as r:
                    r.read()
                break
            except Exception:
                time.sleep(0.05)
        else:
            self.fail("proxy never came up")
        return port

    def call(self, port):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % port,
            data=json.dumps({"model": "m", "stream": True,
                             "messages": [{"role": "user", "content": "hi"}]}
                            ).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
        except urllib.error.HTTPError as e:
            e.read()

    def rows(self):
        for _ in range(100):
            if os.path.exists(self.log):
                with open(self.log, encoding="utf-8") as fh:
                    got = [json.loads(l) for l in fh if l.strip()]
                calls = [r for r in got if not r.get("event")]
                if calls:
                    return calls
            time.sleep(0.05)
        return []

    def test_a_gateway_error_at_the_window_is_named_and_not_retried(self):
        port = self.start("gateway_502", self.WINDOW_S + 0.15, self.WINDOW_S)
        self.call(port)
        rows = self.rows()
        self.assertEqual(len(rows), 1, "the clock cut was retried: %s" % rows)
        self.assertEqual(rows[0]["upstream_refusal_class"],
                         GENERATION_WINDOW_EXCEEDED)
        self.assertEqual(rows[0]["attempt"], 1)
        self.assertEqual(self.srv.hits, 1,
                         "the provider was asked again for a deterministic cut")

    def test_the_same_error_well_inside_the_window_is_still_a_burst(self):
        """The control: fix 4's burst path must keep working. Only the CLOCK
        distinguishes these two, and nothing else may change."""
        port = self.start("gateway_502", 0.0, self.WINDOW_S)
        self.call(port)
        rows = self.rows()
        self.assertGreater(len(rows), 1, "a fast 502 stopped being retried")
        for row in rows:
            self.assertNotIn("upstream_refusal_class", row)

    def test_the_http_200_error_chunk_shape_is_named_too(self):
        """What r3 actually recorded: status 200, error spliced into the SSE."""
        port = self.start("sse_error", self.WINDOW_S + 0.15, self.WINDOW_S)
        self.call(port)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], 200)
        self.assertEqual(rows[0]["upstream_error"],
                         "upstream_error_in_200:upstream_error")
        self.assertEqual(rows[0]["upstream_refusal_class"],
                         GENERATION_WINDOW_EXCEEDED)

    def test_with_no_declared_window_the_ledger_keeps_its_old_shape(self):
        """No lane that declares no window may see a new field appear."""
        port = self.start("sse_error", self.WINDOW_S + 0.15, None, retries=0)
        self.call(port)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("upstream_refusal_class", rows[0])


if __name__ == "__main__":
    unittest.main()
