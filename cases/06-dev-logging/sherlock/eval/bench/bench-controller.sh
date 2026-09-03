#!/usr/bin/env bash
set -euo pipefail
BENCH_CONTROLLER_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" exec python3 - "$@" <<'PY'
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse


HERE = Path(os.environ.pop("BENCH_CONTROLLER_HERE"))
TERMINAL = {"DONE", "REJECTED", "RUNNER_FAILED", "BLOCKED", "BLOCKED_UNKNOWN"}
STATUS_FIELDS = {"schema", "controller_id", "phase", "updated_at", "child_run_tag",
                 "child_manifest_sha256", "reason"}
LINK_FIELDS = {"schema", "parent_trace", "parent_identity_sha256", "child_run_tag",
               "child_trace", "child_manifest_sha256", "linked_at", "key_id", "hmac_sha256"}
PROOF_FIELDS = {"pid", "process_start_ticks", "pgid", "boot_id_sha256", "command_sha256"}
PROOF_AUTH_FIELDS = {"schema", "controller_id", "child_run_tag", *PROOF_FIELDS,
                     "key_id", "hmac_sha256"}
LIMIT_NAMES = ("max_upstream_attempts", "max_request_bytes", "max_wall_seconds",
               "max_consecutive_provider_failures")
LIMIT_ENV = {
    "max_upstream_attempts": "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS",
    "max_request_bytes": "SHERLOCK_BUDGET_MAX_REQUEST_BYTES",
    "max_wall_seconds": "SHERLOCK_BUDGET_MAX_WALL_SECONDS",
    "max_consecutive_provider_failures": "SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES",
}
RUNTIME_ENV_ALLOW = {
    "HOME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
}
HEALTH_ENV_ALLOW = {*RUNTIME_ENV_ALLOW, "SHERLOCK_API_KEY"}
TARGET_ENV_ALLOW = {
    *RUNTIME_ENV_ALLOW,
    "BENCH_RUNS", "QWEN_BIN", "SHERLOCK_API_KEY", "SHERLOCK_BASE_URL",
    "SHERLOCK_CONTEXT_WINDOW", "SHERLOCK_MAX_OUTPUT_TOKENS", "SHERLOCK_DATASET", "SHERLOCK_EXPECTED_RETURNED_IDENTITY",
    # The lane's measured generation window and throughput (fix 9). Not on this
    # allowlist means silently scrubbed, which would disarm the launch check on
    # exactly the launcher it was written for.
    "SHERLOCK_GENERATION_WINDOW_S", "SHERLOCK_OUTPUT_TOKENS_PER_S",
    "SHERLOCK_TTFT_RESERVE_S",
    # The gate backstop. run-bench.sh writes this number into the target's
    # `model.sessionTokenLimit`, the only exact client-side check qwen has
    # against the 262,000 request ceiling — `prove` refuses a corporate profile
    # that declares none, and scrubbed here it could never be declared from a
    # paid launcher. It was ADDED (fix 7's repair) as the escape from a
    # generation-window/compaction-reserve conflict; that escape was FALSIFIED
    # by paid run 20260828T204908Z-v42 and is gone. The variable stays for the
    # gate backstop alone, which is the one thing it actually does.
    "SHERLOCK_SESSION_TOKEN_LIMIT",
    "SHERLOCK_MAX_RETRIES", "SHERLOCK_MODEL", "SHERLOCK_PROMPT_FILE",
    "SHERLOCK_REQUEST_TIMEOUT_MS", "SHERLOCK_RESUME_BACKOFF_S", "SHERLOCK_SEED_WORK",
    "SHERLOCK_RESUME_MAX_ATTEMPTS", "SHERLOCK_TIMEOUT", "SHERLOCK_UPSTREAM_LOG",
    "SHERLOCK_UPSTREAM_RETRY", "SHERLOCK_UPSTREAM_RETRY_BASE_MS", "UPSTREAM_LANE_PROXY",
    # Lane-integrity knobs. Passed through so a cold first run against a new
    # provider can turn the cache guard off deliberately (SHERLOCK_CACHE_GUARD=0)
    # instead of someone discovering it cannot be turned off and deleting it.
    "SHERLOCK_CACHE_GUARD", "SHERLOCK_CACHE_MIN_RATE", "SHERLOCK_CACHE_MIN_CALLS",
    # How many wrong-model answers may be discarded and re-issued per call
    # before the lane trips. 0 restores the pre-2026-08-26 abort-on-first
    # behaviour; it is NOT covered by SHERLOCK_UPSTREAM_RETRY, which governs
    # provider ERRORS and is deliberately 0 on the paid launchers.
    "SHERLOCK_SUBSTITUTION_RETRY",
}
MAX_JSON_BYTES = 1024 * 1024
MAX_ARTIFACTS = 4096
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_TRACE_BYTES = 2 * 1024 * 1024 * 1024
MAX_INVENTORY_BYTES = 768 * 1024
MAX_ARTIFACT_PATH_BYTES = 1024
REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class Blocked(Exception):
    def __init__(self, reason, unknown=False):
        super().__init__(reason); self.reason = reason; self.unknown = unknown


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def signed(row, key):
    result = dict(row)
    result["hmac_sha256"] = hmac.new(key, canonical(row), hashlib.sha256).hexdigest()
    return result


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic_replace(path, data, mode=0o600):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path); fsync_dir(path.parent)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def publish_no_replace(path, data, mode=0o600):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False); fsync_dir(path.parent)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def strict_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row: raise ValueError("duplicate JSON key")
        row[key] = value
    return row


def read_bytes(path, limit=MAX_JSON_BYTES):
    path = Path(path); nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise Blocked("NONREGULAR_STATE", True)
        fd = os.open(path, os.O_RDONLY | nofollow)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or
                    (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or
                    opened.st_size > limit):
                raise Blocked("INVALID_STATE", True)
            chunks = []; total = 0
            while total <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - total))
                if not chunk: break
                chunks.append(chunk); total += len(chunk)
            finished = os.fstat(fd)
            if (total > limit or (opened.st_dev, opened.st_ino, opened.st_size) !=
                    (finished.st_dev, finished.st_ino, finished.st_size) or total != finished.st_size):
                raise Blocked("INVALID_STATE", True)
            return b"".join(chunks)
        finally: os.close(fd)
    except Blocked:
        raise
    except OSError as exc:
        raise Blocked("INVALID_STATE", True) from exc


