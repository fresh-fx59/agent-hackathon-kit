#!/usr/bin/env python3
"""Build a sealed, deterministic corpus for the Sherlock contract probe."""
import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath


RECIPE_KEYS = {"schema", "dataset", "ranges", "required_shapes"}
RANGE_KEYS = {"source", "start", "end", "destination"}
SELECT_RANGE_KEYS = {"source", "select", "destination"}
SELECT_KEYS = {"count", "required_event_data_fields"}
SHAPE_SCHEMAS = {
    "authentication": {"file", "ip_field", "message_field"},
    "inventory": {"file", "service_field", "process_field"},
    "reported_context": {"file", "line", "message_field"},
    "timeline": {"left_file", "right_file", "time_field"},
}
CONTROL_NAMES = {"probe-expectations.json", "probe-fixture-manifest.json"}


class ContractError(Exception):
    pass


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _no_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: %s" % key)
        value[key] = item
    return value


def _strict_json(text):
    return json.loads(text, object_pairs_hook=_no_duplicate_keys)


def _read_recipe(recipe):
    if isinstance(recipe, (str, Path)):
        path = Path(recipe)
        try:
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise OSError("recipe is not a regular file")
            return _strict_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ContractError("PROBE_RECIPE_READ") from exc
    return recipe


def _nonempty_string(value, error):
    if type(value) is not str or not value or any(ord(char) < 32 or ord(char) == 127
                                                   for char in value):
        raise ContractError(error)
    return value


def _canonical_relative(value, error):
    value = _nonempty_string(value, error)
    if "\\" in value:
        raise ContractError(error)
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ContractError(error)
    parts = [part for part in candidate.parts if part != "."]
    if not parts:
        raise ContractError(error)
    return "/".join(parts)


def _contains(child, parent):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _samefile(left, right):
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _ancestors(path):
    current = Path(path)
    while True:
        yield current
        if current.parent == current:
            return
        current = current.parent


def _destination_conflicts(source_root, destination):
    """Reject a destination whose existing target is source or below it."""
    existing = Path(destination)
    while not os.path.lexists(existing):
        if existing.parent == existing:
            return False
        existing = existing.parent
    try:
        # Resolve the actual target before walking ancestors: the lexical path
        # can cross an external symlink whose target is case-folded source data.
        target = existing.resolve(strict=True)
    except OSError:
        return False
    return any(_samefile(ancestor, source_root) for ancestor in _ancestors(target))


def _regular_directory(path, error):
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise ContractError(error) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ContractError(error)


def _read_stable_regular_bytes(root, relative, error):
    """Read one root-contained regular file without following a final symlink."""
    path = root
    parts = relative.split("/")
    for index, component in enumerate(parts):
        path = path / component
        try:
            mode = os.lstat(path).st_mode
        except OSError as exc:
            raise ContractError(error) from exc
        if stat.S_ISLNK(mode) or (index < len(parts) - 1 and not stat.S_ISDIR(mode)):
            raise ContractError(error)
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise ContractError(error)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ContractError(error)
            chunks = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = os.lstat(path)
    except OSError as exc:
        raise ContractError(error) from exc
    fingerprint = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if fingerprint(before) != fingerprint(opened) or fingerprint(before) != fingerprint(after) or \
            fingerprint(before) != fingerprint(current):
        raise ContractError(error)
    return b"".join(chunks)


def _read_regular_bytes(root, relative, error):
    """Read the immutable extraction snapshot; final verification reopens directly."""
    return _read_stable_regular_bytes(root, relative, error)


def _source_identity(root, relative, error):
    path = root
    parts = relative.split("/")
    for index, component in enumerate(parts):
        path = path / component
        try:
            item = os.lstat(path)
        except OSError as exc:
            raise ContractError(error) from exc
        if stat.S_ISLNK(item.st_mode) or (index < len(parts) - 1 and not stat.S_ISDIR(item.st_mode)):
            raise ContractError(error)
    if not stat.S_ISREG(item.st_mode):
        raise ContractError(error)
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns


def _event_data(record):
    event = record.get("Event", {}) if isinstance(record, dict) else {}
    return event.get("EventData", {}) if isinstance(event, dict) else {}


def _event_time(record):
    event = record.get("Event", {}) if isinstance(record, dict) else {}
    system = event.get("System", {}) if isinstance(event, dict) else {}
    created = system.get("TimeCreated", {}) if isinstance(system, dict) else {}
    attrs = created.get("#attributes", {}) if isinstance(created, dict) else {}
    return attrs.get("SystemTime") if isinstance(attrs, dict) else None


