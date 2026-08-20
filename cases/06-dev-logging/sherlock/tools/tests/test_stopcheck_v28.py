#!/usr/bin/env python3
"""Provider-free regression coverage for the v28 Stop worklist feedback."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
VERSION = os.environ.get("SHERLOCK_STOPCHECK_VERSION", "v28")
SKILL = SHERLOCK / "skills" / VERSION
STOPCHECK = SKILL / "tools" / "stopcheck.py"


class Workspace:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root / "corpus"
        self.work = self.root / "work"
        self.marker = self.root / ".sherlock" / "active.json"

    def __enter__(self):
        (self.corpus / "host").mkdir(parents=True)
        (self.corpus / "host" / "app.log").write_text("event\n", encoding="utf-8")
        self.work.mkdir()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.tmp.cleanup()

    def activate(self, worklists, hosts=None):
        self.marker.parent.mkdir()
        data = {
            "version": int(VERSION[1:]),
            "active": True,
            "workspace": str(self.root.resolve()),
            "skill_root": str(SKILL.resolve()),
            "corpus": str(self.corpus.resolve()),
            "out": str(self.work.resolve()),
            "mode": "multi" if hosts else "single",
            "worklists": worklists,
        }
        if hosts:
            data["hosts_manifest"] = "hosts.tsv"
            data["hosts"] = hosts
        self.marker.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def stop(self):
        env = os.environ.copy()
        env["QWEN_SKILL_ROOT"] = str(SKILL)
        payload = {"cwd": str(self.root), "hook_event_name": "Stop"}
        run = subprocess.run([sys.executable, str(STOPCHECK)], input=json.dumps(payload),
                             text=True, capture_output=True, cwd=str(self.root), env=env)
        self.assert_hook_json(run)
        return json.loads(run.stdout)

    @staticmethod
    def assert_hook_json(run):
        assert run.returncode == 0, run.stderr
        assert run.stderr == "", run.stderr
        json.loads(run.stdout)


def worklist(ids):
    return "# id\tverdict\taxis\tref\tfrequency\trecord\n" + "".join(
        "%s\t?\trare\thost/app.log:1\tn=1\tevent\n" % row_id for row_id in ids)


def multi_host_fixture(workspace):
    hosts = [
        {"name": "alpha", "worklist": "worklist-alpha.tsv", "map": "map-alpha.txt"},
        {"name": "beta", "worklist": "worklist-beta.tsv", "map": "map-beta.txt"},
    ]
    workspace.activate([host["worklist"] for host in hosts], hosts=hosts)
    (workspace.work / "worklist-alpha.tsv").write_text(
        worklist(["alpha-1", "alpha-2", "alpha-3", "alpha-4"]), encoding="utf-8")
    (workspace.work / "worklist-beta.tsv").write_text(
        worklist(["beta-1", "beta-2", "beta-3", "beta-4"]), encoding="utf-8")
    for host in hosts:
        (workspace.work / host["map"]).write_text("map\n", encoding="utf-8")
    (workspace.work / "hosts.tsv").write_text(
        "# host\tfiles\tlines\ttemp\toutside\tworklist\tmap\tfolded\n"
        "alpha\t1\t4\t0\t0\tworklist-alpha.tsv\tmap-alpha.txt\t0\n"
        "beta\t1\t4\t0\t0\tworklist-beta.tsv\tmap-beta.txt\t0\n",
        encoding="utf-8")


class StopcheckV28(unittest.TestCase):
    def test_single_worklist_reports_total_action_and_examples(self):
        ids = ["single-%02d" % number for number in range(1, 9)]
        with Workspace() as workspace:
            workspace.activate(["worklist.tsv"])
            (workspace.work / "worklist.tsv").write_text(worklist(ids), encoding="utf-8")
            result = workspace.stop()

        self.assertEqual("block", result["decision"])
        reason = result["reason"]
        self.assertIn("8 unresolved rows", reason)
        self.assertIn("examples only", reason)
        self.assertIn("ONE TRIAGE pass", reason)
        self.assertIn("ALL remaining rows", reason)
        self.assertIn("single-01", reason)
        self.assertIn("single-05", reason)
        self.assertNotIn("single-06", reason)

    def test_multi_host_worklists_report_aggregate_and_sample_both_hosts(self):
        with Workspace() as workspace:
            multi_host_fixture(workspace)
            result = workspace.stop()

        self.assertEqual("block", result["decision"])
        reason = result["reason"]
        self.assertIn("8 unresolved rows across all worklists", reason)
        self.assertNotIn("4 unresolved rows", reason)
        self.assertIn("alpha-1", reason)
        self.assertIn("beta-1", reason)

    def test_reason_cap_keeps_count_action_and_example_label(self):
        ids = [("long-%02d-" % number) + ("x" * 512) for number in range(1, 9)]
        with Workspace() as workspace:
            workspace.activate(["worklist.tsv"])
            (workspace.work / "worklist.tsv").write_text(worklist(ids), encoding="utf-8")
            result = workspace.stop()

        reason = result["reason"]
        self.assertLessEqual(len(reason), 220)
        for phrase in ("8 unresolved rows", "ONE TRIAGE pass", "ALL remaining rows",
                       "update every '?'", "examples only"):
            self.assertIn(phrase, reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
