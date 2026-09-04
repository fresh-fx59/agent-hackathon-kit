#!/usr/bin/env python3
"""Provider-free, authoritative validity reducer for one sealed trace."""
import argparse
import errno
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tempfile
import unicodedata
HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
CANDIDATE_KEYS = {"schema", "run_tag", "result_stream", "work_root", "artifact",
                  "upstream_completed", "transport", "stats", "usage"}
TRANSPORT_KEYS = {"exit_code", "status", "duration_s"}
USAGE_KEYS = {"turns", "input_tokens", "output_tokens", "errored"}
USAGE_NUMBER_KEYS = {"turns", "input_tokens", "output_tokens"}
MAX_JSON = 1024 * 1024
MAX_STREAM = 64 * 1024 * 1024
MAX_STDERR = 64 * 1024
CHECKER_TIMEOUT = 60.0
LOGMAP_TIMEOUT = 180.0
SECRET_RE = re.compile(r"(?:bearer\s+|(?:sk|ghp|glpat|xox[baprs])-|AKIA[0-9A-Z]{16}|"
                       r"(?:password|token|api[_-]?key)\s*[:=])", re.I)

class ValidityError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def fail(code):
    raise ValidityError(code)

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if not spec or not spec.loader:
        fail("authority_import_failed")
    module = importlib.util.module_from_spec(spec)
    prior = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception:
        fail("authority_import_failed")
    finally:
        sys.dont_write_bytecode = prior
    return module


MANIFEST = load_module("sherlock_run_manifest", HERE / "run-manifest.py")
DELIVERABLE = load_module("sherlock_deliverable", SHERLOCK / "measure" / "deliverable.py")

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")

def sha(data):
    return hashlib.sha256(data).hexdigest()

def normalized_text(value):
    value = unicodedata.normalize("NFC", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(value.split())

def lock(fd, operation, code):
    while True:
        try:
            fcntl.flock(fd, operation)
            return
        except OSError as error:
            if error.errno == errno.EINTR:
                continue
            fail(code)

def fingerprint_match(kind, needle, haystack):
    if kind == "substring":
        return needle in haystack
    boundary = r"(?<![\w])%s(?![\w])" % re.escape(needle)
    return re.search(boundary, haystack) is not None

def read_relative(trace_fd, relative, prefix, maximum=MAX_STREAM, missing=False):
    try:
        data = MANIFEST._read_relative(trace_fd, relative, prefix)
    except MANIFEST.ManifestError as error:
        if missing and "FILE_MISSING" in str(error):
            return None
        fail("candidate_path_invalid")
    if len(data) > maximum:
        fail("candidate_oversized")
    return data

def json_object(data, code):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        fail(code)
    if not isinstance(value, dict):
        fail(code)
    return value

def bounded_number(value, integer=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)) or value < 0:
        fail("candidate_invalid")
    if value > 10 ** 15:
        fail("candidate_invalid")
    return value

def load_candidate(trace_fd, manifest):
    data = read_relative(trace_fd, "candidate.json", "CANDIDATE", MAX_JSON)
    row = json_object(data, "candidate_invalid")
    if set(row) != CANDIDATE_KEYS or row.get("schema") != 1:
        fail("candidate_invalid")
    fixed = {"result_stream": "out.json", "work_root": "work",
             "artifact": "work/report.md", "upstream_completed": "upstream-completed.jsonl"}
    if any(row.get(k) != v for k, v in fixed.items()) or row.get("run_tag") != manifest.get("run_tag"):
        fail("candidate_invalid")
    transport, usage = row.get("transport"), row.get("usage")
    if not isinstance(transport, dict) or set(transport) != TRANSPORT_KEYS:
        fail("candidate_invalid")
    if not isinstance(usage, dict) or set(usage) != USAGE_KEYS:
        fail("candidate_invalid")
    if row.get("stats") is not None and not isinstance(row.get("stats"), dict):
        fail("candidate_invalid")
    if transport["status"] not in (None, "success", "error"):
        fail("candidate_invalid")
    bounded_number(transport["exit_code"], True); bounded_number(transport["duration_s"])
    for key in USAGE_NUMBER_KEYS:
        bounded_number(usage[key], True)
    if type(usage["errored"]) is not bool:
        fail("candidate_invalid")
    return row, data

MIN_MAIN_REQUESTS = 2


def terminal_exit(code):
    """An unknown terminal is never zero: v31 reports it as unknown."""
    return code if type(code) is int else "unknown"


