#!/usr/bin/env python3
"""Provider-free behavior tests for Sherlock harness qualification."""
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
BENCH = SHERLOCK / "eval" / "bench"
TOOL = BENCH / "harness-qualification.py"
LAUNCHER = BENCH / "run-harness-qualification.sh"


def load(name, path=TOOL):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def write_json(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(row) + b"\n")


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hq_matrix_tmp")
        self.root = Path(self.temp.name).resolve()
        self.qual = load("hq_matrix_" + self.root.name)

    def tearDown(self): self.temp.cleanup()

    def fixtures(self):
        root = self.root / "inputs"
        self.qual._default_fixtures(root)
        return root

    def test_fixed_adapters_exercise_all_eleven_repository_paths(self):
        row = self.qual.run_fault_matrix(self.fixtures())
        self.assertEqual(row["schema"], 2)
        self.assertEqual(row["verdict"], "clean")
        self.assertEqual({item["id"] for item in row["faults"]}, set(self.qual.EXPECTED))
        self.assertTrue(all(item["passed"] for item in row["faults"]))
        self.assertTrue(all(Path(item["tool"]).is_relative_to(SHERLOCK)
                            for item in row["faults"]))
        self.assertTrue(all(row["adapter_tools"][item["tool"]] == item["tool_sha256"]
                            for item in row["faults"]))

    def test_cli_default_fixture_survives_an_aliased_output_parent(self):
        real = self.root / "real"
        real.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        output = alias / "fault-matrix.json"
        with mock.patch("builtins.print"):
            self.assertEqual(self.qual.main(["matrix", "--output", str(output)]), 0)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["verdict"], "clean")

    def test_caller_executables_and_expected_observations_are_rejected(self):
        root = self.root / "attacker"; root.mkdir()
        executable(root / "accept-all", "#!/bin/sh\nexit 0\n")
        write_json(root / "manifest.json", {"schema": 1, "timeout_seconds": 1,
                   "faults": [{"id": name, "program": "accept-all",
                               "expected": {"exit": code, "failure": failure}}
                              for name, (code, failure) in self.qual.EXPECTED.items()]})
        with self.assertRaisesRegex(self.qual.QualificationFailure, "MATRIX_INPUT_SCHEMA"):
            self.qual.run_fault_matrix(root)

    def test_sealed_input_hash_tamper_is_rejected(self):
        root = self.fixtures()
        (root / "corpus.log").write_text("attacker changed it\n")
        with self.assertRaisesRegex(self.qual.QualificationFailure, "MATRIX_INPUT_HASH"):
            self.qual.run_fault_matrix(root)

    def test_child_environment_is_allowlisted_and_timeout_kills_process_group(self):
        script = self.root / "child.py"
        marker = self.root / "survivor"
        executable(script, textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import os, subprocess, sys
            assert 'SHERLOCK_API_KEY' not in os.environ
            subprocess.Popen([sys.executable, '-c',
                \"import pathlib,time;time.sleep(1);pathlib.Path({str(marker)!r}).write_text('alive')\"])
            print('x' * 1024)
            import time; time.sleep(30)
            """))
        with mock.patch.dict(os.environ, {"SHERLOCK_API_KEY": "must-not-cross"}):
            rc, stdout, stderr, timed_out = self.qual._run_bounded(
                [str(script)], cwd=self.root, timeout=.15)
        self.assertTrue(timed_out)
        self.assertNotEqual(rc, 0)
        self.assertLessEqual(len(stdout), self.qual.MAX_PROCESS_OUTPUT)
        time.sleep(1.1)
        self.assertFalse(marker.exists())


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hq_audit_tmp")
        self.root = Path(self.temp.name).resolve()
        self.qual = load("hq_audit_" + self.root.name)
        inputs = self.root / "matrix-inputs"; self.qual._default_fixtures(inputs)
        self.matrix = self.qual.run_fault_matrix(inputs)

    def tearDown(self): self.temp.cleanup()

    def forged(self, matrix_hash="0" * 64):
        return {"schema": 2, "accepted": True, "proof_scope": "harness_only",
                "matrix_sha256": matrix_hash, "qualification_manifest_sha256": "1" * 64,
                "trace": "run-forged",
                "bindings": {name + "_sha256": "2" * 64 for name in self.qual.BINDINGS},
                "free_run": {"id": "run-forged", "input_manifest_sha256": "2" * 64,
                             "terminal_verdict": "ACCEPTED"},
                "free_model_observations": {"requested": "gpt-5.5", "sent": "gpt-5.5",
                                             "returned": ["gpt-5.5"]}}

    def test_synthetic_self_authorized_unsealed_trace_is_rejected(self):
        trace = self.root / "runs" / "run-forged"; trace.mkdir(parents=True)
        output = self.root / "harness-acceptance.json"
        with self.assertRaisesRegex(self.qual.QualificationFailure,
                                    "(CONTROLLER_ROOT|AUTHORITY|TERMINAL_SEAL|SEAL_TRACE)"):
            self.qual.audit_harness(trace, self.matrix, output)
        self.assertFalse(output.exists())

    def test_arbitrary_hex_receipt_requires_preserved_signed_audit(self):
        path = self.root / "harness-acceptance.json"; write_json(path, self.forged())
        with self.assertRaisesRegex(self.qual.QualificationFailure,
                                    "(AUTHORITY|RECEIPT_MARKER|PRESERVED)"):
            self.qual.verify_receipt(path)

    def test_null_hash_maps_to_qualification_failure(self):
        path = self.root / "null.json"; write_json(path, self.forged(None))
        with self.assertRaises(self.qual.QualificationFailure):
            self.qual.verify_receipt(path)

    def test_terminal_seal_recomputes_run_id_and_complete_artifact_inventory(self):
        trace = self.root / "runs" / "run-1"; trace.mkdir(parents=True)
        (trace / "artifact.txt").write_text("must be inventoried\n")
        (trace / "sealed").write_bytes(b"")
        key = b"k" * 32
        row = {"schema": 1, "run_tag": "attacker-run",
               "child_manifest_sha256": "a" * 64, "artifacts": [],
               "key_id": hashlib.sha256(key).hexdigest()}
        row["hmac_sha256"] = hmac.new(key, canonical(row), hashlib.sha256).hexdigest()
        write_json(trace / "trace-manifest.json", row)
        with self.assertRaisesRegex(self.qual.QualificationFailure, "TERMINAL_SEAL_INVALID"):
            self.qual._terminal_seal(trace, key, "a" * 64, "run-1")

    def test_controller_commitment_ledger_is_receipt_bound(self):
        self.assertIn("controller_commitments", self.qual.BINDINGS)

    def publication_fixture(self):
        root = self.root / "publish"; root.mkdir()
        trace = root / "runs" / "run-1"; trace.mkdir(parents=True)
        controller = root / "controller"; (controller / "records").mkdir(parents=True)
        (controller / "records" / "run-commitments.jsonl").write_text("")
        (controller / "keys").mkdir(); key = controller / "keys" / "controller.key"
        key.write_bytes(b"k" * 32); key.chmod(0o600)
        matrix_path = root / "fault-matrix.json"; write_json(matrix_path, self.matrix)
        control = {"schema": 2, "free_run_id": "run-1", "trace": str(trace),
                   "matrix": str(matrix_path), "qwen_binary": "/bin/false",
                   "free_model_observations": {"requested": "gpt-5.5", "sent": "gpt-5.5",
                                                "returned": ["gpt-5.5"]}}
        write_json(root / "harness-qualification-input.json", control)
        files = {name: (root / name, (name + "\n").encode()) for name in self.qual.BINDINGS}
        return root, trace, files

    def test_descriptor_publication_has_authenticated_marker(self):
        root, trace, files = self.publication_fixture()
        output = root / "harness-acceptance.json"
        with mock.patch.object(self.qual, "_verify_trace", return_value=(root, files, b"{}\n")):
            receipt = self.qual.audit_harness(trace, self.matrix, output)
        marker = json.loads(Path(str(output) + ".accepted").read_text())
        supplied = marker.pop("hmac_sha256")
        self.assertEqual(marker["receipt_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(supplied, hmac.new(b"k" * 32, canonical(marker), hashlib.sha256).hexdigest())
        self.assertEqual(receipt["trace"], "run-1")

    def test_each_bound_artifact_is_reaudited_not_shape_verified(self):
        root, trace, files = self.publication_fixture()
        output = root / "harness-acceptance.json"
        with mock.patch.object(self.qual, "_verify_trace", return_value=(root, files, b"{}\n")):
            self.qual.audit_harness(trace, self.matrix, output)
        for name in self.qual.BINDINGS:
            with self.subTest(binding=name):
                changed = dict(files)
                path, raw = changed[name]
                changed[name] = (path, raw + b"tamper")
                with mock.patch.object(self.qual, "_verify_trace",
                                       return_value=(root, changed, b"{}\n")):
                    with self.assertRaisesRegex(self.qual.QualificationFailure,
                                                "PRESERVED_BINDINGS_MISMATCH"):
                        self.qual.verify_receipt(output)

    def test_parent_swap_and_fsync_failure_remove_receipt_and_marker(self):
        root, trace, files = self.publication_fixture()
        output = root / "harness-acceptance.json"; moved = self.root / "moved"
        def swap(*_):
            root.rename(moved); root.mkdir()
            return moved, files, b"{}\n"
        with mock.patch.object(self.qual, "_verify_trace", side_effect=swap):
            with self.assertRaises(self.qual.QualificationFailure):
                self.qual.audit_harness(trace, self.matrix, output)
        self.assertFalse(output.exists()); self.assertFalse((moved / output.name).exists())

        shutil.rmtree(root); moved.rename(root)
        trace = root / "runs" / "run-1"; output = root / "harness-acceptance.json"
        with mock.patch.object(self.qual, "_verify_trace", return_value=(root, files, b"{}\n")), \
             mock.patch.object(self.qual.os, "fsync", side_effect=OSError("injected")):
            with self.assertRaises(self.qual.QualificationFailure):
                self.qual.audit_harness(trace, self.matrix, output)
        self.assertFalse(output.exists()); self.assertFalse(Path(str(output) + ".accepted").exists())

    def test_matrix_truth_and_tool_hash_are_recomputed(self):
        for mutate in (
            lambda row: row["faults"][0]["observed"].update(exit=99),
            lambda row: row["faults"].pop(),
            lambda row: row.update(qualification_tool_sha256="0" * 64),
            lambda row: row["faults"][0].update(tool_sha256="0" * 64),
        ):
            row = json.loads(json.dumps(self.matrix)); mutate(row)
            with self.assertRaises(self.qual.QualificationFailure): self.qual._validate_matrix(row)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hq_launcher_tmp")
        self.root = Path(self.temp.name).resolve()

    def tearDown(self): self.temp.cleanup()

    def invoke(self, output, **extra):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
               "SHERLOCK_API_KEY": "dummy-never-contacted", **extra}
        return subprocess.run([str(LAUNCHER), str(output)], env=env, text=True,
                              capture_output=True, cwd=SHERLOCK)

    def test_key_relative_existing_and_any_symlink_ancestor_reject_before_stage(self):
        no_key = subprocess.run([str(LAUNCHER), str(self.root / "no-key")], env={"PATH": os.environ["PATH"]},
                                text=True, capture_output=True)
        self.assertNotEqual(no_key.returncode, 0)
        self.assertNotEqual(self.invoke("relative").returncode, 0)
        existing = self.root / "existing"; existing.mkdir()
        self.assertNotEqual(self.invoke(existing).returncode, 0)
        real = self.root / "real"; real.mkdir(); alias = self.root / "alias"; alias.symlink_to(real)
        through = alias / "new-root"
        self.assertNotEqual(self.invoke(through).returncode, 0)
        self.assertFalse((real / "new-root").exists())

    def test_environment_test_mode_executable_and_trace_overrides_cannot_forge(self):
        fake = self.root / "fake"; executable(fake, "#!/bin/sh\nexit 0\n")
        output = self.root / "override"
        result = self.invoke(output, SHERLOCK_HARNESS_TEST_MODE="1",
                             SHERLOCK_HARNESS_CONTROLLER=str(fake),
                             SHERLOCK_HARNESS_AUDITOR=str(fake),
                             SHERLOCK_HARNESS_TRACE=str(self.root / "attacker"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((output / "harness-acceptance.json").exists())

    def test_one_argument_launcher_builds_complete_fixed_controller_bundle_and_input(self):
        repo = self.root / "repo"; bench = repo / "eval" / "bench"; bench.mkdir(parents=True)
        shutil.copy2(LAUNCHER, bench / LAUNCHER.name); (bench / LAUNCHER.name).chmod(0o700)
        required = ("skills/v44/SKILL.md", "skills/v44/reference/report-contract.corporate.json",
                    "skills/v44/tools/reportcheck.py", "skills/v44/tools/citecheck.py",
                    "skills/v44/tools/statecheck.py", "skills/v44/tools/triagecheck.py",
                    "skills/v44/tools/stopcheck.py", "measure/interactive-drive.py",
                    "measure/upstream-log-proxy.py", "measure/probes/lane-health.sh",
                    "measure/corporate-settings.py",
                    "eval/bench/run-bench.sh", "eval/bench/run-manifest.py",
                    "eval/bench/run-verdict.py", "eval/bench/seal-trace.py", "eval/bench/score-bench.py")
        for relative in required: executable(repo / relative, "#!/bin/sh\nexit 0\n")
        executable(repo / "measure/corporate-settings.py", textwrap.dedent("""\
            #!/usr/bin/env python3
            import json,sys
            assert sys.argv[1] == 'emit-run'
            value=int(sys.argv[sys.argv.index('--session-token-limit')+1])
            print(json.dumps({'schema':1,'fixed':True,
                              'model':{'sessionTokenLimit':value}},
                             sort_keys=True,separators=(',',':')))
            """))
        executable(bench / "harness-qualification.py", textwrap.dedent("""\
            #!/usr/bin/env python3
            import pathlib, sys
            command=sys.argv[1]; out=pathlib.Path(sys.argv[sys.argv.index('--output')+1])
            if command == 'matrix': out.write_text('{"schema":2,"verdict":"clean"}\\n')
            elif command == 'audit':
                assert (out.parent/'harness-qualification-input.json').is_file()
                out.write_text('{"accepted":true,"proof_scope":"harness_only","schema":2}\\n')
            """))
        executable(bench / "bench-controller.sh", textwrap.dedent("""\
            #!/bin/sh
            python3 - <<'PY'
            import json, os, pathlib
            root=pathlib.Path(os.environ['SHERLOCK_CONTROLLER_ROOT']).parent
            keep=[n for n in os.environ if n.startswith('SHERLOCK_') or n.startswith('GIT_CONFIG_') or n in ('BENCH_RUNS','QWEN_BIN')]
            (root/'captured-env.json').write_text(json.dumps({n:os.environ[n] for n in keep},sort_keys=True))
            (pathlib.Path(os.environ['BENCH_RUNS'])/'run-fixed').mkdir()
            PY
            """))
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture.invalid", "commit", "-qm", "fixture"], check=True)
        fake_bin = self.root / "bin"; fake_bin.mkdir()
        executable(fake_bin / "qwen", "#!/bin/sh\n[ \"${1-}\" = --version ] && echo 'Qwen 0.21.1'\n")
        output = self.root / "production"
        env = {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
               "HOME": os.environ.get("HOME", "/tmp"), "SHERLOCK_API_KEY": "dummy",
               "SHERLOCK_HARNESS_TRACE": str(self.root / "attacker")}
        result = subprocess.run([str(bench / LAUNCHER.name), str(output)], env=env,
                                text=True, capture_output=True, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads((output / "captured-env.json").read_text())
        for name in ("SHERLOCK_CONTROLLER_ROOT", "SHERLOCK_FREE_TEST_COMMAND",
                     "SHERLOCK_HEALTH_COMMAND", "SHERLOCK_TARGET_COMMAND", "BENCH_RUNS",
                     "SHERLOCK_PROMPT_FILE", "SHERLOCK_ANSWER_KEY", "SHERLOCK_ARM",
                     "SHERLOCK_SKILL_ROOT", "SHERLOCK_REPORT_CHECKER",
                     "SHERLOCK_STATE_CHECKER", "SHERLOCK_TRIAGE_CHECKER",
                     "SHERLOCK_STOP_CHECKER", "SHERLOCK_CITATION_CHECKER", "QWEN_BIN",
                     "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS", "SHERLOCK_CONTEXT_WINDOW",
                     "SHERLOCK_RENDERER", "SHERLOCK_TARGET_PROFILE",
                     "SHERLOCK_SETTINGS", "SHERLOCK_INPUT_PACKAGE"):
            self.assertIn(name, captured)
        settings = output / "corporate-settings.json"
        package = output / "input-package.json"
        profile = json.loads((output / "target-profile.json").read_text())
        settings_row = json.loads(settings.read_text())
        budget = json.loads((output / "probe-budget.json").read_text())
        self.assertTrue(settings.is_file()); self.assertTrue(package.is_file())
        self.assertEqual(settings_row["model"]["sessionTokenLimit"], 230000)
        self.assertEqual(profile["session_token_limit"], 230000)
        self.assertEqual(budget["session_token_limit"], 230000)
        self.assertEqual(captured["SHERLOCK_SESSION_TOKEN_LIMIT"], "230000")
        self.assertEqual(profile["settings_sha256"], hashlib.sha256(settings.read_bytes()).hexdigest())
        self.assertNotEqual(profile["settings_sha256"], profile["tool_schema_sha256"])
        self.assertTrue((output / "harness-qualification-input.json").is_file())
        free_test = Path(shlex.split(captured["SHERLOCK_FREE_TEST_COMMAND"])[0]).read_text()
        self.assertIn("export GIT_CONFIG_COUNT=1", free_test)
        self.assertIn("export GIT_CONFIG_KEY_0=safe.directory", free_test)
        self.assertIn("REPO=" + shlex.quote(str(repo.resolve())), free_test)
        self.assertIn('export GIT_CONFIG_VALUE_0="$REPO"', free_test)
        self.assertNotIn('export GIT_CONFIG_VALUE_0="$ROOT"', free_test)
        self.assertEqual(captured["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(captured["GIT_CONFIG_KEY_0"], "safe.directory")
        self.assertEqual(captured["GIT_CONFIG_VALUE_0"], str(repo.resolve()))
        state = subprocess.run(
            [sys.executable, str(SHERLOCK / "skills/v44/tools/statecheck.py"),
             "--corpus", str(output / "generated-probe-corpus"), "--json"],
            text=True, capture_output=True)
        self.assertEqual(state.returncode, 4, state.stdout + state.stderr)
        census = json.loads(state.stdout)
        self.assertFalse(census["empty_census"])
        self.assertGreater(census["total_records"], 0)
        self.assertTrue(census["groups"], "qualification corpus must exercise statecheck")
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                       text=True).strip()
        self.assertEqual((output / "implementation-commit.txt").read_text().strip(), head)
        self.assertNotIn("SHERLOCK_HARNESS_TRACE", captured)
        self.assertEqual([json.loads(line)["stage"] for line in
                          (output / "stage-events.jsonl").read_text().splitlines()],
                         ["matrix", "controller", "audit"])


if __name__ == "__main__":
    unittest.main()
