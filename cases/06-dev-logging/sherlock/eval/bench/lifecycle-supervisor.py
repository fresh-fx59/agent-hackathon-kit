#!/usr/bin/env python3
"""Fail-closed observation and generated-evidence lifecycle for controlled runs."""

import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import signal
import stat
import subprocess
import sys
import time


SCHEMA = 1
MAX_OBSERVATION_AGE_NS = 60_000_000_000
MAX_HOOK_INPUT_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024 * 1024
CANONICAL_WORK_FILES = (
    "work/worklist.tsv",
    "work/worklist.manifest.json",
    "work/rules.tsv",
)
LAUNCH_FIELDS = {
    "schema", "action", "execution_mode", "run_tag", "run_nonce",
    "predecessor_nonce", "launched_at", "observer_dir",
    "observer_identity_sha256", "observer_capability_sha256", "boot_id",
    "controller_pid", "controller_start_ticks", "controller_pgid",
    "target_profile_sha256", "run_budget_sha256", "input_package_sha256",
    "settings_sha256", "lifecycle_helper_sha256", "authorization_sha256",
    "manifest_sha256", "package_version", "package_sha256", "workspace_dir",
    "key_id", "hmac_sha256",
}
RECEIPT_FIELDS = {
    "schema", "run_tag", "run_nonce", "completed_at", "launch_sha256",
    "observer_dir", "observer_identity_sha256", "lifecycle_helper_sha256",
    "guardian_pid", "guardian_start_ticks", "guardian_exit_code",
    "last_accepted_observation_sha256", "registry_sha256", "pairs_sha256",
    "expectations_sha256", "batch_tools_sha256", "rejections_sha256", "hook_starts_sha256",
    "hook_events_sha256", "post_tool_batch_events_sha256",
    "subagent_state_sha256", "subagent_events_sha256",
    "nested_dispatches_sha256", "root_boundary_events_sha256",
    "session_start_events_sha256", "subagent_count", "nested_dispatch_count",
    "root_boundary_count", "session_start_count",
    "expected_tool_count", "batched_tool_count", "completed_tool_count",
    "rejected_tool_count", "fault_sha256",
    "fault_reason", "guardian_events_sha256", "status", "key_id",
    "hmac_sha256",
}


class LifecycleFault(RuntimeError):
    def __init__(self, reason, detail):
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _strict_json(raw, *, require_object=True):
    def pairs(items):
        row = {}
        for key, value in items:
            if key in row:
                raise ValueError("duplicate JSON key: %s" % key)
            row[key] = value
        return row

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    if require_object and not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_temp(parent, data, mode=0o600):
    for _ in range(64):
        temp = parent / (".lifecycle.%s.tmp" % secrets.token_hex(16))
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), mode)
            break
        except FileExistsError:
            continue
    else:
        raise OSError("cannot allocate lifecycle temporary file")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return temp
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _atomic_replace(path, data, mode=0o600):
    temp = _write_temp(path.parent, data, mode)
    try:
        os.replace(temp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _atomic_create(path, data, mode=0o600):
    temp = _write_temp(path.parent, data, mode)
    try:
        try:
            os.link(temp, path, follow_symlinks=False)
        except FileExistsError:
            raise
        _fsync_dir(path.parent)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _append(path, row):
    data = _canonical(row) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise OSError("short lifecycle journal write")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _locked(observer):
    path = Path(observer) / "segment.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_regular(path, limit=None):
    path = Path(path)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise LifecycleFault("UNSAFE_FILE", str(path))
    if limit is not None and before.st_size > limit:
        raise LifecycleFault("FILE_TOO_LARGE", str(path))
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if limit is not None and total > limit:
                raise LifecycleFault("FILE_TOO_LARGE", str(path))
        finished = os.fstat(fd)
    finally:
        os.close(fd)
    after = os.lstat(path)

    def identity(value):
        return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode),
                value.st_size, value.st_mtime_ns, value.st_ctime_ns)

    if identity(before) != identity(opened) or identity(opened) != identity(finished) \
            or identity(finished) != identity(after) or total != after.st_size:
        raise LifecycleFault("FILE_CHANGED_WHILE_READ", str(path))
    return b"".join(chunks)


def _load_json(path, *, absent=None):
    try:
        return _strict_json(_read_regular(path, MAX_HOOK_INPUT_BYTES))
    except FileNotFoundError:
        return absent


def _validate_nonce(value):
    if not isinstance(value, str) or len(value) < 16 or len(value) > 256 \
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in value):
        raise LifecycleFault("INVALID_NONCE", repr(value))
    return value


def _identity(observer):
    row = _load_json(Path(observer) / "identity.json")
    if not isinstance(row, dict) or row.get("schema") != SCHEMA:
        raise LifecycleFault("INVALID_SEGMENT", "identity")
    return row


def _sign_record(observer, row):
    observer = Path(observer)
    identity = _identity(observer)
    key = _read_regular(observer / "observation.key", 32)
    if len(key) != 32 or not hmac.compare_digest(
            sha256(key), identity.get("capability_sha256", "")):
        raise LifecycleFault("INVALID_CAPABILITY", "observer capability")
    signed = dict(row)
    signed["key_id"] = sha256(key)
    signed.pop("hmac_sha256", None)
    signed["hmac_sha256"] = hmac.new(
        key, _canonical(signed), hashlib.sha256).hexdigest()
    return signed


def verify_signed_record(observer, row):
    if not isinstance(row, dict) or not isinstance(row.get("hmac_sha256"), str):
        raise LifecycleFault("INVALID_SIGNED_RECORD", "shape")
    observed = row["hmac_sha256"]
    unsigned = dict(row)
    unsigned.pop("hmac_sha256", None)
    expected = _sign_record(observer, unsigned)
    if (row.get("key_id") != expected.get("key_id")
            or not hmac.compare_digest(observed, expected["hmac_sha256"])):
        raise LifecycleFault("INVALID_SIGNED_RECORD", "signature")
    return row


def _hex_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


def publish_launch(observer, trace, run_nonce, boot_id, *, action, run_tag,
                   predecessor_nonce, package_version, package_sha256,
                   controller_pid, controller_start_ticks, controller_pgid,
                   workspace_dir,
                   target_profile_sha256, run_budget_sha256,
                   input_package_sha256, settings_sha256,
                   lifecycle_helper_sha256, authorization_sha256,
                   manifest_sha256):
    """Publish the controller-owned, sealed lifecycle authority before Qwen."""
    observer, trace = Path(observer), Path(trace)
    if observer.parent.resolve() != trace.resolve():
        raise LifecycleFault("INVALID_LAUNCH", "observer must be under trace")
    identity = _identity(observer)
    if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
        raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "launch")
    digests = (package_sha256, target_profile_sha256, run_budget_sha256,
               input_package_sha256, settings_sha256,
               lifecycle_helper_sha256, authorization_sha256,
               manifest_sha256)
    workspace_dir = Path(workspace_dir)
    if (not workspace_dir.is_absolute() or not workspace_dir.is_dir()
            or stat.S_IMODE(os.lstat(workspace_dir).st_mode) != 0o700
            or workspace_dir.resolve().parent != trace.resolve()):
        raise LifecycleFault("INVALID_LAUNCH", "workspace contract")
    if (not all(_hex_digest(value) for value in digests)
            or not isinstance(action, str) or not action
            or not isinstance(run_tag, str) or not run_tag
            or not isinstance(package_version, str) or not package_version
            or type(controller_pid) is not int or controller_pid <= 0
            or type(controller_pgid) is not int or controller_pgid <= 0
            or not isinstance(controller_start_ticks, str)
            or not controller_start_ticks):
        raise LifecycleFault("INVALID_LAUNCH", "field contract")
    row = {
        "schema": SCHEMA, "action": action,
        "execution_mode": "operator_monitored", "run_tag": run_tag,
        "run_nonce": run_nonce, "predecessor_nonce": predecessor_nonce,
        "launched_at": _wall_now(), "observer_dir": str(observer.resolve()),
        "observer_identity_sha256": sha256(
            _read_regular(observer / "identity.json", MAX_HOOK_INPUT_BYTES)),
        "observer_capability_sha256": identity["capability_sha256"],
        "boot_id": boot_id, "controller_pid": controller_pid,
        "controller_start_ticks": controller_start_ticks,
        "controller_pgid": controller_pgid,
        "workspace_dir": str(workspace_dir.resolve()),
        "target_profile_sha256": target_profile_sha256,
        "run_budget_sha256": run_budget_sha256,
        "input_package_sha256": input_package_sha256,
        "settings_sha256": settings_sha256,
        "lifecycle_helper_sha256": lifecycle_helper_sha256,
        "authorization_sha256": authorization_sha256,
        "manifest_sha256": manifest_sha256,
        "package_version": package_version, "package_sha256": package_sha256,
    }
    signed = _sign_record(observer, row)
    if set(signed) != LAUNCH_FIELDS:
        raise LifecycleFault("INVALID_LAUNCH", "internal schema")
    _atomic_create(trace / "lifecycle-launch.json", _canonical(signed) + b"\n")
    return signed


