#!/usr/bin/env python3
"""Wait for a Sherlock trace, verify its report, and explain any failure."""
import argparse
import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import importlib.util


HERE = Path(__file__).resolve().parent
STATUS_TOOL = HERE / "bench-status.py"
LIFECYCLE_TOOL = HERE / "lifecycle-supervisor.py"
TERMINAL = {"ACCEPTED", "REJECTED", "RUN_FAILED", "FINISHED", "FINISHED_UNCHECKED"}
REQUIRED_GATES = ("citecheck", "triagecheck", "statecheck", "reportcheck")
MAX_JSON = 1024 * 1024
MAX_LEDGER = 64 * 1024 * 1024
MAX_LEDGER_ROWS = 100000
PRIMARY_FAILURE_CODES = frozenset({
    # The verdict independently validates an authenticated projection. Keep the
    # same pinned set as the status verifier; do not execute mutable helpers.
    "ATTRIBUTION_UNAVAILABLE", "CACHE_TERMS_INCOMPLETE", "COMPACTION_OUTPUT_CLIPPED",
    "EXPECTED_IDENTITY_UNKNOWN", "GENERATION_WINDOW_EXCEEDED",
    "LANE_ABORT_UNREADABLE", "LANE_ACCOUNTING_INCOMPLETE", "LANE_AUDIT_FAILED",
    "LEDGER_EMPTY", "LEDGER_MALFORMED", "LEDGER_MISSING", "NO_PROGRESS",
    "CLEAR_NOT_EFFECTIVE", "TARGET_REFUSED", "STAGE_STALLED", "WRAPPER_NONZERO",
    "DRIVER_EXIT", "BUDGET_EXCEEDED", "OUTPUT_BUDGET_EXHAUSTED_BY_REASONING",
    "PER_REQUEST_TOKEN_GATE_BREACHED", "PER_REQUEST_TOKEN_GATE_UNMEASURED",
    "PROMPT_CACHE_COLLAPSE", "REASONING_CONTENT_NOT_RELAYED",
    "RETURNED_MODEL_FAMILY_MISMATCH", "RETURNED_MODEL_UNKNOWN",
    "ROUTE_ADVANCE_COUNTERS_INCONSISTENT", "ROUTE_ADVANCE_HISTORY_UNREADABLE",
    "ROUTE_ADVANCE_UNRECORDED", "ROUTE_IDENTITY_UNREADABLE", "USAGE_UNREADABLE",
    "RATE_SNAPSHOT_INVALID", "RATE_SNAPSHOT_CHANGED", "ACTION_BUDGET_INVALID",
    "MAX_PROVIDER_CALLS", "MAX_PROMPT_TOKENS", "MAX_COMPLETION_TOKENS",
    "MAX_WALL_TIME_S", "MAX_ESTIMATED_COST_RUB",
    "HARNESS_QUALIFICATION_MISSING", "TARGET_PROBE_NOT_AUTHORIZED",
    "TARGET_PROBE_BUDGET", "TARGET_CONTRACT_FAILED", "TARGET_IDENTITY_MISMATCH",
    "TARGET_IDENTITY_UNVERIFIABLE", "TARGET_RECEIPT_EXPIRED", "TARGET_RECEIPT_USED",
    "APPROVAL_REPLAYED", "FULL_RUN_NOT_AUTHORIZED", "INPUTS_INCOMPARABLE",
    "BILLING_UNKNOWN",
})


def status_projection(args):
    if not args.authenticated:
        trace = Path(args.trace).resolve()
        value = read_json(trace / "status.json")
        if (value.get("schema") != 1
                or value.get("run_tag") != trace.name
                or not isinstance(value.get("phase"), str)
                or Path(value.get("trace_dir", "")).resolve() != trace):
            raise ValueError("TRACE_UNRESOLVED")
        return value
    command = [sys.executable, str(STATUS_TOOL), args.trace]
    if args.target_probe:
        command.append("--target-probe")
    else:
        command.extend(["--commitment-file", args.commitment_file,
                        "--commitment-key", args.commitment_key])
    command.append("--json")
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("TRACE_UNRESOLVED") from exc
    if result.returncode != 0:
        raise ValueError("TRACE_UNRESOLVED")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("TRACE_UNRESOLVED")
    if args.target_probe and value.get("selection") != "target-probe":
        raise ValueError("TRACE_UNRESOLVED")
    return value


def running_verdict(status):
    return {
        "schema": 1,
        "run_tag": status.get("run_tag"),
        "state": "running",
        "phase": status.get("phase"),
        "finished": False,
        "successful": None,
        "report_correct": None,
        "report_correctness_scope": "pending",
        "authenticated": status.get("authenticated", False),
        "authority": status.get("authority"),
        "failures": [],
        "metrics": {},
        "improvements": [],
    }


def read_json(path):
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_size > MAX_JSON:
        raise ValueError("unsafe artifact")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid artifact")
    return value