def read_object(path, fields=None, limit=MAX_JSON_BYTES):
    try:
        row = json.loads(read_bytes(path, limit).decode("utf-8"), object_pairs_hook=strict_object)
    except Blocked:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise Blocked("INVALID_STATE", True) from exc
    if not isinstance(row, dict) or (fields is not None and set(row) != fields):
        raise Blocked("INVALID_STATE", True)
    return row


def state_paths(controller):
    return controller / "status.json", controller / "status-events.jsonl"


def persist(controller, controller_id, phase, tag=None, manifest_sha=None, reason=None):
    row = {"schema": 1, "controller_id": controller_id, "phase": phase, "updated_at": now(),
           "child_run_tag": tag, "child_manifest_sha256": manifest_sha, "reason": reason}
    status_path, events_path = state_paths(controller)
    atomic_replace(status_path, canonical(row) + b"\n")
    with open(events_path, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(canonical(row) + b"\n"); handle.flush(); os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    fsync_dir(controller)
    return row


def bootstrap_key(root):
    key_dir = root / "keys"; key_dir.mkdir(mode=0o700, exist_ok=True)
    mode = os.lstat(key_dir).st_mode
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode) or os.path.realpath(key_dir) != str(key_dir):
        raise Blocked("INVALID_KEY_ROOT")
    key_path = key_dir / "controller.key"
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key_fd = None
        try:
            before = os.lstat(key_path)
            info = os.stat(key_path, follow_symlinks=False)
            same = lambda left, right: (left.st_dev, left.st_ino, stat.S_IFMT(left.st_mode)) == (
                right.st_dev, right.st_ino, stat.S_IFMT(right.st_mode))
            if (not same(before, info) or not stat.S_ISREG(before.st_mode) or
                    stat.S_ISLNK(before.st_mode) or info.st_uid != os.geteuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 32):
                raise Blocked("INVALID_CONTROLLER_KEY")
            key_fd = os.open(key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(key_fd)
            if (not same(before, opened) or not stat.S_ISREG(opened.st_mode) or
                    opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600 or
                    opened.st_size != 32):
                raise Blocked("INVALID_CONTROLLER_KEY")
            chunks = []; total = 0
            while total <= 32:
                chunk = os.read(key_fd, 33 - total)
                if not chunk: break
                chunks.append(chunk); total += len(chunk)
            finished = os.fstat(key_fd)
            rebound = os.lstat(key_path)
            if (total != 32 or not same(opened, finished) or not same(finished, rebound) or
                    finished.st_uid != os.geteuid() or stat.S_IMODE(finished.st_mode) != 0o600 or
                    finished.st_size != 32):
                raise Blocked("INVALID_CONTROLLER_KEY")
            return key_path, b"".join(chunks)
        except Blocked:
            raise
        except OSError as exc:
            raise Blocked("INVALID_CONTROLLER_KEY") from exc
        finally:
            if key_fd is not None: os.close(key_fd)
    data = secrets.token_bytes(32)
    try:
        os.write(fd, data); os.fsync(fd)
    finally: os.close(fd)
    fsync_dir(key_dir)
    return key_path, data


def clean_root(name):
    value = os.environ.get(name, "")
    if not value or not os.path.isabs(value): raise Blocked("MISSING_" + name)
    path = Path(value)
    if os.path.realpath(value) != os.path.normpath(value): raise Blocked("NONCANONICAL_" + name)
    path.mkdir(parents=True, exist_ok=True)
    if stat.S_ISLNK(os.lstat(path).st_mode): raise Blocked("SYMLINK_" + name)
    return path


def ensure_authority_boundaries(root, runs):
    root_s, runs_s = str(root), str(runs)
    common = os.path.commonpath((root_s, runs_s))
    if common in (root_s, runs_s): raise Blocked("AUTHORITY_ROOT_OVERLAP")
    authority = []
    for name in ("keys", "records"):
        path = root / name
        if path.exists():
            mode = os.lstat(path).st_mode
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode) or os.path.realpath(path) != str(path):
                raise Blocked("INVALID_AUTHORITY_ROOT")
        authority.append(str(path))
    if os.path.commonpath(authority) in authority:
        raise Blocked("AUTHORITY_ROOT_OVERLAP")


def proc_stat(pid, proc_root, controller=False):
    directory = proc_root / str(pid)
    if not directory.is_dir() and proc_root != Path("/proc") and controller:
        directory = proc_root / "self"
    stat_path = directory / "stat"; command_path = directory / "cmdline"
    try:
        raw_stat = stat_path.read_text(encoding="utf-8")
        if "{pid}" in raw_stat or "{pgid}" in raw_stat:
            raw_stat = raw_stat.replace("{pid}", str(pid)).replace("{pgid}", str(os.getpgid(pid)))
        close = raw_stat.rfind(")")
        values = raw_stat[close + 2:].split()
        state = values[0]; pgid = int(values[2]); start = int(values[19])
        boot = (proc_root / "sys/kernel/random/boot_id").read_bytes().strip()
        command = command_path.read_bytes()
    except (OSError, ValueError, IndexError) as exc:
        raise Blocked("PROC_UNAVAILABLE") from exc
    return state, {"pid": pid, "process_start_ticks": start, "pgid": pgid,
           "boot_id_sha256": digest(boot), "command_sha256": digest(command)}


def proc_snapshot(pid, proc_root, leader=False, controller=False):
    state, row = proc_stat(pid, proc_root, controller)
    if leader and row["pgid"] != pid: raise Blocked("RUNNER_NOT_GROUP_LEADER", True)
    return row


def process_alive(proof, proc_root):
    try:
        os.kill(proof["pid"], 0)
        state, current = proc_stat(proof["pid"], proc_root)
        return state != "Z" and current == proof and current["pgid"] == proof["pid"]
    except (OSError, Blocked):
        return False


def group_members(proof, proc_root):
    members = []
    try: names = os.listdir(proc_root)
    except OSError as exc: raise Blocked("PROC_UNAVAILABLE", True) from exc
    for name in names:
        if not name.isdigit(): continue
        pid = int(name)
        try:
            os.kill(pid, 0)
            state, current = proc_stat(pid, proc_root)
        except (OSError, Blocked):
            continue
        if state != "Z" and current["pgid"] == proof["pgid"]:
            members.append(pid)
    return sorted(members)


def run_tool(path, arguments, env=None):
    prefix = [sys.executable, path] if path.endswith(".py") else [path]
    return subprocess.run(prefix + arguments, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)


