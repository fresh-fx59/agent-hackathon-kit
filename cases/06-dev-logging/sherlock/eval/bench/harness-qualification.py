#!/usr/bin/env python3
"""Provider-free qualification of the Sherlock harness, never of a paid target."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
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
    "terminal_verdict", "seal_trace_tool", "terminal_seal", "gates",
    "replay", "report", "upstream_jsonl", "upstream_bodies",
    "corpus_tree", "launcher", "controller_commitments", "controller_key_id",
)
MAX_FILE = 16 * 1024 * 1024
MAX_PROCESS_OUTPUT = 64 * 1024
MATRIX_TIMEOUT = 0.35
SAFE_ENV = {"HOME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP",
            "TMP", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}


def _expected_adapter_tool(case_id):
    if case_id.endswith("_gate_failure"):
        name = {"report": "reportcheck.py", "citation": "citecheck.py",
                "state": "statecheck.py", "triage": "triagecheck.py"}[case_id.split("_")[0]]
        return SHERLOCK / "skills/v44/tools" / name
    if case_id in {"admission_refusal", "timeout"}:
        return HERE / "bench-controller.sh"
    if case_id == "artifact_tamper":
        return HERE / "run-manifest.py"
    if case_id == "lifecycle_transition":
        return SHERLOCK / "tools/tests/test_run_state.py"
    return SHERLOCK / "tools/tests/test_run_verdict.py"


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
    _exact_dict(matrix, {"schema", "verdict", "input_manifest_sha256",
                         "qualification_tool_sha256", "adapter_tools", "faults"}, "MATRIX")
    if matrix["schema"] != 2 or matrix["verdict"] != "clean":
        raise QualificationFailure("MATRIX_VERDICT")
    if matrix["qualification_tool_sha256"] != digest(Path(__file__).read_bytes()):
        raise QualificationFailure("MATRIX_TOOL_MISMATCH")
    if not isinstance(matrix["input_manifest_sha256"], str) or not HEX.fullmatch(matrix["input_manifest_sha256"]):
        raise QualificationFailure("MATRIX_MANIFEST_HASH")
    tools = matrix["adapter_tools"]
    if not isinstance(tools, dict) or not tools or any(
            not isinstance(name, str) or not isinstance(value, str) or not HEX.fullmatch(value)
            for name, value in tools.items()):
        raise QualificationFailure("MATRIX_ADAPTER_TOOLS")
    if set(tools) != {str(_expected_adapter_tool(name)) for name in EXPECTED}:
        raise QualificationFailure("MATRIX_ADAPTER_TOOLS")
    rows = matrix["faults"]
    if not isinstance(rows, list) or len(rows) != len(EXPECTED):
        raise QualificationFailure("MATRIX_ROWS")
    seen = set()
    for row in rows:
        _exact_dict(row, {"id", "expected", "observed", "passed", "raw_exit",
                          "stdout_sha256", "stderr_sha256", "tool", "tool_sha256"},
                    "MATRIX_ROW")
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
        if (isinstance(row["raw_exit"], bool) or not isinstance(row["raw_exit"], int)
                or not isinstance(row["tool"], str) or row["tool"] not in matrix["adapter_tools"]
                or any(not isinstance(row[name], str) or not HEX.fullmatch(row[name])
                       for name in ("stdout_sha256", "stderr_sha256", "tool_sha256"))
                or matrix["adapter_tools"].get(row["tool"]) != row["tool_sha256"]):
            raise QualificationFailure("MATRIX_ADAPTER_EVIDENCE")
        fixed_tool = _expected_adapter_tool(case_id)
        if (row["tool"] != str(fixed_tool)
                or row["tool_sha256"] != digest(_plain_file(fixed_tool.absolute(),
                                                            "ADAPTER_TOOL"))):
            raise QualificationFailure("MATRIX_ADAPTER_EVIDENCE")
        truth = observed == literal
        if row["passed"] is not truth or not truth:
            raise QualificationFailure("MATRIX_FAILED")
    if seen != set(EXPECTED):
        raise QualificationFailure("MATRIX_MISSING")


def _safe_child_env(extra=None):
    env = {name: os.environ[name] for name in SAFE_ENV if name in os.environ}
    env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin",
                                   "/usr/sbin", "/sbin"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def _run_bounded(command, *, cwd: Path, timeout=MATRIX_TIMEOUT, env=None):
    """Run one fixed adapter with bounded pipes and kill its entire process group."""
    process = subprocess.Popen(command, cwd=cwd, env=_safe_child_env(env),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    selector = selectors.DefaultSelector()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    timed_out = False
    overflow = False
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        for key, _ in selector.select(min(remaining, 0.05)):
            chunk = os.read(key.fd, min(8192, MAX_PROCESS_OUTPUT + 1))
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            bucket = streams[key.fileobj]
            bucket.extend(chunk)
            if len(bucket) > MAX_PROCESS_OUTPUT:
                overflow = True
                break
        if overflow:
            break
        if process.poll() is not None and not selector.get_map():
            break
    if timed_out or overflow:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=1)
    else:
        process.wait(timeout=1)
    for stream in streams:
        try: selector.unregister(stream)
        except (KeyError, ValueError): pass
        stream.close()
    if overflow:
        raise QualificationFailure("ADAPTER_OUTPUT_TOO_LARGE")
    return process.returncode, bytes(streams[process.stdout]), bytes(streams[process.stderr]), timed_out


def _matrix_input(root: Path):
    raw = _plain_file((root / "manifest.json").absolute(), "MATRIX_INPUT_MANIFEST")
    value = parse_json(raw, "MATRIX_INPUT_MANIFEST")
    if not isinstance(value, dict) or set(value) != {"schema", "inputs"} or value["schema"] != 2:
        raise QualificationFailure("MATRIX_INPUT_SCHEMA")
    if not isinstance(value["inputs"], list) or not value["inputs"]:
        raise QualificationFailure("MATRIX_INPUT_SCHEMA")
    seen = set()
    for item in value["inputs"]:
        _exact_dict(item, {"path", "sha256"}, "MATRIX_INPUT")
        relative = item["path"]
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or relative in seen
                or not isinstance(item["sha256"], str) or not HEX.fullmatch(item["sha256"])):
            raise QualificationFailure("MATRIX_INPUT_SCHEMA")
        seen.add(relative)
        data = _plain_file((root / relative).absolute(), "MATRIX_INPUT")
        if digest(data) != item["sha256"]:
            raise QualificationFailure("MATRIX_INPUT_HASH")
    return raw


def _adapter(case_id: str, root: Path, work: Path):
    gate = {
        "report_gate_failure": SHERLOCK / "skills/v44/tools/reportcheck.py",
        "citation_gate_failure": SHERLOCK / "skills/v44/tools/citecheck.py",
        "state_gate_failure": SHERLOCK / "skills/v44/tools/statecheck.py",
        "triage_gate_failure": SHERLOCK / "skills/v44/tools/triagecheck.py",
    }
    if case_id in gate:
        tool = gate[case_id]
        report = root / "invalid-report.md"
        corpus = work / "corpus"; corpus.mkdir()
        (corpus / "qualification.log").write_text("bounded fixture\n", encoding="utf-8")
        if case_id == "report_gate_failure":
            command = [sys.executable, str(tool), str(report)]
        elif case_id == "citation_gate_failure":
            command = [sys.executable, str(tool), "--corpus", str(corpus), str(report)]
        elif case_id == "state_gate_failure":
            command = [sys.executable, str(tool), "--corpus", str(corpus), "--report",
                       str(report), "--out", str(work / "state.json")]
        else:
            ledger = work / "worklist.tsv"; ledger.write_text("", encoding="utf-8")
            command = [sys.executable, str(tool), "--worklist", str(ledger),
                       "--corpus", str(corpus)]
        timeout = 1.0
    elif case_id == "admission_refusal":
        tool = HERE / "bench-controller.sh"
        command = [str(tool)]
        timeout = 1.0
    elif case_id == "artifact_tamper":
        tool = HERE / "run-manifest.py"
        broken = work / "tampered-trace"; broken.mkdir()
        authority = work / "authority"; authority.mkdir()
        records = authority / "commitments.jsonl"; records.write_bytes(b"")
        key = authority / "controller.key"; key.write_bytes(b"k" * 32); key.chmod(0o600)
        command = [sys.executable, str(tool), "verify", str(broken),
                   "--commitment-file", str(records), "--commitment-key", str(key), "--json"]
        timeout = 1.0
    elif case_id == "lifecycle_transition":
        tool = SHERLOCK / "tools/tests/test_run_state.py"
        command = [sys.executable, str(tool),
                   "RunStateTests.test_first_terminal_failure_survives_every_later_phase"]
        timeout = 5.0
    elif case_id == "timeout":
        tool = HERE / "bench-controller.sh"
        controllers = work / "controllers"; controllers.mkdir()
        runs = work / "runs"; runs.mkdir()
        command = [str(tool)]
        timeout = MATRIX_TIMEOUT
        env = {"SHERLOCK_CONTROLLER_ROOT": str(controllers), "BENCH_RUNS": str(runs),
               "SHERLOCK_FREE_TEST_COMMAND": "sleep 30", "SHERLOCK_HEALTH_COMMAND": "false",
               "SHERLOCK_TARGET_COMMAND": "false",
               "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS": "1",
               "SHERLOCK_BUDGET_MAX_REQUEST_BYTES": "1",
               "SHERLOCK_BUDGET_MAX_WALL_SECONDS": "1",
               "SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES": "1"}
        rc, stdout, stderr, timed_out = _run_bounded(command, cwd=work, timeout=timeout, env=env)
        return rc, stdout, stderr, timed_out, tool
    else:
        tool = SHERLOCK / "tools/tests/test_run_verdict.py"
        test = ("RunVerdictTests.test_missing_driver_receipt_does_not_collapse_into_attempt_layer"
                if case_id == "attempt_exit" else
                "RunVerdictTests.test_terminal_exit_layers_keep_first_failure_when_wrapper_finishes_later")
        command = [sys.executable, str(tool), test]
        timeout = 5.0
    rc, stdout, stderr, timed_out = _run_bounded(command, cwd=work, timeout=timeout)
    return rc, stdout, stderr, timed_out, tool


def _adapter_succeeded(case_id, rc, stdout, stderr, timed_out):
    """Project fixed adapter evidence into the matrix's public exit vocabulary."""
    if case_id == "timeout":
        return timed_out and rc != 0
    if timed_out:
        return False
    if case_id == "admission_refusal":
        return rc == 1 and stderr.strip() == b"MISSING_SHERLOCK_CONTROLLER_ROOT"
    if case_id == "artifact_tamper":
        return rc == 2 and b"E_MANIFEST_FILE_MISSING" in (stdout + stderr)
    if case_id in {"attempt_exit", "driver_exit", "wrapper_exit", "lifecycle_transition"}:
        return rc == 0 and b"OK" in stderr
    expected_raw = {"report_gate_failure": 1, "citation_gate_failure": 1,
                    "state_gate_failure": 3, "triage_gate_failure": 1}
    return rc == expected_raw.get(case_id) and bool(stdout + stderr)


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
    manifest_raw = _matrix_input(root)
    work_root = Path(tempfile.mkdtemp(prefix=".matrix-work-", dir=str(root)))
    observed_rows = []
    try:
        for case_id in sorted(EXPECTED):
            case_work = work_root / case_id; case_work.mkdir()
            rc, stdout, stderr, timed_out, tool = _adapter(case_id, root, case_work)
            expected = {"exit": EXPECTED[case_id][0], "failure": EXPECTED[case_id][1]}
            exercised = _adapter_succeeded(case_id, rc, stdout, stderr, timed_out)
            observed = expected if exercised else {"exit": rc, "failure": "ADAPTER_UNEXERCISED"}
            observed_rows.append({"id": case_id, "expected": expected,
                                  "observed": observed, "passed": observed == expected,
                                  "raw_exit": rc, "stdout_sha256": digest(stdout),
                                  "stderr_sha256": digest(stderr), "tool": str(tool),
                                  "tool_sha256": digest(_plain_file(tool.absolute(), "ADAPTER_TOOL"))})
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    tools = {row["tool"]: row["tool_sha256"] for row in observed_rows}
    return {"schema": 2, "verdict": "clean" if all(r["passed"] for r in observed_rows) else "failed",
            "input_manifest_sha256": digest(manifest_raw), "adapter_tools": tools,
            "qualification_tool_sha256": digest(Path(__file__).read_bytes()),
            "faults": observed_rows}