def init_segment(trace, run_nonce, boot_id, *, capability=None,
                 predecessor_nonce=None):
    trace = Path(trace)
    _validate_nonce(run_nonce)
    if not isinstance(boot_id, str) or not boot_id:
        raise LifecycleFault("INVALID_BOOT_ID", repr(boot_id))
    if trace.is_symlink() or not trace.is_dir():
        raise LifecycleFault("INVALID_TRACE", str(trace))
    observer = trace / ("observer-%s" % run_nonce)
    os.mkdir(observer, 0o700)
    os.chmod(observer, 0o700)
    (observer / "objects").mkdir(mode=0o700)
    key = capability if capability is not None else secrets.token_bytes(32)
    if not isinstance(key, bytes) or len(key) != 32:
        raise LifecycleFault("INVALID_CAPABILITY", "must be 32 bytes")
    _atomic_create(observer / "observation.key", key, 0o600)
    row = {"schema": SCHEMA, "run_nonce": run_nonce, "boot_id": boot_id,
           "created_at": _wall_now(), "capability_sha256": sha256(key),
           "predecessor_nonce": predecessor_nonce}
    _atomic_create(observer / "identity.json", _canonical(row) + b"\n", 0o600)
    _atomic_create(observer / "registry.json",
                   _canonical({"schema": SCHEMA, "files": {}}) + b"\n", 0o600)
    _atomic_create(observer / "pairs.json",
                   _canonical({"schema": SCHEMA, "pairs": {}}) + b"\n", 0o600)
    _atomic_create(observer / "expectations.json",
                   _canonical({"schema": SCHEMA, "expected": {}}) + b"\n", 0o600)
    _atomic_create(observer / "batch-tools.json",
                   _canonical({"schema": SCHEMA, "tools": {}}) + b"\n", 0o600)
    _atomic_create(observer / "rejections.json",
                   _canonical({"schema": SCHEMA, "rejected": {}}) + b"\n", 0o600)
    _atomic_create(observer / "subagent-state.json",
                   _canonical({"schema": SCHEMA, "active": None,
                               "completed": [], "next_permit_sequence": 0}) + b"\n",
                   0o600)
    _fsync_dir(observer)
    return observer


def _wall_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _observation_mac(capability, row):
    unsigned = dict(row)
    unsigned.pop("mac", None)
    return hmac.new(capability, _canonical(unsigned), hashlib.sha256).hexdigest()


def publish_observation(observer, run_nonce, boot_id, *, sequence,
                        monotonic_ns=None, wall_time=None,
                        last_completed_request=None, last_completed_tool=None,
                        pending_operation=None, capability=None):
    observer = Path(observer)
    with _locked(observer):
        identity = _identity(observer)
        if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
            raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "observation writer")
        key = capability if capability is not None else _read_regular(
            observer / "observation.key", 32)
        if len(key) != 32 or not hmac.compare_digest(sha256(key), identity["capability_sha256"]):
            raise LifecycleFault("INVALID_CAPABILITY", "observation writer")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise LifecycleFault("INVALID_OBSERVATION", "sequence")
        prior = _load_json(observer / "current-observation.json", absent=None)
        expected = 0 if prior is None else prior.get("sequence", -2) + 1
        if sequence != expected:
            raise LifecycleFault("OBSERVATION_SEQUENCE", "expected %s got %s" % (expected, sequence))
        for name, value in (("last_completed_request", last_completed_request),
                            ("last_completed_tool", last_completed_tool),
                            ("pending_operation", pending_operation)):
            if value is not None and (not isinstance(value, str) or len(value) > 4096):
                raise LifecycleFault("INVALID_OBSERVATION", name)
        row = {"schema": SCHEMA, "run_nonce": run_nonce, "sequence": sequence,
               "boot_id": boot_id,
               "monotonic_ns": time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
               "wall_time": _wall_now() if wall_time is None else wall_time,
               "last_completed_request": last_completed_request,
               "last_completed_tool": last_completed_tool,
               "pending_operation": pending_operation}
        if not isinstance(row["monotonic_ns"], int) or isinstance(row["monotonic_ns"], bool) \
                or row["monotonic_ns"] < 0:
            raise LifecycleFault("INVALID_OBSERVATION", "monotonic_ns")
        row["mac"] = _observation_mac(key, row)
        _append(observer / "observations.jsonl", row)
        _atomic_replace(observer / "current-observation.json",
                        _canonical(row) + b"\n")
        return row


def _fault_unlocked(observer, run_nonce, reason, detail):
    observer = Path(observer)
    prior = _load_json(observer / "fault.json", absent=None)
    if prior is not None:
        return prior
    row = {"schema": SCHEMA, "run_nonce": run_nonce, "reason": reason,
           "detail": detail, "observed_at": _wall_now(),
           "monotonic_ns": time.monotonic_ns()}
    try:
        _atomic_create(observer / "fault.json", _canonical(row) + b"\n")
    except FileExistsError:
        return _load_json(observer / "fault.json")
    _append(observer / "faults.jsonl", row)
    return row


def record_fault(observer, run_nonce, reason, detail):
    with _locked(observer):
        return _fault_unlocked(observer, run_nonce, reason, detail)


def register_expected_tools(observer, run_nonce, boot_id, request_reference,
                            tool_use_ids):
    """Durably record tool hooks a completed provider response must produce."""
    if not isinstance(request_reference, str) or not request_reference:
        raise LifecycleFault("INVALID_TOOL_EXPECTATION", "request reference")
    if not isinstance(tool_use_ids, list) or not tool_use_ids:
        raise LifecycleFault("INVALID_TOOL_EXPECTATION", "tool ids")
    cleaned = []
    for value in tool_use_ids:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise LifecycleFault("INVALID_TOOL_EXPECTATION", "tool id")
        if value in cleaned:
            raise LifecycleFault("INVALID_TOOL_EXPECTATION",
                                 "duplicate tool id: %s" % value)
        cleaned.append(value)
    observer = Path(observer)
    with _locked(observer):
        terminal = _load_json(observer / "fault.json", absent=None)
        if terminal is not None:
            raise LifecycleFault("TERMINAL", "terminal lifecycle fault: %s" % terminal.get("reason"))
        identity = _identity(observer)
        if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
            _raise_fault(observer, run_nonce, "SEGMENT_IDENTITY_MISMATCH", "tool expectation")
        state = _load_json(observer / "expectations.json")
        if state.get("schema") != SCHEMA or not isinstance(state.get("expected"), dict):
            _raise_fault(observer, run_nonce, "EXPECTATION_STATE_MALFORMED", "shape")
        try:
            owners = _expected_tool_owners(state)
        except LifecycleFault as exc:
            _raise_fault(observer, run_nonce, exc.reason, exc.detail)
        for tool_id in cleaned:
            owner = owners.get(tool_id)
            if owner is not None and owner != request_reference:
                _raise_fault(observer, run_nonce, "TOOL_EXPECTATION_REUSED",
                             "%s belongs to %s, not %s" % (
                                 tool_id, owner, request_reference))
        row = state["expected"].get(request_reference)
        if row is None:
            row = {"request_reference": request_reference, "tool_use_ids": [],
                   "registered_at": _wall_now()}
            state["expected"][request_reference] = row
        for tool_id in cleaned:
            if tool_id not in row["tool_use_ids"]:
                row["tool_use_ids"].append(tool_id)
        _atomic_replace(observer / "expectations.json", _canonical(state) + b"\n")
        _append(observer / "expected-tools.jsonl", {
            "schema": SCHEMA, "run_nonce": run_nonce,
            "request_reference": request_reference, "tool_use_ids": cleaned,
            "observed_at": _wall_now(), "monotonic_ns": time.monotonic_ns()})
        return row


def _expected_tool_owners(state):
    if state.get("schema") != SCHEMA or not isinstance(state.get("expected"), dict):
        raise LifecycleFault("EXPECTATION_STATE_MALFORMED", "shape")
    owners = {}
    for request_reference, request in state["expected"].items():
        if (not isinstance(request_reference, str) or not request_reference
                or not isinstance(request, dict)
                or request.get("request_reference") != request_reference
                or not isinstance(request.get("tool_use_ids"), list)):
            raise LifecycleFault("EXPECTATION_STATE_MALFORMED", "request shape")
        seen = set()
        for tool_id in request["tool_use_ids"]:
            if (not isinstance(tool_id, str) or not tool_id
                    or len(tool_id) > 4096 or tool_id in seen):
                raise LifecycleFault("EXPECTATION_STATE_MALFORMED",
                                     "invalid or duplicate tool id")
            seen.add(tool_id)
            owner = owners.get(tool_id)
            if owner is not None and owner != request_reference:
                raise LifecycleFault("TOOL_EXPECTATION_REUSED",
                                     "%s belongs to %s and %s" % (
                                         tool_id, owner, request_reference))
            owners[tool_id] = request_reference
    return owners


