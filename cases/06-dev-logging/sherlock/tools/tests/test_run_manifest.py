#!/usr/bin/env python3
"""Provider-free contract tests for sealed benchmark manifests."""
import contextlib
import datetime as dt
import errno
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve()
SHERLOCK = HERE.parents[2]
TOOL = SHERLOCK / "eval" / "bench" / "run-manifest.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("run_manifest", str(TOOL))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_row(root, relative):
    data = (root / relative).read_bytes()
    return {"path": relative, "on_disk_bytes": len(data),
            "lines": data.count(b"\n"), "sha256": hashlib.sha256(data).hexdigest()}


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.corpus = self.root / "source"
        self.corpus.mkdir()
        (self.corpus / "host").mkdir()
        (self.corpus / "host" / "a.log").write_text("alpha\nbeta\n", encoding="utf-8")
        (self.corpus / "b.log").write_text("gamma\n", encoding="utf-8")
        self.key = self.root / "answer-key.json"
        self.key_data = {
            "dataset": "fixture", "files": [file_row(self.corpus, "b.log"),
                                                 file_row(self.corpus, "host/a.log")],
            "defects": [{"id": "F-01", "proof_locations": [
                {"file": "host/a.log", "line_start": 1, "line_end": 1}]},
                {"id": "F-02", "proof_locations": [
                    {"file": "b.log", "line_start": 1, "line_end": 1}]}]}
        self.write_key()
        self.staged = self.root / "target" / "corpus"
        self.trace = self.root / "trace"
        self.renderer = self._file("renderer.py", "render\n")
        self.prompt = self._file("prompt.txt", "investigate\n")
        self.skill = self.root / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("procedure\n", encoding="utf-8")
        self.runner = self._file("runner.sh", "run\n")
        self.scorer = self._file("scorer.py", "score\n")
        (self.skill / "tools").mkdir()
        self.triage = self._skill_file("triagecheck.py", "triage\n")
        self.stop = self._skill_file("stopcheck.py", "stop\n")
        self.citation = self._skill_file("citecheck.py", "cite\n")
        self.target_cli = self._file("qwen", "cli\n")
        self.parent = self._file("controller-parent.json", "{}\n")
        authority = self.root / "authority"
        authority.mkdir()
        records = authority / "records"
        records.mkdir()
        keys = authority / "keys"
        keys.mkdir()
        self.commitment = records / "run-commitments.jsonl"
        self.commitment_key = keys / "commitment.key"
        self.commitment_key.write_bytes(secrets.token_bytes(32))
        self.commitment_key.chmod(0o600)
        self.health = self.root / "health.json"
        self.write_health()
        self.profile = self._file("target-profile.json", json.dumps({
            "schema": 1,
            "provider_base_url": "https://provider.example/v1/",
            "route": "/chat/completions",
            "secret_ref": "LINKAPI_API_KEY",
            "requested_model": "qwen-target",
            "expected_returned_identity": "qwen-real",
            "identity_mode": "provider_pinned_version",
            "temperature": 0,
            "top_p": 1,
            "max_output_tokens": 1024,
            "session_token_limit": 4096,
            "cache": {"enabled": True},
            "interactive": {"enabled": False},
            "qwen": {"cli": "qwen"},
            "limits": {"requests": 1},
            "settings_sha256": "1" * 64,
            "system_prompt_sha256": "2" * 64,
            "skill_sha256": "3" * 64,
            "tool_schema_sha256": "4" * 64,
            "gate_sha256": {"reportcheck": "5" * 64, "citecheck": "6" * 64,
                            "statecheck": "7" * 64, "triagecheck": "8" * 64},
            "lane_guard": {"enabled": True},
        }))

    def _file(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def _skill_file(self, name, text):
        path = self.skill / "tools" / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_key(self):
        self.key.write_text(json.dumps(self.key_data), encoding="utf-8")

    def write_health(self, **updates):
        now = dt.datetime.now(dt.timezone.utc)
        row = {"schema": 1,
               "checked_at": (now - dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
               "expires_at": (now + dt.timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
               "lane": "paid", "provider": "linkapi", "requested_model": "qwen-target",
               "shape": "history", "tools": 25, "sizes_kb": [100, 250, 400],
               "history": [{"size_kb": n, "status": 200,
                            "returned_model": "qwen-real"} for n in (100, 250, 400)],
               "verdict": "HEALTHY"}
        row.update(updates)
        self.health.write_text(json.dumps(row), encoding="utf-8")

    def stage(self, **updates):
        args = {"source_corpus": str(self.corpus), "answer_key": str(self.key),
                "dataset": "fixture", "destination": str(self.staged),
                "forbid_paths": ()}
        args.update(updates)
        return MOD.stage_corpus(**args)

    def create(self, **updates):
        args = {"trace": str(self.trace), "run_tag": "run-001", "dataset": "fixture",
                "arm": "v28", "source_corpus": str(self.corpus),
                "answer_key": str(self.key), "renderer": str(self.renderer),
                "prompt": str(self.prompt), "skill_root": str(self.skill),
                "runner": str(self.runner), "scorer": str(self.scorer),
                "triage_checker": str(self.triage), "stop_checker": str(self.stop),
                "citation_checker": str(self.citation), "target_cli": str(self.target_cli),
                "target_version": "0.21.1", "requested_model": "qwen-target",
                "provider": "linkapi", "expected_returned_identity": "qwen-real",
                "lane": "paid", "health_receipt": str(self.health),
                "target_profile": str(self.profile),
                "controller_parent": str(self.parent),
                "commitment_file": str(self.commitment),
                "commitment_key": str(self.commitment_key),
                "staged_corpus_destination": str(self.staged), "forbid_paths": ()}
        args.update(updates)
        return MOD.create_manifest(**args)

    def verify(self, **updates):
        args = {"trace": str(self.trace), "commitment_file": str(self.commitment),
                "commitment_key": str(self.commitment_key)}
        args.update(updates)
        return MOD.verify_manifest(**args)

    def create_argv(self):
        values = (("run-tag", "run-001"), ("dataset", "fixture"), ("arm", "v28"),
                  ("source-corpus", self.corpus), ("answer-key", self.key),
                  ("renderer", self.renderer), ("prompt", self.prompt),
                  ("skill-root", self.skill), ("runner", self.runner),
                  ("scorer", self.scorer), ("triage-checker", self.triage),
                  ("stop-checker", self.stop), ("citation-checker", self.citation),
                  ("target-cli", self.target_cli), ("target-version", "0.21.1"),
                  ("requested-model", "qwen-target"), ("provider", "linkapi"),
                  ("expected-returned-identity", "qwen-real"), ("lane", "paid"),
                  ("health-receipt", self.health), ("target-profile", self.profile),
                  ("controller-parent", self.parent),
                  ("commitment-file", self.commitment),
                  ("commitment-key", self.commitment_key),
                  ("staged-corpus-destination", self.staged))
        argv = [str(TOOL), "create", str(self.trace)]
        for name, value in values:
            argv.extend(("--" + name, str(value)))
        return argv

    def verify_argv(self):
        return [str(TOOL), "verify", str(self.trace),
                "--commitment-file", str(self.commitment),
                "--commitment-key", str(self.commitment_key)]


MOD = load_tool()


class RunManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def valid_manifest(self):
        staged = self.fx.stage()
        manifest = self.fx.create()
        verified = self.fx.verify()
        self.assertEqual(staged["included_count"], 2)
        self.assertEqual(manifest["expected"]["ids"], ["F-01", "F-02"])
        self.assertEqual(verified["manifest_sha256"], manifest["manifest_sha256"])
        return manifest

    def test_valid_create_stage_verify(self):
        manifest = self.valid_manifest()
        self.assertNotIn("defects", json.dumps(manifest))
        self.assertNotIn("proof_locations", json.dumps(manifest))
        self.assertEqual(manifest["corpus"]["excluded"]["count"], 0)
        self.assertRegex(manifest["target"]["identity_sha256"], r"^[0-9a-f]{64}$")

    def test_target_profile_is_bound_and_unknown_fields_fail(self):
        self.fx.stage()
        manifest = self.fx.create()
        self.assertEqual(manifest["target_profile"]["sha256"],
                         hashlib.sha256(self.fx.profile.read_bytes()).hexdigest())
        profile = json.loads(self.fx.profile.read_text())
        profile["surprise"] = True
        self.fx.profile.write_text(json.dumps(profile))
        with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_PROFILE_SCHEMA"):
            self.fx.create(trace=str(self.fx.root / "other-trace"), run_tag="run-002")

    def test_target_profile_requires_a_bounded_secret_reference(self):
        self.fx.stage()
        profile = json.loads(self.fx.profile.read_text())
        profile["secret_ref"] = "x" * 129
        self.fx.profile.write_text(json.dumps(profile))
        with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_PROFILE_SCHEMA"):
            self.fx.create()

    def test_compare_allows_only_declared_model_fields(self):
        left = self.valid_manifest()["input_identity"]
        right = json.loads(json.dumps(left))
        right["provider"] = "other"
        right["requested_model"] = "other-model"
        row = MOD.compare_inputs(left, right, ("provider", "requested_model"))
        self.assertEqual(row["verdict"], "declared_differences_only")
        right["settings_sha256"] = "0" * 64
        row = MOD.compare_inputs(left, right, ("provider", "requested_model"))
        self.assertEqual(row["verdict"], "incomparable")
        self.assertEqual(row["undeclared"], ["settings_sha256"])

    def test_compare_rejects_incomplete_or_malformed_input_identities(self):
        for left, right in (({"schema": 1, "x": None}, {"schema": 1}),
                            ({"raw_prompt_sha256": "a" * 64},
                             {"raw_prompt_sha256": "b" * 64})):
            with self.subTest(left=left, right=right):
                with self.assertRaisesRegex(MOD.ManifestError, "E_INPUT_IDENTITY_SCHEMA"):
                    MOD.compare_inputs(left, right)
        left = self.valid_manifest()["input_identity"]
        right = json.loads(json.dumps(left))
        right["arm_commit"] = None
        with self.assertRaisesRegex(MOD.ManifestError, "E_INPUT_IDENTITY_SCHEMA"):
            MOD.compare_inputs(left, right)

    def test_target_profile_secret_ref_accepts_only_environment_references(self):
        for value in ("sk_live_SYNTHETIC123456", "hf_SYNTHETIC123456",
                      "pplx-SYNTHETIC123456", "token=synthetic"):
            with self.subTest(value=value):
                fx = Fixture(self.root_for("secret-ref-" + value.replace("=", "-")))
                fx.stage()
                profile = json.loads(fx.profile.read_text())
                profile["secret_ref"] = value
                fx.profile.write_text(json.dumps(profile))
                with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_PROFILE_SCHEMA"):
                    fx.create()

    def test_target_profile_nested_controls_are_exact_and_typed(self):
        invalid = (
            ("cache-unknown", "cache", {"enabled": True, "unknown": False}),
            ("interactive-missing", "interactive", {}),
            ("qwen-wrong-type", "qwen", {"cli": True}),
            ("lane-guard-wrong-type", "lane_guard", {"enabled": "true"}),
            ("limit-zero", "limits", {"requests": 0}),
            ("limit-negative", "limits", {"requests": -1}),
            ("limit-float", "limits", {"requests": 1.5}),
            ("limit-bool", "limits", {"requests": True}),
            ("limit-null", "limits", {"requests": None}),
        )
        for label, field, value in invalid:
            with self.subTest(label=label):
                fx = Fixture(self.root_for("nested-" + label))
                fx.stage()
                profile = json.loads(fx.profile.read_text())
                profile[field] = value
                fx.profile.write_text(json.dumps(profile))
                with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_PROFILE_SCHEMA"):
                    fx.create()

    def test_target_profile_url_is_strict_and_removes_one_trailing_slash(self):
        profile = json.loads(self.fx.profile.read_text())
        for value in (" https://example.test/v1", "https://example.test/v1\n",
                      "https://example.test/a b", "https://example.test/%zz",
                      "https://exa_mple.test/v1", "https://example.test\\v1",
                      "https://example.test:/v1", "https://example.test:0/v1",
                      "https://example.test:65536/v1"):
            with self.subTest(value=value):
                candidate = dict(profile)
                candidate["provider_base_url"] = value
                with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_PROFILE_SCHEMA"):
                    MOD.validate_target_profile(candidate)
        profile["provider_base_url"] = "HTTPS://EXAMPLE.TEST:443/v1///"
        self.assertEqual(MOD.validate_target_profile(profile)["provider_base_url"],
                         "https://example.test/v1//")

    def test_target_profile_rejects_duplicate_json_keys_at_every_depth(self):
        for label, old, new in (
                ("top", '"schema": 1', '"schema": 99, "schema": 1'),
                ("nested", '"cache": {"enabled": true}',
                 '"cache": {"enabled": true, "enabled": false}')):
            with self.subTest(label=label):
                fx = Fixture(self.root_for("duplicate-profile-" + label))
                fx.stage()
                profile = fx.profile.read_text()
                fx.profile.write_text(profile.replace(old, new, 1))
                with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_PROFILE_JSON"):
                    fx.create()

    def test_compare_cli_rejects_duplicate_nested_json_keys(self):
        identity = self.valid_manifest()["input_identity"]
        left = self.fx._file("duplicate-left.json", json.dumps(identity).replace(
            '"cache": {"enabled": true}', '"cache": {"enabled": true, "enabled": false}', 1))
        right = self.fx._file("duplicate-right.json", json.dumps(identity))
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", [str(TOOL), "compare", str(left), str(right), "--json"]), \
                contextlib.redirect_stderr(stderr):
            code = MOD.main()
        self.assertEqual(code, 2)
        self.assertIn("E_COMPARE_JSON", stderr.getvalue())

    def test_canonical_prompt_only_normalizes_staged_root(self):
        a = MOD.canonical_prompt(b"read /tmp/a/corpus/Security.jsonl\n", "/tmp/a/corpus")
        b = MOD.canonical_prompt(b"read /tmp/b/corpus/Security.jsonl\n", "/tmp/b/corpus")
        self.assertEqual(a, b)
        self.assertEqual(a, b"read ${CORPUS_ROOT}/Security.jsonl\n")

    def test_canonical_prompt_does_not_normalize_sibling_prefixes(self):
        a = MOD.canonical_prompt(b"read /tmp/a/corpus-archive/Security.jsonl\n", "/tmp/a/corpus")
        b = MOD.canonical_prompt(b"read /tmp/b/corpus-archive/Security.jsonl\n", "/tmp/b/corpus")
        self.assertEqual(a, b"read /tmp/a/corpus-archive/Security.jsonl\n")
        self.assertEqual(b, b"read /tmp/b/corpus-archive/Security.jsonl\n")
        self.assertNotEqual(a, b)
        self.assertEqual(MOD.canonical_prompt(b"read /tmp/a/corpus:archive\n", "/tmp/a/corpus"),
                         b"read /tmp/a/corpus:archive\n")

    @unittest.skipUnless(os.path.islink("/tmp"), "/tmp is not a system symlink")
    def test_clean_abs_canonicalizes_system_tmp_alias(self):
        self.assertEqual(MOD.clean_abs("/tmp/compare-input.json"),
                         os.path.realpath("/tmp/compare-input.json"))

    def test_compare_names_raw_path_only_prompt_difference(self):
        left = self.valid_manifest()["input_identity"]
        right = json.loads(json.dumps(left))
        right["raw_prompt_sha256"] = "f" * 64
        row = MOD.compare_inputs(left, right, ())
        self.assertEqual(row["verdict"], "declared_differences_only")
        self.assertEqual(row["differences"], ["prompt_path_only"])

    def test_checkers_must_be_owned_by_selected_skill_version(self):
        self.fx.stage()
        outside = self.fx._file("outside-checker.py", "check\n")
        with self.assertRaisesRegex(MOD.ManifestError, "E_CHECKER_NOT_VERSION_OWNED"):
            self.fx.create(triage_checker=str(outside))

    def test_manifest_collision_is_refused(self):
        self.fx.stage(); self.fx.create()
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_DUPLICATE"):
            self.fx.create()

    def test_wrong_dataset_is_refused(self):
        with self.assertRaisesRegex(MOD.ManifestError, "E_DATASET_MISMATCH"):
            self.fx.stage(dataset="other")

    def test_missing_and_duplicate_entries_are_refused(self):
        self.fx.key_data["files"][0]["path"] = "missing.log"; self.fx.write_key()
        with self.assertRaisesRegex(MOD.ManifestError, "E_CORPUS_FILE_MISSING"):
            self.fx.stage()
        self.fx.key_data["files"] = [file_row(self.fx.corpus, "b.log")] * 2; self.fx.write_key()
        with self.assertRaisesRegex(MOD.ManifestError, "E_CORPUS_PATH_DUPLICATE"):
            self.fx.stage()

    def test_traversal_and_symlink_entries_are_refused(self):
        self.fx.key_data["files"][0]["path"] = "../outside.log"; self.fx.write_key()
        with self.assertRaisesRegex(MOD.ManifestError, "E_KEY_PATH"):
            self.fx.stage()
        self.fx.key_data["files"] = [file_row(self.fx.corpus, "b.log")]
        self.fx.key_data["files"][0]["path"] = "link.log"
        os.symlink("b.log", self.fx.corpus / "link.log"); self.fx.write_key()
        with self.assertRaisesRegex(MOD.ManifestError, "E_CORPUS_SYMLINK"):
            self.fx.stage()

    def test_symlinked_staging_parent_is_refused(self):
        real = self.fx.root / "real-target"; real.mkdir()
        linked = self.fx.root / "linked-target"; os.symlink(real, linked)
        with self.assertRaisesRegex(MOD.ManifestError, "E_STAGE_SYMLINK"):
            self.fx.stage(destination=str(linked / "corpus"))

    def test_nested_symlinked_stage_and_trace_parents_are_refused(self):
        real = self.fx.root / "real"; (real / "existing").mkdir(parents=True)
        linked = self.fx.root / "linked"; os.symlink(real, linked)
        with self.assertRaisesRegex(MOD.ManifestError, "E_STAGE_SYMLINK"):
            self.fx.stage(destination=str(linked / "existing" / "stage"))
        self.fx.stage()
        with self.assertRaisesRegex(MOD.ManifestError, "E_TRACE_SYMLINK"):
            self.fx.create(trace=str(linked / "existing" / "trace"))

    def test_open_source_fd_survives_parent_swap_without_escape(self):
        evil = self.fx.root / "evil"; (evil / "host").mkdir(parents=True)
        (evil / "host" / "a.log").write_text("evil\n", encoding="utf-8")
        root_fd = MOD._open_dir(str(self.fx.corpus), "CORPUS")
        moved = self.fx.root / "source-moved"
        os.rename(self.fx.corpus, moved); os.symlink(evil, self.fx.corpus)
        try:
            data = MOD._read_relative(root_fd, "host/a.log", "CORPUS")
        finally:
            os.close(root_fd)
        self.assertEqual(data, b"alpha\nbeta\n")

    def test_size_line_and_hash_mismatch_are_refused(self):
        for field, code in (("on_disk_bytes", "E_CORPUS_SIZE_MISMATCH"),
                            ("lines", "E_CORPUS_LINE_MISMATCH"),
                            ("sha256", "E_CORPUS_HASH_MISMATCH")):
            with self.subTest(field=field):
                fx = Fixture(self.root_for(field))
                fx.key_data["files"][0][field] = (999 if field != "sha256" else "0" * 64)
                fx.write_key()
                with self.assertRaisesRegex(MOD.ManifestError, code): fx.stage()

    def root_for(self, name):
        path = Path(self.temp.name) / name
        path.mkdir()
        return path

    def test_missing_proof_path_is_refused(self):
        self.fx.key_data["defects"][0]["proof_locations"][0]["file"] = "absent.log"
        self.fx.write_key()
        with self.assertRaisesRegex(MOD.ManifestError, "E_PROOF_FILE_MISSING"):
            self.fx.stage()

    def test_answer_key_inside_target_workspace_is_refused(self):
        self.fx.stage(); self.fx.trace.mkdir()
        unsafe = self.fx.trace / "answer-key.json"
        unsafe.write_bytes(self.fx.key.read_bytes())
        with self.assertRaisesRegex(MOD.ManifestError, "E_KEY_TARGET_VISIBLE"):
            self.fx.create(answer_key=str(unsafe))

    def test_forbidden_labels_facts_attacker_and_custom_paths_are_refused(self):
        for relative in ("labels/events.log", "facts.json", "attacker-only/proof.log",
                         "private/hidden.log"):
            with self.subTest(relative=relative):
                fx = Fixture(self.root_for(relative.replace("/", "-")))
                path = fx.corpus / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n", encoding="utf-8")
                fx.key_data["files"].append(file_row(fx.corpus, relative)); fx.write_key()
                kwargs = {"forbid_paths": ("private/hidden.log",)} if relative.startswith("private") else {}
                with self.assertRaisesRegex(MOD.ManifestError, "E_FORBIDDEN_STAGED_PATH"):
                    fx.stage(**kwargs)

    def test_extra_source_file_is_excluded(self):
        (self.fx.corpus / "extra.log").write_text("not allowlisted\n", encoding="utf-8")
        staged = self.fx.stage(); manifest = self.fx.create()
        self.assertFalse((self.fx.staged / "extra.log").exists())
        self.assertEqual(staged["excluded_count"], 1)
        self.assertEqual(manifest["corpus"]["excluded"]["count"], 1)

    def test_staged_tamper_is_rejected(self):
        self.valid_manifest()
        (self.fx.staged / "b.log").write_text("omega\n", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_STAGED_HASH_MISMATCH"):
            self.fx.verify()

    def test_artifact_tamper_is_rejected(self):
        targets = (("renderer", "E_RENDERER_DIGEST_MISMATCH"),
                   ("prompt", "E_PROMPT_DIGEST_MISMATCH"),
                   ("runner", "E_RUNNER_DIGEST_MISMATCH"),
                   ("scorer", "E_SCORER_DIGEST_MISMATCH"),
                   ("triage", "E_TRIAGE_CHECKER_DIGEST_MISMATCH"),
                   ("stop", "E_STOP_CHECKER_DIGEST_MISMATCH"),
                   ("citation", "E_CITATION_CHECKER_DIGEST_MISMATCH"))
        for attr, code in targets:
            with self.subTest(attr=attr):
                fx = Fixture(self.root_for("tamper-" + attr)); fx.stage(); fx.create()
                Path(getattr(fx, attr)).write_text("changed\n", encoding="utf-8")
                with self.assertRaisesRegex(MOD.ManifestError, code):
                    fx.verify()
        fx = Fixture(self.root_for("tamper-skill")); fx.stage(); fx.create()
        (fx.skill / "SKILL.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_SKILL_DIGEST_MISMATCH"):
            fx.verify()

    def test_answer_key_target_cli_parent_and_health_tamper_are_rejected(self):
        targets = (("key", "E_ANSWER_KEY_DIGEST_MISMATCH"),
                   ("target_cli", "E_TARGET_CLI_DIGEST_MISMATCH"),
                   ("parent", "E_CONTROLLER_PARENT_DIGEST_MISMATCH"),
                   ("health", "E_HEALTH_RECEIPT_DIGEST_MISMATCH"))
        for attr, code in targets:
            with self.subTest(attr=attr):
                fx = Fixture(self.root_for("direct-" + attr)); fx.stage(); fx.create()
                path = Path(getattr(fx, attr))
                if attr == "health":
                    row = json.loads(path.read_text()); row["note"] = "changed"
                    path.write_text(json.dumps(row), encoding="utf-8")
                else:
                    path.write_text("changed\n", encoding="utf-8")
                with self.assertRaisesRegex(MOD.ManifestError, code):
                    fx.verify()

    def test_answer_key_validation_and_digest_use_one_snapshot(self):
        self.fx.stage()
        original_bytes = self.fx.key.read_bytes()
        original_read = MOD._read_path
        reads = {"key": 0}
        def alternate_second_read(path, prefix):
            data, canonical_path = original_read(path, prefix)
            if canonical_path == MOD.clean_abs(str(self.fx.key)):
                reads["key"] += 1
                if reads["key"] == 2:
                    return b'{"dataset":"replacement"}', canonical_path
            return data, canonical_path
        with mock.patch.object(MOD, "_read_path", side_effect=alternate_second_read):
            manifest = self.fx.create()
        self.assertEqual(manifest["artifacts"]["answer_key"]["sha256"],
                         hashlib.sha256(original_bytes).hexdigest())
        self.fx.verify()

    def test_health_validation_and_digest_use_one_snapshot(self):
        self.fx.stage()
        original_bytes = self.fx.health.read_bytes()
        original_read = MOD._read_path
        reads = {"health": 0}
        def alternate_second_read(path, prefix):
            data, canonical_path = original_read(path, prefix)
            if canonical_path == MOD.clean_abs(str(self.fx.health)):
                reads["health"] += 1
                if reads["health"] == 2:
                    return b'{"schema":1}', canonical_path
            return data, canonical_path
        with mock.patch.object(MOD, "_read_path", side_effect=alternate_second_read):
            manifest = self.fx.create()
        self.assertEqual(manifest["health_receipt"]["sha256"],
                         hashlib.sha256(original_bytes).hexdigest())
        self.fx.verify()

    def test_resealed_manifest_tamper_is_rejected_by_external_commitment(self):
        manifest = self.valid_manifest()
        path = self.fx.trace / "run-manifest.json"
        manifest["run_tag"] = "run-tampered"
        unsigned = dict(manifest); unsigned.pop("manifest_sha256")
        manifest["manifest_sha256"] = hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_MISMATCH"):
            self.fx.verify()

    def test_resealed_manifest_and_jsonl_without_key_is_rejected(self):
        manifest = self.valid_manifest()
        manifest["run_tag"] = "rebound-run"
        unsigned = dict(manifest); unsigned.pop("manifest_sha256")
        manifest["manifest_sha256"] = hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        (self.fx.trace / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        commitment = json.loads(self.fx.commitment.read_text())
        commitment["run_tag"] = "rebound-run"
        commitment["manifest_sha256"] = manifest["manifest_sha256"]
        self.fx.commitment.write_text(json.dumps(commitment) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_AUTH"):
            self.fx.verify()

    def test_wrong_and_tampered_commitment_keys_are_rejected(self):
        self.valid_manifest()
        wrong = self.fx.root / "wrong.key"
        wrong.write_bytes(bytes(range(32, 64))); wrong.chmod(0o600)
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_AUTH"):
            self.fx.verify(commitment_key=str(wrong))
        self.fx.commitment_key.write_bytes(bytes(range(1, 33)))
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_AUTH"):
            self.fx.verify()

    def test_commitment_key_path_mode_and_size_are_enforced(self):
        def key_at(path, data=None, mode=0o600):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data if data is not None else secrets.token_bytes(32)); path.chmod(mode)
            return str(path)

        cases = []
        for name, placement in (("source", lambda fx: fx.corpus / "controller.key"),
                                ("trace", lambda fx: fx.trace / "controller.key"),
                                ("stage", lambda fx: fx.staged / "controller.key"),
                                ("skill", lambda fx: fx.skill / "controller.key"),
                                ("commitment", lambda fx: fx.commitment.parent / "controller.key")):
            cases.append((name, placement, "E_COMMITMENT_KEY_LOCATION"))
        for index, (name, placement, code) in enumerate(cases):
            with self.subTest(case=name):
                fx = Fixture(self.root_for("key-location-%d" % index)); fx.stage()
                with self.assertRaisesRegex(MOD.ManifestError, code):
                    fx.create(commitment_key=key_at(placement(fx)))
        fx = Fixture(self.root_for("key-mode")); fx.stage(); fx.commitment_key.chmod(0o644)
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_KEY_MODE"):
            fx.create()
        fx = Fixture(self.root_for("key-short")); fx.stage(); fx.commitment_key.write_bytes(b"short")
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_KEY_BOUNDS"):
            fx.create()
        fx = Fixture(self.root_for("key-long")); fx.stage(); fx.commitment_key.write_bytes(b"x" * 33)
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_KEY_BOUNDS"):
            fx.create()
        fx = Fixture(self.root_for("key-symlink")); fx.stage()
        real = fx.root / "real.key"; key_at(real)
        linked = fx.root / "linked.key"; os.symlink(real, linked)
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_KEY_SYMLINK"):
            fx.create(commitment_key=str(linked))

    def test_commitment_key_is_not_persisted_or_emitted(self):
        manifest = self.valid_manifest()
        serialized = json.dumps(manifest) + self.fx.commitment.read_text()
        self.assertNotIn(str(self.fx.commitment_key), serialized)
        self.assertNotIn(self.fx.commitment_key.read_bytes().hex(), serialized)
        self.assertRegex(json.loads(self.fx.commitment.read_text())["key_id"], r"^[0-9a-f]{64}$")

    def test_commitment_key_bytes_are_opaque_controller_provisioning(self):
        self.fx.commitment_key.write_bytes(b"x" * 32)
        before = self.fx.commitment_key.read_bytes()
        self.valid_manifest()
        self.assertEqual(self.fx.commitment_key.read_bytes(), before)
        for path in self.fx.root.rglob("*"):
            if path.is_file() and path != self.fx.commitment_key:
                self.assertNotIn(before, path.read_bytes(), str(path))

    def test_target_identity_seal_tamper_is_rejected(self):
        manifest = self.valid_manifest()
        manifest["target"]["identity_sha256"] = "0" * 64
        unsigned = dict(manifest); unsigned.pop("manifest_sha256")
        manifest["manifest_sha256"] = hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        (self.fx.trace / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        commitment = json.loads(self.fx.commitment.read_text())
        commitment["manifest_sha256"] = manifest["manifest_sha256"]
        payload = dict(commitment); payload.pop("hmac_sha256")
        commitment["hmac_sha256"] = hmac.new(
            self.fx.commitment_key.read_bytes(),
            json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        self.fx.commitment.write_text(json.dumps(commitment) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_TARGET_IDENTITY_MISMATCH"):
            self.fx.verify()

    def test_commitment_tamper_and_conflicting_run_tag_are_refused(self):
        self.valid_manifest()
        self.fx.commitment.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_MISSING"):
            self.fx.verify()
        shared_dir = Path(self.temp.name) / "shared-records"; shared_dir.mkdir()
        shared = shared_dir / "shared-commitments.jsonl"
        one = Fixture(self.root_for("commit-one")); one.stage(); one.create(commitment_file=str(shared))
        two = Fixture(self.root_for("commit-two")); two.stage()
        two.commitment_key.write_bytes(one.commitment_key.read_bytes())
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_CONFLICT"):
            two.create(commitment_file=str(shared))

    def test_missing_commitment_verify_does_not_create_it(self):
        self.valid_manifest()
        self.fx.commitment.unlink()
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_MISSING"):
            self.fx.verify()
        self.assertFalse(self.fx.commitment.exists())

    def test_missing_commitment_file_and_parent_have_stable_cli_error(self):
        for missing_parent in (False, True):
            with self.subTest(missing_parent=missing_parent):
                fx = Fixture(self.root_for("missing-parent-%s" % missing_parent))
                fx.stage(); fx.create(); fx.commitment.unlink()
                if missing_parent:
                    fx.commitment.parent.rmdir()
                result = subprocess.run([
                    sys.executable, str(TOOL), "verify", str(fx.trace),
                    "--commitment-file", str(fx.commitment),
                    "--commitment-key", str(fx.commitment_key)],
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertRegex(result.stderr, r"^E_COMMITMENT_MISSING:")
                self.assertNotIn("Traceback", result.stderr)

    def test_manifest_error_has_structured_code(self):
        with self.assertRaises(MOD.ManifestError) as caught:
            self.fx.stage(dataset="wrong")
        self.assertEqual(caught.exception.code, "E_DATASET_MISMATCH")

    def test_commitment_must_remain_outside_target_roots(self):
        self.fx.stage()
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_LOCATION"):
            self.fx.create(commitment_file=str(self.fx.trace / "commitments.jsonl"))

    def test_health_stale_wrong_lane_shape_and_mixed_identity_are_refused(self):
        now = dt.datetime.now(dt.timezone.utc)
        cases = (({"expires_at": (now - dt.timedelta(seconds=1)).isoformat()}, "E_HEALTH_STALE"),
                 ({"lane": "other"}, "E_HEALTH_IDENTITY_MISMATCH"),
                 ({"shape": "plain"}, "E_HEALTH_SHAPE"),
                 ({"tools": 24}, "E_HEALTH_TOOLS"),
                 ({"sizes_kb": [100, 250]}, "E_HEALTH_SIZES"),
                 ({"history": [{"size_kb": 100, "status": 200, "returned_model": "qwen-real"},
                                {"size_kb": 250, "status": 200, "returned_model": "other"},
                                {"size_kb": 400, "status": 200, "returned_model": "qwen-real"}]},
                  "E_HEALTH_RETURNED_IDENTITY"))
        for updates, code in cases:
            with self.subTest(code=code):
                fx = Fixture(self.root_for("health-" + code.lower())); fx.stage()
                fx.write_health(**updates)
                with self.assertRaisesRegex(MOD.ManifestError, code): fx.create()

    def test_target_identity_and_health_schema_mismatch_are_refused(self):
        self.fx.stage()
        self.fx.write_health(requested_model="wrong")
        with self.assertRaisesRegex(MOD.ManifestError, "E_HEALTH_IDENTITY_MISMATCH"):
            self.fx.create()
        self.fx.write_health(schema=2)
        with self.assertRaisesRegex(MOD.ManifestError, "E_HEALTH_SCHEMA"):
            self.fx.create()

    def test_health_maximum_age_lifetime_and_future_skew_are_enforced(self):
        now = dt.datetime.now(dt.timezone.utc)
        cases = (({"checked_at": (now - dt.timedelta(minutes=16)).isoformat(),
                   "expires_at": (now + dt.timedelta(minutes=1)).isoformat()}, "E_HEALTH_STALE"),
                 ({"checked_at": now.isoformat(),
                   "expires_at": (now + dt.timedelta(minutes=16)).isoformat()}, "E_HEALTH_LIFETIME"),
                 ({"checked_at": (now + dt.timedelta(seconds=61)).isoformat(),
                   "expires_at": (now + dt.timedelta(minutes=2)).isoformat()}, "E_HEALTH_FUTURE"))
        for updates, code in cases:
            with self.subTest(code=code):
                fx = Fixture(self.root_for(code.lower())); fx.stage(); fx.write_health(**updates)
                with self.assertRaisesRegex(MOD.ManifestError, code): fx.create()

    def test_verification_uses_authenticated_commit_time_for_health_freshness(self):
        self.fx.stage()
        self.fx.create()
        real_datetime = dt.datetime
        verified_later = real_datetime.now(dt.timezone.utc) + dt.timedelta(minutes=20)

        class LaterDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return verified_later if tz is not None else verified_later.replace(tzinfo=None)

        with mock.patch.object(MOD.dt, "datetime", LaterDatetime):
            self.fx.verify()

    def test_authenticated_commit_time_must_fall_inside_health_window(self):
        now = dt.datetime.now(dt.timezone.utc)
        cases = (("expired", now + dt.timedelta(minutes=11), "E_HEALTH_STALE"),
                 ("before-check", now - dt.timedelta(minutes=3), "E_HEALTH_FUTURE"))
        for name, committed_at, code in cases:
            with self.subTest(name=name):
                fx = Fixture(self.root_for("commit-time-" + name)); fx.stage(); fx.create()
                row = json.loads(fx.commitment.read_text(encoding="utf-8"))
                row["committed_at"] = committed_at.isoformat().replace("+00:00", "Z")
                payload = dict(row); payload.pop("hmac_sha256")
                row["hmac_sha256"] = hmac.new(
                    fx.commitment_key.read_bytes(),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
                fx.commitment.write_text(json.dumps(row) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(MOD.ManifestError, code):
                    fx.verify()

    def test_malformed_key_schema_has_stable_errors(self):
        cases = (({"files": None}, "E_KEY_FILES"),
                 ({"files": [None]}, "E_KEY_FILE_ENTRY"),
                 ({"defects": None}, "E_KEY_DEFECTS"),
                 ({"defects": [None]}, "E_KEY_DEFECTS"),
                 ({"files.0.path": 7}, "E_KEY_PATH"),
                 ({"files.0.lines": "two"}, "E_KEY_FILE_ENTRY"),
                 ({"defects.0.proof_locations": None}, "E_KEY_PROOFS"),
                 ({"defects.0.proof_locations": ["bad"]}, "E_KEY_PROOF"))
        for index, (mutation, code) in enumerate(cases):
            with self.subTest(code=code, mutation=mutation):
                fx = Fixture(self.root_for("key-malformed-%d" % index))
                key, value = next(iter(mutation.items()))
                if key == "files": fx.key_data["files"] = value
                elif key == "defects": fx.key_data["defects"] = value
                elif key.startswith("files.0."): fx.key_data["files"][0][key.split(".")[-1]] = value
                elif key == "defects.0.proof_locations": fx.key_data["defects"][0]["proof_locations"] = value
                fx.write_key()
                with self.assertRaisesRegex(MOD.ManifestError, code): fx.stage()

    def test_malformed_health_schema_has_stable_errors(self):
        cases = (({"schema": "1"}, "E_HEALTH_SCHEMA"),
                 ({"checked_at": []}, "E_HEALTH_TIME"),
                 ({"lane": []}, "E_HEALTH_IDENTITY"),
                 ({"tools": "25"}, "E_HEALTH_TOOLS"),
                 ({"sizes_kb": [{}]}, "E_HEALTH_SIZES"),
                 ({"history": None}, "E_HEALTH_HISTORY"),
                 ({"history": [None]}, "E_HEALTH_HISTORY"),
                 ({"history": [{"size_kb": 100, "status": 200,
                                 "returned_model": {}}]}, "E_HEALTH_RETURNED_IDENTITY"),
                 ({"verdict": []}, "E_HEALTH_VERDICT"))
        for index, (updates, code) in enumerate(cases):
            with self.subTest(code=code):
                fx = Fixture(self.root_for("health-malformed-%d" % index)); fx.stage()
                fx.write_health(**updates)
                with self.assertRaisesRegex(MOD.ManifestError, code): fx.create()

    def test_cli_identity_labels_are_bounded(self):
        self.fx.stage()
        for value in ("bad\nidentifier", "x" * 129):
            with self.subTest(value_len=len(value)):
                with self.assertRaisesRegex(MOD.ManifestError, "E_IDENTIFIER_INVALID"):
                    self.fx.create(run_tag=value)

    def test_commitment_write_failure_rolls_back_without_manifest(self):
        self.fx.stage()
        with mock.patch.object(MOD.os, "write",
                               side_effect=OSError(errno.ENOSPC, "injected full write")):
            with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_WRITE"):
                self.fx.create()
        self.assertFalse((self.fx.trace / "run-manifest.json").exists())
        self.assertEqual(self.fx.commitment.read_bytes(), b"")
        self.fx.create()
        self.fx.verify()

    def test_partial_commitment_write_failure_rolls_back(self):
        self.fx.stage()
        real_write = MOD.os.write
        calls = 0

        def partial_then_fail(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(fd, data[:7])
            raise OSError(errno.ENOSPC, "injected partial write")

        with mock.patch.object(MOD.os, "write", side_effect=partial_then_fail):
            with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_WRITE"):
                self.fx.create()
        self.assertFalse((self.fx.trace / "run-manifest.json").exists())
        self.assertEqual(self.fx.commitment.read_bytes(), b"")
        self.fx.create()
        self.fx.verify()

    def test_short_commitment_writes_are_completed(self):
        self.fx.stage()
        real_write = MOD.os.write

        def short_write(fd, data):
            return real_write(fd, data[:min(7, len(data))])

        with mock.patch.object(MOD.os, "write", side_effect=short_write):
            self.fx.create()
        self.assertEqual(len(self.fx.commitment.read_text().splitlines()), 1)
        json.loads(self.fx.commitment.read_text())
        self.fx.verify()

    def test_hard_death_after_short_write_repairs_final_fragment(self):
        self.fx.stage()
        real_write = MOD.os.write
        calls = 0

        def partial_then_die(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(fd, data[:7])
            raise SystemExit(91)

        with mock.patch.object(MOD.os, "write", side_effect=partial_then_die):
            with self.assertRaises(SystemExit):
                self.fx.create()
        self.assertEqual(len(self.fx.commitment.read_bytes()), 7)
        self.assertFalse((self.fx.trace / "run-manifest.json").exists())
        self.fx.create()
        self.fx.verify()
        self.assertEqual(len(self.fx.commitment.read_text().splitlines()), 1)

    def test_newline_terminated_malformed_commitment_stays_fail_closed(self):
        self.valid_manifest()
        malformed = self.fx.commitment.read_bytes() + b'{"incomplete":true}\n'
        self.fx.commitment.write_bytes(malformed)
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_SCHEMA"):
            self.fx.create()
        self.assertEqual(self.fx.commitment.read_bytes(), malformed)
        self.assertTrue((self.fx.trace / "run-manifest.json").exists())

    def test_premature_commitment_eof_has_stable_race_error(self):
        self.fx.commitment.write_bytes(b"{}\n")
        fd = os.open(self.fx.commitment, os.O_RDONLY)
        try:
            with mock.patch.object(MOD.os, "read",
                                   side_effect=(b"", AssertionError("read retried after EOF"))):
                with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_RACE"):
                    MOD._commitment_rows(fd)
        finally:
            os.close(fd)

    def test_create_lock_acquisition_retries_eintr(self):
        self.fx.stage()
        real_flock = MOD.fcntl.flock
        interrupted = False

        def inject(fd, operation):
            nonlocal interrupted
            if operation == MOD.fcntl.LOCK_EX and not interrupted:
                interrupted = True
                raise OSError(errno.EINTR, "injected interrupt")
            return real_flock(fd, operation)

        with mock.patch.object(MOD.fcntl, "flock", side_effect=inject):
            self.fx.create()
        self.assertTrue(interrupted)
        self.fx.verify()

    def test_verify_lock_acquisition_retries_eintr_without_mutation(self):
        self.valid_manifest()
        before = self.fx.commitment.read_bytes()
        real_flock = MOD.fcntl.flock
        interrupted = False

        def inject(fd, operation):
            nonlocal interrupted
            if operation == MOD.fcntl.LOCK_SH and not interrupted:
                interrupted = True
                raise OSError(errno.EINTR, "injected interrupt")
            return real_flock(fd, operation)

        with mock.patch.object(MOD.fcntl, "flock", side_effect=inject):
            self.fx.verify()
        self.assertTrue(interrupted)
        self.assertEqual(self.fx.commitment.read_bytes(), before)

    def test_create_cli_lock_error_is_stable_without_traceback(self):
        self.fx.stage()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", self.fx.create_argv()), \
                mock.patch.object(MOD.fcntl, "flock",
                                  side_effect=OSError(errno.EIO, "injected lock error")), \
                contextlib.redirect_stderr(stderr):
            code = MOD.main()
        self.assertEqual(code, 2)
        self.assertIn("E_COMMITMENT_LOCK", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_verify_cli_lock_error_is_stable_without_traceback(self):
        self.valid_manifest()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", self.fx.verify_argv()), \
                mock.patch.object(MOD.fcntl, "flock",
                                  side_effect=OSError(errno.EIO, "injected lock error")), \
                contextlib.redirect_stderr(stderr):
            code = MOD.main()
        self.assertEqual(code, 2)
        self.assertIn("E_COMMITMENT_LOCK", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_crash_after_commitment_before_manifest_is_retryable(self):
        self.fx.stage()
        with mock.patch.object(MOD.os, "link",
                               side_effect=OSError(errno.EIO, "injected publish failure")):
            with self.assertRaisesRegex(MOD.ManifestError, "E_MANIFEST_PUBLISH"):
                self.fx.create()
        self.assertFalse((self.fx.trace / "run-manifest.json").exists())
        self.assertEqual(len(self.fx.commitment.read_text().splitlines()), 1)
        self.fx.create()
        self.fx.verify()
        self.assertEqual(len(self.fx.commitment.read_text().splitlines()), 1)

    def test_exact_manifest_without_commitment_is_repaired(self):
        original = self.valid_manifest()
        self.fx.commitment.write_bytes(b"")
        repaired = self.fx.create()
        self.assertEqual(repaired, original)
        self.fx.verify()
        self.assertEqual(len(self.fx.commitment.read_text().splitlines()), 1)

    def test_conflicting_orphan_commitment_fails_closed(self):
        self.valid_manifest()
        (self.fx.trace / "run-manifest.json").unlink()
        self.fx.prompt.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_COMMITMENT_CONFLICT"):
            self.fx.create()
        self.assertFalse((self.fx.trace / "run-manifest.json").exists())
        self.assertEqual(len(self.fx.commitment.read_text().splitlines()), 1)

    def test_conflicting_manifest_without_commitment_fails_closed(self):
        self.valid_manifest()
        self.fx.commitment.write_bytes(b"")
        (self.fx.trace / "run-manifest.json").write_bytes(b"winner\n")
        with self.assertRaisesRegex(MOD.ManifestError, "E_MANIFEST_CONFLICT"):
            self.fx.create()
        self.assertEqual(self.fx.commitment.read_bytes(), b"")
        self.assertEqual((self.fx.trace / "run-manifest.json").read_bytes(), b"winner\n")

    def test_atomic_manifest_race_never_overwrites_winner(self):
        trace = self.fx.root / "atomic-trace"; trace.mkdir()
        original = MOD.os.link
        def race(src, dst, **kwargs):
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                         dir_fd=kwargs["dst_dir_fd"])
            os.write(fd, b"winner\n"); os.close(fd)
            return original(src, dst, **kwargs)
        with mock.patch.object(MOD.os, "link", side_effect=race):
            with self.assertRaisesRegex(MOD.ManifestError, "E_MANIFEST_EXISTS"):
                MOD._atomic_manifest(str(trace), {"schema": 1})
        self.assertEqual((trace / "run-manifest.json").read_bytes(), b"winner\n")

    def test_secret_shaped_input_is_rejected_without_echo(self):
        self.fx.stage()
        secret = "api_key=do-not-print"
        with self.assertRaises(MOD.ManifestError) as caught:
            self.fx.create(requested_model=secret)
        self.assertEqual(str(caught.exception), "E_SECRET_INPUT: rejected secret-shaped input")
        self.assertNotIn(secret, str(caught.exception))

    def test_cli_json_is_metadata_only(self):
        stage = subprocess.run([sys.executable, str(TOOL), "stage",
            "--source-corpus", str(self.fx.corpus), "--answer-key", str(self.fx.key),
            "--dataset", "fixture", "--destination", str(self.fx.staged), "--json"],
            text=True, capture_output=True)
        self.assertEqual(stage.returncode, 0, stage.stderr)
        row = json.loads(stage.stdout)
        self.assertEqual(set(row), {"corpus_manifest_sha256", "excluded_count", "included_count",
                                    "staged_manifest_sha256"})


if __name__ == "__main__":
    unittest.main()
