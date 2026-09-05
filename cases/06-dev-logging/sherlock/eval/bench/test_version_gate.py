#!/usr/bin/env python3
"""Offline regression tests for the immutable runtime-package gate."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("version_gate", HERE / "version-gate.py")
VERSION_GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSION_GATE)


class VersionGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skills = self.root / "skills"
        self.package = self.skills / "v44"
        self.package.mkdir(parents=True)
        (self.package / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
        (self.package / "tools").mkdir()
        (self.package / "tools" / "gate.py").write_text("print('gate')\n", encoding="utf-8")
        (self.package / "tools" / "gate.py").chmod(0o755)
        self.registry = self.root / "version-registry.json"
        self.registry.write_text(json.dumps({"schema": 1, "versions": {
            "v44": VERSION_GATE.tree_digest(self.package)}}) + "\n", encoding="utf-8")
        self.kwargs = {"trusted_baseline": {}}

    def tearDown(self):
        self.temp.cleanup()

    def test_mutated_registered_package_refuses_before_contact(self):
        (self.package / "SKILL.md").write_text("changed\n", encoding="utf-8")
        contacted = []
        with self.assertRaises(VERSION_GATE.VersionGateError):
            VERSION_GATE.before_contact("v44", self.skills, self.registry,
                                        self.root / "snapshots", lambda _: contacted.append(True), **self.kwargs)
        self.assertEqual(contacted, [])

    def test_invalid_and_unregistered_versions_are_rejected(self):
        for version in ("v044", "v45", "arm-a", "../v44"):
            with self.assertRaises(VERSION_GATE.VersionGateError):
                VERSION_GATE.verify_version(version, self.skills, self.registry, **self.kwargs)

    def test_snapshot_is_immutable_identity_after_source_mutation(self):
        verified = VERSION_GATE.verify_version("v44", self.skills, self.registry, **self.kwargs)
        snapshot = VERSION_GATE.seal_snapshot(verified, self.root / "snapshots")
        (self.package / "SKILL.md").write_text("source changed\n", encoding="utf-8")
        self.assertEqual(VERSION_GATE.tree_digest(snapshot.path), verified.digest)
        self.assertEqual(snapshot.version, "v44")

    def test_snapshot_preserves_executable_tools_while_removing_write_bits(self):
        verified = VERSION_GATE.verify_version("v44", self.skills, self.registry, **self.kwargs)
        snapshot = VERSION_GATE.seal_snapshot(verified, self.root / "snapshots")

        self.assertEqual(stat.S_IMODE((snapshot.path / "tools" / "gate.py").stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((snapshot.path / "SKILL.md").stat().st_mode), 0o444)

    def test_generated_bytecode_does_not_change_the_registered_package_identity(self):
        cache = self.package / "tools" / "__pycache__"
        cache.mkdir()
        (cache / "gate.cpython-312.pyc").write_bytes(b"generated")

        verified = VERSION_GATE.verify_version("v44", self.skills, self.registry, **self.kwargs)

        self.assertEqual(verified.digest, json.loads(self.registry.read_text())["versions"]["v44"])

    def test_registration_is_idempotent_and_rejects_changed_existing_bytes(self):
        package = self.skills / "v45"
        shutil.copytree(self.package, package)
        (package / "SKILL.md").write_text("---\nname: next\n---\n", encoding="utf-8")

        first = VERSION_GATE.register_version("v45", self.skills, self.registry, **self.kwargs)
        registered_once = self.registry.read_bytes()
        second = VERSION_GATE.register_version("v45", self.skills, self.registry, **self.kwargs)
        self.assertEqual(second.digest, first.digest)
        self.assertEqual(self.registry.read_bytes(), registered_once)

        (package / "SKILL.md").write_text("changed after registration\n", encoding="utf-8")
        with self.assertRaises(VERSION_GATE.VersionGateError):
            VERSION_GATE.register_version("v45", self.skills, self.registry, **self.kwargs)
        self.assertEqual(self.registry.read_bytes(), registered_once)

    def test_distinct_registered_version_selects_its_bound_gate_tools(self):
        package = self.skills / "v45"
        shutil.copytree(HERE.parent.parent / "skills" / "v44", package)
        (package / "synthetic-version.txt").write_text("generic v45 test change\n", encoding="utf-8")

        registered = VERSION_GATE.register_version("v45", self.skills, self.registry,
                                                   **self.kwargs)
        snapshot = VERSION_GATE.seal_snapshot(registered, self.root / "snapshots")

        self.assertEqual(snapshot.version, "v45")
        self.assertEqual(VERSION_GATE.tree_digest(snapshot.path), registered.digest)
        for name in ("citecheck", "reportcheck", "statecheck", "triagecheck"):
            selected = snapshot.path / "tools" / (name + ".py")
            source = package / "tools" / (name + ".py")
            self.assertTrue(selected.is_file())
            self.assertEqual(selected.read_bytes(), source.read_bytes())

    def test_committed_ancestor_registration_cannot_be_rewritten_with_the_package(self):
        repo = self.root / "repo"
        package = repo / "skills" / "v45"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text("first\n", encoding="utf-8")
        registry = repo / "version-registry.json"
        registry.write_text(json.dumps({"schema": 1, "versions": {
            "v45": VERSION_GATE.tree_digest(package)}}) + "\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Version Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "version@test.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "register v45"], check=True)

        (package / "SKILL.md").write_text("rewritten\n", encoding="utf-8")
        registry.write_text(json.dumps({"schema": 1, "versions": {
            "v45": VERSION_GATE.tree_digest(package)}}) + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "rewrite v45"], check=True)

        with self.assertRaisesRegex(VERSION_GATE.VersionGateError, "history"):
            VERSION_GATE.verify_version("v45", repo / "skills", registry,
                                        trusted_baseline={})

    def test_direct_runner_rejects_explicit_unregistered_selector_before_qwen(self):
        marker = self.root / "qwen-contacted"
        qwen = self.root / "qwen-tripwire"
        qwen.write_text("#!/usr/bin/env bash\nprintf contacted > %s\nexit 97\n" % marker,
                        encoding="utf-8")
        qwen.chmod(0o755)
        runner = HERE / "run-bench.sh"
        done = subprocess.run(
            ["bash", str(runner), "none"], text=True, capture_output=True, timeout=20,
            env=dict(os.environ, SHERLOCK_PACKAGE_VERSION="v999", QWEN_BIN=str(qwen),
                     BENCH_RUNS=str(self.root / "runs")))

        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(marker.exists(), (done.stdout, done.stderr))
        self.assertIn("unregistered package version", done.stderr)

    def test_direct_runner_rejects_changed_registered_package_before_qwen(self):
        project = self.root / "sandbox"
        bench = project / "eval" / "bench"
        bench.mkdir(parents=True)
        for name in ("run-bench.sh", "version-gate.py", "skill-version-registry.json"):
            shutil.copy2(HERE / name, bench / name)
        package = project / "skills" / "v44"
        shutil.copytree(HERE.parent.parent / "skills" / "v44", package)
        (package / "SKILL.md").write_text("changed registered bytes\n", encoding="utf-8")
        marker = self.root / "qwen-contacted"
        qwen = self.root / "qwen-tripwire"
        qwen.write_text("#!/usr/bin/env bash\nprintf contacted > %s\nexit 97\n" % marker,
                        encoding="utf-8")
        qwen.chmod(0o755)

        done = subprocess.run(
            ["bash", str(bench / "run-bench.sh"), "arbitrary-label"], text=True,
            capture_output=True, timeout=20,
            env=dict(os.environ, SHERLOCK_PACKAGE_VERSION="v44", QWEN_BIN=str(qwen),
                     BENCH_RUNS=str(self.root / "runs")))

        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(marker.exists(), (done.stdout, done.stderr))
        self.assertIn("registered package bytes changed", done.stderr)

    def test_direct_selector_keeps_arm_label_independent_and_legacy_v43_works(self):
        marker = self.root / "qwen-contacted"
        qwen = self.root / "qwen-tripwire"
        qwen.write_text("#!/usr/bin/env bash\nprintf contacted > %s\nexit 97\n" % marker,
                        encoding="utf-8")
        qwen.chmod(0o755)
        missing = self.root / "missing-corpus"
        base_env = dict(os.environ, QWEN_BIN=str(qwen), SHERLOCK_CORPUS=str(missing),
                        BENCH_RUNS=str(self.root / "runs"))

        selected = subprocess.run(
            ["bash", str(HERE / "run-bench.sh"), "arbitrary-label"], text=True,
            capture_output=True, timeout=20,
            env=dict(base_env, SHERLOCK_PACKAGE_VERSION="v44"))
        legacy = subprocess.run(
            ["bash", str(HERE / "run-bench.sh"), "v43"], text=True,
            capture_output=True, timeout=20, env=base_env)

        for done in (selected, legacy):
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("corpus not found", done.stderr)
            self.assertNotIn("unusable arm", done.stderr)
        self.assertFalse(marker.exists())

    def test_symlink_and_malformed_registry_are_rejected(self):
        (self.package / "bad").symlink_to(self.package / "SKILL.md")
        with self.assertRaises(VERSION_GATE.VersionGateError):
            VERSION_GATE.verify_version("v44", self.skills, self.registry, **self.kwargs)
        (self.package / "bad").unlink()
        self.registry.write_text('{"schema":1,"versions":{"v44":"nope"}}\n', encoding="utf-8")
        with self.assertRaises(VERSION_GATE.VersionGateError):
            VERSION_GATE.verify_version("v44", self.skills, self.registry, **self.kwargs)


if __name__ == "__main__":
    unittest.main()
