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
import sys
import tempfile


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
    path = Path(path)
    try:
        state = os.lstat(path)
    except OSError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "missing asset") from exc
    if not stat.S_ISREG(state.st_mode) or stat.S_ISLNK(state.st_mode) or state.st_size > limit:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "unsafe asset")
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "unreadable asset") from exc
    if len(data) > limit:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "oversize asset")
    return data


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(value):
    if not isinstance(value, str):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "time")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "time") from exc


def _time_text(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _atomic_no_replace(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ProbeFailure("APPROVAL_REPLAYED", "no replacement") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
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


def _asset(path, code="TARGET_PROBE_NOT_AUTHORIZED"):
    try:
        data = safe_read_regular(path, 64 * 1024 * 1024)
    except ProbeFailure as exc:
        raise ProbeFailure(code, str(exc)) from exc
    return data, sha256(data)


def _gate_digests():
    return {name: sha256((HERE.parent.parent / "skills" / "v44" / "tools" / (name + ".py")).read_bytes())
            for name in GATES}


def _profile(args):
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
               "settings_sha256": sha256(settings.read_bytes()),
               "system_prompt_sha256": sha256((skill / "SKILL.md").read_bytes()),
               "skill_sha256": _tree_digest(skill),
               "tool_schema_sha256": qwen_sha, "gate_sha256": _gate_digests(),
               "lane_guard": {"enabled": True}}
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
        profile = _profile(args)
        files = {"target-profile.json": canonical(profile) + b"\n",
                 "probe-budget.json": canonical(DEFAULT_BUDGET) + b"\n",
                 "fixture-manifest.json": (fixture_dir / "probe-fixture-manifest.json").read_bytes(),
                 "input-package.json": canonical({"schema": 1, "arm": args.arm,
                     "fixture_tree_sha256": fixture["output_tree_sha256"],
                     "fixture_expectations_sha256": fixture["expectations_sha256"],
                     "runner_sha256": sha256((HERE / "run-manifest.py").read_bytes()),
                     "driver_sha256": sha256((HERE / "run-bench.sh").read_bytes()),
                     "proxy_sha256": sha256((HERE.parent.parent / "measure" / "upstream-log-proxy.py").read_bytes()),
                     "gate_sha256": _gate_digests()}) + b"\n"}
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
    nonce_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_no_replace(nonce_root / (nonce + ".json"), canonical({"nonce": nonce, "manifest_sha256": supplied_hash}) + b"\n")


def authorize(manifest_path, supplied_hash, nonce_root, action="target_contract_probe"):
    raw = safe_read_regular(manifest_path)
    if not _hex(supplied_hash) or not hmac.compare_digest(sha256(raw), supplied_hash):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest hash")
    row = _strict_json(raw, PROBE_MANIFEST_KEYS)
    if row.get("schema") != 1 or row.get("action") != action or _iso(row["expires_at"]) <= _now():
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "action or expiry")
    for name in ("target_profile_sha256", "probe_budget_sha256", "fixture_manifest_sha256", "input_package_sha256"):
        if not _hex(row.get(name)):
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "manifest digest")
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
    return values


def run(manifest_path, supplied_hash, nonce_root, *, secret_reader, proxy_starter, runner):
    """Cross the only contact boundary after approval and all sealed-byte checks."""
    manifest = authorize(manifest_path, supplied_hash, nonce_root)
    root = Path(manifest_path).parent
    package = _verify_package(root, manifest)
    work = root / "probe-work"
    if os.path.lexists(work):
        raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "work already exists")
    work.mkdir(mode=0o700)
    secret = secret_reader(package["target-profile.json"]["secret_ref"])
    proxy = proxy_starter(package["target-profile.json"], secret, root / "probe-budget.json")
    return runner(profile_path=root / "target-profile.json", fixture=root / "fixture",
                  budget_path=root / "probe-budget.json", work=work, proxy=proxy, retries=0)


def audit_identity(mode, expected, returned):
    if mode not in ("provider_pinned_version", "alias_unresolved") or not isinstance(expected, str) or not expected:
        raise ProbeFailure("TARGET_IDENTITY_UNVERIFIABLE")
    if not isinstance(returned, list) or not returned or any(not isinstance(item, str) or not item for item in returned):
        raise ProbeFailure("TARGET_IDENTITY_UNVERIFIABLE")
    if any(item != expected for item in returned):
        raise ProbeFailure("TARGET_IDENTITY_MISMATCH")
    return mode


def _write_result(trace, result):
    path = Path(trace) / "probe-result.json"
    try:
        _atomic_no_replace(path, canonical(result) + b"\n")
    except ProbeFailure:
        # A sealed result exists; never overwrite an earlier attempt's observation.
        pass