def validate_health(path, expected):
    try: row = read_object(path)
    except Exception as exc: raise Blocked("HEALTH_RECEIPT_INVALID") from exc
    try:
        checked = dt.datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        expires = dt.datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    except Exception as exc: raise Blocked("HEALTH_RECEIPT_INVALID") from exc
    current = dt.datetime.now(dt.timezone.utc)
    history = row.get("history")
    matrix_ok = (isinstance(history, list) and len(history) == 3 and
                 type(row.get("reps")) is int and row["reps"] == 1 and
                 all(isinstance(item, dict) for item in history) and
                 all(type(item.get("size_kb")) is int and
                     type(item.get("attempt")) is int for item in history) and
                 {(item["size_kb"], item["attempt"]) for item in history} ==
                 {(100, 1), (250, 1), (400, 1)} and
                 all(type(item.get("status")) is int and item["status"] == 200 and
                     item.get("returned_model") == expected["identity"] for item in history))
    ok = (row.get("schema") == 1 and row.get("verdict") == "HEALTHY" and
          row.get("lane") == expected["lane"] and row.get("provider") == expected["provider"] and
          row.get("requested_model") == expected["model"] and row.get("endpoint") == expected["base"] and
          row.get("shape") == "history" and row.get("tools") == 25 and
          row.get("sizes_kb") == [100, 250, 400] and matrix_ok and
          checked <= current <= expires and (current - checked).total_seconds() <= 900)
    if not ok: raise Blocked("HEALTH_RECEIPT_INVALID")


def verify_link(controller, key):
    link = read_object(controller / "controller-child.json", LINK_FIELDS)
    unsigned = dict(link); supplied = unsigned.pop("hmac_sha256")
    if not isinstance(supplied, str) or not hmac.compare_digest(
            supplied, hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()):
        raise Blocked("LINK_AUTH_INVALID", True)
    if link["parent_trace"] != str(controller) or link["parent_identity_sha256"] != digest(str(controller).encode()):
        raise Blocked("LINK_IDENTITY_INVALID", True)
    if link["key_id"] != digest(key): raise Blocked("LINK_KEY_INVALID", True)
    trace = Path(link["child_trace"])
    manifest = read_object(trace / "run-manifest.json")
    if manifest.get("run_tag") != link["child_run_tag"] or manifest.get("manifest_sha256") != link["child_manifest_sha256"]:
        raise Blocked("LINK_CHILD_INVALID", True)
    return link, trace


def process_proof_unsigned(controller_id, tag, proof, key):
    return {"schema": 1, "controller_id": controller_id, "child_run_tag": tag,
            **proof, "key_id": digest(key)}


def verify_process_proof(controller, controller_id, tag, proof, key):
    row = read_object(controller / "child-process-proof.json", PROOF_AUTH_FIELDS)
    unsigned = dict(row); supplied = unsigned.pop("hmac_sha256")
    expected = process_proof_unsigned(controller_id, tag, proof, key)
    if (unsigned != expected or not isinstance(supplied, str) or not hmac.compare_digest(
            supplied, hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest())):
        raise Blocked("PROCESS_PROOF_AUTH_INVALID", True)
    return proof


def budget_initial(tag, limits):
    return {"schema": 1, "run_tag": tag, "updated_at": now(), "attempts_charged": 0,
            "request_bytes": 0, "consecutive_provider_failures": 0,
            "limits": dict(limits), "verdict": "WITHIN", "reason": None}


def budget_read(path, tag, limits):
    fields = {"schema", "run_tag", "updated_at", "attempts_charged", "request_bytes",
              "consecutive_provider_failures", "limits", "verdict", "reason"}
    try: row = read_object(path, fields)
    except Exception as exc: raise Blocked("BUDGET_STATE_UNKNOWN", True) from exc
    if (row.get("schema") != 1 or row.get("run_tag") != tag or row.get("limits") != limits or
            row.get("verdict") not in ("WITHIN", "EXCEEDED") or
            any(type(row.get(name)) is not int or row[name] < 0 for name in
                ("attempts_charged", "request_bytes", "consecutive_provider_failures")) or
            (row.get("reason") is not None and
             (not isinstance(row["reason"], str) or not REASON_CODE.fullmatch(row["reason"])))):
        raise Blocked("BUDGET_STATE_UNKNOWN", True)
    return row


def write_receipt(trace, tag, manifest_sha, state, limits, elapsed, key, verdict=None, reason=None):
    row = {"schema": 1, "run_tag": tag, "manifest_sha256": manifest_sha, "observed_at": now(),
           "attempts_charged": state["attempts_charged"], "request_bytes": state["request_bytes"],
           "input_tokens": None, "output_tokens": None, "wall_seconds": max(0, int(elapsed)),
           "consecutive_provider_failures": state["consecutive_provider_failures"],
           "limits": dict(limits), "verdict": verdict or state["verdict"],
           "reason": reason if reason is not None else state["reason"], "key_id": digest(key)}
    atomic_replace(trace / "controller-receipt.json", canonical(signed(row, key)) + b"\n")


def terminate_owned(proof, proc_root, child=None):
    evidence = {"sigterm_sent": False, "sigkill_sent": False, "survivors": []}
    def members():
        if child is not None: child.poll()
        return group_members(proof, proc_root)

    live = members()
    if not live: return evidence
    try:
        os.killpg(proof["pgid"], signal.SIGTERM); evidence["sigterm_sent"] = True
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and members(): time.sleep(.05)
    live = members()
    if live:
        try:
            os.killpg(proof["pgid"], signal.SIGKILL); evidence["sigkill_sent"] = True
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and members(): time.sleep(.05)
    evidence["survivors"] = members()
    return evidence