def _same_file(left, right):
    return (left.st_dev, left.st_ino, stat.S_IFMT(left.st_mode)) == (
        right.st_dev, right.st_ino, stat.S_IFMT(right.st_mode))


def _real_directory(path: Path, label: str):
    if not path.is_absolute():
        raise QualificationFailure(label + "_NOT_ABSOLUTE")
    try:
        info = path.lstat()
    except OSError as exc:
        raise QualificationFailure(label + "_MISSING") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or path.resolve(strict=True) != path):
        raise QualificationFailure(label + "_ALIASED")
    return info


def _tree_bytes(root: Path, label: str, *, gzip_required=False):
    _real_directory(root, label)
    rows = []
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise QualificationFailure(label + "_ALIASED")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise QualificationFailure(label + "_ALIASED")
        raw = _plain_file(path.absolute(), label)
        total += len(raw)
        if len(rows) >= 4096 or total > 128 * 1024 * 1024:
            raise QualificationFailure(label + "_TOO_LARGE")
        if gzip_required and not raw.startswith(b"\x1f\x8b"):
            raise QualificationFailure(label + "_NOT_GZIP")
        rows.append({"path": relative, "bytes": len(raw), "sha256": digest(raw)})
    if not rows:
        raise QualificationFailure(label + "_EMPTY")
    return canonical({"schema": 1, "files": rows, "bytes": total}) + b"\n"


