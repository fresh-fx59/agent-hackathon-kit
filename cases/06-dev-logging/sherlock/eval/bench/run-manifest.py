#!/usr/bin/env python3
"""Seal benchmark identity and stage only answer-key-allowlisted corpus bytes."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile


SECRET = re.compile(r"(?:bearer\s+|(?:sk|ghp|glpat|xox[baprs])-|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN .*PRIVATE KEY-----|(?:password|token|api[_-]?key)\s*[:=])", re.I)
BUILTIN_FORBIDDEN = {"answer-key", "answer_key", "labels", "label", "facts.json",
                     "ground-truth", "ground_truth", "attacker-only", "attacker_only"}


class ManifestError(ValueError):
    pass


def fail(code, message):
    raise ManifestError("%s: %s" % (code, message))


def safe(*values):
    for value in values:
        if isinstance(value, str) and SECRET.search(value):
            fail("E_SECRET_INPUT", "rejected secret-shaped input")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def load_json(path, code):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError):
        fail(code, "JSON input is unavailable or invalid")
    if not isinstance(value, dict):
        fail(code, "JSON input must be an object")
    return value


def normalized(relative, code="E_CORPUS_PATH_INVALID"):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        fail(code, "relative path is not normalized")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(p in ("", ".", "..") for p in path.parts):
        fail(code, "relative path is not normalized")
    if path.as_posix() != relative:
        fail(code, "relative path is not normalized")
    return relative


def within(path, root):
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root)
    except ValueError:
        return False


def reject_symlink_components(path, code):
    current = os.path.dirname(os.path.abspath(path))
    while not os.path.lexists(current):
        parent = os.path.dirname(current)
        if parent == current: break
        current = parent
    if os.path.islink(current):
        fail(code, "symlink in destination path")


def read_relative(root, relative, prefix):
    root = os.path.realpath(root)
    current = root
    for component in PurePosixPath(relative).parts:
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except OSError:
            fail("E_%s_FILE_MISSING" % prefix, "allowlisted file is missing")
        if stat.S_ISLNK(info.st_mode):
            fail("E_%s_SYMLINK" % prefix, "symlink in corpus path")
    if not within(current, root) or not stat.S_ISREG(info.st_mode):
        fail("E_%s_PATH_INVALID" % prefix, "corpus path escaped or is not a file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(current, flags)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read()
    except OSError:
        fail("E_%s_RACE" % prefix, "corpus file changed during inspection")
    return data


def all_tree_entries(root, prefix):
    found = []
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in list(dirs) + list(files):
            full = os.path.join(base, name)
            if os.path.islink(full):
                fail("E_%s_SYMLINK" % prefix, "symlink in corpus tree")
        for name in files:
            full = os.path.join(base, name)
            if not os.path.isfile(full):
                fail("E_%s_PATH_INVALID" % prefix, "non-regular corpus entry")
            found.append(Path(full).relative_to(root).as_posix())
    return sorted(found)


def forbidden(relative, supplied):
    parts = [p.lower() for p in PurePosixPath(relative).parts]
    for part in parts:
        if (part in BUILTIN_FORBIDDEN or part.startswith("answer-key") or
                part.startswith("labels.") or part.startswith("label.") or
                part.startswith("attacker-only") or part.startswith("attacker_only")):
            return True
    return relative in supplied or any(p in supplied for p in parts)


def inspect_corpus(root, key, dataset, prefix="CORPUS", forbid_paths=()):
    if os.path.islink(os.path.abspath(root)):
        fail("E_%s_SYMLINK" % prefix, "corpus root is a symlink")
    if key.get("dataset") != dataset:
        fail("E_DATASET_MISMATCH", "answer key dataset does not match requested dataset")
    entries = key.get("files")
    if not isinstance(entries, list) or not entries:
        fail("E_KEY_FILES", "answer key requires a non-empty files array")
    supplied = tuple(normalized(p, "E_FORBID_PATH_INVALID") for p in forbid_paths)
    rows, names = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            fail("E_KEY_FILES", "answer key file entry is invalid")
        relative = normalized(entry.get("path"))
        if relative in names:
            fail("E_CORPUS_PATH_DUPLICATE", "answer key file path is duplicated")
        names.add(relative)
        if forbidden(relative, supplied):
            fail("E_FORBIDDEN_STAGED_PATH", "allowlisted path is forbidden in target workspace")
        data = read_relative(root, relative, prefix)
        size, lines, sha = len(data), data.count(b"\n"), digest(data)
        if entry.get("on_disk_bytes") != size:
            fail("E_%s_SIZE_MISMATCH" % prefix, "allowlisted byte size mismatch")
        if entry.get("lines") != lines:
            fail("E_%s_LINE_MISMATCH" % prefix, "allowlisted line count mismatch")
        if entry.get("sha256") != sha:
            fail("E_%s_HASH_MISMATCH" % prefix, "allowlisted SHA-256 mismatch")
        rows.append({"path": relative, "bytes": size, "lines": lines, "sha256": sha})
    line_counts = {row["path"]: row["lines"] for row in rows}
    for defect in key.get("defects", []):
        if not isinstance(defect, dict):
            fail("E_KEY_DEFECTS", "answer key defect entry is invalid")
        for field in ("proof_locations", "alternate_proof_locations"):
            for proof in defect.get(field, []):
                relative = normalized(proof.get("file") if isinstance(proof, dict) else None)
                if relative not in names:
                    fail("E_PROOF_FILE_MISSING", "proof path is not allowlisted in corpus")
                start, end = proof.get("line_start"), proof.get("line_end")
                if (not isinstance(start, int) or isinstance(start, bool) or
                        not isinstance(end, int) or isinstance(end, bool) or
                        start < 1 or end < start or end > line_counts[relative]):
                    fail("E_PROOF_LOCATION_INVALID", "proof line range is outside corpus file")
    actual = set(all_tree_entries(root, prefix))
    missing = names - actual
    if missing:
        fail("E_%s_FILE_MISSING" % prefix, "allowlisted file is missing")
    extra = sorted(actual - names)
    if prefix == "STAGED" and extra:
        fail("E_STAGED_EXTRA", "staged corpus contains a non-allowlisted file")
    rows.sort(key=lambda row: row["path"])
    return {"files": rows, "manifest_sha256": digest(canonical(rows)),
            "excluded": {"count": len(extra), "paths_sha256": digest(canonical(extra))}}


def key_ids(key):
    ids = []
    for defect in key.get("defects", []):
        finding_id = defect.get("id") if isinstance(defect, dict) else None
        if not isinstance(finding_id, str) or not finding_id or finding_id in ids:
            fail("E_EXPECTED_ID", "answer key expected IDs must be unique strings")
        ids.append(finding_id)
    return sorted(ids)


def file_asset(path, code):
    try:
        data = Path(path).read_bytes()
    except OSError:
        fail(code, "bound file is unavailable")
    return {"path": os.path.realpath(path), "bytes": len(data), "sha256": digest(data)}


def tree_asset(root):
    root = os.path.realpath(root)
    rows = []
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in dirs + sorted(files):
            if os.path.islink(os.path.join(base, name)):
                fail("E_SKILL_SYMLINK", "skill tree contains a symlink")
        for name in sorted(files):
            full = os.path.join(base, name)
            data = Path(full).read_bytes()
            rows.append({"path": Path(full).relative_to(root).as_posix(),
                         "bytes": len(data), "sha256": digest(data)})
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
    row = load_json(path, "E_HEALTH_JSON")
    if row.get("schema") != 1: fail("E_HEALTH_SCHEMA", "unsupported health receipt schema")
    checked, expires, now = utc(row.get("checked_at")), utc(row.get("expires_at")), dt.datetime.now(dt.timezone.utc)
    if checked > now or expires <= checked: fail("E_HEALTH_TIME", "health receipt timestamps are inconsistent")
    if expires <= now: fail("E_HEALTH_STALE", "health receipt has expired")
    if (row.get("lane"), row.get("provider"), row.get("requested_model")) != (lane, provider, requested_model):
        fail("E_HEALTH_IDENTITY_MISMATCH", "health receipt target identity does not match")
    if row.get("shape") != "history": fail("E_HEALTH_SHAPE", "health receipt did not probe history shape")
    if isinstance(row.get("tools"), bool) or not isinstance(row.get("tools"), int) or row["tools"] < 25:
        fail("E_HEALTH_TOOLS", "health receipt carried fewer than 25 tools")
    sizes = row.get("sizes_kb")
    if not isinstance(sizes, list) or not {100, 250, 400}.issubset(set(sizes)):
        fail("E_HEALTH_SIZES", "health receipt lacks required request sizes")
    history = row.get("history")
    if not isinstance(history, list) or not history:
        fail("E_HEALTH_HISTORY", "health receipt history is empty or invalid")
    identities, seen = set(), set()
    for item in history:
        if not isinstance(item, dict) or item.get("status") != 200:
            fail("E_HEALTH_HISTORY", "health receipt history contains an unhealthy result")
        seen.add(item.get("size_kb"))
        identity = item.get("returned_model")
        if not isinstance(identity, str) or not identity:
            fail("E_HEALTH_RETURNED_IDENTITY", "returned identity is empty or mixed")
        identities.add(identity)
    if not {100, 250, 400}.issubset(seen) or identities != {returned_identity}:
        fail("E_HEALTH_RETURNED_IDENTITY", "returned identity is empty or mixed")
    if row.get("verdict") != "HEALTHY": fail("E_HEALTH_VERDICT", "health verdict is not HEALTHY")
    return file_asset(path, "E_HEALTH_FILE")


def stage_corpus(source_corpus, answer_key, dataset, destination, forbid_paths=()):
    safe(source_corpus, answer_key, dataset, destination, *forbid_paths)
    reject_symlink_components(destination, "E_STAGE_SYMLINK")
    if within(answer_key, destination): fail("E_KEY_TARGET_VISIBLE", "answer key is inside target workspace")
    key = load_json(answer_key, "E_ANSWER_KEY_JSON")
    source = inspect_corpus(source_corpus, key, dataset, forbid_paths=forbid_paths)
    destination = os.path.abspath(destination)
    if os.path.lexists(destination): fail("E_STAGE_EXISTS", "staged corpus destination already exists")
    created = False
    try:
        os.makedirs(destination); created = True
        for row in source["files"]:
            data = read_relative(source_corpus, row["path"], "CORPUS")
            if digest(data) != row["sha256"]: fail("E_CORPUS_RACE", "source changed during staging")
            parts = PurePosixPath(row["path"]).parts
            parent_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY |
                                getattr(os, "O_NOFOLLOW", 0))
            try:
                for component in parts[:-1]:
                    try: os.mkdir(component, 0o700, dir_fd=parent_fd)
                    except FileExistsError: pass
                    next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY |
                                      getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
                    os.close(parent_fd); parent_fd = next_fd
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data); handle.flush(); os.fsync(handle.fileno())
            finally:
                os.close(parent_fd)
        staged = inspect_corpus(destination, key, dataset, "STAGED", forbid_paths)
    except Exception:
        if created: shutil.rmtree(destination)
        raise
    return {"included_count": len(source["files"]), "excluded_count": source["excluded"]["count"],
            "corpus_manifest_sha256": source["manifest_sha256"],
            "staged_manifest_sha256": staged["manifest_sha256"]}


def atomic_manifest(path, row):
    directory = os.path.dirname(path); os.makedirs(directory, exist_ok=True)
    lock = path + ".lock"
    try: lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError: fail("E_MANIFEST_LOCKED", "manifest creation is already active")
    try:
        if os.path.exists(path): fail("E_MANIFEST_EXISTS", "run manifest already exists")
        fd, temporary = tempfile.mkstemp(prefix=".run-manifest.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(row, handle, ensure_ascii=False, sort_keys=True); handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
            dir_fd = os.open(directory, os.O_RDONLY); os.fsync(dir_fd); os.close(dir_fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    finally:
        os.close(lock_fd); os.unlink(lock)


def create_manifest(trace, run_tag, dataset, arm, source_corpus, answer_key, renderer,
                    prompt, skill_root, runner, scorer, triage_checker, stop_checker,
                    citation_checker, target_cli, target_version, requested_model, provider,
                    expected_returned_identity, lane, health_receipt, controller_parent,
                    staged_corpus_destination, forbid_paths=()):
    values = locals(); safe(*(v for v in values.values() if isinstance(v, str)), *forbid_paths)
    trace = os.path.abspath(trace); staged_corpus_destination = os.path.abspath(staged_corpus_destination)
    if within(answer_key, trace) or within(answer_key, staged_corpus_destination):
        fail("E_KEY_TARGET_VISIBLE", "answer key is inside target workspace")
    if not all(within(path, skill_root) for path in
               (triage_checker, stop_checker, citation_checker)):
        fail("E_CHECKER_NOT_VERSION_OWNED", "checker is outside selected skill version")
    key = load_json(answer_key, "E_ANSWER_KEY_JSON")
    source = inspect_corpus(source_corpus, key, dataset, forbid_paths=forbid_paths)
    staged = inspect_corpus(staged_corpus_destination, key, dataset, "STAGED", forbid_paths)
    if source["manifest_sha256"] != staged["manifest_sha256"]:
        fail("E_STAGED_MANIFEST_MISMATCH", "staged corpus does not match source corpus")
    expected_ids = key_ids(key)
    artifacts = {"answer_key": file_asset(answer_key, "E_ANSWER_KEY_FILE"),
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
    target = {"version": target_version, "requested_model": requested_model,
              "expected_returned_identity": expected_returned_identity,
              "provider": provider, "lane": lane}
    target["identity_sha256"] = digest(canonical(target))
    row = {"schema": 1, "run_tag": run_tag, "dataset": dataset, "arm": arm,
           "trace": {"path": trace, "identity_sha256": digest(trace.encode("utf-8"))},
           "corpus": {"source_path": os.path.realpath(source_corpus),
                      "staged_path": os.path.realpath(staged_corpus_destination),
                      "files": source["files"], "source_manifest_sha256": source["manifest_sha256"],
                      "staged_manifest_sha256": staged["manifest_sha256"],
                      "excluded": source["excluded"], "forbid_paths": sorted(forbid_paths)},
           "expected": {"ids": expected_ids, "id_count": len(expected_ids),
                        "file_count": len(source["files"])},
           "artifacts": artifacts, "skill": tree_asset(skill_root),
           "target": target,
           "health_receipt": health}
    row["manifest_sha256"] = digest(canonical(row))
    atomic_manifest(os.path.join(trace, "run-manifest.json"), row)
    return row


def verify_manifest(trace):
    path = os.path.join(os.path.abspath(trace), "run-manifest.json")
    row = load_json(path, "E_MANIFEST_JSON")
    seal = row.get("manifest_sha256"); unsealed = dict(row); unsealed.pop("manifest_sha256", None)
    if seal != digest(canonical(unsealed)): fail("E_MANIFEST_DIGEST_MISMATCH", "manifest seal mismatch")
    if row.get("trace", {}).get("path") != os.path.abspath(trace): fail("E_TRACE_IDENTITY_MISMATCH", "trace identity mismatch")
    artifacts = row.get("artifacts", {})
    for name, asset in artifacts.items():
        current = file_asset(asset.get("path"), "E_%s_FILE" % name.upper())
        if current["sha256"] != asset.get("sha256"):
            fail("E_%s_DIGEST_MISMATCH" % name.upper(), "bound artifact digest mismatch")
    skill = tree_asset(row.get("skill", {}).get("path"))
    if (skill["sha256"], skill["git_commit"]) != (row["skill"].get("sha256"), row["skill"].get("git_commit")):
        fail("E_SKILL_DIGEST_MISMATCH", "bound skill identity mismatch")
    key = load_json(artifacts["answer_key"]["path"], "E_ANSWER_KEY_JSON")
    corpus = row["corpus"]; supplied = tuple(corpus.get("forbid_paths", []))
    source = inspect_corpus(corpus["source_path"], key, row["dataset"], forbid_paths=supplied)
    staged = inspect_corpus(corpus["staged_path"], key, row["dataset"], "STAGED", supplied)
    if source["manifest_sha256"] != corpus.get("source_manifest_sha256") or source["excluded"] != corpus.get("excluded"):
        fail("E_CORPUS_MANIFEST_MISMATCH", "source corpus manifest changed")
    if staged["manifest_sha256"] != corpus.get("staged_manifest_sha256"):
        fail("E_STAGED_MANIFEST_MISMATCH", "staged corpus manifest changed")
    target = row["target"]
    target_identity = dict(target); target_seal = target_identity.pop("identity_sha256", None)
    if target_seal != digest(canonical(target_identity)):
        fail("E_TARGET_IDENTITY_MISMATCH", "target identity digest mismatch")
    health = validate_health(row["health_receipt"]["path"], target["lane"], target["provider"],
                             target["requested_model"], target["expected_returned_identity"])
    if health["sha256"] != row["health_receipt"].get("sha256"):
        fail("E_HEALTH_RECEIPT_DIGEST_MISMATCH", "health receipt digest changed")
    out = dict(row); out["manifest_sha256"] = seal
    return out


def parser():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    for name in ("source-corpus", "answer-key", "dataset", "destination"):
        stage.add_argument("--" + name, required=True)
    stage.add_argument("--forbid-path", action="append", default=[]); stage.add_argument("--json", action="store_true")
    create = sub.add_parser("create"); create.add_argument("trace")
    for name in ("run-tag", "dataset", "arm", "source-corpus", "answer-key", "renderer", "prompt",
                 "skill-root", "runner", "scorer", "triage-checker", "stop-checker", "citation-checker",
                 "target-cli", "target-version", "requested-model", "provider", "expected-returned-identity",
                 "lane", "health-receipt", "controller-parent", "staged-corpus-destination"):
        create.add_argument("--" + name, required=True)
    create.add_argument("--forbid-path", action="append", default=[]); create.add_argument("--json", action="store_true")
    verify = sub.add_parser("verify"); verify.add_argument("trace"); verify.add_argument("--json", action="store_true")
    return ap


def summary(command, row):
    if command == "stage": return row
    return {"schema": row["schema"], "run_tag": row["run_tag"], "dataset": row["dataset"],
            "arm": row["arm"], "manifest_sha256": row["manifest_sha256"],
            "expected_id_count": row["expected"]["id_count"],
            "corpus_file_count": row["expected"]["file_count"]}


def main():
    args = parser().parse_args(); values = vars(args); command = values.pop("command"); as_json = values.pop("json")
    values["forbid_paths"] = tuple(values.pop("forbid_path", [])) if "forbid_path" in values else ()
    try:
        if command == "stage": row = stage_corpus(**values)
        elif command == "create": row = create_manifest(**values)
        else: values.pop("forbid_paths", None); row = verify_manifest(**values)
    except ManifestError as error:
        print(str(error), file=sys.stderr); return 2
    result = summary(command, row)
    if as_json: print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else: print("%s: %s" % (command.upper(), result.get("manifest_sha256", result.get("staged_manifest_sha256"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
