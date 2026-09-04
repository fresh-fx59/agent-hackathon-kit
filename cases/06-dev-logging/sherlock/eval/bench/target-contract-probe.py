#!/usr/bin/env python3
"""Seal, authorize, and audit the bounded paid target-contract probe.

This module deliberately has no default provider action.  The `run` boundary
authorizes and rechecks every sealed input before it obtains a secret or starts
the normal runner; callers inject those actions so the boundary remains testable
without a provider.
"""
import argparse
import gzip
import datetime as dt
import errno
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import math
import re
import time
from urllib.parse import urlparse


HERE = Path(__file__).resolve().parent
GATES = ("reportcheck", "citecheck", "statecheck", "triagecheck")
PROBE_MANIFEST_KEYS = {"schema", "action", "created_at", "expires_at", "nonce",
                       "target_profile_sha256", "probe_budget_sha256", "prompt_sha256",
                       "fixture_manifest_sha256", "input_package_sha256", "rate_snapshot_sha256"}
PROBE_MAX_PROVIDER_CALLS = 6
PROBE_MAX_OUTPUT_TOKENS = 20000
PROBE_SESSION_TOKEN_LIMIT = 230000
DEFAULT_BUDGET = {"schema": 2, "max_provider_calls": PROBE_MAX_PROVIDER_CALLS,
                  "max_prompt_tokens": PROBE_MAX_PROVIDER_CALLS * PROBE_SESSION_TOKEN_LIMIT,
                  "max_completion_tokens": PROBE_MAX_PROVIDER_CALLS * PROBE_MAX_OUTPUT_TOKENS,
                  "max_wall_time_s": 600, "max_estimated_cost_rub": 55.0}


class ProbeFailure(ValueError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(code + (": " + detail if detail else ""))


class PrepareArgs:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUN_MANIFEST = _load("sherlock_run_manifest", "run-manifest.py")
FIXTURE = _load("sherlock_contract_probe_fixture", "contract-probe-fixture.py")
ORACLE = _load("sherlock_target_contract_oracle", "target-contract-oracle.py")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _strict_json(raw, keys=None):
    def reject_duplicates(pairs):
        row = {}
        for key, value in pairs:
            if key in row:
                raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "duplicate JSON key")
            row[key] = value
        return row
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "invalid JSON") from exc
    if keys is not None and (not isinstance(value, dict) or set(value) != set(keys)):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest schema")
    return value


def safe_read_regular(path, limit=1024 * 1024):
    """Read a regular single-link file without following any path component."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    parts = candidate.parts
    # macOS exposes its temporary hierarchy through the OS-owned `/var` alias.
    # Normalize only those fixed platform mount aliases before descriptor walk;
    # do not resolve caller-controlled descendants.
    if len(parts) > 1 and parts[1] in ("var", "tmp") and os.path.islink("/" + parts[1]):
        target = os.readlink("/" + parts[1])
        target_root = Path("/") / target if not os.path.isabs(target) else Path(target)
        candidate = target_root.joinpath(*parts[2:])
        parts = candidate.parts
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    directory = None
    handle = None
    try:
        directory = os.open(parts[0], directory_flags)
        for component in parts[1:-1]:
            if component in ("", ".", ".."):
                raise OSError(errno.EINVAL, "unsafe path")
            next_directory = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        handle = os.open(parts[-1], flags, dir_fd=directory)
        state = os.fstat(handle)
        if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1 or state.st_size > limit:
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "unsafe asset")
        data = os.read(handle, limit + 1)
    except ProbeFailure:
        raise
    except OSError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "unreadable asset") from exc
    finally:
        if handle is not None:
            os.close(handle)
        if directory is not None:
            os.close(directory)
    if len(data) > limit:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "oversize asset")
    return data


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(value):
    if not isinstance(value, str):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "time")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed
    except ValueError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "time") from exc


def _time_text(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _absolute_platform_path(path):
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    parts = candidate.parts
    if len(parts) > 1 and parts[1] in ("var", "tmp") and os.path.islink("/" + parts[1]):
        target = os.readlink("/" + parts[1])
        candidate = (Path("/") / target if not os.path.isabs(target) else Path(target)).joinpath(*parts[2:])
    return candidate


def _open_directory_no_follow(path, create=False):
    """Descriptor-walk a directory; no caller-controlled component is followed."""
    candidate = _absolute_platform_path(path)
    parts = candidate.parts
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(parts[0], flags)
    try:
        for component in parts[1:]:
            if component in ("", ".", ".."):
                raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "unsafe directory")
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    # A created directory is an entry in its parent.  Persist
                    # that entry before it becomes the next descriptor root.
                    os.fsync(fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _atomic_no_replace(path, data):
    path = Path(path)
    parent_fd = None
    created = False
    try:
        parent_fd = _open_directory_no_follow(path.parent, create=True)
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                     0o600, dir_fd=parent_fd)
        created = True
    except FileExistsError as exc:
        raise ProbeFailure("APPROVAL_REPLAYED", "no replacement") from exc
    except OSError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "unsafe publication") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent_fd)
    except BaseException:
        if created:
            try:
                os.unlink(path.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        raise
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _write_relative(root_fd, relative, data, code="TARGET_PROBE_PREPARE"):
    """Publish a sealed leaf below an already-held root, exclusively by fd."""
    parts = relative.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ProbeFailure(code, "unsafe package name")
    held = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try: os.mkdir(part, 0o700, dir_fd=held)
            except FileExistsError: pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=held)
            os.close(held); held = next_fd
        fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                     0o600, dir_fd=held)
        try:
            os.write(fd, data); os.fsync(fd)
        finally: os.close(fd)
        os.fsync(held)
    except FileExistsError as exc:
        raise ProbeFailure(code, "package leaf exists") from exc
    finally:
        os.close(held)


def _read_relative(root_fd, relative, limit=64 * 1024 * 1024):
    parts = relative.split("/"); held = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=held)
            os.close(held); held = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=held)
        try:
            chunks = []; total = 0
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - total))
                if not chunk: break
                chunks.append(chunk); total += len(chunk)
                if total > limit: raise ProbeFailure("TARGET_PROBE_PREPARE", "package leaf too large")
            return b"".join(chunks)
        finally: os.close(fd)
    finally:
        os.close(held)


def _remove_owned(path, expected_sha):
    """Rollback only the exact regular leaf published by this transaction."""
    try:
        if not hmac.compare_digest(sha256(safe_read_regular(path)), expected_sha):
            return
        parent_fd = _open_directory_no_follow(Path(path).parent)
        try:
            os.unlink(Path(path).name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except (OSError, ProbeFailure):
        return


def _unlink_relative(root_fd, relative):
    """Unlink a canonical managed leaf through the audit's held root fd.

    This deliberately does not inspect link count or content: rollback owns the
    *directory entry*, not every inode reference an attacker may have made.
    """
    parts = relative.split("/")
    held = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if part in ("", ".", ".."):
                return
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=held)
            except FileNotFoundError:
                return
            os.close(held); held = next_fd
        try:
            os.unlink(parts[-1], dir_fd=held)
            os.fsync(held)
        except FileNotFoundError:
            pass
    finally:
        os.close(held)


def _remove_receipt_transaction(root_fd, receipt_nonce=None):
    """Remove every canonical inert publication after a managed failure."""
    _unlink_relative(root_fd, "target-contract-receipt.json.sha256")
    _unlink_relative(root_fd, "target-contract-receipt.json")
    if receipt_nonce is not None:
        _unlink_relative(root_fd, "receipt-nonces/%s.json" % receipt_nonce)
    try:
        os.rmdir("receipt-nonces", dir_fd=root_fd)
        os.fsync(root_fd)
    except OSError:
        pass


def _remove_empty_owned_directory(path):
    """Remove only the empty receipt-nonce directory created by this audit."""
    try:
        parent_fd = _open_directory_no_follow(Path(path).parent)
        try:
            state = os.stat(Path(path).name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
                return
            os.rmdir(Path(path).name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except (OSError, ProbeFailure):
        return


def _fsync_directory(path):
    fd = _open_directory_no_follow(path)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_package_no_replace(staging, root):
    """Claim the final package name with mkdir, never rename over a rival root."""
    root = Path(root)
    try:
        os.mkdir(root, 0o700)
    except FileExistsError as exc:
        raise ProbeFailure("TARGET_PROBE_PREPARE", "root already exists") from exc
    _fsync_directory(root.parent)
    try:
        for source in sorted(Path(staging).iterdir(), key=lambda item: item.name):
            target = root / source.name
            mode = os.lstat(source).st_mode
            if stat.S_ISREG(mode):
                _atomic_no_replace(target, safe_read_regular(source, 64 * 1024 * 1024))
            elif stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
                # The staging directory is agent-owned; copy its stable bytes
                # under a fresh O_EXCL root rather than replacing a final path.
                shutil.copytree(source, target, symlinks=False)
                for current, _, _ in os.walk(target, topdown=False):
                    _fsync_directory(current)
            else:
                raise ProbeFailure("TARGET_PROBE_PREPARE", "unsafe staged package")
        _fsync_directory(root)
    except BaseException:
        # A failed package is deliberately left visible only as an incomplete,
        # non-authorizable root; deleting a raced path would be less safe.
        raise


def _tree_digest(path):
    root = Path(path)
    try:
        root_mode = os.lstat(root).st_mode
    except OSError as exc:
        raise ProbeFailure("TARGET_CONTRACT_FAILED", "tree missing") from exc
    if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
        raise ProbeFailure("TARGET_CONTRACT_FAILED", "unsafe tree")
    rows = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories = sorted(dirs)
        filenames = sorted(files)
        for name in directories + filenames:
            child = current_path / name
            mode = os.lstat(child).st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ProbeFailure("TARGET_CONTRACT_FAILED", "unsafe tree member")
        dirs[:] = directories
        for name in filenames:
            child = current_path / name
            relative = child.relative_to(root).as_posix()
            if "__pycache__" in Path(relative).parts or name.endswith((".pyc", ".pyo")):
                continue
            rows.append([relative, sha256(safe_read_regular(child, 64 * 1024 * 1024))])
    return sha256(canonical(rows))


def _fixture_tree_hash(root, outputs, code):
    """Match Task 2's sealed output-tree contract and reject every extra leaf."""
    root = Path(root)
    expected = set(outputs) | {"probe-expectations.json", "probe-fixture-manifest.json"}
    actual = set()
    for current, directories, files in os.walk(root, followlinks=False):
        for directory in directories:
            mode = os.lstat(Path(current) / directory).st_mode
            if stat.S_ISLNK(mode):
                raise ProbeFailure(code, "unsafe fixture member")
        for filename in files:
            child = Path(current) / filename
            mode = os.lstat(child).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or mode and os.lstat(child).st_nlink != 1:
                raise ProbeFailure(code, "unsafe fixture member")
            actual.add(child.relative_to(root).as_posix())
    if actual != expected:
        raise ProbeFailure(code, "fixture tree members")
    hasher = hashlib.sha256()
    for name in sorted(outputs):
        hasher.update(name.encode("utf-8") + b"\0" + sha256(_asset(root / name, code)[0]).encode("ascii") + b"\n")
    return hasher.hexdigest()