def artifact_rows(trace):
    rows = []
    entries = 0
    total_bytes = 0
    inventory_bytes = 2
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)

    def same(left, right):
        return (left.st_dev, left.st_ino, stat.S_IFMT(left.st_mode)) == (
            right.st_dev, right.st_ino, stat.S_IFMT(right.st_mode))

    def visit(directory_fd, prefix="", depth=0):
        nonlocal entries, total_bytes, inventory_bytes
        if depth > 64: raise Blocked("TRACE_ARTIFACT_LIMIT", True)
        try:
            names = []
            with os.scandir(directory_fd) as listing:
                for entry in listing:
                    if not prefix and entry.name in ("trace-manifest.json", "sealed"):
                        continue
                    entries += 1
                    if entries > MAX_ARTIFACTS: raise Blocked("TRACE_ARTIFACT_LIMIT", True)
                    names.append(entry.name)
            names.sort()
        except OSError as exc: raise Blocked("TRACE_ARTIFACT_UNSAFE", True) from exc
        for name in names:
            relative = prefix + name
            if len(relative.encode("utf-8")) > MAX_ARTIFACT_PATH_BYTES:
                raise Blocked("TRACE_ARTIFACT_LIMIT", True)
            try: before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc: raise Blocked("TRACE_ARTIFACT_UNSAFE", True) from exc
            if stat.S_ISDIR(before.st_mode):
                try: child_fd = os.open(name, os.O_RDONLY | directory_flag | nofollow, dir_fd=directory_fd)
                except OSError as exc: raise Blocked("TRACE_ARTIFACT_UNSAFE", True) from exc
                try:
                    if not same(before, os.fstat(child_fd)): raise Blocked("TRACE_ARTIFACT_RACE", True)
                    visit(child_fd, relative + "/", depth + 1)
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not same(after, os.fstat(child_fd)): raise Blocked("TRACE_ARTIFACT_RACE", True)
                finally: os.close(child_fd)
            elif stat.S_ISREG(before.st_mode):
                try: file_fd = os.open(name, os.O_RDONLY | nofollow, dir_fd=directory_fd)
                except OSError as exc: raise Blocked("TRACE_ARTIFACT_UNSAFE", True) from exc
                try:
                    opened = os.fstat(file_fd)
                    if not same(before, opened): raise Blocked("TRACE_ARTIFACT_RACE", True)
                    if opened.st_size > MAX_ARTIFACT_BYTES or total_bytes + opened.st_size > MAX_TRACE_BYTES:
                        raise Blocked("TRACE_ARTIFACT_LIMIT", True)
                    hasher = hashlib.sha256(); length = 0
                    while True:
                        chunk = os.read(file_fd, 1024 * 1024)
                        if not chunk: break
                        length += len(chunk)
                        if length > MAX_ARTIFACT_BYTES or total_bytes + length > MAX_TRACE_BYTES:
                            raise Blocked("TRACE_ARTIFACT_LIMIT", True)
                        hasher.update(chunk)
                    finished = os.fstat(file_fd)
                    rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if (not same(opened, finished) or not same(finished, rebound) or
                            finished.st_size != length):
                        raise Blocked("TRACE_ARTIFACT_RACE", True)
                finally: os.close(file_fd)
                row = {"path": relative, "bytes": length, "sha256": hasher.hexdigest()}
                inventory_bytes += len(canonical(row)) + 1
                if inventory_bytes > MAX_INVENTORY_BYTES:
                    raise Blocked("TRACE_ARTIFACT_LIMIT", True)
                total_bytes += length; rows.append(row)
            else:
                raise Blocked("TRACE_ARTIFACT_UNSAFE", True)

    try: root_fd = os.open(trace, os.O_RDONLY | directory_flag | nofollow)
    except OSError as exc: raise Blocked("TRACE_ARTIFACT_UNSAFE", True) from exc
    try: visit(root_fd)
    finally: os.close(root_fd)
    return rows


