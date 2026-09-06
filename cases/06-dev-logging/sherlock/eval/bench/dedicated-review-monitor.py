#!/usr/bin/env python3
"""Fail-closed subscription-review observation writer for monitored runs.

The reviewer sees a complete bounded delta, never a capability key.  This is a
development harness component; lifecycle-supervisor remains the authority for
signing observations and stopping an owned controller.
"""
import argparse
import base64
import binascii
import datetime as dt
import gzip
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SCHEMA = 1
DEFAULT_DYNAMIC_CAPACITY = 512 * 1024
DEFAULT_CYCLE_DEADLINE_S = 45.0
# Matches lifecycle-supervisor.MAX_OBSERVATION_AGE_NS: an initial controller
# status is evidence of a live pending permit only inside the existing window.
INITIAL_PENDING_MAX_AGE_S = 60.0
TERMINAL_PHASES = {"BLOCKED", "BLOCKED_UNKNOWN", "FAILED", "SUCCEEDED", "COMPLETE", "TERMINAL"}
NORMAL_TERMINAL_PHASES = {"SUCCEEDED", "COMPLETE", "TERMINAL"}


class MonitorError(RuntimeError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def wall_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp_age(value, collected_at):
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        timestamp = dt.datetime.fromisoformat(value[:-1] + "+00:00")
        age = (collected_at - timestamp).total_seconds()
        return age if age >= 0 else None
    except (OverflowError, ValueError):
        return None


def review_lifecycle(controller, identity, last_request, last_tool, collected_at):
    """Expose the bounded initial-permit state without inventing activity."""
    updated_at = controller.get("updated_at")
    created_at = identity.get("created_at")
    status_age = timestamp_age(updated_at, collected_at)
    identity_age = timestamp_age(created_at, collected_at)
    initial_pending = (controller["phase"] == "HEALTH_CHECKING"
                       and last_request is None and last_tool is None
                       and identity_age is not None and identity_age < INITIAL_PENDING_MAX_AGE_S)
    return {"controller_phase": controller["phase"], "initial_pending": initial_pending,
            "pending_operation": "awaiting initial controller permit" if initial_pending else None,
            "controller_status_updated_at": updated_at,
            "controller_status_age_seconds": status_age,
            "observer_identity_created_at": created_at,
            "observer_identity_age_seconds": identity_age}


def strict_object(raw):
    def reject_duplicates(items):
        row = {}
        for key, value in items:
            if key in row:
                raise ValueError("duplicate JSON key")
            row[key] = value
        return row
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def read_regular(path, maximum=None):
    path = Path(path)
    st = os.lstat(path)
    if not os.path.isfile(path) or os.path.islink(path):
        raise MonitorError("unsafe source %s" % path)
    if maximum is not None and st.st_size > maximum:
        raise MonitorError("source capacity exceeded %s" % path)
    with path.open("rb") as handle:
        data = handle.read((maximum + 1) if maximum is not None else -1)
    if maximum is not None and len(data) > maximum:
        raise MonitorError("source capacity exceeded %s" % path)
    return data, (st.st_dev, st.st_ino)


def atomic_create(path, data, mode=0o400):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_replace(path, data, mode=0o600):
    path = Path(path)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    try:
        atomic_create(tmp, data, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append(path, row):
    with Path(path).open("ab") as handle:
        handle.write(canonical(row) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_helper(path):
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("dedicated_review_lifecycle", path)
    if spec is None or spec.loader is None:
        raise MonitorError("lifecycle helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, sha256(path.read_bytes())


def relative(run_root, path):
    try:
        return str(Path(path).resolve().relative_to(Path(run_root).resolve()))
    except ValueError as exc:
        raise MonitorError("source outside run root") from exc


def object_path(monitor_dir, digest):
    return Path(monitor_dir) / "objects" / digest


def store_object(monitor_dir, data):
    digest = sha256(data)
    path = object_path(monitor_dir, digest)
    try:
        atomic_create(path, data)
    except FileExistsError:
        prior, _ = read_regular(path, len(data))
        if prior != data:
            raise MonitorError("object collision")
    return digest


def load_json_optional(path):
    if not os.path.lexists(path):
        return None
    raw, _ = read_regular(path)
    return strict_object(raw)


def controller_status(run_root):
    rows = sorted(Path(run_root).glob("controller/*/status.json"))
    if len(rows) != 1:
        raise MonitorError("expected exactly one controller status")
    raw, identity = read_regular(rows[0])
    row = strict_object(raw)
    phase = row.get("phase")
    if not isinstance(phase, str):
        raise MonitorError("controller phase")
    return rows[0], raw, identity, row


def ensure_live(run_root, observer):
    observer = Path(observer)
    if os.path.lexists(observer / "fault.json"):
        raise MonitorError("lifecycle fault exists")
    if os.path.lexists(observer.parent / "lifecycle-receipt.json"):
        raise MonitorError("lifecycle receipt exists")
    _, _, _, status = controller_status(run_root)
    if status["phase"] in TERMINAL_PHASES or status["phase"] not in {"HEALTH_CHECKING", "QWEN_RUNNING"}:
        raise MonitorError("controller terminal or not running: %s" % status["phase"])


def discover_observer(run_root):
    identities = sorted(Path(run_root).glob("runs/*/observer-*/identity.json"))
    if len(identities) != 1:
        raise MonitorError("expected exactly one observer identity")
    identity_raw, _ = read_regular(identities[0])
    identity = strict_object(identity_raw)
    nonce, boot = identity.get("run_nonce"), identity.get("boot_id")
    if not isinstance(nonce, str) or not nonce or not isinstance(boot, str) or not boot:
        raise MonitorError("observer identity")
    return identities[0].parent, nonce, boot, identity_raw


def source_paths(run_root, observer):
    root, trace = Path(run_root), Path(observer).parent
    append_only = [
        root / "runs" / (trace.name + ".upstream.jsonl"),
        observer / "hook-events.jsonl", observer / "post-tool-batch-events.jsonl",
        observer / "session-start-events.jsonl", observer / "root-boundary-events.jsonl",
        observer / "guardian-events.jsonl",
        trace / "workspace" / "interactive-events.jsonl",
    ]
    state = [
        observer / "identity.json", observer / "pairs.json", observer / "registry.json",
        observer / "subagent-state.json",
        trace / "upstream-inflight.json", trace / "workspace" / "work" / "checkpoint.json",
        trace / "lifecycle-launch.json",
    ]
    controller, _, _, _ = controller_status(root)
    state.append(controller)
    # Production runs use `upstream-bodies`; retain the fixture-era spelling
    # for provider-free tests and old preserved mirrors.
    trees = [trace / "upstream-bodies", trace / ".upstream.bodies", trace / "workspace" / "work"]
    return append_only, state, trees


def optional_mutable_state(path, observer):
    trace = Path(observer).parent
    return Path(path) in {
        trace / "upstream-inflight.json",
        trace / "workspace" / "work" / "checkpoint.json",
    }


def tree_files(tree):
    if not os.path.lexists(tree):
        return []
    if not Path(tree).is_dir() or os.path.islink(tree):
        raise MonitorError("unsafe tree %s" % tree)
    return [path for path in sorted(Path(tree).rglob("*"))
            if path.is_file() and not path.is_symlink()]


def state_path(monitor_dir):
    return Path(monitor_dir) / "cursor.json"


def load_state(monitor_dir):
    path = state_path(monitor_dir)
    if not path.exists():
        return {"schema": SCHEMA, "sources": {}, "last_completed_request": None,
                "last_completed_tool": None}
    return strict_object(read_regular(path)[0])


def source_entry(run_root, path, prior, capacity):
    raw, identity = read_regular(path, capacity)
    offset = int(prior.get("offset", 0)) if isinstance(prior, dict) else 0
    prior_identity = prior.get("identity") if isinstance(prior, dict) else None
    prior_prefix = prior.get("prefix_sha256") if isinstance(prior, dict) else None
    if offset < 0 or offset > len(raw):
        raise MonitorError("source truncated %s" % path)
    if prior_identity is not None and prior_identity != list(identity):
        raise MonitorError("source identity changed %s" % path)
    if offset and (not isinstance(prior_prefix, str) or sha256(raw[:offset]) != prior_prefix):
        raise MonitorError("source prefix changed %s" % path)
    delta = raw[offset:]
    entry = {"path": relative(run_root, path), "identity": list(identity), "offset": offset,
             "end": len(raw), "prefix_sha256": sha256(raw), "sha256": sha256(raw),
             "data_base64": base64.b64encode(delta).decode("ascii")}
    if path.name in {"hook-events.jsonl", "post-tool-batch-events.jsonl", "guardian-events.jsonl"}:
        decoded = []
        for line in delta.splitlines():
            try:
                event = strict_object(line)
                encoded = event.get("input_base64")
                if encoded is not None:
                    if not isinstance(encoded, str) or not isinstance(event.get("input_sha256"), str):
                        raise MonitorError("event input encoding")
                    try:
                        decoded_raw = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise MonitorError("event input encoding") from exc
                    if sha256(decoded_raw) != event["input_sha256"]:
                        raise MonitorError("event input digest")
                    event["decoded_input"] = decoded_raw.decode("utf-8")
                    try:
                        decoded_json = strict_object(decoded_raw)
                        event["decoded_input_json"] = decoded_json
                        for field in ("tool_input", "output", "error", "tool_response", "result"):
                            if field in decoded_json:
                                event["decoded_" + field] = decoded_json[field]
                    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                        pass
                decoded.append(event)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise MonitorError("malformed %s event" % path.name) from exc
        entry["decoded_events"] = decoded
    if path.name.endswith(".gz") and delta:
        try:
            decoded_raw = gzip.decompress(delta)
        except (OSError, EOFError) as exc:
            raise MonitorError("malformed gzip evidence") from exc
        try:
            decoded_text = decoded_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MonitorError("non-UTF8 gzip evidence") from exc
        try:
            entry["decoded_json"] = strict_object(decoded_raw)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            entry["decoded_text"] = decoded_text
    return entry, raw, identity


def static_entry(run_root, path, capacity):
    raw, identity = read_regular(path, capacity)
    return ({"path": relative(run_root, path), "identity": list(identity),
             "sha256": sha256(raw), "data_base64": base64.b64encode(raw).decode("ascii")},
            raw, identity)


def latest_request(delta, prior):
    result = prior
    for line in delta.splitlines():
        try:
            row = strict_object(line)
        except (ValueError, UnicodeDecodeError):
            continue
        request_id = row.get("request_id")
        if isinstance(request_id, str) and row.get("status") == 200:
            result = request_id
    return result


def latest_tool(pairs, prior):
    if not isinstance(pairs, dict) or not isinstance(pairs.get("pairs"), dict):
        raise MonitorError("pairs state malformed")
    best = (-1, prior)
    for key, pair in pairs["pairs"].items():
        if not isinstance(pair, dict):
            raise MonitorError("pair malformed")
        row = pair.get("post")
        if row is None:
            pre = pair.get("pre")
            if (isinstance(pre, dict) and isinstance(pre.get("sequence"), int)
                    and isinstance(pre.get("output"), dict)
                    and pre["output"].get("continue") is False):
                row = pre
        if isinstance(row, dict) and isinstance(row.get("sequence"), int):
            tool_id = pair.get("tool_call_id") or key
            if isinstance(tool_id, str):
                best = max(best, (row["sequence"], tool_id))
    return best[1]


def bind_gzip_object(monitor_dir, path, entry, raw):
    """Keep original provider bytes immutable outside the reviewer prompt."""
    if Path(path).name.endswith(".gz"):
        entry["raw_object_sha256"] = store_object(monitor_dir, raw)
        del entry["data_base64"]


def verify_snapshot_object_bindings(monitor_dir, snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("dynamic"), list):
        raise MonitorError("snapshot dynamic evidence")
    for entry in snapshot["dynamic"]:
        if not isinstance(entry, dict) or "raw_object_sha256" not in entry:
            continue
        digest = entry["raw_object_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or entry.get("sha256") != digest:
            raise MonitorError("snapshot raw object binding")
        try:
            raw, _ = read_regular(object_path(monitor_dir, digest))
        except OSError as exc:
            raise MonitorError("snapshot raw object binding") from exc
        if sha256(raw) != digest:
            raise MonitorError("snapshot raw object digest")


def collect_snapshot(run_root, observer, monitor_dir, state, *, capacity):
    """Read complete new dynamic evidence and retain identities for revalidation."""
    append_only, statics, trees = source_paths(run_root, observer)
    total = 0
    captured, validation, next_sources = [], [], {}
    prior_sources = state.get("sources")
    if not isinstance(prior_sources, dict):
        raise MonitorError("cursor sources malformed")
    for path in append_only:
        key = relative(run_root, path)
        if not os.path.lexists(path):
            if key in prior_sources:
                raise MonitorError("source disappeared %s" % path)
            continue
        entry, raw, identity = source_entry(run_root, path, prior_sources.get(key), capacity - total)
        bind_gzip_object(monitor_dir, path, entry, raw)
        total += len(raw) - entry["offset"]
        if total > capacity:
            raise MonitorError("dynamic evidence capacity exceeded")
        captured.append(entry)
        validation.append((path, raw, identity, "append"))
        next_sources[key] = {"offset": len(raw), "identity": list(identity),
                             "prefix_sha256": sha256(raw)}
    state_entries = []
    controller, observer_identity = None, None
    for path in statics:
        if not os.path.lexists(path):
            continue
        try:
            entry, raw, file_identity = static_entry(run_root, path, capacity - total)
        except FileNotFoundError:
            if optional_mutable_state(path, observer):
                continue
            raise MonitorError("state source disappeared %s" % path)
        except OSError as exc:
            raise MonitorError("state source unreadable %s" % path) from exc
        total += len(raw)
        if total > capacity:
            raise MonitorError("dynamic evidence capacity exceeded")
        state_entries.append(entry)
        mode = "critical" if path.name == "identity.json" else "mutable"
        validation.append((path, raw, file_identity, mode))
        if path.name == "status.json":
            controller = strict_object(raw)
        if path.name == "identity.json":
            observer_identity = strict_object(raw)
    mutable_tree_paths = {relative(run_root, statics_path) for statics_path in statics}
    for tree in trees:
        for path in tree_files(tree):
            key = relative(run_root, path)
            if key in mutable_tree_paths:
                continue
            entry, raw, identity = source_entry(run_root, path, prior_sources.get(key), capacity - total)
            bind_gzip_object(monitor_dir, path, entry, raw)
            total += len(raw) - entry["offset"]
            if total > capacity:
                raise MonitorError("dynamic evidence capacity exceeded")
            captured.append(entry)
            validation.append((path, raw, identity, "append"))
            next_sources[key] = {"offset": len(raw), "identity": list(identity),
                                 "prefix_sha256": sha256(raw)}
    current_dynamic = set(next_sources)
    dynamic_roots = tuple(relative(run_root, tree) + "/" for tree in trees)
    for key in prior_sources:
        if key in mutable_tree_paths:
            continue
        if key.startswith(dynamic_roots) and key not in current_dynamic:
            raise MonitorError("dynamic source disappeared %s" % key)
    pairs_entry = next((item for item in state_entries if item["path"].endswith("/pairs.json")), None)
    if pairs_entry is None:
        raise MonitorError("pairs missing")
    pairs = strict_object(base64.b64decode(pairs_entry["data_base64"]))
    if controller is None or not isinstance(controller.get("phase"), str):
        raise MonitorError("controller phase")
    if observer_identity is None or not isinstance(observer_identity.get("run_nonce"), str):
        raise MonitorError("observer identity")
    upstream = next((item for item in captured if item["path"].endswith(".upstream.jsonl")), None)
    request_delta = base64.b64decode(upstream["data_base64"]) if upstream else b""
    last_request = latest_request(request_delta, state.get("last_completed_request"))
    last_tool = latest_tool(pairs, state.get("last_completed_tool"))
    collected_at = dt.datetime.now(dt.timezone.utc)
    snapshot = {"schema": SCHEMA, "run_nonce": observer_identity["run_nonce"],
                "collected_at": collected_at.isoformat().replace("+00:00", "Z"),
                "dynamic": captured, "state": state_entries,
                "last_completed_request": last_request, "last_completed_tool": last_tool,
                "review_lifecycle": review_lifecycle(
                    controller, observer_identity, last_request, last_tool, collected_at),
                "capacity_bytes": capacity, "captured_dynamic_bytes": total}
    raw = canonical(snapshot)
    return snapshot, raw, validation, {"schema": SCHEMA, "sources": next_sources,
                                        "last_completed_request": snapshot["last_completed_request"],
                                        "last_completed_tool": snapshot["last_completed_tool"]}


def revalidate(validation):
    for path, captured, identity, mode in validation:
        # Mutable state is valid only at its independently captured instant;
        # its later replacement or removal is evidence for the next snapshot.
        if mode == "mutable":
            continue
        try:
            current, current_identity = read_regular(path)
        except OSError as exc:
            raise MonitorError("source unavailable before publish %s" % path) from exc
        if mode in {"append", "critical"} and current_identity != identity:
            raise MonitorError("source identity changed before publish %s" % path)
        if mode == "append":
            if not current.startswith(captured):
                raise MonitorError("source prefix changed before publish %s" % path)
        elif mode == "critical" and current != captured:
            raise MonitorError("state changed before publish %s" % path)


def load_command(path):
    raw, _ = read_regular(path)
    row = strict_object(raw)
    argv = row.get("argv")
    if (not isinstance(argv, list) or not argv or not isinstance(argv[0], str) or not argv[0]
            or any(not isinstance(item, str) for item in argv[1:])):
        raise MonitorError("review command argv")
    return argv, sha256(raw), raw


def verify_launch(lifecycle, observer, nonce, boot, helper_hash):
    raw, _ = read_regular(Path(observer).parent / "lifecycle-launch.json")
    launch = strict_object(raw)
    lifecycle.verify_signed_record(observer, launch)
    if (launch.get("run_nonce") != nonce or launch.get("boot_id") != boot
            or launch.get("lifecycle_helper_sha256") != helper_hash
            or not isinstance(launch.get("package_sha256"), str)):
        raise MonitorError("launch binding")
    return raw


def expected_model(argv):
    """Return the canonical primary model required by the pinned CLI command."""
    try:
        model = argv[argv.index("--model") + 1]
    except (ValueError, IndexError) as exc:
        raise MonitorError("review command model") from exc
    if model != "sonnet":
        raise MonitorError("review command must pin sonnet")
    return "claude-sonnet-5"


def parse_decision(stdout, snapshot_digest, nonce, last_request, last_tool,
                   expected_primary_model):
    try:
        outer = strict_object(stdout)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise MonitorError("review envelope JSON") from exc
    if outer.get("is_error") is not False or outer.get("subtype") != "success":
        raise MonitorError("review envelope unsuccessful")
    if outer.get("terminal_reason") != "completed":
        raise MonitorError("review envelope terminal reason")
    usage = outer.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        raise MonitorError("review model usage missing")
    primary = [entry for entry in usage.values()
               if isinstance(entry, dict) and entry.get("canonicalModel") == expected_primary_model]
    if len(primary) != 1:
        raise MonitorError("review primary model identity")
    body = outer.get("result")
    if not isinstance(body, str):
        raise MonitorError("review result missing")
    try:
        decision = strict_object(body.encode("utf-8"))
    except (UnicodeEncodeError, ValueError, json.JSONDecodeError) as exc:
        raise MonitorError("review decision JSON") from exc
    required = {"schema", "snapshot_sha256", "run_nonce", "decision",
                "last_completed_request", "last_completed_tool", "pending_operation", "reason"}
    if set(decision) != required or decision.get("schema") != SCHEMA:
        raise MonitorError("review decision schema")
    if decision["snapshot_sha256"] != snapshot_digest or decision["run_nonce"] != nonce:
        raise MonitorError("review identity mismatch")
    if decision["decision"] not in {"continue", "stop"}:
        raise MonitorError("review decision")
    if decision["last_completed_request"] != last_request or decision["last_completed_tool"] != last_tool:
        raise MonitorError("review completed identifiers")
    if not isinstance(decision["pending_operation"], str) or not decision["pending_operation"]:
        raise MonitorError("review pending operation")
    if not isinstance(decision["reason"], str) or len(decision["reason"]) > 4096:
        raise MonitorError("review reason")
    return decision, outer, primary[0]


def fail(lifecycle, observer, nonce, monitor_dir, reason):
    row = {"schema": SCHEMA, "failed_at": wall_now(), "reason": str(reason)}
    atomic_replace(Path(monitor_dir) / "failure.json", canonical(row) + b"\n")
    if not os.path.lexists(Path(observer) / "fault.json"):
        lifecycle.record_fault(observer, nonce, "REVIEW_MONITOR_FAILED", str(reason)[:4096])
    raise MonitorError(str(reason))


def terminal_boundary(run_root, observer):
    """A normal controller close is audit work, never a review failure."""
    if os.path.lexists(Path(observer) / "fault.json"):
        return False
    if os.path.lexists(Path(observer).parent / "lifecycle-receipt.json"):
        return True
    _, _, _, status = controller_status(run_root)
    return status["phase"] in NORMAL_TERMINAL_PHASES


def terminal_boundary_reason(run_root, observer):
    if os.path.lexists(Path(observer).parent / "lifecycle-receipt.json"):
        return "lifecycle receipt"
    return "controller terminal: %s" % controller_status(run_root)[3]["phase"]


def preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir, *, snapshot_digest,
                            prompt_digest, command_hash, launch_digest, helper_hash,
                            stdout_digest=None, stderr_digest=None, exit_code=None,
                            stdout=None, last_request=None, last_tool=None, argv=None):
    try:
        atomic_create(Path(monitor_dir) / "reviews.jsonl", b"", 0o600)
    except FileExistsError:
        pass
    row = {"schema": SCHEMA, "run_nonce": nonce, "terminal_unpublished": True,
           "terminal_reason": terminal_boundary_reason(run_root, observer),
           "snapshot_sha256": snapshot_digest, "review_prompt_sha256": prompt_digest,
           "review_command_sha256": command_hash, "launch_sha256": launch_digest,
           "lifecycle_helper_sha256": helper_hash, "recorded_at": wall_now()}
    if stdout_digest is not None:
        row.update({"review_stdout_sha256": stdout_digest,
                    "review_stderr_sha256": stderr_digest, "review_exit_code": exit_code})
        try:
            decision, outer, primary = parse_decision(
                stdout, snapshot_digest, nonce, last_request, last_tool, expected_model(argv))
            row.update({"review_model": primary["canonicalModel"], "review_usage": outer.get("usage"),
                        "review_model_usage": outer["modelUsage"], "decision": decision})
        except MonitorError:
            pass
    append(Path(monitor_dir) / "terminal-unpublished-reviews.jsonl",
           lifecycle._sign_record(observer, row))


def run_cycle(run_root, observer, nonce, boot, helper_path, prompt_path, command_path,
              monitor_dir, *, deadline_s=DEFAULT_CYCLE_DEADLINE_S,
              dynamic_capacity=DEFAULT_DYNAMIC_CAPACITY):
    started = time.monotonic()
    run_root, observer, monitor_dir = Path(run_root).resolve(), Path(observer).resolve(), Path(monitor_dir).resolve()
    lifecycle, helper_hash = load_helper(helper_path)
    prompt = read_regular(prompt_path)[0]
    argv, command_hash, command_raw = load_command(command_path)
    state = load_state(monitor_dir)
    try:
        ensure_live(run_root, observer)
        identity = load_json_optional(observer / "identity.json")
        if identity.get("run_nonce") != nonce or identity.get("boot_id") != boot:
            raise MonitorError("observer identity changed")
        launch_raw = verify_launch(lifecycle, observer, nonce, boot, helper_hash)
        prompt_digest, command_digest, launch_digest = (store_object(monitor_dir, prompt),
                                                         store_object(monitor_dir, command_raw),
                                                         store_object(monitor_dir, launch_raw))
        snapshot, snapshot_raw, validation, next_state = collect_snapshot(
            run_root, observer, monitor_dir, state, capacity=dynamic_capacity)
        snapshot_digest = store_object(monitor_dir, snapshot_raw)
        if terminal_boundary(run_root, observer):
            preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir,
                                    snapshot_digest=snapshot_digest, prompt_digest=prompt_digest,
                                    command_hash=command_hash, launch_digest=launch_digest,
                                    helper_hash=helper_hash)
            return None
        remaining = deadline_s - (time.monotonic() - started)
        if remaining <= 1:
            raise MonitorError("cycle deadline before review")
        review_input = prompt + b"\nSNAPSHOT_SHA256=" + snapshot_digest.encode() + b"\n" + snapshot_raw + b"\n"
        try:
            completed = subprocess.run(argv, input=review_input, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, check=False, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            store_object(monitor_dir, stdout); store_object(monitor_dir, stderr)
            raise MonitorError("review timeout") from exc
        stdout_digest, stderr_digest = store_object(monitor_dir, completed.stdout), store_object(monitor_dir, completed.stderr)
        if terminal_boundary(run_root, observer):
            preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir,
                                    snapshot_digest=snapshot_digest, prompt_digest=prompt_digest,
                                    command_hash=command_hash, launch_digest=launch_digest,
                                    helper_hash=helper_hash, stdout_digest=stdout_digest,
                                    stderr_digest=stderr_digest, exit_code=completed.returncode,
                                    stdout=completed.stdout,
                                    last_request=snapshot["last_completed_request"],
                                    last_tool=snapshot["last_completed_tool"], argv=argv)
            return None
        if completed.returncode != 0:
            raise MonitorError("review exit %s" % completed.returncode)
        decision, outer, primary_usage = parse_decision(
            completed.stdout, snapshot_digest, nonce,
            snapshot["last_completed_request"], snapshot["last_completed_tool"],
            expected_model(argv))
        if decision["decision"] != "continue":
            raise MonitorError("review stop decision")
        if time.monotonic() >= started + deadline_s:
            raise MonitorError("cycle deadline before publication")
        if terminal_boundary(run_root, observer):
            preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir,
                                    snapshot_digest=snapshot_digest, prompt_digest=prompt_digest,
                                    command_hash=command_hash, launch_digest=launch_digest,
                                    helper_hash=helper_hash, stdout_digest=stdout_digest,
                                    stderr_digest=stderr_digest, exit_code=completed.returncode,
                                    stdout=completed.stdout,
                                    last_request=snapshot["last_completed_request"],
                                    last_tool=snapshot["last_completed_tool"], argv=argv)
            return None
        ensure_live(run_root, observer)
        try:
            revalidate(validation)
        except MonitorError:
            if terminal_boundary(run_root, observer):
                preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir,
                                        snapshot_digest=snapshot_digest, prompt_digest=prompt_digest,
                                        command_hash=command_hash, launch_digest=launch_digest,
                                        helper_hash=helper_hash, stdout_digest=stdout_digest,
                                        stderr_digest=stderr_digest, exit_code=completed.returncode,
                                        stdout=completed.stdout,
                                        last_request=snapshot["last_completed_request"],
                                        last_tool=snapshot["last_completed_tool"], argv=argv)
                return None
            raise
        prior = load_json_optional(observer / "current-observation.json")
        sequence = 0 if prior is None else prior.get("sequence", -1) + 1
        observation = lifecycle.publish_observation(
            observer, nonce, boot, sequence=sequence,
            last_completed_request=snapshot["last_completed_request"],
            last_completed_tool=snapshot["last_completed_tool"],
            pending_operation=decision["pending_operation"])
        observation_raw = canonical(observation) + b"\n"
        review = {"schema": SCHEMA, "sequence": sequence, "run_nonce": nonce,
                  "snapshot_sha256": snapshot_digest, "review_stdout_sha256": stdout_digest,
                  "review_stderr_sha256": stderr_digest, "review_command_sha256": command_hash,
                  "review_prompt_sha256": prompt_digest, "launch_sha256": launch_digest,
                  "lifecycle_helper_sha256": helper_hash,
                  "review_model": primary_usage["canonicalModel"],
                  "review_usage": outer.get("usage"), "review_model_usage": outer["modelUsage"],
                  "decision": decision, "observation_sha256": sha256(observation_raw),
                  "review_duration_ns": time.monotonic_ns() - int(started * 1_000_000_000),
                  "published_at": wall_now()}
        review = lifecycle._sign_record(observer, review)
        append(monitor_dir / "reviews.jsonl", review)
        atomic_replace(state_path(monitor_dir), canonical(next_state) + b"\n")
        return review
    except lifecycle.LifecycleFault as exc:
        if exc.reason == "TERMINAL_OBSERVATION" and terminal_boundary(run_root, observer):
            preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir,
                                    snapshot_digest=snapshot_digest, prompt_digest=prompt_digest,
                                    command_hash=command_hash, launch_digest=launch_digest,
                                    helper_hash=helper_hash, stdout_digest=stdout_digest,
                                    stderr_digest=stderr_digest, exit_code=completed.returncode,
                                    stdout=completed.stdout,
                                    last_request=snapshot["last_completed_request"],
                                    last_tool=snapshot["last_completed_tool"], argv=argv)
            return None
        return fail(lifecycle, observer, nonce, monitor_dir, exc)
    except MonitorError as exc:
        if terminal_boundary(run_root, observer):
            if "snapshot_digest" in locals():
                preserve_terminal_delta(lifecycle, run_root, observer, nonce, monitor_dir,
                                        snapshot_digest=snapshot_digest, prompt_digest=prompt_digest,
                                        command_hash=command_hash, launch_digest=launch_digest,
                                        helper_hash=helper_hash,
                                        stdout_digest=locals().get("stdout_digest"),
                                        stderr_digest=locals().get("stderr_digest"),
                                        exit_code=completed.returncode if "completed" in locals() else None,
                                        stdout=completed.stdout if "completed" in locals() else None,
                                        last_request=snapshot["last_completed_request"],
                                        last_tool=snapshot["last_completed_tool"], argv=argv)
            return None
        return fail(lifecycle, observer, nonce, monitor_dir, exc)


