#!/usr/bin/env python3
"""Spec 2026-09-25 (hook timeout units + launcher final gate), acceptance ladder 1."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("launcher_units", HERE / "run-v52-gpt55-comparison.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
WRAPPER = HERE / "stop-hook-log.py"


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


class UnitTableTest(unittest.TestCase):
    def test_pinned_version_is_ms(self):
        self.assertEqual(R.QWEN_HOOK_TIMEOUT_UNITS, {"0.22.0": "ms"})
        self.assertEqual(R.hook_timeout_unit("0.22.0"), "ms")
        self.assertEqual(R.hook_timeout_value("0.22.0", R.STOP_HOOK_TIMEOUT_S), 50000)

    def test_unknown_version_refused(self):
        for v in ("0.21.1", "0.23.0", "", "0.22.0 "):
            with self.assertRaises(R.QwenVersionUnverified) as cm:
                R.hook_timeout_unit(v)
            self.assertEqual(cm.exception.status, "qwen_version_unverified")

    def test_settings_writer_emits_50000(self):
        d = Path(tempfile.mkdtemp())
        try:
            (d / ".qwen").mkdir(); (d / "skills/v53").mkdir(parents=True)
            (d / ".qwen/settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{
                "type": "command", "command": R.STOP_HOOK_COMMAND, "timeout": 600}]}]}}))
            R.use_package("v53"); R.stage_harness_layout(d)
            self.assertEqual(R.stop_hook_timeout(json.loads((d / ".qwen/settings.json").read_text())), 50000)
            self.assertNotIn("permissions", json.loads((d / ".qwen/settings.json").read_text()))
            src = (HERE / "run-v52-gpt55-comparison.py").read_text()
            self.assertEqual(R.QWEN_APPROVAL_MODE, "yolo")
            self.assertIn('"--approval-mode", QWEN_APPROVAL_MODE', src)
        finally:
            R.use_package("v52"); shutil.rmtree(d)


class RunRefusesUnverifiedVersionTest(unittest.TestCase):
    def test_terminal_qwen_version_unverified(self):
        d = Path(tempfile.mkdtemp())
        saved = (R.load_manifest, R.validate_manifest)
        try:
            qwen = d / "qwen"; qwen.write_text("#!/bin/sh\necho 0.23.0\n"); qwen.chmod(0o755)
            proxy = d / "proxy.py"; proxy.write_text("")
            control = d / "control"; control.mkdir()
            manifest = {"model": "gpt-5.5", "package": "v52", "proxy": str(proxy.resolve()),
                        "qwen": str(qwen.resolve()), "upstream_base": "http://127.0.0.1:8317/v1"}
            R.load_manifest = lambda c: (manifest, "A")
            R.validate_manifest = lambda c, m: None
            os.environ["SHERLOCK_API_KEY_FILE"] = str(d / "k")
            args = types.SimpleNamespace(control_root=str(control), approval="A", nonce_root=str(d / "n"),
                                         qwen=str(qwen), proxy=str(proxy), listen_port=1, max_wall_seconds=10)
            self.assertEqual(R.run(args), 2)
            term = json.loads((control / "run-terminal.json").read_text())
            self.assertEqual(term["status"], "qwen_version_unverified")
            self.assertFalse((control / "qwen-runtime.json").exists())
        finally:
            R.load_manifest, R.validate_manifest = saved
            os.environ.pop("SHERLOCK_API_KEY_FILE", None)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, signal.SIG_DFL)
            shutil.rmtree(d)


class RelaunchAfterRefusalTest(unittest.TestCase):
    """A refused launch (busy port) leaves qwen-runtime.json; a relaunch must still pass that step."""
    def test_busy_port_then_relaunch(self):
        import socket
        d = Path(tempfile.mkdtemp())
        saved = (R.load_manifest, R.validate_manifest, R.preflight_skill_list)
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(1); port = sock.getsockname()[1]
        try:
            qwen = d / "qwen"; qwen.write_text("#!/bin/sh\necho 0.22.0\n"); qwen.chmod(0o755)
            proxy = d / "proxy.py"; proxy.write_text("")
            control = d / "control"; control.mkdir()
            manifest = {"model": "gpt-5.5", "package": "v52", "proxy": str(proxy.resolve()),
                        "qwen": str(qwen.resolve()), "upstream_base": "http://127.0.0.1:8317/v1", "prepared_root": str(d)}
            R.load_manifest = lambda c: (manifest, "A")
            R.validate_manifest = lambda c, m: None
            reached = []
            def preflight(*a):
                reached.append(True); raise R.Refusal("stop after port check (test)")
            R.preflight_skill_list = preflight
            os.environ["SHERLOCK_API_KEY_FILE"] = str(d / "k")
            args = types.SimpleNamespace(control_root=str(control), approval="A", nonce_root=str(d / "n"),
                                         qwen=str(qwen), proxy=str(proxy), listen_port=port, max_wall_seconds=10)
            self.assertEqual(R.run(args), 2)
            self.assertTrue((control / "qwen-runtime.json").is_file())
            self.assertEqual(reached, [])
            sock.close()
            (control / "run-terminal.json").unlink(); (control / "run.done").unlink(missing_ok=True)
            self.assertEqual(R.run(args), 2)
            self.assertEqual(reached, [True])  # got past qwen-runtime.json and the port check
            term = json.loads((control / "run-terminal.json").read_text())
            self.assertIn("stop after port check", term["error"])
            rt = json.loads((control / "qwen-runtime.json").read_text())
            self.assertEqual((rt["qwen_version"], rt["approval_mode"]), ("0.22.0", "yolo"))
        finally:
            sock.close()
            R.load_manifest, R.validate_manifest, R.preflight_skill_list = saved
            os.environ.pop("SHERLOCK_API_KEY_FILE", None)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, signal.SIG_DFL)
            shutil.rmtree(d)


class ReceiptClassifierTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp()) / "work"
        (self.work / "validation").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.work.parent)

    def attempt(self, aid, complete=True, verdict="clean"):
        p = self.work / "validation" / aid; p.mkdir()
        gates = {g: {"exit_code": 0, "parsed_blocking": 0} for g in R.FINALIZER_GATES}
        m = {"attempt_id": aid, "gates": gates if complete else {"reportcheck": gates["reportcheck"]}}
        if complete: m.update(verdict=verdict, finished_at="x")
        (p / "metadata.json").write_text(json.dumps(m))

    def test_incomplete_newer_than_complete_is_killed(self):
        self.attempt("20260925T063048Z-a"); self.attempt("20260925T063112Z-b", complete=False)
        complete, incomplete = R.classify_receipts(self.work)
        self.assertEqual([a for a, _ in complete], ["20260925T063048Z-a"])
        self.assertEqual([a for a, _ in incomplete], ["20260925T063112Z-b"])
        self.assertEqual(R.killed_receipt(self.work), "20260925T063112Z-b")

    def test_incomplete_older_than_complete_is_ignored(self):
        self.attempt("20260925T060000Z-a", complete=False); self.attempt("20260925T063048Z-b")
        self.assertIsNone(R.killed_receipt(self.work))

    def test_unreadable_receipt_is_incomplete(self):
        p = self.work / "validation" / "20260925T070000Z-c"; p.mkdir()
        (p / "metadata.json").write_text("{")
        self.assertEqual(R.killed_receipt(self.work), "20260925T070000Z-c")


class HookLogTerminalTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.log = self.d / "stop-hook.jsonl"
        self.t = time.time()

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, *rows):
        self.log.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_start_end_is_ok(self):
        self.write({"event": "start", "invocation": "i", "ts": iso(self.t)},
                   {"event": "end", "invocation": "i", "ts": iso(self.t), "decision": "allow"})
        self.assertIsNone(R.hook_log_terminal(self.log, self.t - 5, self.t - 1))

    def test_start_without_end_is_killed(self):
        self.write({"event": "start", "invocation": "i", "ts": iso(self.t)})
        self.assertEqual(R.hook_log_terminal(self.log, self.t - 5, self.t - 1)[0], "stop_hook_killed")

    def test_kill_row_is_killed(self):
        self.write({"event": "start", "invocation": "i", "ts": iso(self.t)},
                   {"event": "kill", "invocation": "i", "ts": iso(self.t), "signal": "SIGTERM", "elapsed_ms": 9969})
        st, det = R.hook_log_terminal(self.log, self.t - 5, self.t - 1)
        self.assertEqual((st, det["signal"]), ("stop_hook_killed", "SIGTERM"))

    def test_final_session_without_end_is_unlogged(self):
        self.write({"ts": iso(self.t - 100), "decision": "allow"})  # legacy row, earlier session
        self.assertEqual(R.hook_log_terminal(self.log, self.t - 200, self.t - 10)[0], "stop_hook_unlogged")
        self.log.unlink()
        self.assertEqual(R.hook_log_terminal(self.log, self.t - 200, self.t - 10)[0], "stop_hook_unlogged")

    def test_previous_session_row_just_before_final_start_is_not_final(self):
        # v53 small test: S2 hook row ts 06:29:53.159, S3 started 06:29:53.854, S3 wrote no row.
        self.write({"ts": iso(self.t - 0.695), "decision": "allow"})
        self.assertEqual(R.hook_log_terminal(self.log, self.t - 100, self.t)[0], "stop_hook_unlogged")

    def test_continuation_ignores_start_rows(self):
        work = self.d / "work"; work.mkdir()
        (work / "handoff.txt").write_text("1) /clear\n2) /sherlock ПРОДОЛЖИ X\n")
        self.write({"event": "end", "invocation": "i", "ts": iso(time.time()), "decision": "allow",
                    "reason": "stage handoff accepted"},
                   {"event": "start", "invocation": "j", "ts": iso(time.time())})
        self.assertEqual(R.continuation_prompt(work, self.log, time.time() - 5), "/sherlock ПРОДОЛЖИ X")


class WrapperSignalTest(unittest.TestCase):
    def test_sigterm_logs_start_and_kill_and_kills_child(self):
        d = Path(tempfile.mkdtemp()); log = d / "h.jsonl"; pidf = d / "child.pid"
        try:
            env = dict(os.environ, SHERLOCK_STOP_HOOK_LOG=str(log))
            p = subprocess.Popen([sys.executable, str(WRAPPER), "sh", "-c", "echo $$ > %s; exec sleep 30" % pidf],
                                 stdin=subprocess.PIPE, env=env)
            p.stdin.write(b"{}"); p.stdin.close()
            for _ in range(100):
                if pidf.exists() and pidf.read_text().strip(): break
                time.sleep(0.05)
            time.sleep(0.2)
            p.send_signal(signal.SIGTERM); rc = p.wait(10)
            rows = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual([r["event"] for r in rows], ["start", "kill"])
            self.assertEqual(rows[0]["invocation"], rows[1]["invocation"])
            self.assertEqual(rows[1]["signal"], "SIGTERM")
            self.assertEqual(rc, 128 + signal.SIGTERM)
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidf.read_text()), 0)
        finally:
            shutil.rmtree(d)

    def test_normal_exit_logs_start_and_end(self):
        d = Path(tempfile.mkdtemp()); log = d / "h.jsonl"
        try:
            env = dict(os.environ, SHERLOCK_STOP_HOOK_LOG=str(log))
            out = subprocess.run([sys.executable, str(WRAPPER), "sh", "-c", "cat >/dev/null; echo '{\"decision\":\"allow\"}'"],
                                 input=b"{}", env=env, capture_output=True, timeout=10)
            self.assertEqual(out.returncode, 0)
            self.assertIn(b'"allow"', out.stdout)
            rows = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual([r["event"] for r in rows], ["start", "end"])
            self.assertEqual(rows[1]["decision"], "allow")
        finally:
            shutil.rmtree(d)


FAKE_FINALIZE = r'''
import json, sys, os, pathlib
a = sys.argv; work = pathlib.Path(a[a.index("--work") + 1])
mode = os.environ.get("FAKE_MODE", pathlib.Path(work, "mode").read_text().strip())
att = work / "validation" / "20990101T000000Z-launcher"; att.mkdir(parents=True)
import hashlib
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
m = {"verdict": "clean" if mode == "clean" else "blocking", "finished_at": "x",
     "inputs_changed_during_validation": False, "argv": a,
     "gates": {g: {"exit_code": 0, "parsed_blocking": 0 if mode == "clean" else 1}
               for g in ("reportcheck", "citecheck", "triagecheck", "statecheck")},
     "inputs_after": {"report": {"sha256": sha(work / "report.md")},
                      "worklist": {"sha256": sha(work / "worklist.tsv")},
                      "rules": {"sha256": sha(work / "rules.tsv")}}}
(att / "metadata.json").write_text(json.dumps(m))
print(json.dumps({"attempt": str(att), "verdict": m["verdict"]}))
'''


class FinalDecisionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()); R.use_package("v53")
        tools = self.root / "skills/v53/tools"; tools.mkdir(parents=True)
        (tools / "finalize.py").write_text(FAKE_FINALIZE)
        self.work = self.root / "work"; (self.work / "validation").mkdir(parents=True)
        for n in ("report.md", "worklist.tsv", "rules.tsv"): (self.work / n).write_text(n)
        self.control = self.root / "control"; (self.control / "trace").mkdir(parents=True)
        self.t = time.time()

    def tearDown(self):
        R.use_package("v52"); shutil.rmtree(self.root)

    def hook(self, *rows):
        (self.control / "trace/stop-hook.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def ok_hook(self):
        self.hook({"event": "start", "invocation": "i", "ts": iso(self.t)},
                  {"event": "end", "invocation": "i", "ts": iso(self.t), "decision": "allow"})

    def decide(self, mode):
        (self.work / "mode").write_text(mode)
        return R.final_decision(self.root, self.control, self.t - 10, self.t - 5)

    def test_clean_gate_completes_and_records(self):
        self.ok_hook()
        st, det = self.decide("clean")
        self.assertEqual(st, "completed")
        self.assertEqual(det["final_gate"]["verdict"], "clean")
        self.assertIn("--require-index", det["final_gate"]["argv"])
        self.assertTrue((self.control / "final-gate/metadata.json").is_file())
        self.assertTrue((self.control / "final-gate/result.json").is_file())

    def test_blocking_gate_fails_even_with_clean_hook(self):
        self.ok_hook()
        self.assertEqual(self.decide("blocking")[0], "validation_failed")

    def test_killed_hook_beats_clean_gate(self):
        self.hook({"event": "start", "invocation": "i", "ts": iso(self.t)},
                  {"event": "kill", "invocation": "i", "ts": iso(self.t), "signal": "SIGTERM"})
        st, det = self.decide("clean")
        self.assertEqual(st, "stop_hook_killed")
        self.assertEqual(det["final_gate"]["verdict"], "clean")

    def test_unlogged_final_session(self):
        self.hook({"ts": iso(self.t - 8), "decision": "allow"})
        self.assertEqual(self.decide("clean")[0], "stop_hook_unlogged")

    def test_partial_receipt_is_stop_hook_killed(self):
        self.ok_hook()
        p = self.work / "validation" / "20260925T063112Z-x"; p.mkdir()
        (p / "metadata.json").write_text(json.dumps({"gates": {"reportcheck": {}}}))
        st, det = self.decide("clean")
        self.assertEqual((st, det["killed_attempt"]), ("stop_hook_killed", "20260925T063112Z-x"))


if __name__ == "__main__":
    unittest.main()
