#!/usr/bin/env python3
"""Fail-closed admission for an exact-target full paid run."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import sys


ADMISSION_KEYS = {
    "schema", "action", "created_at", "expires_at", "nonce",
    "harness_receipt_sha256", "target_receipt_sha256",
    "target_receipt_checksum_sha256", "target_profile_sha256",
    "full_input_package_sha256", "full_run_budget_sha256",
    "accept_alias_identity_risk",
}
HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 16 * 1024 * 1024
HARNESS_KEYS = {"schema", "accepted", "proof_scope", "matrix_sha256",
                "qualification_manifest_sha256", "trace", "bindings", "free_run",
                "free_model_observations"}
HARNESS_BINDINGS = {
    name + "_sha256" for name in (
        "implementation_commit", "implementation_dirty", "runner", "driver", "proxy",
        "run_manifest_tool", "run_verdict_tool", "test_manifest", "qwen_binary",
        "qwen_version", "arm", "skill_v44", "report_contract", "report_gate_program",
        "report_gate_result", "citation_gate_program", "citation_gate_result",
        "state_gate_program", "state_gate_result", "triage_gate_program",
        "triage_gate_result", "settings", "tool_schema", "input_manifest",
        "terminal_verdict", "seal_trace_tool", "terminal_seal", "gates", "replay",
        "report", "upstream_jsonl", "upstream_bodies", "corpus_tree", "launcher",
        "controller_commitments", "controller_key_id")
}
PROFILE_KEYS = {
    "schema", "provider_base_url", "route", "secret_ref", "requested_model",
    "expected_returned_identity", "identity_mode", "temperature", "top_p",
    "max_output_tokens", "session_token_limit", "cache", "interactive", "qwen",
    "limits", "settings_sha256", "system_prompt_sha256", "skill_sha256",
    "tool_schema_sha256", "gate_sha256", "lane_guard",
}
BUDGET_KEYS = {"schema", "max_upstream_attempts", "max_request_bytes",
               "max_wall_seconds", "max_consecutive_provider_failures",
               "context_window", "max_output_tokens", "session_token_limit",
               "request_timeout_ms"}
DEFAULT_NONCE_ROOT = (
    Path(pwd.getpwuid(os.getuid()).pw_dir)
    / ".local" / "state" / "sherlock" / "paid-admission-nonces"
)


class AdmissionFailure(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(code + ((": " + detail) if detail else ""))


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode() + b"\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    return sha256_bytes(_read_plain(path, "FULL_RUN_NOT_AUTHORIZED"))


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _read_plain(path: Path, code: str) -> bytes:
    path = Path(path)
    try:
        before = path.lstat()
    except OSError as error:
        raise AdmissionFailure(code, "missing artifact") from error
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1):
        raise AdmissionFailure(code, "unsafe artifact")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            inside = os.fstat(handle.fileno())
            data = handle.read(MAX_JSON_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError as error:
        raise AdmissionFailure(code, "unreadable artifact") from error
    identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
    if len(data) > MAX_JSON_BYTES or identity(inside) != identity(after):
        raise AdmissionFailure(code, "unstable artifact")
    return data


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "duplicate JSON key")
        result[key] = value
    return result


def _read_json(path: Path, code: str) -> tuple[dict, bytes]:
    raw = _read_plain(path, code)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except AdmissionFailure:
        raise
    except (UnicodeError, ValueError) as error:
        raise AdmissionFailure(code, "invalid JSON") from error
    if not isinstance(value, dict):
        raise AdmissionFailure(code, "JSON object required")
    return value, raw


def _time(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise AdmissionFailure(code, "invalid time")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AdmissionFailure(code, "invalid time") from error
    if parsed.tzinfo is None:
        raise AdmissionFailure(code, "timezone required")
    return parsed.astimezone(dt.timezone.utc)


def _now(value=None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    if isinstance(value, str):
        return _time(value, "FULL_RUN_NOT_AUTHORIZED")
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "invalid current time")
    return value.astimezone(dt.timezone.utc)


def _bound_asset(root: Path, name: str, expected: object, code: str):
    if not _is_hex(expected):
        raise AdmissionFailure(code, "invalid digest")
    path = root / name
    value, raw = _read_json(path, code)
    if not hmac.compare_digest(sha256_bytes(raw), expected):
        raise AdmissionFailure(code, "digest mismatch")
    return path, value, raw


def _validate_harness(receipt: dict):
    bindings = receipt.get("bindings")
    if (set(receipt) != HARNESS_KEYS or receipt.get("schema") != 2
            or receipt.get("accepted") is not True
            or receipt.get("proof_scope") != "harness_only"
            or not isinstance(bindings, dict) or set(bindings) != HARNESS_BINDINGS
            or any(not _is_hex(value) for value in bindings.values())):
        raise AdmissionFailure("HARNESS_QUALIFICATION_MISSING", "stale receipt")


def _validate_profile_and_identity(profile: dict, receipt: dict):
    if (set(profile) != PROFILE_KEYS or profile.get("schema") != 1
            or any(not _is_hex(profile.get(name)) for name in
                   ("settings_sha256", "system_prompt_sha256", "skill_sha256",
                    "tool_schema_sha256"))
            or not isinstance(profile.get("gate_sha256"), dict)
            or set(profile["gate_sha256"]) != {"reportcheck", "citecheck",
                                                "statecheck", "triagecheck"}
            or any(not _is_hex(value) for value in profile["gate_sha256"].values())):
        raise AdmissionFailure("INPUTS_INCOMPARABLE", "profile schema")
    if receipt.get("target_profile_sha256") is None:
        raise AdmissionFailure("TARGET_CONTRACT_FAILED", "profile unbound")
    requested = profile.get("requested_model")
    expected = profile.get("expected_returned_identity")
    mode = profile.get("identity_mode")
    receipt_requested = receipt.get("requested_model")
    sent = receipt.get("sent_model")
    returned = receipt.get("returned_identities")
    if (not isinstance(requested, str) or not requested
            or not isinstance(expected, str) or not expected
            or receipt_requested != requested or sent != requested):
        raise AdmissionFailure("TARGET_IDENTITY_MISMATCH")
    if not isinstance(returned, list) or not returned or any(
            not isinstance(item, str) or not item for item in returned):
        raise AdmissionFailure("TARGET_IDENTITY_UNVERIFIABLE")
    if mode == "provider_pinned_version":
        if receipt.get("identity_assurance") != mode or any(
                item != expected for item in returned):
            raise AdmissionFailure("TARGET_IDENTITY_MISMATCH")
    elif mode == "alias_unresolved":
        if receipt.get("identity_assurance") != mode or any(
                item != expected for item in returned):
            raise AdmissionFailure("TARGET_IDENTITY_MISMATCH")
    else:
        raise AdmissionFailure("TARGET_IDENTITY_UNVERIFIABLE")


def verify_admission(manifest, now=None) -> dict:
    manifest_path = Path(manifest)
    try:
        if (not manifest_path.is_absolute()
                or manifest_path.resolve(strict=True) != manifest_path):
            raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "aliased manifest path")
    except OSError as error:
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "manifest path") from error
    root = manifest_path.parent
    action, action_raw = _read_json(manifest_path, "FULL_RUN_NOT_AUTHORIZED")
    if (set(action) != ADMISSION_KEYS or action.get("schema") != 1
            or action.get("action") != "full_paid_run"
            or not _is_hex(action.get("nonce"))
            or not isinstance(action.get("accept_alias_identity_risk"), bool)):
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "manifest schema")
    created = _time(action["created_at"], "FULL_RUN_NOT_AUTHORIZED")
    expires = _time(action["expires_at"], "FULL_RUN_NOT_AUTHORIZED")
    current = _now(now)
    if not created <= current < expires:
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "manifest expired")

    harness_path, harness, _ = _bound_asset(
        root, "harness-acceptance.json", action["harness_receipt_sha256"],
        "HARNESS_QUALIFICATION_MISSING")
    _validate_harness(harness)
    target_path, target, target_raw = _bound_asset(
        root, "target-contract-receipt.json", action["target_receipt_sha256"],
        "TARGET_PROBE_NOT_AUTHORIZED")
    checksum_path = root / "target-contract-receipt.json.sha256"
    checksum_raw = _read_plain(checksum_path, "TARGET_PROBE_NOT_AUTHORIZED")
    if (not _is_hex(action["target_receipt_checksum_sha256"])
            or not hmac.compare_digest(sha256_bytes(checksum_raw),
                                       action["target_receipt_checksum_sha256"])
            or checksum_raw != (sha256_bytes(target_raw) + "\n").encode("ascii")):
        raise AdmissionFailure("TARGET_PROBE_NOT_AUTHORIZED", "detached checksum")
    if (target.get("schema") != 1 or target.get("accepted") is not True
            or target.get("proof_scope") != "representative_sample_only"
            or target.get("authenticated") is not True
            or target.get("authority") != "operator-approved-target-probe"
            or not _is_hex(target.get("nonce"))):
        raise AdmissionFailure("TARGET_CONTRACT_FAILED", "target receipt")
    target_created = _time(target.get("created_at"), "TARGET_CONTRACT_FAILED")
    target_expires = _time(target.get("expires_at"), "TARGET_CONTRACT_FAILED")
    if not target_created <= current < target_expires:
        raise AdmissionFailure("TARGET_RECEIPT_EXPIRED")

    profile_path, profile, profile_raw = _bound_asset(
        root, "target-profile.json", action["target_profile_sha256"],
        "INPUTS_INCOMPARABLE")
    if not hmac.compare_digest(target.get("target_profile_sha256", ""),
                               sha256_bytes(profile_raw)):
        raise AdmissionFailure("INPUTS_INCOMPARABLE", "target profile mismatch")
    _validate_profile_and_identity(profile, target)
    settings_path = root / "corporate-settings.json"
    settings_raw = _read_plain(settings_path, "INPUTS_INCOMPARABLE")
    if not hmac.compare_digest(sha256_bytes(settings_raw), profile["settings_sha256"]):
        raise AdmissionFailure("INPUTS_INCOMPARABLE", "settings mismatch")
    shared = {
        "settings_sha256": profile["settings_sha256"],
        "tool_schema_sha256": profile["tool_schema_sha256"],
        "skill_v44_sha256": profile["skill_sha256"],
        "report_gate_program_sha256": profile["gate_sha256"]["reportcheck"],
        "citation_gate_program_sha256": profile["gate_sha256"]["citecheck"],
        "state_gate_program_sha256": profile["gate_sha256"]["statecheck"],
        "triage_gate_program_sha256": profile["gate_sha256"]["triagecheck"],
    }
    if any(harness["bindings"].get(name) != value for name, value in shared.items()):
        raise AdmissionFailure("HARNESS_QUALIFICATION_MISSING", "shared digest mismatch")
    assurance = target["identity_assurance"]
    if assurance == "alias_unresolved" and not action["accept_alias_identity_risk"]:
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "alias risk not accepted")

    inputs_path, inputs, _ = _bound_asset(
        root, "full-input-package.json", action["full_input_package_sha256"],
        "INPUTS_INCOMPARABLE")
    comparison = inputs.get("comparison")
    if inputs.get("schema") != 1 or (comparison is not None and (
            not isinstance(comparison, dict)
            or comparison.get("status") == "incomparable"
            or comparison.get("undeclared_differences") not in (None, []))):
        raise AdmissionFailure("INPUTS_INCOMPARABLE", "input package")
    budget_path, budget, _ = _bound_asset(
        root, "full-run-budget.json", action["full_run_budget_sha256"],
        "TARGET_PROBE_BUDGET")
    if (set(budget) != BUDGET_KEYS or budget.get("schema") != 1
            or any(type(budget.get(name)) is not int or budget[name] <= 0
                   for name in BUDGET_KEYS - {"schema"})):
        raise AdmissionFailure("TARGET_PROBE_BUDGET", "full-run budget")

    return {
        "schema": 1,
        "accepted": True,
        "action": "full_paid_run",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_bytes(action_raw),
        "harness_receipt": str(harness_path),
        "target_receipt": str(target_path),
        "target_receipt_checksum": str(checksum_path),
        "target_profile": str(profile_path),
        "settings": str(settings_path),
        "full_input_package": str(inputs_path),
        "full_run_budget": str(budget_path),
        "action_nonce": action["nonce"],
        "target_receipt_nonce": target["nonce"],
        "identity_assurance": assurance,
    }


def _open_canonical_directory(path: Path, code: str) -> int:
    path = Path(path)
    try:
        before = path.lstat()
        if (not path.is_absolute() or path.resolve(strict=True) != path
                or stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode)):
            raise AdmissionFailure(code, "aliased directory")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                             | getattr(os, "O_NOFOLLOW", 0))
        inside = os.fstat(descriptor)
        after = path.lstat()
    except AdmissionFailure:
        raise
    except OSError as error:
        raise AdmissionFailure(code, "directory unavailable") from error
    identity = lambda row: (row.st_dev, row.st_ino)
    if identity(before) != identity(inside) or identity(inside) != identity(after):
        os.close(descriptor)
        raise AdmissionFailure(code, "directory changed")
    return descriptor


def _fsync_directory_fd(directory_fd: int):
    os.fsync(directory_fd)


def _ensure_durable_directory(path: Path, code: str) -> int:
    """Open an absolute canonical directory, durably creating missing levels."""
    path = Path(path)
    if not path.is_absolute():
        raise AdmissionFailure(code, "nonce root must be absolute")
    cursor = path
    missing = []
    while True:
        try:
            cursor.lstat()
            break
        except FileNotFoundError:
            if cursor.parent == cursor:
                raise AdmissionFailure(code, "nonce root unavailable")
            missing.append(cursor.name)
            cursor = cursor.parent
        except OSError as error:
            raise AdmissionFailure(code, "nonce root unavailable") from error
    directory_fd = _open_canonical_directory(cursor, code)
    try:
        for name in reversed(missing):
            try:
                os.mkdir(name, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            except OSError as error:
                raise AdmissionFailure(code, "nonce root unavailable") from error
            # The child name must survive a crash before any marker is published.
            _fsync_directory_fd(directory_fd)
            try:
                child_fd = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                child = os.fstat(child_fd)
            except OSError as error:
                raise AdmissionFailure(code, "nonce root unavailable") from error
            if not stat.S_ISDIR(child.st_mode):
                os.close(child_fd)
                raise AdmissionFailure(code, "nonce root unavailable")
            os.close(directory_fd)
            directory_fd = child_fd
        try:
            final = path.lstat()
        except OSError as error:
            raise AdmissionFailure(code, "nonce root unavailable") from error
        opened = os.fstat(directory_fd)
        if (path.resolve(strict=True) != path or stat.S_ISLNK(final.st_mode)
                or (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino)):
            raise AdmissionFailure(code, "nonce root changed")
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _write_once_at(directory_fd: int, name: str, value: object):
    if Path(name).name != name or name in (".", ".."):
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "unsafe output name")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def prepare_admission(root, now=None, accept_alias_identity_risk=False) -> dict:
    """Build the exact approval bytes after all pre-approval checks pass."""
    root = Path(root)
    directory_fd = _open_canonical_directory(root, "FULL_RUN_NOT_AUTHORIZED")
    current = _now(now)
    try:
        names = {
            "harness_receipt_sha256": "harness-acceptance.json",
            "target_receipt_sha256": "target-contract-receipt.json",
            "target_receipt_checksum_sha256": "target-contract-receipt.json.sha256",
            "target_profile_sha256": "target-profile.json",
            "full_input_package_sha256": "full-input-package.json",
            "full_run_budget_sha256": "full-run-budget.json",
        }
        hashes = {field: sha256_bytes(_read_plain(root / name, "FULL_RUN_NOT_AUTHORIZED"))
                  for field, name in names.items()}
        target, _ = _read_json(root / "target-contract-receipt.json",
                               "TARGET_PROBE_NOT_AUTHORIZED")
        target_expiry = _time(target.get("expires_at"), "TARGET_CONTRACT_FAILED")
        expires = min(current + dt.timedelta(minutes=30), target_expiry)
        if expires <= current:
            raise AdmissionFailure("TARGET_RECEIPT_EXPIRED")
        action = {
            "schema": 1,
            "action": "full_paid_run",
            "created_at": _iso(current),
            "expires_at": _iso(expires),
            "nonce": secrets.token_hex(32),
            **hashes,
            "accept_alias_identity_risk": bool(accept_alias_identity_risk),
        }
        temporary = ".paid-admission-manifest.pending-" + secrets.token_hex(8)
        _write_once_at(directory_fd, temporary, action)
        _fsync_directory_fd(directory_fd)
        try:
            verify_admission(root / temporary, now=current)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
                _fsync_directory_fd(directory_fd)
            except FileNotFoundError:
                pass
        try:
            _write_once_at(directory_fd, "paid-admission-manifest.json", action)
            _fsync_directory_fd(directory_fd)
        except FileExistsError as error:
            raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "manifest exists") from error
        raw = canonical(action)
        return {"schema": 1, "action": "full_paid_run",
                "manifest": str(root / "paid-admission-manifest.json"),
                "manifest_sha256": sha256_bytes(raw), "expires_at": action["expires_at"],
                "nonce": action["nonce"]}
    finally:
        os.close(directory_fd)


def consume_admission(manifest, now=None, operator_approved_full="") -> dict:
    manifest_path = Path(manifest)
    manifest_raw = _read_plain(manifest_path, "FULL_RUN_NOT_AUTHORIZED")
    manifest_hash = sha256_bytes(manifest_raw)
    if (not _is_hex(operator_approved_full)
            or not hmac.compare_digest(operator_approved_full, manifest_hash)):
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "approval hash")
    result = verify_admission(manifest_path, now=now)
    admission_fd = _open_canonical_directory(manifest_path.parent,
                                             "FULL_RUN_NOT_AUTHORIZED")
    root = DEFAULT_NONCE_ROOT
    try:
        nonce_fd = _ensure_durable_directory(root, "FULL_RUN_NOT_AUTHORIZED")
    except BaseException:
        os.close(admission_fd)
        raise
    lock_flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open("admission.lock", lock_flags | os.O_CREAT | os.O_EXCL,
                          0o600, dir_fd=nonce_fd)
    except FileExistsError:
        lock_fd = os.open("admission.lock", lock_flags, dir_fd=nonce_fd)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        target_marker = result["target_receipt_nonce"] + ".target.used"
        action_marker = result["action_nonce"] + ".action.used"
        try:
            os.stat(target_marker, dir_fd=nonce_fd, follow_symlinks=False)
            raise AdmissionFailure("TARGET_RECEIPT_USED")
        except FileNotFoundError:
            pass
        try:
            os.stat(action_marker, dir_fd=nonce_fd, follow_symlinks=False)
            raise AdmissionFailure("APPROVAL_REPLAYED")
        except FileNotFoundError:
            pass
        marker = {
            "schema": 1,
            "manifest_sha256": manifest_hash,
            "action_nonce": result["action_nonce"],
            "target_receipt_nonce": result["target_receipt_nonce"],
        }
        # Target first: a crash after either durable write requires a new probe.
        _write_once_at(nonce_fd, target_marker, dict(marker, kind="target_receipt"))
        _fsync_directory_fd(nonce_fd)
        _write_once_at(nonce_fd, action_marker, dict(marker, kind="full_run_action"))
        _fsync_directory_fd(nonce_fd)
        accepted = dict(result, consumed=True)
        _write_once_at(admission_fd, "paid-admission.json", accepted)
        _fsync_directory_fd(admission_fd)
    except FileExistsError as error:
        raise AdmissionFailure("FULL_RUN_NOT_AUTHORIZED", "accepted record exists") from error
    finally:
        os.close(lock_fd)
        os.close(nonce_fd)
        os.close(admission_fd)
    return accepted


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    consume = commands.add_parser("consume")
    consume.add_argument("--manifest", required=True)
    consume.add_argument("--operator-approved-full", required=True)
    consume.add_argument("--json", action="store_true")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--accept-alias-identity-risk", action="store_true")
    prepare.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_admission(
                args.root,
                accept_alias_identity_risk=args.accept_alias_identity_risk,
            )
        else:
            result = consume_admission(
                args.manifest,
                operator_approved_full=args.operator_approved_full,
            )
    except AdmissionFailure as error:
        print(error.code, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
