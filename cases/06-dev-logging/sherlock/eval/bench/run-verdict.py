#!/usr/bin/env python3
"""Wait for a Sherlock trace, verify its report, and explain any failure."""
import argparse
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


HERE = Path(__file__).resolve().parent
STATUS_TOOL = HERE / "bench-status.py"
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
    if (not isinstance(budget, dict) or set(budget) != fields or budget.get("schema") != 2
            or budget.get("run_tag") != run_tag or budget.get("budget_assurance") != "client_pre_dispatch"):
        return None, None
    snapshot = budget.get("rate_snapshot")
    if (not _fresh_timestamp(budget.get("updated_at")) or not isinstance(snapshot, dict)
            or not isinstance(budget.get("limits"), dict)
            or set(budget["limits"]) != set(limits)
            or any(not _finite_nonnegative(budget["limits"].get(name)) for name in limits)
            or any(type(budget["limits"].get(name)) is not int for name in limits[:3])
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
            or any(budget["completed_overshoot"][name] != max(
                budget["observed"][name] - budget["limits"]["max_" + name], 0)
                   for name in observed)
            or budget.get("verdict") not in {"WITHIN", "EXCEEDED"}
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
            or not _fresh_timestamp(snapshot.get("effective_at"))):
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
