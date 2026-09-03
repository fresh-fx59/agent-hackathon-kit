#!/usr/bin/env python3
"""Seal, authorize, and audit the bounded paid target-contract probe.

This module deliberately has no default provider action.  The `run` boundary
authorizes and rechecks every sealed input before it obtains a secret or starts
the normal runner; callers inject those actions so the boundary remains testable
without a provider.
"""
import argparse
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


HERE = Path(__file__).resolve().parent
GATES = ("reportcheck", "citecheck", "statecheck", "triagecheck")
PROBE_MANIFEST_KEYS = {"schema", "action", "created_at", "expires_at", "nonce",
                       "target_profile_sha256", "probe_budget_sha256",
                       "fixture_manifest_sha256", "input_package_sha256"}
DEFAULT_BUDGET = {"schema": 2, "max_provider_calls": 10, "max_prompt_tokens": 400000,
                  "max_completion_tokens": 20000, "max_wall_time_s": 600,
                  "max_estimated_cost_rub": 15.0}


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
        dirs[:] = sorted(dirs)
        for name in dirs + sorted(files):
            child = current_path / name
            mode = os.lstat(child).st_mode
            relative = child.relative_to(root).as_posix()
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ProbeFailure("TARGET_CONTRACT_FAILED", "unsafe tree member")
            if stat.S_ISREG(mode):
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
               "max_output_tokens": 20000, "session_token_limit": 230000,
               "cache": {"enabled": False}, "interactive": {"enabled": False},
               "qwen": {"cli": str(qwen)}, "limits": {"requests": 10},
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


def prepare(args, secret_reader=None):
    """Build a fresh, immutable probe package; `secret_reader` is intentionally unused."""
    root = Path(args.root)
    if os.path.lexists(root):
        raise ProbeFailure("TARGET_PROBE_PREPARE", "root already exists")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".target-probe-", dir=root.parent))
    try:
        fixture_dir = staging / "fixture"
        fixture = FIXTURE.build_fixture(args.source_corpus, fixture_dir, HERE / "probe" / "recipe.json", 4401)
        settings_tool = HERE.parent.parent / "measure" / "corporate-settings.py"
        settings_run = subprocess.run(["python3", str(settings_tool), "emit-run", "--max-retries", "0",
                                       "--skill-directory", str(HERE.parent.parent / "skills" / "v44")],
                                      text=True, capture_output=True, timeout=30)
        if settings_run.returncode:
            raise ProbeFailure("TARGET_PROBE_PREPARE", "corporate settings")
        settings_bytes = settings_run.stdout.encode("utf-8")
        profile = _profile(args, sha256(settings_bytes))
        files = {"target-profile.json": canonical(profile) + b"\n",
                 "corporate-settings.json": settings_bytes,
                 "probe-budget.json": canonical(DEFAULT_BUDGET) + b"\n",
                 "fixture-manifest.json": (fixture_dir / "probe-fixture-manifest.json").read_bytes(),
                 "input-package.json": canonical({"schema": 1, "arm": args.arm,
                     "fixture_tree_sha256": fixture["output_tree_sha256"],
                     "fixture_expectations_sha256": fixture["expectations_sha256"],
                     "settings_sha256": sha256(settings_bytes),
                     "runner_sha256": sha256((HERE / "bench-controller.sh").read_bytes()),
                     "driver_sha256": sha256((HERE / "run-bench.sh").read_bytes()),
                     "proxy_sha256": sha256((HERE.parent.parent / "measure" / "upstream-log-proxy.py").read_bytes()),
                     "oracle_sha256": sha256((HERE / "target-contract-oracle.py").read_bytes()),
                     "audit_sha256": sha256(Path(__file__).read_bytes()), "qwen_sha256": _asset(args.qwen_bin, "TARGET_PROBE_PREPARE")[1],
                     "skill_sha256": _tree_digest(HERE.parent.parent / "skills" / "v44"), "gate_sha256": _gate_digests()}) + b"\n"}
        for name, data in files.items():
            (staging / name).write_bytes(data)
        created = _now()
        ttl = (dt.timedelta(hours=24) if args.identity_mode == "provider_pinned_version" and
               args.requested_model == args.expected_returned_identity else dt.timedelta(minutes=30))
        manifest = {"schema": 1, "action": "target_contract_probe", "created_at": _time_text(created),
                    "expires_at": _time_text(created + ttl), "nonce": secrets.token_hex(32),
                    "target_profile_sha256": sha256(files["target-profile.json"]),
                    "probe_budget_sha256": sha256(files["probe-budget.json"]),
                    "fixture_manifest_sha256": sha256(files["fixture-manifest.json"]),
                    "input_package_sha256": sha256(files["input-package.json"])}
        (staging / "probe-manifest.json").write_bytes(canonical(manifest) + b"\n")
        # Fixture is part of the sealed package, with its original source bytes only.
        if os.path.lexists(root):
            raise ProbeFailure("TARGET_PROBE_PREPARE", "root already exists")
        os.replace(staging, root)
        staging = None
    finally:
        if staging and staging.exists():
            shutil.rmtree(staging)
    return {"root": str(root), "manifest_sha256": sha256((root / "probe-manifest.json").read_bytes())}


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