def seal_trace(trace, tag, manifest_sha, key):
    manifest_path = trace / "trace-manifest.json"; sealed_path = trace / "sealed"
    manifest_exists = os.path.lexists(manifest_path)
    sealed_exists = os.path.lexists(sealed_path)
    if sealed_exists and not manifest_exists:
        raise Blocked("TERMINAL_SEAL_MISMATCH", True)
    if manifest_exists:
        verify_terminal(trace, tag, manifest_sha, key, require_marker=sealed_exists)
        if sealed_exists:
            return
    else:
        row = {"schema": 1, "run_tag": tag, "child_manifest_sha256": manifest_sha,
               "artifacts": artifact_rows(trace), "key_id": digest(key)}
        publish_no_replace(manifest_path, canonical(signed(row, key)) + b"\n")
    fd = os.open(sealed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try: os.fsync(fd)
    finally: os.close(fd)
    fsync_dir(trace)


def verify_terminal(trace, tag, manifest_sha, key, require_marker=True):
    row = read_object(trace / "trace-manifest.json")
    supplied = row.get("hmac_sha256"); unsigned = dict(row); unsigned.pop("hmac_sha256", None)
    expected_fields = {"schema", "run_tag", "child_manifest_sha256", "artifacts", "key_id"}
    if (set(unsigned) != expected_fields or row.get("schema") != 1 or row.get("run_tag") != tag or
            row.get("child_manifest_sha256") != manifest_sha or row.get("key_id") != digest(key) or
            not isinstance(supplied, str) or not hmac.compare_digest(
                supplied, hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()) or
            row.get("artifacts") != artifact_rows(trace)):
        raise Blocked("TERMINAL_SEAL_MISMATCH", True)
    if require_marker:
        try: mode = os.lstat(trace / "sealed").st_mode
        except OSError as exc: raise Blocked("TERMINAL_SEAL_MISMATCH", True) from exc
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise Blocked("TERMINAL_SEAL_MISMATCH", True)


def verify_manifest_authority(root, trace, tag, manifest_sha, key_path):
    manifest_path = trace / "run-manifest.json"
    before = read_bytes(manifest_path)
    manifest = read_object(manifest_path)
    if manifest.get("run_tag") != tag or manifest.get("manifest_sha256") != manifest_sha:
        raise Blocked("MANIFEST_AUTH_LOST", True)
    manifest_tool = os.environ.get("SHERLOCK_MANIFEST_TOOL", str(HERE / "run-manifest.py"))
    result = run_tool(manifest_tool, ["verify", str(trace), "--commitment-file",
                      str(root / "records/run-commitments.jsonl"), "--commitment-key", str(key_path)])
    if result.returncode or read_bytes(manifest_path) != before:
        raise Blocked("MANIFEST_AUTH_LOST", True)


def validity_result(trace, tag, manifest_sha, key):
    try: row = read_object(trace / "validity.json")
    except Exception: return False
    supplied = row.get("hmac_sha256"); unsigned = dict(row); unsigned.pop("hmac_sha256", None)
    return (isinstance(supplied, str) and hmac.compare_digest(
        supplied, hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()) and
        row.get("run_tag") == tag and row.get("manifest_sha256") == manifest_sha and row.get("valid") is True)


def parse_configuration():
    required = ("SHERLOCK_CONTROLLER_ROOT", "SHERLOCK_FREE_TEST_COMMAND", "BENCH_RUNS",
                "SHERLOCK_TARGET_COMMAND", "SHERLOCK_HEALTH_COMMAND")
    for name in required:
        if not os.environ.get(name): raise Blocked("MISSING_" + name)
    limits = {}
    for field, name in LIMIT_ENV.items():
        try: value = int(os.environ.get(name, ""))
        except ValueError: raise Blocked("INVALID_" + name)
        if value <= 0: raise Blocked("INVALID_" + name)
        limits[field] = value
    if any(os.environ.get(name) for name in
           ("SHERLOCK_BUDGET_MAX_INPUT_TOKENS", "SHERLOCK_BUDGET_MAX_OUTPUT_TOKENS",
            "SHERLOCK_BUDGET_MAX_TOKENS")):
        raise Blocked("BLOCKED_USAGE_UNAVAILABLE")
    return limits


def acquire_lock(root, resume_id):
    path = root / "paid-lane.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd); raise Blocked("BLOCKED_EXISTING_CONTROLLER")
    nonterminal = []
    try:
        for candidate in root.iterdir():
            if not re.fullmatch(r"controller-[A-Za-z0-9._-]{1,95}", candidate.name):
                continue
            mode = os.lstat(candidate).st_mode
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                raise Blocked("BLOCKED_UNKNOWN", True)
            try:
                phase = read_object(candidate / "status.json", STATUS_FIELDS).get("phase")
            except Blocked:
                if candidate.name != resume_id: raise
                nonterminal.append(candidate.name); continue
            if phase not in TERMINAL:
                nonterminal.append(candidate.name)
    except Blocked:
        os.close(fd); raise
    except OSError as exc:
        os.close(fd); raise Blocked("BLOCKED_UNKNOWN", True) from exc
    if nonterminal and (resume_id is None or nonterminal != [resume_id]):
        os.close(fd); raise Blocked("BLOCKED_EXISTING_CONTROLLER")
    owner_path = root / "paid-lane-owner.json"
    if owner_path.exists():
        try: owner = read_object(owner_path)
        except Exception:
            os.close(fd); raise Blocked("BLOCKED_UNKNOWN", True)
        prior_id = owner.get("controller_id")
        if prior_id and prior_id != resume_id:
            prior_status = root / prior_id / "status.json"
            try: phase = read_object(prior_status, STATUS_FIELDS)["phase"]
            except Exception: phase = None
            if phase not in TERMINAL:
                os.close(fd); raise Blocked("BLOCKED_EXISTING_CONTROLLER")
    return fd, owner_path


def controlled_environment(allowed):
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    path_parts = []
    qwen = os.environ.get("QWEN_BIN", "")
    if "QWEN_BIN" in allowed and qwen and os.path.isabs(qwen):
        path_parts.append(str(Path(qwen).parent))
    home = os.environ.get("HOME", "")
    if home and os.path.isabs(home): path_parts.append(str(Path(home) / ".local/bin"))
    path_parts.extend((str(Path(sys.executable).parent), "/opt/homebrew/bin", "/usr/local/bin",
                       "/usr/bin", "/bin", "/usr/sbin", "/sbin"))
    env["PATH"] = os.pathsep.join(dict.fromkeys(path_parts))
    return env


def target_environment(tag, trace, staged_corpus, limits):
    env = controlled_environment(TARGET_ENV_ALLOW)
    env.update({"SHERLOCK_RUN_TAG": tag, "SHERLOCK_TRACE": str(trace),
                "SHERLOCK_CORPUS": str(staged_corpus),
                "SHERLOCK_REQUIRE_ATTRIBUTION": "1", "SHERLOCK_ALLOW_SUBAGENT": "0"})
    for field, name in LIMIT_ENV.items(): env[name] = str(limits[field])
    return env


def controller_id_new():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "controller-%s-%s" % (stamp, secrets.token_hex(6))


def run_fresh(root, runs, controller_id, controller, key_path, key, limits, lock_fd, owner_path, proc_root):
    tag = "run-" + controller_id.removeprefix("controller-")
    trace = runs / tag
    controller.mkdir(mode=0o700)
    identity = {"schema": 1, "controller_id": controller_id, "parent_trace": str(controller)}
    atomic_replace(controller / "controller-identity.json", canonical(identity) + b"\n")
    persist(controller, controller_id, "FIXING", tag)
    persist(controller, controller_id, "TESTING", tag)
    free = subprocess.run(os.environ["SHERLOCK_FREE_TEST_COMMAND"], shell=True)
    if free.returncode:
        persist(controller, controller_id, "BLOCKED", tag, reason="FREE_TEST_FAILED"); return 1
    try: controller_proof = proc_snapshot(os.getpid(), proc_root, controller=True)
    except Blocked as exc:
        persist(controller, controller_id, "BLOCKED", tag, reason=exc.reason); return 1
    owner = {"schema": 1, "controller_id": controller_id, "child_run_tag": tag, **controller_proof}
    atomic_replace(owner_path, canonical(owner) + b"\n")
    trace.mkdir(mode=0o700)
    persist(controller, controller_id, "HEALTH_CHECKING", tag)
    health = controller / "health-receipt.json"
    lane = os.environ.get("SHERLOCK_LANE", "paid")
    provider = os.environ.get("SHERLOCK_PROVIDER", "linkapi")
    model = os.environ.get("SHERLOCK_MODEL", "")
    identity_name = os.environ.get("SHERLOCK_EXPECTED_RETURNED_IDENTITY", "")
    base = os.environ.get("SHERLOCK_BASE_URL", "")
    health_env = controlled_environment(HEALTH_ENV_ALLOW)
    health_env.update({"SHERLOCK_MODEL": model, "SHERLOCK_BASE_URL": base,
                       "SHERLOCK_ALLOW_SUBAGENT": "0",
                       "PROBE_RECEIPT_PATH": str(health), "PROBE_LANE": lane,
                       "PROBE_PROVIDER": provider, "PROBE_EXPECTED_RETURNED_MODEL": identity_name,
                       "PROBE_BASE_URL": base, "PROBE_ENDPOINT_LABEL": base,
                       "PROBE_REPS": "1", "PROBE_SIZES_KB": "100 250 400",
                       "PROBE_SHAPE": "history", "PROBE_TOOLS": "25"})
    result = subprocess.run(os.environ["SHERLOCK_HEALTH_COMMAND"], shell=True, env=health_env)
    try:
        if result.returncode: raise Blocked("HEALTH_COMMAND_FAILED")
        validate_health(health, {"lane": lane, "provider": provider, "model": model,
                                 "identity": identity_name, "base": base})
    except Blocked as exc:
        persist(controller, controller_id, "BLOCKED", tag, reason=exc.reason); return 1
    manifest_tool = os.environ.get("SHERLOCK_MANIFEST_TOOL", str(HERE / "run-manifest.py"))
    staged = controller / "staged-corpus"
    stage_args = ["stage", "--source-corpus", os.environ.get("SHERLOCK_CORPUS", ""),
                  "--answer-key", os.environ.get("SHERLOCK_ANSWER_KEY", ""),
                  "--dataset", os.environ.get("SHERLOCK_DATASET", "bench649"),
                  "--destination", str(staged)]
    if run_tool(manifest_tool, stage_args).returncode:
        persist(controller, controller_id, "BLOCKED", tag, reason="MANIFEST_STAGE_FAILED"); return 1
    records = root / "records"; records.mkdir(mode=0o700, exist_ok=True)
    commitment = records / "run-commitments.jsonl"
    create_values = [
        ("run-tag", tag), ("dataset", os.environ.get("SHERLOCK_DATASET", "bench649")),
        ("arm", os.environ.get("SHERLOCK_ARM", "v3")), ("source-corpus", os.environ.get("SHERLOCK_CORPUS", "")),
        ("answer-key", os.environ.get("SHERLOCK_ANSWER_KEY", "")),
        ("renderer", os.environ.get("SHERLOCK_RENDERER", "")),
        ("prompt", os.environ.get("SHERLOCK_PROMPT_FILE", "")),
        ("skill-root", os.environ.get("SHERLOCK_SKILL_ROOT", "")),
        ("runner", str(HERE / "run-bench.sh")), ("scorer", os.environ.get("SHERLOCK_SCORER", "")),
        ("triage-checker", os.environ.get("SHERLOCK_TRIAGE_CHECKER", "")),
        ("stop-checker", os.environ.get("SHERLOCK_STOP_CHECKER", "")),
        ("citation-checker", os.environ.get("SHERLOCK_CITATION_CHECKER", "")),
        ("target-cli", os.environ.get("QWEN_BIN", "")),
        ("target-version", os.environ.get("SHERLOCK_TARGET_VERSION", "")),
        ("requested-model", model), ("provider", provider),
        ("expected-returned-identity", identity_name), ("lane", lane),
        ("health-receipt", str(health)), ("controller-parent", str(controller / "controller-identity.json")),
        ("commitment-file", str(commitment)), ("commitment-key", str(key_path)),
        ("staged-corpus-destination", str(staged))]
    create_args = ["create", str(trace)]
    for name, value in create_values: create_args += ["--" + name, value]
    if run_tool(manifest_tool, create_args).returncode or run_tool(
            manifest_tool, ["verify", str(trace), "--commitment-file", str(commitment),
                            "--commitment-key", str(key_path)]).returncode:
        persist(controller, controller_id, "BLOCKED", tag, reason="MANIFEST_INVALID"); return 1
    try:
        manifest = read_object(trace / "run-manifest.json")
        manifest_sha = manifest["manifest_sha256"]
        if manifest.get("run_tag") != tag or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha): raise ValueError()
        if os.listdir(trace) != ["run-manifest.json"]: raise ValueError()
    except Exception:
        persist(controller, controller_id, "BLOCKED", tag, reason="MANIFEST_INVALID"); return 1
    link_unsigned = {"schema": 1, "parent_trace": str(controller),
        "parent_identity_sha256": digest(str(controller).encode()), "child_run_tag": tag,
        "child_trace": str(trace), "child_manifest_sha256": manifest_sha,
        "linked_at": now(), "key_id": digest(key)}
    publish_no_replace(controller / "controller-child.json", canonical(signed(link_unsigned, key)) + b"\n")
    persist(controller, controller_id, "READY", tag, manifest_sha)
    persist(controller, controller_id, "QWEN_RUNNING", tag, manifest_sha)
    command = os.environ["SHERLOCK_TARGET_COMMAND"]
    child = subprocess.Popen(["bash", "-c", "exec " + command], start_new_session=True,
                             env=target_environment(tag, trace, staged, limits))
    ready = trace / ".runner-ready"; deadline = time.monotonic() + 30
    while not ready.is_file() and child.poll() is None and time.monotonic() < deadline: time.sleep(.02)
    if not ready.is_file():
        if child.poll() is None: child.terminate()
        persist(controller, controller_id, "RUNNER_FAILED", tag, manifest_sha, "RUNNER_HANDSHAKE_FAILED")
        return 1
    try: proof = proc_snapshot(child.pid, proc_root, leader=True)
    except Blocked as exc:
        if child.poll() is None: child.terminate()
        persist(controller, controller_id, "BLOCKED_UNKNOWN", tag, manifest_sha, exc.reason); return 1
    try:
        publish_no_replace(trace / "upstream-budget-state.json",
                           canonical(budget_initial(tag, limits)) + b"\n")
        publish_no_replace(trace / "controller-process.json", canonical(proof) + b"\n")
        publish_no_replace(controller / "child-process-proof.json",
                           canonical(signed(process_proof_unsigned(
                               controller_id, tag, proof, key), key)) + b"\n")
    except FileExistsError:
        terminate_owned(proof, proc_root)
        persist(controller, controller_id, "BLOCKED_UNKNOWN", tag, manifest_sha,
                "HANDSHAKE_ARTIFACT_COLLISION")
        return 1
    return monitor_and_finish(root, controller, controller_id, trace, tag, manifest_sha,
                              key_path, key, limits, proc_root, proof, child)


