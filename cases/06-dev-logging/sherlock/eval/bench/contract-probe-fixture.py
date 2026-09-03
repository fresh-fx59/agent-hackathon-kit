#!/usr/bin/env python3
"""Build a sealed, deterministic corpus for the Sherlock contract probe."""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


RECIPE_KEYS = {"schema", "dataset", "ranges", "required_shapes"}
RANGE_KEYS = {"source", "start", "end", "destination"}
SHAPE_KEYS = {"authentication", "inventory", "reported_context", "timeline"}


class ContractError(Exception):
    pass


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _read_recipe(recipe):
    if isinstance(recipe, (str, Path)):
        try:
            return json.loads(Path(recipe).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError("PROBE_RECIPE_READ") from exc
    return recipe


def _within(child, parent):
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


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
        for number, line in enumerate(body.decode("utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ContractError("PROBE_SOURCE_JSONL") from exc
            if not isinstance(row, dict):
                raise ContractError("PROBE_SOURCE_JSONL")
            rows.append((number, row))
        result[name] = rows
    return result


def _expectations(outputs, shapes):
    records = _records(outputs)
    auth = shapes["authentication"]
    inventory = shapes["inventory"]
    reported = shapes["reported_context"]
    timeline = shapes["timeline"]
    for field, value in (("authentication", auth), ("inventory", inventory),
                         ("reported_context", reported), ("timeline", timeline)):
        if not isinstance(value, dict):
            raise ContractError("PROBE_REQUIRED_SHAPES")
    try:
        auth_rows = records[auth["file"]]
        inventory_rows = records[inventory["file"]]
        reported_row = records[reported["file"]][int(reported["line"]) - 1][1]
        timeline_left = records[timeline["left_file"]]
        timeline_right = records[timeline["right_file"]]
        ip_field = auth["ip_field"]
        message_field = auth["message_field"]
        service_field = inventory["service_field"]
        process_field = inventory["process_field"]
        reported_field = reported["message_field"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ContractError("PROBE_REQUIRED_SHAPES") from exc
    ips = sorted({str(_event_data(row).get(ip_field)) for _, row in auth_rows
                  if _event_data(row).get(ip_field) not in (None, "", "-")})
    messages = [str(_event_data(row).get(message_field)) for _, row in auth_rows
                if _event_data(row).get(message_field) is not None]
    services = sorted({str(_event_data(row).get(service_field)) for _, row in inventory_rows
                       if _event_data(row).get(service_field) is not None})
    processes = sorted({str(_event_data(row).get(process_field)) for _, row in inventory_rows
                        if _event_data(row).get(process_field) is not None})
    context = _event_data(reported_row).get(reported_field)
    left_times = [time for _, row in timeline_left if (time := _event_time(row))]
    right_times = [time for _, row in timeline_right if (time := _event_time(row))]
    if not (ips and messages and services and processes and context and left_times and right_times):
        raise ContractError("PROBE_REQUIRED_SHAPES")
    return {
        "schema": 1,
        "authentication": {"external_ips": ips, "messages": messages},
        "inventory": {"services": services, "processes": processes},
        "reported_context": str(context),
        "timeline": {"authentication_first": min(left_times),
                     "inventory_first": min(right_times)},
    }


def build_fixture(source, destination, recipe, seed):
    """Copy the recipe's inclusive ranges and publish a hash-sealed fixture."""
    source = Path(source)
    destination = Path(destination)
    recipe = _read_recipe(recipe)
    if not isinstance(recipe, dict) or set(recipe) != RECIPE_KEYS or recipe.get("schema") != 1:
        raise ContractError("PROBE_RECIPE_SCHEMA")
    if not isinstance(recipe["dataset"], str) or not isinstance(recipe["ranges"], list):
        raise ContractError("PROBE_RECIPE_SCHEMA")
    if set(recipe["required_shapes"]) != SHAPE_KEYS:
        raise ContractError("PROBE_REQUIRED_SHAPES")
    if not source.is_dir() or _within(destination, source):
        raise ContractError("PROBE_DESTINATION")

    outputs, range_manifest, source_hashes = {}, [], {}
    seen_destinations = set()
    for item in recipe["ranges"]:
        if not isinstance(item, dict) or set(item) != RANGE_KEYS:
            raise ContractError("PROBE_RANGE_SCHEMA")
        relative_source = Path(item["source"])
        relative_destination = Path(item["destination"])
        if (relative_source.is_absolute() or relative_destination.is_absolute() or
                ".." in relative_source.parts or ".." in relative_destination.parts):
            raise ContractError("PROBE_RANGE_PATH")
        if item["destination"] in seen_destinations:
            raise ContractError("PROBE_RANGE_DESTINATION")
        seen_destinations.add(item["destination"])
        try:
            start, end = int(item["start"]), int(item["end"])
        except (TypeError, ValueError) as exc:
            raise ContractError("PROBE_RANGE_LINES") from exc
        path = source / relative_source
        if start < 1 or end < start or not path.is_file() or not _within(path, source):
            raise ContractError("PROBE_RANGE_LINES")
        raw = path.read_bytes()
        lines = raw.splitlines(keepends=True)
        if end > len(lines):
            raise ContractError("PROBE_RANGE_LINES")
        body = b"".join(lines[start - 1:end])
        destination_name = relative_destination.as_posix()
        outputs[destination_name] = body
        source_name = relative_source.as_posix()
        source_hashes[source_name] = _sha256_bytes(raw)
        range_manifest.append({"source": source_name, "source_sha256": source_hashes[source_name],
                               "start": start, "end": end, "destination": destination_name,
                               "output_sha256": _sha256_bytes(body),
                               "lines": list(range(start, end + 1))})

    expectations = _expectations(outputs, recipe["required_shapes"])
    expectations_bytes = _json_bytes(expectations)
    tree_hasher = hashlib.sha256()
    for name in sorted(outputs):
        tree_hasher.update(name.encode("utf-8") + b"\0" + _sha256_bytes(outputs[name]).encode("ascii") + b"\n")
    manifest = {
        "schema": 1, "dataset": recipe["dataset"], "seed": int(seed),
        "recipe_sha256": _sha256_bytes(_json_bytes(recipe)), "source_sha256": source_hashes,
        "outputs": {item["destination"]: {"sha256": item["output_sha256"], "lines": item["lines"]}
                    for item in range_manifest},
        "ranges": range_manifest, "output_tree_sha256": tree_hasher.hexdigest(),
        "expectations": "probe-expectations.json",
        "expectations_sha256": _sha256_bytes(expectations_bytes),
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".probe-", dir=destination.parent))
    try:
        for name, body in outputs.items():
            output = staging / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(body)
        (staging / manifest["expectations"]).write_bytes(expectations_bytes)
        (staging / "probe-fixture-manifest.json").write_bytes(_json_bytes(manifest))
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staging, destination)
    finally:
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