def skill_receipt(final):
    """Reject a session that never loaded the skill or never investigated.

    r4 returned a healthy HTTP-200 stream, exit 0 and fabricated JSON while
    stats.skills.totalCalls was 0: the main model answered in one request and
    every tool call belonged to the managed auto-memory extractor.
    """
    stats = final.get("stats") if isinstance(final.get("stats"), dict) else {}
    skills = stats.get("skills") if isinstance(stats.get("skills"), dict) else {}
    tools = stats.get("tools") if isinstance(stats.get("tools"), dict) else {}
    models = stats.get("models") if isinstance(stats.get("models"), dict) else {}
    skill_calls = skills.get("totalCalls") if type(skills.get("totalCalls")) is int else 0
    tool_calls = tools.get("totalCalls") if type(tools.get("totalCalls")) is int else 0
    main_requests = 0
    for model in models.values():
        if not isinstance(model, dict):
            continue
        by_source = model.get("bySource") if isinstance(model.get("bySource"), dict) else {}
        main = by_source.get("main") if isinstance(by_source.get("main"), dict) else {}
        api = main.get("api") if isinstance(main.get("api"), dict) else {}
        requests = api.get("totalRequests")
        if type(requests) is int:
            main_requests += requests
    reasons = []
    if skill_calls < 1:
        reasons.append("no_skill_load")
    if main_requests < MIN_MAIN_REQUESTS:
        reasons.append("no_investigation")
    return {"skill_calls": skill_calls, "tool_calls": tool_calls,
            "main_requests": main_requests, "reasons": reasons}


MAX_ATTEMPT_RECEIPT = 1 << 20


def terminal_receipt(trace_fd):
    """Read the runner's own attempt receipt; never invent a zero exit.

    v30 exited 0 whenever a parseable candidate existed and left
    candidate.transport.exit_code null, so a killed or crashed session looked
    identical to a clean one.
    """
    rows = []
    try:
        fd = os.open("attempts.jsonl", os.O_RDONLY, dir_fd=trace_fd)
    except OSError:
        fd = None
    if fd is not None:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_ATTEMPT_RECEIPT + 1)
        if len(data) > MAX_ATTEMPT_RECEIPT:
            data = b""
        for line in data.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    last = rows[-1] if rows else {}
    code = terminal_exit(last.get("exit_code"))
    duration = last.get("duration_s") if isinstance(last.get("duration_s"), (int, float)) else None
    reasons = []
    if code == "unknown":
        reasons.append("exit_unknown")
    elif code != 0:
        reasons.append("exit_nonzero")
    return {"exit_code": code, "duration_s": duration, "attempts": len(rows),
            "reasons": reasons}


def result_facts(data, candidate):
    try:
        stream = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        fail("result_stream_invalid")
    rows = stream if isinstance(stream, list) else [stream]
    results = [(i, row) for i, row in enumerate(rows)
               if isinstance(row, dict) and row.get("type") == "result"]
    if len(results) != 1 or results[0][0] != len(rows) - 1:
        fail("result_stream_invalid")
    final = results[0][1]
    message = final.get("result") or ""
    if not isinstance(message, str):
        fail("result_stream_invalid")
    api_error = message.lstrip().startswith("[API Error") or ("[API Error" in message and len(message) < 400)
    status = "error" if final.get("is_error") is True or api_error else "success"
    usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}
    derived_usage = {"turns": final.get("num_turns") if type(final.get("num_turns")) is int else None,
                     "input_tokens": usage.get("input_tokens") if type(usage.get("input_tokens")) is int else None,
                     "output_tokens": usage.get("output_tokens") if type(usage.get("output_tokens")) is int else None}
    # Qwen owns result status, usage, and stats.  The shell runner owns exit and
    # duration, which are reconciled against attempts.jsonl below; Qwen's result
    # object normally has neither field.  Comparing all runner transport fields
    # to the result made every real candidate added in 69c5a11 impossible.
    if candidate["transport"]["status"] != status:
        fail("candidate_metadata_mismatch")
    for key, value in candidate["usage"].items():
        expected = status == "error" if key == "errored" else derived_usage.get(key)
        if value is not None and value != expected:
            fail("candidate_metadata_mismatch")
    if candidate["stats"] != final.get("stats"):
        fail("candidate_metadata_mismatch")
    return final, message, dict(candidate["transport"]), derived_usage

