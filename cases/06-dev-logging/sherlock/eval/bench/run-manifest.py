#!/usr/bin/env python3
"""Blind-stage an allowlisted corpus and commit an immutable run identity."""
import argparse
import datetime as dt
import errno
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile

SECRET = re.compile(r"(?:bearer\s+|(?:sk|ghp|glpat|xox[baprs])-|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN .*PRIVATE KEY-----|(?:password|token|api[_-]?key)\s*[:=])", re.I)
IDENTIFIER = re.compile(r"^[A-Za-z0-9_./:@+\[\]-]+$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = {"answer-key", "answer_key", "labels", "label", "facts.json",
             "ground-truth", "ground_truth", "attacker-only", "attacker_only"}
MAX_ID = 128
MAX_PATH = 4096
MAX_HEALTH_SECONDS = 15 * 60
MAX_FUTURE_SKEW = 60
COMMITMENT_PAYLOAD_KEYS = {"schema", "run_tag", "trace_dir", "trace_identity_sha256",
                           "manifest_sha256", "committed_at", "key_id"}
COMMITMENT_KEYS = COMMITMENT_PAYLOAD_KEYS | {"hmac_sha256"}


class ManifestError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__("%s: %s" % (code, message))


def fail(code, message):
    raise ManifestError(code, message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def now_text():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_text(value, code="E_INPUT_INVALID", maximum=MAX_PATH):
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        fail(code, "input must be a bounded non-empty string")
    if SECRET.search(value):
        fail("E_SECRET_INPUT", "rejected secret-shaped input")
    return value


def identity(value):
    safe_text(value, "E_IDENTIFIER_INVALID", MAX_ID)
    if not IDENTIFIER.fullmatch(value):
        fail("E_IDENTIFIER_INVALID", "identity label has invalid characters or length")
    return value


def normalized(value, code="E_KEY_PATH"):
    safe_text(value, code, 512)
    path = PurePosixPath(value)
    if ("\\" in value or path.is_absolute() or path.as_posix() != value or
            any(part in ("", ".", "..") for part in path.parts)):
        fail(code, "relative path is not normalized")
    return value


def clean_abs(path):
    path = os.path.abspath(safe_text(path))
    temp = os.path.abspath(tempfile.gettempdir())
    if path == temp or path.startswith(temp + os.sep):
        path = os.path.realpath(temp) + path[len(temp):]
    return path


def within(path, root):
    try:
        path, root = clean_abs(path), clean_abs(root)
        return os.path.commonpath((path, root)) == root
    except (ValueError, ManifestError):
        return False


def _open_dir(path, prefix, create=False, exclusive_leaf=False):
    """Open an absolute directory component-by-component without following links."""
    path = clean_abs(path)
    parts = Path(path).parts[1:]
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.sep, flags)
    try:
        for index, component in enumerate(parts):
            leaf = index == len(parts) - 1
            try:
                info = os.stat(component, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    fail("E_%s_SYMLINK" % prefix, "symlink in directory path")
                if leaf and exclusive_leaf:
                    fail("E_%s_EXISTS" % prefix, "destination already exists")
            except FileNotFoundError:
                if not create:
                    fail("E_%s_FILE_MISSING" % prefix, "directory path is missing")
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    fail("E_%s_RACE" % prefix, "directory changed during creation")
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    fail("E_%s_SYMLINK" % prefix, "symlink in directory path")
                fail("E_%s_RACE" % prefix, "directory changed during traversal")
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_parent(path, prefix, create=False):
    path = clean_abs(path)
    return _open_dir(os.path.dirname(path), prefix, create=create), os.path.basename(path), path


def _read_relative(root_fd, relative, prefix):
    parts = PurePosixPath(relative).parts
    fd = os.dup(root_fd)
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except OSError as error:
                code = "SYMLINK" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "FILE_MISSING"
                fail("E_%s_%s" % (prefix, code), "corpus path is unsafe or missing")
            os.close(fd)
            fd = next_fd
        try:
            info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                fail("E_%s_SYMLINK" % prefix, "symlink in corpus path")
            if not stat.S_ISREG(info.st_mode):
                fail("E_%s_PATH_INVALID" % prefix, "corpus entry is not a regular file")
            file_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
            with os.fdopen(file_fd, "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            fail("E_%s_FILE_MISSING" % prefix, "allowlisted file is missing")
        except OSError:
            fail("E_%s_RACE" % prefix, "corpus file changed during inspection")
    finally:
        os.close(fd)


def _read_path(path, prefix):
    parent_fd, name, canonical_path = _open_parent(path, prefix)
    try:
        return _read_relative(parent_fd, normalized(name, "E_%s_PATH" % prefix), prefix), canonical_path
    finally:
        os.close(parent_fd)


def _write_relative(root_fd, relative, data):
    parts = PurePosixPath(relative).parts
    fd = os.dup(root_fd)
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=fd)
                next_fd = os.open(component, flags, dir_fd=fd)
            except OSError:
                fail("E_STAGE_SYMLINK", "unsafe staged directory")
            os.close(fd)
            fd = next_fd
        try:
            out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd)
        except OSError:
            fail("E_STAGE_RACE", "staged file destination changed")
        with os.fdopen(out, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)


