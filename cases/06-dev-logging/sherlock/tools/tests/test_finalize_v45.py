#!/usr/bin/env python3
"""Focused contract tests for the v45 non-destructive finalization helper."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
FINALIZE = ROOT / "skills" / "v45" / "tools" / "finalize.py"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FinalizeV45(unittest.TestCase):
    def load_helper(self):
        spec = importlib.util.spec_from_file_location("finalize_v45", FINALIZE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, root, blocking=None):
        package = root / "package"
        tools = package / "tools"
        tools.mkdir(parents=True)
        for gate in ("reportcheck", "citecheck", "statecheck", "triagecheck"):
            payload = {"blocking": (blocking or {}).get(gate, 0), "gate": gate}
            (tools / (gate + ".py")).write_text(
                "import json\n"
                "print(json.dumps(" + repr(payload) + "))\n",
                encoding="utf-8",
            )
        corpus = root / "corpus"
        corpus.mkdir()
        (corpus / "evidence.log").write_text("evidence\n", encoding="utf-8")
        work = root / "work"
        work.mkdir()
        (work / "report.md").write_text("draft\n", encoding="utf-8")
        (work / "worklist.tsv").write_text("row\t+\n", encoding="utf-8")
        (work / "rules.tsv").write_text("rule\n", encoding="utf-8")
        return package, corpus, work

    def canonical_fixture(self, root):
        corpus = root / "corpus"
        shutil.copytree(ROOT / "tools/tests/fixtures/target-contract-source", corpus)
        work = root / "work"; work.mkdir()
        shutil.copy2(ROOT / "tools/tests/fixtures/target-contract-reports/canonical.md", work / "report.md")
        (work / "worklist.tsv").write_text(
            "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
            "A1\tN фон: Security.jsonl:1 «external authentication from 203.0.113.7»\trare\tSecurity.jsonl:1\tn=1\texternal authentication from 203.0.113.7\n"
            "A2\tN фон: System.jsonl:1 «RemoteAdmin service installed»\trare\tSystem.jsonl:1\tn=1\tRemoteAdmin service installed\n"
            "A3\tN фон: Security.jsonl:3 «external authentication from 198.51.100.9»\trare\tSecurity.jsonl:3\tn=1\texternal authentication from 198.51.100.9\n",
            encoding="utf-8")
        (work / "rules.tsv").write_text("# id\tусловие\tвердикт\tутверждение\tоснование\n", encoding="utf-8")
        return ROOT / "skills/v45", corpus, work

    def test_clean_run_records_all_gate_outputs_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.fixture(Path(tmp))
            before = {p: sha256(p) for p in (work / "report.md", work / "worklist.tsv", work / "rules.tsv", corpus / "evidence.log")}
            result = self.load_helper().run(package, work, corpus)
            self.assertEqual(result, 0)
            attempts = list((work / "validation").iterdir())
            self.assertEqual(len(attempts), 1)
            attempt = attempts[0]
            meta = json.loads((attempt / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["schema"], 1)
            self.assertEqual(meta["verdict"], "clean")
            self.assertEqual(set(meta["gates"]), {"reportcheck", "citecheck", "statecheck", "triagecheck"})
            for gate, detail in meta["gates"].items():
                self.assertEqual(detail["exit_code"], 0, gate)
                self.assertTrue((attempt / detail["stdout_artifact"]).is_file(), gate)
                self.assertTrue((attempt / detail["stderr_artifact"]).is_file(), gate)
                self.assertEqual(detail["parsed_blocking"], 0, gate)
            self.assertEqual(before, {p: sha256(p) for p in before})

    def test_blocking_json_fails_but_keeps_raw_gate_output_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.fixture(Path(tmp), {"citecheck": 2})
            evidence_hash = sha256(corpus / "evidence.log")
            self.assertNotEqual(self.load_helper().run(package, work, corpus), 0)
            attempt = next((work / "validation").iterdir())
            meta = json.loads((attempt / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["verdict"], "blocking")
            self.assertEqual(meta["gates"]["citecheck"]["parsed_blocking"], 2)
            self.assertIn(b'"blocking": 2', (attempt / meta["gates"]["citecheck"]["stdout_artifact"]).read_bytes())
            self.assertEqual(evidence_hash, sha256(corpus / "evidence.log"))

    def test_non_json_or_crashed_gate_is_a_failure_with_complete_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.fixture(Path(tmp))
            (package / "tools" / "statecheck.py").write_text(
                "import sys\nprint('not json')\nprint('crash', file=sys.stderr)\nsys.exit(7)\n",
                encoding="utf-8",
            )
            self.assertNotEqual(self.load_helper().run(package, work, corpus), 0)
            attempt = next((work / "validation").iterdir())
            meta = json.loads((attempt / "metadata.json").read_text(encoding="utf-8"))
            state = meta["gates"]["statecheck"]
            self.assertEqual(state["exit_code"], 7)
            self.assertEqual(state["parse_error"], "invalid_json")
            self.assertEqual((attempt / state["stdout_artifact"]).read_bytes(), b"not json\n")
            self.assertEqual((attempt / state["stderr_artifact"]).read_bytes(), b"crash\n")
            self.assertEqual(set(meta["gates"]), {"reportcheck", "citecheck", "statecheck", "triagecheck"})

    def test_pretty_json_with_blocking_defects_is_parsed_like_run_bench(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.fixture(Path(tmp))
            (package / "tools" / "citecheck.py").write_text(
                "import json\nprint('diagnostic')\nprint(json.dumps({'blocking_defects': 0}, indent=1))\n",
                encoding="utf-8",
            )
            self.assertEqual(self.load_helper().run(package, work, corpus), 0)

    def test_stop_finalizer_blocks_recursion_and_reports_saved_attempt_on_failure(self):
        stop_path = ROOT / "skills" / "v45" / "tools" / "stopcheck.py"
        spec = importlib.util.spec_from_file_location("stopcheck_v45", stop_path)
        stop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stop)
        previous = os.environ.get(stop.FINALIZE_STOP_ENV)
        os.environ[stop.FINALIZE_STOP_ENV] = "1"
        try:
            result, reason = stop.run_finalize("/corpus", "/work", "/package", 9999999999)
        finally:
            if previous is None:
                os.environ.pop(stop.FINALIZE_STOP_ENV, None)
            else:
                os.environ[stop.FINALIZE_STOP_ENV] = previous
        self.assertIsNone(result)
        self.assertIn("recursive", reason)

    def test_actual_unchanged_gates_accept_canonical_contract_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.canonical_fixture(Path(tmp))
            self.assertEqual(self.load_helper().run(package, work, corpus), 0)
            meta = json.loads(next((work / "validation").iterdir()).joinpath("metadata.json").read_text())
            self.assertEqual(meta["verdict"], "clean")
            self.assertTrue(all(row["exit_code"] == 0 and row["parsed_blocking"] == 0
                                for row in meta["gates"].values()))

    def test_actual_gates_accept_manifest_selected_multi_host_ledgers_and_retain_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, corpus, work = self.canonical_fixture(root)
            rows = (work / "worklist.tsv").read_text(encoding="utf-8").splitlines(True)
            (work / "worklist.tsv").unlink()
            (work / "worklist-alpha.tsv").write_text("".join(rows[:2]), encoding="utf-8")
            (work / "worklist-beta.tsv").write_text("".join([rows[0]] + rows[2:]), encoding="utf-8")
            for name in ("map-alpha.txt", "map-beta.txt"):
                (work / name).write_text("map\n", encoding="utf-8")
            (work / "hosts.tsv").write_text(
                "alpha\t1\t1\t0\t0\tworklist-alpha.tsv\tmap-alpha.txt\t0\n"
                "beta\t1\t1\t0\t0\tworklist-beta.tsv\tmap-beta.txt\t0\n", encoding="utf-8")
            marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
            marker.write_text(json.dumps({"version": 36, "active": True, "workspace": str(root),
                "skill_root": str(package.resolve()), "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                "mode": "multi", "worklists": ["worklist-alpha.tsv", "worklist-beta.tsv"],
                "hosts_manifest": "hosts.tsv", "hosts": [{"name": "alpha", "worklist": "worklist-alpha.tsv", "map": "map-alpha.txt"},
                                                        {"name": "beta", "worklist": "worklist-beta.tsv", "map": "map-beta.txt"}]}), encoding="utf-8")
            self.assertEqual(self.load_helper().run(package, work, corpus), 0)
            attempt = next((work / "validation").iterdir())
            meta = json.loads((attempt / "metadata.json").read_text())
            self.assertEqual(meta["selected_worklists"], ["worklist-alpha.tsv", "worklist-beta.tsv"])
            self.assertTrue((attempt / meta["composed_ledger"]).is_file())

    def test_actual_gates_reject_broken_draft_and_missing_ledger_without_losing_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.canonical_fixture(Path(tmp))
            shutil.copy2(ROOT / "tools/tests/fixtures/target-contract-reports/broken.md", work / "report.md")
            (work / "worklist.tsv").unlink()
            self.assertNotEqual(self.load_helper().run(package, work, corpus), 0)
            attempt = next((work / "validation").iterdir())
            meta = json.loads((attempt / "metadata.json").read_text())
            self.assertEqual(meta["verdict"], "blocking")
            self.assertEqual(meta["gates"], {})
            self.assertEqual(meta["ledger_error"], "ValueError")

    def test_manifest_duplicate_or_outside_ledgers_are_refused_before_gates(self):
        for worklists in (["worklist.tsv", "worklist.tsv"], ["../outside.tsv"]):
            with self.subTest(worklists=worklists), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                package, corpus, work = self.fixture(root)
                marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
                marker.write_text(json.dumps({"version": 36, "active": True, "workspace": str(root),
                    "skill_root": str(package.resolve()), "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                    "mode": "single", "worklists": worklists}), encoding="utf-8")
                self.assertNotEqual(self.load_helper().run(package, work, corpus), 0)
                meta = json.loads(next((work / "validation").iterdir()).joinpath("metadata.json").read_text())
                self.assertEqual(meta["verdict"], "blocking")
                self.assertEqual(meta["gates"], {})

    def test_input_mutation_invalidates_an_otherwise_clean_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.fixture(Path(tmp))
            (package / "tools" / "statecheck.py").write_text(
                "from pathlib import Path\nimport json,sys\n"
                "Path(sys.argv[sys.argv.index('--report')+1]).write_text('changed')\n"
                "print(json.dumps({'blocking': 0}))\n", encoding="utf-8")
            self.assertNotEqual(self.load_helper().run(package, work, corpus), 0)
            meta = json.loads(next((work / "validation").iterdir()).joinpath("metadata.json").read_text())
            self.assertTrue(meta["inputs_changed_during_validation"])

    def test_deadline_leaves_incremental_receipt_for_every_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, corpus, work = self.fixture(Path(tmp))
            (package / "tools" / "reportcheck.py").write_text(
                "import time\ntime.sleep(1)\nprint('{\\\"blocking\\\":0}')\n", encoding="utf-8")
            self.assertNotEqual(self.load_helper().run(package, work, corpus, deadline_seconds=.01), 0)
            meta = json.loads(next((work / "validation").iterdir()).joinpath("metadata.json").read_text())
            self.assertEqual(set(meta["gates"]), set(self.load_helper().GATES))
            self.assertEqual(meta["gates"]["reportcheck"]["exit_code"], 124)
            for gate in self.load_helper().GATES:
                self.assertTrue((next((work / "validation").iterdir()) / (gate + ".stdout")).is_file())

    def test_actual_v45_stop_runs_finalizer_and_keeps_a_clean_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, corpus, work = self.canonical_fixture(root)
            marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
            marker.write_text(json.dumps({"version": 36, "active": True,
                "workspace": str(root), "skill_root": str(package.resolve()),
                "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                "mode": "single", "worklists": ["worklist.tsv"]}) + "\n", encoding="utf-8")
            event = {"cwd": str(root), "hook_event_name": "Stop",
                     "last_assistant_message": (work / "report.md").read_text(encoding="utf-8").strip()}
            result = subprocess.run([sys.executable, str(package / "tools/stopcheck.py")],
                                    input=json.dumps(event), text=True, capture_output=True,
                                    cwd=root, env={**os.environ, "QWEN_SKILL_ROOT": str(package)})
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(result.stdout)
            self.assertEqual(decision["decision"], "allow")
            attempts = list((work / "validation").iterdir())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(json.loads((attempts[0] / "metadata.json").read_text())["verdict"], "clean")

    def test_actual_v45_stop_allows_manifest_selected_multi_host_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, corpus, work = self.canonical_fixture(root)
            rows = (work / "worklist.tsv").read_text(encoding="utf-8").splitlines(True)
            (work / "worklist.tsv").unlink()
            (work / "worklist-alpha.tsv").write_text("".join(rows[:2]), encoding="utf-8")
            (work / "worklist-beta.tsv").write_text("".join([rows[0]] + rows[2:]), encoding="utf-8")
            for name in ("map-alpha.txt", "map-beta.txt"):
                (work / name).write_text("map\n", encoding="utf-8")
            (work / "hosts.tsv").write_text(
                "alpha\t1\t1\t0\t0\tworklist-alpha.tsv\tmap-alpha.txt\t0\n"
                "beta\t1\t1\t0\t0\tworklist-beta.tsv\tmap-beta.txt\t0\n", encoding="utf-8")
            marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
            marker.write_text(json.dumps({"version": 36, "active": True, "workspace": str(root),
                "skill_root": str(package.resolve()), "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                "mode": "multi", "worklists": ["worklist-alpha.tsv", "worklist-beta.tsv"], "hosts_manifest": "hosts.tsv",
                "hosts": [{"name": "alpha", "worklist": "worklist-alpha.tsv", "map": "map-alpha.txt"},
                          {"name": "beta", "worklist": "worklist-beta.tsv", "map": "map-beta.txt"}]}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(package / "tools/stopcheck.py")],
                input=json.dumps({"cwd": str(root), "hook_event_name": "Stop",
                                  "last_assistant_message": (work / "report.md").read_text(encoding="utf-8").strip()}),
                text=True, capture_output=True, cwd=root,
                env={**os.environ, "QWEN_SKILL_ROOT": str(package)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["decision"], "allow")
            attempt = next((work / "validation").iterdir())
            meta = json.loads((attempt / "metadata.json").read_text())
            self.assertEqual(meta["selected_worklists"], ["worklist-alpha.tsv", "worklist-beta.tsv"])
            self.assertTrue((attempt / "worklist.tsv").is_file())

    def test_actual_v45_stop_visibly_blocks_an_unfinished_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, corpus, work = self.canonical_fixture(root)
            (work / "report.md").write_text("draft\n", encoding="utf-8")
            marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
            marker.write_text(json.dumps({"version": 36, "active": True,
                "workspace": str(root), "skill_root": str(package.resolve()),
                "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                "mode": "single", "worklists": ["worklist.tsv"]}) + "\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(package / "tools/stopcheck.py")],
                input=json.dumps({"cwd": str(root), "hook_event_name": "Stop", "last_assistant_message": "draft"}),
                text=True, capture_output=True, cwd=root, env={**os.environ, "QWEN_SKILL_ROOT": str(package)})
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(result.stdout)
            self.assertEqual(decision["decision"], "block")
            self.assertIn("report", decision["reason"].lower())

    def test_actual_v45_stop_finalizer_blocks_unaccounted_state_and_keeps_attempt(self):
        """The legacy Stop gates pass; the new state census must still block delivery."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, corpus, work = self.canonical_fixture(root)
            # This distinct actor makes a second state group.  It is deliberately
            # absent from the otherwise valid report and worklist, so only the
            # v45 finalizer's statecheck can reject the completed-looking draft.
            with (corpus / "System.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"Event": {"System": {
                    "Provider": {"#attributes": {"Name": "Service Control Manager"}},
                    "EventID": 7045,
                    "TimeCreated": {"#attributes": {"SystemTime": "2026-08-30T10:05:00Z"}}},
                    "EventData": {"ServiceName": "SecondService", "ImagePath": "C:\\\\Tools\\\\second.exe",
                    "SubjectUserSid": "S-1-5-21-901", "Message": "second service installed"}}}) + "\n")
            marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
            marker.write_text(json.dumps({"version": 36, "active": True,
                "workspace": str(root), "skill_root": str(package.resolve()),
                "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                "mode": "single", "worklists": ["worklist.tsv"]}) + "\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(package / "tools/stopcheck.py")],
                input=json.dumps({"cwd": str(root), "hook_event_name": "Stop",
                                  "last_assistant_message": (work / "report.md").read_text(encoding="utf-8").strip()}),
                text=True, capture_output=True, cwd=root,
                env={**os.environ, "QWEN_SKILL_ROOT": str(package)})
            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(result.stdout)
            self.assertEqual(decision["decision"], "block")
            self.assertIn("validation", decision["reason"].lower())
            attempts = list((work / "validation").iterdir())
            self.assertEqual(len(attempts), 1)
            meta = json.loads((attempts[0] / "metadata.json").read_text())
            self.assertEqual(meta["verdict"], "blocking")
            self.assertGreater(meta["gates"]["statecheck"]["parsed_blocking"], 0)


if __name__ == "__main__":
    unittest.main()