def acquire_lock(run_root):
    run_root = Path(run_root)
    path = run_root.parent / (".%s.review-monitor.lock" % run_root.name)
    try:
        atomic_create(path, canonical({"schema": SCHEMA, "pid": os.getpid(), "started_at": wall_now()}) + b"\n", 0o600)
    except FileExistsError as exc:
        raise MonitorError("monitor writer already exists") from exc
    return path


def prepare_monitor(run_root, monitor_dir):
    """Prepare only sibling monitor state; never create the fresh run root."""
    run_root, monitor_dir = Path(run_root).resolve(strict=False), Path(monitor_dir).resolve(strict=False)
    if monitor_dir.parent != run_root.parent or monitor_dir.name != run_root.name + ".monitor":
        raise MonitorError("monitor directory must be the run-root sibling")
    monitor_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    (monitor_dir / "objects").mkdir(mode=0o700)
    return acquire_lock(run_root)


def watch(args):
    run_root, monitor_dir = Path(args.run_root).resolve(strict=False), Path(args.monitor_dir).resolve(strict=False)
    lock = prepare_monitor(run_root, monitor_dir)
    try:
        while True:
            try:
                observer, nonce, boot, _ = discover_observer(run_root)
            except MonitorError:
                time.sleep(args.poll_s)
                continue
            if os.path.lexists(observer / "fault.json") or os.path.lexists(observer.parent / "lifecycle-receipt.json"):
                return 0
            if terminal_boundary(run_root, observer):
                return 0
            if not os.path.lexists(observer.parent / "lifecycle-launch.json"):
                time.sleep(args.poll_s)
                continue
            run_cycle(run_root, observer, nonce, boot, args.helper, args.prompt,
                      args.review_command_json, monitor_dir,
                      deadline_s=args.cycle_deadline_s, dynamic_capacity=args.dynamic_byte_capacity)
            time.sleep(args.poll_s)
    finally:
        if os.path.lexists(lock):
            os.unlink(lock)


