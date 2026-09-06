#!/usr/bin/env python3
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "sherlock_lifecycle_supervisor", HERE / "lifecycle-supervisor.py")
LIFECYCLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIFECYCLE)


class LifecycleSupervisorTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="sherlock-lifecycle-test-"))
        self.trace = self.temp / "trace"
        self.trace.mkdir()
        self.workspace = self.temp / "workspace"
        self.workspace.mkdir()
        self.nonce = "a" * 32
        self.boot = "test-boot-id"
        self.capability = b"c" * 32
        self.observer = LIFECYCLE.init_segment(
            self.trace, self.nonce, self.boot, capability=self.capability)

    def tearDown(self):
        shutil.rmtree(self.temp)

    def observe(self, sequence=0, monotonic_ns=10_000_000_000, **changes):
        values = {
            "last_completed_request": None,
            "last_completed_tool": None,
            "pending_operation": "reviewing current capture and gate state",
        }
        values.update(changes)
        return LIFECYCLE.publish_observation(
            self.observer, self.nonce, self.boot, sequence=sequence,
            monotonic_ns=monotonic_ns, wall_time="2026-09-05T12:00:00Z",
            capability=self.capability, **values)

    def hook(self, phase, tool_id="tool-1", session_id="session-1", **extra):
        row = {"hook_event_name": phase, "session_id": session_id,
               "tool_use_id": tool_id, "tool_call_id": tool_id,
               "tool_name": "run_shell_command",
               "tool_input": {"command": "true"}}
        row.update(extra)
        raw = json.dumps(row, sort_keys=True).encode()
        return LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot, raw)

    def post_tool_batch(self, calls):
        row = {
            "cwd": str(self.workspace),
            "hook_event_name": "PostToolBatch",
            "permission_mode": "default",
            "session_id": "session-1",
            "timestamp": "2026-09-06T00:00:00.000Z",
            "tool_calls": calls,
            "transcript_path": str(self.workspace / "transcript.jsonl"),
        }
        raw = json.dumps(row, sort_keys=True).encode()
        return LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot, raw)

    def lifecycle_hook(self, phase, **extra):
        row = {
            "cwd": str(self.workspace),
            "hook_event_name": phase,
            "permission_mode": "default",
            "session_id": "session-1",
            "timestamp": "2026-09-06T00:00:00.000Z",
            "transcript_path": str(self.workspace / "transcript.jsonl"),
        }
        row.update(extra)
        raw = json.dumps(row, sort_keys=True).encode()
        return LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot, raw)

    @staticmethod
    def invalid_directory_call(tool_id="tool-1", **changes):
        row = {
            "tool_name": "run_shell_command",
            "tool_input": {"command": "true", "directory": "/outside-workspace"},
            "tool_use_id": tool_id,
            "tool_call_id": tool_id,
            "status": "error",
            "tool_response": {
                "error": "Directory is not within registered workspace directories",
                "error_type": "invalid_tool_params",
                "execution_status": "not_started",
            },
        }
        row.update(changes)
        return row

    @staticmethod
    def successful_call(tool_id="tool-1"):
        return {
            "tool_name": "run_shell_command", "tool_input": {"command": "true"},
            "tool_use_id": tool_id, "tool_call_id": tool_id, "status": "success",
            "tool_response": {"execution_status": "completed"},
        }

    def write_active(self, worklists):
        work = self.workspace / "work"
        work.mkdir(exist_ok=True)
        marker_dir = self.workspace / ".sherlock"
        marker_dir.mkdir(exist_ok=True)
        marker = {"version": 36, "active": True,
                  "workspace": str(self.workspace), "out": str(work),
                  "mode": "single" if worklists == ["worklist.tsv"] else "multi",
                  "worklists": worklists}
        if marker["mode"] == "multi":
            marker["hosts_manifest"] = "hosts.tsv"
            (work / "hosts.tsv").write_text(
                "h\t-\t-\t-\t-\t%s\tmap.md\n" % worklists[0])
        (marker_dir / "active.json").write_text(json.dumps(marker) + "\n")
        return work

    def test_observation_is_fresh_without_aggregate_lifetime_limit(self):
        self.observe()
        first = LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot, now_monotonic_ns=69_000_000_000)
        self.observe(sequence=1, monotonic_ns=9_000_000_000_000)
        later = LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=9_059_000_000_000)
        self.assertEqual(first["sequence"], 0)
        self.assertEqual(later["sequence"], 1)

    def test_guardian_supervision_persists_sequence_against_regression(self):
        now = 10_000_000_000
        first = self.observe(monotonic_ns=now)
        self.assertEqual(LIFECYCLE.check_supervision(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=now + 1)["sequence"], 0)
        self.observe(sequence=1, monotonic_ns=now + 2)
        self.assertEqual(LIFECYCLE.check_supervision(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=now + 3)["sequence"], 1)
        (self.observer / "current-observation.json").write_text(
            json.dumps(first, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault, "REGRESSED"):
            LIFECYCLE.check_supervision(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=now + 4)

    def test_missing_stale_wrong_future_and_regressed_observations_fault(self):
        cases = ("missing", "stale", "wrong-nonce", "future", "regressed")
        for case in cases:
            with self.subTest(case=case):
                shutil.rmtree(self.observer)
                self.observer = LIFECYCLE.init_segment(
                    self.trace, self.nonce, self.boot, capability=self.capability)
                now = 100_000_000_000
                if case != "missing":
                    self.observe(monotonic_ns=now)
                current = self.observer / "current-observation.json"
                if case == "stale":
                    now += 60_000_000_001
                elif case == "wrong-nonce":
                    row = json.loads(current.read_text())
                    row["run_nonce"] = "b" * 32
                    current.write_text(json.dumps(row) + "\n")
                elif case == "future":
                    now -= 1
                elif case == "regressed":
                    first = current.read_bytes()
                    self.observe(sequence=1, monotonic_ns=now + 1)
                    LIFECYCLE.check_dispatch(
                        self.observer, self.nonce, self.boot,
                        now_monotonic_ns=now + 2)
                    current.write_bytes(first)
                with self.assertRaises(LIFECYCLE.LifecycleFault):
                    LIFECYCLE.check_dispatch(
                        self.observer, self.nonce, self.boot,
                        now_monotonic_ns=now + (2 if case == "regressed" else 0))
                fault = json.loads((self.observer / "fault.json").read_text())
                self.assertEqual(fault["schema"], 1)
                self.assertEqual(fault["run_nonce"], self.nonce)

    def test_malformed_observation_becomes_a_permanent_fault(self):
        (self.observer / "current-observation.json").write_text("{not-json\n")
        with self.assertRaises(LIFECYCLE.LifecycleFault):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_000)
        self.assertTrue((self.observer / "fault.json").exists())

    def test_fault_is_permanent_even_after_fresh_observation(self):
        with self.assertRaises(LIFECYCLE.LifecycleFault):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_000)
        self.observe(monotonic_ns=10_000_000_000)
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault, "terminal"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_001)

    def test_exact_client_prevalidation_rejection_discharge_is_separate(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
        result = self.post_tool_batch([self.invalid_directory_call()])
        self.assertTrue(result["continue"])
        self.assertEqual(
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_001)["sequence"], 0)
        rejected = json.loads((self.observer / "rejections.json").read_text())
        self.assertEqual(set(rejected["rejected"]), {"tool-1"})
        batch = json.loads(
            (self.observer / "post-tool-batch-events.jsonl").read_text().splitlines()[0])
        self.assertEqual(batch["input_sha256"], LIFECYCLE.sha256(base64.b64decode(
            batch["input_base64"])))

    def test_batch_success_or_started_error_cannot_waive_missing_hooks(self):
        for changes in (
                {"status": "success", "tool_response": {"execution_status": "completed"}},
                {"tool_response": {"error_type": "invalid_tool_params",
                                   "execution_status": "started"}},
                {"tool_response": {"error_type": "execution_error",
                                   "execution_status": "not_started"}}):
            with self.subTest(changes=changes):
                shutil.rmtree(self.observer)
                self.observer = LIFECYCLE.init_segment(
                    self.trace, self.nonce, self.boot, capability=self.capability)
                self.observe()
                LIFECYCLE.register_expected_tools(
                    self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
                result = self.post_tool_batch([
                    self.invalid_directory_call(**changes)])
                self.assertFalse(result["continue"])
                fault = json.loads((self.observer / "fault.json").read_text())
                self.assertEqual(fault["reason"], "EXPECTED_TOOL_HOOK_MISSING")

    def test_batch_rejection_cannot_overlap_pair_or_use_unknown_duplicate_id(self):
        cases = ("overlap", "unknown", "duplicate", "replayed")
        for case in cases:
            with self.subTest(case=case):
                shutil.rmtree(self.observer)
                self.observer = LIFECYCLE.init_segment(
                    self.trace, self.nonce, self.boot, capability=self.capability)
                self.observe()
                LIFECYCLE.register_expected_tools(
                    self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
                if case == "overlap":
                    self.hook("PreToolUse")
                    self.hook("PostToolUse", tool_response={"ok": True})
                    calls = [self.invalid_directory_call()]
                elif case == "unknown":
                    calls = [self.invalid_directory_call("unknown")]
                elif case == "duplicate":
                    calls = [self.invalid_directory_call(), self.invalid_directory_call()]
                else:
                    calls = [self.invalid_directory_call()]
                result = self.post_tool_batch(calls)
                if case == "replayed" and result["continue"]:
                    result = self.post_tool_batch([self.invalid_directory_call()])
                self.assertFalse(result["continue"])
                self.assertTrue((self.observer / "fault.json").exists())

    def test_completed_pair_still_requires_one_batch_receipt(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
        self.hook("PreToolUse")
        self.hook("PostToolUse", tool_response={"ok": True})
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                    "EXPECTED_TOOL_BATCH_MISSING"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_001)

    def test_completed_pair_cannot_replay_batch_receipt(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
        self.hook("PreToolUse")
        self.hook("PostToolUse", tool_response={"ok": True})
        self.assertTrue(self.post_tool_batch([self.successful_call()])["continue"])
        replay = self.post_tool_batch([self.successful_call()])
        self.assertFalse(replay["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "CLIENT_BATCH_DUPLICATE")

    def test_pre_snapshot_preserves_deleted_registered_bytes_and_post_faults(self):
        work = self.write_active(["worklist.tsv"])
        original = b"id\t?\trare\tSecurity.jsonl:1\t1\trecord\n"
        (work / "worklist.tsv").write_bytes(original)
        (work / "worklist.manifest.json").write_text(
            json.dumps({"schema": 1, "ids": ["id"], "rows": 1,
                        "sha256": "0" * 64}) + "\n")
        before = self.hook("PreToolUse")
        digest = LIFECYCLE.sha256(original)
        self.assertTrue(before["continue"])
        self.assertEqual((self.observer / "objects" / digest).read_bytes(), original)

        (work / "worklist.tsv").unlink()
        after = self.hook("PostToolUse", tool_response={"ok": True})
        self.assertFalse(after["continue"])
        self.assertEqual(after["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertTrue((self.observer / "fault.json").exists())
        with self.assertRaises(LIFECYCLE.LifecycleFault):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_000)

    def test_same_count_rename_cannot_replace_a_registered_path(self):
        work = self.write_active(["worklist.tsv", "worklist-host.tsv"])
        (work / "worklist.tsv").write_text("one\n")
        (work / "worklist-host.tsv").write_text("two\n")
        self.hook("PreToolUse")
        (work / "worklist-host.tsv").rename(work / "replacement.tsv")
        result = self.hook("PostToolUse", tool_response={"ok": True})
        self.assertFalse(result["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertIn("worklist-host.tsv", fault["detail"])

    def test_content_revision_registers_a_new_object_without_deletion_fault(self):
        work = self.write_active(["worklist.tsv"])
        ledger = work / "worklist.tsv"
        ledger.write_bytes(b"before\n")
        self.hook("PreToolUse")
        ledger.write_bytes(b"after\n")
        result = self.hook("PostToolUse", tool_response={"ok": True})
        self.assertTrue(result["continue"])
        registry = json.loads((self.observer / "registry.json").read_text())
        versions = registry["files"]["work/worklist.tsv"]["versions"]
        self.assertEqual(versions, [LIFECYCLE.sha256(b"before\n"),
                                    LIFECYCLE.sha256(b"after\n")])

    def test_duplicate_hook_is_idempotent_only_for_identical_input(self):
        work = self.write_active(["worklist.tsv"])
        (work / "worklist.tsv").write_text("row\n")
        first = self.hook("PreToolUse")
        duplicate = self.hook("PreToolUse")
        self.assertEqual(first, duplicate)
        conflict = self.hook("PreToolUse", tool_input={"command": "false"})
        self.assertFalse(conflict["continue"])
        self.assertTrue((self.observer / "fault.json").exists())

    def test_unmatched_pre_pair_blocks_dispatch(self):
        self.observe()
        self.hook("PreToolUse")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault, "hook pair"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_001)

    def test_provider_expected_tool_requires_matching_completed_hook_pair(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
        result = self.post_tool_batch([self.successful_call()])
        self.assertFalse(result["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "EXPECTED_TOOL_HOOK_MISSING")

    def test_completed_hook_pair_satisfies_provider_expectation(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["tool-1"])
        self.hook("PreToolUse")
        self.hook("PostToolUse", tool_response={"ok": True})
        self.assertTrue(self.post_tool_batch([self.successful_call()])["continue"])
        row = LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_001)
        self.assertEqual(row["sequence"], 0)

    def test_provider_tool_call_id_maps_to_qwen_generated_tool_use_id(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-provider-1"])
        self.hook("PreToolUse", tool_id="toolu-qwen-1",
                  tool_call_id="call-provider-1")
        self.hook("PostToolUse", tool_id="toolu-qwen-1",
                  tool_call_id="call-provider-1", tool_response={"ok": True})
        self.assertTrue(self.post_tool_batch(
            [self.successful_call("call-provider-1")])["continue"])
        row = LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_001)
        self.assertEqual(row["sequence"], 0)
        pairs = json.loads((self.observer / "pairs.json").read_text())
        pair = pairs["pairs"]["session-1\x1ftoolu-qwen-1"]
        self.assertEqual(pair["tool_call_id"], "call-provider-1")

    def test_foreground_child_dispatch_and_continuation_are_exactly_scoped(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent",
            ["call-parent-agent", "call-parent-sibling"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect the evidence"})
        self.assertTrue(self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")["continue"])

        row = LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_001,
            request_sha256=LIFECYCLE.sha256(b"child request 1"))
        self.assertEqual(row["sequence"], 0)
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-child-1", ["call-child"])
        self.hook("PreToolUse", tool_id="toolu-child", tool_call_id="call-child")
        self.hook("PostToolUse", tool_id="toolu-child", tool_call_id="call-child",
                  tool_response={"ok": True})
        self.assertTrue(self.post_tool_batch(
            [self.successful_call("call-child")])["continue"])

        LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_002,
            request_sha256=LIFECYCLE.sha256(b"child request 2"))
        self.assertTrue(self.lifecycle_hook(
            "SubagentStop", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")["continue"])
        self.hook("PostToolUse", tool_id="toolu-parent",
                  tool_call_id="call-parent-agent", tool_response={"ok": True})
        self.assertTrue(self.post_tool_batch(
            [self.successful_call("call-parent-agent")])["continue"])
        self.hook("PreToolUse", tool_id="toolu-sibling",
                  tool_call_id="call-parent-sibling")
        self.hook("PostToolUse", tool_id="toolu-sibling",
                  tool_call_id="call-parent-sibling", tool_response={"ok": True})
        self.assertTrue(self.post_tool_batch(
            [self.successful_call("call-parent-sibling")])["continue"])
        self.assertTrue(self.lifecycle_hook("UserPromptSubmit", prompt="continue")["continue"])

    def test_capped_child_stop_revokes_only_batch_renewed_permit(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent", ["call-parent-agent"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect"})
        self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")
        LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_001,
            request_sha256=LIFECYCLE.sha256(b"child request"))
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-child", ["call-child"])
        self.hook("PreToolUse", tool_id="toolu-child", tool_call_id="call-child")
        self.hook("PostToolUse", tool_id="toolu-child", tool_call_id="call-child",
                  tool_response={"ok": True})
        self.post_tool_batch([self.successful_call("call-child")])
        self.assertTrue(self.lifecycle_hook(
            "SubagentStop", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")["continue"])
        dispatches = [json.loads(line) for line in
                      (self.observer / "nested-dispatches.jsonl").read_text().splitlines()]
        self.assertEqual([row["action"] for row in dispatches], ["consume", "revoke"])

    def test_parent_post_before_matching_child_stop_faults(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent", ["call-parent-agent"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect"})
        self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")
        result = self.hook("PostToolUse", tool_id="toolu-parent",
                           tool_call_id="call-parent-agent", tool_response={"ok": True})
        self.assertFalse(result["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "SUBAGENT_STOP_MISSING")

    def test_child_dispatch_does_not_waive_unrelated_pending_tool(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-old", ["call-old"])
        self.hook("PreToolUse", tool_id="toolu-old", tool_call_id="call-old")
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent", ["call-parent-agent"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect"})
        result = self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")
        self.assertFalse(result["continue"])
        self.assertEqual(json.loads((self.observer / "fault.json").read_text())["reason"],
                         "SUBAGENT_START_UNAUTHORIZED")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault, "terminal"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_001,
                request_sha256=LIFECYCLE.sha256(b"child request"))

    def test_root_boundary_refuses_active_child_and_missing_inner_batch(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent", ["call-parent-agent"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect"})
        self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")
        result = self.lifecycle_hook("UserPromptSubmit", prompt="root continuation")
        self.assertFalse(result["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "SUBAGENT_STOP_MISSING")

    def test_stop_refuses_unused_initial_permit_and_nested_start(self):
        for case in ("unused", "nested"):
            with self.subTest(case=case):
                shutil.rmtree(self.observer)
                self.observer = LIFECYCLE.init_segment(
                    self.trace, self.nonce, self.boot, capability=self.capability)
                self.observe()
                LIFECYCLE.register_expected_tools(
                    self.observer, self.nonce, self.boot, "request-parent",
                    ["call-parent-agent"])
                self.hook(
                    "PreToolUse", tool_id="toolu-parent",
                    tool_call_id="call-parent-agent", tool_name="agent",
                    tool_input={"subagent_type": "sherlock-triage",
                                "run_in_background": False, "prompt": "inspect"})
                self.lifecycle_hook(
                    "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
                    agent_type="sherlock-triage")
                if case == "unused":
                    result = self.lifecycle_hook(
                        "SubagentStop", agent_id="sherlock-triage-call-parent-agent",
                        agent_type="sherlock-triage")
                    expected = "SUBAGENT_DISPATCH_MISSING"
                else:
                    result = self.lifecycle_hook(
                        "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
                        agent_type="sherlock-triage")
                    expected = "NESTED_SUBAGENT_UNSUPPORTED"
                self.assertFalse(result["continue"])
                self.assertEqual(json.loads(
                    (self.observer / "fault.json").read_text())["reason"], expected)

    def test_child_continuation_requires_inner_batch_and_fresh_permit(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent", ["call-parent-agent"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect"})
        self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")
        LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_001,
            request_sha256=LIFECYCLE.sha256(b"child request"))
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-child", ["call-child"])
        self.hook("PreToolUse", tool_id="toolu-child", tool_call_id="call-child")
        self.hook("PostToolUse", tool_id="toolu-child", tool_call_id="call-child",
                  tool_response={"ok": True})
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                    "EXPECTED_TOOL_BATCH_MISSING"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_002,
                request_sha256=LIFECYCLE.sha256(b"child continuation"))

    def test_batch_renewal_cannot_replay_a_consumed_child_request(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-parent", ["call-parent-agent"])
        self.hook(
            "PreToolUse", tool_id="toolu-parent", tool_call_id="call-parent-agent",
            tool_name="agent", tool_input={
                "subagent_type": "sherlock-triage", "run_in_background": False,
                "prompt": "inspect"})
        self.lifecycle_hook(
            "SubagentStart", agent_id="sherlock-triage-call-parent-agent",
            agent_type="sherlock-triage")
        digest = LIFECYCLE.sha256(b"child request")
        LIFECYCLE.check_dispatch(
            self.observer, self.nonce, self.boot,
            now_monotonic_ns=10_000_000_001, request_sha256=digest)
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-child", ["call-child"])
        self.hook("PreToolUse", tool_id="toolu-child", tool_call_id="call-child")
        self.hook("PostToolUse", tool_id="toolu-child", tool_call_id="call-child",
                  tool_response={"ok": True})
        self.post_tool_batch([self.successful_call("call-child")])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                    "NESTED_DISPATCH_REPLAY"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_002, request_sha256=digest)

    def test_subagent_start_requires_exact_foreground_parent_identity(self):
        for changes in ({"agent_id": "wrong"}, {"agent_type": "other"}):
            with self.subTest(changes=changes):
                shutil.rmtree(self.observer)
                self.observer = LIFECYCLE.init_segment(
                    self.trace, self.nonce, self.boot, capability=self.capability)
                self.observe()
                LIFECYCLE.register_expected_tools(
                    self.observer, self.nonce, self.boot, "request-parent",
                    ["call-parent-agent"])
                self.hook(
                    "PreToolUse", tool_id="toolu-parent",
                    tool_call_id="call-parent-agent", tool_name="agent",
                    tool_input={"subagent_type": "sherlock-triage",
                                "run_in_background": False, "prompt": "inspect"})
                event = {"agent_id": "sherlock-triage-call-parent-agent",
                         "agent_type": "sherlock-triage"}
                event.update(changes)
                result = self.lifecycle_hook("SubagentStart", **event)
                self.assertFalse(result["continue"])
                self.assertEqual(json.loads(
                    (self.observer / "fault.json").read_text())["reason"],
                    "SUBAGENT_START_UNAUTHORIZED")

    def test_pre_and_post_reject_different_provider_tool_call_ids(self):
        self.hook("PreToolUse", tool_id="toolu-qwen-1",
                  tool_call_id="call-provider-1")
        result = self.hook("PostToolUse", tool_id="toolu-qwen-1",
                           tool_call_id="call-provider-2",
                           tool_response={"ok": True})
        self.assertFalse(result["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "HOOK_CALL_ID_MISMATCH")

    def test_hook_pair_identity_rejects_key_delimiter_in_input_ids(self):
        result = self.hook("PreToolUse", session_id="session\x1fother")
        self.assertFalse(result["continue"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "INVALID_HOOK_INPUT")

    def test_two_hook_pairs_cannot_consume_one_provider_tool_call(self):
        self.observe()
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-provider-1"])
        for tool_use_id in ("toolu-qwen-1", "toolu-qwen-2"):
            self.hook("PreToolUse", tool_id=tool_use_id,
                      tool_call_id="call-provider-1")
            self.hook("PostToolUse", tool_id=tool_use_id,
                      tool_call_id="call-provider-1", tool_response={"ok": True})
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                    "HOOK_CALL_ID_REUSED"):
            LIFECYCLE.check_dispatch(
                self.observer, self.nonce, self.boot,
                now_monotonic_ns=10_000_000_001)

    def test_provider_tool_call_id_cannot_cross_request_boundaries(self):
        LIFECYCLE.register_expected_tools(
            self.observer, self.nonce, self.boot, "request-1", ["call-provider-1"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                    "TOOL_EXPECTATION_REUSED"):
            LIFECYCLE.register_expected_tools(
                self.observer, self.nonce, self.boot, "request-2",
                ["call-provider-1"])
        fault = json.loads((self.observer / "fault.json").read_text())
        self.assertEqual(fault["reason"], "TOOL_EXPECTATION_REUSED")

    def test_duplicate_provider_tool_call_id_in_one_response_is_rejected(self):
        with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                    "INVALID_TOOL_EXPECTATION"):
            LIFECYCLE.register_expected_tools(
                self.observer, self.nonce, self.boot, "request-1",
                ["call-provider-1", "call-provider-1"])

    def test_hook_receipt_retains_exact_input_and_output(self):
        work = self.write_active(["worklist.tsv"])
        (work / "worklist.tsv").write_text("row\n")
        source = {"hook_event_name": "PreToolUse", "session_id": "s",
                  "tool_use_id": "t", "tool_call_id": "t",
                  "tool_name": "write_file",
                  "tool_input": {"file_path": "work/report.md", "content": "x"}}
        raw = json.dumps(source, ensure_ascii=False, indent=2).encode()
        output = LIFECYCLE.handle_hook(
            self.observer, self.workspace, self.nonce, self.boot, raw)
        receipt = json.loads((self.observer / "hook-events.jsonl").read_text().splitlines()[-1])
        self.assertEqual(base64.b64decode(receipt["input_base64"]), raw)
        self.assertEqual(receipt["output"], output)

    def test_changing_file_is_rejected_without_silent_retry(self):
        path = self.workspace / "race.tsv"
        path.write_bytes(b"before\n")
        original_read = LIFECYCLE.os.read
        changed = []

        def mutate_after_read(fd, length):
            data = original_read(fd, length)
            if data and not changed:
                changed.append(True)
                path.write_bytes(b"changed-and-longer\n")
            return data

        with mock.patch.object(LIFECYCLE.os, "read", side_effect=mutate_after_read):
            with self.assertRaisesRegex(LIFECYCLE.LifecycleFault,
                                        "FILE_CHANGED_WHILE_READ"):
                LIFECYCLE._read_regular(path)

    def test_symlinked_required_file_faults_without_snapshot(self):
        work = self.write_active(["worklist.tsv"])
        source = self.temp / "outside.tsv"
        source.write_text("outside\n")
        (work / "worklist.tsv").symlink_to(source)
        output = self.hook("PreToolUse")
        self.assertFalse(output["continue"])
        self.assertEqual(list((self.observer / "objects").iterdir()), [])

    def test_hook_cli_returns_success_with_explicit_denial_json(self):
        command = [sys.executable, str(HERE / "lifecycle-supervisor.py"), "hook",
                   "--observer-dir", str(self.observer),
                   "--workspace", str(self.workspace), "--nonce", self.nonce,
                   "--boot-id", self.boot]
        done = subprocess.run(command, input="{}", text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        output = json.loads(done.stdout)
        self.assertFalse(output["continue"])
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_launch_and_terminal_receipt_are_capability_signed(self):
        self.observe(monotonic_ns=time.monotonic_ns())
        digest = "d" * 64
        launch_workspace = self.trace / "workspace"
        launch_workspace.mkdir(mode=0o700)
        launch = LIFECYCLE.publish_launch(
            self.observer, self.trace, self.nonce, self.boot,
            action="target_contract_probe_operator_monitored",
            run_tag="target-contract-probe", predecessor_nonce=None,
            package_version="v45", package_sha256=digest,
            controller_pid=os.getpid(), controller_start_ticks="fixture",
            controller_pgid=os.getpgrp(), workspace_dir=launch_workspace,
            target_profile_sha256=digest,
            run_budget_sha256=digest, input_package_sha256=digest,
            settings_sha256=digest, lifecycle_helper_sha256=digest,
            authorization_sha256=digest, manifest_sha256=digest)
        launch_path = self.trace / "lifecycle-launch.json"
        self.assertEqual(LIFECYCLE.verify_signed_record(
            self.observer, launch), launch)
        receipt = LIFECYCLE.finalize_segment(
            self.observer, self.trace, self.nonce, self.boot,
            run_tag="target-contract-probe",
            launch_sha256=LIFECYCLE.sha256(launch_path.read_bytes()),
            lifecycle_helper_sha256=digest, guardian_pid=123,
            guardian_start_ticks="guardian-fixture", guardian_exit_code=-15)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["expected_tool_count"], 0)
        self.assertEqual(LIFECYCLE.verify_signed_record(
            self.observer, receipt), receipt)

    def test_guardian_expires_owned_controller_and_leaves_unrelated_process(self):
        shutil.rmtree(self.observer)
        boot = LIFECYCLE.current_boot_id()
        self.observer = LIFECYCLE.init_segment(
            self.trace, self.nonce, boot, capability=self.capability)
        LIFECYCLE.publish_observation(
            self.observer, self.nonce, boot, sequence=0,
            monotonic_ns=time.monotonic_ns() - LIFECYCLE.MAX_OBSERVATION_AGE_NS - 1,
            capability=self.capability)
        controller = subprocess.Popen(["sleep", "30"], start_new_session=True)
        unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            rc = LIFECYCLE.run_guardian(
                self.observer, self.nonce, boot, controller.pid,
                LIFECYCLE.process_start_ticks(controller.pid),
                os.getpgid(controller.pid), interval_s=0.01)
            self.assertEqual(rc, 2)
            controller.wait(timeout=2)
            self.assertIsNone(unrelated.poll())
            event = json.loads(
                (self.observer / "guardian-events.jsonl").read_text().splitlines()[-1])
            self.assertTrue(event["ownership_verified"])
            self.assertTrue(event["signal_sent"])
        finally:
            for process in (controller, unrelated):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=2)

    def test_guardian_allows_provider_expectation_and_running_tool_until_dispatch(self):
        shutil.rmtree(self.observer)
        boot = LIFECYCLE.current_boot_id()
        self.boot = boot
        self.observer = LIFECYCLE.init_segment(
            self.trace, self.nonce, boot, capability=self.capability)
        self.observe(monotonic_ns=time.monotonic_ns())
        controller = subprocess.Popen(["sleep", "30"], start_new_session=True)
        guardian = None
        try:
            LIFECYCLE.register_expected_tools(
                self.observer, self.nonce, boot, "request-1", ["tool-1"])
            guardian = subprocess.Popen([
                sys.executable, str(HERE / "lifecycle-supervisor.py"), "guardian",
                "--observer-dir", str(self.observer), "--nonce", self.nonce,
                "--boot-id", boot, "--controller-pid", str(controller.pid),
                "--controller-start-ticks", LIFECYCLE.process_start_ticks(controller.pid),
                "--controller-pgid", str(os.getpgid(controller.pid)), "--interval", "0.01",
            ])
            time.sleep(0.05)
            self.assertIsNone(guardian.poll(), "expectation is pending client hook delivery")
            self.assertIsNone(controller.poll())
            self.assertFalse((self.observer / "fault.json").exists())

            self.hook("PreToolUse")
            time.sleep(0.05)
            self.assertIsNone(guardian.poll(), "tool may run between its pre and post hooks")
            self.assertIsNone(controller.poll())
            self.assertFalse((self.observer / "fault.json").exists())

            self.hook("PostToolUse", tool_response={"ok": True})
            self.assertTrue(self.post_tool_batch(
                [self.successful_call()])["continue"])
            LIFECYCLE.check_dispatch(self.observer, self.nonce, boot)
        finally:
            for process in (guardian, controller):
                if process is not None and process.poll() is None:
                    process.terminate()
                if process is not None:
                    process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