def _asset(path, code="TARGET_PROBE_NOT_AUTHORIZED"):
    try:
        data = safe_read_regular(path, 64 * 1024 * 1024)
    except ProbeFailure as exc:
        raise ProbeFailure(code, str(exc)) from exc
    return data, sha256(data)


def validate_test_transport(value):
    """Accept only the explicit loopback transport used by local integration tests.

    This is deliberately separate from the provider URL in a paid profile: it is
    an opt-in test override and must be rejected before an approval nonce can be
    spent.  A prefix test is not a URL security boundary (userinfo and DNS
    suffixes both defeat it).
    """
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "test transport")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "test transport") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None or parsed.password is not None or port is None
            or parsed.params or parsed.fragment):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "test transport")
    return value


def _gate_digests():
    return {name: sha256((HERE.parent.parent / "skills" / "v44" / "tools" / (name + ".py")).read_bytes())
            for name in GATES}


def _profile(args, settings_sha):
    qwen = Path(args.qwen_bin)
    _, qwen_sha = _asset(qwen, "TARGET_PROBE_PREPARE")
    settings = HERE.parent.parent / "measure" / "corporate-settings.py"
    skill = HERE.parent.parent / "skills" / "v44"
    profile = {"schema": 1, "provider_base_url": args.provider_base_url.rstrip("/"),
               "route": args.route, "secret_ref": args.secret_ref,
               "requested_model": args.requested_model,
               "expected_returned_identity": args.expected_returned_identity,
               "identity_mode": args.identity_mode, "temperature": 0, "top_p": 1,
               "max_output_tokens": PROBE_MAX_OUTPUT_TOKENS,
               "session_token_limit": PROBE_SESSION_TOKEN_LIMIT,
               "cache": {"enabled": False}, "interactive": {"enabled": False},
               "qwen": {"cli": str(qwen)}, "limits": {"requests": PROBE_MAX_PROVIDER_CALLS},
               "settings_sha256": settings_sha,
               "system_prompt_sha256": sha256((skill / "SKILL.md").read_bytes()),
               "skill_sha256": _tree_digest(skill),
               "tool_schema_sha256": _tree_digest(HERE.parent.parent / "skills" / "v44" / "tools"), "gate_sha256": _gate_digests(),
               "lane_guard": {"enabled": True}}
    if args.identity_mode == "provider_pinned_version" and (
            args.requested_model != args.expected_returned_identity or
            re.search(r"(?:^|-)\d{6,}(?:$|-)", args.requested_model) is None):
        raise ProbeFailure("TARGET_PROBE_PREPARE", "pinned identity must be an exact version")
    try:
        return RUN_MANIFEST.validate_target_profile(profile)
    except Exception as exc:
        raise ProbeFailure("TARGET_PROBE_PREPARE", "invalid target profile") from exc


def _sealed_rate_snapshot(path, run_tag="target-contract-probe"):
    """Accept only Task 3's exact, current, self-hashed configured rate card."""
    raw = safe_read_regular(path)
    row = _strict_json(raw)
    fields = {"schema", "run_tag", "effective_at", "source", "sha256",
              "prompt_rub_per_token", "completion_rub_per_token"}
    unsigned = {key: row[key] for key in fields - {"sha256"}} if isinstance(row, dict) and set(row) == fields else {}
    if (not isinstance(row, dict) or set(row) != fields or row.get("schema") != 1
            or row.get("run_tag") != run_tag or not isinstance(row.get("source"), str) or not row["source"].strip()
            or not _hex(row.get("sha256"))
            or any(not _finite_number(row.get(key)) for key in ("prompt_rub_per_token", "completion_rub_per_token"))
            or not hmac.compare_digest(row["sha256"], sha256(canonical(unsigned)))):
        raise ProbeFailure("TARGET_PROBE_PREPARE", "rate snapshot")
    try:
        effective = _iso(row["effective_at"])
    except ProbeFailure as exc:
        raise ProbeFailure("TARGET_PROBE_PREPARE", "rate snapshot") from exc
    age = (_now() - effective).total_seconds()
    if age > 86400 or age < -300:
        raise ProbeFailure("TARGET_PROBE_PREPARE", "rate snapshot")
    return raw, row