def _completed_tool_ids(state):
    if state.get("schema") != SCHEMA or not isinstance(state.get("pairs"), dict):
        raise LifecycleFault("HOOK_STATE_MALFORMED", "shape")
    owners = {}
    completed = set()
    pending = []
    for key, pair in state["pairs"].items():
        if (not isinstance(key, str) or key.count("\x1f") != 1
                or any(not part for part in key.split("\x1f"))
                or not isinstance(pair, dict)):
            raise LifecycleFault("HOOK_STATE_MALFORMED", "pair shape")
        call_id = pair.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 4096:
            raise LifecycleFault("HOOK_STATE_MALFORMED", "tool_call_id")
        owner = owners.get(call_id)
        if owner is not None and owner != key:
            raise LifecycleFault("HOOK_CALL_ID_REUSED",
                                 "%s belongs to %s and %s" % (call_id, owner, key))
        owners[call_id] = key
        pre, post = pair.get("pre"), pair.get("post")
        if post is not None or (isinstance(pre, dict)
                                and pre.get("output", {}).get("continue") is False):
            completed.add(call_id)
        elif pre is not None:
            pending.append(key)
    return completed, sorted(pending)


def _rejected_tool_ids(state):
    if state.get("schema") != SCHEMA or not isinstance(state.get("rejected"), dict):
        raise LifecycleFault("REJECTION_STATE_MALFORMED", "shape")
    rejected = set()
    fields = {"tool_call_id", "tool_name", "request_reference", "status",
              "error_type", "execution_status", "input_sha256", "sequence"}
    for call_id, row in state["rejected"].items():
        if (not isinstance(call_id, str) or not call_id or len(call_id) > 4096
                or not isinstance(row, dict) or set(row) != fields
                or row.get("tool_call_id") != call_id
                or not isinstance(row.get("tool_name"), str) or not row["tool_name"]
                or not isinstance(row.get("request_reference"), str)
                or not row["request_reference"]
                or row.get("status") != "error"
                or row.get("error_type") != "invalid_tool_params"
                or row.get("execution_status") != "not_started"
                or not _hex_digest(row.get("input_sha256"))
                or type(row.get("sequence")) is not int or row["sequence"] < 0):
            raise LifecycleFault("REJECTION_STATE_MALFORMED", "rejected call shape")
        rejected.add(call_id)
    return rejected


def _batched_tool_ids(state):
    if state.get("schema") != SCHEMA or not isinstance(state.get("tools"), dict):
        raise LifecycleFault("BATCH_STATE_MALFORMED", "shape")
    batched = set()
    fields = {"tool_call_id", "tool_name", "request_reference", "status",
              "error_type", "execution_status", "input_sha256", "sequence"}
    for call_id, row in state["tools"].items():
        if (not isinstance(call_id, str) or not call_id or len(call_id) > 4096
                or not isinstance(row, dict) or set(row) != fields
                or row.get("tool_call_id") != call_id
                or not isinstance(row.get("tool_name"), str) or not row["tool_name"]
                or not isinstance(row.get("request_reference"), str)
                or not row["request_reference"]
                or not isinstance(row.get("status"), str) or not row["status"]
                or (row.get("error_type") is not None
                    and not isinstance(row.get("error_type"), str))
                or (row.get("execution_status") is not None
                    and not isinstance(row.get("execution_status"), str))
                or not _hex_digest(row.get("input_sha256"))
                or type(row.get("sequence")) is not int or row["sequence"] < 0):
            raise LifecycleFault("BATCH_STATE_MALFORMED", "tool shape")
        batched.add(call_id)
    return batched


def _subagent_state(observer):
    state = _load_json(Path(observer) / "subagent-state.json")
    if (state.get("schema") != SCHEMA or set(state) != {
            "schema", "active", "completed", "next_permit_sequence"}
            or not isinstance(state["completed"], list)
            or type(state["next_permit_sequence"]) is not int
            or state["next_permit_sequence"] < 0
            or (state["active"] is not None and not isinstance(state["active"], dict))):
        raise LifecycleFault("SUBAGENT_STATE_MALFORMED", "shape")
    return state


