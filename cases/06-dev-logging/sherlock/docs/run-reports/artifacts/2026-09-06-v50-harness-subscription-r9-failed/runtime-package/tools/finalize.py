#!/usr/bin/env python3
"""Non-destructively run Sherlock's four final report gates.

Each invocation owns one immutable validation attempt under ``work/validation``.
The helper intentionally does not repair or remove any investigation artifact.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid


GATES = ("reportcheck", "citecheck", "statecheck", "triagecheck")
DEFAULT_DEADLINE_SECONDS = 60.0


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root, excluded=()):
    """Version-gate-compatible digest; unsafe package members are never skipped."""
    root = Path(root)
    rows = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort(); files.sort()
        for directory in directories:
            path = Path(current) / directory
            if path.is_symlink():
                raise ValueError("unsafe tree member: " + str(path))
        for filename in files:
            path = Path(current) / filename
            relative = path.relative_to(root).as_posix()
            if any(relative == item or relative.startswith(item + "/") for item in excluded):
                continue
            if "__pycache__" in Path(relative).parts or filename.endswith((".pyc", ".pyo")):
                continue
            before = os.lstat(path)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ValueError("unsafe tree member: " + str(path))
            data = path.read_bytes()
            after = os.lstat(path)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("tree changed while reading: " + str(path))
            rows.append([relative, hashlib.sha256(data).hexdigest()])
    if not rows:
        raise ValueError("empty tree: " + str(root))
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(path, base):
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        relative = str(path)
    if not path.is_file():
        return {"path": relative, "sha256": None, "missing": True}
    return {"path": relative, "sha256": sha256_file(path), "missing": False}


def evidence_identity(corpus, base):
    try:
        relative = corpus.relative_to(base).as_posix()
    except ValueError:
        relative = str(corpus)
    if not corpus.is_dir():
        return {"path": relative, "sha256": None, "missing": True}
    return {"path": relative, "sha256": tree_sha256(corpus), "missing": False}


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)


def parse_blocking(stdout):
    payload = None
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None, "stdout_not_utf8"
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _end = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if payload is None:
        return None, "invalid_json"
    for key in ("blocking", "blocking_defects"):
        if isinstance(payload.get(key), int) and not isinstance(payload[key], bool):
            return payload[key], None
    return None, "missing_blocking"


def command_for(gate, tools, report, corpus, ledger, work):
    program = tools / (gate + ".py")
    if gate == "reportcheck":
        return [sys.executable, str(program), str(report), "--json"]
    if gate == "citecheck":
        return [sys.executable, str(program), str(report), "--corpus", str(corpus), "--require-quote", "--ledger", str(ledger), "--json"]
    if gate == "statecheck":
        return [sys.executable, str(program), "--corpus", str(corpus), "--report", str(report), "--json"]
    return [sys.executable, str(program), "--worklist", str(ledger), "--rules", str(work / "rules.tsv"), "--corpus", str(corpus), "--json"]


def _stopcheck():
    spec = importlib.util.spec_from_file_location("sherlock_stopcheck_for_finalize",
                                                  Path(__file__).with_name("stopcheck.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def materialize_ledger(work, attempt):
    """Use the active manifest's exact ledgers and retain their composition."""
    stop = _stopcheck()
    marker, _path, _why = stop.load_marker(str(work.parent))
    if marker:
        if stop.real(marker.get("out") or "") != str(work):
            raise ValueError("active marker selects another work directory")
        items = stop.manifest_worklists(marker, str(work))
    else:
        ledger = work / "worklist.tsv"
        if not ledger.is_file():
            raise ValueError("worklist.tsv is missing")
        items = [{"path": str(ledger), "rel": "worklist.tsv", "host": None}]
    composed, temporary = stop.compose_worklists(items, str(work))
    retained = attempt / "worklist.tsv"
    os.replace(temporary, retained)
    return retained, [item["rel"] for item in items]