def prepare(args, secret_reader=None):
    """Build a fresh, immutable probe package; `secret_reader` is intentionally unused."""
    # Do not create even a parent directory until the operator-configured rate
    # bytes have passed the same seven-field contract Task 3 will consume.
    try:
        rate_raw, _rate = _sealed_rate_snapshot(args.rate_snapshot)
    except (OSError, ProbeFailure) as exc:
        raise ProbeFailure("TARGET_PROBE_PREPARE", "rate snapshot") from exc
    root = Path(args.root)
    # Hold both parent and final root for the whole publication: a caller path
    # is only a locator, never an authority after this point.
    try:
        parent_fd = _open_directory_no_follow(root.parent, create=True)
    except (OSError, ProbeFailure) as exc:
        raise ProbeFailure("TARGET_PROBE_PREPARE", "unsafe root parent") from exc
    try:
        try:
            os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise ProbeFailure("TARGET_PROBE_PREPARE", "root already exists") from exc
        root_fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        os.fsync(parent_fd)
        fixture_dir = root / "fixture"
        fixture = FIXTURE.build_fixture(args.source_corpus, fixture_dir, HERE / "probe" / "recipe.json", 4401)
        settings_tool = HERE.parent.parent / "measure" / "corporate-settings.py"
        settings_run = subprocess.run(["python3", str(settings_tool), "emit-run", "--max-retries", "0",
                                       "--max-tokens", str(PROBE_MAX_OUTPUT_TOKENS),
                                       "--session-token-limit", str(PROBE_SESSION_TOKEN_LIMIT),
                                       "--skill-directory", str(HERE.parent.parent / "skills" / "v44")],
                                      text=True, capture_output=True, timeout=30)
        if settings_run.returncode:
            raise ProbeFailure("TARGET_PROBE_PREPARE", "corporate settings")
        settings_bytes = settings_run.stdout.encode("utf-8")
        profile = _profile(args, sha256(settings_bytes))
        files = {"target-profile.json": canonical(profile) + b"\n",
                 "corporate-settings.json": settings_bytes,
                 "probe-budget.json": canonical(DEFAULT_BUDGET) + b"\n",
                 "probe-rate-snapshot.json": rate_raw,
                 "fixture-manifest.json": (fixture_dir / "probe-fixture-manifest.json").read_bytes(),
                 "probe/prompt.txt": (HERE / "probe" / "prompt.txt").read_bytes(),
                 "input-package.json": canonical({"schema": 1, "arm": args.arm,
                     "fixture_tree_sha256": fixture["output_tree_sha256"],
                     "fixture_expectations_sha256": fixture["expectations_sha256"],
                     "settings_sha256": sha256(settings_bytes),
                     "runner_sha256": sha256((HERE / "bench-controller.sh").read_bytes()),
                     "driver_sha256": sha256((HERE / "run-bench.sh").read_bytes()),
                     "proxy_sha256": sha256((HERE.parent.parent / "measure" / "upstream-log-proxy.py").read_bytes()),
                     "oracle_sha256": sha256((HERE / "target-contract-oracle.py").read_bytes()),
                     "audit_sha256": sha256(Path(__file__).read_bytes()),
                     "bench_status_sha256": sha256((HERE / "bench-status.py").read_bytes()),
                     "run_verdict_sha256": sha256((HERE / "run-verdict.py").read_bytes()),
                     "qwen_sha256": _asset(args.qwen_bin, "TARGET_PROBE_PREPARE")[1],
                     "skill_sha256": _tree_digest(HERE.parent.parent / "skills" / "v44"), "gate_sha256": _gate_digests()}) + b"\n"}
        for name, data in files.items():
            _write_relative(root_fd, name, data)
        created = _now()
        ttl = (dt.timedelta(hours=24) if args.identity_mode == "provider_pinned_version" and
               args.requested_model == args.expected_returned_identity else dt.timedelta(minutes=30))
        manifest = {"schema": 1, "action": "target_contract_probe", "created_at": _time_text(created),
                    "expires_at": _time_text(created + ttl), "nonce": secrets.token_hex(32),
                    "target_profile_sha256": sha256(files["target-profile.json"]),
                    "probe_budget_sha256": sha256(files["probe-budget.json"]),
                    "rate_snapshot_sha256": sha256(files["probe-rate-snapshot.json"]),
                    "fixture_manifest_sha256": sha256(files["fixture-manifest.json"]),
                    "input_package_sha256": sha256(files["input-package.json"]),
                    "prompt_sha256": sha256(files["probe/prompt.txt"])}
        _write_relative(root_fd, "probe-manifest.json", canonical(manifest) + b"\n")
        os.fsync(root_fd)
    finally:
        try: os.close(root_fd)
        except UnboundLocalError: pass
        os.close(parent_fd)
    return {"root": str(root), "manifest_sha256": sha256(safe_read_regular(root / "probe-manifest.json"))}


def _consume_nonce(nonce_root, nonce, supplied_hash):
    if not isinstance(nonce, str) or len(nonce) != 64 or not _hex(nonce):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "nonce")
    nonce_root = Path(nonce_root)
    try:
        nonce_fd = _open_directory_no_follow(nonce_root, create=True)
        os.close(nonce_fd)
    except (OSError, ProbeFailure) as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "nonce root") from exc
    _atomic_no_replace(nonce_root / (nonce + ".json"), canonical({"nonce": nonce, "manifest_sha256": supplied_hash}) + b"\n")


def _nonce_is_available(nonce_root, nonce):
    """Reject a known replay before resolving any secret; consume still races safely."""
    try:
        fd = _open_directory_no_follow(nonce_root)
    except FileNotFoundError:
        return
    except (OSError, ProbeFailure) as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "nonce root") from exc
    try:
        try:
            os.stat(nonce + ".json", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ProbeFailure("APPROVAL_REPLAYED", "nonce already consumed")
    finally:
        os.close(fd)


def _record_action_authorization(root, nonce_root, manifest, supplied_hash, authority_root=None):
    """Bind a trace audit to the durable nonce that admitted the run."""
    token = Path(nonce_root) / (manifest["nonce"] + ".json")
    raw = safe_read_regular(token)
    expected = canonical({"nonce": manifest["nonce"], "manifest_sha256": supplied_hash}) + b"\n"
    if not hmac.compare_digest(raw, expected):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "nonce evidence")
    probe_root = Path(root if authority_root is None else authority_root).resolve()
    token = token.resolve()
    _atomic_no_replace(Path(root) / "action-authorization.json", canonical({
        "schema": 1, "action_nonce": manifest["nonce"],
        "manifest_raw_sha256": supplied_hash,
        "nonce_record_path": str(token), "nonce_record_sha256": sha256(raw),
        "nonce_root": str(Path(nonce_root).resolve()), "probe_root": str(probe_root),
        "trace_path": str(probe_root / "probe-work" / "runs" / "target-contract-probe"),
        "bench_status_sha256": sha256((HERE / "bench-status.py").read_bytes()),
        "run_verdict_sha256": sha256((HERE / "run-verdict.py").read_bytes()),
    }) + b"\n")


def authorize(manifest_path, supplied_hash, nonce_root, action="target_contract_probe", *, consume=True):
    raw = safe_read_regular(manifest_path)
    if not _hex(supplied_hash) or not hmac.compare_digest(sha256(raw), supplied_hash):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest hash")
    row = _strict_json(raw, PROBE_MANIFEST_KEYS)
    created, expires = _iso(row.get("created_at")), _iso(row.get("expires_at"))
    if row.get("schema") != 1 or row.get("action") != action or expires <= _now() or expires <= created:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "action or expiry")
    for name in ("target_profile_sha256", "probe_budget_sha256", "fixture_manifest_sha256", "input_package_sha256", "rate_snapshot_sha256"):
        if not _hex(row.get(name)):
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest digest")
    # Authorization expiry is identity-specific.  An alias is evidence only
    # briefly; a provider-pinned requested=sent=returned version can last 24h.
    profile_raw, profile_hash = _asset(Path(manifest_path).parent / "target-profile.json")
    if not hmac.compare_digest(profile_hash, row["target_profile_sha256"]):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "profile binding")
    profile = _strict_json(profile_raw)
    mode = profile.get("identity_mode")
    maximum = dt.timedelta(hours=24) if mode == "provider_pinned_version" else dt.timedelta(minutes=30)
    if mode not in {"provider_pinned_version", "alias_unresolved"} or expires - created > maximum:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "identity expiry")
    if mode == "provider_pinned_version" and profile.get("requested_model") != profile.get("expected_returned_identity"):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "pinned identity")
    package = _verify_package(Path(manifest_path).parent, row)
    for field, path in (("bench_status_sha256", HERE / "bench-status.py"),
                        ("run_verdict_sha256", HERE / "run-verdict.py")):
        if not hmac.compare_digest(package["input-package.json"][field], _asset(path)[1]):
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "program binding")
    if consume:
        try:
            _consume_nonce(nonce_root, row["nonce"], supplied_hash)
            _record_action_authorization(Path(manifest_path).parent, nonce_root, row, supplied_hash)
        except ProbeFailure as exc:
            if exc.code == "APPROVAL_REPLAYED":
                raise
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "nonce") from exc
    return row


