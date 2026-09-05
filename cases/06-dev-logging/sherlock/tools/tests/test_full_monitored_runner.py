#!/usr/bin/env python3
"""Provider-free acceptance for the monitored runner terminal audit."""
import importlib.util
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = load("lifecycle_supervisor_runner_test", ROOT / "eval/bench/lifecycle-supervisor.py")
VERDICT = load("run_verdict_runner_test", ROOT / "eval/bench/run-verdict.py")


class MonitoredTerminalAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.trace = Path(self.temp.name) / "trace"
        self.trace.mkdir()
        self.nonce = "n" * 32
        self.boot = "fixture-boot"
        self.observer = LIFECYCLE.init_segment(self.trace, self.nonce, self.boot,
                                               capability=b"x" * 32)
        LIFECYCLE.publish_observation(self.observer, self.nonce, self.boot,
                                      sequence=0, monotonic_ns=time.monotonic_ns(),
                                      capability=b"x" * 32)
        self.workspace = self.trace / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.digest = "d" * 64
        self.launch = LIFECYCLE.publish_launch(
            self.observer, self.trace, self.nonce, self.boot,
            action="harness_qualification_operator_monitored", run_tag="fixture",
            predecessor_nonce=None, package_version="v45", package_sha256=self.digest,
            controller_pid=os.getpid(), controller_start_ticks="fixture",
            controller_pgid=os.getpgrp(), workspace_dir=self.workspace,
            target_profile_sha256=self.digest, run_budget_sha256=self.digest,
            input_package_sha256=self.digest, settings_sha256=self.digest,
            lifecycle_helper_sha256=self.digest, authorization_sha256=self.digest,
            manifest_sha256=self.digest)

    def tearDown(self):
        self.temp.cleanup()

    def finish(self):
        return LIFECYCLE.finalize_segment(
            self.observer, self.trace, self.nonce, self.boot, run_tag="fixture",
            launch_sha256=LIFECYCLE.sha256((self.trace / "lifecycle-launch.json").read_bytes()),
            lifecycle_helper_sha256=self.digest, guardian_pid=123,
            guardian_start_ticks="fixture", guardian_exit_code=-15)

    def test_valid_signed_terminal_receipt_passes(self):
        self.finish()
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace), [])

    def test_provider_call_id_bridges_qwen_tool_use_id_in_terminal_audit(self):
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-provider-1"])
        for phase in ("PreToolUse", "PostToolUse"):
            event = {
                "hook_event_name": phase,
                "session_id": "session-1",
                "tool_use_id": "toolu-qwen-1",
                "tool_call_id": "call-provider-1",
                "tool_name": "skill",
                "tool_input": {"skill": "mockskill"},
            }
            if phase == "PostToolUse":
                event["tool_response"] = {"ok": True}
            result = LIFECYCLE.handle_hook(
                self.observer, self.workspace, self.nonce, self.boot,
                json.dumps(event, sort_keys=True).encode())
            self.assertTrue(result["continue"])
        batch_event = {
            "hook_event_name": "PostToolBatch", "session_id": "session-1",
            "tool_calls": [{
                "tool_name": "skill", "tool_input": {"skill": "mockskill"},
                "tool_use_id": "call-provider-1",
                "tool_call_id": "call-provider-1", "status": "success",
                "tool_response": {"execution_status": "completed"},
            }],
        }
        result = LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot,
            json.dumps(batch_event, sort_keys=True).encode())
        self.assertTrue(result["continue"])
        receipt = self.finish()
        self.assertEqual(receipt["expected_tool_count"], 1)
        self.assertEqual(receipt["completed_tool_count"], 1)
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace), [])

    def test_auditor_rejects_replayed_completed_batch_id(self):
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-provider-1"])
        for phase in ("PreToolUse", "PostToolUse"):
            event = {
                "hook_event_name": phase, "session_id": "session-1",
                "tool_use_id": "toolu-qwen-1", "tool_call_id": "call-provider-1",
                "tool_name": "skill", "tool_input": {"skill": "mockskill"},
            }
            if phase == "PostToolUse":
                event["tool_response"] = {"ok": True}
            self.assertTrue(LIFECYCLE.handle_hook(
                self.observer, self.workspace, self.nonce, self.boot,
                json.dumps(event, sort_keys=True).encode())["continue"])
        batch_event = {
            "hook_event_name": "PostToolBatch", "session_id": "session-1",
            "tool_calls": [{
                "tool_name": "skill", "tool_input": {"skill": "mockskill"},
                "tool_use_id": "call-provider-1", "tool_call_id": "call-provider-1",
                "status": "success", "tool_response": {"execution_status": "completed"},
            }],
        }
        self.assertTrue(LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot,
            json.dumps(batch_event, sort_keys=True).encode())["continue"])
        receipt = self.finish()

        batch_path = self.observer / "post-tool-batch-events.jsonl"
        batch_path.write_bytes(batch_path.read_bytes() + batch_path.read_bytes())
        receipt["post_tool_batch_events_sha256"] = LIFECYCLE.sha256(
            batch_path.read_bytes())
        receipt = LIFECYCLE._sign_record(self.observer, receipt)
        (self.trace / "lifecycle-receipt.json").write_bytes(
            LIFECYCLE._canonical(receipt) + b"\n")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_INVALID"])

    def test_prevalidation_rejection_is_separately_bound_and_audited(self):
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-invalid"])
        event = {
            "cwd": str(self.workspace),
            "hook_event_name": "PostToolBatch",
            "permission_mode": "default",
            "session_id": "session-1",
            "timestamp": "2026-09-06T00:00:00.000Z",
            "tool_calls": [{
                "tool_name": "run_shell_command",
                "tool_input": {"command": "true", "directory": "/outside"},
                "tool_use_id": "call-invalid",
                "tool_call_id": "call-invalid",
                "status": "error",
                "tool_response": {
                    "error": "Directory is outside the workspace",
                    "error_type": "invalid_tool_params",
                    "execution_status": "not_started",
                },
            }],
            "transcript_path": str(self.workspace / "transcript.jsonl"),
        }
        result = LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot,
            json.dumps(event, sort_keys=True).encode())
        self.assertTrue(result["continue"])
        receipt = self.finish()
        self.assertEqual(receipt["expected_tool_count"], 1)
        self.assertEqual(receipt["completed_tool_count"], 0)
        self.assertEqual(receipt["rejected_tool_count"], 1)
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace), [])

        rejections = json.loads((self.observer / "rejections.json").read_text())
        rejections["rejected"]["call-invalid"]["input_sha256"] = "0" * 64
        (self.observer / "rejections.json").write_text(
            json.dumps(rejections, sort_keys=True, separators=(",", ":")) + "\n")
        receipt["rejections_sha256"] = LIFECYCLE.sha256(
            (self.observer / "rejections.json").read_bytes())
        receipt = LIFECYCLE._sign_record(self.observer, receipt)
        (self.trace / "lifecycle-receipt.json").write_bytes(
            LIFECYCLE._canonical(receipt) + b"\n")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_INVALID"])

    def test_auditor_rejects_unknown_nonrejection_batch_id(self):
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-valid"])
        for phase in ("PreToolUse", "PostToolUse"):
            event = {
                "hook_event_name": phase, "session_id": "session-1",
                "tool_use_id": "toolu-valid", "tool_call_id": "call-valid",
                "tool_name": "run_shell_command", "tool_input": {"command": "true"},
            }
            if phase == "PostToolUse":
                event["tool_response"] = {"ok": True}
            self.assertTrue(LIFECYCLE.handle_hook(
                self.observer, self.workspace, self.nonce, self.boot,
                json.dumps(event, sort_keys=True).encode())["continue"])
        batch_event = {
            "hook_event_name": "PostToolBatch", "session_id": "session-1",
            "tool_calls": [{"tool_name": "run_shell_command",
                            "tool_input": {"command": "true"},
                            "tool_use_id": "call-valid", "tool_call_id": "call-valid",
                            "status": "success",
                            "tool_response": {"execution_status": "completed"}}],
        }
        self.assertTrue(LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot,
            json.dumps(batch_event, sort_keys=True).encode())["continue"])
        receipt = self.finish()

        batch_path = self.observer / "post-tool-batch-events.jsonl"
        batch = json.loads(batch_path.read_text())
        raw = json.loads(LIFECYCLE.base64.b64decode(batch["input_base64"]))
        raw["tool_calls"][0]["tool_use_id"] = "call-unknown"
        raw["tool_calls"][0]["tool_call_id"] = "call-unknown"
        raw_bytes = json.dumps(raw, sort_keys=True).encode()
        batch["input_base64"] = LIFECYCLE.base64.b64encode(raw_bytes).decode()
        batch["input_sha256"] = LIFECYCLE.sha256(raw_bytes)
        batch["tool_call_ids"] = ["call-unknown"]
        batch_path.write_bytes(LIFECYCLE._canonical(batch) + b"\n")
        receipt["post_tool_batch_events_sha256"] = LIFECYCLE.sha256(
            batch_path.read_bytes())
        receipt = LIFECYCLE._sign_record(self.observer, receipt)
        (self.trace / "lifecycle-receipt.json").write_bytes(
            LIFECYCLE._canonical(receipt) + b"\n")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_INVALID"])

    def test_schema2_trace_without_launch_is_rejected(self):
        other = Path(self.temp.name) / "unlaunched"
        other.mkdir()
        (other / "target-profile.json").write_text(
            '{"schema":2,"execution_mode":"operator_monitored"}', encoding="utf-8")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(other),
                         ["LIFECYCLE_AUDIT_INVALID"])

    def test_cli_refuses_unlaunched_schema2_trace(self):
        trace = Path(self.temp.name) / "cli-unlaunched"
        trace.mkdir()
        (trace / "target-profile.json").write_text(
            '{"schema":2,"execution_mode":"operator_monitored"}', encoding="utf-8")
        (trace / "status.json").write_text(json.dumps({
            "schema": 1, "run_tag": trace.name, "phase": "REJECTED",
            "trace_dir": str(trace),
        }), encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "eval/bench/run-verdict.py"),
                                 str(trace), "--json"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("LIFECYCLE_AUDIT_INVALID", json.loads(result.stdout)["failures"])

    def test_tampered_terminal_receipt_is_rejected(self):
        self.finish()
        receipt = self.trace / "lifecycle-receipt.json"
        receipt.write_text(receipt.read_text().replace('"PASS"', '"FAULT"'), encoding="utf-8")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_INVALID"])

    def test_launch_without_terminal_receipt_is_rejected(self):
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_INVALID"])

    def test_fault_or_incomplete_receipt_is_rejected(self):
        receipt = self.finish()
        receipt["status"] = "FAULT"
        receipt["fault_reason"] = "GUARDIAN_EXPIRED"
        receipt = LIFECYCLE._sign_record(self.observer, receipt)
        (self.trace / "lifecycle-receipt.json").write_bytes(LIFECYCLE._canonical(receipt) + b"\n")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_FAULT"])

    def test_signed_but_false_tool_count_is_rejected(self):
        receipt = self.finish()
        receipt["expected_tool_count"] = 1
        receipt["completed_tool_count"] = 1
        receipt = LIFECYCLE._sign_record(self.observer, receipt)
        (self.trace / "lifecycle-receipt.json").write_bytes(LIFECYCLE._canonical(receipt) + b"\n")
        self.assertEqual(VERDICT.monitored_lifecycle_failures(self.trace),
                         ["LIFECYCLE_AUDIT_INVALID"])