def authorize(manifest_path, supplied_hash, nonce_root, action="target_contract_probe", *, consume=True):
    raw = safe_read_regular(manifest_path)
    if not _hex(supplied_hash) or not hmac.compare_digest(sha256(raw), supplied_hash):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest hash")
    row = _strict_json(raw, PROBE_MANIFEST_KEYS)
    created, expires = _iso(row.get("created_at")), _iso(row.get("expires_at"))
    if row.get("schema") != 1 or row.get("action") != action or expires <= _now() or \
            expires <= created or expires - created > dt.timedelta(hours=24):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "action or expiry")
    for name in ("target_profile_sha256", "probe_budget_sha256", "fixture_manifest_sha256", "input_package_sha256"):
        if not _hex(row.get(name)):
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest digest")
    if consume:
        try:
            _consume_nonce(nonce_root, row["nonce"], supplied_hash)
        except ProbeFailure as exc:
            if exc.code == "APPROVAL_REPLAYED":
                raise
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "nonce") from exc
    return row


def _verify_package(root, manifest, code="TARGET_PROBE_NOT_AUTHORIZED"):
    names = {"target-profile.json": "target_profile_sha256", "probe-budget.json": "probe_budget_sha256",
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
    package = values["input-package.json"]
    expected_package = {"schema", "arm", "fixture_tree_sha256", "fixture_expectations_sha256",
                        "settings_sha256", "runner_sha256", "driver_sha256", "proxy_sha256",
                        "oracle_sha256", "audit_sha256", "qwen_sha256", "skill_sha256", "gate_sha256"}
    if not isinstance(package, dict) or set(package) != expected_package or package.get("schema") != 1 or \
            not isinstance(package.get("arm"), str) or not _hex(package.get("fixture_tree_sha256")) or \
            not _hex(package.get("fixture_expectations_sha256")) or \
            any(not _hex(package.get(name)) for name in ("settings_sha256", "runner_sha256", "driver_sha256", "proxy_sha256", "oracle_sha256", "audit_sha256", "qwen_sha256", "skill_sha256")) or \
            not isinstance(package.get("gate_sha256"), dict) or set(package["gate_sha256"]) != set(GATES) or \
            any(not _hex(value) for value in package["gate_sha256"].values()):
        raise ProbeFailure(code, "input package invalid")
    fixture_manifest = values["fixture-manifest.json"]
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
    }
    for field, path in dependencies.items():
        if not hmac.compare_digest(_asset(path, code)[1], package[field]):
            raise ProbeFailure(code, "stable dependency changed")
    if not hmac.compare_digest(_asset(values["target-profile.json"]["qwen"]["cli"], code)[1], package["qwen_sha256"]) or \
            not hmac.compare_digest(_tree_digest(HERE.parent.parent / "skills" / "v44"), package["skill_sha256"]):
        raise ProbeFailure(code, "stable dependency changed")
    return values