def _verify_package(root, manifest, code="TARGET_PROBE_NOT_AUTHORIZED"):
    names = {"target-profile.json": "target_profile_sha256", "probe-budget.json": "probe_budget_sha256",
             "probe-rate-snapshot.json": "rate_snapshot_sha256",
             "fixture-manifest.json": "fixture_manifest_sha256", "input-package.json": "input_package_sha256"}
    values = {}
    for name, field in names.items():
        raw, digest = _asset(Path(root) / name, code)
        if not hmac.compare_digest(digest, manifest[field]):
            raise ProbeFailure(code, "sealed asset mismatch")
        values[name] = _strict_json(raw)
    try:
        RUN_MANIFEST.validate_target_profile(values["target-profile.json"])
    except Exception as exc:
        raise ProbeFailure(code, "target profile invalid") from exc
    if values["probe-budget.json"] != DEFAULT_BUDGET:
        raise ProbeFailure(code, "probe budget invalid")
    try:
        _sealed_rate_snapshot(Path(root) / "probe-rate-snapshot.json")
    except ProbeFailure as exc:
        raise ProbeFailure(code, "rate snapshot invalid") from exc
    package = values["input-package.json"]
    expected_package = {"schema", "arm", "fixture_tree_sha256", "fixture_expectations_sha256",
                        "settings_sha256", "runner_sha256", "driver_sha256", "proxy_sha256",
                        "oracle_sha256", "audit_sha256", "bench_status_sha256", "run_verdict_sha256",
                        "qwen_sha256", "skill_sha256", "gate_sha256"}
    if not isinstance(package, dict) or set(package) != expected_package or package.get("schema") != 1 or \
            not isinstance(package.get("arm"), str) or not _hex(package.get("fixture_tree_sha256")) or \
            not _hex(package.get("fixture_expectations_sha256")) or \
            any(not _hex(package.get(name)) for name in ("settings_sha256", "runner_sha256", "driver_sha256", "proxy_sha256", "oracle_sha256", "audit_sha256", "qwen_sha256", "skill_sha256")) or \
            not isinstance(package.get("gate_sha256"), dict) or set(package["gate_sha256"]) != set(GATES) or \
            any(not _hex(value) for value in package["gate_sha256"].values()):
        raise ProbeFailure(code, "input package invalid")
    fixture_manifest = values["fixture-manifest.json"]
    if not hmac.compare_digest(_asset(Path(root) / "probe" / "prompt.txt", code)[1], manifest["prompt_sha256"]):
        raise ProbeFailure(code, "probe prompt changed")
    if type(fixture_manifest) is not dict or fixture_manifest.get("schema") != 1 or \
            type(fixture_manifest.get("outputs")) is not dict or \
            not isinstance(fixture_manifest.get("expectations"), str):
        raise ProbeFailure(code, "fixture manifest invalid")
    fixture_root = Path(root) / "fixture"
    for name, expected in fixture_manifest["outputs"].items():
        if not isinstance(name, str) or not isinstance(expected, dict) or not _hex(expected.get("sha256")):
            raise ProbeFailure(code, "fixture manifest invalid")
        _, actual = _asset(fixture_root / name, code)
        if not hmac.compare_digest(actual, expected["sha256"]):
            raise ProbeFailure(code, "fixture changed")
    expectation = fixture_root / fixture_manifest["expectations"]
    _, expectation_sha = _asset(expectation, code)
    if not _hex(fixture_manifest.get("expectations_sha256")) or not hmac.compare_digest(expectation_sha, fixture_manifest["expectations_sha256"]):
        raise ProbeFailure(code, "fixture expectations changed")
    fixture_tree = _fixture_tree_hash(fixture_root, fixture_manifest["outputs"], code)
    if not hmac.compare_digest(fixture_tree, package["fixture_tree_sha256"]) or \
            not hmac.compare_digest(expectation_sha, package["fixture_expectations_sha256"]):
        raise ProbeFailure(code, "fixture tree changed")
    if not hmac.compare_digest(_asset(Path(root) / "corporate-settings.json", code)[1], package["settings_sha256"]) or \
            not hmac.compare_digest(package["settings_sha256"], values["target-profile.json"]["settings_sha256"]):
        raise ProbeFailure(code, "settings changed")
    dependencies = {
        "runner_sha256": HERE / "bench-controller.sh", "driver_sha256": HERE / "run-bench.sh",
        "proxy_sha256": HERE.parent.parent / "measure" / "upstream-log-proxy.py",
        "oracle_sha256": HERE / "target-contract-oracle.py", "audit_sha256": Path(__file__),
        "bench_status_sha256": HERE / "bench-status.py", "run_verdict_sha256": HERE / "run-verdict.py",
    }
    for field, path in dependencies.items():
        if not hmac.compare_digest(_asset(path, code)[1], package[field]):
            raise ProbeFailure(code, "stable dependency changed")
    if not hmac.compare_digest(_asset(values["target-profile.json"]["qwen"]["cli"], code)[1], package["qwen_sha256"]) or \
            not hmac.compare_digest(_tree_digest(HERE.parent.parent / "skills" / "v44"), package["skill_sha256"]):
        raise ProbeFailure(code, "stable dependency changed")
    return values


def run(manifest_path, supplied_hash, nonce_root, *, secret_reader, proxy_starter, runner,
        transport_base_url=None):
    """Cross the only contact boundary after approval and all sealed-byte checks."""
    work = Path(manifest_path).parent / "probe-work"
    try:
        if transport_base_url is not None:
            validate_test_transport(transport_base_url)
        manifest = authorize(manifest_path, supplied_hash, nonce_root, consume=False)
        root = Path(manifest_path).parent
        package = _verify_package(root, manifest)
        # The controller would reject these deterministically; keep that
        # refusal on this side of the one-way action token too.
        secret_ref = package["target-profile.json"]["secret_ref"]
        allowed = {"SHERLOCK_API_KEY", secret_ref}
        conflicts = [key for key in os.environ if key.startswith("SHERLOCK_") and key not in allowed]
        if conflicts:
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "ambient controller input")
        if os.path.lexists(work):
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "work already exists")
    # Freeze a private, no-callback-visible copy before one-way approval use.
        work.mkdir(mode=0o700)
        sealed = work / "sealed-input"
        sealed.mkdir(mode=0o700)
        for name in ("target-profile.json", "corporate-settings.json", "probe-budget.json", "probe-rate-snapshot.json", "fixture-manifest.json", "input-package.json", "probe-manifest.json", "probe/prompt.txt"):
            (sealed / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, sealed / name)
        shutil.copytree(root / "fixture", sealed / "fixture", symlinks=False)
        _verify_package(sealed, manifest)
        _nonce_is_available(nonce_root, manifest["nonce"])
    # All knowable validation, snapshots, and secret availability precede the
    # one-way nonce consumption.  Originals are never used after this point.
        profile = _strict_json(_asset(sealed / "target-profile.json")[0])
        secret = secret_reader(profile["secret_ref"])
        _verify_package(sealed, manifest)
        _consume_nonce(nonce_root, manifest["nonce"], supplied_hash)
        _record_action_authorization(sealed, nonce_root, manifest, supplied_hash, authority_root=root)
    # Secret resolution is the only allowed operation before the proxy.  Treat
    # callbacks as hostile: validate the owned snapshot immediately afterwards.
        _verify_package(sealed, manifest)
        profile = _strict_json(_asset(sealed / "target-profile.json")[0])
        proxy = proxy_starter(profile, secret, sealed / "probe-budget.json")
        # The proxy boundary is external to the sealed package.  Treat it as
        # hostile and rehash the immutable snapshot immediately before its
        # pathnames become runner input.
        _verify_package(sealed, manifest)
        return runner(profile_path=sealed / "target-profile.json", fixture=sealed / "fixture",
                      budget_path=sealed / "probe-budget.json", rate_snapshot_path=sealed / "probe-rate-snapshot.json",
                      work=work, proxy=proxy, retries=0)
    except ProbeFailure as exc:
        terminal_root = work if work.is_dir() else Path(manifest_path).parent
        if not os.path.lexists(terminal_root / "probe-result.json"):
            _write_result(terminal_root, {"schema": 1, "accepted": False, "checked_at": _time_text(_now()),
                                          "failure": exc.code, "detail": str(exc)})
        raise
    except Exception as exc:
        # Timeout and unexpected local runner errors are terminal observations,
        # never a reason to omit the one result artifact after nonce use.
        terminal_root = work if work.is_dir() else Path(manifest_path).parent
        failure = ProbeFailure("TARGET_CONTRACT_FAILED", type(exc).__name__)
        if not os.path.lexists(terminal_root / "probe-result.json"):
            _write_result(terminal_root, {"schema": 1, "accepted": False, "checked_at": _time_text(_now()),
                                          "failure": failure.code, "detail": str(failure)})
        raise failure from exc


