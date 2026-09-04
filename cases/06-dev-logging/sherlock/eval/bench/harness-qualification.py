#!/usr/bin/env python3
"""Provider-free qualification of the Sherlock harness, never of a paid target."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
HEX = re.compile(r"^[0-9a-f]{64}$")
EXPECTED = {
    "admission_refusal": (2, "HARNESS_QUALIFICATION_MISSING"),
    "artifact_tamper": (2, "TRACE_INVALID"),
    "attempt_exit": (9, "ATTEMPT_NONZERO"),
    "citation_gate_failure": (4, "CITATION_GATE_FAILED"),
    "driver_exit": (9, "DRIVER_NONZERO"),
    "lifecycle_transition": (2, "INVALID_TRANSITION"),
    "report_gate_failure": (4, "REPORT_GATE_FAILED"),
    "state_gate_failure": (4, "STATE_GATE_FAILED"),
    "timeout": (124, "TIMEOUT"),
    "triage_gate_failure": (4, "TRIAGE_GATE_FAILED"),
    "wrapper_exit": (2, "WRAPPER_NONZERO"),
}
BINDINGS = (
    "implementation_commit", "implementation_dirty", "runner", "driver", "proxy",
    "run_manifest_tool", "run_verdict_tool", "test_manifest", "qwen_binary",
    "qwen_version", "arm", "skill_v44", "report_contract", "report_gate_program",
    "report_gate_result", "citation_gate_program", "citation_gate_result",
    "state_gate_program", "state_gate_result", "triage_gate_program",
    "triage_gate_result", "settings", "tool_schema", "input_manifest",
    "terminal_verdict",
)
MAX_FILE = 16 * 1024 * 1024


class QualificationFailure(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QualificationFailure("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_json(data: bytes, label: str):
    try:
        if len(data) > MAX_FILE:
            raise QualificationFailure(label + "_TOO_LARGE")
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    except QualificationFailure:
        raise
    except Exception as exc:
        raise QualificationFailure(label + "_MALFORMED") from exc


def _plain_file(path: Path, label: str) -> bytes:
    if not path.is_absolute():
        raise QualificationFailure(label + "_NOT_ABSOLUTE")
    try:
        before = path.lstat()
    except OSError as exc:
        raise QualificationFailure(label + "_MISSING") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise QualificationFailure(label + "_ALIASED")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            inside = os.fstat(handle.fileno())
            data = handle.read(MAX_FILE + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise QualificationFailure(label + "_UNREADABLE") from exc
    if len(data) > MAX_FILE or (inside.st_dev, inside.st_ino, inside.st_size,
                                inside.st_mtime_ns) != (after.st_dev, after.st_ino,
                                                       after.st_size, after.st_mtime_ns):
        raise QualificationFailure(label + "_UNSTABLE")
    return data


def _exact_dict(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise QualificationFailure(label + "_SCHEMA")


def _validate_matrix(matrix):
    _exact_dict(matrix, {"schema", "verdict", "fixture_manifest_sha256",
                         "qualification_tool_sha256", "faults"}, "MATRIX")
    if matrix["schema"] != 1 or matrix["verdict"] != "clean":
        raise QualificationFailure("MATRIX_VERDICT")
    if matrix["qualification_tool_sha256"] != digest(Path(__file__).read_bytes()):
        raise QualificationFailure("MATRIX_TOOL_MISMATCH")
    if not isinstance(matrix["fixture_manifest_sha256"], str) or not HEX.fullmatch(matrix["fixture_manifest_sha256"]):
        raise QualificationFailure("MATRIX_MANIFEST_HASH")
    rows = matrix["faults"]
    if not isinstance(rows, list) or len(rows) != len(EXPECTED):
        raise QualificationFailure("MATRIX_ROWS")
    seen = set()
    for row in rows:
        _exact_dict(row, {"id", "expected", "observed", "passed"}, "MATRIX_ROW")
        case_id = row["id"]
        if case_id not in EXPECTED or case_id in seen:
            raise QualificationFailure("MATRIX_ID")
        seen.add(case_id)
        want_exit, want_failure = EXPECTED[case_id]
        literal = {"exit": want_exit, "failure": want_failure}
        if row["expected"] != literal:
            raise QualificationFailure("MATRIX_EXPECTED")
        observed = row["observed"]
        _exact_dict(observed, {"exit", "failure"}, "MATRIX_OBSERVED")
        if (isinstance(observed["exit"], bool) or not isinstance(observed["exit"], int)
                or not isinstance(observed["failure"], str)):
            raise QualificationFailure("MATRIX_OBSERVED_TYPE")
        truth = observed == literal
        if row["passed"] is not truth or not truth:
            raise QualificationFailure("MATRIX_FAILED")
    if seen != set(EXPECTED):
        raise QualificationFailure("MATRIX_MISSING")


def run_fault_matrix(fixtures: Path) -> dict:
    root = Path(fixtures)
    if not root.is_absolute():
        root = root.resolve()
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise QualificationFailure("FIXTURE_MISSING") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise QualificationFailure("FIXTURE_ROOT_INVALID")
    manifest_path = root / "manifest.json"
    manifest_raw = _plain_file(manifest_path, "FIXTURE_MANIFEST")
    manifest = parse_json(manifest_raw, "FIXTURE_MANIFEST")
    _exact_dict(manifest, {"schema", "timeout_seconds", "faults"}, "FIXTURE_MANIFEST")
    timeout = manifest["timeout_seconds"]
    if manifest["schema"] != 1 or not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not (0 < timeout <= 10):
        raise QualificationFailure("FIXTURE_MANIFEST_SCHEMA")
    rows = manifest["faults"]
    if not isinstance(rows, list) or len(rows) != len(EXPECTED):
        raise QualificationFailure("FIXTURE_CASES")
    declared = {}
    for item in rows:
        _exact_dict(item, {"id", "program", "expected"}, "FIXTURE_CASE")
        case_id = item["id"]
        if case_id not in EXPECTED or case_id in declared:
            raise QualificationFailure("FIXTURE_ID")
        want = {"exit": EXPECTED[case_id][0], "failure": EXPECTED[case_id][1]}
        if item["expected"] != want or not isinstance(item["program"], str):
            raise QualificationFailure("FIXTURE_EXPECTED")
        declared[case_id] = item
    if set(declared) != set(EXPECTED):
        raise QualificationFailure("FIXTURE_MISSING_CASE")
    receipts = root / "receipts"
    try:
        receipts.mkdir(mode=0o700)
    except OSError as exc:
        raise QualificationFailure("FIXTURE_RECEIPTS_EXIST") from exc
    observed_rows = []
    root_real = root.resolve()
    for case_id in sorted(EXPECTED):
        item = declared[case_id]
        relative = Path(item["program"])
        if relative.is_absolute() or ".." in relative.parts:
            raise QualificationFailure("FIXTURE_ESCAPE")
        program = root / relative
        data = _plain_file(program.absolute(), "FIXTURE_PROGRAM")
        del data
        if program.resolve().parent == root_real or root_real not in program.resolve().parents:
            raise QualificationFailure("FIXTURE_ESCAPE")
        if not os.access(program, os.X_OK):
            raise QualificationFailure("FIXTURE_NOT_EXECUTABLE")
        env = os.environ.copy()
        env["SHERLOCK_FAULT_RECEIPT"] = str(receipts / (case_id + ".receipt"))
        try:
            # The manifest bounds fixture work; a small fixed interpreter-start
            # allowance keeps the bound deterministic on loaded macOS runners.
            execution_timeout = (float(timeout) + 0.10 if case_id == "timeout"
                                 else max(float(timeout) + 0.10, 1.25))
            result = subprocess.run([str(program)], cwd=root, env=env, text=True,
                                    capture_output=True, timeout=execution_timeout)
            if case_id == "timeout":
                raise QualificationFailure("FIXTURE_TIMEOUT_NOT_OBSERVED")
            lines = result.stdout.splitlines()
            if len(lines) != 1 or result.stderr:
                raise QualificationFailure("FIXTURE_OUTPUT_MALFORMED")
            payload = parse_json((lines[0] + "\n").encode(), "FIXTURE_OUTPUT")
            _exact_dict(payload, {"schema", "id", "failure"}, "FIXTURE_OUTPUT")
            if payload["schema"] != 1 or payload["id"] != case_id or not isinstance(payload["failure"], str):
                raise QualificationFailure("FIXTURE_OUTPUT_SCHEMA")
            observed = {"exit": result.returncode, "failure": payload["failure"]}
        except subprocess.TimeoutExpired as exc:
            if case_id != "timeout":
                raise QualificationFailure("FIXTURE_UNEXPECTED_TIMEOUT") from exc
            observed = {"exit": 124, "failure": "TIMEOUT"}
        receipt = receipts / (case_id + ".receipt")
        if _plain_file(receipt.absolute(), "FIXTURE_RECEIPT") != (case_id + "\n").encode():
            raise QualificationFailure("FIXTURE_RECEIPT_INVALID")
        expected = {"exit": EXPECTED[case_id][0], "failure": EXPECTED[case_id][1]}
        observed_rows.append({"id": case_id, "expected": expected,
                              "observed": observed, "passed": observed == expected})
    return {"schema": 1, "verdict": "clean" if all(r["passed"] for r in observed_rows) else "failed",
            "fixture_manifest_sha256": digest(manifest_raw),
            "qualification_tool_sha256": digest(Path(__file__).read_bytes()),
            "faults": observed_rows}


def _binding_files(control):
    bindings = control.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != set(BINDINGS):
        raise QualificationFailure("BINDINGS_SCHEMA")
    result, inodes = {}, set()
    for name in BINDINGS:
        item = bindings[name]
        _exact_dict(item, {"path", "sha256"}, "BINDING")
        if not isinstance(item["path"], str) or not isinstance(item["sha256"], str) or not HEX.fullmatch(item["sha256"]):
            raise QualificationFailure("BINDING_SCHEMA")
        path = Path(item["path"])
        raw = _plain_file(path, "BINDING_" + name.upper())
        info = path.stat()
        inode = (info.st_dev, info.st_ino)
        if inode in inodes:
            raise QualificationFailure("BINDING_ALIAS")
        inodes.add(inode)
        if digest(raw) != item["sha256"]:
            raise QualificationFailure("BINDING_HASH_MISMATCH")
        result[name] = (path, raw)
    return result


def _verify_trace(trace: Path, control, files):
    run_id = control.get("free_run_id")
    observations = control.get("free_model_observations")
    if not isinstance(run_id, str) or not run_id or observations != {
            "requested": "gpt-5.5", "sent": "gpt-5.5", "returned": ["gpt-5.5"]}:
        raise QualificationFailure("FREE_IDENTITY_MISMATCH")
    commitment = control.get("commitment_file")
    key = control.get("commitment_key")
    if not isinstance(commitment, str) or not isinstance(key, str):
        raise QualificationFailure("AUTHORITY_SCHEMA")
    if (files["run_manifest_tool"][1] != (HERE / "run-manifest.py").read_bytes()
            or files["run_verdict_tool"][1] != (HERE / "run-verdict.py").read_bytes()):
        raise QualificationFailure("VALIDATOR_TOOL_MISMATCH")
    manifest_cmd = [sys.executable, str(HERE / "run-manifest.py"), "verify", str(trace),
                    "--commitment-file", commitment, "--commitment-key", key, "--json"]
    verifier_env = os.environ.copy()
    verifier_env["PYTHONDONTWRITEBYTECODE"] = "1"
    checked = subprocess.run(manifest_cmd, text=True, capture_output=True, timeout=30,
                             env=verifier_env)
    if checked.returncode != 0:
        raise QualificationFailure("RUN_MANIFEST_INVALID")
    verdict_cmd = [sys.executable, str(HERE / "run-verdict.py"), str(trace),
                   "--commitment-file", commitment, "--commitment-key", key, "--json"]
    checked = subprocess.run(verdict_cmd, text=True, capture_output=True, timeout=30,
                             env=verifier_env)
    if checked.returncode != 0 or checked.stdout.encode() != files["terminal_verdict"][1]:
        raise QualificationFailure("TERMINAL_VERDICT_STALE")
    verdict = parse_json(files["terminal_verdict"][1], "TERMINAL_VERDICT")
    if (verdict.get("run_tag") != run_id or verdict.get("phase") != "ACCEPTED"
            or verdict.get("successful") is not True or verdict.get("report_correct") is not True
            or verdict.get("authenticated") is not True):
        raise QualificationFailure("TERMINAL_VERDICT_INVALID")
    manifest = parse_json(files["input_manifest"][1], "INPUT_MANIFEST")
    profile = manifest.get("target") if isinstance(manifest, dict) else None
    if (manifest.get("run_tag") != run_id or manifest.get("arm") != "v44"
            or not isinstance(profile, dict) or profile.get("provider") != "cliproxyapi"
            or profile.get("lane") != "subscription" or profile.get("requested_model") != "gpt-5.5"
            or profile.get("expected_returned_identity") != "gpt-5.5"):
        raise QualificationFailure("INPUT_MANIFEST_IDENTITY")
    identity = manifest.get("input_identity")
    arm = parse_json(files["arm"][1], "ARM")
    if (not isinstance(identity, dict) or arm != {"schema": 1, "arm": identity.get("arm"),
            "commit": identity.get("arm_commit"), "tree": identity.get("arm_tree")}
            or files["implementation_commit"][1].decode("utf-8").strip() != identity.get("arm_commit")
            or digest(files["runner"][1]) != identity.get("runner_sha256")
            or digest(files["driver"][1]) != identity.get("driver_sha256")
            or digest(files["skill_v44"][1]) != identity.get("skill_sha256")
            or digest(files["settings"][1]) != identity.get("settings_sha256")
            or digest(files["tool_schema"][1]) != identity.get("tool_schema_sha256")):
        raise QualificationFailure("BOUND_INPUT_IDENTITY")
    gate_hashes = identity.get("gate_sha256")
    if not isinstance(gate_hashes, dict) or any(
            digest(files[name + "_gate_program"][1]) != gate_hashes.get(key)
            for name, key in (("report", "reportcheck"), ("citation", "citecheck"),
                              ("state", "statecheck"), ("triage", "triagecheck"))):
        raise QualificationFailure("BOUND_GATE_IDENTITY")
    tests = parse_json(files["test_manifest"][1], "TEST_MANIFEST")
    if tests.get("schema") != 1 or tests.get("provider_free") is not True or tests.get("failed") != 0:
        raise QualificationFailure("TEST_MANIFEST_INVALID")
    for gate in ("report", "citation", "state", "triage"):
        result = parse_json(files[gate + "_gate_result"][1], "GATE_RESULT")
        if result != {"schema": 1, "gate": gate, "exit_code": 0, "blocking": 0}:
            raise QualificationFailure("GATE_RESULT_INVALID")
    upstream = trace / "upstream-completed.jsonl"
    raw = _plain_file(upstream.absolute(), "UPSTREAM_COMPLETED")
    rows = [parse_json((line + "\n").encode(), "UPSTREAM_ROW") for line in raw.decode().splitlines()]
    if not rows or any(row.get("run_tag") != run_id or row.get("requested_model") != "gpt-5.5"
                       or row.get("sent_model") != "gpt-5.5" or row.get("returned_model") != "gpt-5.5"
                       or row.get("status") != 200 for row in rows):
        raise QualificationFailure("UPSTREAM_IDENTITY")


def audit_harness(trace: Path, matrix: dict, output: Path) -> dict:
    _validate_matrix(matrix)
    trace = Path(trace)
    output = Path(output)
    if not trace.is_absolute() or not output.is_absolute() or os.path.lexists(output):
        raise QualificationFailure("AUDIT_PATH_INVALID")
    try:
        trace_info = trace.lstat()
        parent_info = output.parent.lstat()
    except OSError as exc:
        raise QualificationFailure("AUDIT_PATH_MISSING") from exc
    if (stat.S_ISLNK(trace_info.st_mode) or not stat.S_ISDIR(trace_info.st_mode)
            or stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode)):
        raise QualificationFailure("AUDIT_PATH_ALIASED")
    control_path = trace / "harness-qualification-input.json"
    control_raw = _plain_file(control_path.absolute(), "QUALIFICATION_MANIFEST")
    control = parse_json(control_raw, "QUALIFICATION_MANIFEST")
    _exact_dict(control, {"schema", "free_run_id", "commitment_file", "commitment_key",
                          "bindings", "free_model_observations"}, "QUALIFICATION_MANIFEST")
    if control["schema"] != 1:
        raise QualificationFailure("QUALIFICATION_MANIFEST_SCHEMA")
    files = _binding_files(control)
    _verify_trace(trace, control, files)
    hashes = {name + "_sha256": digest(files[name][1]) for name in BINDINGS}
    receipt = {"schema": 1, "accepted": True, "proof_scope": "harness_only",
               "matrix_sha256": digest(canonical(matrix)),
               "qualification_manifest_sha256": digest(control_raw),
               "bindings": hashes,
               "free_run": {"id": control["free_run_id"],
                            "input_manifest_sha256": hashes["input_manifest_sha256"],
                            "terminal_verdict": "ACCEPTED"},
               "free_model_observations": control["free_model_observations"]}
    payload = canonical(receipt) + b"\n"
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    except OSError as exc:
        raise QualificationFailure("RECEIPT_WRITE_FAILED") from exc
    return receipt


def verify_receipt(path: Path) -> dict:
    raw = _plain_file(Path(path), "RECEIPT")
    value = parse_json(raw, "RECEIPT")
    _exact_dict(value, {"schema", "accepted", "proof_scope", "matrix_sha256",
                        "qualification_manifest_sha256", "bindings", "free_run",
                        "free_model_observations"}, "RECEIPT")
    if (value["schema"] != 1 or value["accepted"] is not True
            or value["proof_scope"] != "harness_only"
            or not HEX.fullmatch(value["matrix_sha256"])
            or not HEX.fullmatch(value["qualification_manifest_sha256"])):
        raise QualificationFailure("RECEIPT_INVALID")
    bindings = value["bindings"]
    if (not isinstance(bindings, dict)
            or set(bindings) != {name + "_sha256" for name in BINDINGS}
            or any(not isinstance(item, str) or not HEX.fullmatch(item)
                   for item in bindings.values())):
        raise QualificationFailure("RECEIPT_BINDINGS")
    free_run = value["free_run"]
    _exact_dict(free_run, {"id", "input_manifest_sha256", "terminal_verdict"},
                "RECEIPT_FREE_RUN")
    if (not isinstance(free_run["id"], str) or not free_run["id"]
            or free_run["input_manifest_sha256"] != bindings["input_manifest_sha256"]
            or free_run["terminal_verdict"] != "ACCEPTED"
            or value["free_model_observations"] != {"requested": "gpt-5.5",
                "sent": "gpt-5.5", "returned": ["gpt-5.5"]}):
        raise QualificationFailure("RECEIPT_FREE_RUN")
    return value


def _default_fixtures(root: Path):
    root.mkdir(mode=0o700)
    programs = root / "programs"; programs.mkdir()
    rows = []
    for case_id, (exit_code, failure) in EXPECTED.items():
        program = programs / (case_id + ".py")
        pause = "sleep 1" if case_id == "timeout" else ":"
        payload = json.dumps({"failure": failure, "id": case_id, "schema": 1},
                             sort_keys=True, separators=(",", ":"))
        body = ("#!/bin/sh\nset -C\numask 077\n"
                f"printf '%s\\n' '{case_id}' > \"$SHERLOCK_FAULT_RECEIPT\" || exit 125\n"
                f"{pause}\nprintf '%s\\n' '{payload}'\nexit {exit_code}\n")
        program.write_text(body); program.chmod(0o700)
        rows.append({"id": case_id, "program": "programs/" + program.name,
                     "expected": {"exit": exit_code, "failure": failure}})
    (root / "manifest.json").write_bytes(canonical({"schema": 1, "timeout_seconds": .25,
                                                      "faults": rows}) + b"\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    matrix_parser = sub.add_parser("matrix")
    matrix_parser.add_argument("--fixtures")
    matrix_parser.add_argument("--output", required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--trace", required=True)
    audit_parser.add_argument("--matrix", required=True)
    audit_parser.add_argument("--output", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "matrix":
            output = Path(args.output)
            if not output.is_absolute() or os.path.lexists(output):
                raise QualificationFailure("MATRIX_OUTPUT_INVALID")
            fixture = Path(args.fixtures) if args.fixtures else output.with_suffix(".fixtures")
            if not args.fixtures:
                _default_fixtures(fixture)
            row = run_fault_matrix(fixture)
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical(row) + b"\n")
        elif args.command == "audit":
            matrix = parse_json(_plain_file(Path(args.matrix), "MATRIX_FILE"), "MATRIX_FILE")
            row = audit_harness(Path(args.trace), matrix, Path(args.output))
        else:
            row = verify_receipt(Path(args.receipt))
        print(canonical(row).decode())
        return 0
    except (QualificationFailure, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