def monitor_and_finish(root, controller, controller_id, trace, tag, manifest_sha,
                       key_path, key, limits, proc_root, proof, child=None):
    started = time.monotonic(); budget_path = trace / "upstream-budget-state.json"
    breach = None; budget = None
    while group_members(proof, proc_root):
        if child is not None: child.poll()
        elapsed = time.monotonic() - started
        try: budget = budget_read(budget_path, tag, limits)
        except Blocked as exc: breach = exc.reason; break
        if budget["verdict"] == "EXCEEDED": breach = budget["reason"] or "BUDGET_EXCEEDED"
        elif elapsed >= limits["max_wall_seconds"]: breach = "MAX_WALL_SECONDS"
        write_receipt(trace, tag, manifest_sha, budget, limits, elapsed, key,
                      "EXCEEDED" if breach else None, breach)
        if breach: break
        time.sleep(.05)
    if not breach:
        try: budget = budget_read(budget_path, tag, limits)
        except Blocked as exc: breach = exc.reason
        else:
            if budget["verdict"] == "EXCEEDED":
                breach = budget["reason"] or "BUDGET_EXCEEDED"
            elif time.monotonic() - started >= limits["max_wall_seconds"]:
                breach = "MAX_WALL_SECONDS"
    if breach:
        phase = "BLOCKED_UNKNOWN" if breach == "BUDGET_STATE_UNKNOWN" else "BLOCKED"
        if budget is not None:
            write_receipt(trace, tag, manifest_sha, budget, limits, time.monotonic() - started,
                          key, "EXCEEDED", breach)
        persist(controller, controller_id, phase, tag, manifest_sha, breach)
        evidence = terminate_owned(proof, proc_root, child)
        atomic_replace(trace / "controller-termination.json", canonical(evidence) + b"\n")
        if evidence["survivors"]: raise Blocked("OWNED_PROCESS_SURVIVED", True)
        if phase == "BLOCKED_UNKNOWN": return 1
        seal_trace(trace, tag, manifest_sha, key); return 1
    rc = child.wait() if child is not None else 0
    budget = budget_read(budget_path, tag, limits)
    write_receipt(trace, tag, manifest_sha, budget, limits, time.monotonic() - started, key)
    # RC 4 = DELIVERED, THEN REFUSED BY ITS OWN GATES. It is not a runner
    # failure and must still be validated and scored: it is the single most
    # informative outcome this eval produces, and it is exactly what the v36
    # winevtx run was. Bucketing it with transport failures would have thrown
    # that run away. 2 (transport) and 3 (no deliverable) still short-circuit.
    RC_GATES_BLOCKING = 4
    if rc == RC_GATES_BLOCKING and (trace / "candidate.json").is_file():
        rc = 0
    if rc != 0 or not (trace / "candidate.json").is_file():
        persist(controller, controller_id, "RUNNER_FAILED", tag, manifest_sha,
                "RUNNER_NONZERO" if rc else "CANDIDATE_MISSING")
        seal_trace(trace, tag, manifest_sha, key); return 1
    persist(controller, controller_id, "VERIFYING", tag, manifest_sha)
    return validate_and_finish(root, controller, controller_id, trace, tag, manifest_sha,
                               key_path, key)