def _scan_fd(root_fd, prefix):
    found = []
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)

    def walk(fd, relative):
        try:
            names = sorted(os.listdir(fd))
        except OSError:
            fail("E_%s_RACE" % prefix, "corpus directory changed during scan")
        for name in names:
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                fail("E_%s_RACE" % prefix, "corpus entry changed during scan")
            child = relative + (name,)
            if stat.S_ISLNK(info.st_mode):
                fail("E_%s_SYMLINK" % prefix, "symlink in corpus tree")
            if stat.S_ISDIR(info.st_mode):
                try:
                    child_fd = os.open(name, flags, dir_fd=fd)
                except OSError:
                    fail("E_%s_RACE" % prefix, "corpus directory changed during scan")
                try:
                    walk(child_fd, child)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                found.append(PurePosixPath(*child).as_posix())
            else:
                fail("E_%s_PATH_INVALID" % prefix, "non-regular corpus entry")

    walk(root_fd, ())
    return found


def load_json(path, code, expected_sha256=None, digest_code=None):
    data, canonical_path = _read_path(path, code.replace("E_", ""))
    asset = {"path": canonical_path, "bytes": len(data), "sha256": digest(data)}
    if expected_sha256 is not None and asset["sha256"] != expected_sha256:
        fail(digest_code or code, "bound artifact digest mismatch")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        fail(code, "JSON input is unavailable or invalid")
    if not isinstance(value, dict):
        fail(code, "JSON input must be an object")
    return value, asset


def forbidden(relative, supplied):
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    return (relative in supplied or any(part in supplied for part in parts) or
            any(part in FORBIDDEN or part.startswith(("answer-key", "labels.", "label.",
                                                      "attacker-only", "attacker_only"))
                for part in parts))


def inspect_corpus(root, key, dataset, prefix="CORPUS", forbid_paths=(), root_fd=None):
    if not isinstance(key.get("dataset"), str) or key["dataset"] != dataset:
        fail("E_DATASET_MISMATCH", "answer key dataset does not match requested dataset")
    entries = key.get("files")
    defects = key.get("defects")
    if not isinstance(entries, list) or not entries:
        fail("E_KEY_FILES", "answer key requires a non-empty files array")
    if not isinstance(defects, list):
        fail("E_KEY_DEFECTS", "answer key defects must be an array")
    supplied = tuple(normalized(value, "E_FORBID_PATH_INVALID") for value in forbid_paths)
    owned = root_fd is None
    fd = _open_dir(root, prefix) if owned else root_fd
    rows, names = [], set()
    try:
        for entry in entries:
            if not isinstance(entry, dict):
                fail("E_KEY_FILE_ENTRY", "answer key file entry is invalid")
            relative = normalized(entry.get("path"), "E_KEY_PATH")
            size, lines, sha = entry.get("on_disk_bytes"), entry.get("lines"), entry.get("sha256")
            if (isinstance(size, bool) or not isinstance(size, int) or size < 0 or
                    isinstance(lines, bool) or not isinstance(lines, int) or lines < 0 or
                    not isinstance(sha, str) or not HEX64.fullmatch(sha)):
                fail("E_KEY_FILE_ENTRY", "answer key file metadata has invalid types")
            if relative in names:
                fail("E_CORPUS_PATH_DUPLICATE", "answer key file path is duplicated")
            names.add(relative)
            if forbidden(relative, supplied):
                fail("E_FORBIDDEN_STAGED_PATH", "allowlisted path is forbidden in target workspace")
            data = _read_relative(fd, relative, prefix)
            actual = {"path": relative, "bytes": len(data), "lines": data.count(b"\n"),
                      "sha256": digest(data)}
            for field, expected, code in (("bytes", size, "SIZE"), ("lines", lines, "LINE"),
                                          ("sha256", sha, "HASH")):
                if actual[field] != expected:
                    fail("E_%s_%s_MISMATCH" % (prefix, code), "allowlisted metadata mismatch")
            rows.append(actual)
        line_counts = {row["path"]: row["lines"] for row in rows}
        for defect in defects:
            if not isinstance(defect, dict):
                fail("E_KEY_DEFECTS", "answer key defect entry is invalid")
            for field in ("proof_locations", "alternate_proof_locations"):
                proofs = defect.get(field, [])
                if not isinstance(proofs, list):
                    fail("E_KEY_PROOFS", "answer key proof locations must be an array")
                for proof in proofs:
                    if not isinstance(proof, dict):
                        fail("E_KEY_PROOF", "answer key proof entry is invalid")
                    relative = normalized(proof.get("file"), "E_KEY_PROOF")
                    if relative not in names:
                        fail("E_PROOF_FILE_MISSING", "proof path is not allowlisted")
                    start, end = proof.get("line_start"), proof.get("line_end")
                    if (isinstance(start, bool) or not isinstance(start, int) or
                            isinstance(end, bool) or not isinstance(end, int) or
                            start < 1 or end < start or end > line_counts[relative]):
                        fail("E_PROOF_LOCATION_INVALID", "proof line range is outside corpus file")
        actual_names = set(_scan_fd(fd, prefix))
    finally:
        if owned:
            os.close(fd)
    extra = sorted(actual_names - names)
    if names - actual_names:
        fail("E_%s_FILE_MISSING" % prefix, "allowlisted file is missing")
    if prefix == "STAGED" and extra:
        fail("E_STAGED_EXTRA", "staged corpus contains a non-allowlisted file")
    rows.sort(key=lambda row: row["path"])
    return {"files": rows, "manifest_sha256": digest(canonical(rows)),
            "excluded": {"count": len(extra), "paths_sha256": digest(canonical(extra))}}