def _lifecycle_module():
    """Load the pinned local verifier; terminal evidence is not trusted JSON."""
    spec = importlib.util.spec_from_file_location("sherlock_lifecycle_verifier",
                                                  LIFECYCLE_TOOL)
    if spec is None or spec.loader is None:
        raise ValueError("lifecycle verifier unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def monitored_lifecycle_failures(trace):
    """Return fail-closed terminal-audit findings for a monitored trace.

    A signed launch makes lifecycle evidence mandatory.  This deliberately
    verifies raw bytes and signatures rather than accepting the controller's
    status projection, which is mutable after launch.
    """
    trace = Path(trace).resolve()
    launch_path = trace / "lifecycle-launch.json"
    if not os.path.lexists(launch_path):
        # Schema 2 is the finite-proof replacement: it has no aggregate cap,
        # so lifecycle supervision is mandatory rather than an optional add-on.
        try:
            profile = read_json(trace / "target-profile.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return []
        if (profile.get("schema") == 2
                and profile.get("execution_mode") == "operator_monitored"):
            return ["LIFECYCLE_AUDIT_INVALID"]
        return []
    try:
        lifecycle = _lifecycle_module()
        launch_raw = lifecycle._read_regular(launch_path, MAX_JSON)
        launch = lifecycle._strict_json(launch_raw)
        if set(launch) != lifecycle.LAUNCH_FIELDS:
            raise ValueError("launch schema")
        observer = Path(launch["observer_dir"])
        if observer.parent.resolve() != trace or not observer.name.startswith("observer-"):
            raise ValueError("observer location")
        lifecycle.verify_signed_record(observer, launch)
        if (launch.get("schema") != 1 or launch.get("execution_mode") != "operator_monitored"
                or not isinstance(launch.get("run_nonce"), str)
                or not isinstance(launch.get("run_tag"), str)
                or not isinstance(launch.get("lifecycle_helper_sha256"), str)):
            raise ValueError("launch identity")
        receipt_raw = lifecycle._read_regular(trace / "lifecycle-receipt.json", MAX_JSON)
        receipt = lifecycle._strict_json(receipt_raw)
        if set(receipt) != lifecycle.RECEIPT_FIELDS:
            raise ValueError("receipt schema")
        lifecycle.verify_signed_record(observer, receipt)
        identity_raw = lifecycle._read_regular(observer / "identity.json", MAX_JSON)
        if (receipt.get("schema") != 1
                or receipt.get("run_nonce") != launch["run_nonce"]
                or receipt.get("run_tag") != launch["run_tag"]
                or receipt.get("launch_sha256") != lifecycle.sha256(launch_raw)
                or receipt.get("observer_dir") != launch["observer_dir"]
                or receipt.get("observer_identity_sha256") != lifecycle.sha256(identity_raw)
                or receipt.get("lifecycle_helper_sha256") != launch["lifecycle_helper_sha256"]
                or type(receipt.get("expected_tool_count")) is not int
                or type(receipt.get("batched_tool_count")) is not int
                or type(receipt.get("completed_tool_count")) is not int
                or type(receipt.get("rejected_tool_count")) is not int
                or receipt["expected_tool_count"] < 0
                or receipt["batched_tool_count"] < 0
                or receipt["completed_tool_count"] < 0
                or receipt["rejected_tool_count"] < 0):
            raise ValueError("receipt identity")
        # Receipt digests cover the observer state the controller had at its
        # terminal decision.  A mismatch is invalid evidence even if the HMAC
        # itself remains valid.
        for field, name in (("registry_sha256", "registry.json"),
                            ("pairs_sha256", "pairs.json"),
                            ("expectations_sha256", "expectations.json"),
                            ("batch_tools_sha256", "batch-tools.json"),
                            ("rejections_sha256", "rejections.json")):
            if receipt[field] != lifecycle.sha256(lifecycle._read_regular(observer / name, MAX_JSON)):
                raise ValueError("observer digest")
        for field, name in (("last_accepted_observation_sha256", "last-accepted-observation.json"),
                            ("hook_starts_sha256", "hook-starts.jsonl"),
                            ("hook_events_sha256", "hook-events.jsonl"),
                            ("post_tool_batch_events_sha256",
                             "post-tool-batch-events.jsonl"),
                            ("guardian_events_sha256", "guardian-events.jsonl"),
                            ("fault_sha256", "fault.json")):
            claimed = receipt.get(field)
            if claimed is None:
                if (observer / name).exists():
                    raise ValueError("missing optional observer digest")
            elif (not isinstance(claimed, str)
                  or claimed != lifecycle.sha256(lifecycle._read_regular(
                      observer / name,
                      MAX_LEDGER if name == "post-tool-batch-events.jsonl" else MAX_JSON))):
                raise ValueError("optional observer digest")
        expected = lifecycle._strict_json(lifecycle._read_regular(
            observer / "expectations.json", MAX_JSON))
        pairs = lifecycle._strict_json(lifecycle._read_regular(
            observer / "pairs.json", MAX_JSON))
        batch_tools = lifecycle._strict_json(lifecycle._read_regular(
            observer / "batch-tools.json", MAX_JSON))
        rejections = lifecycle._strict_json(lifecycle._read_regular(
            observer / "rejections.json", MAX_JSON))
        if (expected.get("schema") != lifecycle.SCHEMA
                or not isinstance(expected.get("expected"), dict)
                or pairs.get("schema") != lifecycle.SCHEMA
                or not isinstance(pairs.get("pairs"), dict)
                or batch_tools.get("schema") != lifecycle.SCHEMA
                or not isinstance(batch_tools.get("tools"), dict)
                or rejections.get("schema") != lifecycle.SCHEMA
                or not isinstance(rejections.get("rejected"), dict)):
            raise ValueError("tool state schema")
        expected_owners = {}
        for request_reference, request in expected["expected"].items():
            if (not isinstance(request_reference, str) or not request_reference
                    or not isinstance(request, dict)
                    or request.get("request_reference") != request_reference
                    or not isinstance(request.get("tool_use_ids"), list)):
                raise ValueError("expectation schema")
            seen = set()
            for tool_id in request["tool_use_ids"]:
                if (not isinstance(tool_id, str) or not tool_id
                        or len(tool_id) > 4096 or tool_id in seen
                        or tool_id in expected_owners):
                    raise ValueError("ambiguous expected tool id")
                seen.add(tool_id)
                expected_owners[tool_id] = request_reference
        expected_ids = set(expected_owners)
        completed_ids = set()
        completed_owners = {}
        incomplete = False
        for key, pair in pairs.get("pairs", {}).items():
            if (not isinstance(key, str) or key.count("\x1f") != 1
                    or any(not part for part in key.split("\x1f"))
                    or not isinstance(pair, dict)):
                raise ValueError("pair schema")
            tool_id = pair.get("tool_call_id")
            if (not isinstance(tool_id, str) or not tool_id
                    or len(tool_id) > 4096 or tool_id in completed_owners):
                raise ValueError("ambiguous completed tool id")
            completed_owners[tool_id] = key
            pre, post = pair.get("pre"), pair.get("post")
            if post is not None or (isinstance(pre, dict)
                                    and pre.get("output", {}).get("continue") is False):
                completed_ids.add(tool_id)
            elif pre is not None:
                incomplete = True
        rejected_ids = set()
        batch_ids = set()
        batch_fields = {"tool_call_id", "tool_name", "request_reference", "status",
                        "error_type", "execution_status", "input_sha256", "sequence"}
        for tool_id, row in batch_tools["tools"].items():
            if (not isinstance(tool_id, str) or not tool_id or len(tool_id) > 4096
                    or not isinstance(row, dict) or set(row) != batch_fields
                    or row.get("tool_call_id") != tool_id
                    or not isinstance(row.get("tool_name"), str) or not row["tool_name"]
                    or row.get("request_reference") != expected_owners.get(tool_id)
                    or not isinstance(row.get("status"), str) or not row["status"]
                    or (row.get("error_type") is not None
                        and not isinstance(row.get("error_type"), str))
                    or (row.get("execution_status") is not None
                        and not isinstance(row.get("execution_status"), str))
                    or not lifecycle._hex_digest(row.get("input_sha256"))
                    or type(row.get("sequence")) is not int or row["sequence"] < 0):
                raise ValueError("batch tool schema")
            batch_ids.add(tool_id)
        rejection_fields = {"tool_call_id", "tool_name", "request_reference", "status",
                            "error_type", "execution_status", "input_sha256", "sequence"}
        for tool_id, row in rejections["rejected"].items():
            if (not isinstance(tool_id, str) or not tool_id or len(tool_id) > 4096
                    or not isinstance(row, dict) or set(row) != rejection_fields
                    or row.get("tool_call_id") != tool_id
                    or not isinstance(row.get("tool_name"), str) or not row["tool_name"]
                    or row.get("request_reference") != expected_owners.get(tool_id)
                    or row.get("status") != "error"
                    or row.get("error_type") != "invalid_tool_params"
                    or row.get("execution_status") != "not_started"
                    or not lifecycle._hex_digest(row.get("input_sha256"))
                    or type(row.get("sequence")) is not int or row["sequence"] < 0):
                raise ValueError("rejection schema")
            rejected_ids.add(tool_id)
        derived_batch_tools = {}
        derived_rejections = {}
        batch_path = observer / "post-tool-batch-events.jsonl"
        if batch_path.exists():
            batch_raw = lifecycle._read_regular(batch_path, MAX_LEDGER)
            for expected_sequence, raw_line in enumerate(batch_raw.splitlines()):
                receipt_row = lifecycle._strict_json(raw_line)
                receipt_fields = {
                    "schema", "run_nonce", "sequence", "session_id", "phase",
                    "input_sha256", "input_base64", "tool_call_ids",
                    "rejected_tool_call_ids", "output", "observed_at", "monotonic_ns",
                }
                if (set(receipt_row) != receipt_fields
                        or receipt_row.get("schema") != lifecycle.SCHEMA
                        or receipt_row.get("run_nonce") != launch["run_nonce"]
                        or receipt_row.get("sequence") != expected_sequence
                        or receipt_row.get("phase") != "PostToolBatch"
                        or not isinstance(receipt_row.get("session_id"), str)
                        or not receipt_row["session_id"]
                        or not isinstance(receipt_row.get("tool_call_ids"), list)
                        or not isinstance(receipt_row.get("rejected_tool_call_ids"), list)
                        or receipt_row.get("output", {}).get("continue") is not True):
                    raise ValueError("batch receipt schema")
                input_raw = base64.b64decode(
                    receipt_row["input_base64"], validate=True)
                if (lifecycle.sha256(input_raw) != receipt_row.get("input_sha256")
                        or len(input_raw) > lifecycle.MAX_HOOK_INPUT_BYTES):
                    raise ValueError("batch input digest")
                event = lifecycle._strict_json(input_raw)
                calls = event.get("tool_calls")
                if (event.get("hook_event_name") != "PostToolBatch"
                        or event.get("session_id") != receipt_row["session_id"]
                        or not isinstance(calls, list) or not calls):
                    raise ValueError("batch event schema")
                call_ids, qualifying = [], []
                for call in calls:
                    if not isinstance(call, dict):
                        raise ValueError("batch call schema")
                    tool_id = call.get("tool_call_id")
                    response = call.get("tool_response")
                    if (not isinstance(tool_id, str) or not tool_id or len(tool_id) > 4096
                            or call.get("tool_use_id") != tool_id
                            or not isinstance(call.get("tool_name"), str)
                            or not call["tool_name"] or not isinstance(response, dict)
                            or tool_id not in expected_owners
                            or tool_id in call_ids or tool_id in derived_batch_tools):
                        raise ValueError("batch call identity")
                    call_ids.append(tool_id)
                    derived_batch_tools[tool_id] = {
                        "tool_call_id": tool_id, "tool_name": call["tool_name"],
                        "request_reference": expected_owners[tool_id],
                        "status": call.get("status"),
                        "error_type": response.get("error_type"),
                        "execution_status": response.get("execution_status"),
                        "input_sha256": receipt_row["input_sha256"],
                        "sequence": expected_sequence,
                    }
                    if (call.get("status") == "error"
                            and response.get("error_type") == "invalid_tool_params"
                            and response.get("execution_status") == "not_started"):
                        qualifying.append(tool_id)
                        derived_rejections[tool_id] = {
                            "tool_call_id": tool_id, "tool_name": call["tool_name"],
                            "request_reference": expected_owners.get(tool_id),
                            "status": "error", "error_type": "invalid_tool_params",
                            "execution_status": "not_started",
                            "input_sha256": receipt_row["input_sha256"],
                            "sequence": expected_sequence,
                        }
                if (call_ids != receipt_row["tool_call_ids"]
                        or qualifying != receipt_row["rejected_tool_call_ids"]):
                    raise ValueError("batch receipt projection")
        if derived_batch_tools != batch_tools["tools"]:
            raise ValueError("derived batch tool state")
        if derived_rejections != rejections["rejected"]:
            raise ValueError("derived rejection state")
        if (completed_ids & rejected_ids or rejected_ids - expected_ids
                or batch_ids != expected_ids):
            raise ValueError("ambiguous rejected tool")
        if (receipt["expected_tool_count"] != len(expected_ids)
                or receipt["batched_tool_count"] != len(expected_ids & batch_ids)
                or receipt["completed_tool_count"] != len(expected_ids & completed_ids)
                or receipt["rejected_tool_count"] != len(expected_ids & rejected_ids)):
            raise ValueError("derived tool reconciliation")
        if receipt.get("status") != "PASS" or receipt.get("fault_sha256") is not None \
                or receipt.get("fault_reason") is not None \
                or incomplete or receipt["expected_tool_count"] != receipt["batched_tool_count"] \
                or receipt["expected_tool_count"] != (\
                    receipt["completed_tool_count"] + receipt["rejected_tool_count"]):
            return ["LIFECYCLE_AUDIT_FAULT"]
        return []
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError,
            json.JSONDecodeError, AttributeError):
        return ["LIFECYCLE_AUDIT_INVALID"]


def discover_controller_authority(trace):
    """Use the controller's fixed layout; bench-status authenticates both hints."""
    manifest = read_json(Path(trace).resolve() / "run-manifest.json")
    commitment = manifest.get("commitment")
    if not isinstance(commitment, dict):
        raise ValueError("missing commitment authority")
    commitment_path = Path(commitment.get("path", ""))
    if (not commitment_path.is_absolute()
            or commitment_path.name != "run-commitments.jsonl"
            or commitment_path.parent.name != "records"):
        raise ValueError("non-controller authority layout")
    root = commitment_path.parent.parent
    return str(commitment_path), str(root / "keys" / "controller.key")


def replay(trace):
    trace = Path(trace)
    script = trace / "replay.sh"
    if script.is_symlink() or not script.is_file():
        return None
    bash = shutil.which("bash")
    if bash is None:
        return None
    path_parts = [
        str(Path(sys.executable).parent),
        str(Path(bash).parent),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    environment = {
        "PATH": os.pathsep.join(dict.fromkeys(path_parts)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "LC_ALL": "C",
    }
    try:
        # Replayers are authenticated programs, but the gate programs they run
        # may have non-gating maintenance side effects.  triagecheck, for
        # example, refreshes an existing checkpoint.json.  Never execute those
        # programs inside the evidence tree whose terminal seal we are proving.
        with tempfile.TemporaryDirectory(prefix="sherlock-verdict-replay-") as raw:
            scratch = Path(raw) / "trace"
            scratch.mkdir()
            for name in ("replay.sh", "gates.json"):
                source = trace / name
                if source.exists():
                    if source.is_symlink() or not source.is_file():
                        return None
                    shutil.copy2(source, scratch / name)
            for name in ("staged-corpus", "gate-tools", "reference", "work"):
                source = trace / name
                if not source.exists():
                    continue
                if source.is_symlink() or not source.is_dir():
                    return None
                for entry in source.rglob("*"):
                    if entry.is_symlink() or (not entry.is_dir() and not entry.is_file()):
                        return None
                shutil.copytree(source, scratch / name)
            result = subprocess.run(
                [bash, str(scratch / "replay.sh")],
                cwd=scratch,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1800,
            )
    except (OSError, shutil.Error, subprocess.TimeoutExpired):
        return None
    return result.returncode


def upstream_metrics(trace):
    path = trace / "upstream-completed.jsonl"
    if not os.path.lexists(path):
        return {}, []
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_size > MAX_LEDGER:
        raise ValueError("unsafe ledger")
    calls = prompt = output = cached = length = reasoning = usage_calls = 0
    snapshot_visible = snapshot_reasoning = snapshot_clips = 0
    memory_visible = memory_reasoning = memory_clips = 0
    peak = 0
    clipped = []
    estimates = []
    estimate_complete = True
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            calls += 1
            if calls > MAX_LEDGER_ROWS:
                raise ValueError("oversized ledger")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("invalid ledger row")
            estimate = row.get("estimated_cost_rub")
            if (isinstance(estimate, bool) or not isinstance(estimate, (int, float))
                    or not math.isfinite(estimate) or estimate < 0):
                estimate_complete = False
            else:
                estimates.append(estimate)
            request_class = row.get("clipped_request_class")
            normalized_class = (
                request_class.replace("-", "_") if isinstance(request_class, str) else None
            )
            if row.get("finish_reason") == "length":
                length += 1
                if normalized_class in {"compaction", "state_snapshot"}:
                    clipped.append(normalized_class)
            # An absent counter is an unknown observation; an explicit null or
            # malformed provider counter is an invalid ledger, never exact zero.
            if "usage" not in row:
                continue
            usage = row["usage"]
            if not isinstance(usage, dict):
                raise ValueError("invalid provider usage counters")
            prompt_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            if (type(prompt_tokens) is not int or prompt_tokens < 0 or
                    type(output_tokens) is not int or output_tokens < 0):
                raise ValueError("invalid provider usage counters")
            details = usage.get("prompt_tokens_details")
            if details is None:
                cached_tokens = 0
            elif isinstance(details, dict):
                cached_tokens = details.get("cached_tokens", 0)
                if type(cached_tokens) is not int or cached_tokens < 0:
                    raise ValueError("invalid provider usage counters")
            else:
                raise ValueError("invalid provider usage counters")
            completion_details = usage.get("completion_tokens_details")
            if completion_details is None:
                reasoning_tokens = 0
            elif isinstance(completion_details, dict):
                reasoning_tokens = completion_details.get("reasoning_tokens", 0)
                if type(reasoning_tokens) is not int or reasoning_tokens < 0:
                    raise ValueError("invalid provider usage counters")
            else:
                raise ValueError("invalid provider usage counters")
            usage_calls += 1
            prompt += prompt_tokens
            output += output_tokens
            cached += cached_tokens
            reasoning += reasoning_tokens
            if (row.get("finish_reason") == "length"
                    and normalized_class in {"compaction", "state_snapshot"}):
                memory_clips += 1
                memory_reasoning += reasoning_tokens
                memory_visible += max(0, output_tokens - reasoning_tokens)
            if row.get("finish_reason") == "length" and normalized_class == "state_snapshot":
                snapshot_clips += 1
                snapshot_reasoning += reasoning_tokens
                snapshot_visible += max(0, output_tokens - reasoning_tokens)
            maximum = row.get("request_max_tokens")
            maximum = maximum if type(maximum) is int and maximum >= 0 else 0
            peak = max(peak, prompt_tokens + maximum)
    metrics = {
        "upstream_calls": calls,
        "provider_calls_observed": calls,
        "usage_bearing_calls": usage_calls,
        "usage_observed": {
            "prompt_tokens": prompt,
            "cached_prompt_tokens": cached,
            "completion_tokens": output,
        },
        "estimated_cost": (
            sum(estimates) if calls and estimate_complete and len(estimates) == calls else None
        ),
        "prompt_tokens": prompt,
        "output_tokens": output,
        "cached_prompt_tokens": cached,
        "cache_hit_percent": round(100.0 * cached / prompt, 1) if prompt else None,
        "length_stops": length,
        "peak_prompt_plus_max_tokens": peak,
        "reasoning_tokens": reasoning,
        "snapshot_length_stops": snapshot_clips,
        "snapshot_reasoning_tokens": snapshot_reasoning,
        "snapshot_visible_tokens": snapshot_visible,
        "memory_length_stops": memory_clips,
        "memory_reasoning_tokens": memory_reasoning,
        "memory_visible_tokens": memory_visible,
    }
    return metrics, clipped


def _optional_json(trace, name):
    try:
        return read_json(trace / name)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None


def _exit_code(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _finite_nonnegative(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and
            math.isfinite(value) and value >= 0)


def _fresh_timestamp(value):
    """Match the dispatch boundary: UTC, no older than a day or 5m future."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
            return False
        age = time.time() - parsed.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return -300 <= age <= 86400


def _utc_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0) else None


def _latest_attempt_exit(trace):
    path = trace / "attempts.jsonl"
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_size > MAX_JSON:
            return None
        rows = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in reversed(rows):
        try:
            row = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(row, dict):
            code = _exit_code(row.get("exit_code"))
            if code is not None:
                return code
    return None


def _billing_metrics(trace):
    # No provider-verifiable receipt schema or trust root exists yet. Local
    # files, their hashes, and controller signatures are not provider evidence.
    return {"provider_billed_calls": None, "provider_billed_cost": None}


def _budget_estimate(trace, run_tag):
    """Return only a fresh configured estimate from a complete budget state."""
    budget = _optional_json(trace, "upstream-budget-state.json")
    fields = {"schema", "run_tag", "updated_at", "limits", "rate_snapshot",
              "budget_assurance", "projected", "observed", "completed_overshoot",
              "observed_usage_unknown", "completed_attempt_ids", "verdict", "reason"}
    limits = ("max_provider_calls", "max_prompt_tokens", "max_completion_tokens",
              "max_wall_time_s", "max_estimated_cost_rub")
    observed = ("provider_calls", "prompt_tokens", "completion_tokens")
    projected = observed + ("wall_time_s", "estimated_cost_rub")
    if not isinstance(budget, dict) or set(budget) != fields or budget.get("run_tag") != run_tag:
        return None, None
    monitored = budget.get("schema") == 3
    if monitored:
        profile = _optional_json(trace, "target-profile.json")
        if (not isinstance(profile, dict) or profile.get("schema") != 2
                or profile.get("execution_mode") != "operator_monitored"
                or profile.get("request_read_timeout_s") != 600
                or budget.get("budget_assurance") != "operator_monitored"):
            return None, None
    elif budget.get("schema") != 2 or budget.get("budget_assurance") != "client_pre_dispatch":
        return None, None
    snapshot = budget.get("rate_snapshot")
    launch_rate_valid = True
    monitored_update_valid = True
    if monitored:
        launch = _optional_json(trace, "launch-start.json")
        sealed_snapshot = _optional_json(trace, "probe-rate-snapshot.json")
        started_at = launch.get("started_at") if isinstance(launch, dict) else None
        try:
            started = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            effective = dt.datetime.fromisoformat(snapshot.get("effective_at", "").replace("Z", "+00:00")) \
                if isinstance(snapshot, dict) else None
            launch_rate_valid = (started.tzinfo is not None and started.utcoffset() == dt.timedelta(0)
                                 and effective is not None and effective.tzinfo is not None
                                 and effective.utcoffset() == dt.timedelta(0)
                                 and effective <= started + dt.timedelta(minutes=5)
                                 and started - effective <= dt.timedelta(hours=24)
                                 and snapshot == sealed_snapshot)
            updated = _utc_timestamp(budget.get("updated_at"))
            monitored_update_valid = (updated is not None and updated >= started
                                      and updated.timestamp() <= time.time() + 300)
        except (AttributeError, TypeError, ValueError, OverflowError):
            launch_rate_valid = False
            monitored_update_valid = False
    if ((not monitored and not _fresh_timestamp(budget.get("updated_at")))
            or (monitored and not monitored_update_valid) or not isinstance(snapshot, dict)
            or not launch_rate_valid
            or not isinstance(budget.get("limits"), dict)
            or set(budget["limits"]) != set(limits)
            or (monitored and any(budget["limits"].get(name) is not None for name in limits))
            or (not monitored and any(not _finite_nonnegative(budget["limits"].get(name)) for name in limits))
            or (not monitored and any(type(budget["limits"].get(name)) is not int for name in limits[:3]))
            or any(not isinstance(budget.get(group), dict) for group in
                   ("projected", "observed", "completed_overshoot"))
            or set(budget["projected"]) != set(projected)
            or set(budget["observed"]) != set(observed)
            or set(budget["completed_overshoot"]) != set(observed)):
        return None, None
    if (any(not _finite_nonnegative(budget["projected"].get(name)) for name in projected)
            or any(type(budget["projected"].get(name)) is not int for name in observed)
            or any(type(budget[group].get(name)) is not int or budget[group][name] < 0
                   for group in ("observed", "completed_overshoot") for name in observed)
            or type(budget.get("observed_usage_unknown")) is not int
            or budget["observed_usage_unknown"] < 0
            or not isinstance(budget.get("completed_attempt_ids"), list)
            or len(set(budget["completed_attempt_ids"])) != len(budget["completed_attempt_ids"])
            or not all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{32}\.a[1-9][0-9]*", item)
                       for item in budget["completed_attempt_ids"])
            or budget["observed"]["provider_calls"] != len(budget["completed_attempt_ids"])
            or budget["projected"]["provider_calls"] < budget["observed"]["provider_calls"]
            or budget["observed_usage_unknown"] > budget["observed"]["provider_calls"]
            or (monitored and any(budget["completed_overshoot"].values()))
            or (not monitored and any(budget["completed_overshoot"][name] != max(
                budget["observed"][name] - budget["limits"]["max_" + name], 0)
                   for name in observed))
            or (monitored and (budget.get("verdict") != "WITHIN" or budget.get("reason") is not None))
            or (not monitored and budget.get("verdict") not in {"WITHIN", "EXCEEDED"})
            or (budget.get("reason") is not None and
                (not isinstance(budget.get("reason"), str) or
                 re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", budget["reason"]) is None))):
        return None, None
    cost = budget["projected"]["estimated_cost_rub"]
    rate_fields = {"schema", "run_tag", "effective_at", "source", "sha256",
                   "prompt_rub_per_token", "completion_rub_per_token"}
    if (set(snapshot) != rate_fields or snapshot.get("schema") != 1
            or snapshot.get("run_tag") != run_tag
            or not all(isinstance(snapshot.get(name), str) and snapshot[name]
                       for name in ("effective_at", "source", "sha256"))
            or (not monitored and not _fresh_timestamp(snapshot.get("effective_at")))):
        return None, None
    if any(isinstance(snapshot.get(name), bool)
           or not isinstance(snapshot.get(name), (int, float))
           or not math.isfinite(snapshot[name]) or snapshot[name] < 0
           for name in ("prompt_rub_per_token", "completion_rub_per_token")):
        return None, None
    unsigned = {name: snapshot[name] for name in rate_fields if name != "sha256"}
    digest = hashlib.sha256(json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if snapshot["sha256"] != digest:
        return None, None
    return cost, snapshot


def terminal_verdict(args, status):
    trace = Path(args.trace).resolve()
    failures = []
    if not args.authenticated:
        failures.append("AUTHORITY_UNCONTROLLED")
        try:
            read_json(trace / "candidate.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            failures.append("CANDIDATE_MISSING")
    elif args.target_probe:
        try:
            read_json(trace / "candidate.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            failures.append("CANDIDATE_MISSING")
    validity = status.get("validity")
    if (args.authenticated and not args.target_probe
            and (not isinstance(validity, dict) or validity.get("state") != "accepted")):
        failures.append("VALIDITY_NOT_ACCEPTED")
    try:
        gates = read_json(trace / "gates.json")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        gates = {}
        failures.append("GATES_INVALID")
    raw_gate_rows = gates.get("gates") if isinstance(gates.get("gates"), dict) else {}
    gate_rows = {
        name: raw_gate_rows.get(name) if isinstance(raw_gate_rows.get(name), dict) else {}
        for name in REQUIRED_GATES
    }
    gate_exits = {name: gate_rows.get(name, {}).get("exit_code") for name in REQUIRED_GATES}
    gate_blocking = {name: gate_rows.get(name, {}).get("blocking") for name in REQUIRED_GATES}
    unknown_gates = [
        name for name in REQUIRED_GATES
        if (type(gate_exits[name]) is not int or gate_exits[name] < 0
            or type(gate_blocking[name]) is not int or gate_blocking[name] < 0)
    ]
    gates_clean = (
        gates.get("verdict") == "clean"
        and gates.get("arm_intact") is True
        and all(
            isinstance(gate_rows.get(name), dict)
            and type(gate_rows[name].get("exit_code")) is int
            and gate_rows[name].get("exit_code") == 0
            and type(gate_rows[name].get("blocking")) is int
            and gate_rows[name].get("blocking") == 0
            for name in REQUIRED_GATES
        )
    )
    if unknown_gates:
        failures.append("GATE_RESULT_UNKNOWN")
    if not gates_clean and "GATES_INVALID" not in failures:
        failures.append("GATES_BLOCKING")
    arm_changed = []
    if gates.get("arm_intact") is False:
        failures.append("ARM_MUTATED")
        try:
            arm_integrity = read_json(trace / "arm-integrity.json")
            changed = arm_integrity.get("changed")
            if isinstance(changed, list):
                arm_changed = [
                    item.get("path") for item in changed
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                ]
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            pass
    try:
        lane = read_json(trace / "lane-integrity.json")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        lane = None
        failures.append("LANE_INTEGRITY_INVALID")
    if lane is not None:
        if lane.get("schema") != 1 or lane.get("verdict") not in {"clean", "breach"}:
            failures.append("LANE_INTEGRITY_INVALID")
        elif lane.get("verdict") != "clean":
            failures.append("LANE_INTEGRITY_BREACH")
    if os.path.lexists(trace / "seal-failure.json"):
        failures.append("TRACE_NOT_SEALED")
    report = trace / "work" / "report.md"
    report_present = report.is_file() and not report.is_symlink() and report.stat().st_size > 0
    if not report_present:
        failures.append("REPORT_MISSING")
    replay_exit = replay(trace)
    if replay_exit != 0:
        failures.append("REPLAY_FAILED")
    try:
        ledger_metrics, clipped = upstream_metrics(trace)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        ledger_metrics, clipped = {}, []
        failures.append("UPSTREAM_LEDGER_INVALID")
    lifecycle_failures = monitored_lifecycle_failures(trace)
    failures.extend(lifecycle_failures)
    if clipped:
        failures.append("COMPACTION_OUTPUT_CLIPPED")
    if args.target_probe and (ledger_metrics.get("provider_calls_observed", 0) < 1
                              or ledger_metrics.get("usage_bearing_calls", 0) < 1):
        failures.append("UPSTREAM_USAGE_MISSING")
    report_integrity_failures = {
        "AUTHORITY_UNCONTROLLED",
        "CANDIDATE_MISSING",
        "VALIDITY_NOT_ACCEPTED",
        "ARM_MUTATED",
        "LANE_INTEGRITY_INVALID",
        "LANE_INTEGRITY_BREACH",
        "TRACE_NOT_SEALED",
        "UPSTREAM_LEDGER_INVALID",
        "LIFECYCLE_AUDIT_INVALID",
        "LIFECYCLE_AUDIT_FAULT",
        "COMPACTION_OUTPUT_CLIPPED",
    }
    report_correct = (
        gates_clean
        and report_present
        and replay_exit == 0
        and not report_integrity_failures.intersection(failures)
    )
    successful = (
        status.get("phase") == "ACCEPTED"
        and not failures
        and report_correct
    )
    improvements = []
    if not args.authenticated:
        improvements.append({
            "code": "USE_AUTHENTICATED_CONTROLLER_NEXT_RUN",
            "action": "Launch paid runs through bench-controller so status and artifacts are committed.",
            "evidence": {"authority": "uncontrolled-local"},
        })
    if "CANDIDATE_MISSING" in failures:
        improvements.append({
            "code": "PRESERVE_CANDIDATE_ARTIFACT",
            "action": "Require a bounded regular candidate.json before accepting a direct run.",
            "evidence": {"path": "candidate.json"},
        })
    gate_repairs = {
        "citecheck": ("FIX_CITATION_DEFECTS",
                      "Repair or remove every unsupported citation, then replay citecheck."),
        "triagecheck": ("CLOSE_WORKLIST_GAPS",
                        "Resolve every uncovered worklist row, then replay triagecheck."),
        "statecheck": ("ADD_MISSING_STATE_EVIDENCE",
                       "Add cited evidence for every changed state, then replay statecheck."),
        "reportcheck": ("REPAIR_REPORT_CONTRACT",
                        "Restore the required inventory, missing-data, labels, and final verdict."),
    }
    for gate, (code, action) in gate_repairs.items():
        if gate in unknown_gates:
            continue
        if gate_exits[gate] != 0 or gate_blocking[gate] > 0:
            gate_json = gate_rows[gate].get("json")
            gate_json = gate_json if isinstance(gate_json, dict) else {}
            if gate == "citecheck":
                report_evidence = gate_json.get("report_evidence")
                report_evidence = (
                    report_evidence if isinstance(report_evidence, dict) else {}
                )
                enum_decode = report_evidence.get("enum_decode")
                enum_decode = enum_decode if isinstance(enum_decode, dict) else {}
                enum_items = enum_decode.get("items")
                if isinstance(enum_items, list) and enum_items:
                    improvements.append({
                        "code": "FIX_UNKNOWN_ENUM_DECODE",
                        "action": (
                            "Keep unknown enum values raw, or add a reviewed mapping before "
                            "the run; never edit the frozen arm during measurement."
                        ),
                        "evidence": {"items": enum_items[:20], "blocking": gate_blocking[gate]},
                    })
                    continue
            if gate == "reportcheck":
                defects = gate_json.get("defects")
                defect_names = {
                    item.get("defect") for item in defects
                    if isinstance(defects, list) and isinstance(item, dict)
                } if isinstance(defects, list) else set()
                if defects and defect_names == {"label_unknown"}:
                    improvements.append({
                        "code": "FIX_LABEL_BOUNDARY_PARSER",
                        "action": (
                            "Parse labels only at assertion boundaries; never whitelist "
                            "uppercase corpus text."
                        ),
                        "evidence": {"defect": "label_unknown", "blocking": gate_blocking[gate]},
                    })
                    continue
            improvements.append({
                "code": code,
                "action": action,
                "evidence": {"gate": gate, "exit_code": gate_exits[gate],
                             "blocking": gate_blocking[gate]},
            })
    if "ARM_MUTATED" in failures:
        improvements.append({
            "code": "PREVENT_ARM_MUTATION",
            "action": (
                "Make frozen grader data read-only and reject its first write attempt, "
                "not only the final snapshot."
            ),
            "evidence": {"changed": arm_changed},
        })
    if unknown_gates:
        improvements.append({
            "code": "RECORD_MACHINE_GATE_RESULTS",
            "action": "Require each gate to emit an integer blocking count before acceptance.",
            "evidence": {"gates": unknown_gates},
        })
    if replay_exit != 0:
        improvements.append({
            "code": "REPAIR_REPLAY_DIVERGENCE",
            "action": "Compare recorded and replayed gate exits before trusting the report.",
            "evidence": {"replay_exit": replay_exit},
        })
    if not report_present:
        improvements.append({
            "code": "WRITE_REPORT_ARTIFACT",
            "action": "Require a non-empty work/report.md before the run can finish.",
            "evidence": {"path": "work/report.md"},
        })
    if "TRACE_NOT_SEALED" in failures:
        improvements.append({
            "code": "REBUILD_SELF_CONTAINED_TRACE",
            "action": "Seal corpus, grader, reference data, and replay before acceptance.",
            "evidence": {"artifact": "seal-failure.json"},
        })
    if "UPSTREAM_LEDGER_INVALID" in failures:
        improvements.append({
            "code": "REPAIR_UPSTREAM_LEDGER",
            "action": "Preserve a bounded valid JSON row for every paid upstream call.",
            "evidence": {"artifact": "upstream-completed.jsonl"},
        })
    if "LANE_INTEGRITY_INVALID" in failures:
        improvements.append({
            "code": "REPAIR_LANE_INTEGRITY_EVIDENCE",
            "action": "Require a schema-1 clean or breach lane-integrity verdict.",
            "evidence": {"artifact": "lane-integrity.json"},
        })
    lane = lane or {}
    generation_window_seconds = None
    try:
        run_inputs = read_json(trace / "run-inputs.json")
        generation_window = run_inputs.get("generation_window")
        if isinstance(generation_window, dict):
            generation_window_seconds = generation_window.get("generation_window_seconds")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        pass
    reasoning_memory_clip = (
        bool(clipped)
        and ledger_metrics.get("memory_reasoning_tokens", 0) > 0
        and isinstance(generation_window_seconds, (int, float))
        and generation_window_seconds <= 0
    )
    if reasoning_memory_clip:
        improvements.append({
            "code": "SEPARATE_REASONING_FROM_SNAPSHOT_BUDGET",
            "action": (
                "Disable reasoning for compaction/state-snapshot requests, or reserve "
                "its tokens separately before another paid run."
            ),
            "evidence": {
                "generation_window_seconds": generation_window_seconds,
                "memory_length_stops": ledger_metrics.get("memory_length_stops"),
                "memory_reasoning_tokens": ledger_metrics.get("memory_reasoning_tokens"),
                "memory_visible_tokens": ledger_metrics.get("memory_visible_tokens"),
            },
        })
    elif lane.get("reason") == "COMPACTION_OUTPUT_CLIPPED" or clipped:
        improvements.append({
            "code": "USE_UNWINDOWED_OR_198S_LANE",
            "action": "Use a lane without a generation clock, or one with at least 198 seconds.",
            "evidence": {"reason": lane.get("reason") or "COMPACTION_OUTPUT_CLIPPED",
                         "detail": lane.get("detail"), "request_classes": clipped},
        })
    elif lane.get("verdict") == "breach":
        improvements.append({
            "code": "USE_EXACT_MODEL_LANE",
            "action": "Use a lane that returns the committed model identity on every call.",
            "evidence": {"reason": lane.get("reason"), "detail": lane.get("detail")},
        })
    attempt_exit_code = _latest_attempt_exit(trace)
    driver = _optional_json(trace, "driver-result.json")
    if not isinstance(driver, dict):
        driver = _optional_json(trace, "recovery.json")
    driver_exit_code = _exit_code(driver.get("exit_code")) if isinstance(driver, dict) else None
    exit_layers = {
        "attempt_exit_code": attempt_exit_code,
        "driver_exit_code": driver_exit_code,
        "gate_exit_codes": gate_exits,
        "wrapper_exit_code": _exit_code(
            status.get("wrapper_exit_code", status.get("exit_code"))
        ),
        "primary_failure": (status.get("primary_failure")
                            if status.get("primary_failure") in PRIMARY_FAILURE_CODES else None),
        "terminal_observation": status.get("phase"),
    }
    if args.target_probe and any(exit_layers[name] != 0 for name in
                                 ("attempt_exit_code", "driver_exit_code", "wrapper_exit_code")):
        failures.append("EXIT_LAYER_NONZERO")
    metrics = {"gate_exits": gate_exits, "gate_blocking": gate_blocking,
               "lifecycle_failures": lifecycle_failures,
               "replay_exit": replay_exit}
    metrics.update(ledger_metrics)
    estimate, rate_snapshot = _budget_estimate(trace, status.get("run_tag"))
    metrics["estimated_cost"] = estimate
    metrics["rate_snapshot"] = rate_snapshot
    metrics["rate_assurance"] = "configured_unverified" if estimate is not None else None
    metrics.update(_billing_metrics(trace))
    if args.target_probe:
        successful = status.get("phase") == "ACCEPTED" and not failures and report_correct
    return {
        "schema": 1,
        "run_tag": status.get("run_tag"),
        "state": "finished",
        "phase": status.get("phase"),
        "finished": True,
        "successful": successful,
        "report_correct": report_correct,
        "report_correctness_scope": "sealed-contract-gates",
        "authenticated": args.authenticated,
        "authority": args.authority,
        "failures": failures,
        "metrics": metrics,
        "improvements": improvements,
        **exit_layers,
    }


def answer(value):
    if value is None:
        return "pending"
    return "yes" if value else "no"


def render_summary(row):
    """Render cost evidence without turning observation or estimates into billing claims."""
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    lines = [
        "provider_calls_observed=%s" % metrics.get("provider_calls_observed"),
        "usage_bearing_calls=%s" % metrics.get("usage_bearing_calls"),
    ]
    if metrics.get("estimated_cost") is not None:
        lines.extend([
            "configured_estimated_cost=%s" % metrics.get("estimated_cost"),
            "rate_assurance=%s" % metrics.get("rate_assurance"),
        ])
    return "\n".join(lines)


def emit(row, as_json):
    if as_json:
        sys.stdout.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return
    failures = ",".join(row["failures"]) or "none"
    improvements = ",".join(item["code"] for item in row["improvements"]) or "none"
    lines = [
        "%s %s %s" % (row["state"].upper(), row.get("run_tag"), row.get("phase")),
        "successful=%s report_correct=%s" % (
            answer(row["successful"]), answer(row["report_correct"])
        ),
        "report_scope=%s" % row["report_correctness_scope"],
        "authenticated=%s authority=%s" % (
            answer(row["authenticated"]), row["authority"]
        ),
        "failures=%s" % failures,
        "improvements=%s" % improvements,
    ]
    lines.extend(render_summary(row).splitlines())
    sys.stdout.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Wait for one Sherlock trace and verify its terminal report."
    )
    parser.add_argument("trace", help="run trace directory")
    parser.add_argument("--commitment-file", help="override controller commitment ledger")
    parser.add_argument("--commitment-key", help="override controller HMAC key")
    parser.add_argument("--target-probe", action="store_true",
                        help="verify the separately authorized target-contract probe")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    parser.add_argument("--wait", action="store_true", help="poll until terminal or timeout")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=86400.0)
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0 or args.timeout_seconds <= 0:
        parser.error("poll and timeout seconds must be positive")
    if bool(args.commitment_file) != bool(args.commitment_key):
        parser.error("commitment file and key must be supplied together")
    if args.target_probe and (args.commitment_file or args.commitment_key):
        parser.error("target-probe is mutually exclusive with commitment authority")
    manifest_path = Path(args.trace).resolve() / "run-manifest.json"
    args.authenticated = (True if args.target_probe
                          else bool(args.commitment_file) or os.path.lexists(manifest_path))
    args.authority = ("operator-approved-target-probe" if args.target_probe
                      else "controller-hmac" if args.authenticated else "uncontrolled-local")
    if args.authenticated and not args.target_probe and not args.commitment_file:
        try:
            args.commitment_file, args.commitment_key = discover_controller_authority(args.trace)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            sys.stderr.write("TRACE_AUTHORITY_UNRESOLVED\n")
            return 2
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        try:
            status = status_projection(args)
        except (OSError, ValueError, json.JSONDecodeError):
            sys.stderr.write("TRACE_UNRESOLVED\n")
            return 2
        status["authenticated"] = args.authenticated
        status["authority"] = args.authority
        if status.get("phase") in TERMINAL:
            break
        row = running_verdict(status)
        if not args.wait or time.monotonic() >= deadline:
            emit(row, args.json)
            return 2
        time.sleep(min(args.poll_seconds, max(0.0, deadline - time.monotonic())))
    row = terminal_verdict(args, status)
    emit(row, args.json)
    return 0 if row["successful"] and row["report_correct"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