if __name__ == "__main__":
    unittest.main()

class FullMonitoredSelectedRunnerTest(unittest.TestCase):
    """One provider-free selected-subscription run crosses controller -> runner -> proxy."""

    @staticmethod
    def _controller_fixture_class():
        controller = ROOT / "eval/bench/bench-controller.sh"
        fixture_source = ROOT / "tools/tests/test_bench_controller.sh"
        source = fixture_source.read_text(encoding="utf-8").split(
            'exec python3 - "$@" <<\'PY\'\n', 1)[1].rsplit("\nPY\n", 1)[0]
        namespace = {"__name__": "full_monitored_runner_controller_fixture"}
        old = os.environ.get("BENCH_CONTROLLER_HERE")
        os.environ["BENCH_CONTROLLER_HERE"] = str(controller.parent)
        try:
            exec(compile(source, str(controller), "exec"), namespace)
        finally:
            if old is None:
                os.environ.pop("BENCH_CONTROLLER_HERE", None)
            else:
                os.environ["BENCH_CONTROLLER_HERE"] = old
        return namespace["ControllerFixture"], controller

    def _selected_inputs(self):
        from tools.tests.test_harness_selected_package import SelectedPackageTests
        selected = SelectedPackageTests()
        selected.setUp()
        self.addCleanup(selected.doCleanups)
        selected.prepare()
        return selected

    def _write_qwen(self, directory, tools):
        qwen = directory / "loopback-qwen.py"
        qwen.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys, urllib.request