def scan_tree(root_fd, excluded=(), destination=None):
    rows, blobs = [], []
    for name in MANIFEST._scan_fd(root_fd, "VALIDITY"):
        if any(re.fullmatch(pattern, name) for pattern in excluded): continue
        data = MANIFEST._read_relative(root_fd, name, "VALIDITY")
        rows.append([name, len(data), sha(data)]); blobs.append(data)
        if destination is not None:
            target = Path(destination) / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return sha(canonical(rows)), blobs

def tree_bytes(root, excluded=(), destination=None):
    root_fd = MANIFEST._open_dir(str(root), "VALIDITY")
    try: return scan_tree(root_fd, excluded, destination)
    finally: os.close(root_fd)

def trace_tree(trace_fd, name, excluded=(), destination=None):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try: root_fd = os.open(name, flags, dir_fd=trace_fd)
    except OSError: fail("candidate_path_invalid")
    try: return scan_tree(root_fd, excluded, destination)
    finally: os.close(root_fd)

def safe_environment():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}

def run_bounded(argv, cwd, timeout, stdout_limit=MAX_JSON, sensitive=None,
                json_expected=True):
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        def limits():
            resource.setrlimit(resource.RLIMIT_FSIZE,
                               (max(stdout_limit, MAX_STDERR), max(stdout_limit, MAX_STDERR)))
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=safe_environment(), stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, preexec_fn=limits)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(); return {"error": "checker_timeout"}
        except OSError:
            return {"error": "checker_failed"}
        out.seek(0, os.SEEK_END); out_size = out.tell()
        err.seek(0, os.SEEK_END); err_size = err.tell()
        if out_size >= stdout_limit or err_size >= MAX_STDERR or rc == -getattr(signal, "SIGXFSZ", 25):
            return {"error": "checker_oversized"}
        out.seek(0); err.seek(0); stdout = out.read(); stderr = err.read()
    combined = stdout + stderr
    if SECRET_RE.search(combined.decode("utf-8", "ignore")):
        return {"error": "checker_output_sensitive"}
    if sensitive:
        token = normalized_text(sensitive)
        if len(token) >= 16 and token in normalized_text(combined.decode("utf-8", "ignore")):
            return {"error": "checker_output_sensitive"}
    if not json_expected:
        return {"exit_code": rc}
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        return {"error": "checker_malformed", "exit_code": rc}
    if not isinstance(value, dict):
        return {"error": "checker_malformed", "exit_code": rc}
    return {"exit_code": rc, "json": value}

def authority_snapshot(manifest, root):
    skill = Path(root) / "skill"; skill.mkdir(); rows, blobs = [], []
    skill_fd = MANIFEST._open_dir(manifest["skill"]["path"], "SKILL")
    try:
        for name in MANIFEST._scan_fd(skill_fd, "SKILL"):
            if name.startswith(".git/"): continue
            data = MANIFEST._read_relative(skill_fd, name, "SKILL")
            rows.append({"path": name, "bytes": len(data), "sha256": sha(data)}); blobs.append(data)
            target = skill / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    finally: os.close(skill_fd)
    if len(rows) != manifest["skill"]["file_count"] or sha(canonical(rows)) != manifest["skill"]["sha256"]:
        fail("authority_changed")
    paths = {}
    for name in ("stop_checker", "triage_checker", "citation_checker"):
        asset = manifest["artifacts"][name]
        try: relative = Path(asset["path"]).resolve().relative_to(Path(manifest["skill"]["path"]).resolve())
        except ValueError: fail("authority_changed")
        target = skill / relative; data = target.read_bytes()
        if len(data) != asset["bytes"] or sha(data) != asset["sha256"]: fail("authority_changed")
        paths[name] = str(target)
    extras = {}
    for name in ("answer_key", "prompt"):
        asset = manifest["artifacts"][name]; data, _ = MANIFEST._read_path(asset["path"], name.upper())
        if len(data) != asset["bytes"] or sha(data) != asset["sha256"]: fail("authority_changed")
        extras[name] = data
    corpus = Path(root) / "corpus"; corpus.mkdir()
    corpus_fd = MANIFEST._open_dir(manifest["corpus"]["staged_path"], "STAGED")
    corpus_blobs = []
    try:
        for item in manifest["corpus"]["files"]:
            data = MANIFEST._read_relative(corpus_fd, item["path"], "STAGED")
            if len(data) != item["bytes"] or sha(data) != item["sha256"]: fail("authority_changed")
            target = corpus / item["path"]; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            corpus_blobs.append(data)
    finally: os.close(corpus_fd)
    return paths, skill, blobs, extras, corpus, corpus_blobs

