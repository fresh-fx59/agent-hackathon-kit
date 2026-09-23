#!/usr/bin/env python3
"""Focused tests: staged skill parent, skill preflight, named finalizer error,
Stop-hook decision log, and the proxy startup banner."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parent.parent
SPEC = importlib.util.spec_from_file_location("runner_obs", HERE / "run-v52-gpt55-comparison.py")
RUNNER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RUNNER)
WRAPPER = HERE / "stop-hook-log.py"
PROXY = SHERLOCK / "measure" / "upstream-log-proxy.py"
STOPCHECK = SHERLOCK / "skills" / "v52" / "tools" / "stopcheck.py"


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-obs-"))
    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def prepared(self):
        p = self.root / "prepared"
        (p / ".qwen").mkdir(parents=True)
        (p / "skills/v52/tools").mkdir(parents=True)
        (p / "skills/v52/SKILL.md").write_text("---\nname: sherlock\n---\n", encoding="utf-8")
        (p / "skills/v52/tools/stopcheck.py").write_text("# stub\n", encoding="utf-8")
        (p / "corpus-src").mkdir(); (p / "corpus-src/e.json").write_text("{}\n")
        (p / "corpus").symlink_to(p / "corpus-src", target_is_directory=True)
        (p / "prompt.txt").write_text("prompt\n")
        settings = {"model": {"sessionTokenLimit": 230000, "generationConfig": {
            "contextWindowSize": 262000, "reasoning": False,
            "extra_body": {"thinking": {"type": "disabled"}},
            "samplingParams": {"max_tokens": 20000}, "timeout": 660000, "maxRetries": 0}},
            "skills": {"directories": [str(p / "skills/v52")]},
            "hooks": {"Stop": [{"hooks": [{"type": "command",
                      "command": "python3 \"$QWEN_SKILL_ROOT/tools/stopcheck.py\""}]}]}}
        (p / ".qwen/settings.json").write_text(json.dumps(settings))
        return p


class SkillRootTest(Base):
    def test_staging_points_qwen_at_parent_and_leaves_skill_tree_identical(self):
        p = self.prepared(); before = tree_hash(p / "skills/v52")
        with self.assertRaisesRegex(RUNNER.Refusal, "skills-root"):
            RUNNER.prepared_inventory(p)
        RUNNER.stage_harness_layout(p); RUNNER.stage_harness_layout(p)  # idempotent
        settings = json.loads((p / ".qwen/settings.json").read_text())
        self.assertEqual(settings["skills"]["directories"], [str(p.resolve() / "skills-root")])
        self.assertEqual(settings["hooks"]["Stop"][0]["hooks"][0]["command"], RUNNER.STOP_HOOK_COMMAND)
        self.assertEqual((p / "skills-root/sherlock/SKILL.md").resolve(),
                         (p / "skills/v52/SKILL.md").resolve())
        rows = RUNNER.prepared_inventory(p)[1]
        self.assertIn({"path": "skills-root/sherlock", "kind": "symlink", "target": "../skills/v52"}, rows)
        self.assertEqual(before, tree_hash(p / "skills/v52"))

    def test_wrong_link_or_extra_entry_is_refused(self):
        p = self.prepared(); RUNNER.stage_harness_layout(p)
        (p / "skills-root/other").mkdir()
        with self.assertRaisesRegex(RUNNER.Refusal, "exactly one"):
            RUNNER.prepared_inventory(p)
        (p / "skills-root/other").rmdir()
        (p / "skills-root/sherlock").unlink()
        (p / "skills-root/sherlock").symlink_to(p / "corpus-src", target_is_directory=True)
        with self.assertRaisesRegex(RUNNER.Refusal, "symlink to skills/v52"):
            RUNNER.prepared_inventory(p)


class PreflightTest(Base):
    def fake_qwen(self, body):
        q = self.root / "fake-qwen.py"
        q.write_text("import json,os,sys,time\n" + body)
        return [sys.executable, str(q)]

    def test_preflight_passes_when_sherlock_listed_and_never_uses_real_upstream(self):
        qwen = self.fake_qwen(
            "assert os.environ['OPENAI_BASE_URL']=='http://127.0.0.1:9/v1'\n"
            "print(json.dumps({'type':'system','slash_commands':['clear','sherlock']}),flush=True)\n"
            "time.sleep(30)\n")
        control = self.root / "control"; control.mkdir()
        t = time.monotonic(); RUNNER.preflight_skill_list(qwen, self.root, control)
        self.assertLess(time.monotonic() - t, 10)
        receipt = json.loads((control / "skill-preflight.json").read_text())
        self.assertTrue(receipt["passed"]); self.assertIn("sherlock", receipt["slash_commands"])

    def test_preflight_fails_fast_when_skill_missing_or_no_init(self):
        for body, needle in (
                ("print(json.dumps({'type':'system','slash_commands':['clear']}))\n", "clear"),
                ("print('not json')\n", "no init event")):
            control = self.root / ("c%d" % len(list(self.root.glob("c*")))); control.mkdir()
            with self.assertRaisesRegex(RUNNER.Refusal, "not in Qwen's skill list.*" + needle):
                RUNNER.preflight_skill_list(self.fake_qwen(body), self.root, control)
            self.assertFalse(json.loads((control / "skill-preflight.json").read_text())["passed"])


class PreflightHangTest(PreflightTest):
    def test_silent_qwen_is_killed_at_the_deadline(self):
        old = RUNNER.PREFLIGHT_TIMEOUT_S; RUNNER.PREFLIGHT_TIMEOUT_S = 1
        try:
            control = self.root / "hang"; control.mkdir(); t = time.monotonic()
            with self.assertRaisesRegex(RUNNER.Refusal, "no init event"):
                RUNNER.preflight_skill_list(self.fake_qwen("time.sleep(60)\n"), self.root, control)
            self.assertLess(time.monotonic() - t, 10)
        finally:
            RUNNER.PREFLIGHT_TIMEOUT_S = old


class FinalizerErrorTest(Base):
    def test_missing_receipt_names_step_work_and_hook_decision(self):
        work = self.root / "work"; work.mkdir(); (work / "report.md").write_text("x")
        control = self.root / "control"; (control / "trace").mkdir(parents=True)
        (control / "qwen-output.json").write_text(json.dumps([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "write_file", "input": {}}]}}]))
        (control / "trace/stop-hook.jsonl").write_text(json.dumps(
            {"decision": "allow", "reason": "Sherlock inactive"}) + "\n")
        with self.assertRaises(RUNNER.TerminalFailure) as raised:
            RUNNER.validate_finalizer_receipt(work, control)
        msg = str(raised.exception)
        for needle in ("skill never invoked", "skill_invocations=0", "report.md",
                       "allow:Sherlock inactive"):
            self.assertIn(needle, msg)
        self.assertEqual(raised.exception.details["work_contents"], ["report.md"])

    def test_skill_invoked_but_finalizer_not_run(self):
        work = self.root / "work"; work.mkdir()
        control = self.root / "control"; control.mkdir()
        (control / "qwen-output.json").write_text(json.dumps([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "skill", "input": {"skill": "sherlock"}}]}}]))
        with self.assertRaisesRegex(RUNNER.TerminalFailure, "finalize.py never ran.*stop_hook=None"):
            RUNNER.validate_finalizer_receipt(work, control)

    def test_terminal_record_carries_diagnostics(self):
        control = self.root / "control"; control.mkdir()
        RUNNER.terminal(control, "validation_failed", error="e", qwen_exit=0,
                        diagnostics={"missing_step": "skill never invoked"})
        row = json.loads((control / "run-terminal.json").read_text())
        self.assertEqual(row["diagnostics"]["missing_step"], "skill never invoked")


class StopHookWrapperTest(Base):
    def run_wrapper(self, cmd, stdin=b'{"hook_event_name":"Stop","session_id":"s1"}'):
        log = self.root / "hook.jsonl"
        env = dict(os.environ, SHERLOCK_STOP_HOOK_LOG=str(log))
        proc = subprocess.run([sys.executable, str(WRAPPER)] + cmd, input=stdin,
                              capture_output=True, env=env, cwd=self.root)
        rows = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
        return proc, rows

    def test_block_and_allow_pass_through_unchanged_and_are_logged(self):
        for decision, rc in (("block", 0), ("allow", 3)):
            hook = self.root / ("%s.py" % decision)
            hook.write_text("import sys,json\nsys.stdin.read()\nsys.stderr.write('E')\n"
                            "print(json.dumps({'decision':%r,'reason':'R'}))\nsys.exit(%d)\n" % (decision, rc))
            direct = subprocess.run([sys.executable, str(hook)], input=b"{}", capture_output=True)
            proc, rows = self.run_wrapper([sys.executable, str(hook)])
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr),
                             (direct.returncode, direct.stdout, direct.stderr))
            row = rows[-1]
            self.assertEqual((row["decision"], row["reason"], row["exit_code"]), (decision, "R", rc))
            self.assertEqual(row["input"]["hook_event_name"], "Stop")

    def test_real_v52_stopcheck_inactive_allow_is_logged(self):
        proc, rows = self.run_wrapper([sys.executable, str(STOPCHECK)], stdin=b"{}")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(rows[-1]["decision"], "allow")

    def test_unwritable_log_never_changes_result(self):
        hook = self.root / "h.py"; hook.write_text("print('{\"decision\":\"allow\"}')\n")
        env = dict(os.environ, SHERLOCK_STOP_HOOK_LOG=str(self.root / "missing-dir/x.jsonl"))
        proc = subprocess.run([sys.executable, str(WRAPPER), sys.executable, str(hook)],
                              input=b"", capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0); self.assertIn(b"allow", proc.stdout)
        self.assertIn(b"could not append", proc.stderr)


class ProxyBannerTest(Base):
    def test_banner_prints_route_file_base_not_env_default(self):
        key = self.root / "key"; key.write_text("dummy\n")
        route = self.root / "route.json"
        route.write_text(json.dumps({"schema": 1, "base": "http://127.0.0.1:8317/v1", "model": "gpt-5.5",
                                     "expected_returned_identity": "gpt-5.5", "key_file": str(key),
                                     "generation": 1}))
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        env = dict(os.environ, LISTEN_PORT=str(port), UPSTREAM_ROUTE_FILE=str(route),
                   UPSTREAM_LOG=str(self.root / "up.jsonl"), UPSTREAM_BODY_DIR=str(self.root / "b"))
        env.pop("UPSTREAM_BASE", None)
        proc = subprocess.Popen([sys.executable, str(PROXY)], stderr=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, env=env)
        try:
            line = proc.stderr.readline().decode()
        finally:
            proc.kill(); proc.wait()
        self.assertIn("-> http://127.0.0.1:8317/v1 [from route file]", line)
        self.assertNotIn("linkapi", line)


if __name__ == "__main__":
    unittest.main()
