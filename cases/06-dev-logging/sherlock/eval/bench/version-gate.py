#!/usr/bin/env python3
"""Immutable runtime-package selection and snapshot sealing."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import argparse

HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE / "skill-version-registry.json"
# Exported checkouts use this anchor; a rewritten registry cannot establish v44.
TRUSTED_BASELINE = {"v44": "cdaff38068e4fdfeec5d61fab3a1e03ab98cb95baaa5295d93f522c667bfa55a"}

class VersionGateError(ValueError):
    pass

class VerifiedVersion:
    def __init__(self, version, path, digest):
        self.version, self.path, self.digest = version, Path(path), digest

def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def _valid_version(value):
    return (isinstance(value, str) and value.startswith("v") and value[1:].isdigit()
            and str(int(value[1:])) == value[1:] and int(value[1:]) > 0)

def _read_regular(path):
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise VersionGateError("unsafe package member")
        with open(path, "rb") as handle:
            data = handle.read()
        after = os.lstat(path)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise VersionGateError("package changed while reading")
        return data
    except OSError as exc:
        raise VersionGateError("unsafe package member") from exc

def tree_digest(root):
    root = Path(root)
    try: mode = os.lstat(root).st_mode
    except OSError as exc: raise VersionGateError("package missing: " + str(root)) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode): raise VersionGateError("unsafe package root")
    rows = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        for directory in directories:
            if stat.S_ISLNK(os.lstat(Path(current) / directory).st_mode): raise VersionGateError("unsafe package member")
        for filename in files:
            path = Path(current) / filename; data = _read_regular(path)
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in Path(relative).parts or filename.endswith((".pyc", ".pyo")):
                continue
            rows.append([relative, hashlib.sha256(data).hexdigest()])
    if not rows: raise VersionGateError("empty package")
    return hashlib.sha256(_canonical(rows)).hexdigest()

def _registry(path):
    try: row = json.loads(_read_regular(path).decode())
    except (UnicodeError, json.JSONDecodeError, VersionGateError) as exc: raise VersionGateError("malformed registry") from exc
    versions = row.get("versions") if isinstance(row, dict) and set(row) == {"schema", "versions"} and row.get("schema") == 1 else None
    if not isinstance(versions, dict) or not versions: raise VersionGateError("malformed registry")
    for version, digest in versions.items():
        if not _valid_version(version) or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): raise VersionGateError("malformed registry")
    return versions

def _registry_from_bytes(raw):
    try:
        row = json.loads(raw.decode())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VersionGateError("malformed registry") from exc
    versions = row.get("versions") if isinstance(row, dict) and set(row) == {"schema", "versions"} and row.get("schema") == 1 else None
    if not isinstance(versions, dict) or not versions: raise VersionGateError("malformed registry")
    for version, digest in versions.items():
        if not _valid_version(version) or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): raise VersionGateError("malformed registry")
    return versions

def _committed_registries(path):
    path = Path(path).resolve()
    try:
        repo = Path(subprocess.check_output(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL).strip()).resolve()
        relative = path.relative_to(repo).as_posix()
        commits = subprocess.check_output(
            ["git", "-C", str(repo), "log", "--format=%H", "--", relative],
            text=True, stderr=subprocess.DEVNULL).splitlines()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return []
    registries = []
    for commit in commits:
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(repo), "show", commit + ":" + relative],
                stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            continue
        registries.append(_registry_from_bytes(raw))
    return registries

def _write_registry(path, versions):
    path = Path(path)
    raw = _canonical({"schema": 1, "versions": versions}) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".version-registry-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if descriptor is not None: os.close(descriptor)
        try: os.unlink(temporary_name)
        except FileNotFoundError: pass

def verify_registry(path=DEFAULT_REGISTRY, trusted_baseline=TRUSTED_BASELINE):
    current = _registry(Path(path))
    for version, digest in trusted_baseline.items():
        if current.get(version) != digest: raise VersionGateError("trusted baseline changed")
    history = {}
    for ancestor in _committed_registries(Path(path)):
        for version, digest in ancestor.items():
            if version in history and history[version] != digest:
                raise VersionGateError("registry history changed")
            history[version] = digest
    for version, digest in history.items():
        if current.get(version) != digest: raise VersionGateError("registry history changed")
    return current

def verify_version(version, skills_root, registry_path=DEFAULT_REGISTRY, trusted_baseline=TRUSTED_BASELINE):
    if not _valid_version(version): raise VersionGateError("invalid package version")
    versions = verify_registry(registry_path, trusted_baseline)
    if version not in versions: raise VersionGateError("unregistered package version")
    root = Path(skills_root) / version; observed = tree_digest(root)
    if observed != versions[version]: raise VersionGateError("registered package bytes changed")
    return VerifiedVersion(version, root, observed)

def register_version(version, skills_root, registry_path=DEFAULT_REGISTRY,
                     trusted_baseline=TRUSTED_BASELINE):
    if not _valid_version(version): raise VersionGateError("invalid package version")
    versions = verify_registry(registry_path, trusted_baseline)
    root = Path(skills_root) / version
    observed = tree_digest(root)
    if version in versions:
        if versions[version] != observed: raise VersionGateError("registration conflicts with existing version")
        return VerifiedVersion(version, root, observed)
    updated = dict(versions)
    updated[version] = observed
    _write_registry(registry_path, updated)
    verify_registry(registry_path, trusted_baseline)
    return VerifiedVersion(version, root, observed)

def seal_snapshot(verified, snapshot_root):
    snapshot_root = Path(snapshot_root); snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = snapshot_root / (verified.version + "-" + verified.digest)
    if destination.exists():
        if tree_digest(destination) != verified.digest: raise VersionGateError("snapshot identity changed")
        for current, directories, files in os.walk(destination):
            paths = [Path(current), *(Path(current) / name for name in directories + files)]
            if any(stat.S_IMODE(os.lstat(path).st_mode) & 0o222 for path in paths):
                raise VersionGateError("snapshot permissions changed")
        return VerifiedVersion(verified.version, destination, verified.digest)
    staging = Path(tempfile.mkdtemp(prefix=".package-", dir=snapshot_root))
    try:
        copied = staging / verified.version; shutil.copytree(verified.path, copied, symlinks=True)
        if tree_digest(verified.path) != verified.digest or tree_digest(copied) != verified.digest: raise VersionGateError("package changed while snapshotting")
        for current, _, files in os.walk(copied):
            # macOS requires the moved directory itself to remain owner-writable
            # for rename; all children are sealed before publication and the
            # root loses write permission immediately after the atomic move.
            os.chmod(current, 0o755 if Path(current) == copied else 0o555)
            for name in files:
                path = Path(current) / name
                mode = stat.S_IMODE(os.lstat(path).st_mode)
                os.chmod(path, 0o555 if mode & 0o111 else 0o444)
        os.replace(copied, destination)
        os.chmod(destination, 0o555)
    finally: shutil.rmtree(staging, ignore_errors=True)
    return VerifiedVersion(verified.version, destination, verified.digest)

def before_contact(version, skills_root, registry_path, snapshot_root, contact, trusted_baseline=TRUSTED_BASELINE):
    return contact(seal_snapshot(verify_version(version, skills_root, registry_path, trusted_baseline), snapshot_root))

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--skills-root", required=True)
    parser.add_argument("--snapshot-root")
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args(argv)
    try:
        verified = (register_version(args.version, args.skills_root) if args.register
                    else verify_version(args.version, args.skills_root))
        if args.snapshot_root:
            verified = seal_snapshot(verified, args.snapshot_root)
    except VersionGateError as exc:
        parser.error(str(exc))
    print(verified.path)

if __name__ == "__main__":
    main()