def import_authorities(paths, skill):
    stop = load_module("validity_stopcheck", paths["stop_checker"])
    triage = load_module("validity_triagecheck", paths["triage_checker"])
    logmap = skill / "tools" / "logmap.py"
    if not logmap.is_file():
        fail("inventory_reference_failed")
    return stop, triage, logmap

def inventory(workspace, out_dir, stop, triage):
    marker, _, _ = stop.load_marker(str(workspace))
    if not isinstance(marker, dict):
        fail("inventory_target_failed")
    items = stop.manifest_worklists(marker, str(out_dir))
    combined, temporary = stop.compose_worklists(items, str(out_dir))
    try:
        rows = triage.read_worklist(combined)
    finally:
        try: os.unlink(temporary)
        except OSError: pass
    if not isinstance(rows, list):
        fail("inventory_target_failed")
    tuples = []
    for row in rows:
        if not isinstance(row, dict):
            fail("inventory_target_failed")
        tuples.append([row.get("id"), row.get("хост"), row.get("ось"), row.get("ref"),
                       row.get("n"), row.get("всплеск"), row.get("запись")])
    marker_row = {key: marker.get(key) for key in
                  ("version", "mode", "worklists", "hosts_manifest", "hosts") if key in marker}
    value = {"marker": marker_row, "rows": tuples}
    return value, sha(canonical(value))

def reference_inventory(manifest, stop, triage, logmap, corpus):
    with tempfile.TemporaryDirectory(prefix="sherlock-validity-ref-") as root:
        root = Path(root); out = root / "work"; out.mkdir()
        argv = [sys.executable, str(logmap), str(corpus), "--out", str(out),
                "--worklist-cap", "250", "--per-file-cap", "40", "--rate-cap", "70",
                "--map-cap", "150000", "--seed", "20260728", "--jobs", "1"]
        result = run_bounded(argv, str(root), LOGMAP_TIMEOUT, stdout_limit=MAX_STREAM,
                             json_expected=False)
        if result.get("error") or result.get("exit_code") != 0:
            fail("inventory_reference_failed")
        return inventory(root, out, stop, triage)

def checker_summary(name, result):
    if result.get("error"):
        return {"name": name, "error": result["error"], "exit_code": None, "blocking": None}
    data = result["json"]
    if name == "triage":
        blocking = data.get("blocking")
        rows = data.get("rows")
        if type(blocking) is not int or type(rows) is not int:
            return {"name": name, "error": "checker_malformed", "exit_code": result["exit_code"], "blocking": None}
        return {"name": name, "error": None, "exit_code": result["exit_code"],
                "blocking": blocking, "rows": rows}
    ledger = data.get("ledger")
    if not isinstance(ledger, dict) or type(ledger.get("unresolved_total")) is not int:
        return {"name": name, "error": "checker_malformed", "exit_code": result["exit_code"], "blocking": None}
    return {"name": name, "error": None, "exit_code": result["exit_code"],
            "blocking": ledger["unresolved_total"]}