def key_ids(key):
    defects = key.get("defects")
    if not isinstance(defects, list):
        fail("E_KEY_DEFECTS", "answer key defects must be an array")
    ids = []
    for defect in defects:
        finding_id = defect.get("id") if isinstance(defect, dict) else None
        try:
            identity(finding_id)
        except ManifestError:
            fail("E_EXPECTED_ID", "answer key expected IDs must be bounded unique strings")
        if finding_id in ids:
            fail("E_EXPECTED_ID", "answer key expected IDs must be bounded unique strings")
        ids.append(finding_id)
    return sorted(ids)


def file_asset(path, code):
    data, canonical_path = _read_path(path, code.replace("E_", ""))
    return {"path": canonical_path, "bytes": len(data), "sha256": digest(data)}


def tree_asset(root):
    root = clean_abs(root)
    fd = _open_dir(root, "SKILL")
    try:
        names = [name for name in _scan_fd(fd, "SKILL") if not name.startswith(".git/")]
        rows = []
        for name in names:
            data = _read_relative(fd, name, "SKILL")
            rows.append({"path": name, "bytes": len(data), "sha256": digest(data)})
    finally:
        os.close(fd)
    if not rows:
        fail("E_SKILL_EMPTY", "skill tree is empty")
    try:
        commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"],
                                         stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {"path": root, "file_count": len(rows), "sha256": digest(canonical(rows)),
            "git_commit": commit}


def utc(value):
    if not isinstance(value, str):
        fail("E_HEALTH_TIME", "health timestamps must be UTC strings")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("E_HEALTH_TIME", "health timestamps must be UTC strings")
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        fail("E_HEALTH_TIME", "health timestamps must be UTC strings")
    return parsed


def validate_health(path, lane, provider, requested_model, returned_identity):
    row, asset = load_json(path, "E_HEALTH_JSON")
    if isinstance(row.get("schema"), bool) or row.get("schema") != 1:
        fail("E_HEALTH_SCHEMA", "unsupported health receipt schema")
    for value in (row.get("lane"), row.get("provider"), row.get("requested_model")):
        try:
            identity(value)
        except ManifestError:
            fail("E_HEALTH_IDENTITY", "health identity must be a bounded label")
    checked, expires, now = utc(row.get("checked_at")), utc(row.get("expires_at")), dt.datetime.now(dt.timezone.utc)
    if checked > now + dt.timedelta(seconds=MAX_FUTURE_SKEW):
        fail("E_HEALTH_FUTURE", "health check time exceeds clock skew")
    if now - checked > dt.timedelta(seconds=MAX_HEALTH_SECONDS) or expires <= now:
        fail("E_HEALTH_STALE", "health receipt is stale")
    if expires <= checked or expires - checked > dt.timedelta(seconds=MAX_HEALTH_SECONDS):
        fail("E_HEALTH_LIFETIME", "health receipt lifetime exceeds 15 minutes")
    if (row["lane"], row["provider"], row["requested_model"]) != (lane, provider, requested_model):
        fail("E_HEALTH_IDENTITY_MISMATCH", "health receipt target identity does not match")
    if row.get("shape") != "history":
        fail("E_HEALTH_SHAPE", "health receipt did not probe history shape")
    tools = row.get("tools")
    if isinstance(tools, bool) or not isinstance(tools, int) or tools < 25:
        fail("E_HEALTH_TOOLS", "health receipt carried fewer than 25 tools")
    sizes = row.get("sizes_kb")
    if (not isinstance(sizes, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in sizes) or
            not {100, 250, 400}.issubset(set(sizes))):
        fail("E_HEALTH_SIZES", "health receipt lacks required request sizes")
    history = row.get("history")
    if not isinstance(history, list) or not history:
        fail("E_HEALTH_HISTORY", "health receipt history is empty or invalid")
    identities, seen = set(), set()
    for item in history:
        if not isinstance(item, dict):
            fail("E_HEALTH_HISTORY", "health receipt history row is invalid")
        size, status_code, returned = item.get("size_kb"), item.get("status"), item.get("returned_model")
        if (isinstance(size, bool) or not isinstance(size, int) or
                isinstance(status_code, bool) or status_code != 200):
            fail("E_HEALTH_HISTORY", "health receipt history contains an unhealthy result")
        if not isinstance(returned, str) or not returned:
            fail("E_HEALTH_RETURNED_IDENTITY", "returned identity is empty or mixed")
        try:
            identity(returned)
        except ManifestError:
            fail("E_HEALTH_RETURNED_IDENTITY", "returned identity is empty or mixed")
        seen.add(size)
        identities.add(returned)
    if not {100, 250, 400}.issubset(seen) or identities != {returned_identity}:
        fail("E_HEALTH_RETURNED_IDENTITY", "returned identity is empty or mixed")
    if not isinstance(row.get("verdict"), str) or row["verdict"] != "HEALTHY":
        fail("E_HEALTH_VERDICT", "health verdict is not HEALTHY")
    return asset


