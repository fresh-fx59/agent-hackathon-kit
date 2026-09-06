#!/usr/bin/env python3
"""v50 chooses the ledgers advertised by logmap, never filename patterns."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SHERLOCK = Path(__file__).resolve().parents[2]
PACKAGE = SHERLOCK / "skills" / "v50"


def system_events():
    rows = []
    for index in range(12):
        rows.append({"Event": {"System": {
            "Provider": {"#attributes": {"Name": "Service Control Manager"}},
            "EventID": {"#attributes": {"Qualifiers": 16384}, "#text": 7045},
            "Level": 4 if index < 6 else 6,
            "TimeCreated": {"#attributes": {"SystemTime": "2026-09-04T00:00:%02dZ" % index}},
            "Security": {"#attributes": {"UserID": "S-1-5-18"}}},
            "EventData": {"ServiceName": "SherlockQualificationHealthy",
                          "ImagePath": "C:\\Windows\\System32\\svchost.exe -k qualification"}}})
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                   for row in rows)


class WorklistSelectionV50Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.corpus = self.workspace / "corpus"
        self.work = self.workspace / "nested" / "work"
        self.corpus.mkdir()
        self.work.parent.mkdir()
        (self.corpus / "System.jsonl").write_text(system_events(), encoding="utf-8")
        env = {**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)}
        result = subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "logmap.py"), str(self.corpus),
             "--out", str(self.work), "--single-host"],
            cwd=self.workspace, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "worklist-index.tsv").is_file())
        worklist = self.work / "worklist.tsv"
        rewritten = []
        for line in worklist.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                columns = line.split("\t")
                columns[1] = "N fixture verified"
                line = "\t".join(columns)
            rewritten.append(line)
        worklist.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def checkpoint(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "checkpoint.py"), *args,
             "--work", str(self.work)], text=True, capture_output=True, cwd=cwd)

    def gate(self):
        event = {"cwd": str(self.workspace), "hook_event_name": "PreToolUse",
                 "session_id": "before-clear", "tool_name": "write_file",
                 "tool_input": {"path": "nested/work/sentinel.txt"}}
        return subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "boundarycheck.py")],
            input=json.dumps(event), text=True, capture_output=True, cwd=self.workspace,
            env={**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)})

    def stop(self, strict=False):
        event = {"cwd": str(self.workspace), "hook_event_name": "Stop",
                 "session_id": "before-clear", "last_assistant_message": "handoff"}
        env = {**os.environ, "QWEN_SKILL_ROOT": str(PACKAGE)}
        if strict:
            env["SHERLOCK_STRICT_MARKER_LIFECYCLE"] = "1"
        return subprocess.run([sys.executable, str(PACKAGE / "tools" / "stopcheck.py")],
                              input=json.dumps(event), text=True, capture_output=True,
                              cwd=self.workspace, env=env)

    def test_outer_unrelated_marker_does_not_override_workspace_authority(self):
        outer_work = self.root / "other-work"
        outer_corpus = self.root / "other-corpus"
        outer_work.mkdir()
        outer_corpus.mkdir()
        (outer_work / "worklist.tsv").write_text("# id\tverdict\nother\tN\n",
                                                  encoding="utf-8")
        marker = json.loads((self.workspace / ".sherlock" / "active.json").read_text(
            encoding="utf-8"))
        marker.update({"workspace": str(self.root), "out": str(outer_work),
                       "corpus": str(outer_corpus)})
        outer_marker = self.root / ".sherlock" / "active.json"
        outer_marker.parent.mkdir()
        outer_marker.write_text(json.dumps(marker) + "\n", encoding="utf-8")

        caller = self.root / "outside-caller"
        caller.mkdir()
        result = self.checkpoint("init", cwd=caller)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads((self.work / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(row["worklist_authority"]["workspace"], str(self.workspace))
        self.assertEqual(self.checkpoint("handoff", "--done", "triage", cwd=caller).returncode,
                         0)
        result = self.stop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "allow")

    def test_logmap_index_is_not_a_checkpoint_ledger(self):
        self.assertEqual(self.checkpoint("init").returncode, 0)
        self.assertEqual(self.checkpoint("handoff", "--done", "triage").returncode, 0)
        row = json.loads((self.work / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(list(row["worklists"]), ["worklist.tsv"])
        self.assertEqual(row["total"], 2)
        self.assertEqual(row["pending_handoff"]["worklist_seals"], row["worklists"])
        with (self.work / "worklist-index.tsv").open("a", encoding="utf-8") as handle:
            handle.write("view-extra.tsv\tedge\t1\n")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("handoff pending", reason)

    def test_ledger_change_after_handoff_still_fails_the_seal_check(self):
        self.assertEqual(self.checkpoint("init").returncode, 0)
        self.assertEqual(self.checkpoint("handoff", "--done", "triage").returncode, 0)
        with (self.work / "worklist.tsv").open("a", encoding="utf-8") as handle:
            handle.write("tampered\tN\n")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("worklist seals changed", reason)

    def test_triage_refresh_force_create_records_selected_authority(self):
        result = subprocess.run(
            [sys.executable, str(PACKAGE / "tools" / "triagecheck.py"),
             "--worklist", str(self.work / "worklist.tsv"), "--rules",
             str(self.work / "rules.tsv"), "--corpus", str(self.corpus), "--json",
             "--refresh-checkpoint"], text=True, capture_output=True)
        self.assertTrue((self.work / "checkpoint.json").is_file(), result.stderr)
        row = json.loads((self.work / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(row["worklist_authority"]["source"], "active-marker")
        self.assertEqual(row["worklists"], {"worklist.tsv": row["worklists"]["worklist.tsv"]})

    def test_marker_replacement_after_handoff_rejects_bound_authority(self):
        self.assertEqual(self.checkpoint("init").returncode, 0)
        self.assertEqual(self.checkpoint("handoff", "--done", "triage").returncode, 0)
        replacement_corpus = self.workspace / "replacement-corpus"
        replacement_corpus.mkdir()
        marker_path = self.workspace / ".sherlock" / "active.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["corpus"] = str(replacement_corpus)
        marker_path.write_text(json.dumps(marker) + "\n", encoding="utf-8")
        result = self.stop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_marker_timestamp_change_preserves_handoff_authority(self):
        self.assertEqual(self.checkpoint("init").returncode, 0)
        self.assertEqual(self.checkpoint("handoff", "--done", "triage").returncode, 0)
        marker_path = self.workspace / ".sherlock" / "active.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["updated_at"] = "2026-09-06T15:40:12+00:00"
        marker_path.write_text(json.dumps(marker) + "\n", encoding="utf-8")
        result = self.stop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "allow")

    def test_controlled_stop_blocks_after_marker_removal(self):
        self.assertEqual(self.checkpoint("init").returncode, 0)
        self.assertEqual(self.checkpoint("handoff", "--done", "triage").returncode, 0)
        (self.workspace / ".sherlock" / "active.json").unlink()
        result = self.stop(strict=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_standalone_hosts_manifest_excludes_derived_worklist_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve() / "nested" / "work"
            work.mkdir(parents=True)
            for name, row_id in (("worklist-alpha.tsv", "a1"),
                                 ("worklist-beta.tsv", "b1")):
                (work / name).write_text("# id\tverdict\n%s\tN closed\n" % row_id,
                                         encoding="utf-8")
            (work / "map-alpha.txt").write_text("map\n", encoding="utf-8")
            (work / "map-beta.txt").write_text("map\n", encoding="utf-8")
            (work / "worklist-index.tsv").write_text(
                "# derived view index\nview-a.tsv\tedge\t99\n", encoding="utf-8")
            (work / "hosts.tsv").write_text(
                "# host\tfiles\trows\ttime\tskipped\tworklist\tmap\tfolded\n"
                "alpha\t1\t1\t0\t0\tworklist-alpha.tsv\tmap-alpha.txt\t0\n"
                "beta\t1\t1\t0\t0\tworklist-beta.tsv\tmap-beta.txt\t0\n",
                encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(PACKAGE / "tools" / "checkpoint.py"), "init",
                 "--work", str(work)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(row["worklists"]),
                             ["worklist-alpha.tsv", "worklist-beta.tsv"])
            self.assertEqual((row["total"], row["resolved"]), (2, 2))

    def test_outside_caller_cannot_ignore_malformed_ancestor_marker(self):
        marker = self.workspace / ".sherlock" / "active.json"
        marker.write_text("{}\n", encoding="utf-8")
        result = self.checkpoint("init")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker authority", result.stdout)

    def test_malformed_advertised_marker_never_falls_back_to_worklist_name(self):
        marker = self.workspace / ".sherlock" / "active.json"
        marker.write_text("{}\n", encoding="utf-8")
        result = self.checkpoint("init", cwd=self.workspace)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker authority", result.stdout)


if __name__ == "__main__":
    unittest.main()