def audit_identity(mode, expected, returned):
    if mode not in ("provider_pinned_version", "alias_unresolved") or not isinstance(expected, str) or not expected:
        raise ProbeFailure("TARGET_IDENTITY_UNVERIFIABLE")
    if not isinstance(returned, list) or not returned or any(not isinstance(item, str) or not item for item in returned):
        raise ProbeFailure("TARGET_IDENTITY_UNVERIFIABLE")
    if any(item != expected for item in returned):
        raise ProbeFailure("TARGET_IDENTITY_MISMATCH")
    return mode


def _finite_number(value):
    return type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _strict_budget(row):
    fields = {"schema", "run_tag", "updated_at", "limits", "rate_snapshot", "budget_assurance",
              "projected", "observed", "completed_overshoot", "observed_usage_unknown",
              "completed_attempt_ids", "verdict", "reason"}
    counters = ("provider_calls", "prompt_tokens", "completion_tokens")
    limits = ("max_provider_calls", "max_prompt_tokens", "max_completion_tokens", "max_wall_time_s", "max_estimated_cost_rub")
    if not isinstance(row, dict) or set(row) != fields or row.get("schema") != 2 or \
            row.get("budget_assurance") != "client_pre_dispatch" or set(row.get("limits", {})) != set(limits) or \
            not all(_finite_number(row["limits"][key]) for key in limits) or \
            not isinstance(row.get("run_tag"), str) or not row["run_tag"] or \
            not isinstance(row.get("rate_snapshot"), dict):
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    try:
        updated = _iso(row["updated_at"]); effective = _iso(row["rate_snapshot"].get("effective_at"))
    except ProbeFailure as exc:
        raise ProbeFailure("TARGET_PROBE_BUDGET") from exc
    rate = row["rate_snapshot"]
    rate_fields = {"schema", "run_tag", "effective_at", "source", "sha256", "prompt_rub_per_token", "completion_rub_per_token"}
    unsigned = {key: rate[key] for key in rate_fields - {"sha256"}} if set(rate) == rate_fields else {}
    if (set(rate) != rate_fields or rate.get("schema") != 1 or rate.get("run_tag") != row["run_tag"]
            or not isinstance(rate.get("source"), str) or not rate["source"] or not _hex(rate.get("sha256"))
            or any(not _finite_number(rate.get(key)) for key in ("prompt_rub_per_token", "completion_rub_per_token"))
            or not hmac.compare_digest(rate["sha256"], sha256(canonical(unsigned)))
            or updated > _now() + dt.timedelta(minutes=5) or effective > _now() + dt.timedelta(minutes=5)
            or _now() - effective > dt.timedelta(hours=24)):
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    expected_limits = {key: DEFAULT_BUDGET[key] for key in limits}
    if row["limits"] != expected_limits:
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    if any(not isinstance(row.get(group), dict) for group in ("projected", "observed", "completed_overshoot")) or \
            set(row["projected"]) != set(counters + ("wall_time_s", "estimated_cost_rub")) or \
            set(row["observed"]) != set(counters) or set(row["completed_overshoot"]) != set(counters) or \
            not all(_finite_number(row["projected"][key]) for key in row["projected"]) or \
            not all(type(row[group][key]) is int and row[group][key] >= 0 for group in ("observed", "completed_overshoot") for key in counters):
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    if any(row["projected"][key] > row["limits"]["max_" + key] for key in ("provider_calls", "prompt_tokens", "completion_tokens", "wall_time_s", "estimated_cost_rub")):
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    attempts = row.get("completed_attempt_ids")
    if (type(row.get("observed_usage_unknown")) is not int or row["observed_usage_unknown"] < 0
            or not isinstance(attempts, list) or len(set(attempts)) != len(attempts)
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}\.a[1-9][0-9]*", value) is None for value in attempts)
            or row["observed"]["provider_calls"] != len(attempts)
            or row["observed_usage_unknown"] > row["observed"]["provider_calls"]
            or row["projected"]["provider_calls"] < row["observed"]["provider_calls"]
            or any(row["completed_overshoot"][key] != max(row["observed"][key] - row["limits"]["max_" + key], 0) for key in counters)
            or row.get("verdict") not in {"WITHIN", "EXCEEDED"}
            or (row["verdict"] == "WITHIN" and (any(row["completed_overshoot"].values()) or row.get("reason") is not None))
            or (row["verdict"] == "EXCEEDED" and (not any(row["completed_overshoot"].values()) or not isinstance(row.get("reason"), str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["reason"]) is None))):
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    return row


def _write_result(trace, result, root_fd=None):
    path = Path(trace) / "probe-result.json"
    try:
        if root_fd is None:
            _atomic_no_replace(path, canonical(result) + b"\n")
        else:
            _write_relative(root_fd, "probe-result.json", canonical(result) + b"\n", "TARGET_CONTRACT_FAILED")
        return True
    except ProbeFailure:
        # A sealed result exists; never overwrite an earlier attempt's observation.
        return False


def _audit_authorization(trace, manifest, raw_manifest):
    """Require the durable action token that admitted this exact manifest."""
    raw, _ = _asset(Path(trace) / "action-authorization.json", "TARGET_CONTRACT_FAILED")
    row = _strict_json(raw)
    required = {"schema", "action_nonce", "manifest_raw_sha256",
                "nonce_record_path", "nonce_record_sha256", "nonce_root", "probe_root", "trace_path",
                "bench_status_sha256", "run_verdict_sha256"}
    expected_root = Path(trace).resolve().parents[2]
    expected_trace = expected_root / "probe-work" / "runs" / "target-contract-probe"
    if set(row) != required or row.get("schema") != 1 \
            or row.get("action_nonce") != manifest.get("nonce") \
            or row.get("manifest_raw_sha256") != sha256(raw_manifest) \
            or row.get("probe_root") != str(expected_root) or row.get("trace_path") != str(expected_trace) \
            or not _hex(row.get("nonce_record_sha256")) or not isinstance(row.get("nonce_root"), str) \
            or row.get("nonce_record_path") != str((Path(row["nonce_root"]) / (manifest["nonce"] + ".json")).resolve()) \
            or row.get("bench_status_sha256") != _asset(HERE / "bench-status.py")[1] \
            or row.get("run_verdict_sha256") != _asset(HERE / "run-verdict.py")[1]:
        raise ProbeFailure("TARGET_CONTRACT_FAILED", "action authorization")
    nonce_raw = safe_read_regular(row["nonce_record_path"])
    wanted = canonical({"nonce": manifest["nonce"], "manifest_sha256": sha256(raw_manifest)}) + b"\n"
    if not hmac.compare_digest(nonce_raw, wanted) or not hmac.compare_digest(sha256(nonce_raw), row["nonce_record_sha256"]):
        raise ProbeFailure("TARGET_CONTRACT_FAILED", "action authorization")
    return row, raw