def run(package, work, corpus, deadline_seconds=DEFAULT_DEADLINE_SECONDS):
    package, work, corpus = (Path(value).resolve() for value in (package, work, corpus))
    validation = work / "validation"
    validation.mkdir(parents=True, exist_ok=True)
    attempt_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex
    attempt = validation / attempt_id
    attempt.mkdir()  # exclusive: evidence from a failed attempt is never overwritten
    report = work / "report.md"
    tracked = {"report": report, "worklist": work / "worklist.tsv", "rules": work / "rules.tsv"}
    base = work.parent
    metadata = {
        "schema": 1,
        "attempt_id": attempt_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "package_tree_sha256": tree_sha256(package) if package.is_dir() else None,
        "package_tree_sha256_algorithm": "version-gate.tree_digest/v1",
        "inputs_before": {key: identity(path, base) for key, path in tracked.items()},
        "work_tree_before": tree_sha256(work, excluded=("validation",)) if work.is_dir() else None,
        "evidence_before": evidence_identity(corpus, base),
        "gates": {},
    }
    write_json(attempt / "metadata.json", metadata)
    try:
        ledger, selected_worklists = materialize_ledger(work, attempt)
        metadata["selected_worklists"] = selected_worklists
        metadata["composed_ledger"] = ledger.name
    except Exception as error:
        metadata["ledger_error"] = type(error).__name__
        metadata["verdict"] = "blocking"
        metadata["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json(attempt / "metadata.json", metadata)
        print(json.dumps({"attempt": str(attempt), "verdict": "blocking", "gates": {}}, ensure_ascii=False))
        return 2
    tools = package / "tools"
    deadline = time.monotonic() + float(deadline_seconds)
    for gate in GATES:
        argv = command_for(gate, tools, report, corpus, ledger, work)
        stdout_path = attempt / (gate + ".stdout")
        stderr_path = attempt / (gate + ".stderr")
        try:
            timeout = max(0.01, deadline - time.monotonic())
            completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       check=False, cwd=work.parent, timeout=timeout)
            exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
            exception = None
        except subprocess.TimeoutExpired as error:
            exit_code = 124
            stdout = error.stdout if isinstance(error.stdout, bytes) else b""
            stderr = error.stderr if isinstance(error.stderr, bytes) else b""
            exception = "TimeoutExpired"
        except OSError as error:
            exit_code, stdout, stderr, exception = 127, b"", str(error).encode("utf-8", "backslashreplace"), type(error).__name__
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        blocking, parse_error = parse_blocking(stdout)
        detail = {
            "argv": argv,
            "exit_code": exit_code,
            "tool_sha256": sha256_file(Path(argv[1])) if Path(argv[1]).is_file() else None,
            "stdout_artifact": stdout_path.name,
            "stderr_artifact": stderr_path.name,
            "parsed_blocking": blocking,
        }
        if parse_error:
            detail["parse_error"] = parse_error
        if exception:
            detail["exception"] = exception
        metadata["gates"][gate] = detail
        write_json(attempt / "metadata.json", metadata)
    metadata["inputs_after"] = {key: identity(path, base) for key, path in tracked.items()}
    metadata["work_tree_after"] = tree_sha256(work, excluded=("validation",)) if work.is_dir() else None
    metadata["evidence_after"] = evidence_identity(corpus, base)
    metadata["package_tree_sha256_after"] = tree_sha256(package) if package.is_dir() else None
    inputs_changed = (metadata["inputs_after"] != metadata["inputs_before"]
                      or metadata["work_tree_after"] != metadata["work_tree_before"]
                      or metadata["evidence_after"] != metadata["evidence_before"]
                      or metadata["package_tree_sha256_after"] != metadata["package_tree_sha256"])
    clean = all(detail["exit_code"] == 0 and detail["parsed_blocking"] == 0 for detail in metadata["gates"].values()) and not inputs_changed
    metadata["inputs_changed_during_validation"] = inputs_changed
    metadata["verdict"] = "clean" if clean else "blocking"
    metadata["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(attempt / "metadata.json", metadata)
    print(json.dumps({"attempt": str(attempt), "verdict": metadata["verdict"], "gates": metadata["gates"]}, ensure_ascii=False))
    return 0 if clean else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="work")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--package", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS)
    args = parser.parse_args(argv)
    return run(args.package, args.work, args.corpus, args.deadline_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