def validate_and_finish(root, controller, controller_id, trace, tag, manifest_sha, key_path, key):
    validator = os.environ.get("SHERLOCK_VALIDATOR_TOOL", str(HERE / "validate-run.py"))
    if not (trace / "validity.json").exists():
        result = run_tool(validator, [str(trace), "--commitment-file", str(root / "records/run-commitments.jsonl"),
                                     "--commitment-key", str(key_path), "--ledger", os.environ.get("SHERLOCK_LEDGER", "")])
        if result.returncode:
            persist(controller, controller_id, "RUNNER_FAILED", tag, manifest_sha, "VALIDATOR_NONZERO")
            seal_trace(trace, tag, manifest_sha, key); return 1
    verify_manifest_authority(root, trace, tag, manifest_sha, key_path)
    accepted = validity_result(trace, tag, manifest_sha, key)
    phase = "DONE" if accepted else "REJECTED"
    persist(controller, controller_id, phase, tag, manifest_sha, None if accepted else "VALIDITY_REJECTED")
    seal_trace(trace, tag, manifest_sha, key)
    return 0 if accepted else 1


def target_contract_probe(argv):
    """Reserve a closed controller entrypoint for the sealed paid probe.

    This parser is intentionally separate from the normal controller: an
    arbitrary executable, configuration root, or resume identifier must never
    be interpreted as a probe instruction.
    """
    if len(argv) not in (4, 6) or tuple(argv[:4:2]) != ("--sealed-input", "--work") or \
            (len(argv) == 6 and argv[4] != "--transport-base-url"):
        print("PROBE_ARGUMENTS", file=sys.stderr)
        return 2
    sealed, work = (Path(argv[1]), Path(argv[3]))
    if not sealed.is_absolute() or not work.is_absolute() or not sealed.is_dir() or os.path.islink(sealed):
        print("PROBE_INPUT", file=sys.stderr)
        return 1
    required = ("target-profile.json", "probe-budget.json", "input-package.json", "fixture")
    for name in required:
        candidate = sealed / name
        try:
            mode = os.lstat(candidate).st_mode
        except OSError:
            print("PROBE_PACKAGE", file=sys.stderr)
            return 1
        if os.path.islink(candidate) or not (stat.S_ISDIR(mode) if name == "fixture" else stat.S_ISREG(mode)):
            print("PROBE_PACKAGE", file=sys.stderr)
            return 1
    try:
        profile = json.loads((sealed / "target-profile.json").read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or not isinstance(profile.get("provider_base_url"), str) or \
                not isinstance(profile.get("requested_model"), str) or \
                not isinstance(profile.get("expected_returned_identity"), str) or \
                not isinstance(profile.get("qwen"), dict) or not isinstance(profile["qwen"].get("cli"), str):
            raise ValueError()
    except (OSError, ValueError, json.JSONDecodeError):
        print("PROBE_PACKAGE", file=sys.stderr)
        return 1
    transport = profile["provider_base_url"]
    if len(argv) == 6:
        try:
            parsed = urlparse(argv[5]); port = parsed.port
        except ValueError:
            parsed = None; port = None
        if (parsed is None or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username is not None or parsed.password is not None or port is None
                or parsed.params or parsed.fragment):
            print("PROBE_TRANSPORT", file=sys.stderr)
            return 1
        transport = argv[5]
    runs = work / "runs"
    trace = runs / "target-contract-probe"
    try:
        runs.mkdir(mode=0o700)
        trace.mkdir(mode=0o700)
        manifest = trace / "run-manifest.json"
        fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical({"schema": 1, "probe_input": str(sealed)}) + b"\n")
            handle.flush(); os.fsync(handle.fileno())
    except (OSError, ValueError):
        print("PROBE_TRACE", file=sys.stderr)
        return 1
    # The probe is sealed input, not a general purpose bench invocation.  Do
    # not let ambient SHERLOCK switches change the model, prompt, arm, retries,
    # settings, or proxy after approval.
    secret_ref = profile.get("secret_ref")
    if not isinstance(secret_ref, str) or not secret_ref or secret_ref not in os.environ:
        print("PROBE_SECRET_REF", file=sys.stderr)
        return 1
    allowed_probe_env = {*RUNTIME_ENV_ALLOW, secret_ref}
    conflicts = sorted(name for name in os.environ if name.startswith("SHERLOCK_") and name not in allowed_probe_env)
    if conflicts:
        print("PROBE_ENV_CONFLICT", file=sys.stderr)
        return 1
    env = controlled_environment(allowed_probe_env)
    env.update({"BENCH_RUNS": str(runs), "SHERLOCK_RUN_TAG": trace.name,
                "SHERLOCK_TRACE": str(trace), "SHERLOCK_CORPUS": str(sealed / "fixture"),
                "SHERLOCK_BASE_URL": transport, "SHERLOCK_MODEL": profile["requested_model"],
                "SHERLOCK_EXPECTED_RETURNED_IDENTITY": profile["expected_returned_identity"],
                "QWEN_BIN": profile["qwen"]["cli"], "SHERLOCK_MAX_RETRIES": "0",
                "SHERLOCK_RESUME_MAX_ATTEMPTS": "0", "SHERLOCK_UPSTREAM_RETRY": "0",
                "SHERLOCK_TARGET_PROBE_MODE": "1", secret_ref: os.environ[secret_ref],
                "SHERLOCK_API_KEY": os.environ[secret_ref],
                "SHERLOCK_PROBE_SEALED_INPUT": str(sealed),
                "SHERLOCK_PROBE_SETTINGS": str(sealed / "corporate-settings.json"),
                "SHERLOCK_PROBE_BUDGET": str(sealed / "probe-budget.json"),
                "SHERLOCK_PROBE_ARM": json.loads((sealed / "input-package.json").read_text(encoding="utf-8")).get("arm", "")})
    done = subprocess.run(["bash", str(HERE / "run-bench.sh"), "v44"],
                          env=env, text=True, capture_output=True, timeout=600)
    print(json.dumps({"trace": str(trace), "runner_exit_code": done.returncode,
                      "stdout_sha256": digest(done.stdout.encode("utf-8"))}, sort_keys=True))
    if done.returncode:
        print("PROBE_RUNNER_FAILED", file=sys.stderr)
    return done.returncode


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--target-contract-probe":
        return target_contract_probe(sys.argv[2:])
    resume_id = None
    if len(sys.argv) == 3 and sys.argv[1] == "--resume": resume_id = sys.argv[2]
    elif len(sys.argv) != 1:
        print("usage: bench-controller.sh [--resume CONTROLLER_ID]", file=sys.stderr); return 2
    try:
        limits = parse_configuration()
        root = clean_root("SHERLOCK_CONTROLLER_ROOT"); runs = clean_root("BENCH_RUNS")
        ensure_authority_boundaries(root, runs)
        key_path, key = bootstrap_key(root)
        lock_fd, owner_path = acquire_lock(root, resume_id)
    except Blocked as exc:
        print(exc.reason, file=sys.stderr); return 1
    try:
        proc_root = Path(os.environ.get("SHERLOCK_PROC_ROOT", "/proc"))
        if resume_id:
            if not re.fullmatch(r"controller-[A-Za-z0-9._-]{1,95}", resume_id): raise Blocked("INVALID_CONTROLLER_ID")
            controller = root / resume_id
            if os.path.realpath(controller) != str(controller) or not controller.is_dir(): raise Blocked("CONTROLLER_MISSING")
            status = read_object(controller / "status.json", STATUS_FIELDS)
            if status["controller_id"] != resume_id: raise Blocked("CONTROLLER_ID_MISMATCH", True)
            if status["phase"] in TERMINAL:
                if status["child_manifest_sha256"] is None:
                    return 1
                try:
                    link, trace = verify_link(controller, key)
                    terminal_exists = (os.path.lexists(trace / "trace-manifest.json") or
                                       os.path.lexists(trace / "sealed"))
                    if terminal_exists:
                        # The terminal manifest authenticates every trace artifact, including
                        # the process proof.  Verify it before consulting child-writable state.
                        seal_trace(trace, link["child_run_tag"], link["child_manifest_sha256"], key)
                    else:
                        proof_path = trace / "controller-process.json"
                        if os.path.lexists(proof_path):
                            proof = read_object(proof_path, PROOF_FIELDS)
                            verify_process_proof(controller, resume_id,
                                                 link["child_run_tag"], proof, key)
                            evidence = terminate_owned(proof, proc_root)
                            if evidence["survivors"]:
                                raise Blocked("OWNED_PROCESS_SURVIVED", True)
                            if evidence["sigterm_sent"] or evidence["sigkill_sent"]:
                                atomic_replace(trace / "controller-termination.json",
                                               canonical(evidence) + b"\n")
                        seal_trace(trace, link["child_run_tag"], link["child_manifest_sha256"], key)
                except Blocked as exc:
                    persist(controller, resume_id, "BLOCKED_UNKNOWN", status["child_run_tag"],
                            status["child_manifest_sha256"], exc.reason); return 1
                return 0 if status["phase"] == "DONE" else 1
            link, trace = verify_link(controller, key)
            if status["phase"] == "VERIFYING":
                if not (trace / "candidate.json").is_file():
                    raise Blocked("CANDIDATE_MISSING", True)
                return validate_and_finish(root, controller, resume_id, trace,
                                           link["child_run_tag"], link["child_manifest_sha256"],
                                           key_path, key)
            if status["phase"] != "QWEN_RUNNING": raise Blocked("RESUME_PHASE_AMBIGUOUS", True)
            proof = read_object(trace / "controller-process.json", PROOF_FIELDS)
            verify_process_proof(controller, resume_id, link["child_run_tag"], proof, key)
            if not group_members(proof, proc_root): raise Blocked("RECORDED_LAUNCH_UNCERTAIN", True)
            owner = {"schema": 1, "controller_id": resume_id, "child_run_tag": link["child_run_tag"],
                     **proc_snapshot(os.getpid(), proc_root, controller=True)}
            atomic_replace(owner_path, canonical(owner) + b"\n")
            return monitor_and_finish(root, controller, resume_id, trace, link["child_run_tag"],
                                      link["child_manifest_sha256"], key_path, key, limits, proc_root, proof)
        controller_id = controller_id_new(); controller = root / controller_id
        return run_fresh(root, runs, controller_id, controller, key_path, key, limits,
                         lock_fd, owner_path, proc_root)
    except Blocked as exc:
        failed_id = resume_id or locals().get("controller_id")
        failed_controller = root / failed_id if failed_id else None
        if failed_controller is not None and failed_controller.is_dir():
            try:
                status = read_object(failed_controller / "status.json", STATUS_FIELDS)
                tag = status.get("child_run_tag"); manifest_sha = status.get("child_manifest_sha256")
            except Exception:
                try:
                    link, _ = verify_link(failed_controller, key)
                    tag = link["child_run_tag"]; manifest_sha = link["child_manifest_sha256"]
                except Exception:
                    tag = None; manifest_sha = None
            try:
                persist(failed_controller, failed_id,
                        "BLOCKED_UNKNOWN" if exc.unknown else "BLOCKED",
                        tag, manifest_sha, exc.reason)
            except Exception:
                pass
        print(exc.reason, file=sys.stderr); return 1
    finally:
        os.close(lock_fd)


raise SystemExit(main())
PY