def audit(trace):
    trace = Path(trace)
    result = {"schema": 1, "accepted": False, "checked_at": _time_text(_now())}
    try:
        raw_manifest, _ = _asset(trace / "probe-manifest.json", "TARGET_CONTRACT_FAILED")
        manifest = _strict_json(raw_manifest, PROBE_MANIFEST_KEYS)
        package = _verify_package(trace, manifest, "TARGET_CONTRACT_FAILED")
        if _iso(manifest["expires_at"]) <= _now():
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "expired")
        gates = _strict_json(_asset(trace / "probe-gates.json", "TARGET_CONTRACT_FAILED")[0])
        if set(gates) != set(GATES) or any(not isinstance(gates[name], dict) or
                gates[name].get("returncode") != 0 or gates[name].get("blocking") != 0 for name in GATES):
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "gate failure")
        oracle = _strict_json(_asset(trace / "probe-oracle.json", "TARGET_CONTRACT_FAILED")[0])
        if oracle.get("accepted") is not True:
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "oracle failure")
        exits = _strict_json(_asset(trace / "exit-layers.json", "TARGET_CONTRACT_FAILED")[0])
        required_exits = {"attempt_exit_code", "driver_exit_code", "wrapper_exit_code", "gate_exit_code", "terminal_observation"}
        if not required_exits <= set(exits) or any(exits[key] != 0 for key in required_exits - {"terminal_observation"}) or exits["terminal_observation"] != "RUN_SUCCEEDED":
            raise ProbeFailure("TARGET_CONTRACT_FAILED", "exit layer")
        ledger = _strict_json(_asset(trace / "ledger.json", "TARGET_CONTRACT_FAILED")[0])
        budget = _strict_json(_asset(trace / "upstream-budget-state.json", "TARGET_CONTRACT_FAILED")[0])
        if budget.get("schema") != 2 or budget.get("budget_assurance") != "client_pre_dispatch" or not isinstance(budget.get("observed"), dict):
            raise ProbeFailure("TARGET_PROBE_BUDGET")
        calls = ledger.get("provider_calls_observed")
        if type(calls) is not int or calls < 1 or budget["observed"].get("provider_calls") != calls:
            raise ProbeFailure("TARGET_PROBE_BUDGET")
        identity = audit_identity(package["target-profile.json"]["identity_mode"],
                                  package["target-profile.json"]["expected_returned_identity"],
                                  ledger.get("returned_identities"))
        request_tree = _tree_digest(trace / "request-bodies")
        response_tree = _tree_digest(trace / "response-bodies")
        report_raw, report_hash = _asset(trace / "final-report.md", "TARGET_CONTRACT_FAILED")
        receipt = {"schema": 1, "accepted": True, "proof_scope": "representative_sample_only",
                   "created_at": _time_text(_now()), "expires_at": manifest["expires_at"], "nonce": manifest["nonce"],
                   "target_profile_sha256": manifest["target_profile_sha256"], "probe_manifest_sha256": sha256(raw_manifest),
                   "fixture_manifest_sha256": manifest["fixture_manifest_sha256"],
                   "input_package_sha256": manifest["input_package_sha256"],
                   "requested_model": package["target-profile.json"]["requested_model"],
                   "sent_model": package["target-profile.json"]["requested_model"],
                   "returned_identities": ledger["returned_identities"], "identity_assurance": identity,
                   "gate_sha256": {name: sha256(canonical(gates[name])) for name in GATES},
                   "final_report_sha256": report_hash, "ledger_sha256": sha256(canonical(ledger)),
                   "request_body_tree_sha256": request_tree, "response_body_tree_sha256": response_tree,
                   "provider_calls_observed": calls, "usage": ledger.get("usage"),
                   "estimated_cost_rub": budget.get("projected", {}).get("estimated_cost_rub"),
                   "rate_snapshot": budget.get("rate_snapshot"), "budget_assurance": "client_pre_dispatch",
                   "exit_layers": exits, "provider_billed_calls": None, "provider_billed_rub": None,
                   "audit_tool_sha256": sha256(Path(__file__).read_bytes())}
        receipt_path = trace / "target-contract-receipt.json"
        _atomic_no_replace(receipt_path, canonical(receipt) + b"\n")
        _atomic_no_replace(Path(str(receipt_path) + ".sha256"), sha256(receipt_path.read_bytes()).encode("ascii") + b"\n")
        result.update({"accepted": True, "receipt": str(receipt_path), "identity_assurance": identity})
        _write_result(trace, result)
        return result
    except ProbeFailure as exc:
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
    auth = sub.add_parser("run")
    auth.add_argument("--manifest", required=True); auth.add_argument("--operator-approved-probe", required=True)
    auth.add_argument("--nonce-root", required=True)
    approval = sub.add_parser("authorize")
    approval.add_argument("--manifest", required=True); approval.add_argument("--operator-approved-probe", required=True)
    approval.add_argument("--nonce-root", required=True)
    check = sub.add_parser("audit"); check.add_argument("--trace", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            row = prepare(PrepareArgs(**{key.replace("_", "-").replace("-", "_"): value for key, value in vars(args).items() if key != "command"}))
        elif args.command == "audit":
            row = audit(args.trace)
        elif args.command == "authorize":
            row = authorize(args.manifest, args.operator_approved_probe, args.nonce_root)
        else:
            raise ProbeFailure("TARGET_PROBE_NOT_AUTHORIZED", "CLI run requires an integrated runner")
        print(json.dumps(row, sort_keys=True))
        return 0
    except ProbeFailure as exc:
        print(exc.code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