def contamination(manifest, message, artifact, delivered, work_blobs,
                  key_data, prompt_data, skill_blobs, corpus_blobs,
                  settings_pre, settings_saved, transcript):
    key_path = manifest["artifacts"]["answer_key"]["path"]
    key = json_object(key_data, "manifest_invalid")
    corpus_text = normalized_text(b"\n".join(corpus_blobs).decode("utf-8", "replace"))
    fingerprints = []
    dataset = normalized_text(key.get("dataset", manifest.get("dataset", "")))
    for defect in key.get("defects", []):
        if not isinstance(defect, dict): continue
        defect_id = normalized_text(defect.get("id", ""))
        if dataset and defect_id:
            fingerprints.append(("answer_id", "%s:%s" % (dataset, defect_id), True, "token"))
        for field in ("title", "description", "root_cause", "trap"):
            value = normalized_text(defect.get(field, ""))
            if len(value) >= 24: fingerprints.append((field, value, False, "substring"))
        for proof in defect.get("proof_locations", []):
            if not isinstance(proof, dict): continue
            path, start, end = proof.get("file"), proof.get("line_start"), proof.get("line_end")
            if path and type(start) is int:
                value = "%s:%s%s" % (path, start, ("-%s" % end) if type(end) is int and end != start else "")
                fingerprints.append(("proof", normalized_text(value), True, "token"))
    for crib in key.get("crib_strings", []):
        value = normalized_text(crib)
        if len(value) >= 16: fingerprints.append(("crib", value, False, "substring"))
    fingerprints.append(("answer_key_name", normalized_text(os.path.basename(key_path)), True, "token"))
    fingerprints = [(cat, text, hard, kind) for cat, text, hard, kind in fingerprints
                    if text and (hard or not fingerprint_match(kind, text, corpus_text))]
    sources = [("prompt", prompt_data, True), ("skill", b"\n".join(skill_blobs), True),
               ("workspace", b"\n".join(work_blobs), True),
               ("settings_pre", settings_pre, True), ("settings_saved", settings_saved, True),
               ("staged_corpus", b"\n".join(corpus_blobs), True),
               ("transcript", transcript, False),
               ("message", message.encode(), False), ("artifact", artifact.encode(), False),
               ("delivered", delivered.encode(), False)]
    hits = []
    for source, blob, pre_run in sources:
        haystack = normalized_text(blob.decode("utf-8", "replace"))
        for category, needle, always_hidden, kind in fingerprints:
            if fingerprint_match(kind, needle, haystack) and (pre_run or always_hidden or
                                                              not fingerprint_match(kind, needle, corpus_text)):
                hits.append({"source": source, "category": category,
                             "source_sha256": sha(blob), "fingerprint_sha256": sha(needle.encode())})
                if len(hits) >= 64: break
        if len(hits) >= 64: break
    source_rows = [{"source": source, "bytes": len(blob), "sha256": sha(blob)}
                   for source, blob, _ in sources]
    return {"schema": "contamination-v1", "hit_count": len(hits), "hits": hits,
            "sources": source_rows}

