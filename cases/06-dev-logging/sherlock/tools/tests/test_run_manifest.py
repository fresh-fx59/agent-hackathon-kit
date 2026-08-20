#!/usr/bin/env python3
"""Provider-free contract tests for sealed benchmark manifests."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
        self.health = self.root / "health.json"
        self.write_health()

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
                "controller_parent": str(self.parent),
                "staged_corpus_destination": str(self.staged), "forbid_paths": ()}
        args.update(updates)
        return MOD.create_manifest(**args)


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
        verified = MOD.verify_manifest(str(self.fx.trace))
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

    def test_checkers_must_be_owned_by_selected_skill_version(self):
        self.fx.stage()
        outside = self.fx._file("outside-checker.py", "check\n")
        with self.assertRaisesRegex(MOD.ManifestError, "E_CHECKER_NOT_VERSION_OWNED"):
            self.fx.create(triage_checker=str(outside))

    def test_manifest_collision_is_refused(self):
        self.fx.stage(); self.fx.create()
        with self.assertRaisesRegex(MOD.ManifestError, "E_MANIFEST_EXISTS"):
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
        with self.assertRaisesRegex(MOD.ManifestError, "E_CORPUS_PATH_INVALID"):
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
            MOD.verify_manifest(str(self.fx.trace))

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
                    MOD.verify_manifest(str(fx.trace))
        fx = Fixture(self.root_for("tamper-skill")); fx.stage(); fx.create()
        (fx.skill / "SKILL.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(MOD.ManifestError, "E_SKILL_DIGEST_MISMATCH"):
            MOD.verify_manifest(str(fx.trace))

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
