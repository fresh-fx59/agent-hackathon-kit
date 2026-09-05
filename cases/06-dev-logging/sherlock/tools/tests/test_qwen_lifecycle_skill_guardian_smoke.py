#!/usr/bin/env python3
"""Regression coverage for the installed-Qwen lifecycle loopback fixture itself."""
import importlib.util
from pathlib import Path
import sys
import os
import signal
import subprocess
import time
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "tools/tests/qwen_lifecycle_skill_guardian_smoke.py"
HELPER = ROOT / "eval/bench/lifecycle-supervisor.py"


def load_smoke():
    spec = importlib.util.spec_from_file_location("skill_guardian_smoke_test", SMOKE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SkillGuardianSmokeTest(unittest.TestCase):
    def test_client_timeout_persists_failed_case_and_terminates_client(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_qwen = root / "qwen-sleeper.py"
            fake_qwen.write_text("""#!/usr/bin/env python3
import json, os, sys, time, urllib.request
if "--version" in sys.argv:
    print("fixture")
    raise SystemExit(0)
request = urllib.request.Request(os.environ["OPENAI_BASE_URL"] + "/chat/completions", data=json.dumps({}).encode(), method="POST")
try:
    urllib.request.urlopen(request, timeout=2).read()
except Exception:
    pass
time.sleep(30)
""")
            fake_qwen.chmod(0o700)
            evidence = root / "evidence"; evidence.mkdir()
            result = smoke.run_case(fake_qwen, evidence, HELPER, "skill", client_timeout_seconds=0.1)
            self.assertEqual("TimeoutExpired", result["exception"]["type"])
            self.assertIsNotNone(result["exit_code"])
            self.assertTrue((root / "evidence/skill/result.json").is_file())
            self.assertEqual(smoke.digest(HELPER), result["lifecycle_helper_sha256"])


    def test_process_group_termination_stops_orphaned_child(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "child.pid"
            program = ("import os,pathlib,time; p=os.fork(); "
                       "(pathlib.Path(" + repr(str(pid_file)) + ").write_text(str(p)) if p else time.sleep(30)); "
                       "time.sleep(30) if p else os._exit(0)")
            leader = subprocess.Popen([sys.executable, "-c", program], start_new_session=True)
            for _ in range(100):
                if pid_file.exists(): break
                time.sleep(0.01)
            child = int(pid_file.read_text())
            smoke.terminate_process_group(leader)
            leader.wait(timeout=2)
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)

    def test_invalid_directory_mode_configures_post_tool_batch(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / "evidence"; evidence.mkdir()
            fake = root / "qwen-sleeper.py"; fake.write_text("#!/usr/bin/env python3\nimport sys,time\nif '--version' in sys.argv: print('fixture'); raise SystemExit(0)\ntime.sleep(30)\n"); fake.chmod(0o700)
            result = smoke.run_case(fake, evidence, HELPER, "invalid-then-repaired", registration_timeout_seconds=.05, client_timeout_seconds=.1)
            settings = __import__("json").loads((evidence / "invalid-then-repaired/settings.input.json").read_text())
            self.assertIn("PostToolBatch", settings["hooks"])

    def test_post_tool_batch_negative_modes_keep_the_required_hook_boundary(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / "evidence"; evidence.mkdir()
            fake = root / "qwen-sleeper.py"
            fake.write_text("#!/usr/bin/env python3\nimport sys,time\nif '--version' in sys.argv: print('fixture'); raise SystemExit(0)\ntime.sleep(30)\n")
            fake.chmod(0o700)
            for mode in ("invalid-no-batch", "executed-no-batch", "batch-no-execution-hooks"):
                smoke.run_case(fake, evidence, HELPER, mode,
                               registration_timeout_seconds=.05, client_timeout_seconds=.1)
            rejected_settings = __import__("json").loads(
                (evidence / "invalid-no-batch/settings.input.json").read_text())
            batch_only_settings = __import__("json").loads(
                (evidence / "batch-no-execution-hooks/settings.input.json").read_text())
            executed_settings = __import__("json").loads(
                (evidence / "executed-no-batch/settings.input.json").read_text())
            self.assertNotIn("PostToolBatch", rejected_settings["hooks"])
            self.assertNotIn("PostToolBatch", executed_settings["hooks"])
            self.assertIn("PreToolUse", executed_settings["hooks"])
            self.assertIn("PostToolUse", executed_settings["hooks"])
            self.assertEqual(["PostToolBatch"], sorted(batch_only_settings["hooks"]))

    def test_successful_tool_modes_install_post_tool_batch(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / "evidence"; evidence.mkdir()
            fake = root / "qwen-sleeper.py"
            fake.write_text("#!/usr/bin/env python3\nimport sys,time\nif '--version' in sys.argv: print('fixture'); raise SystemExit(0)\ntime.sleep(30)\n")
            fake.chmod(0o700)
            for mode in ("skill", "slow-shell"):
                smoke.run_case(fake, evidence, HELPER, mode,
                               registration_timeout_seconds=.05, client_timeout_seconds=.1)
                settings = __import__("json").loads(
                    (evidence / mode / "settings.input.json").read_text())
                self.assertIn("PostToolBatch", settings["hooks"])

    def test_invalid_directory_mode_registers_rejected_and_repaired_calls(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / "evidence"; evidence.mkdir()
            fake = root / "qwen-three.py"
            fake.write_text("""#!/usr/bin/env python3
import json, os, sys, time, urllib.request
if "--version" in sys.argv: print("fixture"); raise SystemExit(0)
for _ in range(3):
 try: urllib.request.urlopen(urllib.request.Request(os.environ["OPENAI_BASE_URL"]+"/chat/completions",data=b"{}",method="POST"),timeout=2).read()
 except Exception: pass
time.sleep(.5)
"""); fake.chmod(0o700)
            result = smoke.run_case(fake, evidence, HELPER, "invalid-then-repaired", client_timeout_seconds=2)
            expected = (evidence / "invalid-then-repaired" / "observer-invalid-then-repaired-nonce-20260906" / "expected-tools.jsonl").read_text()
            self.assertIn("call_repaired", expected)
            # The proxy must register both provider IDs before relaying each
            # response.  PostToolBatch discharges only the prevalidation
            # rejection; it does not make an unregistered call disappear.
            self.assertIn("call_invalid", expected)
            self.assertTrue((evidence / "invalid-then-repaired/response-1.sse").is_file())

    def test_negative_modes_register_the_exact_provider_ids_before_relay(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / "evidence"; evidence.mkdir()
            fake = root / "qwen-two.py"
            fake.write_text("""#!/usr/bin/env python3
import os, sys, time, urllib.request
if "--version" in sys.argv: print("fixture"); raise SystemExit(0)
for _ in range(2):
 try: urllib.request.urlopen(urllib.request.Request(os.environ["OPENAI_BASE_URL"] + "/chat/completions", data=b"{}", method="POST"), timeout=2).read()
 except Exception: pass
time.sleep(.2)
""")
            fake.chmod(0o700)
            for mode, call_id in (("invalid-no-batch", "call_invalid"),
                                  ("executed-no-batch", "call_executed-no-batch"),
                                  ("batch-no-execution-hooks", "call_executed_without_hooks")):
                smoke.run_case(fake, evidence, HELPER, mode, client_timeout_seconds=2)
                expected = (evidence / mode / ("observer-" + mode + "-nonce-20260906") /
                            "expected-tools.jsonl").read_text()
                self.assertIn(call_id, expected)

    def test_missing_hook_mode_omits_hooks_and_refuses_next_dispatch(self):
        smoke = load_smoke()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_qwen = root / "qwen-two-requests.py"
            fake_qwen.write_text("""#!/usr/bin/env python3
import json, os, sys, urllib.request
if "--version" in sys.argv:
    print("fixture")
    raise SystemExit(0)
for _ in range(2):
    request = urllib.request.Request(os.environ["OPENAI_BASE_URL"] + "/chat/completions", data=json.dumps({}).encode(), method="POST")
    try:
        urllib.request.urlopen(request, timeout=2).read()
    except Exception:
        pass
time.sleep(0.5)
""")
            fake_qwen.chmod(0o700)
            evidence = root / "evidence"; evidence.mkdir()
            result = smoke.run_case(fake_qwen, evidence, HELPER, "missing-hook", client_timeout_seconds=2)
            settings = __import__("json").loads((evidence / "missing-hook/settings.input.json").read_text())
            self.assertEqual({}, settings["hooks"])
            self.assertTrue(result["dispatch_error"])
            self.assertFalse((evidence / "missing-hook/response-1.sse").exists())


if __name__ == "__main__":
    unittest.main()
