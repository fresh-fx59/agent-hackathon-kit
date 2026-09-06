#!/usr/bin/env python3
"""Provider-free tests for the dedicated subscription-review observation lane."""
import importlib.util
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = load("lifecycle_for_dedicated_monitor", ROOT / "eval/bench/lifecycle-supervisor.py")
MONITOR = load("dedicated_review_monitor", ROOT / "eval/bench/dedicated-review-monitor.py")


class DedicatedReviewMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run-root"
        self.trace = self.root / "runs" / "run-fixture"
        self.trace.mkdir(parents=True)
        self.workspace = self.trace / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.nonce, self.boot, self.capability = "n" * 32, "fixture-boot", b"x" * 32
        self.observer = LIFECYCLE.init_segment(self.trace, self.nonce, self.boot,
                                               capability=self.capability)
        controller = self.root / "controller" / "controller-fixture"
        controller.mkdir(parents=True)
        self.status = controller / "status.json"
        self.write_json(self.status, {"schema": 1, "phase": "QWEN_RUNNING", "reason": None})
        self.upstream = self.root / "runs" / "run-fixture.upstream.jsonl"
        self.upstream.write_text(json.dumps({"request_id": "request-1", "status": 200,
                                             "finish_reason": "tool_calls"}) + "\n")
        bodies = self.trace / ".upstream.bodies"
        bodies.mkdir(); (bodies / "request-1.response").write_bytes(b"complete response bytes")
        self.monitor = self.root.with_name(self.root.name + ".monitor")
        self.monitor.mkdir(); (self.monitor / "objects").mkdir()
        self.prompt = ROOT / "eval/bench/dedicated-review-prompt.md"
        self.stub = self.root / "review-stub.py"
        self.stub.write_text(
            "import json,sys\n"
            "raw=sys.stdin.buffer.read(); marker=b'\\nSNAPSHOT_SHA256='\n"
            "part=raw.split(marker,1)[1]; snapshot=json.loads(part.split(b'\\n',1)[1])\n"
            "decision={'schema':1,'snapshot_sha256':__import__('hashlib').sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'run_nonce':snapshot['run_nonce'],'decision':'continue','last_completed_request':snapshot['last_completed_request'],'last_completed_tool':snapshot['last_completed_tool'],'pending_operation':'reviewed pending work','reason':'fixture evidence coherent'}\n"
            "print(json.dumps({'is_error':False,'subtype':'success','terminal_reason':'completed','modelUsage':{'claude-sonnet-5':{'canonicalModel':'claude-sonnet-5','inputTokens':1},'claude-haiku-4-5-20251001':{'canonicalModel':'claude-haiku-4-5'}},'usage':{'input_tokens':1},'result':json.dumps(decision,separators=(',',':'))},separators=(',',':')))\n",
            encoding="utf-8")
        self.command = self.root / "review-command.json"
        self.write_json(self.command, {"argv": [sys.executable, str(self.stub), "--model", "sonnet"]})
        self.digest = "d" * 64
        LIFECYCLE.publish_launch(
            self.observer, self.trace, self.nonce, self.boot, action="fixture", run_tag="run-fixture",
            predecessor_nonce=None, package_version="v50", package_sha256=self.digest,
            controller_pid=os.getpid(), controller_start_ticks="fixture", controller_pgid=os.getpgrp(),
            workspace_dir=self.workspace, target_profile_sha256=self.digest, run_budget_sha256=self.digest,
            input_package_sha256=self.digest, settings_sha256=self.digest,
            lifecycle_helper_sha256=MONITOR.sha256((ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()),
            authorization_sha256=self.digest, manifest_sha256=self.digest)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_json(path, row):
        Path(path).write_bytes(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")

    def cycle(self, **kwargs):
        return MONITOR.run_cycle(self.root, self.observer, self.nonce, self.boot,
                                 ROOT / "eval/bench/lifecycle-supervisor.py", self.prompt,
                                 self.command, self.monitor, **kwargs)

    def test_valid_review_binds_exact_snapshot_to_observation(self):
        """A successful review is required before the monitor can publish."""
        review = self.cycle()
        observation = json.loads((self.observer / "current-observation.json").read_text())
        self.assertEqual(observation["sequence"], 0)
        self.assertEqual(observation["last_completed_request"], "request-1")
        self.assertEqual(review["observation_sha256"],
                         MONITOR.sha256(MONITOR.canonical(observation) + b"\n"))
        snapshot = json.loads((self.monitor / "objects" / review["snapshot_sha256"]).read_text())
        self.assertEqual(snapshot["last_completed_request"], "request-1")
        self.assertEqual(review["review_model"], "claude-sonnet-5")
        self.assertIn("claude-haiku-4-5-20251001", review["review_model_usage"])
        body = next(value for value in snapshot["dynamic"]
                    if value["path"].endswith("request-1.response"))
        self.assertEqual(__import__("base64").b64decode(body["data_base64"]), b"complete response bytes")

    def test_real_cli_envelope_rejects_wrong_primary_and_error_status(self):
        digest = "a" * 64
        base = {"is_error": False, "subtype": "success", "terminal_reason": "completed",
                "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}},
                "result": "{}"}
        with self.assertRaisesRegex(MONITOR.MonitorError, "primary model"):
            wrong = dict(base)
            wrong["modelUsage"] = {"claude-opus-5": {"canonicalModel": "claude-opus-5"}}
            MONITOR.parse_decision(json.dumps(wrong).encode(), digest, self.nonce, None, None,
                                   "claude-sonnet-5")
        with self.assertRaisesRegex(MONITOR.MonitorError, "unsuccessful"):
            failed = dict(base); failed["is_error"] = True
            MONITOR.parse_decision(json.dumps(failed).encode(), digest, self.nonce, None, None,
                                   "claude-sonnet-5")
        with self.assertRaisesRegex(MONITOR.MonitorError, "envelope JSON"):
            MONITOR.parse_decision(b"not-json", digest, self.nonce, None, None,
                                   "claude-sonnet-5")

    def test_pinned_command_allows_empty_disable_values_but_not_empty_executable(self):
        pinned = ROOT / "docs/run-reports/artifacts/2026-09-06-v50-launch-preparation-r2/review-command-sonnet-low.json"
        argv, _, _ = MONITOR.load_command(pinned)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        for invalid in ([], ["", "--model", "sonnet"], ["claude", None]):
            self.write_json(self.command, {"argv": invalid})
            with self.assertRaisesRegex(MONITOR.MonitorError, "review command argv"):
                MONITOR.load_command(self.command)

    def test_prepare_watch_does_not_create_fresh_run_root_and_locks_by_sibling(self):
        fresh = Path(self.temp.name) / "fresh-run"
        monitor = fresh.with_name(fresh.name + ".monitor")
        lock = MONITOR.prepare_monitor(fresh, monitor)
        self.assertFalse(fresh.exists())
        self.assertTrue(lock.exists())
        other = fresh.with_name(fresh.name + ".other.monitor")
        with self.assertRaisesRegex(MONITOR.MonitorError, "run-root sibling"):
            MONITOR.prepare_monitor(fresh, other)
        lock.unlink()

    def test_denied_pre_without_post_and_fresh_checkpoint_absence_are_reviewed(self):
        event = {"hook_event_name": "PreToolUse", "tool_call_id": "denied-1",
                 "tool_use_id": "denied-1", "tool_name": "run_shell_command",
                 "tool_input": {"command": "must-not-run"}}
        event_input = json.dumps(event["tool_input"], sort_keys=True, separators=(",", ":")).encode()
        event["input_base64"] = __import__("base64").b64encode(event_input).decode()
        event["input_sha256"] = MONITOR.sha256(event_input)
        (self.observer / "hook-events.jsonl").write_bytes(
            json.dumps(event, sort_keys=True).encode() + b"\n")
        pairs = {"schema": 1, "pairs": {"denied-1": {"tool_call_id": "denied-1",
                 "pre": {"sequence": 2, "output": {"continue": False}}, "post": None}}}
        self.write_json(self.observer / "pairs.json", pairs)
        review = self.cycle()
        snapshot = json.loads((self.monitor / "objects" / review["snapshot_sha256"]).read_text())
        paths = {entry["path"]: entry for entry in snapshot["dynamic"] + snapshot["state"]}
        hook = next(value for key, value in paths.items() if key.endswith("hook-events.jsonl"))
        self.assertIn(b"must-not-run", __import__("base64").b64decode(hook["data_base64"]))
        self.assertEqual(hook["decoded_events"][0]["tool_input"]["command"], "must-not-run")
        self.assertEqual(hook["decoded_events"][0]["decoded_input_json"]["command"], "must-not-run")
        pair = next(value for key, value in paths.items() if key.endswith("pairs.json"))
        self.assertFalse(json.loads(__import__("base64").b64decode(pair["data_base64"]))["pairs"]["denied-1"]["pre"]["output"]["continue"])
        self.assertFalse((self.workspace / "work" / "checkpoint.json").exists())

    def test_changed_prefix_and_terminal_state_do_not_publish(self):
        self.stub.write_text(
            "import hashlib,json,pathlib,sys\n"
            "raw=sys.stdin.buffer.read(); part=raw.split(b'\\nSNAPSHOT_SHA256=',1)[1]; snapshot=json.loads(part.split(b'\\n',1)[1])\n"
            "pathlib.Path(sys.argv[1]).write_text('replaced')\n"
            "d={'schema':1,'snapshot_sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'run_nonce':snapshot['run_nonce'],'decision':'continue','last_completed_request':snapshot['last_completed_request'],'last_completed_tool':snapshot['last_completed_tool'],'pending_operation':'pending','reason':'fixture'}\n"
            "print(json.dumps({'is_error':False,'subtype':'success','terminal_reason':'completed','modelUsage':{'claude-sonnet-5':{'canonicalModel':'claude-sonnet-5'}},'usage':{},'result':json.dumps(d,separators=(',',':'))},separators=(',',':')))\n", encoding="utf-8")
        self.write_json(self.command, {"argv": [sys.executable, str(self.stub), str(self.upstream), "--model", "sonnet"]})
        with self.assertRaisesRegex(MONITOR.MonitorError, "prefix"):
            self.cycle()
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertTrue((self.observer / "fault.json").exists())

    def test_append_and_mutable_checkpoint_change_queue_for_next_review(self):
        checkpoint = self.workspace / "work" / "checkpoint.json"
        checkpoint.parent.mkdir()
        self.stub.write_text(
            "import hashlib,json,pathlib,sys\n"
            "raw=sys.stdin.buffer.read(); part=raw.split(b'\\nSNAPSHOT_SHA256=',1)[1]; snapshot=json.loads(part.split(b'\\n',1)[1])\n"
            "pathlib.Path(sys.argv[1]).open('a').write(json.dumps({'request_id':'request-2','status':200})+'\\n'); pathlib.Path(sys.argv[2]).write_text('{\"stage\":\"triage\"}')\n"
            "d={'schema':1,'snapshot_sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'run_nonce':snapshot['run_nonce'],'decision':'continue','last_completed_request':snapshot['last_completed_request'],'last_completed_tool':snapshot['last_completed_tool'],'pending_operation':'pending','reason':'fixture'}\n"
            "print(json.dumps({'is_error':False,'subtype':'success','terminal_reason':'completed','modelUsage':{'claude-sonnet-5':{'canonicalModel':'claude-sonnet-5'}},'usage':{},'result':json.dumps(d,separators=(',',':'))},separators=(',',':')))\n", encoding="utf-8")
        self.write_json(self.command, {"argv": [sys.executable, str(self.stub), str(self.upstream), str(checkpoint), "--model", "sonnet"]})
        first = self.cycle()
        self.assertEqual(first["sequence"], 0)
        self.assertEqual(json.loads((self.observer / "current-observation.json").read_text())["last_completed_request"], "request-1")
        self.assertTrue(checkpoint.exists())
        # The model-created append was not acknowledged in the first review.
        self.stub.write_text(self.stub.read_text().replace(
            "pathlib.Path(sys.argv[1]).open('a').write(json.dumps({'request_id':'request-2','status':200})+'\\n'); pathlib.Path(sys.argv[2]).write_text('{\"stage\":\"triage\"}')\n", ""), encoding="utf-8")
        second = self.cycle()
        self.assertEqual(second["sequence"], 1)
        self.assertEqual(json.loads((self.observer / "current-observation.json").read_text())["last_completed_request"], "request-2")

    def test_bad_or_stop_reviewer_output_never_publishes(self):
        self.stub.write_text("import sys; sys.stdin.buffer.read(); sys.exit(7)\n", encoding="utf-8")
        with self.assertRaisesRegex(MONITOR.MonitorError, "exit 7"):
            self.cycle()
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertTrue((self.observer / "fault.json").exists())

    def test_capacity_and_wrong_completed_identifier_never_publish(self):
        (self.observer / "hook-events.jsonl").write_bytes(b"x" * 128)
        with self.assertRaisesRegex(MONITOR.MonitorError, "capacity"):
            self.cycle(dynamic_capacity=64)
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertTrue((self.observer / "fault.json").exists())

        self.tearDown(); self.setUp()
        self.stub.write_text(
            "import hashlib,json,sys\n"
            "raw=sys.stdin.buffer.read(); part=raw.split(b'\\nSNAPSHOT_SHA256=',1)[1]; s=json.loads(part.split(b'\\n',1)[1])\n"
            "d={'schema':1,'snapshot_sha256':hashlib.sha256(json.dumps(s,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'run_nonce':s['run_nonce'],'decision':'continue','last_completed_request':'wrong-id','last_completed_tool':s['last_completed_tool'],'pending_operation':'pending','reason':'fixture'}\n"
            "print(json.dumps({'is_error':False,'subtype':'success','terminal_reason':'completed','modelUsage':{'claude-sonnet-5':{'canonicalModel':'claude-sonnet-5'}},'usage':{},'result':json.dumps(d,separators=(',',':'))},separators=(',',':')))\n", encoding="utf-8")
        with self.assertRaisesRegex(MONITOR.MonitorError, "identifiers"):
            self.cycle()
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertTrue((self.observer / "fault.json").exists())

        self.tearDown(); self.setUp()
        self.stub.write_text(
            self.stub.read_text() if False else
            "import hashlib,json,sys\n"
            "raw=sys.stdin.buffer.read(); part=raw.split(b'\\nSNAPSHOT_SHA256=',1)[1]; s=json.loads(part.split(b'\\n',1)[1])\n"
            "d={'schema':1,'snapshot_sha256':hashlib.sha256(json.dumps(s,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'run_nonce':s['run_nonce'],'decision':'stop','last_completed_request':s['last_completed_request'],'last_completed_tool':s['last_completed_tool'],'pending_operation':'fault','reason':'fixture'}\n"
            "print(json.dumps({'is_error':False,'subtype':'success','terminal_reason':'completed','modelUsage':{'claude-sonnet-5':{'canonicalModel':'claude-sonnet-5'}},'usage':{},'result':json.dumps(d,separators=(',',':'))},separators=(',',':')))\n", encoding="utf-8")
        with self.assertRaisesRegex(MONITOR.MonitorError, "stop decision"):
            self.cycle()
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertTrue((self.observer / "fault.json").exists())

        self.tearDown(); self.setUp()
        self.write_json(self.status, {"schema": 1, "phase": "BLOCKED", "reason": "fixture"})
        with self.assertRaisesRegex(MONITOR.MonitorError, "controller"):
            self.cycle()
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertTrue((self.observer / "fault.json").exists())

    def test_terminal_audit_binds_every_observation_and_receipt(self):
        checkpoint = self.workspace / "work" / "checkpoint.json"
        checkpoint.parent.mkdir()
        checkpoint.write_text('{"stage":"initial"}\n', encoding="utf-8")
        self.cycle()
        replacement = checkpoint.with_name(".checkpoint.replacement")
        replacement.write_text('{"stage":"advanced"}\n', encoding="utf-8")
        os.replace(replacement, checkpoint)
        receipt = LIFECYCLE.finalize_segment(
            self.observer, self.trace, self.nonce, self.boot, run_tag="run-fixture",
            launch_sha256=LIFECYCLE.sha256((self.trace / "lifecycle-launch.json").read_bytes()),
            lifecycle_helper_sha256=MONITOR.sha256((ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()),
            guardian_pid=1, guardian_start_ticks="fixture", guardian_exit_code=0)
        result = MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")
        self.assertEqual(result["original_lifecycle_receipt_sha256"],
                         LIFECYCLE.sha256((self.trace / "lifecycle-receipt.json").read_bytes()))
        self.assertEqual(result["accepted_observation_sha256"], [
                         json.loads((self.monitor / "reviews.jsonl").read_text())["observation_sha256"]])
        terminal = json.loads((self.monitor / "objects" / result["terminal_unreviewed_snapshot_sha256"]).read_text())
        self.assertTrue(terminal["terminal_unreviewed"])

        snapshot_path = self.monitor / "objects" / json.loads(
            (self.monitor / "reviews.jsonl").read_text())["snapshot_sha256"]
        snapshot_path.chmod(0o600)
        snapshot_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(MONITOR.MonitorError, "digest"):
            MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")

        (self.monitor / "reviews.jsonl").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(MONITOR.MonitorError):
            MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")

    def test_audit_binds_every_review_input_process_object_and_helper(self):
        fields = ("snapshot_sha256", "review_stdout_sha256", "review_stderr_sha256",
                  "review_prompt_sha256", "review_command_sha256", "launch_sha256")
        for field in fields:
            for mode in ("tamper", "missing"):
                self.tearDown(); self.setUp()
                self.cycle()
                review = json.loads((self.monitor / "reviews.jsonl").read_text())
                target = self.monitor / "objects" / review[field]
                if mode == "tamper":
                    target.chmod(0o600)
                    target.write_bytes(b"changed")
                else:
                    target.unlink()
                LIFECYCLE.finalize_segment(
                    self.observer, self.trace, self.nonce, self.boot, run_tag="run-fixture",
                    launch_sha256=LIFECYCLE.sha256((self.trace / "lifecycle-launch.json").read_bytes()),
                    lifecycle_helper_sha256=MONITOR.sha256((ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()),
                    guardian_pid=1, guardian_start_ticks="fixture", guardian_exit_code=0)
                with self.assertRaisesRegex(MONITOR.MonitorError,
                                            "object digest" if mode == "tamper" else "object binding"):
                    MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")

        self.tearDown(); self.setUp()
        self.cycle()
        review = json.loads((self.monitor / "reviews.jsonl").read_text())
        review["lifecycle_helper_sha256"] = "0" * 64
        (self.monitor / "reviews.jsonl").write_bytes(MONITOR.canonical(
            LIFECYCLE._sign_record(self.observer, review)) + b"\n")
        LIFECYCLE.finalize_segment(
            self.observer, self.trace, self.nonce, self.boot, run_tag="run-fixture",
            launch_sha256=LIFECYCLE.sha256((self.trace / "lifecycle-launch.json").read_bytes()),
            lifecycle_helper_sha256=MONITOR.sha256((ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()),
            guardian_pid=1, guardian_start_ticks="fixture", guardian_exit_code=0)
        with self.assertRaisesRegex(MONITOR.MonitorError, "helper binding"):
            MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")

    def test_terminal_boundary_during_review_leaves_audit_ready_delta(self):
        self.stub.write_text(
            "import pathlib,sys\n"
            "sys.stdin.buffer.read()\n"
            "pathlib.Path(sys.argv[1]).write_text('{\\\"schema\\\":1,\\\"phase\\\":\\\"SUCCEEDED\\\",\\\"reason\\\":null}\\n')\n"
            "print('terminal-before-review-decision')\n", encoding="utf-8")
        self.write_json(self.command, {"argv": [sys.executable, str(self.stub), str(self.status), "--model", "sonnet"]})
        self.assertIsNone(self.cycle())
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertFalse((self.observer / "fault.json").exists())
        self.assertEqual((self.monitor / "reviews.jsonl").read_bytes(), b"")
        terminal_review = json.loads((self.monitor / "terminal-unpublished-reviews.jsonl").read_text())
        self.assertTrue(terminal_review["terminal_unpublished"])
        self.assertEqual((self.monitor / "objects" / terminal_review["review_stdout_sha256"]).read_text(),
                         "terminal-before-review-decision\n")
        self.assertTrue(any((self.monitor / "objects").iterdir()))

        LIFECYCLE.finalize_segment(
            self.observer, self.trace, self.nonce, self.boot, run_tag="run-fixture",
            launch_sha256=LIFECYCLE.sha256((self.trace / "lifecycle-launch.json").read_bytes()),
            lifecycle_helper_sha256=MONITOR.sha256((ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()),
            guardian_pid=1, guardian_start_ticks="fixture", guardian_exit_code=0)
        audit = MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")
        self.assertEqual(audit["terminal_unpublished_review_sha256"], [
                         MONITOR.sha256(MONITOR.canonical(terminal_review) + b"\n")])
        terminal = json.loads((self.monitor / "objects" / audit["terminal_unreviewed_snapshot_sha256"]).read_text())
        self.assertTrue(terminal["terminal_unreviewed"])
        command_object = self.monitor / "objects" / terminal_review["review_command_sha256"]
        command_object.chmod(0o600)
        command_object.write_bytes(b"changed")
        with self.assertRaisesRegex(MONITOR.MonitorError, "terminal review object digest"):
            MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")

    def test_receipt_created_at_publication_is_terminal_unpublished(self):
        original_publish, original_load = LIFECYCLE.publish_observation, MONITOR.load_helper

        def close_before_publish(*args, **kwargs):
            (self.trace / "lifecycle-receipt.json").write_text("{}\n")
            return original_publish(*args, **kwargs)

        LIFECYCLE.publish_observation = close_before_publish
        MONITOR.load_helper = lambda _: (LIFECYCLE, MONITOR.sha256(
            (ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()))
        try:
            self.assertIsNone(self.cycle())
        finally:
            LIFECYCLE.publish_observation, MONITOR.load_helper = original_publish, original_load
        self.assertFalse((self.observer / "current-observation.json").exists())
        self.assertFalse((self.observer / "fault.json").exists())
        self.assertTrue((self.monitor / "terminal-unpublished-reviews.jsonl").exists())
        (self.trace / "lifecycle-receipt.json").unlink()
        LIFECYCLE.finalize_segment(
            self.observer, self.trace, self.nonce, self.boot, run_tag="run-fixture",
            launch_sha256=LIFECYCLE.sha256((self.trace / "lifecycle-launch.json").read_bytes()),
            lifecycle_helper_sha256=MONITOR.sha256((ROOT / "eval/bench/lifecycle-supervisor.py").read_bytes()),
            guardian_pid=1, guardian_start_ticks="fixture", guardian_exit_code=0)
        audit = MONITOR.audit(self.root, self.monitor, ROOT / "eval/bench/lifecycle-supervisor.py")
        self.assertEqual(len(audit["terminal_unpublished_review_sha256"]), 1)

    def test_latest_tool_advances_only_completed_post(self):
        pairs = {"pairs": {
            "done": {"tool_call_id": "done", "pre": {"sequence": 1, "output": {"continue": True}},
                      "post": {"sequence": 2, "result": "ok"}},
            "pending": {"tool_call_id": "pending", "pre": {"sequence": 3, "output": {"continue": True}},
                         "post": None},
            "denied": {"tool_call_id": "denied", "pre": {"sequence": 4, "output": {"continue": False}},
                        "post": None}}}
        self.assertEqual(MONITOR.latest_tool(pairs, None), "denied")

    def test_gzip_provider_body_exposes_one_readable_json_representation(self):
        body = self.trace / ".upstream.bodies" / "provider.req.json.gz"
        with gzip.open(body, "wb") as handle:
            handle.write(b'{"model":"gpt-5.5","marker":"provider-request"}')
        entry, _, _ = MONITOR.source_entry(self.root, body, None, 4096)
        self.assertEqual(entry["decoded_json"]["marker"], "provider-request")
        self.assertNotIn("decoded_text", entry)

    def test_gzip_evidence_is_bound_once_and_not_replayed_after_cursor(self):
        body = self.trace / ".upstream.bodies" / "provider.req.json.gz"
        payload = {"model": "gpt-5.5", "marker": "provider-request", "body": "x" * 100000}
        with gzip.open(body, "wb") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        initial = {"schema": MONITOR.SCHEMA, "sources": {},
                   "last_completed_request": None, "last_completed_tool": None}
        first, _, _, cursor = MONITOR.collect_snapshot(
            self.root, self.observer, self.monitor, initial, capacity=2 * 1024 * 1024)
        first_entry = next(entry for entry in first["dynamic"] if entry["path"].endswith("provider.req.json.gz"))
        self.assertNotIn("data_base64", first_entry)
        self.assertEqual(first_entry["decoded_json"]["marker"], "provider-request")
        self.assertNotIn("decoded_text", first_entry)
        raw_object = self.monitor / "objects" / first_entry["raw_object_sha256"]
        self.assertEqual(raw_object.read_bytes(), body.read_bytes())

        second, second_raw, _, _ = MONITOR.collect_snapshot(
            self.root, self.observer, self.monitor, cursor, capacity=2 * 1024 * 1024)
        second_entry = next(entry for entry in second["dynamic"] if entry["path"].endswith("provider.req.json.gz"))
        self.assertEqual(second_entry["raw_object_sha256"], first_entry["raw_object_sha256"])
        self.assertNotIn("decoded_json", second_entry)
        self.assertNotIn("decoded_text", second_entry)
        self.assertLess(len(second_raw), len(json.dumps(payload).encode("utf-8")))
        self.assertIsNone(MONITOR.verify_snapshot_object_bindings(self.monitor, first))
        raw_object.chmod(0o600)
        raw_object.write_bytes(b"changed")
        with self.assertRaisesRegex(MONITOR.MonitorError, "digest"):
            MONITOR.verify_snapshot_object_bindings(self.monitor, first)


if __name__ == "__main__":
    unittest.main()