def _pair_pre_event(observer, pair):
    pre = pair.get("pre") if isinstance(pair, dict) else None
    sequence = pre.get("sequence") if isinstance(pre, dict) else None
    if type(sequence) is not int or sequence < 0:
        raise LifecycleFault("HOOK_STATE_MALFORMED", "parent pre sequence")
    rows = _read_regular(Path(observer) / "hook-events.jsonl").splitlines()
    if sequence >= len(rows):
        raise LifecycleFault("HOOK_STATE_MALFORMED", "parent pre receipt missing")
    receipt = _strict_json(rows[sequence])
    try:
        raw = base64.b64decode(receipt["input_base64"], validate=True)
        event = _strict_json(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise LifecycleFault("HOOK_STATE_MALFORMED", "parent pre receipt") from exc
    if (receipt.get("sequence") != sequence or receipt.get("phase") != "PreToolUse"
            or receipt.get("input_sha256") != sha256(raw)):
        raise LifecycleFault("HOOK_STATE_MALFORMED", "parent pre receipt identity")
    return event


def _append_subagent_event(observer, run_nonce, phase, event, raw_input, output):
    path = Path(observer) / "subagent-events.jsonl"
    sequence = sum(1 for line in path.read_bytes().splitlines() if line) if path.exists() else 0
    _append(path, {
        "schema": SCHEMA, "run_nonce": run_nonce, "sequence": sequence,
        "phase": phase, "session_id": event["session_id"],
        "agent_id": event["agent_id"], "agent_type": event["agent_type"],
        "input_sha256": sha256(raw_input),
        "input_base64": base64.b64encode(raw_input).decode("ascii"),
        "output": output, "observed_at": _wall_now(),
        "monotonic_ns": time.monotonic_ns(),
    })
    return sequence


def _append_nested_dispatch(observer, run_nonce, active, *, action,
                            request_sha256=None):
    path = Path(observer) / "nested-dispatches.jsonl"
    used_sequences = set()
    used_requests = set()
    if path.exists():
        for raw_line in _read_regular(path).splitlines():
            prior = _strict_json(raw_line)
            sequence = prior.get("sequence")
            if type(sequence) is not int or sequence in used_sequences:
                raise LifecycleFault("NESTED_DISPATCH_REPLAY", "permit sequence")
            used_sequences.add(sequence)
            if prior.get("action") == "consume":
                used_requests.add(prior.get("request_sha256"))
    if active["permit"]["sequence"] in used_sequences:
        raise LifecycleFault("NESTED_DISPATCH_REPLAY", "permit sequence")
    if action == "consume" and request_sha256 in used_requests:
        raise LifecycleFault("NESTED_DISPATCH_REPLAY", "request digest")
    _append(path, {
        "schema": SCHEMA, "run_nonce": run_nonce,
        "sequence": active["permit"]["sequence"],
        "action": action, "provenance": active["permit"]["provenance"],
        "parent_tool_call_id": active["parent_tool_call_id"],
        "agent_id": active["agent_id"],
        "request_sha256": request_sha256,
        "observed_at": _wall_now(), "monotonic_ns": time.monotonic_ns(),
    })


def _active_allowed_parent_ids(active, owners):
    return {tool_id for tool_id, owner in owners.items()
            if owner == active["parent_request_reference"]}


def _validate_active_child_accounting(observer, active, *, allow_parent_pending=True):
    pairs = _load_json(Path(observer) / "pairs.json")
    expected = _load_json(Path(observer) / "expectations.json")
    completed, pending = _completed_tool_ids(pairs)
    owners = _expected_tool_owners(expected)
    batched = _batched_tool_ids(_load_json(Path(observer) / "batch-tools.json"))
    rejected = _rejected_tool_ids(_load_json(Path(observer) / "rejections.json"))
    parent_ids = _active_allowed_parent_ids(active, owners)
    allowed_pending = {active["parent_pair_key"]} if allow_parent_pending else set()
    extra_pending = set(pending) - allowed_pending
    if extra_pending:
        raise LifecycleFault("HOOK_PAIR_MISSING",
                             "incomplete hook pair: %s" % sorted(extra_pending)[0])
    child_ids = set(owners) - parent_ids
    overlap = completed & rejected
    if overlap:
        raise LifecycleFault("TOOL_OUTCOME_AMBIGUOUS", sorted(overlap)[0])
    missing_batch = child_ids - batched
    if missing_batch:
        raise LifecycleFault("EXPECTED_TOOL_BATCH_MISSING", sorted(missing_batch)[0])
    missing = child_ids - completed - rejected
    if missing:
        raise LifecycleFault("EXPECTED_TOOL_HOOK_MISSING", sorted(missing)[0])
    if (rejected - set(owners) or batched - set(owners)
            or (rejected | batched) & parent_ids):
        raise LifecycleFault("SUBAGENT_SCOPE_MISMATCH", "root-origin tool in child scope")
    return completed, pending, owners, batched, rejected


def _raise_fault(observer, run_nonce, reason, detail):
    row = _fault_unlocked(observer, run_nonce, reason, detail)
    raise LifecycleFault(row["reason"], row["detail"])


def _validate_observation(observer, run_nonce, boot_id, now_monotonic_ns):
    identity = _identity(observer)
    if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
        _raise_fault(observer, run_nonce, "SEGMENT_IDENTITY_MISMATCH", "dispatch checker")
    row = _load_json(Path(observer) / "current-observation.json", absent=None)
    if row is None:
        _raise_fault(observer, run_nonce, "OBSERVATION_MISSING", "initial root observation required")
    required = (row.get("schema") == SCHEMA and row.get("run_nonce") == run_nonce
                and row.get("boot_id") == boot_id
                and isinstance(row.get("sequence"), int)
                and not isinstance(row.get("sequence"), bool)
                and row.get("sequence") >= 0
                and isinstance(row.get("monotonic_ns"), int)
                and not isinstance(row.get("monotonic_ns"), bool))
    if not required:
        _raise_fault(observer, run_nonce, "OBSERVATION_INVALID", "identity or schema")
    key = _read_regular(Path(observer) / "observation.key", 32)
    if len(key) != 32 or not hmac.compare_digest(sha256(key), identity["capability_sha256"]) \
            or not isinstance(row.get("mac"), str) \
            or not hmac.compare_digest(row["mac"], _observation_mac(key, row)):
        _raise_fault(observer, run_nonce, "OBSERVATION_INVALID", "authentication")
    age = now_monotonic_ns - row["monotonic_ns"]
    if age < 0:
        _raise_fault(observer, run_nonce, "OBSERVATION_FUTURE", str(age))
    if age > MAX_OBSERVATION_AGE_NS:
        _raise_fault(observer, run_nonce, "OBSERVATION_STALE", "%dns" % age)
    accepted = _load_json(Path(observer) / "last-accepted-observation.json", absent=None)
    if accepted is not None and row["sequence"] < accepted.get("sequence", -1):
        _raise_fault(observer, run_nonce, "OBSERVATION_REGRESSED",
                     "%s < %s" % (row["sequence"], accepted.get("sequence")))
    return row


def _check_supervision_unlocked(observer, run_nonce, boot_id, now):
    terminal = _load_json(observer / "fault.json", absent=None)
    if terminal is not None:
        raise LifecycleFault("TERMINAL", "terminal lifecycle fault: %s" % terminal.get("reason"))
    try:
        return _validate_observation(observer, run_nonce, boot_id, now)
    except LifecycleFault:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        _raise_fault(observer, run_nonce, "OBSERVATION_MALFORMED",
                     "%s: %s" % (type(exc).__name__, exc))


def _accept_observation_unlocked(observer, row, now):
    accepted = {"schema": SCHEMA, "run_nonce": row["run_nonce"],
                "sequence": row["sequence"], "monotonic_ns": row["monotonic_ns"],
                "checked_at_monotonic_ns": now}
    _atomic_replace(observer / "last-accepted-observation.json",
                    _canonical(accepted) + b"\n")


def check_supervision(observer, run_nonce, boot_id, *, now_monotonic_ns=None):
    """Check the continuously enforceable observer and terminal-fault state."""
    observer = Path(observer)
    with _locked(observer):
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        row = _check_supervision_unlocked(observer, run_nonce, boot_id, now)
        _accept_observation_unlocked(observer, row, now)
        return row


def check_dispatch(observer, run_nonce, boot_id, *, now_monotonic_ns=None,
                   request_sha256=None):
    observer = Path(observer)
    with _locked(observer):
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        row = _check_supervision_unlocked(observer, run_nonce, boot_id, now)
        try:
            pairs = _load_json(observer / "pairs.json", absent={"schema": SCHEMA, "pairs": {}})
            if pairs.get("schema") != SCHEMA or not isinstance(pairs.get("pairs"), dict):
                raise ValueError("hook pair state shape")
        except LifecycleFault:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            _raise_fault(observer, run_nonce, "HOOK_STATE_MALFORMED",
                         "%s: %s" % (type(exc).__name__, exc))
        try:
            completed, pending = _completed_tool_ids(pairs)
        except LifecycleFault as exc:
            _raise_fault(observer, run_nonce, exc.reason, exc.detail)
        try:
            subagents = _subagent_state(observer)
            active = subagents["active"]
        except LifecycleFault as exc:
            _raise_fault(observer, run_nonce, exc.reason, exc.detail)
        if active is not None:
            if not _hex_digest(request_sha256):
                _raise_fault(observer, run_nonce, "NESTED_DISPATCH_UNAUTHORIZED",
                             "exact request digest required")
            try:
                _validate_active_child_accounting(observer, active)
            except LifecycleFault as exc:
                _raise_fault(observer, run_nonce, exc.reason, exc.detail)
            permit = active.get("permit")
            if not isinstance(permit, dict) or permit.get("available") is not True:
                _raise_fault(observer, run_nonce, "NESTED_DISPATCH_UNAUTHORIZED",
                             "no one-use child dispatch permit")
            _append_nested_dispatch(observer, run_nonce, active, action="consume",
                                    request_sha256=request_sha256)
            permit["available"] = False
            permit["consumed_request_sha256"] = request_sha256
            _atomic_replace(observer / "subagent-state.json", _canonical(subagents) + b"\n")
            _accept_observation_unlocked(observer, row, now)
            return row
        if pending:
            _raise_fault(observer, run_nonce, "HOOK_PAIR_MISSING",
                         "incomplete hook pair: %s" % pending[0])
        try:
            expected = _load_json(observer / "expectations.json")
            if expected.get("schema") != SCHEMA or not isinstance(expected.get("expected"), dict):
                raise ValueError("expectation state shape")
            owners = _expected_tool_owners(expected)
            batched = _batched_tool_ids(_load_json(observer / "batch-tools.json"))
            rejected = _rejected_tool_ids(_load_json(observer / "rejections.json"))
            overlap = completed & rejected
            if overlap:
                raise LifecycleFault("TOOL_OUTCOME_AMBIGUOUS", sorted(overlap)[0])
            unexpected = rejected - set(owners)
            if unexpected:
                raise LifecycleFault("CLIENT_REJECTION_UNEXPECTED", sorted(unexpected)[0])
            unexpected_batch = batched - set(owners)
            if unexpected_batch:
                raise LifecycleFault("CLIENT_BATCH_UNEXPECTED_TOOL",
                                     sorted(unexpected_batch)[0])
            missing_batch = set(owners) - batched
            if missing_batch:
                raise LifecycleFault("EXPECTED_TOOL_BATCH_MISSING",
                                     sorted(missing_batch)[0])
            missing = []
            missing.extend(tool_id for tool_id in owners
                           if tool_id not in completed and tool_id not in rejected)
        except LifecycleFault as exc:
            _raise_fault(observer, run_nonce, exc.reason, exc.detail)
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            _raise_fault(observer, run_nonce, "EXPECTATION_STATE_MALFORMED",
                         "%s: %s" % (type(exc).__name__, exc))
        if missing:
            _raise_fault(observer, run_nonce, "EXPECTED_TOOL_HOOK_MISSING",
                         "expected tool has no completed hook pair: %s" % sorted(missing)[0])
        _accept_observation_unlocked(observer, row, now)
        return row


def _clean_rel(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise LifecycleFault("UNSAFE_EVIDENCE_PATH", repr(value))
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise LifecycleFault("UNSAFE_EVIDENCE_PATH", value)
    return path.as_posix()


def _relative_to_workspace(workspace, path):
    workspace = Path(workspace).absolute()
    path = Path(path).absolute()
    try:
        rel = path.relative_to(workspace)
    except ValueError as exc:
        raise LifecycleFault("UNSAFE_EVIDENCE_PATH", str(path)) from exc
    return _clean_rel(rel.as_posix())


def _safe_candidate(workspace, relative):
    workspace = Path(workspace).absolute()
    relative = _clean_rel(relative)
    current = workspace
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return workspace / relative
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise LifecycleFault("UNSAFE_EVIDENCE_PATH", relative)
    return workspace / relative


def _discover_required(workspace):
    workspace = Path(workspace).absolute()
    discovered = set()
    for relative in CANONICAL_WORK_FILES:
        path = _safe_candidate(workspace, relative)
        if os.path.lexists(path):
            discovered.add(relative)
    marker_rel = ".sherlock/active.json"
    marker_path = _safe_candidate(workspace, marker_rel)
    if not os.path.lexists(marker_path):
        return discovered
    marker = _strict_json(_read_regular(marker_path, MAX_HOOK_INPUT_BYTES))
    if marker.get("active") is not True or marker.get("workspace") != str(workspace):
        raise LifecycleFault("INVALID_ACTIVE_MANIFEST", "workspace or active")
    out = Path(marker.get("out", ""))
    out_rel = _relative_to_workspace(workspace, out)
    if marker.get("mode") not in ("single", "multi"):
        raise LifecycleFault("INVALID_ACTIVE_MANIFEST", "mode")
    worklists = marker.get("worklists")
    if not isinstance(worklists, list) or not worklists:
        raise LifecycleFault("INVALID_ACTIVE_MANIFEST", "worklists")
    discovered.add(marker_rel)
    seen = set()
    for item in worklists:
        item = _clean_rel(item)
        if item in seen:
            raise LifecycleFault("INVALID_ACTIVE_MANIFEST", "duplicate worklist")
        seen.add(item)
        relative = _clean_rel("%s/%s" % (out_rel, item))
        path = _safe_candidate(workspace, relative)
        if os.path.lexists(path):
            discovered.add(relative)
    hosts = marker.get("hosts_manifest")
    if hosts:
        hosts_rel = _clean_rel("%s/%s" % (out_rel, _clean_rel(hosts)))
        hosts_path = _safe_candidate(workspace, hosts_rel)
        if os.path.lexists(hosts_path):
            discovered.add(hosts_rel)
    return discovered


def _publish_object(observer, digest, data):
    path = Path(observer) / "objects" / digest
    try:
        _atomic_create(path, data, 0o400)
    except FileExistsError:
        if _read_regular(path, len(data)) != data:
            raise LifecycleFault("OBJECT_COLLISION", digest)
    return path


def _load_registry(observer):
    row = _load_json(Path(observer) / "registry.json")
    if row.get("schema") != SCHEMA or not isinstance(row.get("files"), dict):
        raise LifecycleFault("INVALID_REGISTRY", "shape")
    return row


def _snapshot_required(observer, workspace, phase, hook_key):
    registry = _load_registry(observer)
    registered = set(registry["files"])
    discovered = _discover_required(workspace)
    snapshots = []
    contents = {}
    for relative in sorted(registered | discovered):
        path = _safe_candidate(workspace, relative)
        if not os.path.lexists(path):
            if relative in registered:
                raise LifecycleFault("REGISTERED_EVIDENCE_MISSING", relative)
            continue
        data = _read_regular(path, MAX_EVIDENCE_BYTES)
        digest = sha256(data)
        contents[relative] = (digest, data)
    for relative, (digest, data) in contents.items():
        _publish_object(observer, digest, data)
        prior = registry["files"].get(relative)
        if prior is None:
            prior = {"registered_by": hook_key, "versions": []}
            registry["files"][relative] = prior
        if not prior["versions"] or prior["versions"][-1] != digest:
            prior["versions"].append(digest)
        prior.update(current_sha256=digest, last_phase=phase, last_hook=hook_key)
        snapshots.append({"path": relative, "sha256": digest, "bytes": len(data)})
    _atomic_replace(Path(observer) / "registry.json", _canonical(registry) + b"\n")
    return snapshots


def _hook_output(phase, allowed, reason=None):
    specific = {"hookEventName": phase}
    if not allowed:
        specific.update(permissionDecision="deny",
                        permissionDecisionReason=reason or "lifecycle integrity fault")
    row = {"continue": bool(allowed), "hookSpecificOutput": specific}
    if not allowed:
        row["stopReason"] = reason or "lifecycle integrity fault"
    return row


def _record_boundary_gate(observer, raw_input, path, completed=None, error=None, valid=False):
    """Keep the gate exchange durable; command stdout never reaches the model twice."""
    row = {"schema": SCHEMA, "input_sha256": sha256(raw_input),
           "gate_path": str(path), "gate_sha256": None,
           "exit_code": None, "stdout_base64": None, "stderr_base64": None,
           "valid_output": valid, "error": error, "observed_at": _wall_now(),
           "monotonic_ns": time.monotonic_ns()}
    try:
        row["gate_sha256"] = sha256(_read_regular(path, MAX_HOOK_INPUT_BYTES))
    except (LifecycleFault, OSError, ValueError):
        pass
    if completed is not None:
        row.update(exit_code=completed.returncode,
                   stdout_base64=base64.b64encode(completed.stdout).decode("ascii"),
                   stderr_base64=base64.b64encode(completed.stderr).decode("ascii"))
    _append(Path(observer) / "boundary-gate-events.jsonl", row)


def _boundary_gate_output(observer, boundary_gate, raw_input):
    """Run one launcher-selected PreToolUse gate and retain its exact decision."""
    path = Path(boundary_gate)
    completed = None
    try:
        st = os.lstat(path)
        if (not path.is_absolute() or stat.S_ISLNK(st.st_mode)
                or not stat.S_ISREG(st.st_mode) or path.resolve(strict=True) != path):
            raise ValueError("unsafe path")
        completed = subprocess.run(
            [sys.executable, str(path)], input=raw_input, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10, check=False)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        _record_boundary_gate(observer, raw_input, path, completed, type(exc).__name__)
        raise LifecycleFault("BOUNDARY_GATE_FAILED", type(exc).__name__) from exc
    if completed.returncode != 0:
        _record_boundary_gate(observer, raw_input, path, completed, "exit %d" % completed.returncode)
        raise LifecycleFault("BOUNDARY_GATE_FAILED", "exit %d" % completed.returncode)
    try:
        output = _strict_json(completed.stdout)
        if not isinstance(output, dict):
            raise ValueError("non-object output")
        specific = output.get("hookSpecificOutput")
        allowed = output.get("continue")
        if (set(output) - {"continue", "hookSpecificOutput", "stopReason"}
                or type(allowed) is not bool or not isinstance(specific, dict)
                or specific.get("hookEventName") != "PreToolUse"):
            raise ValueError("shape")
        if allowed:
            if set(specific) != {"hookEventName"} or "stopReason" in output:
                raise ValueError("allow shape")
        elif (set(specific) != {"hookEventName", "permissionDecision", "permissionDecisionReason"}
              or specific.get("permissionDecision") != "deny"
              or not isinstance(specific.get("permissionDecisionReason"), str)
              or output.get("stopReason") != specific["permissionDecisionReason"]):
            raise ValueError("deny shape")
    except (LifecycleFault, ValueError, TypeError) as exc:
        _record_boundary_gate(observer, raw_input, path, completed, "invalid output: %s" % type(exc).__name__)
        raise LifecycleFault("BOUNDARY_GATE_FAILED", "invalid output") from exc
    _record_boundary_gate(observer, raw_input, path, completed, valid=True)
    return output


def _hook_key(event):
    session = event.get("session_id")
    tool = event.get("tool_use_id")
    call_id = event.get("tool_call_id")
    if (not isinstance(session, str) or not session
            or not isinstance(tool, str) or not tool
            or "\x1f" in session or "\x1f" in tool
            or not isinstance(call_id, str) or not call_id or len(call_id) > 4096):
        raise LifecycleFault("INVALID_HOOK_INPUT",
                             "session_id/tool_use_id/tool_call_id")
    return "%s\x1f%s" % (session, tool)


def _handle_subagent_event(observer, run_nonce, boot_id, event, raw_input):
    phase = event.get("hook_event_name")
    session = event.get("session_id")
    agent_id = event.get("agent_id")
    agent_type = event.get("agent_type")
    if (phase not in ("SubagentStart", "SubagentStop")
            or not isinstance(session, str) or not session or len(session) > 4096
            or not isinstance(agent_id, str) or not agent_id or len(agent_id) > 4096
            or not isinstance(agent_type, str) or not agent_type or len(agent_type) > 4096):
        raise LifecycleFault("INVALID_HOOK_INPUT", "%s shape" % phase)
    output = _hook_output(phase, True)
    with _locked(observer):
        terminal = _load_json(observer / "fault.json", absent=None)
        if terminal is not None:
            return _hook_output(phase, False,
                                "terminal lifecycle fault: %s" % terminal.get("reason"))
        identity = _identity(observer)
        if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
            raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "hook")
        state = _subagent_state(observer)
        if phase == "SubagentStart":
            if state["active"] is not None:
                raise LifecycleFault("NESTED_SUBAGENT_UNSUPPORTED", agent_id)
            pairs = _load_json(observer / "pairs.json")
            _, pending = _completed_tool_ids(pairs)
            expected = _expected_tool_owners(_load_json(observer / "expectations.json"))
            candidates = []
            for key in pending:
                pair = pairs["pairs"][key]
                pre_event = _pair_pre_event(observer, pair)
                call_id = pair["tool_call_id"]
                tool_input = pre_event.get("tool_input")
                if (pre_event.get("tool_name") == "agent"
                        and isinstance(tool_input, dict)
                        and tool_input.get("run_in_background") is False
                        and tool_input.get("subagent_type") == agent_type
                        and agent_id == "%s-%s" % (agent_type, call_id)
                        and call_id in expected):
                    candidates.append((key, call_id, expected[call_id]))
            if len(candidates) != 1 or len(pending) != 1:
                raise LifecycleFault("SUBAGENT_START_UNAUTHORIZED",
                                     "requires one exact pending foreground agent")
            key, call_id, request_reference = candidates[0]
            sequence = state["next_permit_sequence"]
            state["next_permit_sequence"] += 1
            state["active"] = {
                "agent_id": agent_id, "agent_type": agent_type,
                "session_id": session, "parent_pair_key": key,
                "parent_tool_call_id": call_id,
                "parent_request_reference": request_reference,
                "permit": {"available": True, "provenance": "start",
                           "sequence": sequence, "consumed_request_sha256": None},
            }
        else:
            active = state["active"]
            if (active is None or active.get("agent_id") != agent_id
                    or active.get("agent_type") != agent_type
                    or active.get("session_id") != session):
                raise LifecycleFault("SUBAGENT_STOP_MISMATCH", agent_id)
            _validate_active_child_accounting(observer, active)
            permit = active.get("permit")
            if not isinstance(permit, dict):
                raise LifecycleFault("SUBAGENT_STATE_MALFORMED", "permit")
            if permit.get("available") is True:
                if permit.get("provenance") != "batch":
                    raise LifecycleFault("SUBAGENT_DISPATCH_MISSING",
                                         "unused initial child permit")
                _append_nested_dispatch(observer, run_nonce, active, action="revoke")
                permit["available"] = False
            state["completed"].append({
                "agent_id": agent_id, "agent_type": agent_type,
                "parent_tool_call_id": active["parent_tool_call_id"],
                "parent_request_reference": active["parent_request_reference"],
            })
            state["active"] = None
        _append_subagent_event(observer, run_nonce, phase, event, raw_input, output)
        _atomic_replace(observer / "subagent-state.json", _canonical(state) + b"\n")
        return output


def _require_root_reconciled(observer):
    if _subagent_state(observer)["active"] is not None:
        raise LifecycleFault("SUBAGENT_STOP_MISSING", "root boundary while child active")
    pairs = _load_json(observer / "pairs.json")
    completed, pending = _completed_tool_ids(pairs)
    owners = _expected_tool_owners(_load_json(observer / "expectations.json"))
    batched = _batched_tool_ids(_load_json(observer / "batch-tools.json"))
    rejected = _rejected_tool_ids(_load_json(observer / "rejections.json"))
    if pending:
        raise LifecycleFault("HOOK_PAIR_MISSING", pending[0])
    if set(owners) != batched:
        raise LifecycleFault("EXPECTED_TOOL_BATCH_MISSING",
                             sorted(set(owners) - batched)[0])
    missing = set(owners) - completed - rejected
    if missing:
        raise LifecycleFault("EXPECTED_TOOL_HOOK_MISSING", sorted(missing)[0])


def _handle_session_start(observer, workspace, run_nonce, boot_id, event, raw_input):
    session, source, cwd = (event.get("session_id"), event.get("source"),
                            event.get("cwd"))
    if (not isinstance(session, str) or not session or len(session) > 4096
            or not isinstance(source, str) or not source or len(source) > 128
            or not isinstance(cwd, str) or not cwd
            or Path(cwd).resolve() != Path(workspace).resolve()):
        raise LifecycleFault("INVALID_HOOK_INPUT", "SessionStart shape")
    output = _hook_output("SessionStart", True)
    with _locked(observer):
        terminal = _load_json(observer / "fault.json", absent=None)
        if terminal is not None:
            return _hook_output("SessionStart", False,
                                "terminal lifecycle fault: %s" % terminal.get("reason"))
        identity = _identity(observer)
        if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
            raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "hook")
        if source == "clear":
            _require_root_reconciled(observer)
        path = observer / "session-start-events.jsonl"
        sequence = sum(1 for line in path.read_bytes().splitlines() if line) if path.exists() else 0
        _append(path, {
            "schema": SCHEMA, "run_nonce": run_nonce, "sequence": sequence,
            "phase": "SessionStart", "session_id": session, "source": source,
            "cwd": str(Path(cwd).resolve()), "input_sha256": sha256(raw_input),
            "input_base64": base64.b64encode(raw_input).decode("ascii"),
            "output": output, "observed_at": _wall_now(),
            "monotonic_ns": time.monotonic_ns(),
        })
        return output