def _artifact_rows(trace: Path):
    """Rebuild the controller's terminal inventory without following names."""
    rows = []
    entries = total = inventory_bytes = 0
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)

    def visit(directory_fd, prefix="", depth=0):
        nonlocal entries, total, inventory_bytes
        if depth > 64:
            raise QualificationFailure("TERMINAL_SEAL_INVALID")
        try:
            with os.scandir(directory_fd) as listing:
                names = sorted(entry.name for entry in listing
                               if prefix or entry.name not in {"trace-manifest.json", "sealed"})
        except OSError as exc:
            raise QualificationFailure("TERMINAL_SEAL_INVALID") from exc
        for name in names:
            entries += 1
            relative = prefix + name
            if entries > 4096 or len(relative.encode("utf-8")) > 1024:
                raise QualificationFailure("TERMINAL_SEAL_INVALID")
            try:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(before.st_mode):
                    child_fd = os.open(name, os.O_RDONLY | directory_flag | nofollow,
                                       dir_fd=directory_fd)
                    try:
                        if not _same_file(before, os.fstat(child_fd)):
                            raise QualificationFailure("TERMINAL_SEAL_INVALID")
                        visit(child_fd, relative + "/", depth + 1)
                        rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if not _same_file(rebound, os.fstat(child_fd)):
                            raise QualificationFailure("TERMINAL_SEAL_INVALID")
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise QualificationFailure("TERMINAL_SEAL_INVALID")
                file_fd = os.open(name, os.O_RDONLY | nofollow, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if not _same_file(before, opened) or opened.st_nlink != 1:
                        raise QualificationFailure("TERMINAL_SEAL_INVALID")
                    hasher = hashlib.sha256(); length = 0
                    while True:
                        block = os.read(file_fd, 1024 * 1024)
                        if not block:
                            break
                        length += len(block); total += len(block)
                        if length > MAX_FILE or total > 128 * 1024 * 1024:
                            raise QualificationFailure("TERMINAL_SEAL_INVALID")
                        hasher.update(block)
                    finished = os.fstat(file_fd)
                    rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if (not _same_file(opened, finished) or not _same_file(finished, rebound)
                            or finished.st_size != length or finished.st_nlink != 1):
                        raise QualificationFailure("TERMINAL_SEAL_INVALID")
                finally:
                    os.close(file_fd)
                row = {"path": relative, "bytes": length, "sha256": hasher.hexdigest()}
                inventory_bytes += len(canonical(row)) + 1
                if inventory_bytes > 768 * 1024:
                    raise QualificationFailure("TERMINAL_SEAL_INVALID")
                rows.append(row)
            except OSError as exc:
                raise QualificationFailure("TERMINAL_SEAL_INVALID") from exc

    try:
        root_fd = os.open(trace, os.O_RDONLY | directory_flag | nofollow)
    except OSError as exc:
        raise QualificationFailure("TERMINAL_SEAL_INVALID") from exc
    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
    return rows


def _authority(trace: Path):
    if trace.parent.name != "runs":
        raise QualificationFailure("AUTHORITY_LAYOUT")
    root = trace.parent.parent
    _real_directory(root, "QUALIFICATION_ROOT")
    try:
        resolved_trace = trace.resolve(strict=True)
    except OSError as exc:
        raise QualificationFailure("AUTHORITY_LAYOUT") from exc
    if resolved_trace != trace:
        raise QualificationFailure("AUTHORITY_LAYOUT")
    controller = root / "controller"
    _real_directory(controller, "CONTROLLER_ROOT")
    commitment = controller / "records/run-commitments.jsonl"
    key_path = controller / "keys/controller.key"
    key = _plain_file(key_path.absolute(), "CONTROLLER_KEY")
    if len(key) != 32 or stat.S_IMODE(key_path.stat().st_mode) != 0o600:
        raise QualificationFailure("CONTROLLER_KEY_INVALID")
    return root, commitment, key_path, key


def _terminal_seal(trace: Path, key: bytes, manifest_sha: str, run_id: str):
    raw = _plain_file((trace / "trace-manifest.json").absolute(), "TERMINAL_SEAL")
    row = parse_json(raw, "TERMINAL_SEAL")
    supplied = row.pop("hmac_sha256", None) if isinstance(row, dict) else None
    if (not isinstance(row, dict) or set(row) != {"schema", "run_tag",
            "child_manifest_sha256", "artifacts", "key_id"}
            or row.get("schema") != 1 or row.get("run_tag") != run_id
            or row.get("child_manifest_sha256") != manifest_sha
            or row.get("key_id") != digest(key) or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, hmac.new(key, canonical(row), hashlib.sha256).hexdigest())
            or row.get("artifacts") != _artifact_rows(trace)):
        raise QualificationFailure("TERMINAL_SEAL_INVALID")
    marker = trace / "sealed"
    marker_raw = _plain_file(marker.absolute(), "TERMINAL_SEAL_MARKER")
    if marker_raw:
        raise QualificationFailure("TERMINAL_SEAL_MARKER_INVALID")
    return raw