def _real_gates(trace, report, fixture):
    """Run the bound v44 programs; caller supplied gate summaries are never evidence."""
    tools = HERE.parent.parent / "skills" / "v44" / "tools"
    profile = _strict_json(_asset(Path(trace) / "target-profile.json", "TARGET_CONTRACT_FAILED")[0])
    for name in GATES:
        _, digest = _asset(tools / (name + ".py"), "TARGET_CONTRACT_FAILED")
        if not hmac.compare_digest(digest, profile["gate_sha256"][name]):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "bound gate changed")
    citation = Path(tempfile.mkdtemp(prefix=".probe-citations-"))
    try:
        manifest = _strict_json(_asset(Path(trace) / "fixture-manifest.json", "TARGET_CONTRACT_FAILED")[0])
        for name in manifest["outputs"]:
            destination = citation / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixture / name, destination)
        commands = {
        "reportcheck": ["python3", str(tools / "reportcheck.py"), str(report), "--json"],
        "citecheck": ["python3", str(tools / "citecheck.py"), str(report), "--corpus", str(citation), "--require-quote", "--json"],
        "statecheck": ["python3", str(tools / "statecheck.py"), "--corpus", str(fixture), "--report", str(report), "--json"],
        }
        rows = {}
        for name, command in commands.items():
            done = subprocess.run(command, text=True, capture_output=True, timeout=30)
            try:
                payload = _strict_json(done.stdout.encode("utf-8"))
            except ProbeFailure as exc:
                raise ProbeFailure("TARGET_CONTRACT_FAILED", name + " output") from exc
            if done.returncode != 0 or type(payload.get("blocking")) is not int or payload["blocking"] != 0:
                raise ProbeFailure("TARGET_CONTRACT_FAILED", name + " blocking")
            rows[name] = {"returncode": done.returncode, "raw_sha256": sha256(done.stdout.encode()), "payload": payload}
    # Triage owns worklist state. A normal run must preserve it under trace/work.
        worklist = Path(trace) / "work" / "worklist.tsv"
        if not worklist.is_file():
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "triage worklist missing")
        # `triagecheck` resolves every worklist citation against a disposable
        # gate corpus.  Keep the sealed fixture immutable: its ledger input is
        # an audit-side copy, never an undeclared package member.
        gate_corpus = citation / "triage-corpus"
        shutil.copytree(fixture, gate_corpus)
        (gate_corpus / "ledger.log").write_text(
            "\n".join(["2026-08-30T10:00:00Z INFO routine health check ok"] * 80 +
                      ["2026-08-30T10:00:01Z ALERT singular contract ledger anomaly"]) + "\n",
            encoding="utf-8")
        done = subprocess.run(["python3", str(tools / "triagecheck.py"), "--worklist", str(worklist),
                               "--corpus", str(gate_corpus), "--json"], text=True, capture_output=True, timeout=30)
        payload = _strict_json(done.stdout.encode("utf-8"))
        if done.returncode != 0 or type(payload.get("blocking")) is not int or payload["blocking"] != 0:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "triagecheck blocking")
        rows["triagecheck"] = {"returncode": done.returncode, "raw_sha256": sha256(done.stdout.encode()), "payload": payload}
        return rows
    finally:
        shutil.rmtree(citation)


def _gzip_json(path):
    """Read one raw Task 3 capture without granting it path traversal rights."""
    raw, raw_hash = _asset(path, "TARGET_CONTRACT_FAILED")
    try:
        body = gzip.decompress(raw)
    except (OSError, EOFError) as exc:
        raise ProbeFailure("TARGET_CONTRACT_FAILED", "gzip capture") from exc
    return _strict_json(body), raw_hash


def _task3_observations(trace):
    """Normalize Task 3's durable JSONL/capture contract for the audit.

    The completion journal, rather than a controller-written summary, is the
    authority for which paid actions completed.  Each journal row is tied to
    precisely the request and response bytes the proxy captured.
    """
    journal_raw, journal_hash = _asset(trace / "upstream-completed.jsonl", "TARGET_CONTRACT_FAILED")
    try:
        lines = journal_raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ProbeFailure("TARGET_CONTRACT_FAILED", "completion journal") from exc
    if not lines:
        raise ProbeFailure("TARGET_PROBE_BUDGET", "empty completion journal")
    rows = []
    for line in lines:
        if not line:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "completion journal blank row")
        row = _strict_json(line.encode("utf-8"))
        required = {"request_id", "action_attempt_id", "action_contact_completed",
                    "body_request_file", "body_response_file", "requested_model",
                    "sent_model", "returned_model", "usage"}
        if not required.issubset(row) or row.get("action_contact_completed") is not True:
            raise ProbeFailure("TARGET_PROBE_BUDGET", "completion attribution")
        request_id = row["request_id"]
        attempt = row["action_attempt_id"]
        if (not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None
                or not isinstance(attempt, str) or re.fullmatch(re.escape(request_id) + r"\.a[1-9][0-9]*", attempt) is None):
            raise ProbeFailure("TARGET_PROBE_BUDGET", "completion identifiers")
        req_name, res_name = row["body_request_file"], row["body_response_file"]
        if (not isinstance(req_name, str) or req_name != request_id + ".req.json.gz"
                or not isinstance(res_name, str)
                or re.fullmatch(re.escape(request_id) + r"\.a[1-9][0-9]*\.res\.(?:json|sse)\.gz", res_name) is None):
            raise ProbeFailure("TARGET_PROBE_BUDGET", "capture filename")
        capture_root = trace / "upstream-bodies"
        # Unit fixtures put the captures directly under the trace; production
        # uses the copied Task 3 body directory.  Both are no-follow assets.
        root = capture_root if capture_root.is_dir() else trace
        request, request_hash = _gzip_json(root / req_name)
        response, response_hash = _gzip_json(root / res_name)
        if not isinstance(request, dict) or request.get("model") != row["sent_model"]:
            raise ProbeFailure("TARGET_IDENTITY_MISMATCH", "raw request identity")
        if not isinstance(response, dict) or response.get("model") != row["returned_model"]:
            raise ProbeFailure("TARGET_IDENTITY_MISMATCH", "raw response identity")
        usage = response.get("usage")
        if not _ordinary_usage(usage) or row["usage"] != usage:
            raise ProbeFailure("TARGET_PROBE_BUDGET", "raw response usage")
        rows.append((row, request_hash, response_hash))
    if len({row["action_attempt_id"] for row, _, _ in rows}) != len(rows):
        raise ProbeFailure("TARGET_PROBE_BUDGET", "duplicate completed action")
    return journal_raw, journal_hash, rows


def _ordinary_usage(usage):
    """Accept normal provider counters without allowing malformed accounting."""
    if not isinstance(usage, dict) or not {"prompt_tokens", "completion_tokens"}.issubset(usage):
        return False
    def counters(value):
        if isinstance(value, dict):
            return all(isinstance(key, str) and counters(child) for key, child in value.items())
        return type(value) is int and value >= 0
    if not counters(usage):
        return False
    total = usage.get("total_tokens")
    return total is None or total == usage["prompt_tokens"] + usage["completion_tokens"]