def _handle_root_boundary(observer, run_nonce, boot_id, event, raw_input):
    session = event.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 4096:
        raise LifecycleFault("INVALID_HOOK_INPUT", "UserPromptSubmit shape")
    output = _hook_output("UserPromptSubmit", True)
    with _locked(observer):
        terminal = _load_json(observer / "fault.json", absent=None)
        if terminal is not None:
            return _hook_output("UserPromptSubmit", False,
                                "terminal lifecycle fault: %s" % terminal.get("reason"))
        identity = _identity(observer)
        if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
            raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "hook")
        _require_root_reconciled(observer)
        path = observer / "root-boundary-events.jsonl"
        sequence = sum(1 for line in path.read_bytes().splitlines() if line) if path.exists() else 0
        _append(path, {
            "schema": SCHEMA, "run_nonce": run_nonce, "sequence": sequence,
            "phase": "UserPromptSubmit", "session_id": session,
            "input_sha256": sha256(raw_input),
            "input_base64": base64.b64encode(raw_input).decode("ascii"),
            "output": output, "observed_at": _wall_now(),
            "monotonic_ns": time.monotonic_ns(),
        })
        return output


def _handle_post_tool_batch(observer, run_nonce, boot_id, event, raw_input):
    session = event.get("session_id")
    calls = event.get("tool_calls")
    if (not isinstance(session, str) or not session or len(session) > 4096
            or not isinstance(calls, list) or not calls):
        raise LifecycleFault("INVALID_HOOK_INPUT", "PostToolBatch shape")
    input_hash = sha256(raw_input)
    output = _hook_output("PostToolBatch", True)
    with _locked(observer):
        terminal = _load_json(observer / "fault.json", absent=None)
        if terminal is not None:
            return _hook_output("PostToolBatch", False,
                                "terminal lifecycle fault: %s" % terminal.get("reason"))
        identity = _identity(observer)
        if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
            raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "hook")
        expected = _load_json(observer / "expectations.json")
        pairs = _load_json(observer / "pairs.json")
        batches = _load_json(observer / "batch-tools.json")
        rejections = _load_json(observer / "rejections.json")
        owners = _expected_tool_owners(expected)
        completed, pending = _completed_tool_ids(pairs)
        batched = _batched_tool_ids(batches)
        rejected = _rejected_tool_ids(rejections)
        subagents = _subagent_state(observer)
        active = subagents["active"]
        if completed & rejected:
            raise LifecycleFault("TOOL_OUTCOME_AMBIGUOUS", sorted(completed & rejected)[0])
        if rejected - set(owners):
            raise LifecycleFault("CLIENT_REJECTION_UNEXPECTED",
                                 sorted(rejected - set(owners))[0])
        allowed_pending = {active["parent_pair_key"]} if active is not None else set()
        extra_pending = set(pending) - allowed_pending
        if extra_pending:
            raise LifecycleFault("HOOK_PAIR_MISSING",
                                 "incomplete hook pair: %s" % sorted(extra_pending)[0])
        call_ids = []
        accepted = []
        batch_rows = []
        for call in calls:
            if not isinstance(call, dict):
                raise LifecycleFault("INVALID_HOOK_INPUT", "PostToolBatch call")
            call_id = call.get("tool_call_id")
            tool_use_id = call.get("tool_use_id")
            tool_name = call.get("tool_name")
            response = call.get("tool_response")
            if (not isinstance(call_id, str) or not call_id or len(call_id) > 4096
                    or tool_use_id != call_id
                    or not isinstance(tool_name, str) or not tool_name
                    or not isinstance(call.get("status"), str) or not call["status"]
                    or not isinstance(response, dict)):
                raise LifecycleFault("INVALID_HOOK_INPUT", "PostToolBatch call identity")
            if call_id in call_ids:
                raise LifecycleFault("CLIENT_REJECTION_DUPLICATE", call_id)
            if call_id in batched:
                raise LifecycleFault("CLIENT_BATCH_DUPLICATE", call_id)
            call_ids.append(call_id)
            owner = owners.get(call_id)
            if owner is None:
                raise LifecycleFault("CLIENT_BATCH_UNEXPECTED_TOOL", call_id)
            if active is not None and owner == active["parent_request_reference"]:
                raise LifecycleFault("SUBAGENT_STOP_MISSING",
                                     "root-origin batch while child active: %s" % call_id)
            is_rejected = (call.get("status") == "error"
                           and response.get("error_type") == "invalid_tool_params"
                           and response.get("execution_status") == "not_started")
            if is_rejected:
                if call_id in completed:
                    raise LifecycleFault("TOOL_OUTCOME_AMBIGUOUS", call_id)
                if call_id in rejected:
                    raise LifecycleFault("CLIENT_REJECTION_DUPLICATE", call_id)
                accepted.append((call_id, tool_name, owner))
            elif call_id not in completed:
                raise LifecycleFault(
                    "EXPECTED_TOOL_HOOK_MISSING",
                    "PostToolBatch terminal tool has no completed hook pair: %s" % call_id)
        sequence_path = observer / "post-tool-batch-events.jsonl"
        sequence = 0
        if sequence_path.exists():
            sequence = sum(1 for line in sequence_path.read_bytes().splitlines() if line)
        _append(observer / "hook-starts.jsonl", {
            "schema": SCHEMA, "run_nonce": run_nonce, "session_id": session,
            "phase": "PostToolBatch", "input_sha256": input_hash,
            "input_base64": base64.b64encode(raw_input).decode("ascii"),
            "observed_at": _wall_now(), "monotonic_ns": time.monotonic_ns()})
        receipt = {
            "schema": SCHEMA, "run_nonce": run_nonce, "sequence": sequence,
            "session_id": session, "phase": "PostToolBatch",
            "input_sha256": input_hash,
            "input_base64": base64.b64encode(raw_input).decode("ascii"),
            "tool_call_ids": call_ids,
            "rejected_tool_call_ids": [row[0] for row in accepted],
            "output": output, "observed_at": _wall_now(),
            "monotonic_ns": time.monotonic_ns(),
        }
        _append(sequence_path, receipt)
        for call_id, tool_name, owner in accepted:
            rejections["rejected"][call_id] = {
                "tool_call_id": call_id, "tool_name": tool_name,
                "request_reference": owner, "status": "error",
                "error_type": "invalid_tool_params", "execution_status": "not_started",
                "input_sha256": input_hash, "sequence": sequence,
            }
        for call in calls:
            response = call["tool_response"]
            call_id = call["tool_call_id"]
            batch_rows.append((call_id, {
                "tool_call_id": call_id, "tool_name": call["tool_name"],
                "request_reference": owners[call_id], "status": call["status"],
                "error_type": response.get("error_type"),
                "execution_status": response.get("execution_status"),
                "input_sha256": input_hash, "sequence": sequence,
            }))
        _atomic_replace(observer / "rejections.json", _canonical(rejections) + b"\n")
        for call_id, row in batch_rows:
            batches["tools"][call_id] = row
        _atomic_replace(observer / "batch-tools.json", _canonical(batches) + b"\n")
        if active is not None:
            _validate_active_child_accounting(observer, active)
            permit = active.get("permit")
            if not isinstance(permit, dict) or permit.get("available") is not False:
                raise LifecycleFault("NESTED_DISPATCH_AMBIGUOUS",
                                     "child batch without consumed permit")
            sequence = subagents["next_permit_sequence"]
            subagents["next_permit_sequence"] += 1
            active["permit"] = {
                "available": True, "provenance": "batch", "sequence": sequence,
                "consumed_request_sha256": None,
            }
            _atomic_replace(observer / "subagent-state.json",
                            _canonical(subagents) + b"\n")
        return output