def validate_fresh(trace, trace_fd, manifest, candidate, candidate_data):
    reasons = []
    authority_tmp = tempfile.TemporaryDirectory(prefix="sherlock-validity-authority-")
    authority_root = Path(authority_tmp.name)
    try: paths, skill, skill_blobs, extras, corpus, corpus_blobs = authority_snapshot(manifest, authority_root)
    except Exception:
        authority_tmp.cleanup(); raise
    result_data = read_relative(trace_fd, "out.json", "RESULT", MAX_STREAM)
    upstream_data = read_relative(trace_fd, "upstream-completed.jsonl", "UPSTREAM", MAX_STREAM)
    settings_pre = read_relative(trace_fd, "qwen-settings-pre.json", "SETTINGS_PRE", MAX_JSON)
    settings_saved = read_relative(trace_fd, "qwen-settings.json", "SETTINGS_SAVED", MAX_JSON)
    artifact_data = read_relative(trace_fd, "work/report.md", "ARTIFACT", MAX_STREAM, missing=True)
    artifact = "" if artifact_data is None else artifact_data.decode("utf-8", "replace")
    final, message, transport, usage = result_facts(result_data, candidate)
    delivered = DELIVERABLE.compose(message, artifact)
    delivery = {"channel": DELIVERABLE.channel(message, artifact),
                "relation": DELIVERABLE.duplication(message, artifact)["relation"],
                "divergent": DELIVERABLE.duplication(message, artifact)["relation"] == "divergent",
                "message_sha256": sha(message.encode()), "message_bytes": len(message.encode()),
                "artifact_sha256": sha(artifact.encode()), "artifact_bytes": len(artifact.encode()),
                "delivered_sha256": sha(delivered.encode()), "delivered_bytes": len(delivered.encode())}
    if not delivered.strip(): reasons.append("missing_deliverable")
    artifact_only = transport["status"] != "success" and bool(artifact.strip())
    if transport["status"] != "success": reasons.append("transport_failed")
    if artifact_only:
        usage = {key: None for key in USAGE_NUMBER_KEYS}
    try:
        receipts = [json.loads(line) for line in upstream_data.decode("utf-8").splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in receipts): raise ValueError
    except (UnicodeError, ValueError, TypeError):
        receipts = []; reasons.append("identity_missing")
    own = [row for row in receipts if row.get("run_tag") == manifest["run_tag"]]
    successful = [row for row in own if type(row.get("status")) is int and 200 <= row["status"] < 300]
    identities = {row.get("returned_model") for row in successful if isinstance(row.get("returned_model"), str) and row["returned_model"]}
    if not successful or any(not row.get("returned_model") for row in successful): reasons.append("identity_missing")
    if len(identities) > 1: reasons.append("identity_mixed")
    returned = next(iter(identities)) if len(identities) == 1 else None
    if returned is not None and returned != manifest["target"]["expected_returned_identity"]:
        reasons.append("identity_wrong")
    identity = {"requested_sha256": sha(manifest["target"]["requested_model"].encode()),
                "returned_sha256": sha(returned.encode()) if returned else None,
                "successful_calls": len(successful)}
    target_root = authority_root / "target"; target_work = target_root / "work"; target_work.mkdir(parents=True)
    work_digest, _ = trace_tree(trace_fd, "work", destination=target_work)
    _, work_blobs = trace_tree(trace_fd, "work",
        excluded=(r"report\.md", r"worklist(?:-[^/]+)?\.tsv", r"rules\.tsv", r"map(?:-[^/]+)?\.txt", r"hosts\.tsv", r"axis[^/]*\.tsv"))
    marker = json_object(read_relative(trace_fd, ".sherlock/active.json", "MARKER", MAX_JSON), "inventory_target_failed")
    # `active: True` because a SEALED trace is inert by design (fix 10: the v41
    # trace shipped `"active": true` and a `skill_root` into the live checkout).
    # This marker is not the trace's; it describes the authority workspace this
    # validator just built, which IS live for the length of the check — the same
    # reason workspace/out/corpus/skill_root are overridden on the line below.
    marker.update({"active": True, "workspace": str(target_root),
                   "out": str(target_work),
                   "corpus": str(corpus), "skill_root": str(skill)})
    (target_root / ".sherlock").mkdir(); (target_root / ".sherlock/active.json").write_bytes(canonical(marker)+b"\n")
    inventory_row = {"expected_rows": None, "observed_rows": None,
                     "expected_sha256": None, "observed_sha256": None}
    checker_rows = []
    try:
        stop, triage, logmap = import_authorities(paths, skill)
        reference, reference_sha = reference_inventory(manifest, stop, triage, logmap, corpus)
        target, target_sha = inventory(target_root, target_work, stop, triage)
        inventory_row = {"expected_rows": len(reference["rows"]), "observed_rows": len(target["rows"]),
                         "expected_sha256": reference_sha, "observed_sha256": target_sha}
        if reference != target: reasons.append("inventory_mismatch")
        with tempfile.TemporaryDirectory(prefix="sherlock-validity-check-") as td:
            delivered_path = Path(td) / "delivered.md"; delivered_path.write_text(delivered, encoding="utf-8")
            target_marker, _, _ = stop.load_marker(str(target_root))
            items = stop.manifest_worklists(target_marker, str(target_work))
            combined, temporary = stop.compose_worklists(items, str(target_work))
            try:
                rules = str(target_work / "rules.tsv")
                tri = run_bounded([sys.executable, paths["triage_checker"],
                    "--worklist", combined, "--rules", rules, "--corpus", str(corpus), "--json"],
                    td, CHECKER_TIMEOUT, sensitive=delivered)
                cite = run_bounded([sys.executable, paths["citation_checker"], str(delivered_path),
                    "--corpus", str(corpus), "--require-quote", "--ledger", combined,
                    "--delivered", str(delivered_path), "--json"], td, CHECKER_TIMEOUT, sensitive=delivered)
            finally:
                try: os.unlink(temporary)
                except OSError: pass
            for name, result, failed_reason in (("triage", tri, "triage_failed"),
                                                 ("citation", cite, "citation_failed")):
                summary = checker_summary(name, result); checker_rows.append(summary)
                if summary.get("error"): reasons.append(summary["error"])
                elif summary["exit_code"] != 0 or summary.get("blocking"): reasons.append(failed_reason)
    except ValidityError as error:
        reasons.append(error.code)
    except Exception:
        reasons.append("inventory_target_failed")
    receipt = skill_receipt(final)
    terminal = terminal_receipt(trace_fd)
    if terminal["exit_code"] != "unknown" and transport["exit_code"] != terminal["exit_code"]:
        fail("candidate_metadata_mismatch")
    if (transport["duration_s"] is not None and terminal["duration_s"] is not None
            and transport["duration_s"] < terminal["duration_s"]):
        fail("candidate_metadata_mismatch")
    reasons.extend(terminal["reasons"])
    reasons.extend(receipt["reasons"])
    contamination_row = contamination(manifest, message, artifact, delivered, work_blobs,
                                      extras["answer_key"], extras["prompt"], skill_blobs, corpus_blobs,
                                      settings_pre, settings_saved, result_data)
    if contamination_row["hit_count"]: reasons.append("contaminated")
    row = {"schema": 1, "valid": not reasons, "reasons": sorted(set(reasons)),
           "run_tag": manifest["run_tag"], "manifest_sha256": manifest["manifest_sha256"],
           "candidate_sha256": sha(candidate_data), "result_stream_sha256": sha(result_data),
           "upstream_sha256": sha(upstream_data), "work_sha256": work_digest,
           "artifact_only": artifact_only, "transport": transport, "usage": usage,
           "delivery": delivery, "inventory": inventory_row, "identity": identity,
           "checkers": checker_rows, "contamination": contamination_row,
           "skill_receipt": receipt, "terminal": terminal}
    authority_tmp.cleanup()
    return row