def run(manifest_path, supplied_hash, nonce_root, *, secret_reader, proxy_starter, runner):
    """Cross the only contact boundary after approval and all sealed-byte checks."""
    work = Path(manifest_path).parent / "probe-work"
    try:
        manifest = authorize(manifest_path, supplied_hash, nonce_root, consume=False)
        root = Path(manifest_path).parent
        package = _verify_package(root, manifest)
        if os.path.lexists(work):
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "work already exists")
    # Freeze a private, no-callback-visible copy before one-way approval use.
        work.mkdir(mode=0o700)
        sealed = work / "sealed-input"
        sealed.mkdir(mode=0o700)
        for name in ("target-profile.json", "corporate-settings.json", "probe-budget.json", "fixture-manifest.json", "input-package.json"):
            shutil.copyfile(root / name, sealed / name)
        shutil.copytree(root / "fixture", sealed / "fixture", symlinks=False)
        _verify_package(sealed, manifest)
    # All knowable validation precedes the one-way nonce consumption.
        _consume_nonce(nonce_root, manifest["nonce"], supplied_hash)
        secret = secret_reader(package["target-profile.json"]["secret_ref"])
    # Secret resolution is the only allowed operation before the proxy.  Treat
    # callbacks as hostile: validate the owned snapshot immediately afterwards.
        _verify_package(sealed, manifest)
        profile = _strict_json(_asset(sealed / "target-profile.json")[0])
        proxy = proxy_starter(profile, secret, sealed / "probe-budget.json")
        return runner(profile_path=sealed / "target-profile.json", fixture=sealed / "fixture",
                      budget_path=sealed / "probe-budget.json", work=work, proxy=proxy, retries=0)
    except ProbeFailure as exc:
        terminal_root = work if work.is_dir() else Path(manifest_path).parent
        if not os.path.lexists(terminal_root / "probe-result.json"):
            _write_result(terminal_root, {"schema": 1, "accepted": False, "checked_at": _time_text(_now()),
                                          "failure": exc.code, "detail": str(exc)})
        raise


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
            not isinstance(row.get("rate_snapshot"), dict) or set(row["rate_snapshot"]) != {"effective_at", "source", "sha256"} or \
            not isinstance(row["rate_snapshot"]["source"], str) or not _hex(row["rate_snapshot"]["sha256"]):
        raise ProbeFailure("TARGET_PROBE_BUDGET")
    try:
        _iso(row["updated_at"]); _iso(row["rate_snapshot"]["effective_at"])
    except ProbeFailure as exc:
        raise ProbeFailure("TARGET_PROBE_BUDGET") from exc
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
    return row


def _write_result(trace, result):
    path = Path(trace) / "probe-result.json"
    try:
        _atomic_no_replace(path, canonical(result) + b"\n")
    except ProbeFailure:
        # A sealed result exists; never overwrite an earlier attempt's observation.
        pass


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