def handle_hook(observer, workspace, run_nonce, boot_id, raw_input, *, boundary_gate=None):
    observer = Path(observer)
    phase = "Unknown"
    try:
        if not isinstance(raw_input, bytes) or len(raw_input) > MAX_HOOK_INPUT_BYTES:
            raise LifecycleFault("INVALID_HOOK_INPUT", "size")
        event = _strict_json(raw_input)
        phase = event.get("hook_event_name")
        if phase not in ("PreToolUse", "PostToolUse", "PostToolUseFailure",
                         "PostToolBatch", "SubagentStart", "SubagentStop",
                         "SessionStart", "UserPromptSubmit"):
            raise LifecycleFault("INVALID_HOOK_INPUT", "event phase")
        if boundary_gate is not None and phase != "PreToolUse":
            raise LifecycleFault("BOUNDARY_GATE_FAILED", "non-PreToolUse event")
        if phase == "SessionStart":
            return _handle_session_start(
                observer, workspace, run_nonce, boot_id, event, raw_input)
        if phase in ("SubagentStart", "SubagentStop"):
            return _handle_subagent_event(
                observer, run_nonce, boot_id, event, raw_input)
        if phase == "UserPromptSubmit":
            return _handle_root_boundary(
                observer, run_nonce, boot_id, event, raw_input)
        if phase == "PostToolBatch":
            return _handle_post_tool_batch(
                observer, run_nonce, boot_id, event, raw_input)
        key = _hook_key(event)
        call_id = event["tool_call_id"]
        input_hash = sha256(raw_input)
        with _locked(observer):
            terminal = _load_json(observer / "fault.json", absent=None)
            if terminal is not None:
                return _hook_output(phase, False,
                                    "terminal lifecycle fault: %s" % terminal.get("reason"))
            identity = _identity(observer)
            if identity.get("run_nonce") != run_nonce or identity.get("boot_id") != boot_id:
                raise LifecycleFault("SEGMENT_IDENTITY_MISMATCH", "hook")
            pairs = _load_json(observer / "pairs.json")
            pair = pairs["pairs"].get(key)
            slot = "pre" if phase == "PreToolUse" else "post"
            active = _subagent_state(observer)["active"]
            if (active is not None and slot == "post"
                    and call_id == active.get("parent_tool_call_id")):
                raise LifecycleFault("SUBAGENT_STOP_MISSING",
                                     "parent post while child active: %s" % call_id)
            if pair is not None and pair.get("tool_call_id") != call_id:
                raise LifecycleFault(
                    "HOOK_CALL_ID_MISMATCH",
                    "%s changed from %s to %s" % (
                        key, pair.get("tool_call_id"), call_id))
            if pair is not None and pair.get(slot) is not None:
                prior = pair[slot]
                if prior.get("phase") == phase and prior.get("input_sha256") == input_hash:
                    return prior["output"]
                raise LifecycleFault("HOOK_DUPLICATE_CONFLICT", "%s %s" % (key, phase))
            if slot == "post" and (pair is None or pair.get("pre") is None):
                raise LifecycleFault("HOOK_PAIR_MISSING", "post without pre: %s" % key)
            _append(observer / "hook-starts.jsonl", {
                "schema": SCHEMA, "run_nonce": run_nonce,
                "session_id": event["session_id"], "tool_use_id": event["tool_use_id"],
                "tool_call_id": call_id,
                "phase": phase, "input_sha256": input_hash,
                "input_base64": base64.b64encode(raw_input).decode("ascii"),
                "observed_at": _wall_now(), "monotonic_ns": time.monotonic_ns()})
            snapshots = _snapshot_required(observer, workspace, phase, key)
            output = (_boundary_gate_output(observer, boundary_gate, raw_input)
                      if boundary_gate is not None else _hook_output(phase, True))
            sequence_path = observer / "hook-events.jsonl"
            sequence = 0
            if sequence_path.exists():
                sequence = sum(1 for line in sequence_path.read_bytes().splitlines() if line)
            receipt = {"schema": SCHEMA, "run_nonce": run_nonce,
                       "sequence": sequence, "session_id": event["session_id"],
                       "tool_use_id": event["tool_use_id"],
                       "tool_call_id": call_id, "phase": phase,
                       "input_sha256": input_hash,
                       "input_base64": base64.b64encode(raw_input).decode("ascii"),
                       "request_reference": event.get("request_id"),
                       "snapshots": snapshots, "output": output,
                       "observed_at": _wall_now(), "monotonic_ns": time.monotonic_ns()}
            if pair is None:
                pair = {"tool_call_id": call_id, "pre": None, "post": None}
                pairs["pairs"][key] = pair
            pair[slot] = {"phase": phase, "input_sha256": input_hash,
                          "output": output, "sequence": sequence}
            _append(observer / "hook-events.jsonl", receipt)
            _atomic_replace(observer / "pairs.json", _canonical(pairs) + b"\n")
            return output
    except BaseException as exc:
        reason = exc.reason if isinstance(exc, LifecycleFault) else "HOOK_INTERNAL_ERROR"
        detail = exc.detail if isinstance(exc, LifecycleFault) else "%s: %s" % (type(exc).__name__, exc)
        output = _hook_output(phase, False, "%s: %s" % (reason, detail))
        try:
            record_fault(observer, run_nonce, reason, detail)
            with _locked(observer):
                _append(Path(observer) / "hook-fault-events.jsonl", {
                    "schema": SCHEMA, "run_nonce": run_nonce, "phase": phase,
                    "input_sha256": sha256(raw_input) if isinstance(raw_input, bytes) else None,
                    "input_base64": (base64.b64encode(raw_input).decode("ascii")
                                     if isinstance(raw_input, bytes) else None),
                    "output": output, "reason": reason, "detail": detail,
                    "observed_at": _wall_now(), "monotonic_ns": time.monotonic_ns()})
        except BaseException:
            pass
        return output