def audit(trace):
    trace = Path(trace)
    result = {"schema": 1, "accepted": False, "checked_at": _time_text(_now())}
    receipt_nonce = None
    trace_fd = _open_directory_no_follow(trace)
    try:
        receipt_path = trace / "target-contract-receipt.json"
        checksum_path = Path(str(receipt_path) + ".sha256")
        # Receipt/checksum are inert staging artefacts.  A previous interrupted
        # transaction has no commit marker, so remove its canonical names before
        # refusing this audit; never let it look like an accepted receipt.
        if (os.path.lexists(receipt_path) or os.path.lexists(checksum_path)) and not os.path.lexists(trace / "probe-result.json"):
            _remove_receipt_transaction(trace_fd)
            result.update({"failure": "TARGET_CONTRACT_FAILED", "detail": "orphan receipt transaction"})
            _write_result(trace, result, root_fd=trace_fd)
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "orphan receipt transaction")
        if os.path.lexists(trace / "probe-result.json"):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "attempt result already sealed")
        # A receipt/checksum has no authority absent the final result marker.
        # Clear an interrupted, owned transaction before a fresh audit.
        if os.path.lexists(trace / "target-contract-receipt.json"):
            descriptor = os.open(trace, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                for name in ("target-contract-receipt.json.sha256", "target-contract-receipt.json"):
                    try: os.unlink(name, dir_fd=descriptor)
                    except FileNotFoundError: pass
                    except OSError as exc: raise ProbeFailure("TARGET_CONTRACT_FAILED", "orphan receipt") from exc
            finally:
                os.close(descriptor)
        raw_manifest, _ = _asset(trace / "probe-manifest.json", "TARGET_CONTRACT_FAILED")
        manifest = _strict_json(raw_manifest, PROBE_MANIFEST_KEYS)
        action_authorization, action_authorization_raw = _audit_authorization(trace, manifest, raw_manifest)
        package = _verify_package(trace, manifest, "TARGET_CONTRACT_FAILED")
        if _iso(manifest["expires_at"]) <= _now():
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "expired")
        # The runner's normal path is the only authoritative report artifact.
        report = trace / "work" / "report.md"
        fixture = trace / "fixture"
        oracle = ORACLE.audit_report(report, fixture, fixture / "probe-expectations.json")
        if oracle.get("accepted") is not True:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "oracle failure")
        gates = _real_gates(trace, report, fixture)
        _atomic_no_replace(trace / "probe-oracle.json", canonical(oracle) + b"\n")
        _atomic_no_replace(trace / "probe-gates.json", canonical(gates) + b"\n")
        # Task 7 is re-run here.  A pre-existing verdict is merely an untrusted
        # runner artifact, never the audit's authority.
        status = subprocess.run([sys.executable, str(HERE / "run-verdict.py"), str(trace),
                                 "--target-probe", "--json"], text=True, capture_output=True, timeout=30)
        status_exit_code = status.returncode
        if status.returncode != 0:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "fresh Task 7 verdict")
        verdict_raw = status.stdout.encode("utf-8")
        verdict_hash = sha256(verdict_raw)
        verdict = _strict_json(verdict_raw)
        required_verdict = {"schema", "run_tag", "state", "phase", "finished", "successful", "report_correct",
                            "report_correctness_scope", "authenticated", "authority", "failures", "metrics",
                            "improvements", "attempt_exit_code", "driver_exit_code", "gate_exit_codes",
                            "wrapper_exit_code", "primary_failure", "terminal_observation"}
        if (not required_verdict.issubset(verdict) or verdict.get("schema") != 1 or verdict.get("run_tag") != trace.name
                or verdict.get("state") != "finished" or verdict.get("finished") is not True
                or verdict.get("successful") is not True or verdict.get("report_correct") is not True
                or verdict.get("authenticated") is not True
                or verdict.get("authority") != "operator-approved-target-probe"
                or not isinstance(verdict.get("phase"), str) or not isinstance(verdict.get("failures"), list)
                or not isinstance(verdict.get("metrics"), dict) or not isinstance(verdict.get("improvements"), list)
                or any(type(verdict[key]) is not int or verdict[key] != 0
                for key in ("attempt_exit_code", "driver_exit_code", "wrapper_exit_code"))
                or not isinstance(verdict.get("gate_exit_codes"), dict) or set(verdict["gate_exit_codes"]) != set(GATES)
                or any(type(value) is not int or value != 0 for value in verdict["gate_exit_codes"].values())
                or verdict.get("primary_failure") is not None or not isinstance(verdict.get("terminal_observation"), str)):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "task7 verdict")
        task3_rows = None
        if (trace / "upstream-completed.jsonl").is_file():
            ledger_raw, ledger_hash, task3_rows = _task3_observations(trace)
            ledger = {"provider_calls_observed": len(task3_rows),
                      "usage": {"prompt_tokens": sum(row["usage"]["prompt_tokens"] for row, _, _ in task3_rows),
                                "completion_tokens": sum(row["usage"]["completion_tokens"] for row, _, _ in task3_rows)},
                      "returned_identities": [row["returned_model"] for row, _, _ in task3_rows],
                      "sent_models": [row["sent_model"] for row, _, _ in task3_rows],
                      "call_ids": [row["action_attempt_id"] for row, _, _ in task3_rows]}
        else:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "missing Task 3 completion journal")
        budget = _strict_budget(_strict_json(_asset(trace / "upstream-budget-state.json", "TARGET_CONTRACT_FAILED")[0]))
        if set(ledger) != {"provider_calls_observed", "usage", "returned_identities", "sent_models", "call_ids"} or \
                not isinstance(ledger.get("usage"), dict) or set(ledger["usage"]) != {"prompt_tokens", "completion_tokens"} or \
                any(type(ledger["usage"][key]) is not int or ledger["usage"][key] < 0 for key in ledger["usage"]):
            raise ProbeFailure("TARGET_PROBE_BUDGET")
        calls = ledger.get("provider_calls_observed")
        if type(calls) is not int or calls < 1 or calls > DEFAULT_BUDGET["max_provider_calls"] or \
                budget["observed"].get("provider_calls") != calls or \
                budget["completed_attempt_ids"] != ledger.get("call_ids") or \
                not all(isinstance(value, str) and value for value in ledger.get("call_ids", [])) or \
                len(set(ledger["call_ids"])) != calls or len(ledger["sent_models"]) != calls or len(ledger["returned_identities"]) != calls:
            raise ProbeFailure("TARGET_PROBE_BUDGET")
        if any(model != package["target-profile.json"]["requested_model"] for model in ledger["sent_models"]):
            raise ProbeFailure("TARGET_IDENTITY_MISMATCH")
        # Reconcile each ledger row to the exact body bytes.  A summary ledger
        # cannot invent a sent model, returned identity, call id, or usage.
        requests = []
        responses = []
        if task3_rows is not None:
            for row, _, _ in task3_rows:
                requests.append({"call_id": row["action_attempt_id"], "model": row["sent_model"]})
                responses.append({"call_id": row["action_attempt_id"], "returned_identity": row["returned_model"],
                                  "usage": row["usage"]})
            # Task 3's gzip journal is the action authority, but the copied
            # controller body trees are still evidence and must agree exactly.
            body_tree = trace / "response-bodies"
            if body_tree.is_dir():
                try:
                    body_rows = [_strict_json(_asset(path, "TARGET_CONTRACT_FAILED")[0])
                                 for path in sorted(body_tree.iterdir())]
                except OSError as exc:
                    raise ProbeFailure("TARGET_CONTRACT_FAILED", "response body tree") from exc
                if body_rows != responses:
                    raise ProbeFailure("TARGET_IDENTITY_MISMATCH", "body correlation")
        else:
            for directory, destination in ((trace / "request-bodies", requests), (trace / "response-bodies", responses)):
                try:
                    names = sorted(path.name for path in directory.iterdir())
                except OSError as exc:
                    raise ProbeFailure("TARGET_CONTRACT_FAILED", "body tree") from exc
                for name in names:
                    destination.append(_strict_json(_asset(directory / name, "TARGET_CONTRACT_FAILED")[0]))
        if len(requests) != calls or len(responses) != calls:
            raise ProbeFailure("TARGET_PROBE_BUDGET", "body count")
        request_by_id = {row.get("call_id"): row for row in requests if isinstance(row, dict)}
        response_by_id = {row.get("call_id"): row for row in responses if isinstance(row, dict)}
        if len(request_by_id) != calls or len(response_by_id) != calls or set(request_by_id) != set(ledger["call_ids"]) or set(response_by_id) != set(ledger["call_ids"]):
            raise ProbeFailure("TARGET_PROBE_BUDGET", "body call identity")
        expected_model = package["target-profile.json"]["requested_model"]
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        for call_id, sent, returned in zip(ledger["call_ids"], ledger["sent_models"], ledger["returned_identities"]):
            request = request_by_id[call_id]; response = response_by_id[call_id]
            if (set(request) != {"call_id", "model"} or request["model"] != sent or sent != expected_model
                    or set(response) != {"call_id", "returned_identity", "usage"}
                    or response["returned_identity"] != returned
                    or not _ordinary_usage(response["usage"])):
                raise ProbeFailure("TARGET_IDENTITY_MISMATCH", "body correlation")
            for key in usage:
                usage[key] += response["usage"][key]
        if usage != ledger["usage"] or budget["observed"].get("prompt_tokens") != usage["prompt_tokens"] \
                or budget["observed"].get("completion_tokens") != usage["completion_tokens"]:
            raise ProbeFailure("TARGET_PROBE_BUDGET", "per-call usage")
        rate = budget["rate_snapshot"]
        estimated_cost = (usage["prompt_tokens"] * rate["prompt_rub_per_token"]
                          + usage["completion_tokens"] * rate["completion_rub_per_token"])
        if budget["projected"]["estimated_cost_rub"] < estimated_cost or estimated_cost > budget["limits"]["max_estimated_cost_rub"]:
            raise ProbeFailure("TARGET_PROBE_BUDGET", "rate cost")
        identity = audit_identity(package["target-profile.json"]["identity_mode"],
                                  package["target-profile.json"]["expected_returned_identity"],
                                  ledger.get("returned_identities"))
        if task3_rows is not None:
            request_tree = sha256(canonical({row["action_attempt_id"]: request_hash for row, request_hash, _ in task3_rows}))
            response_tree = sha256(canonical({row["action_attempt_id"]: response_hash for row, _, response_hash in task3_rows}))
        else:
            request_tree = _tree_digest(trace / "request-bodies")
            response_tree = _tree_digest(trace / "response-bodies")
        report_raw, report_hash = _asset(trace / "work" / "report.md", "TARGET_CONTRACT_FAILED")
        completed = _now()
        receipt_ttl = dt.timedelta(hours=24) if identity == "provider_pinned_version" else dt.timedelta(minutes=30)
        fixture_manifest = _strict_json(_asset(trace / "fixture-manifest.json", "TARGET_CONTRACT_FAILED")[0])
        receipt_nonce = secrets.token_hex(32)
        receipt_nonce_path = trace / "receipt-nonces" / (receipt_nonce + ".json")
        receipt_nonce_bytes = canonical({"nonce": receipt_nonce}) + b"\n"
        _write_relative(trace_fd, "receipt-nonces/" + receipt_nonce + ".json", receipt_nonce_bytes,
                        "TARGET_CONTRACT_FAILED")
        receipt = {"schema": 1, "accepted": True, "proof_scope": "representative_sample_only",
                   "created_at": _time_text(completed), "expires_at": _time_text(completed + receipt_ttl), "nonce": receipt_nonce,
                   "action_nonce": manifest["nonce"], "action_authorization_sha256": sha256(action_authorization_raw),
                   "target_profile_sha256": manifest["target_profile_sha256"], "probe_manifest_sha256": sha256(raw_manifest),
                   "fixture_manifest_sha256": manifest["fixture_manifest_sha256"],
                   "input_package_sha256": manifest["input_package_sha256"],
                   "probe_budget_sha256": manifest["probe_budget_sha256"],
                   "probe_rate_snapshot_sha256": manifest["rate_snapshot_sha256"],
                   "fixture_seed": fixture_manifest.get("seed"),
                   "fixture_tree_sha256": package["input-package.json"]["fixture_tree_sha256"],
                   "fixture_report_sha256": package["input-package.json"]["fixture_expectations_sha256"],
                   "probe_prompt_sha256": _asset(trace / "probe" / "prompt.txt", "TARGET_CONTRACT_FAILED")[1],
                   "requested_model": package["target-profile.json"]["requested_model"],
                   "sent_model": package["target-profile.json"]["requested_model"],
                   "returned_identities": ledger["returned_identities"], "identity_assurance": identity,
                   "gate_sha256": {name: gates[name]["raw_sha256"] for name in GATES},
                   "gate_summaries": {name: gates[name]["payload"] for name in GATES},
                   "oracle_sha256": sha256(canonical(oracle)), "probe_oracle_sha256": sha256((trace / "probe-oracle.json").read_bytes()),
                   "probe_gates_sha256": sha256((trace / "probe-gates.json").read_bytes()),
                   "final_report_sha256": report_hash, "ledger_sha256": sha256(ledger_raw),
                   "request_body_tree_sha256": request_tree, "response_body_tree_sha256": response_tree,
                   "provider_calls_observed": calls, "usage": usage,
                   "estimated_cost_rub": estimated_cost,
                   "cost_inputs": {"observed_usage": usage, "rate_snapshot": rate,
                                   "recomputed_estimated_cost_rub": estimated_cost},
                   "rate_snapshot": budget.get("rate_snapshot"), "budget_assurance": budget["budget_assurance"],
                   "task7_verdict_sha256": verdict_hash, "run_verdict_sha256": verdict_hash,
                   "authenticated": verdict["authenticated"], "authority": verdict["authority"],
                   "attempt_exit_code": verdict["attempt_exit_code"], "driver_exit_code": verdict["driver_exit_code"],
                   "gate_exit_codes": verdict["gate_exit_codes"], "wrapper_exit_code": verdict["wrapper_exit_code"],
                   "status_exit_code": status_exit_code, "primary_failure": verdict["primary_failure"],
                   "terminal_observation": verdict["terminal_observation"],
                   "rate_assurance": budget["budget_assurance"],
                   "provider_billed_calls": None, "provider_billed_rub": None,
                   "audit_tool_sha256": sha256(Path(__file__).read_bytes())}
        if os.path.lexists(receipt_path) or os.path.lexists(checksum_path):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "receipt transaction already exists")
        receipt["checksum_path"] = str(checksum_path)
        # These bytes are constructed privately before any public name is
        # claimed.  Receipt and checksum are intentionally not acceptance
        # markers; the result below is the only commit marker and is last.
        receipt_bytes = canonical(receipt) + b"\n"
        checksum_bytes = sha256(receipt_bytes).encode("ascii") + b"\n"
        _write_relative(trace_fd, "target-contract-receipt.json", receipt_bytes, "TARGET_CONTRACT_FAILED")
        _write_relative(trace_fd, "target-contract-receipt.json.sha256", checksum_bytes, "TARGET_CONTRACT_FAILED")
        accepted_result = dict(result, accepted=True, receipt=str(receipt_path), identity_assurance=identity,
                               receipt_sha256=sha256(receipt_bytes),
                               receipt_checksum_sha256=sha256(checksum_bytes),
                               receipt_checksum=checksum_bytes.decode("ascii").strip())
        if not _write_result(trace, accepted_result, root_fd=trace_fd):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "terminal result conflict")
        os.close(trace_fd)
        return accepted_result
    except ProbeFailure as exc:
        _remove_receipt_transaction(trace_fd, receipt_nonce)
        result.update({"accepted": False, "failure": exc.code, "detail": str(exc)})
        try: _write_result(trace, result, root_fd=trace_fd)
        except Exception: pass
        os.close(trace_fd)
        raise
    except Exception as exc:
        _remove_receipt_transaction(trace_fd, receipt_nonce)
        failure = ProbeFailure("TARGET_CONTRACT_FAILED", type(exc).__name__)
        result.update({"accepted": False, "failure": failure.code, "detail": str(failure)})
        try: _write_result(trace, result, root_fd=trace_fd)
        except Exception: pass
        os.close(trace_fd)
        raise failure from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for name in ("root", "source-corpus", "provider-base-url", "route", "secret-ref", "requested-model",
                 "expected-returned-identity", "identity-mode", "qwen-bin", "arm", "rate-snapshot"):
        prep.add_argument("--" + name, required=True)
    prep.add_argument("--json", action="store_true")
    auth = sub.add_parser("run")
    auth.add_argument("--manifest", required=True); auth.add_argument("--operator-approved-probe", required=True)
    auth.add_argument("--nonce-root", required=True)
    # Deliberately no caller-controlled executable.  The integrated path is
    # selected by the sealed controller contract, never by command line.
    auth.add_argument("--json", action="store_true")
    auth.add_argument("--transport-base-url", help="localhost-only integration transport override")
    approval = sub.add_parser("authorize")
    approval.add_argument("--manifest", required=True); approval.add_argument("--operator-approved-probe", required=True)
    approval.add_argument("--nonce-root", required=True)
    check = sub.add_parser("audit"); check.add_argument("--trace", required=True); check.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            row = prepare(PrepareArgs(**{key.replace("_", "-").replace("-", "_"): value for key, value in vars(args).items() if key != "command"}))
        elif args.command == "audit":
            row = audit(args.trace)
        elif args.command == "authorize":
            row = authorize(args.manifest, args.operator_approved_probe, args.nonce_root)
        else:
            def secret_reader(reference):
                value = os.environ.get(reference)
                if not value:
                    raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "secret reference unavailable")
                return value
            def proxy_starter(profile, secret, budget):
                # The fixed controller starts the normal upstream proxy itself;
                # keep this descriptor data-only until that controlled boundary.
                return {"route": profile["route"], "budget_path": str(budget), "secret": secret}
            def runner(**kwargs):
                command = ["bash", str(HERE / "bench-controller.sh"), "--target-contract-probe",
                           "--sealed-input", str(kwargs["profile_path"].parent), "--work", str(kwargs["work"])]
                if args.transport_base_url:
                    command.extend(["--transport-base-url", args.transport_base_url])
                done = subprocess.run(command, text=True, capture_output=True, timeout=600)
                if done.returncode:
                    raise ProbeFailure("TARGET_CONTRACT_FAILED", "controlled runner nonzero")
                row = _strict_json(done.stdout.encode("utf-8"))
                if set(row) != {"trace", "runner_exit_code", "stdout_sha256"} or row.get("runner_exit_code") != 0 or not isinstance(row.get("trace"), str):
                    raise ProbeFailure("TARGET_CONTRACT_FAILED", "controlled runner output")
                row["audit"] = audit(row["trace"])
                return row
            row = run(args.manifest, args.operator_approved_probe, args.nonce_root,
                      secret_reader=secret_reader, proxy_starter=proxy_starter, runner=runner,
                      transport_base_url=args.transport_base_url)
        print(json.dumps(row, sort_keys=True))
        return 0
    except ProbeFailure as exc:
        print(exc.code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
