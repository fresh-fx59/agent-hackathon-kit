#!/usr/bin/env python3
"""Tests for eval/bench/run-bench.sh — the 649 MB corpus runner.

It had no tests, and that is exactly how it drifted: `run-case.sh` grew an
upstream proxy (attribution) and then the model-id split (the 177,000-token
ceiling), and `run-bench.sh` — the runner pointed at the BIGGEST corpus, where
the ceiling bites hardest — silently kept neither.

A stub `qwen` and a stub provider keep this network-free and free of charge.
"""
import json
import os
import importlib.util
import re
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

_spec = importlib.util.spec_from_file_location(
    "deliverable", os.path.join(MEASURE, "deliverable.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

# Records argv, relays ONE request through OPENAI_BASE_URL carrying the id it was
# handed, then answers in run-bench.sh's `--output-format json` shape.
STUB = r"""#!/usr/bin/env bash
printf '%s\0' "$@" >> "$QWEN_STUB_LOG"
[ -f .qwen/settings.json ] && cp .qwen/settings.json "$QWEN_STUB_LOG.settings"
printf '%s' "${QWEN_SKILL_ROOT:-}" > "$QWEN_STUB_LOG.skill-root"
if [ -n "${QWEN_SKILL_ROOT:-}" ] && [ -f "$QWEN_SKILL_ROOT/SKILL.md" ]; then
  printf 'present' > "$QWEN_STUB_LOG.skill-root-state"
fi
M=""
while [ $# -gt 0 ]; do
  case "$1" in --model) M="$2"; shift 2 ;; *) shift ;; esac
done
python3 -c '
import json, os, sys, urllib.request
url = os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions"
body = json.dumps({"model": sys.argv[1], "messages": []}).encode("utf-8")
req = urllib.request.Request(url, data=body,
                             headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10).read()
except Exception:
    pass
' "$M"
# A real run writes its report into work/report.md and may then deliver it on
# either channel, or on neither. The stub reproduces all three.
if [ -n "${QWEN_STUB_REPORT:-}" ]; then
  mkdir -p work && printf '%s' "$QWEN_STUB_REPORT" > work/report.md
fi
[ -n "${QWEN_STUB_KILL:-}" ] && exit 143
QWEN_STUB_MODEL="$M" python3 -c '
import json, os
print(json.dumps([
  {"type": "system", "model": os.environ["QWEN_STUB_MODEL"]},
  {"type": "result",
   "result": os.environ.get("QWEN_STUB_ANSWER", "apps/api.log:1 something broke"),
   "num_turns": 2, "usage": {"input_tokens": 11, "output_tokens": 22}}]))
'
exit 0
"""


class StubProvider:
    def __init__(self):
        self.seen = []
        seen = self.seen

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n)
                try:
                    seen.append(json.loads(raw))
                except ValueError:
                    seen.append({"unparseable": True})
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


class BenchRunnerRig:
    """The stub rig: a fake `qwen`, a stub provider, a throwaway corpus."""

    def go(self, extra_env=None, arm="none"):
        prov = StubProvider()
        self.addCleanup(prov.close)
        d = tempfile.mkdtemp()
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
        log = os.path.join(d, "stub.log")
        self._stub_log = log
        env = dict(os.environ)
        env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                    "QWEN_STUB_LOG": log, "QWEN_BIN": qwen,
                    "SHERLOCK_CORPUS": os.path.join(d, "corpus"),
                    "SHERLOCK_BASE_URL": prov.url,
                    "SHERLOCK_API_KEY": "stub-key",
                    "BENCH_LEDGER": os.path.join(d, "runs-bench.jsonl"),
                    "BENCH_RUNS": os.path.join(d, "runs")})
        env.update(extra_env or {})
        p = subprocess.run(["bash", RUNNER, arm], capture_output=True,
                           text=True, env=env, timeout=120)
        argv = []
        if os.path.exists(log):
            with open(log, "rb") as fh:
                argv = fh.read().decode("utf-8").split("\0")
        rows = []
        led = env["BENCH_LEDGER"]
        if os.path.exists(led):
            with open(led, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        return argv, prov.seen, rows, p

    def cli_model(self, argv):
        return argv[argv.index("--model") + 1]


class TheBenchRunnerUsesTheSameUpstreamLane(BenchRunnerRig, unittest.TestCase):
    def test_the_qwen_child_receives_the_copied_skill_root(self):
        _argv, _seen, _rows, p = self.go(arm="v29")
        self.assertEqual(p.returncode, 0, "stderr: %s" % p.stderr[-800:])
        with open(self._stub_log + ".skill-root", encoding="utf-8") as fh:
            skill_root = fh.read()
        self.assertTrue(skill_root.endswith("/.qwen/skills/log-rca"),
                        "stderr: %s" % p.stderr[-800:])
        with open(self._stub_log + ".skill-root-state", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "present")

    def test_the_cli_is_given_the_clean_id(self):
        argv, _seen, _rows, p = self.go()
        self.assertEqual(self.cli_model(argv), "deepseek-v4-flash",
                         "stderr: %s" % p.stderr[-800:])

    def test_the_provider_receives_the_aliased_id(self):
        _argv, seen, _rows, _p = self.go()
        self.assertEqual([r.get("model") for r in seen], ["[SP]deepseek-v4-flash"])

    def test_the_runner_does_not_append_an_unchecked_ledger_row(self):
        _argv, _seen, rows, _p = self.go()
        self.assertEqual(rows, [], "the runner must not append an unchecked ledger row")

    def test_without_the_lane_the_cli_keeps_the_alias(self):
        argv, _seen, _rows, _p = self.go({"SHERLOCK_UPSTREAM_LOG": "0"})
        self.assertEqual(self.cli_model(argv), "[SP]deepseek-v4-flash")

    def test_it_states_the_context_window_too(self):
        """The 649 MB corpus is where a 177,000-token ceiling hurts most."""
        self.go()
        sp = self._stub_log + ".settings"
        self.assertTrue(os.path.exists(sp), "no .qwen/settings.json was written")
        with open(sp, encoding="utf-8") as fh:
            self.assertEqual(
                json.load(fh)["model"]["generationConfig"]["contextWindowSize"],
                400000)


class TheDeliveredArtifactContract(BenchRunnerRig, unittest.TestCase):
    """Delivery facts are computed by the acceptance validator, not the runner.

    `20260802T221034Z-v11`: `citecheck` green at 45/45, «Теперь финальный шаг —
    вывести отчёт полностью», `read_file(work/report.md)`, stop. Final message
    101 chars; `work/report.md` 19,991 chars and complete. A tool result is not
    the `result` record, so the runner recorded a 101-char answer and the
    scorer had nothing to judge.

    These tests keep the channel semantics stable while the runner emits only a
    candidate for validate-run.py; accepted ledger rows are written there.
    """

    REPORT = ("# Отчёт\n\napps/api.log:1 the vendor_ref query has no index\n"
              "svc/other.log:9 payments-worker panics on a short batch\n")

    def test_a_report_file_is_classified_as_file_delivery(self):
        _a, _s, rows, _p = self.go({"QWEN_STUB_REPORT": self.REPORT})
        self.assertEqual(rows, [])
        answer = "apps/api.log:1 something broke"
        self.assertEqual(D.channel(answer, self.REPORT), "file")
        self.assertGreaterEqual(len(D.compose(answer, self.REPORT)), len(self.REPORT))

    def test_a_collapsed_message_beside_a_full_report_is_classified_as_file(self):
        _a, _s, rows, _p = self.go({"QWEN_STUB_REPORT": self.REPORT,
                                    "QWEN_STUB_ANSWER": "Отчёт готов."})
        self.assertEqual(rows, [])
        self.assertEqual(D.channel("Отчёт готов.", self.REPORT), "file")
        self.assertNotEqual(D.compose("Отчёт готов.", self.REPORT), "Отчёт готов.",
                            "the collapse must stay visible, not be papered over")

    def test_a_run_with_no_report_file_is_classified_as_message(self):
        """Every row before 2026-08-03 is message-only, including the 0-of-11
        baseline. If this row moved, the published comparison would break."""
        _a, _s, rows, _p = self.go()
        self.assertEqual(rows, [])
        answer = "apps/api.log:1 something broke"
        self.assertEqual(D.channel(answer, ""), "message")
        self.assertEqual(len(D.compose(answer, "")), len(answer))

    def test_coverage_counts_files_named_in_the_deliverable(self):
        """`files_cited` drove the "cited 0 of 31" reading of the collapsed run.
        It was counting a 101-char message against a 31-file corpus."""
        _a, _s, rows, _p = self.go({"QWEN_STUB_REPORT": self.REPORT,
                                    "QWEN_STUB_ANSWER": "Отчёт готов."})
        self.assertEqual(rows, [])
        deliverable = D.compose("Отчёт готов.", self.REPORT)
        self.assertEqual(len({path for path in ("apps/api.log", "svc/other.log")
                              if path in deliverable}), 2,
                         "apps/api.log is named in the file, not the message")
        self.assertGreaterEqual(len(re.findall(r":\d+", deliverable)), 2)

    def test_a_killed_run_that_left_a_report_writes_no_unchecked_row(self):
        """A failed transport with a surviving report must not create an
        unchecked accepted row; validate-run.py decides whether it is valid."""
        _a, _s, rows, p = self.go({"QWEN_STUB_REPORT": self.REPORT,
                                   "QWEN_STUB_KILL": "1"})
        self.assertEqual(rows, [], "the runner must not append an unchecked ledger row")
        self.assertNotEqual(p.returncode, 0)

    def test_a_run_that_produced_nothing_at_all_is_still_refused(self):
        """No report, no answer: recording it would put a transport failure on
        the recall axis, which is the confusion this rig exists to separate."""
        _a, _s, rows, p = self.go({"QWEN_STUB_KILL": "1"})
        self.assertEqual(rows, [], "stdout: %s" % p.stdout[-600:])
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