def _verify_trace(trace: Path, control):
    if not isinstance(control, dict):
        raise QualificationFailure("QUALIFICATION_MANIFEST_SCHEMA")
    run_id = control.get("free_run_id")
    observations = control.get("free_model_observations")
    if not isinstance(run_id, str) or not run_id or observations != {
            "requested": "gpt-5.5", "sent": "gpt-5.5", "returned": ["gpt-5.5"]}:
        raise QualificationFailure("FREE_IDENTITY_MISMATCH")
    root, commitment, key_path, key = _authority(trace)
    if control.get("trace") != str(trace) or control.get("matrix") != str(root / "fault-matrix.json"):
        raise QualificationFailure("AUTHORITY_SCHEMA")
    manifest_cmd = [sys.executable, str(HERE / "run-manifest.py"), "verify", str(trace),
                    "--commitment-file", str(commitment), "--commitment-key", str(key_path), "--json"]
    verifier_env = _safe_child_env()
    try:
        checked = subprocess.run(manifest_cmd, text=True, capture_output=True, timeout=30,
                                 env=verifier_env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("RUN_MANIFEST_INVALID") from exc
    if checked.returncode != 0:
        raise QualificationFailure("RUN_MANIFEST_INVALID")
    try:
        seal_checked = subprocess.run([sys.executable, str(HERE / "seal-trace.py"), "audit",
                                       "--trace", str(trace)], text=True, capture_output=True,
                                      timeout=30, env=verifier_env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("SEAL_TRACE_INVALID") from exc
    if seal_checked.returncode != 0:
        raise QualificationFailure("SEAL_TRACE_INVALID")
    verdict_cmd = [sys.executable, str(HERE / "run-verdict.py"), str(trace),
                   "--commitment-file", str(commitment), "--commitment-key", str(key_path), "--json"]
    try:
        checked = subprocess.run(verdict_cmd, text=True, capture_output=True, timeout=30,
                                 env=verifier_env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("TERMINAL_VERDICT_STALE") from exc
    if checked.returncode != 0:
        raise QualificationFailure("TERMINAL_VERDICT_STALE")
    terminal_raw = checked.stdout.encode()
    verdict = parse_json(terminal_raw, "TERMINAL_VERDICT")
    if (verdict.get("run_tag") != run_id or verdict.get("phase") != "ACCEPTED"
            or verdict.get("successful") is not True or verdict.get("report_correct") is not True
            or verdict.get("authenticated") is not True):
        raise QualificationFailure("TERMINAL_VERDICT_INVALID")
    input_manifest_raw = _plain_file((trace / "run-manifest.json").absolute(), "INPUT_MANIFEST")
    manifest = parse_json(input_manifest_raw, "INPUT_MANIFEST")
    profile = manifest.get("target") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("run_tag") != run_id
            or manifest.get("arm") != "v44"
            or not isinstance(profile, dict) or profile.get("provider") != "cliproxyapi"
            or profile.get("lane") != "subscription" or profile.get("requested_model") != "gpt-5.5"
            or profile.get("expected_returned_identity") != "gpt-5.5"):
        raise QualificationFailure("INPUT_MANIFEST_IDENTITY")
    identity = manifest.get("input_identity")
    if not isinstance(identity, dict):
        raise QualificationFailure("BOUND_INPUT_IDENTITY")
    implementation_raw = _plain_file((root / "implementation-commit.txt").absolute(),
                                     "IMPLEMENTATION_COMMIT")
    arm_raw = _plain_file((root / "arm.json").absolute(), "ARM")
    arm = parse_json(arm_raw, "ARM")
    settings_raw = _plain_file((trace / "corporate-settings.json").absolute(), "SETTINGS")
    tool_schema_raw = _plain_file((root / "tool-schema.json").absolute(), "TOOL_SCHEMA")
    prompt_raw = _plain_file((root / "prompt.txt").absolute(), "PROMPT")
    skill_raw = _plain_file((SHERLOCK / "skills/v44/SKILL.md").absolute(), "SKILL_V44")
    commit = identity.get("arm_commit")
    if (not isinstance(commit, str) or implementation_raw != (commit + "\n").encode()
            or arm != {"schema": 1, "arm": identity.get("arm"),
                       "commit": identity.get("arm_commit"), "tree": identity.get("arm_tree")}
            or identity.get("runner_sha256") != digest(
                _plain_file((HERE / "run-bench.sh").absolute(), "RUNNER"))
            or identity.get("settings_sha256") != digest(settings_raw)
            or identity.get("tool_schema_sha256") != digest(tool_schema_raw)
            or identity.get("system_prompt_sha256") != digest(prompt_raw)
            or identity.get("skill_sha256") != digest(skill_raw)):
        raise QualificationFailure("BOUND_INPUT_IDENTITY")
    tests_raw = _plain_file((root / "provider-free-tests.json").absolute(), "TEST_MANIFEST")
    tests = parse_json(tests_raw, "TEST_MANIFEST")
    if (not isinstance(tests, dict) or tests.get("schema") != 1
            or tests.get("provider_free") is not True or tests.get("failed") != 0):
        raise QualificationFailure("TEST_MANIFEST_INVALID")
    suites = tests.get("suites")
    expected_suites = {"run-manifest", "run-state", "run-verdict"}
    if (not isinstance(suites, list) or len(suites) != len(expected_suites)
            or {item.get("name") for item in suites if isinstance(item, dict)} != expected_suites
            or any(
            not isinstance(item, dict) or set(item) != {"name", "path", "sha256", "exit_code"}
            or item["exit_code"] != 0 or not isinstance(item["path"], str)
            or Path(item["path"]) != root / "suite-results" / (item["name"] + ".out")
            or not isinstance(item["sha256"], str) or not HEX.fullmatch(item["sha256"])
            or digest(_plain_file(Path(item["path"]), "SUITE_OUTPUT")) != item["sha256"]
            for item in suites)):
        raise QualificationFailure("TEST_MANIFEST_INVALID")
    gates_raw = _plain_file((trace / "gates.json").absolute(), "GATES")
    gates = parse_json(gates_raw, "GATES")
    gate_rows = gates.get("gates") if isinstance(gates, dict) else None
    if (not isinstance(gates, dict) or gates.get("verdict") != "clean"
            or not isinstance(gate_rows, dict)
            or set(gate_rows) != {"reportcheck", "citecheck", "statecheck", "triagecheck"}
            or any(not isinstance(gate_rows.get(name), dict)
            or gate_rows[name].get("exit_code") != 0
            for name in ("reportcheck", "citecheck", "statecheck", "triagecheck"))):
        raise QualificationFailure("GATE_RESULT_INVALID")
    upstream = trace / "upstream-completed.jsonl"
    raw = _plain_file(upstream.absolute(), "UPSTREAM_COMPLETED")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise QualificationFailure("UPSTREAM_IDENTITY") from exc
    rows = [parse_json((line + "\n").encode(), "UPSTREAM_ROW") for line in lines]
    if not rows or any(not isinstance(row, dict) or row.get("run_tag") != run_id
                       or row.get("requested_model") != "gpt-5.5"
                       or row.get("sent_model") != "gpt-5.5" or row.get("returned_model") != "gpt-5.5"
                       or row.get("status") != 200 for row in rows):
        raise QualificationFailure("UPSTREAM_IDENTITY")
    bodies_raw = _tree_bytes(trace / "upstream-bodies", "UPSTREAM_BODIES", gzip_required=True)
    corpus_raw = _tree_bytes(trace / "staged-corpus", "CORPUS_TREE")
    qwen_value = control.get("qwen_binary")
    qwen = Path(qwen_value) if isinstance(qwen_value, str) else Path("")
    target_cli = manifest.get("artifacts", {}).get("target_cli", {}).get("path")
    try:
        qwen_resolved = qwen.resolve(strict=True)
    except OSError as exc:
        raise QualificationFailure("QWEN_IDENTITY") from exc
    if (not qwen.is_absolute() or not os.access(qwen, os.X_OK) or qwen_resolved != qwen
            or target_cli != str(qwen)):
        raise QualificationFailure("QWEN_IDENTITY")
    qwen_raw = _plain_file(qwen, "QWEN_BINARY")
    version_raw = _plain_file((root / "qwen-version.txt").absolute(), "QWEN_VERSION")
    try:
        version = subprocess.run([str(qwen), "--version"], capture_output=True, timeout=10,
                                 env=verifier_env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("QWEN_IDENTITY") from exc
    if version.returncode or version.stdout != version_raw:
        raise QualificationFailure("QWEN_IDENTITY")
    terminal_seal_raw = _terminal_seal(trace, key, manifest["manifest_sha256"], run_id)
    files = {
        "implementation_commit": (root / "implementation-commit.txt", implementation_raw),
        "implementation_dirty": (root / "implementation-dirty.txt", _plain_file((root / "implementation-dirty.txt").absolute(), "IMPLEMENTATION_DIRTY")),
        "runner": (HERE / "run-bench.sh", _plain_file((HERE / "run-bench.sh").absolute(), "RUNNER")),
        "driver": (SHERLOCK / "measure/interactive-drive.py", _plain_file((SHERLOCK / "measure/interactive-drive.py").absolute(), "DRIVER")),
        "proxy": (SHERLOCK / "measure/upstream-log-proxy.py", _plain_file((SHERLOCK / "measure/upstream-log-proxy.py").absolute(), "PROXY")),
        "run_manifest_tool": (HERE / "run-manifest.py", _plain_file((HERE / "run-manifest.py").absolute(), "RUN_MANIFEST_TOOL")),
        "run_verdict_tool": (HERE / "run-verdict.py", _plain_file((HERE / "run-verdict.py").absolute(), "RUN_VERDICT_TOOL")),
        "seal_trace_tool": (HERE / "seal-trace.py", _plain_file((HERE / "seal-trace.py").absolute(), "SEAL_TRACE_TOOL")),
        "test_manifest": (root / "provider-free-tests.json", tests_raw),
        "qwen_binary": (qwen, qwen_raw), "qwen_version": (root / "qwen-version.txt", version_raw),
        "arm": (root / "arm.json", arm_raw),
        "skill_v44": (SHERLOCK / "skills/v44/SKILL.md", skill_raw),
        "report_contract": (SHERLOCK / "skills/v44/reference/report-contract.corporate.json", _plain_file((SHERLOCK / "skills/v44/reference/report-contract.corporate.json").absolute(), "REPORT_CONTRACT")),
        "settings": (trace / "corporate-settings.json", settings_raw),
        "tool_schema": (root / "tool-schema.json", tool_schema_raw),
        "input_manifest": (trace / "run-manifest.json", input_manifest_raw),
        "terminal_verdict": (root / "terminal-verdict.json", terminal_raw),
        "terminal_seal": (trace / "trace-manifest.json", terminal_seal_raw),
        "gates": (trace / "gates.json", gates_raw),
        "replay": (trace / "replay.sh", _plain_file((trace / "replay.sh").absolute(), "REPLAY")),
        "report": (trace / "work/report.md", _plain_file((trace / "work/report.md").absolute(), "REPORT")),
        "upstream_jsonl": (upstream, raw), "upstream_bodies": (trace / "upstream-bodies", bodies_raw),
        "corpus_tree": (trace / "staged-corpus", corpus_raw),
        "launcher": (HERE / "run-harness-qualification.sh", _plain_file((HERE / "run-harness-qualification.sh").absolute(), "LAUNCHER")),
        "controller_commitments": (commitment, _plain_file(commitment.absolute(),
                                                            "CONTROLLER_COMMITMENTS")),
        "controller_key_id": (key_path, (digest(key) + "\n").encode()),
    }
    gate_tools = {"report": "reportcheck.py", "citation": "citecheck.py",
                  "state": "statecheck.py", "triage": "triagecheck.py"}
    for prefix, filename in gate_tools.items():
        path = SHERLOCK / "skills/v44/tools" / filename
        files[prefix + "_gate_program"] = (path, _plain_file(path.absolute(), prefix.upper() + "_GATE_PROGRAM"))
        files[prefix + "_gate_result"] = (trace / "gates.json", gates_raw)
    if set(files) != set(BINDINGS):
        raise QualificationFailure("BINDINGS_SCHEMA")
    return root, files, terminal_raw


def audit_harness(trace: Path, matrix: dict, output: Path) -> dict:
    _validate_matrix(matrix)
    trace = Path(trace)
    output = Path(output)
    if not trace.is_absolute() or not output.is_absolute():
        raise QualificationFailure("AUDIT_PATH_INVALID")
    root, _, _, key = _authority(trace)
    if output != root / "harness-acceptance.json" or os.path.lexists(output):
        raise QualificationFailure("AUDIT_PATH_INVALID")
    parent_before = _real_directory(root, "RECEIPT_PARENT")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(root, flags)
    except OSError as exc:
        raise QualificationFailure("AUDIT_PATH_ALIASED") from exc
    marker_name = output.name + ".accepted"
    temporary = "." + output.name + ".pending-" + digest(os.urandom(32))[:16]
    control_path = root / "harness-qualification-input.json"
    control_raw = _plain_file(control_path.absolute(), "QUALIFICATION_MANIFEST")
    control = parse_json(control_raw, "QUALIFICATION_MANIFEST")
    _exact_dict(control, {"schema", "free_run_id", "trace", "matrix", "qwen_binary",
                          "free_model_observations"}, "QUALIFICATION_MANIFEST")
    if (control["schema"] != 2 or any(not isinstance(control[name], str) or not control[name]
            for name in ("free_run_id", "trace", "matrix", "qwen_binary"))):
        raise QualificationFailure("QUALIFICATION_MANIFEST_SCHEMA")
    preserved_matrix = parse_json(
        _plain_file((root / "fault-matrix.json").absolute(), "MATRIX_FILE"), "MATRIX_FILE")
    if canonical(preserved_matrix) != canonical(matrix):
        raise QualificationFailure("PRESERVED_MATRIX_MISMATCH")
    try:
        verified_root, files, _ = _verify_trace(trace, control)
        if verified_root != root or not _same_file(parent_before, os.fstat(parent_fd)):
            raise QualificationFailure("RECEIPT_PARENT_CHANGED")
        try:
            current = root.lstat()
        except OSError as exc:
            raise QualificationFailure("RECEIPT_PARENT_CHANGED") from exc
        if not _same_file(parent_before, current) or root.resolve(strict=True) != root:
            raise QualificationFailure("RECEIPT_PARENT_CHANGED")
        hashes = {name + "_sha256": digest(files[name][1]) for name in BINDINGS}
        receipt = {"schema": 2, "accepted": True, "proof_scope": "harness_only",
               "matrix_sha256": digest(canonical(matrix)),
               "qualification_manifest_sha256": digest(control_raw),
               "trace": trace.name,
               "bindings": hashes,
               "free_run": {"id": control["free_run_id"],
                            "input_manifest_sha256": hashes["input_manifest_sha256"],
                            "terminal_verdict": "ACCEPTED"},
               "free_model_observations": control["free_model_observations"]}
        payload = canonical(receipt) + b"\n"
        marker = {"schema": 1, "receipt_sha256": digest(payload),
                  "matrix_sha256": receipt["matrix_sha256"], "trace": trace.name,
                  "controller_key_id": digest(key)}
        marker["hmac_sha256"] = hmac.new(key, canonical(marker), hashlib.sha256).hexdigest()
        marker_payload = canonical(marker) + b"\n"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                     dir_fd=parent_fd)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.link(temporary, output.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                follow_symlinks=False)
        os.unlink(temporary, dir_fd=parent_fd)
        os.fsync(parent_fd)
        marker_tmp = temporary + ".marker"
        fd = os.open(marker_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                     dir_fd=parent_fd)
        with os.fdopen(fd, "wb") as handle:
            handle.write(marker_payload); handle.flush(); os.fsync(handle.fileno())
        os.link(marker_tmp, marker_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                follow_symlinks=False)
        os.unlink(marker_tmp, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return receipt
    except QualificationFailure:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("RECEIPT_WRITE_FAILED") from exc
    finally:
        if sys.exc_info()[0] is not None:
            for name in (temporary, temporary + ".marker", output.name, marker_name):
                try: os.unlink(name, dir_fd=parent_fd)
                except FileNotFoundError: pass
                except OSError: pass
            try: os.fsync(parent_fd)
            except OSError: pass
        os.close(parent_fd)


def verify_receipt(path: Path) -> dict:
    path = Path(path)
    raw = _plain_file(path, "RECEIPT")
    value = parse_json(raw, "RECEIPT")
    _exact_dict(value, {"schema", "accepted", "proof_scope", "matrix_sha256",
                        "qualification_manifest_sha256", "trace", "bindings", "free_run",
                        "free_model_observations"}, "RECEIPT")
    if (value["schema"] != 2 or value["accepted"] is not True
            or value["proof_scope"] != "harness_only"
            or not isinstance(value["matrix_sha256"], str)
            or not HEX.fullmatch(value["matrix_sha256"])
            or not isinstance(value["qualification_manifest_sha256"], str)
            or not HEX.fullmatch(value["qualification_manifest_sha256"])
            or not isinstance(value["trace"], str) or not value["trace"]
            or Path(value["trace"]).name != value["trace"]):
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
    root = path.parent
    _real_directory(root, "RECEIPT_PARENT")
    if path != root / "harness-acceptance.json":
        raise QualificationFailure("PRESERVED_AUDIT_REQUIRED")
    trace = root / "runs" / value["trace"]
    authority_root, _, _, key = _authority(trace)
    if authority_root != root:
        raise QualificationFailure("AUTHORITY_LAYOUT")
    marker_raw = _plain_file(Path(str(path) + ".accepted"), "RECEIPT_MARKER")
    marker = parse_json(marker_raw, "RECEIPT_MARKER")
    supplied = marker.pop("hmac_sha256", None) if isinstance(marker, dict) else None
    if (not isinstance(marker, dict) or set(marker) != {"schema", "receipt_sha256",
            "matrix_sha256", "trace", "controller_key_id"} or marker.get("schema") != 1
            or marker.get("receipt_sha256") != digest(raw)
            or marker.get("matrix_sha256") != value["matrix_sha256"]
            or marker.get("trace") != value["trace"]
            or marker.get("controller_key_id") != digest(key)
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied,
                hmac.new(key, canonical(marker), hashlib.sha256).hexdigest())):
        raise QualificationFailure("RECEIPT_MARKER_INVALID")
    matrix = parse_json(_plain_file((root / "fault-matrix.json").absolute(), "MATRIX_FILE"),
                        "MATRIX_FILE")
    _validate_matrix(matrix)
    if digest(canonical(matrix)) != value["matrix_sha256"]:
        raise QualificationFailure("PRESERVED_MATRIX_MISMATCH")
    control_raw = _plain_file((root / "harness-qualification-input.json").absolute(),
                              "QUALIFICATION_MANIFEST")
    if digest(control_raw) != value["qualification_manifest_sha256"]:
        raise QualificationFailure("PRESERVED_AUDIT_MISMATCH")
    control = parse_json(control_raw, "QUALIFICATION_MANIFEST")
    _, files, _ = _verify_trace(trace, control)
    expected = {name + "_sha256": digest(files[name][1]) for name in BINDINGS}
    if expected != bindings:
        raise QualificationFailure("PRESERVED_BINDINGS_MISMATCH")
    return value


def _default_fixtures(root: Path):
    root.mkdir(mode=0o700)
    corpus = root / "corpus.log"; corpus.write_bytes(b"2026-09-04T00:00:00Z fixture ok\n")
    report = root / "invalid-report.md"; report.write_bytes(b"not a Sherlock report\n")
    rows = [{"path": path.name, "sha256": digest(path.read_bytes())}
            for path in (corpus, report)]
    (root / "manifest.json").write_bytes(canonical({"schema": 2, "inputs": rows}) + b"\n")


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
