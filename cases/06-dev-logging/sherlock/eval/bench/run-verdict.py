#!/usr/bin/env python3
"""Wait for a Sherlock trace, verify its report, and explain any failure."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
STATUS_TOOL = HERE / "bench-status.py"
TERMINAL = {"ACCEPTED", "REJECTED", "RUN_FAILED", "FINISHED", "FINISHED_UNCHECKED"}
REQUIRED_GATES = ("citecheck", "triagecheck", "statecheck", "reportcheck")
MAX_JSON = 1024 * 1024
MAX_LEDGER = 64 * 1024 * 1024
MAX_LEDGER_ROWS = 100000


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
    command = [
        sys.executable,
        str(STATUS_TOOL),
        args.trace,
        "--commitment-file",
        args.commitment_file,
        "--commitment-key",
        args.commitment_key,
        "--json",
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("TRACE_UNRESOLVED") from exc
    if result.returncode != 0:
        raise ValueError("TRACE_UNRESOLVED")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
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
    script = trace / "replay.sh"
    if script.is_symlink() or not script.is_file():
        return None
    path_parts = [str(Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    environment = {
        "PATH": os.pathsep.join(dict.fromkeys(path_parts)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "LC_ALL": "C",
    }
    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            cwd=trace,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode


def upstream_metrics(trace):
    path = trace / "upstream-completed.jsonl"
    if not os.path.lexists(path):
        return {}, []
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_size > MAX_LEDGER:
        raise ValueError("unsafe ledger")
    calls = prompt = output = cached = length = 0
    peak = 0
    clipped = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            calls += 1
            if calls > MAX_LEDGER_ROWS:
                raise ValueError("oversized ledger")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("invalid ledger row")
            if row.get("finish_reason") == "length":
                length += 1
                request_class = row.get("clipped_request_class")
                if request_class in {"compaction", "state-snapshot"}:
                    clipped.append(request_class)
            usage = row.get("usage")
            if not isinstance(usage, dict):
                continue
            prompt_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            prompt_tokens = prompt_tokens if type(prompt_tokens) is int and prompt_tokens >= 0 else 0
            output_tokens = output_tokens if type(output_tokens) is int and output_tokens >= 0 else 0
            details = usage.get("prompt_tokens_details")
            cached_tokens = details.get("cached_tokens") if isinstance(details, dict) else 0
            cached_tokens = cached_tokens if type(cached_tokens) is int and cached_tokens >= 0 else 0
            prompt += prompt_tokens
            output += output_tokens
            cached += cached_tokens
            maximum = row.get("request_max_tokens")
            maximum = maximum if type(maximum) is int and maximum >= 0 else 0
            peak = max(peak, prompt_tokens + maximum)
    metrics = {
        "upstream_calls": calls,
        "prompt_tokens": prompt,
        "output_tokens": output,
        "cached_prompt_tokens": cached,
        "cache_hit_percent": round(100.0 * cached / prompt, 1) if prompt else None,
        "length_stops": length,
        "peak_prompt_plus_max_tokens": peak,
    }
    return metrics, clipped


def terminal_verdict(args, status):
    trace = Path(args.trace).resolve()
    failures = []
    if not args.authenticated:
        try:
            read_json(trace / "candidate.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            failures.append("CANDIDATE_MISSING")
    validity = status.get("validity")
    if (args.authenticated
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
    report_correct = gates_clean and report_present and replay_exit == 0
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
            improvements.append({
                "code": code,
                "action": action,
                "evidence": {"gate": gate, "exit_code": gate_exits[gate],
                             "blocking": gate_blocking[gate]},
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
    if lane.get("reason") == "COMPACTION_OUTPUT_CLIPPED" or clipped:
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
    metrics = {"gate_exits": gate_exits, "gate_blocking": gate_blocking,
               "replay_exit": replay_exit}
    metrics.update(ledger_metrics)
    return {
        "schema": 1,
        "run_tag": status.get("run_tag"),
        "state": "finished",
        "phase": status.get("phase"),
        "finished": True,
        "successful": successful,
        "report_correct": report_correct,
        "authenticated": args.authenticated,
        "authority": args.authority,
        "failures": failures,
        "metrics": metrics,
        "improvements": improvements,
    }


def answer(value):
    if value is None:
        return "pending"
    return "yes" if value else "no"


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
        "authenticated=%s authority=%s" % (
            answer(row["authenticated"]), row["authority"]
        ),
        "failures=%s" % failures,
        "improvements=%s" % improvements,
    ]
    sys.stdout.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Wait for one Sherlock trace and verify its terminal report."
    )
    parser.add_argument("trace", help="run trace directory")
    parser.add_argument("--commitment-file", help="override controller commitment ledger")
    parser.add_argument("--commitment-key", help="override controller HMAC key")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    parser.add_argument("--wait", action="store_true", help="poll until terminal or timeout")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=86400.0)
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0 or args.timeout_seconds <= 0:
        parser.error("poll and timeout seconds must be positive")
    if bool(args.commitment_file) != bool(args.commitment_key):
        parser.error("commitment file and key must be supplied together")
    manifest_path = Path(args.trace).resolve() / "run-manifest.json"
    args.authenticated = bool(args.commitment_file) or os.path.lexists(manifest_path)
    args.authority = "controller-hmac" if args.authenticated else "uncontrolled-local"
    if args.authenticated and not args.commitment_file:
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
