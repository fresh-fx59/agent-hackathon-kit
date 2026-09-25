#!/usr/bin/env python3
"""Harness items for the v53 Stop-hook fix (spec 2026-09-24 items 3, 7, 8, 9, 11-prep).

No model, no network: loop detector, wall clock, signal/atexit terminal +
run.done, permission-denied events, Stop-hook wrapper observability, package
selection and the load-time index build.
"""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("runner_v53", HERE / "run-v52-gpt55-comparison.py")
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)
WRAPPER = HERE / "stop-hook-log.py"


def ts(offset=0):
    return datetime.datetime.fromtimestamp(time.time() + offset, datetime.timezone.utc).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="h53-"))
    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class ConstantsTest(unittest.TestCase):
    def test_spec_values(self):
        self.assertEqual(R.STOP_HOOK_TIMEOUT_S, 600)
        self.assertEqual(R.MAX_WALL_SECONDS, 21600)
        self.assertEqual(R.STOP_HOOK_LOOP_LIMIT, 3)
        self.assertEqual(R.PACKAGES, ("v52", "v53"))


class LoopDetectorTest(Base):
    def write(self, rows):
        log = self.d / "hook.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return log
    def row(self, reason="citecheck timed out after 31 s (no heartbeat)", rep="r1", msg="m1", decision="block"):
        return {"ts": ts(), "decision": decision, "reason": reason, "report_sha256": rep, "msg_sha256": msg}

    def test_three_identical_blocks_is_a_loop(self):
        log = self.write([self.row(), self.row(reason="citecheck timed out after 32 s (no heartbeat)"), self.row()])
        loop = R.stop_hook_loop(log)
        self.assertEqual(loop["count"], 3)
        self.assertEqual(loop["report_sha256"], "r1")

    def test_two_identical_or_changing_inputs_is_not_a_loop(self):
        self.assertIsNone(R.stop_hook_loop(self.write([self.row(), self.row()])))
        self.assertIsNone(R.stop_hook_loop(self.write([self.row(), self.row(rep="r2"), self.row(msg="m2")])))
        self.assertIsNone(R.stop_hook_loop(self.write([self.row(decision="allow")] * 5)))

    def test_rows_before_run_start_are_ignored(self):
        old = dict(self.row(), ts=ts(-3600))
        self.assertIsNone(R.stop_hook_loop(self.write([old, old, self.row()]), since=time.time() - 60))