def audit(trace):
    trace = Path(trace)
    result = {"schema": 1, "accepted": False, "checked_at": _time_text(_now())}
    published = []
    try:
        if os.path.lexists(trace / "probe-result.json"):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "attempt result already sealed")
        raw_manifest, _ = _asset(trace / "probe-manifest.json", "TARGET_CONTRACT_FAILED")
        manifest = _strict_json(raw_manifest, PROBE_MANIFEST_KEYS)
        package = _verify_package(trace, manifest, "TARGET_CONTRACT_FAILED")
        if _iso(manifest["expires_at"]) <= _now():
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "expired")
        report = trace / "final-report.md"
        fixture = trace / "fixture"
        oracle = ORACLE.audit_report(report, fixture, fixture / "probe-expectations.json")
        if oracle.get("accepted") is not True:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "oracle failure")
        gates = _real_gates(trace, report, fixture)
        _atomic_no_replace(trace / "probe-oracle.json", canonical(oracle) + b"\n")
        _atomic_no_replace(trace / "probe-gates.json", canonical(gates) + b"\n")
        exits = _strict_json(_asset(trace / "exit-layers.json", "TARGET_CONTRACT_FAILED")[0])
        required_exits = {"attempt_exit_code", "driver_exit_code", "wrapper_exit_code", "status_exit_code",
                          "gate_exit_codes", "primary_failure", "terminal_observation"}
        if set(exits) != required_exits or any(type(exits[key]) is not int or exits[key] != 0
                for key in ("attempt_exit_code", "driver_exit_code", "wrapper_exit_code", "status_exit_code")) or \
                not isinstance(exits["gate_exit_codes"], dict) or set(exits["gate_exit_codes"]) != set(GATES) or \
                any(type(value) is not int or value != 0 for value in exits["gate_exit_codes"].values()) or \
                exits["primary_failure"] is not None or exits["terminal_observation"] != "RUN_SUCCEEDED":
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "exit layer")
        ledger_raw, _ = _asset(trace / "ledger.json", "TARGET_CONTRACT_FAILED")
        ledger = _strict_json(ledger_raw)
        budget = _strict_budget(_strict_json(_asset(trace / "upstream-budget-state.json", "TARGET_CONTRACT_FAILED")[0]))
        if set(ledger) != {"provider_calls_observed", "usage", "returned_identities", "sent_models", "call_ids"} or \
                not isinstance(ledger.get("usage"), dict) or set(ledger["usage"]) != {"prompt_tokens", "completion_tokens"} or \
                any(type(ledger["usage"][key]) is not int or ledger["usage"][key] < 0 for key in ledger["usage"]):
            raise ProbeFailure("TARGET_PROBE_BUDGET")
        calls = ledger.get("provider_calls_observed")
        if type(calls) is not int or calls < 1 or calls > DEFAULT_BUDGET["max_provider_calls"] or \
                budget["observed"].get("provider_calls") != calls or \
                budget["observed"].get("prompt_tokens") != ledger["usage"]["prompt_tokens"] or \
                budget["observed"].get("completion_tokens") != ledger["usage"]["completion_tokens"] or \
                not all(isinstance(value, str) and value for value in ledger.get("call_ids", [])) or \
                len(set(ledger["call_ids"])) != calls or len(ledger["sent_models"]) != calls or len(ledger["returned_identities"]) != calls:
            raise ProbeFailure("TARGET_PROBE_BUDGET")
        if any(model != package["target-profile.json"]["requested_model"] for model in ledger["sent_models"]):
            raise ProbeFailure("TARGET_IDENTITY_MISMATCH")
        identity = audit_identity(package["target-profile.json"]["identity_mode"],
                                  package["target-profile.json"]["expected_returned_identity"],
                                  ledger.get("returned_identities"))
        request_tree = _tree_digest(trace / "request-bodies")
        response_tree = _tree_digest(trace / "response-bodies")
        report_raw, report_hash = _asset(trace / "final-report.md", "TARGET_CONTRACT_FAILED")
        completed = _now()
        receipt_ttl = dt.timedelta(hours=24) if identity == "provider_pinned_version" else dt.timedelta(minutes=30)
        receipt = {"schema": 1, "accepted": True, "proof_scope": "representative_sample_only",
                   "created_at": _time_text(completed), "expires_at": _time_text(completed + receipt_ttl), "nonce": manifest["nonce"],
                   "target_profile_sha256": manifest["target_profile_sha256"], "probe_manifest_sha256": sha256(raw_manifest),
                   "fixture_manifest_sha256": manifest["fixture_manifest_sha256"],
                   "input_package_sha256": manifest["input_package_sha256"],
                   "requested_model": package["target-profile.json"]["requested_model"],
                   "sent_model": package["target-profile.json"]["requested_model"],
                   "returned_identities": ledger["returned_identities"], "identity_assurance": identity,
                   "gate_sha256": {name: gates[name]["raw_sha256"] for name in GATES},
                   "oracle_sha256": sha256(canonical(oracle)), "probe_oracle_sha256": sha256((trace / "probe-oracle.json").read_bytes()),
                   "probe_gates_sha256": sha256((trace / "probe-gates.json").read_bytes()),
                   "final_report_sha256": report_hash, "ledger_sha256": sha256(ledger_raw),
                   "request_body_tree_sha256": request_tree, "response_body_tree_sha256": response_tree,
                   "provider_calls_observed": calls, "usage": ledger.get("usage"),
                   "estimated_cost_rub": budget.get("projected", {}).get("estimated_cost_rub"),
                   "rate_snapshot": budget.get("rate_snapshot"), "budget_assurance": "client_pre_dispatch",
                   "exit_layers": exits, "provider_billed_calls": None, "provider_billed_rub": None,
                   "audit_tool_sha256": sha256(Path(__file__).read_bytes())}
        receipt_path = trace / "target-contract-receipt.json"
        checksum_path = Path(str(receipt_path) + ".sha256")
        if os.path.lexists(receipt_path) or os.path.lexists(checksum_path):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "receipt transaction already exists")
        receipt_bytes = canonical(receipt) + b"\n"
        checksum_bytes = sha256(receipt_bytes).encode("ascii") + b"\n"
        _atomic_no_replace(receipt_path, receipt_bytes)
        published.append((receipt_path, sha256(receipt_bytes)))
        _atomic_no_replace(checksum_path, checksum_bytes)
        published.append((checksum_path, sha256(checksum_bytes)))
        result.update({"accepted": True, "receipt": str(receipt_path), "identity_assurance": identity})
        _write_result(trace, result)
        return result
    except ProbeFailure as exc:
        for path, digest in reversed(published):
            _remove_owned(path, digest)
        result.update({"failure": exc.code, "detail": str(exc)})
        _write_result(trace, result)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for name in ("root", "source-corpus", "provider-base-url", "route", "secret-ref", "requested-model",
                 "expected-returned-identity", "identity-mode", "qwen-bin", "arm"):
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
                return _strict_json(done.stdout.encode("utf-8"))
            row = run(args.manifest, args.operator_approved_probe, args.nonce_root,
                      secret_reader=secret_reader, proxy_starter=proxy_starter, runner=runner)
        print(json.dumps(row, sort_keys=True))
        return 0
    except ProbeFailure as exc:
        print(exc.code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