def _records(outputs):
    result = {}
    for name, body in outputs.items():
        rows = []
        try:
            lines = body.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ContractError("PROBE_SOURCE_JSONL") from exc
        for number, line in enumerate(lines, 1):
            if not line:
                raise ContractError("PROBE_SOURCE_JSONL")
            try:
                row = _strict_json(line)
            except ValueError as exc:
                raise ContractError("PROBE_SOURCE_JSONL") from exc
            if type(row) is not dict:
                raise ContractError("PROBE_SOURCE_JSONL")
            rows.append((number, row))
        result[name] = rows
    return result


def _validate_shapes(shapes):
    if type(shapes) is not dict or set(shapes) != set(SHAPE_SCHEMAS):
        raise ContractError("PROBE_REQUIRED_SHAPES")
    for name, keys in SHAPE_SCHEMAS.items():
        shape = shapes[name]
        if type(shape) is not dict or set(shape) != keys:
            raise ContractError("PROBE_REQUIRED_SHAPES")
        for field, value in shape.items():
            if field == "line":
                if type(value) is not int or value < 1:
                    raise ContractError("PROBE_REQUIRED_SHAPES")
            else:
                _nonempty_string(value, "PROBE_REQUIRED_SHAPES")


def _require_event_string(value):
    if type(value) is not str or not value:
        raise ContractError("PROBE_REQUIRED_SHAPES")
    return value


def _timestamp(value):
    if type(value) is not str:
        raise ContractError("PROBE_REQUIRED_SHAPES")
    for form in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(value, form)
        except ValueError:
            pass
    raise ContractError("PROBE_REQUIRED_SHAPES")


def _validate_selector(selector):
    if type(selector) is not dict or set(selector) != SELECT_KEYS or \
            type(selector.get("count")) is not int or selector["count"] < 1 or \
            type(selector.get("required_event_data_fields")) is not list or \
            not selector["required_event_data_fields"]:
        raise ContractError("PROBE_RANGE_SCHEMA")
    fields = selector["required_event_data_fields"]
    for field in fields:
        _nonempty_string(field, "PROBE_RANGE_SCHEMA")
    if len(set(fields)) != len(fields):
        raise ContractError("PROBE_RANGE_SCHEMA")