def current_boot_id():
    path = Path("/proc/sys/kernel/random/boot_id")
    if path.exists():
        return path.read_text().strip()
    try:
        raw = subprocess.check_output(["sysctl", "-n", "kern.boottime"],
                                      text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        raw = "unknown-boot"
    return sha256(raw.encode())


def process_start_ticks(pid):
    try:
        raw = Path("/proc/%d/stat" % pid).read_text()
        return raw[raw.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        try:
            return subprocess.check_output(
                ["ps", "-o", "lstart=", "-p", str(pid)], text=True,
                stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return ""


def _optional_digest(path):
    try:
        return sha256(_read_regular(path))
    except FileNotFoundError:
        return None


def finalize_segment(observer, trace, run_nonce, boot_id, *, run_tag,
                     launch_sha256, lifecycle_helper_sha256, guardian_pid,
                     guardian_start_ticks, guardian_exit_code):
    """Close one segment with a signed, independently derived receipt."""
    observer, trace = Path(observer), Path(trace)
    try:
        check_dispatch(observer, run_nonce, boot_id)
    except LifecycleFault:
        pass
    with _locked(observer):
        identity_raw = _read_regular(observer / "identity.json")
        identity = _strict_json(identity_raw)
        if (identity.get("run_nonce") != run_nonce
                or identity.get("boot_id") != boot_id):
            _raise_fault(observer, run_nonce, "SEGMENT_IDENTITY_MISMATCH",
                         "terminal receipt")
        launch_raw = _read_regular(trace / "lifecycle-launch.json")
        launch = _strict_json(launch_raw)
        verify_signed_record(observer, launch)
        if (set(launch) != LAUNCH_FIELDS
                or sha256(launch_raw) != launch_sha256
                or launch.get("run_nonce") != run_nonce
                or launch.get("run_tag") != run_tag
                or launch.get("lifecycle_helper_sha256") != lifecycle_helper_sha256):
            _raise_fault(observer, run_nonce, "LAUNCH_IDENTITY_MISMATCH",
                         "terminal receipt")
        pairs_raw = _read_regular(observer / "pairs.json")
        expected_raw = _read_regular(observer / "expectations.json")
        batch_tools_raw = _read_regular(observer / "batch-tools.json")
        rejections_raw = _read_regular(observer / "rejections.json")
        subagent_state_raw = _read_regular(observer / "subagent-state.json")
        registry_raw = _read_regular(observer / "registry.json")
        pairs, expected = _strict_json(pairs_raw), _strict_json(expected_raw)
        try:
            expected_ids = set(_expected_tool_owners(expected))
            completed_ids, pending = _completed_tool_ids(pairs)
            batched_ids = _batched_tool_ids(_strict_json(batch_tools_raw))
            rejected_ids = _rejected_tool_ids(_strict_json(rejections_raw))
            subagent_state = _subagent_state(observer)
        except LifecycleFault as exc:
            _fault_unlocked(observer, run_nonce, exc.reason, exc.detail)
            expected_ids, batched_ids, completed_ids, rejected_ids, pending = (
                set(), set(), set(), set(), ["malformed"])
            subagent_state = {"completed": [], "active": {"malformed": True}}
        overlap = completed_ids & rejected_ids
        if overlap or rejected_ids - expected_ids or batched_ids - expected_ids:
            _fault_unlocked(observer, run_nonce, "TOOL_OUTCOME_AMBIGUOUS",
                            "terminal completed/rejected reconciliation")
        missing_batch = expected_ids - batched_ids
        if missing_batch:
            _fault_unlocked(observer, run_nonce, "EXPECTED_TOOL_BATCH_MISSING",
                            sorted(missing_batch)[0])
        if pending or not expected_ids.issubset(completed_ids | rejected_ids):
            _fault_unlocked(observer, run_nonce, "HOOK_PAIR_MISSING",
                            "terminal hook reconciliation")
        if subagent_state.get("active") is not None:
            _fault_unlocked(observer, run_nonce, "SUBAGENT_STOP_MISSING",
                            "terminal child state active")
        fault_path = observer / "fault.json"
        fault_sha = _optional_digest(fault_path)
        fault = _load_json(fault_path, absent=None)
        row = {
            "schema": SCHEMA, "run_tag": run_tag, "run_nonce": run_nonce,
            "completed_at": _wall_now(), "launch_sha256": launch_sha256,
            "observer_dir": str(observer.resolve()),
            "observer_identity_sha256": sha256(identity_raw),
            "lifecycle_helper_sha256": lifecycle_helper_sha256,
            "guardian_pid": guardian_pid,
            "guardian_start_ticks": str(guardian_start_ticks),
            "guardian_exit_code": guardian_exit_code,
            "last_accepted_observation_sha256": _optional_digest(
                observer / "last-accepted-observation.json"),
            "registry_sha256": sha256(registry_raw),
            "pairs_sha256": sha256(pairs_raw),
            "expectations_sha256": sha256(expected_raw),
            "batch_tools_sha256": sha256(batch_tools_raw),
            "rejections_sha256": sha256(rejections_raw),
            "hook_starts_sha256": _optional_digest(observer / "hook-starts.jsonl"),
            "hook_events_sha256": _optional_digest(observer / "hook-events.jsonl"),
            "post_tool_batch_events_sha256": _optional_digest(
                observer / "post-tool-batch-events.jsonl"),
            "subagent_state_sha256": sha256(subagent_state_raw),
            "subagent_events_sha256": _optional_digest(observer / "subagent-events.jsonl"),
            "nested_dispatches_sha256": _optional_digest(
                observer / "nested-dispatches.jsonl"),
            "root_boundary_events_sha256": _optional_digest(
                observer / "root-boundary-events.jsonl"),
            "session_start_events_sha256": _optional_digest(
                observer / "session-start-events.jsonl"),
            "subagent_count": len(subagent_state.get("completed", [])),
            "nested_dispatch_count": sum(
                1 for line in (observer / "nested-dispatches.jsonl").read_bytes().splitlines()
                if line) if (observer / "nested-dispatches.jsonl").exists() else 0,
            "root_boundary_count": sum(
                1 for line in (observer / "root-boundary-events.jsonl").read_bytes().splitlines()
                if line) if (observer / "root-boundary-events.jsonl").exists() else 0,
            "session_start_count": sum(
                1 for line in (observer / "session-start-events.jsonl").read_bytes().splitlines()
                if line) if (observer / "session-start-events.jsonl").exists() else 0,
            "expected_tool_count": len(expected_ids),
            "batched_tool_count": len(expected_ids & batched_ids),
            "completed_tool_count": len(expected_ids & completed_ids),
            "rejected_tool_count": len(expected_ids & rejected_ids),
            "fault_sha256": fault_sha,
            "fault_reason": fault.get("reason") if fault is not None else None,
            "guardian_events_sha256": _optional_digest(
                observer / "guardian-events.jsonl"),
            "status": "FAULT" if fault is not None else "PASS",
        }
        signed = _sign_record(observer, row)
        if set(signed) != RECEIPT_FIELDS:
            raise LifecycleFault("INVALID_RECEIPT", "internal schema")
        _atomic_create(trace / "lifecycle-receipt.json",
                       _canonical(signed) + b"\n")
        return signed


def run_guardian(observer, run_nonce, boot_id, controller_pid,
                 controller_start_ticks, controller_pgid, *, interval_s=1.0):
    while True:
        started = time.monotonic_ns()
        try:
            check_supervision(observer, run_nonce, boot_id)
        except LifecycleFault as exc:
            outcome = {"schema": SCHEMA, "run_nonce": run_nonce,
                       "reason": exc.reason, "detail": exc.detail,
                       "detected_monotonic_ns": started, "signal": "SIGTERM",
                       "controller_pid": controller_pid, "ownership_verified": False,
                       "signal_sent": False}
            try:
                owned = (current_boot_id() == boot_id
                         and process_start_ticks(controller_pid) == str(controller_start_ticks)
                         and os.getpgid(controller_pid) == int(controller_pgid))
                outcome["ownership_verified"] = owned
                if owned:
                    os.kill(controller_pid, signal.SIGTERM)
                    outcome["signal_sent"] = True
            except (OSError, ValueError):
                pass
            outcome["completed_monotonic_ns"] = time.monotonic_ns()
            with _locked(observer):
                _append(Path(observer) / "guardian-events.jsonl", outcome)
            return 2
        elapsed = (time.monotonic_ns() - started) / 1_000_000_000
        time.sleep(max(0.0, interval_s - elapsed))


def _parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--trace", required=True); init.add_argument("--nonce", required=True)
    init.add_argument("--boot-id", default=None); init.add_argument("--predecessor-nonce")
    observe = sub.add_parser("observe")
    observe.add_argument("--observer-dir", required=True); observe.add_argument("--nonce", required=True)
    observe.add_argument("--boot-id", default=None); observe.add_argument("--sequence", required=True, type=int)
    observe.add_argument("--last-request"); observe.add_argument("--last-tool")
    observe.add_argument("--pending-operation"); observe.add_argument("--capability-file")
    check = sub.add_parser("check")
    check.add_argument("--observer-dir", required=True); check.add_argument("--nonce", required=True)
    check.add_argument("--boot-id", default=None)
    hook = sub.add_parser("hook")
    hook.add_argument("--observer-dir", required=True); hook.add_argument("--workspace", required=True)
    hook.add_argument("--nonce", required=True); hook.add_argument("--boot-id", default=None)
    hook.add_argument("--boundary-gate", default=None)
    guardian = sub.add_parser("guardian")
    guardian.add_argument("--observer-dir", required=True); guardian.add_argument("--nonce", required=True)
    guardian.add_argument("--boot-id", default=None); guardian.add_argument("--controller-pid", required=True, type=int)
    guardian.add_argument("--controller-start-ticks", required=True); guardian.add_argument("--controller-pgid", required=True, type=int)
    guardian.add_argument("--interval", type=float, default=1.0)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    boot = args.boot_id or current_boot_id()
    try:
        if args.command == "init":
            observer = init_segment(args.trace, args.nonce, boot,
                                    predecessor_nonce=args.predecessor_nonce)
            result = {"schema": SCHEMA, "observer_dir": str(observer),
                      "capability_file": str(observer / "observation.key"),
                      "boot_id": boot, "run_nonce": args.nonce}
        elif args.command == "observe":
            key_path = Path(args.capability_file) if args.capability_file else Path(args.observer_dir) / "observation.key"
            result = publish_observation(
                args.observer_dir, args.nonce, boot, sequence=args.sequence,
                last_completed_request=args.last_request,
                last_completed_tool=args.last_tool,
                pending_operation=args.pending_operation,
                capability=_read_regular(key_path, 32))
        elif args.command == "check":
            result = check_dispatch(args.observer_dir, args.nonce, boot)
        elif args.command == "hook":
            raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
            result = handle_hook(args.observer_dir, args.workspace, args.nonce, boot, raw,
                                 boundary_gate=args.boundary_gate)
        else:
            return run_guardian(args.observer_dir, args.nonce, boot,
                                args.controller_pid, args.controller_start_ticks,
                                args.controller_pgid, interval_s=args.interval)
        sys.stdout.buffer.write(_canonical(result) + b"\n")
        # Pinned Qwen 0.22.0 discards command output when command execution is
        # classified as failed. A detected lifecycle fault is therefore a
        # successful hook invocation whose parsed output explicitly denies the
        # tool and sets continue=false. The durable external fault remains the
        # authority if Qwen crashes or never invokes the hook.
        return 0
    except (LifecycleFault, OSError, ValueError) as exc:
        sys.stderr.write("lifecycle-supervisor: %s\n" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
