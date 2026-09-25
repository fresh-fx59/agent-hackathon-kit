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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gatewatch as GW  # noqa: E402  v53: watchdog, cache, K=2 rule in one place
DEFAULT_DEADLINE_SECONDS = GW.ceiling_s()


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


def command_for(gate, tools, report, corpus, ledger, work, require_index=False):
    program = tools / (gate + ".py")
    if gate == "reportcheck":
        return [sys.executable, str(program), str(report), "--json"]
    if gate == "citecheck":
        return [sys.executable, str(program), str(report), "--corpus", str(corpus), "--require-quote", "--ledger", str(ledger), "--json"] + (["--require-index"] if require_index else [])
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


def _compose_bytes(work):
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
    _composed, temporary = stop.compose_worklists(items, str(work))
    try:
        return Path(temporary).read_bytes()
    finally:
        os.remove(temporary)


def gate_inputs(package, work, corpus, ledger=None):
    """Single source of truth for the files the gates read (spec v4.2 item 2).

    Derived from the gate argv itself (command_for): every argv path that is
    an existing file outside the package tools, plus the source worklists
    behind the composed ledger. finalize tracks these before/after, and the
    verdict key hashes the same set, so no gate input can be left out.
    """
    package, work, corpus = (Path(value).resolve() for value in (package, work, corpus))
    tools = package / "tools"
    report = work / "report.md"
    placeholder = ledger or (work / "worklist.tsv")
    files = {}
    for gate in GATES:
        for arg in command_for(gate, tools, report, corpus, placeholder, work):
            path = Path(arg)
            if not path.is_absolute() or path == Path(sys.executable):
                continue
            path = path.resolve()
            if path == Path(placeholder).resolve() and ledger is not None:
                continue  # the composed copy; its sources are listed below
            if path == corpus or tools in path.parents:
                continue  # corpus -> data_sha256; package -> package hash
            files[path.relative_to(work.parent).as_posix() if work.parent in path.parents else str(path)] = path
    for item in _ledger_items(work):
        path = Path(item["path"]).resolve()
        files[path.relative_to(work.parent).as_posix() if work.parent in path.parents else str(path)] = path
    return dict(sorted(files.items()))


def _ledger_items(work):
    stop = _stopcheck()
    marker, _path, _why = stop.load_marker(str(work.parent))
    if marker:
        if stop.real(marker.get("out") or "") != str(work):
            raise ValueError("active marker selects another work directory")
        return stop.manifest_worklists(marker, str(work))
    ledger = work / "worklist.tsv"
    return [{"path": str(ledger), "rel": "worklist.tsv", "host": None}] if ledger.is_file() else []


def _file_sha(path):
    return sha256_file(path) if Path(path).is_file() else "absent"


def verdict_key(package, work, corpus, data_sha256=None):
    """v53 item 2 cache key; the same function serves finalize and the hook."""
    package, work, corpus = (Path(value).resolve() for value in (package, work, corpus))
    report = work / "report.md"
    if data_sha256 is None:
        data_sha256 = GW.data_sha256_for(str(corpus), str(report)) or evidence_identity(corpus, work.parent)["sha256"]
    rows = [[name, _file_sha(path)] for name, path in gate_inputs(package, work, corpus).items()]
    rows.append(["<composed-ledger>", hashlib.sha256(_compose_bytes(work)).hexdigest()])
    return GW.cache_key(rows, data_sha256, tree_sha256(package))


def gate_reason(gate, detail):
    if detail.get("kill_cause"):
        return GW.timeout_message(gate, detail["elapsed_s"], detail["kill_cause"])
    if gate == "citecheck" and detail.get("exit_code") == 3 and "data index" in detail.get("stdout_head", ""):
        return "Sherlock: " + detail["stdout_head"].strip().splitlines()[0]
    return ("Sherlock: final validation is blocking (%s rc %s, blocking %s); exact evidence is "
            "saved under work/validation. Repair the report and rerun finalize.py."
            % (gate, detail.get("exit_code"), detail.get("parsed_blocking")))