def _selected_lines(raw, selector):
    selected = []
    for number, encoded in enumerate(raw.splitlines(keepends=True), 1):
        if not encoded.strip():
            raise ContractError("PROBE_SOURCE_JSONL")
        try:
            row = _strict_json(encoded.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ContractError("PROBE_SOURCE_JSONL") from exc
        if type(row) is not dict:
            raise ContractError("PROBE_SOURCE_JSONL")
        data = _event_data(row)
        if type(data) is dict and all(type(data.get(field)) is str and data[field]
                                      for field in selector["required_event_data_fields"]):
            selected.append((number, encoded))
    if len(selected) < selector["count"]:
        raise ContractError("PROBE_RANGE_SELECTION")
    return selected[:selector["count"]]


def _expectations(outputs, shapes):
    _validate_shapes(shapes)
    records = _records(outputs)
    auth = shapes["authentication"]
    inventory = shapes["inventory"]
    reported = shapes["reported_context"]
    timeline = shapes["timeline"]
    if timeline["time_field"] != "System.TimeCreated":
        raise ContractError("PROBE_REQUIRED_SHAPES")
    try:
        auth_rows = records[auth["file"]]
        inventory_rows = records[inventory["file"]]
        reported_row = records[reported["file"]][reported["line"] - 1][1]
        timeline_left = records[timeline["left_file"]]
        timeline_right = records[timeline["right_file"]]
    except (KeyError, IndexError) as exc:
        raise ContractError("PROBE_REQUIRED_SHAPES") from exc
    ips = sorted({_require_event_string(_event_data(row).get(auth["ip_field"]))
                  for _, row in auth_rows if _event_data(row).get(auth["ip_field"]) not in (None, "", "-")})
    messages = [_require_event_string(_event_data(row).get(auth["message_field"]))
                for _, row in auth_rows if _event_data(row).get(auth["message_field"]) is not None]
    services = sorted({_require_event_string(_event_data(row).get(inventory["service_field"]))
                       for _, row in inventory_rows if _event_data(row).get(inventory["service_field"]) is not None})
    processes = sorted({_require_event_string(_event_data(row).get(inventory["process_field"]))
                        for _, row in inventory_rows if _event_data(row).get(inventory["process_field"]) is not None})
    context = _require_event_string(_event_data(reported_row).get(reported["message_field"]))
    left_times = [_require_event_string(_event_time(row)) for _, row in timeline_left if _event_time(row)]
    right_times = [_require_event_string(_event_time(row)) for _, row in timeline_right if _event_time(row)]
    if not (ips and messages and services and processes and left_times and right_times):
        raise ContractError("PROBE_REQUIRED_SHAPES")
    left_stamped = [(_timestamp(value), value) for value in left_times]
    right_stamped = [(_timestamp(value), value) for value in right_times]
    left_instant, authentication_first = min(left_stamped)
    right_instant, inventory_first = min(right_stamped)
    relation = ("authentication_before_inventory" if left_instant < right_instant else
                "authentication_equal_inventory" if left_instant == right_instant else
                "authentication_after_inventory")
    return {"schema": 1, "authentication": {"external_ips": ips, "messages": messages},
            "inventory": {"services": services, "processes": processes},
            "reported_context": context,
            "timeline": {"authentication_first": authentication_first, "inventory_first": inventory_first,
                         "relation": relation}}


def _tree_hash(outputs):
    hasher = hashlib.sha256()
    for name in sorted(outputs):
        hasher.update(name.encode("utf-8") + b"\0" + _sha256_bytes(outputs[name]).encode("ascii") + b"\n")
    return hasher.hexdigest()


def _verify_source_hashes(source, source_hashes, identities):
    for name, expected_hash in source_hashes.items():
        if _source_identity(source, name, "PROBE_SOURCE_CHANGED") != identities[name]:
            raise ContractError("PROBE_SOURCE_CHANGED")
        if _sha256_bytes(_read_stable_regular_bytes(source, name, "PROBE_SOURCE_CHANGED")) != expected_hash:
            raise ContractError("PROBE_SOURCE_CHANGED")


def _verify_snapshot_identities(source, source_hashes, identities):
    """Last feasible local check; mutation after this boundary needs a producer lock."""
    _verify_source_hashes(source, source_hashes, identities)


def _verify_staging(staging, outputs, expectations_bytes, manifest_bytes, manifest):
    expected = set(outputs) | {"probe-expectations.json", "probe-fixture-manifest.json"}
    actual = set()
    for parent, directories, files in os.walk(staging):
        for directory in directories:
            if stat.S_ISLNK(os.lstat(Path(parent) / directory).st_mode):
                raise ContractError("PROBE_STAGE_HASH")
        for filename in files:
            path = Path(parent) / filename
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ContractError("PROBE_STAGE_HASH")
            actual.add(path.relative_to(staging).as_posix())
    if actual != expected:
        raise ContractError("PROBE_STAGE_HASH")
    for name, body in outputs.items():
        if (staging / name).read_bytes() != body:
            raise ContractError("PROBE_STAGE_HASH")
    if _tree_hash({name: (staging / name).read_bytes() for name in outputs}) != manifest["output_tree_sha256"] or \
            (staging / "probe-expectations.json").read_bytes() != expectations_bytes or \
            (staging / "probe-fixture-manifest.json").read_bytes() != manifest_bytes:
        raise ContractError("PROBE_STAGE_HASH")


def _validate_recipe(recipe):
    if type(recipe) is not dict or set(recipe) != RECIPE_KEYS or type(recipe.get("schema")) is not int or \
            recipe["schema"] != 1 or type(recipe.get("ranges")) is not list or not recipe["ranges"]:
        raise ContractError("PROBE_RECIPE_SCHEMA")
    _nonempty_string(recipe.get("dataset"), "PROBE_RECIPE_SCHEMA")
    _validate_shapes(recipe.get("required_shapes"))


def build_fixture(source, destination, recipe, seed):
    """Copy inclusive ranges into a new, hash-sealed fixture without replacing output."""
    if type(seed) is not int:
        raise ContractError("PROBE_SEED")
    source_input = Path(source)
    destination_input = Path(destination)
    _regular_directory(source_input, "PROBE_SOURCE")
    source_root = source_input.resolve()
    destination_root = destination_input.resolve(strict=False)
    if _contains(destination_root, source_root) or _contains(source_root, destination_root) or \
            _destination_conflicts(source_root, destination_input) or os.path.lexists(destination_input):
        raise ContractError("PROBE_DESTINATION")
    recipe = _read_recipe(recipe)
    _validate_recipe(recipe)
    outputs, range_manifest, source_hashes, ranges, destinations, snapshots, identities = {}, [], {}, [], set(), {}, {}
    for item in recipe["ranges"]:
        if type(item) is not dict or set(item) not in (RANGE_KEYS, SELECT_RANGE_KEYS):
            raise ContractError("PROBE_RANGE_SCHEMA")
        relative_source = _canonical_relative(item["source"], "PROBE_RANGE_PATH")
        relative_destination = _canonical_relative(item["destination"], "PROBE_RANGE_PATH")
        if set(item) == RANGE_KEYS:
            if type(item["start"]) is not int or type(item["end"]) is not int or item["start"] < 1 or \
                    item["end"] < item["start"]:
                raise ContractError("PROBE_RANGE_LINES")
        else:
            _validate_selector(item["select"])
        if relative_destination in destinations:
            raise ContractError("PROBE_RANGE_DESTINATION")
        destinations.add(relative_destination)
        ranges.append((item, relative_source, relative_destination))
    for left in destinations:
        if any(right != left and right.startswith(left + "/") for right in destinations):
            raise ContractError("PROBE_RANGE_DESTINATION")
        if any(left == control or left.startswith(control + "/") or control.startswith(left + "/")
               for control in CONTROL_NAMES):
            raise ContractError("PROBE_RANGE_DESTINATION")
    for item, relative_source, relative_destination in ranges:
        if relative_source not in snapshots:
            before_identity = _source_identity(source_root, relative_source, "PROBE_SOURCE_CHANGED")
            raw = _read_regular_bytes(source_root, relative_source, "PROBE_RANGE_LINES")
            after_identity = _source_identity(source_root, relative_source, "PROBE_SOURCE_CHANGED")
            if before_identity != after_identity:
                raise ContractError("PROBE_SOURCE_CHANGED")
            snapshots[relative_source] = raw
            source_hashes[relative_source] = _sha256_bytes(raw)
            identities[relative_source] = after_identity
        raw = snapshots[relative_source]
        lines = raw.splitlines(keepends=True)
        if set(item) == RANGE_KEYS:
            if item["end"] > len(lines):
                raise ContractError("PROBE_RANGE_LINES")
            selected = list(enumerate(lines[item["start"] - 1:item["end"]], item["start"]))
        else:
            selected = _selected_lines(raw, item["select"])
        body = b"".join(encoded for _, encoded in selected)
        source_lines = [number for number, _ in selected]
        outputs[relative_destination] = body
        row = {"source": relative_source, "source_sha256": source_hashes[relative_source],
               "destination": relative_destination, "output_sha256": _sha256_bytes(body),
               "lines": source_lines}
        if set(item) == RANGE_KEYS:
            row.update({"start": item["start"], "end": item["end"]})
        else:
            row["select"] = item["select"]
        range_manifest.append(row)
    expectations_bytes = _json_bytes(_expectations(outputs, recipe["required_shapes"]))
    manifest = {"schema": 1, "dataset": recipe["dataset"], "seed": seed,
                "recipe_sha256": _sha256_bytes(_json_bytes(recipe)), "source_sha256": source_hashes,
                "outputs": {item["destination"]: {"sha256": item["output_sha256"], "lines": item["lines"]}
                            for item in range_manifest}, "ranges": range_manifest,
                "output_tree_sha256": _tree_hash(outputs), "expectations": "probe-expectations.json",
                "expectations_sha256": _sha256_bytes(expectations_bytes)}
    manifest_bytes = _json_bytes(manifest)
    destination_input.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".probe-", dir=destination_input.parent))
    claimed_destination = False
    try:
        for name, body in outputs.items():
            output = staging / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(body)
        (staging / manifest["expectations"]).write_bytes(expectations_bytes)
        (staging / "probe-fixture-manifest.json").write_bytes(manifest_bytes)
        _verify_staging(staging, outputs, expectations_bytes, manifest_bytes, manifest)
        _verify_source_hashes(source_root, source_hashes, identities)
        _verify_snapshot_identities(source_root, source_hashes, identities)
        try:
            os.mkdir(destination_input)
            claimed_destination = True
        except FileExistsError as exc:
            raise ContractError("PROBE_DESTINATION") from exc
        try:
            os.replace(staging, destination_input)
            claimed_destination = False
        except OSError as exc:
            raise ContractError("PROBE_PUBLISH") from exc
    finally:
        if claimed_destination:
            try:
                os.rmdir(destination_input)
            except OSError:
                pass
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source", required=True)
    build.add_argument("--destination", required=True)
    build.add_argument("--recipe", required=True)
    build.add_argument("--seed", required=True, type=int)
    build.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = build_fixture(args.source, args.destination, args.recipe, args.seed)
    except ContractError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    else:
        print("fixture built: %s" % manifest["output_tree_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