from pathlib import Path
if '--sherlock-flag-probe-sentinel' in sys.argv:
    raise SystemExit(0)
settings = json.loads(Path('.qwen/settings.json').read_text(encoding='utf-8'))
assert os.environ['SHERLOCK_OPERATOR_MONITORED_MODE'] == '1'
assert settings['skills']['directories'] == ['../skill-catalogue']
assert Path.cwd().name == 'workspace'
skill_root = Path(os.environ['QWEN_SKILL_ROOT'])
assert (Path.cwd() / '../skill-catalogue').resolve() == skill_root.parent.resolve()
requested_model = os.environ.get('SHERLOCK_QWEN_MODEL', os.environ['SHERLOCK_MODEL'])
request = urllib.request.Request(os.environ['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions',
    data=json.dumps({'model': requested_model,
      'messages':[{'role':'user','content':sys.argv[sys.argv.index('-p') + 1]}]}).encode(),
    headers={'Authorization':'Bearer fixture', 'Content-Type':'application/json'})
with urllib.request.urlopen(request, timeout=10) as response:
    model = json.loads(response.read().decode())
assert model['model'] == requested_model
work, corpus = Path('work'), Path('corpus')
work.mkdir(exist_ok=True)
subprocess.run(['python3', %r, str(corpus), '--out', str(work), '--worklist-cap', '10', '--rate-cap', '0', '--jobs', '1'], check=True, stdout=subprocess.DEVNULL)
(work/'rules.tsv').write_text('', encoding='utf-8')
rows = [x for x in (work/'worklist.tsv').read_text(encoding='utf-8').splitlines() if x and not x.startswith('#')]
if not rows:
    security=(corpus/'Security.jsonl').read_text(encoding='utf-8').splitlines()[2]
    system=(corpus/'System.jsonl').read_text(encoding='utf-8').splitlines()[0]
    (work/'worklist.tsv').write_text('# id\\tвердикт\\tось\\tссылка\\tчастота\\tзапись\\n' + 'g1\\t?\\trare\\tSecurity.jsonl:3\\tn=1\\t'+security+'\\n' + 'g2\\t?\\trare\\tSystem.jsonl:1\\tn=1\\t'+system+'\\n', encoding='utf-8')
    rows = [x for x in (work/'worklist.tsv').read_text(encoding='utf-8').splitlines() if x and not x.startswith('#')]
for source in rows:
    row=source.split('\\t'); path,line=row[3].rsplit(':',1)
    quote=(corpus/path).read_text(encoding='utf-8').splitlines()[int(line)-1]
    subprocess.run(['python3', %r, 'next', '--work', str(work)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['python3', %r, 'verdict', '--work', str(work), '--id', row[0], '--cell', 'N n=1 %%s «%%s»' %% (row[3], quote)], check=True, stdout=subprocess.DEVNULL)
(work/'report.md').write_text(model['choices'][0]['message']['content'], encoding='utf-8')
print(json.dumps([{'type':'result','result':'ok','is_error':False,'session_id':'fixture-session','num_turns':1,'usage':{'input_tokens':1,'output_tokens':1}}]))
""" % (str(tools / "logmap.py"), str(tools / "worklist.py"), str(tools / "worklist.py")), encoding="utf-8")
        qwen.chmod(0o700)
        return qwen

    def test_selected_subscription_runs_controller_runner_and_proxy(self):
        """Schema-2 selected inputs cannot bypass the signed controller launch."""
        import hashlib
        import http.server
        import threading
        selected = self._selected_inputs()
        Fixture, controller = self._controller_fixture_class()
        fixture = Fixture(self)
        self.addCleanup(fixture.close)
        qwen = self._write_qwen(fixture.base, selected.fixture.root / "runtime-package" / "tools")
        runner_launch = fixture.base / "runner-launch.sh"
        runner_launch.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "p=" + repr(str(fixture.proc)) + "/$$\n"
            "mkdir -p \"$p\"\n"
            "printf '%s (runner) S 1 %s %s ' \"$$\" \"$$\" \"$$\" > \"$p/stat\"\n"
            "printf '0 %.0s' {1..15} >> \"$p/stat\"\n"
            "printf '41\\n' >> \"$p/stat\"\n"
            "printf 'runner\\0' > \"$p/cmdline\"\n"
            "exec bash " + str(ROOT / "eval/bench/run-bench.sh") + " v45\n",
            encoding="utf-8")
        runner_launch.chmod(0o700)
        canonical_report = (ROOT / "tools/tests/fixtures/target-contract-reports/canonical.md").read_text(encoding="utf-8")
        seen = []
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(inner):
                size = int(inner.headers["Content-Length"])
                seen.append(json.loads(inner.rfile.read(size)))
                response = json.dumps({"id":"fixture", "object":"chat.completion", "model":"gpt-5.5", "choices":[{"index":0,"message":{"role":"assistant","content":canonical_report},"finish_reason":"stop"}], "usage":{"prompt_tokens":1,"completion_tokens":1}}).encode()
                inner.send_response(200); inner.send_header("Content-Type", "application/json"); inner.send_header("Content-Length", str(len(response))); inner.end_headers(); inner.wfile.write(response)
            def log_message(inner, *_args): pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        output = selected.output
        env = fixture.monitored_env(
            SHERLOCK_TARGET_PROFILE=str(output / "target-profile.json"),
            SHERLOCK_SETTINGS=str(output / "corporate-settings.json"),
            SHERLOCK_PROBE_BUDGET=str(output / "probe-budget.json"),
            SHERLOCK_INPUT_PACKAGE=str(output / "input-package.json"),
            SHERLOCK_SKILL_ROOT=str(output / "runtime-package"),
            SHERLOCK_PROMPT_FILE=str(output / "prompt.txt"),
            SHERLOCK_CORPUS=str(selected.fixture.source),
            QWEN_BIN=str(qwen),
            SHERLOCK_TARGET_COMMAND=str(runner_launch),
            SHERLOCK_BASE_URL="http://127.0.0.1:%d/v1" % server.server_port,
            SHERLOCK_MODEL="gpt-5.5", SHERLOCK_EXPECTED_RETURNED_IDENTITY="gpt-5.5",
            SHERLOCK_TARGET_VERSION="v45", SHERLOCK_PROVIDER="loopback-fixture",
            SHERLOCK_PACKAGE_VERSION="v45",
            SHERLOCK_TIMEOUT="0", SHERLOCK_MAX_SESSION_TURNS="-1",
            SHERLOCK_MAX_TOOL_CALLS="-1", SHERLOCK_MAX_WALL_TIME_S="-1",
        )
        process = subprocess.Popen(["bash", str(controller)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        observed = fixture.observe_until_exit(process)
        stdout, stderr = process.communicate(timeout=90)
        status = [path.read_text(encoding="utf-8") for path in fixture.controllers.glob("*/status.json")]
        trace_debug = {path.name: path.read_text(encoding="utf-8", errors="replace")[:500]
                       for path in fixture.runs.glob("run-*/*") if path.is_file()}
        self.assertEqual(process.returncode, 0, (stdout, stderr, status, trace_debug))
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(seen), 1)
        trace = next(path for path in fixture.runs.glob("run-*")
                     if path.is_dir() and (path / "run-manifest.json").is_file())
        receipt = json.loads((trace / "lifecycle-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue((trace / "candidate.json").is_file())
        self.assertEqual(VERDICT.monitored_lifecycle_failures(trace), [])