def stage_corpus(source_corpus, answer_key, dataset, destination, forbid_paths=()):
    identity(dataset)
    for value in (source_corpus, answer_key, destination) + tuple(forbid_paths):
        safe_text(value)
    if within(answer_key, destination):
        fail("E_KEY_TARGET_VISIBLE", "answer key is inside target workspace")
    key, _ = load_json(answer_key, "E_ANSWER_KEY_JSON")
    source_fd = _open_dir(source_corpus, "CORPUS")
    try:
        source = inspect_corpus(source_corpus, key, dataset, forbid_paths=forbid_paths, root_fd=source_fd)
        stage_fd = _open_dir(destination, "STAGE", create=True, exclusive_leaf=True)
        try:
            for row in source["files"]:
                data = _read_relative(source_fd, row["path"], "CORPUS")
                if digest(data) != row["sha256"]:
                    fail("E_CORPUS_RACE", "source changed during staging")
                _write_relative(stage_fd, row["path"], data)
            os.fsync(stage_fd)
            staged = inspect_corpus(destination, key, dataset, "STAGED", forbid_paths, stage_fd)
        finally:
            os.close(stage_fd)
    finally:
        os.close(source_fd)
    return {"included_count": len(source["files"]), "excluded_count": source["excluded"]["count"],
            "corpus_manifest_sha256": source["manifest_sha256"],
            "staged_manifest_sha256": staged["manifest_sha256"]}


def _prepare_manifest(trace, row):
    trace_fd = _open_dir(trace, "TRACE", create=True)
    temporary = ".run-manifest.%d.%s" % (os.getpid(), digest(os.urandom(16))[:16])
    data = canonical(row) + b"\n"
    try:
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=trace_fd)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            fail("E_MANIFEST_PREPARE", "run manifest could not be prepared")
        return trace_fd, temporary, data
    except Exception:
        try:
            os.unlink(temporary, dir_fd=trace_fd)
        except FileNotFoundError:
            pass
        os.close(trace_fd)
        raise


def _discard_prepared(trace_fd, temporary):
    try:
        os.unlink(temporary, dir_fd=trace_fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(trace_fd)


def _publish_prepared(trace_fd, temporary):
    try:
        os.link(temporary, "run-manifest.json", src_dir_fd=trace_fd,
                dst_dir_fd=trace_fd, follow_symlinks=False)
    except FileExistsError:
        return False
    except OSError:
        fail("E_MANIFEST_PUBLISH", "run manifest could not be published")
    try:
        os.fsync(trace_fd)
    except OSError:
        fail("E_MANIFEST_PUBLISH", "run manifest directory could not be synchronized")
    return True


def _manifest_state(trace_fd, expected):
    try:
        existing = _read_relative(trace_fd, "run-manifest.json", "MANIFEST")
    except ManifestError as error:
        if error.code == "E_MANIFEST_FILE_MISSING":
            return "missing"
        fail("E_MANIFEST_CONFLICT", "run manifest path conflicts with prepared manifest")
    return "exact" if existing == expected else "conflict"


def _atomic_manifest(trace, row):
    trace_fd, temporary, _ = _prepare_manifest(trace, row)
    try:
        if not _publish_prepared(trace_fd, temporary):
            fail("E_MANIFEST_EXISTS", "run manifest already exists")
    finally:
        _discard_prepared(trace_fd, temporary)


def _commitment_fd(path, create=True):
    try:
        parent_fd, name, canonical_path = _open_parent(path, "COMMITMENT", create=create)
    except ManifestError as error:
        if not create and error.code == "E_COMMITMENT_FILE_MISSING":
            fail("E_COMMITMENT_MISSING", "external manifest commitment is missing")
        raise
    try:
        flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags | (os.O_CREAT if create else 0), 0o600, dir_fd=parent_fd)
    except FileNotFoundError:
        os.close(parent_fd)
        fail("E_COMMITMENT_MISSING", "external manifest commitment is missing")
    except OSError:
        os.close(parent_fd)
        fail("E_COMMITMENT_SYMLINK", "commitment file path is unsafe")
    return fd, parent_fd, canonical_path