def sign(row, key):
    unsigned = dict(row); unsigned.pop("hmac_sha256", None)
    return hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()

def existing_seal(trace, trace_fd, key, manifest_sha, candidate_sha):
    try:
        data = MANIFEST._read_relative(trace_fd, "validity.json", "VALIDITY")
    except MANIFEST.ManifestError as error:
        if "FILE_MISSING" in str(error): return None
        fail("validity_conflict")
    if len(data) > MAX_JSON: fail("validity_conflict")
    row = json_object(data, "validity_conflict")
    supplied = row.get("hmac_sha256")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, sign(row, key)):
        fail("validity_conflict")
    if row.get("manifest_sha256") != manifest_sha or row.get("candidate_sha256") != candidate_sha:
        fail("validity_conflict")
    result = read_relative(trace_fd, "out.json", "RESULT", MAX_STREAM)
    upstream = read_relative(trace_fd, "upstream-completed.jsonl", "UPSTREAM", MAX_STREAM)
    settings_pre = read_relative(trace_fd, "qwen-settings-pre.json", "SETTINGS_PRE", MAX_JSON)
    settings_saved = read_relative(trace_fd, "qwen-settings.json", "SETTINGS_SAVED", MAX_JSON)
    work_digest, _ = trace_tree(trace_fd, "work")
    source_rows = row.get("contamination", {}).get("sources", [])
    source_digests = {item.get("source"): item.get("sha256") for item in source_rows
                      if isinstance(item, dict)}
    if (row.get("result_stream_sha256") != sha(result) or
            row.get("upstream_sha256") != sha(upstream) or row.get("work_sha256") != work_digest or
            source_digests.get("transcript") != sha(result) or
            source_digests.get("settings_pre") != sha(settings_pre) or
            source_digests.get("settings_saved") != sha(settings_saved)):
        fail("validity_conflict")
    return row

def publish_seal(trace_fd, row):
    payload = canonical(row) + b"\n"
    name = ".validity.%s.tmp" % os.getpid()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o600, dir_fd=trace_fd)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(name, "validity.json", src_dir_fd=trace_fd, dst_dir_fd=trace_fd,
                    follow_symlinks=False)
        except FileExistsError:
            fail("validity_conflict")
        os.fsync(trace_fd)
    finally:
        try: os.unlink(name, dir_fd=trace_fd)
        except OSError: pass
    return payload

def ledger_row(validity, validity_digest, manifest):
    return {"schema": 1, "run_tag": validity["run_tag"], "validity_sha256": validity_digest,
            "manifest_sha256": validity["manifest_sha256"], "candidate_sha256": validity["candidate_sha256"],
            "dataset": manifest["dataset"], "arm": manifest["arm"], "valid": True,
            "requested_sha256": validity["identity"]["requested_sha256"],
            "returned_sha256": validity["identity"]["returned_sha256"],
            "transport": validity["transport"], "usage": validity["usage"],
            "delivery": validity["delivery"]}

def append_ledger(path, row):
    parent_fd, name, _ = MANIFEST._open_parent(path, "LEDGER", create=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
        with os.fdopen(fd, "r+b", closefd=True) as handle:
            lock(handle.fileno(), fcntl.LOCK_EX, "ledger_lock_failed")
            data = handle.read()
            if len(data) > MAX_STREAM: fail("ledger_invalid")
            if data and not data.endswith(b"\n"):
                cut = data.rfind(b"\n") + 1
                handle.seek(cut); handle.truncate(); handle.flush(); os.fsync(handle.fileno())
                data = data[:cut]
            rows = []
            for line in data.splitlines():
                try: value = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError, TypeError): fail("ledger_invalid")
                if not isinstance(value, dict): fail("ledger_invalid")
                rows.append(value)
            same_tag = [value for value in rows if value.get("run_tag") == row["run_tag"]]
            if same_tag:
                if len(same_tag) == 1 and same_tag[0] == row: return
                fail("ledger_conflict")
            handle.seek(0, os.SEEK_END); handle.write(canonical(row) + b"\n")
            handle.flush(); os.fsync(handle.fileno()); os.fsync(parent_fd)
    finally:
        os.close(parent_fd)

