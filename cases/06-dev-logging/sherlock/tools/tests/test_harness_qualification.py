#!/usr/bin/env python3
"""Provider-free behavior tests for Sherlock harness qualification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
BENCH = SHERLOCK / "eval" / "bench"
TOOL = BENCH / "harness-qualification.py"
LAUNCHER = BENCH / "run-harness-qualification.sh"
MANIFEST_TEST = HERE / "test_run_manifest.py"

EXPECTED_ROWS = [
    {"id": "admission_refusal", "expected": {"exit": 2, "failure": "HARNESS_QUALIFICATION_MISSING"}, "observed": {"exit": 2, "failure": "HARNESS_QUALIFICATION_MISSING"}, "passed": True},
    {"id": "artifact_tamper", "expected": {"exit": 2, "failure": "TRACE_INVALID"}, "observed": {"exit": 2, "failure": "TRACE_INVALID"}, "passed": True},
    {"id": "attempt_exit", "expected": {"exit": 9, "failure": "ATTEMPT_NONZERO"}, "observed": {"exit": 9, "failure": "ATTEMPT_NONZERO"}, "passed": True},
    {"id": "citation_gate_failure", "expected": {"exit": 4, "failure": "CITATION_GATE_FAILED"}, "observed": {"exit": 4, "failure": "CITATION_GATE_FAILED"}, "passed": True},
    {"id": "driver_exit", "expected": {"exit": 9, "failure": "DRIVER_NONZERO"}, "observed": {"exit": 9, "failure": "DRIVER_NONZERO"}, "passed": True},
    {"id": "lifecycle_transition", "expected": {"exit": 2, "failure": "INVALID_TRANSITION"}, "observed": {"exit": 2, "failure": "INVALID_TRANSITION"}, "passed": True},
    {"id": "report_gate_failure", "expected": {"exit": 4, "failure": "REPORT_GATE_FAILED"}, "observed": {"exit": 4, "failure": "REPORT_GATE_FAILED"}, "passed": True},
    {"id": "state_gate_failure", "expected": {"exit": 4, "failure": "STATE_GATE_FAILED"}, "observed": {"exit": 4, "failure": "STATE_GATE_FAILED"}, "passed": True},
    {"id": "timeout", "expected": {"exit": 124, "failure": "TIMEOUT"}, "observed": {"exit": 124, "failure": "TIMEOUT"}, "passed": True},
    {"id": "triage_gate_failure", "expected": {"exit": 4, "failure": "TRIAGE_GATE_FAILED"}, "observed": {"exit": 4, "failure": "TRIAGE_GATE_FAILED"}, "passed": True},
    {"id": "wrapper_exit", "expected": {"exit": 2, "failure": "WRAPPER_NONZERO"}, "observed": {"exit": 2, "failure": "WRAPPER_NONZERO"}, "passed": True},
]
FAULT_CASES = [
    ("admission_refusal", 2, "HARNESS_QUALIFICATION_MISSING", False),
    ("artifact_tamper", 2, "TRACE_INVALID", False),
    ("attempt_exit", 9, "ATTEMPT_NONZERO", False),
    ("citation_gate_failure", 4, "CITATION_GATE_FAILED", False),
    ("driver_exit", 9, "DRIVER_NONZERO", False),
    ("lifecycle_transition", 2, "INVALID_TRANSITION", False),
    ("report_gate_failure", 4, "REPORT_GATE_FAILED", False),
    ("state_gate_failure", 4, "STATE_GATE_FAILED", False),
    ("timeout", 124, "TIMEOUT", True),
    ("triage_gate_failure", 4, "TRIAGE_GATE_FAILED", False),
    ("wrapper_exit", 2, "WRAPPER_NONZERO", False),
]
BINDING_NAMES = [
    "implementation_commit", "implementation_dirty", "runner", "driver", "proxy",
    "run_manifest_tool", "run_verdict_tool", "test_manifest", "qwen_binary",
    "qwen_version", "arm", "skill_v44", "report_contract", "report_gate_program",
    "report_gate_result", "citation_gate_program", "citation_gate_result",
    "state_gate_program", "state_gate_result", "triage_gate_program",
    "triage_gate_result", "settings", "tool_schema", "input_manifest",
    "terminal_verdict",
]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha(data):
    return hashlib.sha256(data).hexdigest()

def canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def write_json(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(row) + b"\n")

def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)

def fault_program(case_id, exit_code, failure, sleeps=False, sleep_seconds=1):
    pause = f"sleep {sleep_seconds}" if sleeps else ":"
    payload = json.dumps({"failure": failure, "id": case_id, "schema": 1},
                         sort_keys=True, separators=(",", ":"))
    return textwrap.dedent(f"""\
        #!/bin/sh
        set -C
        umask 077
        printf '%s\\n' '{case_id}' > "$SHERLOCK_FAULT_RECEIPT" || exit 125
        {pause}
        printf '%s\\n' '{payload}'
        exit {exit_code}
        """)

def build_fault_fixtures(root):
    root.mkdir(parents=True)
    (root / "programs").mkdir()
    rows = []
    for case_id, exit_code, failure, sleeps in FAULT_CASES:
        executable(root / "programs" / (case_id + ".py"),
                   fault_program(case_id, exit_code, failure, sleeps))
        rows.append({"id": case_id, "program": "programs/" + case_id + ".py",
                     "expected": {"exit": exit_code, "failure": failure}})
    write_json(root / "manifest.json",
               {"schema": 1, "timeout_seconds": 0.25, "faults": rows})
    return root

class TraceFixture:
    """Real schema-3 manifest plus a real repository terminal verdict."""
    def __init__(self, root):
        self.root = Path(root)
        self.trace = self.root / "trace"
        self.assets = self.root / "assets"
        self.assets.mkdir(parents=True)
        manifest_tests = load("hq_manifest_" + self.root.name, MANIFEST_TEST)
        (self.root / "authority").mkdir()
        self.fx = manifest_tests.Fixture(self.root / "authority")
        self._assets()
        self.fx.stage()
        self.manifest = self.fx.create(
            trace=str(self.trace), run_tag="free-run-001", arm="v44",
            runner=str(self.paths["runner"]), scorer=str(self.paths["driver"]),
            skill_root=str(self.skill), triage_checker=str(self.paths["triage_gate_program"]),
            stop_checker=str(self.skill / "tools" / "stopcheck.py"),
            citation_checker=str(self.paths["citation_gate_program"]),
            target_cli=str(self.paths["qwen_binary"]), target_version="0.21.1",
            requested_model="gpt-5.5", expected_returned_identity="gpt-5.5",
            provider="cliproxyapi", lane="subscription")
        identity = self.manifest["input_identity"]
        write_json(self.paths["arm"], {"schema": 1, "arm": identity["arm"],
                   "commit": identity["arm_commit"], "tree": identity["arm_tree"]})
        self._terminal()
        self._control()

    def _copy(self, source, name):
        target = self.assets / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def _assets(self):
        self.skill = self.assets / "skill-v44"
        shutil.copytree(SHERLOCK / "skills" / "v44", self.skill)
        tool_tree = self.skill / "qualification-tools" / "runtime" / "repo" / "eval" / "bench"
        for helper in ("run-manifest.py", "run-verdict.py", "bench-status.py", "validate-run.py"):
            target = tool_tree / helper; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(BENCH / helper, target)
        subprocess.run(["git", "init", "-q", str(self.skill)], check=True)
        subprocess.run(["git", "-C", str(self.skill), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.skill), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture.invalid", "commit", "-qm", "fixture"], check=True)
        qwen = self.assets / "qwen"
        executable(qwen, "#!/bin/sh\nprintf '%s\\n' 'fake model boundary'\n")
        self.paths = {
            "implementation_commit": self.assets / "implementation-commit.txt",
            "implementation_dirty": self.assets / "implementation-dirty.txt",
            "runner": self._copy(BENCH / "run-bench.sh", "run-bench.sh"),
            "driver": self._copy(SHERLOCK / "measure" / "interactive-drive.py", "interactive-drive.py"),
            "proxy": self._copy(SHERLOCK / "measure" / "upstream-log-proxy.py", "upstream-log-proxy.py"),
            "run_manifest_tool": tool_tree / "run-manifest.py",
            "run_verdict_tool": tool_tree / "run-verdict.py",
            "test_manifest": self.assets / "provider-free-tests.json",
            "qwen_binary": qwen, "qwen_version": self.assets / "qwen-version.txt",
            "arm": self.assets / "arm.json", "skill_v44": self.skill / "SKILL.md",
            "report_contract": self.skill / "reference" / "report-contract.corporate.json",
            "report_gate_program": self.skill / "tools" / "reportcheck.py",
            "report_gate_result": self.assets / "gate-results/reportcheck.json",
            "citation_gate_program": self.skill / "tools" / "citecheck.py",
            "citation_gate_result": self.assets / "gate-results/citecheck.json",
            "state_gate_program": self.skill / "tools" / "statecheck.py",
            "state_gate_result": self.assets / "gate-results/statecheck.json",
            "triage_gate_program": self.skill / "tools" / "triagecheck.py",
            "triage_gate_result": self.assets / "gate-results/triagecheck.json",
            "settings": self.trace / "qwen-settings.json",
            "tool_schema": self.assets / "tool-schema.json",
            "input_manifest": self.trace / "run-manifest.json",
            "terminal_verdict": self.trace / "run-verdict.json",
        }
        self.paths["implementation_commit"].write_bytes(b"f1971b87a6a9c4a1039d7b96610d44021b41a81f\n")
        self.paths["implementation_dirty"].write_bytes(b" M qualification-test-fixture\n")
        self.paths["qwen_version"].write_bytes(b"Qwen 0.21.1\n")
        write_json(self.paths["arm"], {"schema": 1, "arm": "v44", "commit": "a" * 40, "tree": "b" * 40})
        write_json(self.paths["test_manifest"], {"schema": 1, "provider_free": True,
                                                  "passed": 4, "failed": 0,
                                                  "suites": ["harness", "manifest", "verdict", "state"]})
        write_json(self.paths["tool_schema"], {"schema": 1, "tools": ["read_file", "grep_search"]})
        for gate in ("report", "citation", "state", "triage"):
            write_json(self.paths[gate + "_gate_result"],
                       {"schema": 1, "gate": gate, "exit_code": 0, "blocking": 0})
        profile = json.loads(self.fx.profile.read_text())
        profile.update({"provider_base_url": "http://127.0.0.1:8317/v1", "route": "cliproxyapi",
                        "secret_ref": "SHERLOCK_API_KEY", "requested_model": "gpt-5.5",
                        "expected_returned_identity": "gpt-5.5", "identity_mode": "alias_unresolved",
                        "settings_sha256": sha(b"{}\n"), "skill_sha256": sha(self.paths["skill_v44"].read_bytes()),
                        "tool_schema_sha256": sha(self.paths["tool_schema"].read_bytes()),
                        "gate_sha256": {
                            "reportcheck": sha(self.paths["report_gate_program"].read_bytes()),
                            "citecheck": sha(self.paths["citation_gate_program"].read_bytes()),
                            "statecheck": sha(self.paths["state_gate_program"].read_bytes()),
                            "triagecheck": sha(self.paths["triage_gate_program"].read_bytes())}})
        write_json(self.fx.profile, profile)
        self.fx.write_health(lane="subscription", provider="cliproxyapi", requested_model="gpt-5.5",
            history=[{"size_kb": n, "status": 200, "returned_model": "gpt-5.5"} for n in (100, 250, 400)])

    def _terminal(self):
        write_json(self.trace / "status.json", {"schema": 1, "run_tag": "free-run-001", "phase": "ACCEPTED",
            "updated_at": "2026-09-04T00:00:00Z", "pid": None, "attempt": 1, "dataset": "fixture",
            "arm": "v44", "trace_dir": str(self.trace), "detail": None, "session_id": None, "reason": None,
            "exit_code": 0, "duration_s": 1, "upstream_log": None, "inflight_path": None})
        write_json(self.trace / "candidate.json", {"schema": 1, "run_tag": "free-run-001",
            "result_stream": "out.json", "work_root": "work", "artifact": "work/report.md",
            "upstream_completed": "upstream-completed.jsonl",
            "transport": {"exit_code": 0, "status": "success", "duration_s": 1},
            "usage": {"turns": 1, "input_tokens": 10, "output_tokens": 5}})
        write_json(self.trace / "out.json", {"type": "result", "subtype": "success", "result": "# Verified report\n",
            "duration_ms": 1000, "num_turns": 1, "usage": {"input_tokens": 10, "output_tokens": 5}})
        (self.trace / "work").mkdir(); (self.trace / "work/report.md").write_bytes(b"# Verified report\n")
        write_json(self.trace / "qwen-settings-pre.json", {}); write_json(self.trace / "qwen-settings.json", {})
        write_json(self.trace / "gates.json", {"schema": 1, "verdict": "clean", "arm_intact": True,
            "gates": {name: {"exit_code": 0, "blocking": 0}
                      for name in ("citecheck", "triagecheck", "statecheck", "reportcheck")}})
        write_json(self.trace / "lane-integrity.json", {"schema": 1, "verdict": "clean", "reason": None, "detail": None})
        executable(self.trace / "replay.sh", "#!/usr/bin/env bash\nexit 0\n")
        (self.trace / "attempts.jsonl").write_bytes(canonical({"attempt": 1, "exit_code": 0}) + b"\n")
        write_json(self.trace / "driver-result.json", {"exit_code": 0})
        upstream = {"run_tag": "free-run-001", "status": 200, "requested_model": "gpt-5.5",
                    "sent_model": "gpt-5.5", "returned_model": "gpt-5.5", "request_max_tokens": 10,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        (self.trace / "upstream-completed.jsonl").write_bytes(canonical(upstream) + b"\n")
        validity_tool = load("hq_validity_" + self.root.name, BENCH / "validate-run.py")
        validity = {"schema": 1, "valid": True, "reasons": [], "run_tag": "free-run-001",
                    "manifest_sha256": self.manifest["manifest_sha256"],
                    "candidate_sha256": sha((self.trace / "candidate.json").read_bytes())}
        validity["hmac_sha256"] = validity_tool.sign(validity, self.fx.commitment_key.read_bytes())
        write_json(self.trace / "validity.json", validity)
        verifier_env = os.environ.copy(); verifier_env["PYTHONDONTWRITEBYTECODE"] = "1"
        verified = subprocess.run([sys.executable, str(BENCH / "run-manifest.py"), "verify", str(self.trace),
            "--commitment-file", str(self.fx.commitment), "--commitment-key", str(self.fx.commitment_key), "--json"],
            text=True, capture_output=True, env=verifier_env)
        if verified.returncode: raise AssertionError(verified.stderr)
        verdict = subprocess.run([sys.executable, str(BENCH / "run-verdict.py"), str(self.trace),
            "--commitment-file", str(self.fx.commitment), "--commitment-key", str(self.fx.commitment_key), "--json"],
            text=True, capture_output=True, env=verifier_env)
        if verdict.returncode: raise AssertionError(verdict.stderr + verdict.stdout)
        self.paths["terminal_verdict"].write_bytes(verdict.stdout.encode())

    def _control(self):
        self.control = self.trace / "harness-qualification-input.json"
        write_json(self.control, {"schema": 1, "free_run_id": "free-run-001",
            "commitment_file": str(self.fx.commitment), "commitment_key": str(self.fx.commitment_key),
            "bindings": {name: {"path": str(self.paths[name]), "sha256": sha(self.paths[name].read_bytes())}
                         for name in BINDING_NAMES},
            "free_model_observations": {"requested": "gpt-5.5", "sent": "gpt-5.5", "returned": ["gpt-5.5"]}})

class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.qual = load("hq_matrix_" + self.root.name, TOOL)

    def test_executes_all_eleven_programs_once_and_returns_literal_rows(self):
        fixtures = build_fault_fixtures(self.root / "fixtures")
        row = self.qual.run_fault_matrix(fixtures)
        self.assertEqual((row["schema"], row["verdict"], row["faults"]), (1, "clean", EXPECTED_ROWS))
        self.assertEqual(sorted(path.name for path in (fixtures / "receipts").iterdir()),
                         [case_id + ".receipt" for case_id, *_ in FAULT_CASES])
        for case_id, *_ in FAULT_CASES:
            self.assertEqual((fixtures / "receipts" / (case_id + ".receipt")).read_bytes(), (case_id + "\n").encode())

    def test_changed_fixture_outcome_is_literal_failed_observation(self):
        fixtures = build_fault_fixtures(self.root / "fixtures")
        executable(fixtures / "programs/artifact_tamper.py", fault_program("artifact_tamper", 7, "WRONG_FAILURE"))
        row = self.qual.run_fault_matrix(fixtures)
        self.assertEqual(next(item for item in row["faults"] if item["id"] == "artifact_tamper"),
            {"id": "artifact_tamper", "expected": {"exit": 2, "failure": "TRACE_INVALID"},
             "observed": {"exit": 7, "failure": "WRONG_FAILURE"}, "passed": False})
        self.assertEqual(row["verdict"], "failed")

    def test_missing_duplicate_unknown_malformed_timeout_and_escape_fail_closed(self):
        for mutation in ("missing_case", "missing_program", "duplicate", "unknown", "malformed", "timeout", "escape"):
            with self.subTest(mutation=mutation):
                fixtures = build_fault_fixtures(self.root / mutation)
                manifest = json.loads((fixtures / "manifest.json").read_text())
                if mutation == "missing_case": manifest["faults"].pop()
                elif mutation == "missing_program": (fixtures / manifest["faults"][0]["program"]).unlink()
                elif mutation == "duplicate": manifest["faults"][-1]["id"] = manifest["faults"][0]["id"]
                elif mutation == "unknown": manifest["faults"][-1]["id"] = "surprise"
                elif mutation == "malformed": executable(fixtures / manifest["faults"][0]["program"], "#!/bin/sh\necho malformed\nexit 2\n")
                elif mutation == "timeout": executable(fixtures / manifest["faults"][0]["program"],
                    fault_program("admission_refusal", 2, "HARNESS_QUALIFICATION_MISSING", True, 2))
                else: manifest["faults"][0]["program"] = "../escape.py"
                write_json(fixtures / "manifest.json", manifest)
                with self.assertRaises(self.qual.QualificationFailure): self.qual.run_fault_matrix(fixtures)

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.qual = load("hq_audit_" + self.root.name, TOOL)
        self.matrix = self.qual.run_fault_matrix(build_fault_fixtures(self.root / "fixtures"))
        self.trace = TraceFixture(self.root / "valid")

    def audit(self, trace=None, matrix=None, output=None):
        return self.qual.audit_harness((trace or self.trace).trace, matrix or self.matrix,
                                       output or (self.root / "harness-acceptance.json"))

    def test_real_manifest_and_verdict_write_exact_harness_only_receipt_bytes(self):
        output = self.root / "harness-acceptance.json"; row = self.audit(output=output)
        bindings = {name + "_sha256": sha(self.trace.paths[name].read_bytes()) for name in BINDING_NAMES}
        self.assertEqual(row, {"schema": 1, "accepted": True, "proof_scope": "harness_only",
            "matrix_sha256": sha(canonical(self.matrix)), "qualification_manifest_sha256": sha(self.trace.control.read_bytes()),
            "bindings": bindings, "free_run": {"id": "free-run-001",
                "input_manifest_sha256": bindings["input_manifest_sha256"], "terminal_verdict": "ACCEPTED"},
            "free_model_observations": {"requested": "gpt-5.5", "sent": "gpt-5.5", "returned": ["gpt-5.5"]}})
        self.assertEqual(output.read_bytes(), canonical(row) + b"\n")
        verified = subprocess.run([sys.executable, str(TOOL), "verify", "--receipt", str(output), "--json"],
                                  text=True, capture_output=True, cwd=SHERLOCK)
        self.assertEqual((verified.returncode, verified.stdout.encode()), (0, canonical(row) + b"\n"))
        forged = json.loads(json.dumps(row)); forged["bindings"].pop("proxy_sha256")
        forged_path = self.root / "forged-receipt.json"; write_json(forged_path, forged)
        rejected = subprocess.run([sys.executable, str(TOOL), "verify", "--receipt", str(forged_path), "--json"],
                                  text=True, capture_output=True, cwd=SHERLOCK)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("paid model", json.dumps(row).lower()); self.assertNotIn("target qualified", json.dumps(row).lower())

    def test_audit_recomputes_truth_and_rejects_bad_or_forged_rows(self):
        cases = {}
        cases["mismatch"] = json.loads(json.dumps(self.matrix)); cases["mismatch"]["faults"][0]["observed"]["exit"] = 99
        cases["mismatch"]["faults"][0]["passed"] = True
        cases["missing"] = json.loads(json.dumps(self.matrix)); cases["missing"]["faults"].pop()
        cases["duplicate"] = json.loads(json.dumps(self.matrix)); cases["duplicate"]["faults"][-1]["id"] = cases["duplicate"]["faults"][0]["id"]
        cases["unknown"] = json.loads(json.dumps(self.matrix)); cases["unknown"]["faults"][-1]["id"] = "unknown"
        cases["malformed"] = json.loads(json.dumps(self.matrix)); cases["malformed"]["faults"][0]["observed"]["exit"] = True
        cases["forged"] = json.loads(json.dumps(self.matrix)); cases["forged"]["qualification_tool_sha256"] = "0" * 64
        for label, matrix in cases.items():
            with self.subTest(label=label):
                output = self.root / (label + ".json")
                with self.assertRaises(self.qual.QualificationFailure): self.audit(matrix=matrix, output=output)
                self.assertFalse(output.exists())

    def test_each_bound_artifact_tamper_is_rejected_without_receipt(self):
        for name in BINDING_NAMES:
            with self.subTest(binding=name):
                fresh = TraceFixture(self.root / ("tamper-" + name))
                fresh.paths[name].write_bytes(fresh.paths[name].read_bytes() + b"tamper\n")
                output = self.root / ("receipt-" + name + ".json")
                with self.assertRaises(self.qual.QualificationFailure): self.audit(trace=fresh, output=output)
                self.assertFalse(output.exists())

    def test_terminal_identity_alias_and_preexisting_output_fail_closed(self):
        for mutation in ("missing_terminal", "invalid_terminal", "identity_mismatch", "symlink", "hardlink"):
            with self.subTest(mutation=mutation):
                fresh = TraceFixture(self.root / mutation)
                if mutation == "missing_terminal": fresh.paths["terminal_verdict"].unlink()
                elif mutation == "invalid_terminal":
                    verdict = json.loads(fresh.paths["terminal_verdict"].read_text()); verdict["successful"] = False
                    write_json(fresh.paths["terminal_verdict"], verdict)
                    control = json.loads(fresh.control.read_text()); control["bindings"]["terminal_verdict"]["sha256"] = sha(fresh.paths["terminal_verdict"].read_bytes()); write_json(fresh.control, control)
                elif mutation == "identity_mismatch":
                    control = json.loads(fresh.control.read_text()); control["free_model_observations"]["sent"] = "wrong"; write_json(fresh.control, control)
                else:
                    path = fresh.paths["proxy"]; original = path.with_suffix(".original"); path.rename(original)
                    path.symlink_to(original) if mutation == "symlink" else os.link(original, path)
                    control = json.loads(fresh.control.read_text()); control["bindings"]["proxy"]["sha256"] = sha(original.read_bytes()); write_json(fresh.control, control)
                output = self.root / (mutation + ".json")
                with self.assertRaises(self.qual.QualificationFailure): self.audit(trace=fresh, output=output)
                self.assertFalse(output.exists())
        output = self.root / "existing.json"; output.write_bytes(b"keep\n")
        with self.assertRaises(self.qual.QualificationFailure): self.audit(output=output)
        self.assertEqual(output.read_bytes(), b"keep\n")

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)

    def standin(self, name, exit_code=0):
        path = self.root / name
        executable(path, "#!/bin/sh\nprintf '%s\\n' \"$STAGE\" >> \"$STAGE_LOG\"\nexit {}\n".format(exit_code))
        return path

    def invoke(self, output, **updates):
        env = os.environ.copy(); env.update({"SHERLOCK_API_KEY": "synthetic-key-never-persist",
            "SHERLOCK_HARNESS_TEST_MODE": "1", "SHERLOCK_HARNESS_CONTROLLER": str(self.standin("controller")),
            "SHERLOCK_HARNESS_AUDITOR": str(self.standin("auditor")), "STAGE_LOG": str(self.root / "order.log")})
        env.update(updates)
        return subprocess.run([str(LAUNCHER), str(output)], env=env, text=True, capture_output=True, cwd=SHERLOCK)

    def test_explicit_test_standins_preserve_matrix_controller_audit_order_and_environment(self):
        output = self.root / "qualification"; result = self.invoke(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "order.log").read_text().splitlines(), ["controller", "audit"])
        events = [json.loads(line) for line in (output / "stage-events.jsonl").read_text().splitlines()]
        self.assertEqual([row["stage"] for row in events], ["matrix", "controller", "audit"])
        environment = json.loads((output / "controller-environment.json").read_text())
        self.assertEqual(environment, {"SHERLOCK_BASE_URL": "http://127.0.0.1:8317/v1", "SHERLOCK_MODEL": "gpt-5.5",
            "SHERLOCK_LANE": "subscription", "SHERLOCK_PROVIDER": "cliproxyapi",
            "SHERLOCK_SKILL_ROOT": str(SHERLOCK / "skills/v44"),
            "SHERLOCK_REPORT_GATE": str(SHERLOCK / "skills/v44/tools/reportcheck.py"),
            "SHERLOCK_CITATION_GATE": str(SHERLOCK / "skills/v44/tools/citecheck.py"),
            "SHERLOCK_STATE_GATE": str(SHERLOCK / "skills/v44/tools/statecheck.py"),
            "SHERLOCK_TRIAGE_GATE": str(SHERLOCK / "skills/v44/tools/triagecheck.py"),
            "SHERLOCK_CORPUS": str(output / "generated-probe-corpus"), "SHERLOCK_API_KEY_PRESENT": True})
        corpus = [p for p in (output / "generated-probe-corpus").rglob("*") if p.is_file()]
        self.assertTrue(corpus); self.assertLess(sum(p.stat().st_size for p in corpus), 2_000_000)
        for path in output.rglob("*"):
            if path.is_file(): self.assertNotIn(b"synthetic-key-never-persist", path.read_bytes())

    def test_key_relative_existing_and_symlink_root_reject_before_stage(self):
        missing = self.root / "missing"; env = os.environ.copy(); env.pop("SHERLOCK_API_KEY", None)
        result = subprocess.run([str(LAUNCHER), str(missing)], env=env, text=True, capture_output=True, cwd=SHERLOCK)
        self.assertNotEqual(result.returncode, 0); self.assertFalse(missing.exists())
        self.assertNotEqual(self.invoke(Path("relative-output")).returncode, 0)
        existing = self.root / "existing"; existing.mkdir(); self.assertNotEqual(self.invoke(existing).returncode, 0); self.assertEqual(list(existing.iterdir()), [])
        target = self.root / "target"; target.mkdir(); alias = self.root / "alias"; alias.symlink_to(target, target_is_directory=True)
        self.assertNotEqual(self.invoke(alias).returncode, 0); self.assertEqual(list(target.iterdir()), [])

    def test_matrix_controller_and_audit_failure_prevent_later_stages_and_receipt(self):
        bad = build_fault_fixtures(self.root / "bad"); (bad / "programs/admission_refusal.py").unlink()
        matrix_root = self.root / "matrix-fail"; self.assertNotEqual(self.invoke(matrix_root, SHERLOCK_HARNESS_FIXTURES=str(bad)).returncode, 0)
        self.assertFalse((matrix_root / "harness-acceptance.json").exists())
        controller_root = self.root / "controller-fail"; self.assertNotEqual(self.invoke(controller_root,
            SHERLOCK_HARNESS_CONTROLLER=str(self.standin("controller-fail", 7))).returncode, 0)
        self.assertFalse((controller_root / "harness-acceptance.json").exists())
        audit_root = self.root / "audit-fail"; self.assertNotEqual(self.invoke(audit_root,
            SHERLOCK_HARNESS_AUDITOR=str(self.standin("audit-fail", 8))).returncode, 0)
        self.assertFalse((audit_root / "harness-acceptance.json").exists())

if __name__ == "__main__":
    unittest.main()