def _commitment_key(path):
    """Consume the controller's opaque, pre-provisioned 32-byte trust anchor.

    Random generation and pinning belong to the trusted controller.  Byte content
    cannot prove how a key was generated, and this tool never creates, chooses,
    emits, persists, or copies the secret to another location.
    """
    parent_fd, name, canonical_path = _open_parent(path, "COMMITMENT_KEY")
    try:
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        except FileNotFoundError:
            fail("E_COMMITMENT_KEY_MISSING", "commitment key is missing")
        except OSError as error:
            code = "SYMLINK" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "OPEN"
            fail("E_COMMITMENT_KEY_%s" % code, "commitment key path is unsafe")
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                fail("E_COMMITMENT_KEY_TYPE", "commitment key must be a regular file")
            if info.st_uid != os.geteuid():
                fail("E_COMMITMENT_KEY_OWNER", "commitment key must be owned by controller euid")
            if stat.S_IMODE(info.st_mode) != 0o600:
                fail("E_COMMITMENT_KEY_MODE", "commitment key mode must be 0600")
            if info.st_size != 32:
                fail("E_COMMITMENT_KEY_BOUNDS", "commitment key must contain exactly 32 bytes")
            data = b""
            while len(data) < info.st_size:
                data += os.read(fd, min(4096, info.st_size - len(data)))
            if len(data) != info.st_size:
                fail("E_COMMITMENT_KEY_READ", "commitment key could not be read completely")
            return data, digest(data), canonical_path
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _commitment_payload(row, committed_at, key_id):
    trace = row["trace"]
    return {"schema": 2, "run_tag": row["run_tag"], "trace_dir": trace["path"],
            "trace_identity_sha256": trace["identity_sha256"],
            "manifest_sha256": row["manifest_sha256"], "committed_at": committed_at,
            "key_id": key_id}


def _sign_commitment(payload, key):
    return hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()


def _commitment_rows(fd):
    size = os.fstat(fd).st_size
    if size > 8 * 1024 * 1024:
        fail("E_COMMITMENT_BOUNDS", "commitment file is too large")
    os.lseek(fd, 0, os.SEEK_SET)
    data = b""
    while len(data) < size:
        data += os.read(fd, min(65536, size - len(data)))
    rows = []
    for raw in data.splitlines():
        if len(raw) > 1024:
            fail("E_COMMITMENT_BOUNDS", "commitment row is too large")
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            fail("E_COMMITMENT_SCHEMA", "commitment row is invalid")
        if not isinstance(row, dict) or set(row) != COMMITMENT_KEYS or row.get("schema") != 2:
            fail("E_COMMITMENT_SCHEMA", "commitment row is invalid")
        try:
            identity(row.get("run_tag"))
            safe_text(row.get("trace_dir"))
            utc(row.get("committed_at"))
        except ManifestError:
            fail("E_COMMITMENT_SCHEMA", "commitment row is invalid")
        hashes = (row.get("trace_identity_sha256"), row.get("manifest_sha256"),
                  row.get("key_id"), row.get("hmac_sha256"))
        if any(not isinstance(value, str) or not HEX64.fullmatch(value) for value in hashes):
            fail("E_COMMITMENT_SCHEMA", "commitment row is invalid")
        rows.append(row)
    return rows


def _authenticated_commitment(item, row, key, key_id):
    payload = {name: item[name] for name in COMMITMENT_PAYLOAD_KEYS}
    if (item["key_id"] != key_id or
            not hmac.compare_digest(item["hmac_sha256"], _sign_commitment(payload, key))):
        fail("E_COMMITMENT_AUTH", "external manifest commitment authentication failed")
    expected = (2, row["run_tag"], row["trace"]["path"],
                row["trace"]["identity_sha256"], row["manifest_sha256"], key_id)
    actual = (item["schema"], item["run_tag"], item["trace_dir"],
              item["trace_identity_sha256"], item["manifest_sha256"], item["key_id"])
    if actual != expected:
        fail("E_COMMITMENT_CONFLICT", "run tag has a conflicting commitment")


def _append_commitment(fd, parent_fd, line):
    """Append one complete durable row or restore the prior durable length."""
    start = os.lseek(fd, 0, os.SEEK_END)
    offset = 0
    try:
        while offset < len(line):
            written = os.write(fd, line[offset:])
            if not isinstance(written, int) or written <= 0:
                raise OSError(errno.EIO, "commitment write made no progress")
            offset += written
        os.fsync(fd)
        os.fsync(parent_fd)
    except OSError:
        try:
            os.ftruncate(fd, start)
            os.fsync(fd)
            os.fsync(parent_fd)
        except OSError:
            fail("E_COMMITMENT_ROLLBACK", "commitment append rollback failed")
        fail("E_COMMITMENT_WRITE", "commitment append failed")