def audit(run_root, monitor_dir, helper_path):
    run_root, monitor_dir = Path(run_root).resolve(), Path(monitor_dir).resolve()
    observer, nonce, boot, _ = discover_observer(run_root)
    lifecycle, helper_hash = load_helper(helper_path)
    receipt_path = observer.parent / "lifecycle-receipt.json"
    if not receipt_path.exists():
        raise MonitorError("terminal receipt missing")
    receipt_raw, _ = read_regular(receipt_path)
    lifecycle.verify_signed_record(observer, strict_object(receipt_raw))
    reviews_path = monitor_dir / "reviews.jsonl"
    if not reviews_path.exists():
        raise MonitorError("review ledger missing")
    observations_path = observer / "observations.jsonl"
    observations = observations_path.read_bytes().splitlines() if observations_path.exists() else []
    reviews = reviews_path.read_bytes().splitlines()
    if len(observations) != len(reviews):
        raise MonitorError("observation/review count")
    observation_hashes = []
    for sequence, (raw_observation, raw_review) in enumerate(zip(observations, reviews)):
        observation = strict_object(raw_observation)
        review = strict_object(raw_review)
        identity = lifecycle._identity(observer)
        key = lifecycle._read_regular(observer / "observation.key", 32)
        required_observation = {"schema", "run_nonce", "sequence", "boot_id", "monotonic_ns",
                                "wall_time", "last_completed_request", "last_completed_tool",
                                "pending_operation", "mac"}
        if (set(observation) != required_observation or observation.get("run_nonce") != nonce
                or observation.get("boot_id") != boot or not isinstance(observation.get("mac"), str)
                or not hmac.compare_digest(sha256(key), identity.get("capability_sha256", ""))
                or not hmac.compare_digest(observation["mac"], lifecycle._observation_mac(key, observation))):
            raise MonitorError("observation authentication")
        try:
            lifecycle.verify_signed_record(observer, review)
        except lifecycle.LifecycleFault as exc:
            raise MonitorError("review authentication") from exc
        if (observation.get("sequence") != sequence or review.get("sequence") != sequence
                or review.get("run_nonce") != nonce
                or review.get("lifecycle_helper_sha256") != helper_hash
                or review.get("observation_sha256") != sha256(raw_observation + b"\n")):
            raise MonitorError("review observation/helper binding")
        for field in ("snapshot_sha256", "review_stdout_sha256", "review_stderr_sha256",
                      "review_prompt_sha256", "review_command_sha256", "launch_sha256"):
            value = review.get(field)
            if (not isinstance(value, str) or len(value) != 64
                    or not object_path(monitor_dir, value).is_file()):
                raise MonitorError("review object binding")
            try:
                object_raw = object_path(monitor_dir, value).read_bytes()
            except OSError as exc:
                raise MonitorError("review object binding") from exc
            if sha256(object_raw) != value:
                raise MonitorError("review object digest")
            if field == "snapshot_sha256":
                verify_snapshot_object_bindings(monitor_dir, strict_object(object_raw))
        observation_hashes.append(sha256(raw_observation + b"\n"))
    terminal_review_hashes = []
    terminal_reviews_path = monitor_dir / "terminal-unpublished-reviews.jsonl"
    if terminal_reviews_path.exists():
        for raw_terminal_review in terminal_reviews_path.read_bytes().splitlines():
            terminal_review = strict_object(raw_terminal_review)
            try:
                lifecycle.verify_signed_record(observer, terminal_review)
            except lifecycle.LifecycleFault as exc:
                raise MonitorError("terminal review authentication") from exc
            required = {"schema", "run_nonce", "terminal_unpublished", "terminal_reason",
                        "snapshot_sha256", "review_prompt_sha256", "review_command_sha256",
                        "launch_sha256", "lifecycle_helper_sha256", "recorded_at", "key_id", "hmac_sha256"}
            if (not required.issubset(terminal_review) or terminal_review["schema"] != SCHEMA
                    or terminal_review["run_nonce"] != nonce
                    or terminal_review["terminal_unpublished"] is not True
                    or terminal_review["lifecycle_helper_sha256"] != helper_hash
                    or not isinstance(terminal_review["terminal_reason"], str)):
                raise MonitorError("terminal review helper binding")
            fields = ["snapshot_sha256", "review_prompt_sha256", "review_command_sha256", "launch_sha256"]
            has_output = "review_stdout_sha256" in terminal_review
            if has_output != ("review_stderr_sha256" in terminal_review):
                raise MonitorError("terminal review output binding")
            if has_output:
                if not isinstance(terminal_review.get("review_exit_code"), int):
                    raise MonitorError("terminal review exit binding")
                fields.extend(["review_stdout_sha256", "review_stderr_sha256"])
            for field in fields:
                value = terminal_review.get(field)
                if not isinstance(value, str) or len(value) != 64:
                    raise MonitorError("terminal review object binding")
                try:
                    object_raw, _ = read_regular(object_path(monitor_dir, value))
                except OSError as exc:
                    raise MonitorError("terminal review object binding") from exc
                if sha256(object_raw) != value:
                    raise MonitorError("terminal review object digest")
                if field == "snapshot_sha256":
                    verify_snapshot_object_bindings(monitor_dir, strict_object(object_raw))
            terminal_review_hashes.append(sha256(raw_terminal_review + b"\n"))
    # The terminal boundary may contain events appended after the last accepted
    # observation.  Preserve that unreviewed delta explicitly; audit never
    # mislabels it as another subscription decision.
    terminal_snapshot, terminal_raw, _, _ = collect_snapshot(
        run_root, observer, monitor_dir, load_state(monitor_dir),
        capacity=DEFAULT_DYNAMIC_CAPACITY)
    terminal_snapshot["terminal_unreviewed"] = True
    terminal_snapshot_digest = store_object(monitor_dir, canonical(terminal_snapshot))
    result = {"schema": SCHEMA, "run_nonce": nonce, "boot_id": boot,
              "original_lifecycle_receipt_sha256": sha256(receipt_raw),
              "review_ledger_sha256": sha256(reviews_path.read_bytes()),
              "accepted_observation_sha256": observation_hashes,
              "terminal_unpublished_review_sha256": terminal_review_hashes,
              "terminal_unreviewed_snapshot_sha256": terminal_snapshot_digest,
              "audited_at": wall_now()}
    result = lifecycle._sign_record(observer, result)
    atomic_replace(monitor_dir / "terminal-audit.json", canonical(result) + b"\n")
    return result


def parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    watch_p = sub.add_parser("watch")
    for item in (watch_p,):
        item.add_argument("--run-root", required=True); item.add_argument("--helper", required=True)
        item.add_argument("--prompt", required=True); item.add_argument("--review-command-json", required=True)
        item.add_argument("--monitor-dir", required=True); item.add_argument("--poll-s", type=float, default=1.0)
        item.add_argument("--cycle-deadline-s", type=float, default=DEFAULT_CYCLE_DEADLINE_S)
        item.add_argument("--dynamic-byte-capacity", type=int, default=DEFAULT_DYNAMIC_CAPACITY)
    audit_p = sub.add_parser("audit")
    audit_p.add_argument("--run-root", required=True); audit_p.add_argument("--monitor-dir", required=True)
    audit_p.add_argument("--helper", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = watch(args) if args.command == "watch" else audit(args.run_root, args.monitor_dir, args.helper)
        if result is not None:
            sys.stdout.buffer.write(canonical(result) + b"\n")
        return 0
    except (MonitorError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("DEDICATED_REVIEW_MONITOR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