def state(trace, event, run_tag, manifest, reason=None):
    fields = {"run_tag": run_tag, "phase": event, "trace_dir": trace,
              "dataset": manifest.get("dataset"), "arm": manifest.get("arm")}
    if reason: fields["reason"] = reason[:120]
    # State writing is the only consumer of this mutable helper. Keep it lazy so
    # read-only authenticated projections never depend on executable writer code.
    run_state = load_module("sherlock_run_state", SHERLOCK / "measure" / "run_state.py")
    run_state.write_status(os.path.join(trace, "status.json"), **fields)
    run_state.append_event(os.path.join(trace, "status-events.jsonl"), event, **fields)

def bind_verified_trace(trace, trace_fd, manifest):
    try: current = os.stat(trace, follow_symlinks=False)
    except OSError: fail("trace_identity_changed")
    held = os.fstat(trace_fd)
    if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino): fail("trace_identity_changed")
    data = read_relative(trace_fd, "run-manifest.json", "MANIFEST", MAX_JSON)
    if json_object(data, "trace_identity_changed") != manifest: fail("trace_identity_changed")

def validate_run(trace, commitment_file, commitment_key, ledger):
    trace = MANIFEST.clean_abs(trace)
    trace_fd = MANIFEST._open_dir(trace, "TRACE")
    lock_fd = None
    try:
        lock_fd = os.open(".validity.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                          0o600, dir_fd=trace_fd)
        lock(lock_fd, fcntl.LOCK_EX, "validity_lock_failed")
        try:
            manifest = MANIFEST.verify_manifest(trace, commitment_file, commitment_key)
            key, _, _ = MANIFEST._commitment_key(MANIFEST.clean_abs(commitment_key))
        except Exception:
            fail("manifest_invalid")
        bind_verified_trace(trace, trace_fd, manifest)
        state(trace, "VERIFYING", manifest["run_tag"], manifest)
        try:
            candidate, candidate_data = load_candidate(trace_fd, manifest)
            candidate_sha = sha(candidate_data)
        except ValidityError as error:
            candidate_sha = None
            row = {"schema": 1, "valid": False, "reasons": [error.code],
                   "run_tag": manifest["run_tag"], "manifest_sha256": manifest["manifest_sha256"],
                   "candidate_sha256": None}
        else:
            sealed = existing_seal(trace, trace_fd, key, manifest["manifest_sha256"], candidate_sha)
            if sealed is not None:
                row = sealed
            else:
                try:
                    row = validate_fresh(trace, trace_fd, manifest, candidate, candidate_data)
                except ValidityError as error:
                    row = {"schema": 1, "valid": False, "reasons": [error.code],
                           "run_tag": manifest["run_tag"], "manifest_sha256": manifest["manifest_sha256"],
                           "candidate_sha256": candidate_sha}
        bind_verified_trace(trace, trace_fd, manifest)
        if "hmac_sha256" not in row:
            row["hmac_sha256"] = sign(row, key)
            payload = publish_seal(trace_fd, row)
        else:
            payload = canonical(row) + b"\n"
        phase = "ACCEPTED" if row["valid"] else "REJECTED"
        state(trace, phase, manifest["run_tag"], manifest,
              None if row["valid"] else row["reasons"][0])
        if row["valid"]:
            append_ledger(ledger, ledger_row(row, sha(payload), manifest))
        return row
    finally:
        if lock_fd is not None: os.close(lock_fd)
        os.close(trace_fd)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("trace"); parser.add_argument("--commitment-file", required=True)
    parser.add_argument("--commitment-key", required=True); parser.add_argument("--ledger", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        row = validate_run(args.trace, args.commitment_file, args.commitment_key, args.ledger)
    except ValidityError as error:
        row = {"schema": 1, "valid": False, "reasons": [error.code]}
    except Exception:
        row = {"schema": 1, "valid": False, "reasons": ["internal_error"]}
    if args.json: print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    else: print("ACCEPTED" if row.get("valid") else "REJECTED: %s" % ",".join(row.get("reasons", [])))
    return 0 if row.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