def _write_manifest_and_commit(trace, commitment_file, row, key, key_id):
    fd, parent_fd, _ = _commitment_fd(commitment_file)
    trace_fd = temporary = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        trace_fd, temporary, manifest_bytes = _prepare_manifest(trace, row)
        matches = [item for item in _commitment_rows(fd)
                   if item["run_tag"] == row["run_tag"]]
        state = _manifest_state(trace_fd, manifest_bytes)
        if len(matches) > 1:
            fail("E_COMMITMENT_CONFLICT", "run tag has multiple commitments")
        if matches:
            _authenticated_commitment(matches[0], row, key, key_id)
            if state == "exact":
                fail("E_COMMITMENT_DUPLICATE", "run tag already has a commitment")
            if state == "conflict":
                fail("E_MANIFEST_CONFLICT", "run manifest conflicts with commitment")
            # A durable authenticated row is the recovery journal for a crash
            # between ledger fsync and no-replace manifest publication.
            if not _publish_prepared(trace_fd, temporary):
                state = _manifest_state(trace_fd, manifest_bytes)
                if state != "exact":
                    fail("E_MANIFEST_CONFLICT", "run manifest publication lost a race")
            return
        if state == "conflict":
            fail("E_MANIFEST_CONFLICT", "run manifest conflicts with prepared manifest")
        payload = _commitment_payload(row, now_text(), key_id)
        commitment = dict(payload)
        commitment["hmac_sha256"] = _sign_commitment(payload, key)
        _append_commitment(fd, parent_fd, canonical(commitment) + b"\n")
        # If the exact manifest predates the ledger (the legacy partial order),
        # the authenticated append above repairs it.  Otherwise publish last.
        if state == "missing" and not _publish_prepared(trace_fd, temporary):
            state = _manifest_state(trace_fd, manifest_bytes)
            if state != "exact":
                fail("E_MANIFEST_CONFLICT", "run manifest publication lost a race")
    finally:
        if trace_fd is not None:
            _discard_prepared(trace_fd, temporary)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
        os.close(parent_fd)


