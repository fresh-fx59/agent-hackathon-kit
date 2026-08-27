#!/usr/bin/env python3
"""Every budget that can end a paid run must be DECLARED, RECORDED and NAMED.

Run 20260826T224846Z-v39 (r2) is the reason this file exists. 153 upstream
calls, 147 identity-confirmed, 0 substitutions, 42.3 % cache, $0.085443, and
`work/checkpoint.json` at `{"state": "ready_for_synthesis", "resolved": 262,
"total": 262}` — one step from the report. It was then thrown away and the
harness reported the wrong cause:

  * `out-attempt-0.json` row 118 of 432 is a `result` row with
    `error.message: "MAX_TURNS"`, `num_turns: 0` — and it is the terminal row
    of a SUBAGENT (rows 114-117 all carry
    `parent_tool_use_id: call_00_ZQ5JwwwiUGOcRZfUprWq2806`, row 119 is the
    parent's `tool_result` for that same call). The run's OWN outcome is row
    431: `subtype: success`, `is_error: false`, `num_turns: 36`.
  * `broken_session()` took the FIRST `result` row, so it read a subagent's
    fork-cap termination as the run's outcome and printed `broken_stream`
    twice. Nothing was broken.

So two things are tested here. The budgets the launcher can choose must be
chosen, passed and written down; and the classifier must read the RUN's result
row and give a budget overrun its own name.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(MEASURE)
RUNNER = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
CLASSIFY = os.path.join(SHERLOCK, "eval", "bench", "classify-attempt.py")

# The budgets the runner must declare, and the run-inputs.json key each lands in.
DECLARED = {
    "--max-session-turns": "max_session_turns",
    "--max-wall-time": "max_wall_time_seconds",
    "--max-tool-calls": "max_tool_calls",
}
# Recorded but NOT a CLI flag: qwen-code 0.22.0 reads it from the environment
# (workflow-2FCMBTBZ.js: WORKFLOW_SUBAGENT_MAX_TURNS_ENV).
ENV_BUDGETS = {"QWEN_CODE_WORKFLOW_AGENT_MAX_TURNS": "workflow_agent_max_turns"}

STUB = r"""#!/usr/bin/env bash
# Records argv, then answers in run-bench.sh's --output-format json shape.
# It also honours the flag preflight: an unknown-argument sentinel must make it
# behave like the real yargs-strict binary.
for a in "$@"; do
  case "$a" in --sherlock-flag-probe-sentinel)
    echo "Unknown arguments: sherlock-flag-probe-sentinel, sherlockFlagProbeSentinel" >&2
    for r in ${QWEN_STUB_REJECT_FLAGS:-}; do
      echo "Unknown arguments: ${r#--}" >&2
    done
    exit 1 ;;
  esac
done
printf '%s\0' "$@" >> "$QWEN_STUB_LOG"
if [ -n "${QWEN_STUB_REPORT:-}" ]; then
  mkdir -p work && printf '%s' "$QWEN_STUB_REPORT" > work/report.md
fi
python3 -c '
import json
print(json.dumps([
  {"type": "system", "session_id": "0123456789abcdef0123456789abcdef"},
  {"type": "result", "session_id": "0123456789abcdef0123456789abcdef",
   "result": "apps/api.log:1 something broke",
   "num_turns": 2, "usage": {"input_tokens": 11, "output_tokens": 22}}]))