class TerminalAndDoneTest(Base):
    def test_terminal_then_ensure_writes_done_once(self):
        R.terminal(self.d, "completed", qwen_exit=0)
        self.assertFalse((self.d / "run.done").exists())  # done only after children stop
        R.ensure_terminal(self.d, "launcher_exited_without_terminal")
        self.assertEqual(json.loads((self.d / "run-terminal.json").read_text())["status"], "completed")
        self.assertEqual(json.loads((self.d / "run.done").read_text())["status"], "completed")
        R.ensure_terminal(self.d, "other")  # idempotent
        self.assertEqual(json.loads((self.d / "run-terminal.json").read_text())["status"], "completed")

    def test_ensure_without_terminal_records_status(self):
        R.ensure_terminal(self.d, "launcher_exited_without_terminal")
        self.assertEqual(json.loads((self.d / "run.done").read_text())["status"],
                         "launcher_exited_without_terminal")

    def _signal_child(self, sig):
        control = self.d / "control"; control.mkdir()
        script = self.d / "child.py"
        script.write_text(textwrap.dedent("""
            import importlib.util, sys, time
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("r", %r)
            R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
            class A: control_root = %r; max_wall_seconds = None
            def fake_load(control): time.sleep(30)
            R.load_manifest = fake_load
            print("ready", flush=True)
            sys.exit(R.run(A()))
        """ % (str(HERE / "run-v52-gpt55-comparison.py"), str(control))))
        p = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "ready")
        time.sleep(0.3); p.send_signal(sig); p.wait(timeout=20)
        return control

    def test_sigterm_sigint_sighup_always_leave_terminal_and_done(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            control = self._signal_child(sig)
            term = json.loads((control / "run-terminal.json").read_text())
            self.assertEqual(term["status"], "interrupted", sig)
            self.assertTrue((control / "run.done").is_file(), sig)
            shutil.rmtree(control)


class WallClockTest(unittest.TestCase):
    def test_run_loop_enforces_wall_clock(self):
        src = (HERE / "run-v52-gpt55-comparison.py").read_text()
        self.assertIn('TerminalFailure("wall_clock_exceeded"', src)
        self.assertIn('"--max-wall-seconds", type=int, default=MAX_WALL_SECONDS', src)
        self.assertIn('TerminalFailure("stop_hook_loop"', src)


class PermissionDeniedTest(Base):
    def test_events_recorded(self):
        out = self.d / "qwen-output.json"
        out.write_text(json.dumps([{"type": "user", "message": {"content": [{"type": "tool_result",
            "content": "Error: permission was declined (non-interactive mode, shell)"}]}}]))
        self.assertEqual(R.permission_denials(out, 2, self.d), 1)
        row = json.loads((self.d / "permission-denied.jsonl").read_text().splitlines()[0])
        self.assertEqual((row["event"], row["session"]), ("permission_denied", 2))
        self.assertIn("permission was declined", row["excerpt"])
        clean = self.d / "clean.json"; clean.write_text("[]")
        self.assertEqual(R.permission_denials(clean, 3, self.d), 0)


class PackageSelectionTest(Base):
    def test_v52_default_and_v53_selectable(self):
        R.use_package("v52"); self.assertEqual(R.skill_dir(), "skills/v52")
        R.use_package("v53"); self.assertEqual(R.skill_dir(), "skills/v53")
        with self.assertRaises(R.Refusal): R.use_package("v51")
        R.use_package("v52")

    def test_package_sha_binds_only_selected_tree(self):
        inv = [{"path": "skills/v53/a", "sha256": "1"}, {"path": "skills/v52/a", "sha256": "2"}]
        R.use_package("v53"); a = R.package_sha(inv)
        inv[1]["sha256"] = "3"; self.assertEqual(R.package_sha(inv), a)
        inv[0]["sha256"] = "4"; self.assertNotEqual(R.package_sha(inv), a)
        R.use_package("v52")

    def test_build_index_runs_outside_hook_and_logs(self):
        root = self.d / "run"; tools = root / "skills/v53/tools"; tools.mkdir(parents=True)
        (root / "corpus").mkdir(); control = self.d / "control"; control.mkdir()
        (tools / "buildindex.py").write_text(
            "import os,sys\nassert not os.environ.get('SHERLOCK_IN_STOP_HOOK')\n"
            "print('built', sys.argv[1:], os.environ['SHERLOCK_INDEX_ROOT'])\n")
        R.use_package("v53")
        try:
            row = R.build_index(root, control)
            self.assertEqual(row["rc"], 0)
            self.assertIn("--corpus", (control / "buildindex.log").read_text())
            (tools / "buildindex.py").write_text("raise SystemExit(4)\n")
            (control / "buildindex.json").unlink()
            with self.assertRaises(R.TerminalFailure) as cm: R.build_index(root, control)
            self.assertEqual(cm.exception.status, "index_build_failed")
        finally:
            R.use_package("v52")


class WrapperObservabilityTest(Base):
    def run_wrapper(self, hook_src, env_extra=None, msg="REPORT work/report.md sha256=x bytes=1"):
        ws = self.d / "ws"; (ws / "work").mkdir(parents=True); (ws / "work/report.md").write_text("отчёт")
        hook = self.d / "hook.py"; hook.write_text(hook_src)
        env = dict(os.environ, SHERLOCK_STOP_HOOK_LOG=str(self.d / "log.jsonl"),
                   SHERLOCK_STOP_HOOK_TIMEOUT_S="600",
                   SHERLOCK_STOP_HOOK_DETAIL_DIR=str(self.d / "details"), **(env_extra or {}))
        env.pop("SHERLOCK_INDEX_ROOT", None)
        data = json.dumps({"hook_event_name": "Stop", "cwd": str(ws), "last_assistant_message": msg}).encode()
        p = subprocess.run([sys.executable, str(WRAPPER), sys.executable, str(hook)], input=data,
                           capture_output=True, cwd=ws, env=env)
        return p, json.loads((self.d / "log.jsonl").read_text().splitlines()[-1]), ws

    def test_fields_and_detail_copy(self):
        src = textwrap.dedent("""
            import json, os
            d = os.path.join(os.getcwd(), "index", "verdicts"); os.makedirs(d, exist_ok=True)
            json.dump({"cache": "miss", "cache_key": "k1", "gates": {"citecheck": {"rc": 0}}},
                      open(os.path.join(d, "stopcheck-detail.json"), "w"))
            print(json.dumps({"decision": "block", "reason": "x"}))
        """)
        p, row, ws = self.run_wrapper(src)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["decision"], "block")  # passthrough unchanged
        body = "отчёт".encode()
        self.assertEqual(row["report_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual((row["report_chars"], row["report_bytes"]), (5, len(body)))
        self.assertEqual(row["msg_chars"], len("REPORT work/report.md sha256=x bytes=1"))
        self.assertEqual((row["cache"], row["cache_key"]), ("miss", "k1"))
        self.assertEqual(row["stopcheck_detail"]["gates"]["citecheck"]["rc"], 0)
        self.assertTrue((self.d / "details" / row["stopcheck_detail_file"]).is_file())
        self.assertIsNone(row["duration_near_cap"])

    def test_stale_detail_is_not_attributed(self):
        src = "import json; print(json.dumps({'decision': 'allow'}))\n"
        ws = self.d / "ws"; d = ws / "index/verdicts"; d.mkdir(parents=True)
        f = d / "stopcheck-detail.json"; f.write_text('{"cache": "hit"}')
        old = time.time() - 3600; os.utime(f, (old, old))
        _p, row, _ws = self.run_wrapper(src)
        self.assertNotIn("stopcheck_detail", row)

    def test_near_cap_flag(self):
        spec = importlib.util.spec_from_file_location("w", WRAPPER)
        W = importlib.util.module_from_spec(spec); spec.loader.exec_module(W)
        os.environ["SHERLOCK_STOP_HOOK_TIMEOUT_S"] = "600"
        try:
            self.assertEqual(W.near_cap(599000), "outer")
            self.assertEqual(W.near_cap(589000), "stopcheck_ceiling")
            self.assertIsNone(W.near_cap(30000))
        finally:
            del os.environ["SHERLOCK_STOP_HOOK_TIMEOUT_S"]



class CheckerFaultTerminalTest(LoopDetectorTest):
    """Spec item 5: checker_fault is its own terminal, not stop_hook_loop."""
    FAULT = ("Sherlock: checker_fault — citecheck timed out after 50 s (no heartbeat) — not a "
             "citation error; do not edit the report; 2 consecutive timeouts on unchanged inputs.")

    def test_one_checker_fault_block_is_terminal(self):
        log = self.write([self.row(reason=self.FAULT)])
        self.assertIsNotNone(R.checker_fault(log))
        self.assertIsNone(R.stop_hook_loop(log))  # distinct: one row, no loop

    def test_timeouts_and_allows_are_not_checker_fault(self):
        log = self.write([self.row(), self.row(reason=self.FAULT, decision="allow")])
        self.assertIsNone(R.checker_fault(log))
        old = dict(self.row(reason=self.FAULT), ts=ts(-3600))
        self.assertIsNone(R.checker_fault(self.write([old]), since=time.time() - 60))

    def test_launcher_checks_fault_before_loop(self):
        src = (HERE / "run-v52-gpt55-comparison.py").read_text(encoding="utf-8")
        a = src.index('raise TerminalFailure("checker_fault"')
        b = src.index('raise TerminalFailure("stop_hook_loop"')
        self.assertLess(a, b)


class VerdictKeyTest(WrapperObservabilityTest):
    def test_key_file_is_harness_private_and_passed_only_to_hook(self):
        trace = self.d / "trace"; trace.mkdir(mode=0o700)
        key = R.write_verdict_key(trace)
        self.assertEqual(key.stat().st_mode & 0o777, 0o400)
        self.assertEqual(len(key.read_bytes()), 32)
        src = "import json, os; print(json.dumps({'decision': 'allow', 'k': os.environ.get('SHERLOCK_VERDICT_KEY_FILE')}))\n"
        env = {"SHERLOCK_STOP_HOOK_LOG": str(trace / "stop-hook.jsonl")}
        ws = self.d / "ws"; (ws / "work").mkdir(parents=True); (ws / "work/report.md").write_text("r")
        data = json.dumps({"hook_event_name": "Stop", "cwd": str(ws), "last_assistant_message": "m"}).encode()
        hook = self.d / "hook.py"; hook.write_text(src)
        full = dict(os.environ, **env); full.pop("SHERLOCK_VERDICT_KEY_FILE", None)
        p = subprocess.run([sys.executable, str(WRAPPER), sys.executable, str(hook)], input=data,
                           capture_output=True, cwd=ws, env=full)
        self.assertEqual(json.loads(p.stdout)["k"], str(key))
        launcher = (HERE / "run-v52-gpt55-comparison.py").read_text(encoding="utf-8")
        qenv = launcher[launcher.index("qenv = {"):launcher.index("fullenv = {")]
        self.assertNotIn("VERDICT", qenv)  # Qwen / model shell never get the key path


if __name__ == "__main__":
    unittest.main()