def run(package, work, corpus, deadline_seconds=DEFAULT_DEADLINE_SECONDS, require_index=False,
        watch_multiplier=1.0):
    package, work, corpus = (Path(value).resolve() for value in (package, work, corpus))
    validation = work / "validation"
    validation.mkdir(parents=True, exist_ok=True)
    attempt_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex
    attempt = validation / attempt_id
    attempt.mkdir()  # exclusive: evidence from a failed attempt is never overwritten
    report = work / "report.md"
    # Receipt names stay v52-compatible (the harness reads report/worklist/rules);
    # every other gate input from gate_inputs() is tracked under its relative path.
    tracked = {"report": report, "worklist": work / "worklist.tsv", "rules": work / "rules.tsv"}
    try:
        known = {p.resolve() for p in tracked.values()}
        tracked.update({name: path for name, path in gate_inputs(package, work, corpus).items()
                        if path not in known})
    except Exception as error:  # noqa: BLE001 - the key call below fails the same way -> no cache
        tracked["<gate-inputs-error>"] = work / ("." + type(error).__name__)
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
    try:
        key = verdict_key(package, work, corpus)
    except Exception as error:  # noqa: BLE001 - no key means no cache, never a pass
        key, metadata["cache_key_error"] = None, type(error).__name__
    metadata["cache_key"] = key
    for gate in GATES:
        argv = command_for(gate, tools, report, corpus, ledger, work, require_index)
        stdout_path = attempt / (gate + ".stdout")
        stderr_path = attempt / (gate + ".stderr")
        ceiling = max(0.01, deadline - time.monotonic())
        watched = GW.run_watched(argv, ceiling, cwd=work.parent,
                                 hb_limit=GW.HEARTBEAT_LIMIT_S * watch_multiplier,
                                 cpu_limit=GW.CPU_STALL_LIMIT_S * watch_multiplier)
        exit_code, stdout, stderr = watched.returncode, watched.stdout, watched.stderr
        exception = "Timeout:" + watched.cause if watched.cause else None
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
        detail.update({"elapsed_s": round(watched.elapsed, 3), "kill_cause": watched.cause,
                       "ceiling_s": round(ceiling, 3), "heartbeats": watched.beats,
                       "stderr_tail": GW.stderr_tail(stderr),
                       "stdout_head": stdout[:512].decode("utf-8", "replace")})
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
    timed_out = next((g for g, d in metadata["gates"].items() if d.get("kill_cause")), None)
    failing = timed_out or next((g for g, d in metadata["gates"].items()
                                 if not (d["exit_code"] == 0 and d["parsed_blocking"] == 0)), None)
    reason = None
    if failing:
        reason = gate_reason(failing, metadata["gates"][failing])
    elif inputs_changed:
        reason = "Sherlock: inputs changed during validation; rerun finalize.py."
    entry = {"verdict": metadata["verdict"], "reason": reason, "attempt": attempt_id, "timeouts": 0}
    if timed_out and key:
        prior = GW.cache_read(str(base), key) or {}
        entry["timeouts"] = int(prior.get("timeouts") or 0) + 1
        entry["verdict"] = "timeout"
        if entry["timeouts"] >= GW.TIMEOUT_K:
            entry["verdict"] = "checker_fault"
            entry["reason"] = ("Sherlock: checker_fault — %s; %d consecutive timeouts on unchanged "
                               "inputs. Stop here; this is a checker fault, not a report defect."
                               % (reason, entry["timeouts"]))
    metadata["verdict_detail"] = entry
    if key and not inputs_changed:
        metadata["cache_written"] = GW.cache_write(str(base), key, entry)
    metadata["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(attempt / "metadata.json", metadata)
    GW.detail_write(str(base), {"attempt": attempt_id, "cache_key": key,
                                             "cache": "miss", "verdict": entry,
                                             "gates": metadata["gates"]})
    print(json.dumps({"attempt": str(attempt), "verdict": metadata["verdict"], "cache_key": key,
                      "verdict_detail": entry, "gates": metadata["gates"]}, ensure_ascii=False))
    return 0 if clean else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="work")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--package", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS)
    parser.add_argument("--require-index", action="store_true",
                        help="citecheck must use a fresh load-time index (the Stop hook sets this)")
    parser.add_argument("--watch-multiplier", type=float, default=1.0,
                        help="scale heartbeat/CPU watchdog limits (CHECKER-FAULT retry uses 2)")
    parser.add_argument("--strict-index", action="store_true",
                        help="gate mode: index freshness also re-hashes every corpus file "
                             "(defeats an mtime-preserving edit); inherited by citecheck")
    args = parser.parse_args(argv)
    if args.strict_index:
        import ccindex
        os.environ[ccindex.STRICT_ENV] = "1"
    return run(args.package, args.work, args.corpus, args.deadline_seconds, args.require_index,
               args.watch_multiplier)


if __name__ == "__main__":
    raise SystemExit(main())