def _verify_commitment(path, row, trace, key, key_id):
    fd, parent_fd, canonical_path = _commitment_fd(path, create=False)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        rows = _commitment_rows(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        os.close(parent_fd)
    matches = [item for item in rows if item["run_tag"] == row.get("run_tag")]
    if not rows:
        fail("E_COMMITMENT_MISSING", "external manifest commitment is missing")
    if len(matches) != 1:
        fail("E_COMMITMENT_MISMATCH", "external manifest commitment does not match")
    commitment = matches[0]
    payload = {name: commitment[name] for name in COMMITMENT_PAYLOAD_KEYS}
    if (commitment["key_id"] != key_id or
            not hmac.compare_digest(commitment["hmac_sha256"], _sign_commitment(payload, key))):
        fail("E_COMMITMENT_AUTH", "external manifest commitment authentication failed")
    expected = (clean_abs(trace), row.get("trace", {}).get("identity_sha256"),
                row.get("manifest_sha256"))
    if (commitment["trace_dir"], commitment["trace_identity_sha256"],
            commitment["manifest_sha256"]) != expected:
        fail("E_COMMITMENT_MISMATCH", "external manifest commitment does not match")
    return canonical_path


def create_manifest(trace, run_tag, dataset, arm, source_corpus, answer_key, renderer,
                    prompt, skill_root, runner, scorer, triage_checker, stop_checker,
                    citation_checker, target_cli, target_version, requested_model, provider,
                    expected_returned_identity, lane, health_receipt, controller_parent,
                    commitment_file, commitment_key, staged_corpus_destination, forbid_paths=()):
    for value in (run_tag, dataset, arm, target_version, requested_model, provider,
                  expected_returned_identity, lane):
        identity(value)
    paths = (trace, source_corpus, answer_key, renderer, prompt, skill_root, runner, scorer,
             triage_checker, stop_checker, citation_checker, target_cli, health_receipt,
             controller_parent, commitment_file, commitment_key, staged_corpus_destination)
    for value in paths + tuple(forbid_paths):
        safe_text(value)
    trace, staged = clean_abs(trace), clean_abs(staged_corpus_destination)
    if within(answer_key, trace) or within(answer_key, staged):
        fail("E_KEY_TARGET_VISIBLE", "answer key is inside target workspace")
    if any(within(commitment_file, root) for root in (trace, source_corpus, staged, skill_root)):
        fail("E_COMMITMENT_LOCATION", "commitment file must remain controller-owned")
    key_path = clean_abs(commitment_key)
    key_roots = (trace, source_corpus, staged, skill_root, os.path.dirname(clean_abs(commitment_file)))
    if any(within(key_path, root) for root in key_roots):
        fail("E_COMMITMENT_KEY_LOCATION", "commitment key must remain controller-held")
    if not all(within(path, skill_root) for path in (triage_checker, stop_checker, citation_checker)):
        fail("E_CHECKER_NOT_VERSION_OWNED", "checker is outside selected skill version")
    key, key_asset = load_json(answer_key, "E_ANSWER_KEY_JSON")
    source = inspect_corpus(source_corpus, key, dataset, forbid_paths=forbid_paths)
    staged_info = inspect_corpus(staged, key, dataset, "STAGED", forbid_paths)
    if source["manifest_sha256"] != staged_info["manifest_sha256"]:
        fail("E_STAGED_MANIFEST_MISMATCH", "staged corpus does not match source corpus")
    artifacts = {"answer_key": key_asset,
                 "renderer": file_asset(renderer, "E_RENDERER_FILE"),
                 "prompt": file_asset(prompt, "E_PROMPT_FILE"),
                 "runner": file_asset(runner, "E_RUNNER_FILE"),
                 "scorer": file_asset(scorer, "E_SCORER_FILE"),
                 "triage_checker": file_asset(triage_checker, "E_TRIAGE_CHECKER_FILE"),
                 "stop_checker": file_asset(stop_checker, "E_STOP_CHECKER_FILE"),
                 "citation_checker": file_asset(citation_checker, "E_CITATION_CHECKER_FILE"),
                 "target_cli": file_asset(target_cli, "E_TARGET_CLI_FILE"),
                 "controller_parent": file_asset(controller_parent, "E_CONTROLLER_PARENT_FILE")}
    health = validate_health(health_receipt, lane, provider, requested_model,
                             expected_returned_identity)
    key_bytes, key_id, _ = _commitment_key(key_path)
    target = {"version": target_version, "requested_model": requested_model,
              "expected_returned_identity": expected_returned_identity,
              "provider": provider, "lane": lane}
    target["identity_sha256"] = digest(canonical(target))
    commitment_path = clean_abs(commitment_file)
    expected_ids = key_ids(key)
    row = {"schema": 2, "run_tag": run_tag, "dataset": dataset, "arm": arm,
           "trace": {"path": trace, "identity_sha256": digest(trace.encode())},
           "commitment": {"path": commitment_path,
                          "identity_sha256": digest(commitment_path.encode()), "key_id": key_id},
           "corpus": {"source_path": clean_abs(source_corpus), "staged_path": staged,
                      "files": source["files"], "source_manifest_sha256": source["manifest_sha256"],
                      "staged_manifest_sha256": staged_info["manifest_sha256"],
                      "excluded": source["excluded"], "forbid_paths": sorted(forbid_paths)},
           "expected": {"ids": expected_ids, "id_count": len(expected_ids),
                        "file_count": len(source["files"])},
           "artifacts": artifacts, "skill": tree_asset(skill_root), "target": target,
           "health_receipt": health}
    row["manifest_sha256"] = digest(canonical(row))
    _write_manifest_and_commit(trace, commitment_file, row, key_bytes, key_id)
    return row


def verify_manifest(trace, commitment_file, commitment_key):
    safe_text(commitment_key)
    trace = clean_abs(trace)
    trace_fd = _open_dir(trace, "TRACE")
    try:
        data = _read_relative(trace_fd, "run-manifest.json", "MANIFEST")
    finally:
        os.close(trace_fd)
    try:
        row = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        fail("E_MANIFEST_JSON", "run manifest is invalid")
    if not isinstance(row, dict) or row.get("schema") != 2:
        fail("E_MANIFEST_SCHEMA", "run manifest schema is invalid")
    seal = row.get("manifest_sha256")
    unsigned = dict(row)
    unsigned.pop("manifest_sha256", None)
    if not isinstance(seal, str) or seal != digest(canonical(unsigned)):
        fail("E_MANIFEST_DIGEST_MISMATCH", "manifest self-hash mismatch")
    commitment = row.get("commitment")
    trace_identity = row.get("trace")
    if not isinstance(commitment, dict) or not isinstance(trace_identity, dict):
        fail("E_MANIFEST_SCHEMA", "run manifest identity is invalid")
    supplied_commitment = clean_abs(commitment_file)
    if (commitment.get("path") != supplied_commitment or
            commitment.get("identity_sha256") != digest(supplied_commitment.encode())):
        fail("E_COMMITMENT_MISMATCH", "explicit commitment identity does not match")
    if trace_identity.get("path") != trace or trace_identity.get("identity_sha256") != digest(trace.encode()):
        fail("E_TRACE_IDENTITY_MISMATCH", "trace identity mismatch")
    key_path = clean_abs(commitment_key)
    key_roots = (trace, row.get("corpus", {}).get("source_path"),
                 row.get("corpus", {}).get("staged_path"), row.get("skill", {}).get("path"),
                 os.path.dirname(supplied_commitment))
    if any(not isinstance(root, str) or within(key_path, root) for root in key_roots):
        fail("E_COMMITMENT_KEY_LOCATION", "commitment key must remain controller-held")
    key_bytes, key_id, _ = _commitment_key(key_path)
    if commitment.get("key_id") != key_id:
        fail("E_COMMITMENT_AUTH", "external manifest commitment authentication failed")
    _verify_commitment(commitment_file, row, trace, key_bytes, key_id)
    artifacts = row.get("artifacts")
    if not isinstance(artifacts, dict):
        fail("E_MANIFEST_SCHEMA", "artifact inventory is invalid")
    answer_asset = artifacts.get("answer_key")
    if not isinstance(answer_asset, dict) or not isinstance(answer_asset.get("path"), str):
        fail("E_MANIFEST_SCHEMA", "artifact inventory is invalid")
    key, _ = load_json(answer_asset["path"], "E_ANSWER_KEY_JSON",
                       answer_asset.get("sha256"), "E_ANSWER_KEY_DIGEST_MISMATCH")
    required = ("renderer", "prompt", "runner", "scorer", "triage_checker",
                "stop_checker", "citation_checker", "target_cli", "controller_parent")
    for name in required:
        asset = artifacts.get(name)
        if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
            fail("E_MANIFEST_SCHEMA", "artifact inventory is invalid")
        current = file_asset(asset["path"], "E_%s_FILE" % name.upper())
        if current["sha256"] != asset.get("sha256"):
            fail("E_%s_DIGEST_MISMATCH" % name.upper(), "bound artifact digest mismatch")
    skill_row = row.get("skill")
    if not isinstance(skill_row, dict) or not isinstance(skill_row.get("path"), str):
        fail("E_MANIFEST_SCHEMA", "skill identity is invalid")
    skill = tree_asset(skill_row["path"])
    if (skill["sha256"], skill["git_commit"]) != (skill_row.get("sha256"), skill_row.get("git_commit")):
        fail("E_SKILL_DIGEST_MISMATCH", "bound skill identity mismatch")
    corpus = row.get("corpus")
    if not isinstance(corpus, dict):
        fail("E_MANIFEST_SCHEMA", "corpus identity is invalid")
    supplied = corpus.get("forbid_paths")
    if not isinstance(supplied, list):
        fail("E_MANIFEST_SCHEMA", "corpus identity is invalid")
    source = inspect_corpus(corpus.get("source_path"), key, row.get("dataset"), forbid_paths=tuple(supplied))
    staged = inspect_corpus(corpus.get("staged_path"), key, row.get("dataset"), "STAGED", tuple(supplied))
    if source["manifest_sha256"] != corpus.get("source_manifest_sha256") or source["excluded"] != corpus.get("excluded"):
        fail("E_CORPUS_MANIFEST_MISMATCH", "source corpus manifest changed")
    if staged["manifest_sha256"] != corpus.get("staged_manifest_sha256"):
        fail("E_STAGED_MANIFEST_MISMATCH", "staged corpus manifest changed")
    expected = row.get("expected")
    ids = key_ids(key)
    if (not isinstance(expected, dict) or expected.get("ids") != ids or
            expected.get("id_count") != len(ids) or expected.get("file_count") != len(source["files"])):
        fail("E_EXPECTED_ID", "expected inventory changed")
    target = row.get("target")
    if not isinstance(target, dict):
        fail("E_MANIFEST_SCHEMA", "target identity is invalid")
    target_unsigned = dict(target)
    target_seal = target_unsigned.pop("identity_sha256", None)
    if target_seal != digest(canonical(target_unsigned)):
        fail("E_TARGET_IDENTITY_MISMATCH", "target identity digest mismatch")
    health_row = row.get("health_receipt")
    if not isinstance(health_row, dict) or not isinstance(health_row.get("path"), str):
        fail("E_MANIFEST_SCHEMA", "health identity is invalid")
    health = validate_health(health_row["path"], target.get("lane"), target.get("provider"),
                             target.get("requested_model"), target.get("expected_returned_identity"))
    if health["sha256"] != health_row.get("sha256"):
        fail("E_HEALTH_RECEIPT_DIGEST_MISMATCH", "health receipt digest changed")
    return row


def parser():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    for name in ("source-corpus", "answer-key", "dataset", "destination"):
        stage.add_argument("--" + name, required=True)
    stage.add_argument("--forbid-path", action="append", default=[])
    stage.add_argument("--json", action="store_true")
    create = sub.add_parser("create")
    create.add_argument("trace")
    for name in ("run-tag", "dataset", "arm", "source-corpus", "answer-key", "renderer", "prompt",
                 "skill-root", "runner", "scorer", "triage-checker", "stop-checker", "citation-checker",
                 "target-cli", "target-version", "requested-model", "provider", "expected-returned-identity",
                 "lane", "health-receipt", "controller-parent", "commitment-file", "commitment-key",
                 "staged-corpus-destination"):
        create.add_argument("--" + name, required=True)
    create.add_argument("--forbid-path", action="append", default=[])
    create.add_argument("--json", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("trace")
    verify.add_argument("--commitment-file", required=True)
    verify.add_argument("--commitment-key", required=True)
    verify.add_argument("--json", action="store_true")
    return ap


def summary(command, row):
    if command == "stage":
        return row
    return {"schema": row["schema"], "run_tag": row["run_tag"], "dataset": row["dataset"],
            "arm": row["arm"], "manifest_sha256": row["manifest_sha256"],
            "expected_id_count": row["expected"]["id_count"],
            "corpus_file_count": row["expected"]["file_count"]}


def main():
    values = vars(parser().parse_args())
    command = values.pop("command")
    as_json = values.pop("json")
    if "forbid_path" in values:
        values["forbid_paths"] = tuple(values.pop("forbid_path"))
    try:
        if command == "stage":
            row = stage_corpus(**values)
        elif command == "create":
            row = create_manifest(**values)
        else:
            row = verify_manifest(**values)
    except ManifestError as error:
        print(str(error), file=sys.stderr)
        return 2
    result = summary(command, row)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("%s: %s" % (command.upper(), result.get("manifest_sha256",
                                                     result.get("staged_manifest_sha256"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