'
exit 0
"""


class StubProvider:
    def __init__(self):
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                out = json.dumps({"model": "DeepSeek-V4-Flash",
                                  "choices": [{"message": {"content": "ok"}}]}
                                 ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Rig:
    def go(self, extra_env=None, arm="none"):
        prov = StubProvider()
        self.addCleanup(prov.close)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus = os.path.join(d, "corpus", "apps")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "api.log"), "w", encoding="utf-8") as fh:
            fh.write("one\ntwo\n")
        binp = os.path.join(d, "bin")
        os.makedirs(binp)
        qwen = os.path.join(binp, "qwen")
        with open(qwen, "w", encoding="utf-8") as fh:
            fh.write(STUB)
        os.chmod(qwen, os.stat(qwen).st_mode | stat.S_IEXEC)
        self._dir = d
        self._stub_log = os.path.join(d, "stub.log")
        env = dict(os.environ)
        env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                    "QWEN_STUB_LOG": self._stub_log, "QWEN_BIN": qwen,
                    "SHERLOCK_CORPUS": os.path.join(d, "corpus"),
                    "SHERLOCK_BASE_URL": prov.url,
                    "SHERLOCK_API_KEY": "stub-key",
                    "BENCH_LEDGER": os.path.join(d, "runs-bench.jsonl"),
                    "BENCH_RUNS": os.path.join(d, "runs")})
        env.update(extra_env or {})
        p = subprocess.run(["bash", RUNNER, arm], capture_output=True,
                           text=True, env=env, timeout=180)
        argv = []
        if os.path.exists(self._stub_log):
            with open(self._stub_log, "rb") as fh:
                argv = fh.read().decode("utf-8").split("\0")
        return argv, p

    def trace(self):
        runs = os.path.join(self._dir, "runs")
        found = [os.path.join(runs, n) for n in os.listdir(runs)
                 if os.path.isdir(os.path.join(runs, n)) and not n.endswith(".bodies")]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def inputs(self):
        with open(os.path.join(self.trace(), "run-inputs.json"), encoding="utf-8") as fh:
            return json.load(fh)


class TheRunnerDeclaresItsBudgets(Rig, unittest.TestCase):
    def test_every_budget_flag_reaches_the_qwen_command_line(self):
        argv, p = self.go()
        for flag in DECLARED:
            self.assertIn(flag, argv, "%s missing; stderr: %s" % (flag, p.stderr[-1200:]))
            self.assertTrue(argv[argv.index(flag) + 1].strip(),
                            "%s was passed with an empty value" % flag)

    def test_the_subagent_turn_cap_is_exported(self):
        _argv, p = self.go()
        self.assertIn("QWEN_CODE_WORKFLOW_AGENT_MAX_TURNS", p.stdout + p.stderr,
                      "the subagent cap is never named in the run log")

    def test_run_inputs_carries_every_declared_budget_as_a_number(self):
        """Every key the runner writes must be numeric AND asserted here — a
        budget that is recorded but unasserted is the same defect as one that
        is computed and never reaches the verdict."""
        self.go()
        budgets = self.inputs().get("budgets")
        self.assertIsInstance(budgets, dict, "run-inputs.json has no budgets object")
        self.assertEqual(set(budgets),
                         set(DECLARED.values()) | set(ENV_BUDGETS.values())
                         | {"outer_timeout_seconds", "fork_subagent_max_turns"})
        for key, value in budgets.items():
            self.assertIsInstance(value, int, "%s is not numeric" % key)
            self.assertGreater(value, 0, key)
        # `timeout` SIGKILLs qwen; qwen's own budget must fire first.
        self.assertLess(budgets["max_wall_time_seconds"],
                        budgets["outer_timeout_seconds"])
        # Not ours to choose, still ours to record: qwen-code 0.22.0 hard-codes
        # FORK_DEFAULT_MAX_TURNS = 200 (chunks/chunk-WZDM44SB.js) and that is
        # what terminated a subagent on run 20260826T224846Z-v39.
        self.assertEqual(budgets["fork_subagent_max_turns"], 200)

    def test_the_recorded_budgets_are_the_ones_on_the_command_line(self):
        argv, _p = self.go()
        budgets = self.inputs()["budgets"]
        self.assertEqual(str(budgets["max_session_turns"]),
                         argv[argv.index("--max-session-turns") + 1])
        self.assertEqual(str(budgets["max_tool_calls"]),
                         argv[argv.index("--max-tool-calls") + 1])
        # The wall budget is recorded in seconds and sent with an explicit unit.
        self.assertEqual("%ss" % budgets["max_wall_time_seconds"],
                         argv[argv.index("--max-wall-time") + 1])

    def test_the_trace_keeps_how_every_attempt_was_judged(self):
        """`.resume-reason` is overwritten each loop. Without this file the
        claim "the harness reported the wrong cause" is unfalsifiable after the
        run — which is exactly the position r2 left us in."""
        self.go()
        path = os.path.join(self.trace(), "classifications.jsonl")
        self.assertTrue(os.path.exists(path), "no classifications.jsonl in the trace")
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("reason", row)
            self.assertIn("resumable", row)
            self.assertIn("run_result_row_index", row)

    def test_the_flag_preflight_leaves_nothing_behind(self):
        """The preflight launches the real binary. Launched in the repo, a
        binary writes in the repo: the first cut of this left a `work/` tree of
        stub output inside the checkout."""
        before = sorted(os.listdir(SHERLOCK))
        self.go()
        self.assertFalse(os.path.exists(os.path.join(SHERLOCK, "work")),
                         "the preflight wrote work/ into the checkout")
        self.assertEqual(sorted(os.listdir(SHERLOCK)), before)

    def test_the_wall_budget_stays_inside_the_outer_timeout(self):
        """qwen must abort itself (exit 55, session on disk) before `timeout`
        SIGKILLs it and the session is lost."""
        _argv, _p = self.go({"SHERLOCK_TIMEOUT": "1200"})
        self.assertLess(self.inputs()["budgets"]["max_wall_time_seconds"], 1200)

    def test_dropping_a_budget_fails_the_run(self):
        for var in ("SHERLOCK_MAX_SESSION_TURNS", "SHERLOCK_MAX_TOOL_CALLS",
                    "SHERLOCK_MAX_WALL_TIME_S", "SHERLOCK_WORKFLOW_AGENT_MAX_TURNS"):
            with self.subTest(var=var):
                _argv, p = self.go({var: ""})
                self.assertNotEqual(p.returncode, 0, "%s empty was accepted" % var)
                self.assertIn(var, p.stderr)

    def test_a_non_numeric_budget_fails_the_run(self):
        _argv, p = self.go({"SHERLOCK_MAX_TOOL_CALLS": "lots"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("SHERLOCK_MAX_TOOL_CALLS", p.stderr)

    def test_a_flag_the_binary_rejects_stops_the_run_before_it_costs_money(self):
        """A flag qwen does not accept must fail LOUDLY at startup — never be
        silently dropped, and never be discovered by a paid run dying instantly."""
        _argv, p = self.go({"QWEN_STUB_REJECT_FLAGS": "--max-tool-calls"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--max-tool-calls", p.stderr)
        self.assertIn("does not accept", p.stderr)


class TheInstalledQwenAcceptsEveryBudgetFlag(unittest.TestCase):
    """The bundled docs are not the authority; the binary is.

    Probed with a guaranteed-unknown sentinel so yargs fails at PARSE time and
    no model call is ever made: the flag under test is accepted iff it is
    absent from the `Unknown arguments:` line.
    """
    QWEN = os.environ.get("QWEN_BIN") or os.path.expanduser("~/.local/bin/qwen")

    def setUp(self):
        if not os.path.exists(self.QWEN):
            self.skipTest("no installed qwen at %s" % self.QWEN)

    def probe(self, *args):
        return subprocess.run([self.QWEN, *args, "--sherlock-flag-probe-sentinel", "1",
                               "-p", "x"], capture_output=True, text=True, timeout=120)

    def test_the_sentinel_itself_is_reported_unknown(self):
        p = self.probe()
        self.assertIn("sherlock-flag-probe-sentinel", p.stderr,
                      "the probe cannot detect an unknown flag: %s" % p.stderr[:400])

    def test_each_budget_flag_is_known(self):
        for flag, value in (("--max-session-turns", "5"),
                            ("--max-wall-time", "10m"),
                            ("--max-tool-calls", "5")):
            with self.subTest(flag=flag):
                p = self.probe(flag, value)
                unknown = [l for l in p.stderr.splitlines() if "Unknown argument" in l]
                self.assertTrue(unknown, p.stderr[:400])
                self.assertNotIn(flag.lstrip("-"), " ".join(unknown),
                                 "installed qwen rejects %s" % flag)


class TheClassifierNamesTheBudget(unittest.TestCase):
    """`broken_stream` is what two runs were called when nothing was broken."""

    def run_classifier(self, rows, exit_code=0, stderr="", report=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        out = os.path.join(d, "out.json")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(rows if isinstance(rows, str) else json.dumps(rows))
        err = os.path.join(d, "err.txt")
        with open(err, "w", encoding="utf-8") as fh:
            fh.write(stderr)
        rep = os.path.join(d, "report.md")
        if report is not None:
            with open(rep, "w", encoding="utf-8") as fh:
                fh.write(report)
        reason = os.path.join(d, "reason")
        signals = os.path.join(d, "signals.json")
        p = subprocess.run(["python3", CLASSIFY, out, rep, reason, str(exit_code),
                            err, signals], capture_output=True, text=True, timeout=60)
        got_reason = ""
        if os.path.exists(reason):
            with open(reason, encoding="utf-8") as fh:
                got_reason = fh.read().strip()
        got_signals = {}
        if os.path.exists(signals):
            with open(signals, encoding="utf-8") as fh:
                got_signals = json.load(fh)
        return p, got_reason, got_signals

    SID = "0123456789abcdef0123456789abcdef"

    def result(self, **kw):
        row = {"type": "result", "session_id": self.SID, "is_error": False,
               "subtype": "success", "num_turns": 3, "result": "done"}
        row.update(kw)
        return row

    def test_a_turn_overrun_is_not_called_broken_stream(self):
        p, reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID},
             self.result(is_error=True, subtype="error_during_execution",
                         error={"message": "MAX_TURNS"}, num_turns=0)])
        self.assertEqual(reason, "budget_exhausted_turns")
        self.assertNotEqual(reason, "broken_stream")
        self.assertEqual(p.returncode, 0, "a budget overrun stays resumable")
        self.assertEqual(p.stdout.strip(), self.SID)

    def test_exit_53_is_the_turn_budget(self):
        p, reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID}, self.result()],
            exit_code=53,
            stderr="Reached max session turns for this session.")
        self.assertEqual(reason, "budget_exhausted_turns")
        self.assertEqual(p.returncode, 0)

    def test_exit_55_names_the_wall_clock_budget(self):
        _p, reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID}, self.result()],
            exit_code=55,
            stderr="Run aborted: wall-clock budget of 5100s exceeded (--max-wall-time).")
        self.assertEqual(reason, "budget_exhausted_walltime")

    def test_exit_55_names_the_tool_call_budget(self):
        _p, reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID}, self.result()],
            exit_code=55,
            stderr="Run aborted: tool-call budget of 800 exceeded (--max-tool-calls); observed 801.")
        self.assertEqual(reason, "budget_exhausted_tool_calls")

    def test_exit_55_without_a_named_flag_is_still_a_budget_not_a_broken_stream(self):
        """qwen only emits exit 55 for a budget. If its message does not name
        which one, the reason must still say `budget`, never `broken_stream`."""
        _p, reason, signals = self.run_classifier(
            [{"type": "system", "session_id": self.SID}, self.result()],
            exit_code=55, stderr="Run aborted.")
        self.assertEqual(reason, "budget_exhausted_unspecified")
        self.assertEqual(signals["budget_kind"], "unspecified")

    def test_a_subagent_result_row_is_not_the_runs_outcome(self):
        """The r2 shape, exactly: a subagent's MAX_TURNS row mid-transcript,
        the run's own success row last, and no report on disk."""
        rows = [{"type": "system", "session_id": self.SID},
                {"type": "assistant", "session_id": self.SID,
                 "parent_tool_use_id": "call_00_ZQ5JwwwiUGOcRZfUprWq2806"},
                {"type": "result", "session_id": self.SID, "is_error": True,
                 "subtype": "error_during_execution", "num_turns": 0,
                 "error": {"message": "MAX_TURNS"}},
                {"type": "user", "session_id": self.SID, "parent_tool_use_id": None},
                self.result(num_turns=36)]
        p, reason, signals = self.run_classifier(rows)
        self.assertEqual(reason, "no_deliverable",
                         "a subagent's fork cap must not be read as the run's outcome")
        self.assertEqual(signals["subagent_result_rows"], 1)
        self.assertEqual(signals["result_rows_total"], 2)
        self.assertEqual(p.returncode, 0)

    def test_a_real_api_error_is_still_broken_stream(self):
        _p, reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID},
             self.result(result="[API Error: 503 No available upstream endpoint]",
                         is_error=True)])
        self.assertEqual(reason, "broken_stream")

    def test_unparseable_output_is_still_broken_stream(self):
        p, reason, _s = self.run_classifier(
            '[{"type":"system","session_id":"%s"},{"type":"resu' % self.SID)
        self.assertEqual(reason, "broken_stream")
        self.assertEqual(p.stdout.strip(), self.SID)

    def test_a_delivered_report_is_not_resumable_even_at_the_budget_edge(self):
        p, _reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID}, self.result()],
            exit_code=53, stderr="Reached max session turns",
            report="# Отчёт\n\napps/api.log:1 ok\n")
        self.assertEqual(p.returncode, 1, "a run that delivered must not be resumed")

    def test_a_stall_with_no_tool_calls_keeps_its_own_name(self):
        _p, reason, _s = self.run_classifier(
            [{"type": "system", "session_id": self.SID},
             self.result(stats={"tools": {"totalCalls": 0}})])
        self.assertEqual(reason, "stalled_no_tool_calls")

    def test_every_named_counter_reaches_the_verdict_or_the_artifact(self):
        """This project's signature defect: a term computed and printed but
        absent from the exit code and unasserted by any test."""
        _p, reason, signals = self.run_classifier(
            [{"type": "system", "session_id": self.SID},
             self.result(is_error=True, error={"message": "MAX_TURNS"})],
            exit_code=53, stderr="Reached max session turns")
        expected = {"result_rows_total", "subagent_result_rows",
                    "run_result_row_index", "parse_failed", "exit_code",
                    "budget_kind", "api_error", "tool_calls", "has_report",
                    "reason", "session_id", "resumable"}
        self.assertEqual(set(signals), expected)
        # every key carries the value the verdict was actually made from
        self.assertEqual(signals["reason"], reason)
        self.assertEqual(signals["exit_code"], 53)
        self.assertEqual(signals["budget_kind"], "turns")
        self.assertEqual(signals["parse_failed"], False)
        self.assertEqual(signals["api_error"], False)
        self.assertEqual(signals["has_report"], False)
        self.assertEqual(signals["resumable"], True)
        self.assertEqual(signals["session_id"], self.SID)
        self.assertEqual(signals["result_rows_total"], 1)
        self.assertEqual(signals["subagent_result_rows"], 0)
        self.assertEqual(signals["run_result_row_index"], 1)
        self.assertIsNone(signals["tool_calls"])


if __name__ == "__main__":
    unittest.main()
