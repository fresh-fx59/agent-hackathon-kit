#!/usr/bin/env python3
"""Independent exact-oracle checks for the sealed Sherlock contract probe."""
import argparse
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath


REQUIRED = {
    "F-AUTH-EXTERNAL": ("PROVEN", ("external_ips", "citation_files")),
    "F-INVENTORY-SERVICE": ("PROVEN", ("services", "processes", "citation_files")),
    "F-REPORTED-CONTEXT": ("REPORTED", ("reported_context", "citation_files")),
    "F-TIMELINE-LINK": ("INFERENCE", ("timeline", "citation_files")),
}
TIMELINE_RELATION = "authentication_before_inventory"
HEADING = re.compile(r"^( {0,3})(#{1,6})[ \t]+(.*?)[ \t]*$", re.MULTILINE)
SETEXT_HEADING = re.compile(
    r"^( {0,3})(?=\S)(.*?\S)[ \t]*\r?\n {0,3}(=+|-+)[ \t]*(?:\r?\n|$)", re.MULTILINE)
STRICT_HEADING = re.compile(r"^## (F-[A-Z0-9]+(?:-[A-Z0-9]+)*)$")
FIELD_LINE = re.compile(r"^(?:- \[!([A-Z]+)\] )?([A-Za-z_][A-Za-z0-9_]*)=(.+)$", re.MULTILINE)
LABEL = re.compile(r"\[!([A-Z]+)\]")
CITATION = re.compile(r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9_/-]*\.[A-Za-z0-9._-]+):(\d+)")


def _no_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: %s" % key)
        value[key] = item
    return value


def _strict_json(text):
    return json.loads(text, object_pairs_hook=_no_duplicate_keys)


def _load(path):
    try:
        value = _strict_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("EXPECTATIONS_UNREADABLE") from exc
    if type(value) is not dict or type(value.get("schema")) is not int or value["schema"] != 1:
        raise ValueError("EXPECTATIONS_SCHEMA")
    return value


def _headings(text):
    headings = [(match.group(0), len(match.group(2)), match.group(3), match.start(), match.end())
                for match in HEADING.finditer(text)]
    for match in SETEXT_HEADING.finditer(text):
        if HEADING.fullmatch(match.group(1) + match.group(2)):
            continue
        headings.append((match.group(1) + match.group(2),
                         1 if match.group(3)[0] == "=" else 2,
                         match.group(2), match.start(), match.end()))
    return sorted(headings, key=lambda heading: heading[3])


def _sections(text, headings):
    findings = []
    for index, heading in enumerate(headings):
        candidate = re.match(r"(F-\S+)", heading[2])
        if candidate:
            findings.append((candidate.group(1), heading[0], heading[3],
                             text[heading[4]:headings[index + 1][3]
                                  if index + 1 < len(headings) else len(text)]))
    return findings


def _fence_open(line):
    marker = re.match(r"^ {0,3}([`~]{3,})(.*)$", line.rstrip("\r\n"))
    if marker is None or len(set(marker.group(1))) != 1:
        return None
    if marker.group(1)[0] == "`" and "`" in marker.group(2):
        return None
    return marker.group(1)[0], len(marker.group(1))


def _fence_close(line, opening):
    marker = re.fullmatch(r" {0,3}([`~]+)[ \t]*", line.rstrip("\r\n"))
    return marker is not None and len(set(marker.group(1))) == 1 and \
        marker.group(1)[0] == opening[0] and len(marker.group(1)) >= opening[1]


def _visible_markdown(text):
    """Preserve line positions while excluding fenced code from the tiny grammar."""
    visible, opening = [], None
    for line in text.splitlines(keepends=True):
        if opening is None:
            opening = _fence_open(line)
            if opening is None:
                visible.append(line)
                continue
            visible.append("\n" if line.endswith("\n") else "")
        elif _fence_close(line, opening):
            opening = None
            visible.append("\n" if line.endswith("\n") else "")
        else:
            visible.append("\n" if line.endswith("\n") else "")
    return "".join(visible)


def _expected_fields(expected):
    timeline = dict(expected["timeline"])
    return {
        "F-AUTH-EXTERNAL": {"external_ips": expected["authentication"]["external_ips"],
                            "citation_files": ["Security.jsonl"]},
        "F-INVENTORY-SERVICE": {"services": expected["inventory"]["services"],
                                "processes": expected["inventory"]["processes"],
                                "citation_files": ["System.jsonl"]},
        "F-REPORTED-CONTEXT": {"reported_context": expected["reported_context"],
                               "citation_files": ["Security.jsonl"]},
        "F-TIMELINE-LINK": {"timeline": timeline,
                            "citation_files": ["Security.jsonl", "System.jsonl"]},
    }


def _timeline_valid(timeline):
    try:
        left = datetime.strptime(timeline["authentication_first"], "%Y-%m-%dT%H:%M:%SZ")
        right = datetime.strptime(timeline["inventory_first"], "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, TypeError, ValueError):
        return False
    relation = ("authentication_before_inventory" if left < right else
                "authentication_equal_inventory" if left == right else
                "authentication_after_inventory")
    return relation == TIMELINE_RELATION and timeline.get("relation") == TIMELINE_RELATION


def _field_values(section):
    values = {}
    duplicates = set()
    labels = LABEL.findall(section)
    for match in FIELD_LINE.finditer(section):
        label, name, encoded = match.groups()
        if name in values:
            duplicates.add(name)
            continue
        try:
            values[name] = (label, _strict_json(encoded))
        except ValueError:
            values[name] = (label, object())
    return values, duplicates, labels


def _canonical_path(path):
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts or \
            any(part in {"", "."} for part in candidate.parts):
        return None
    return "/".join(candidate.parts)


def _regular_corpus_path(corpus, value):
    canonical = _canonical_path(value)
    if canonical is None or canonical != value:
        return False
    path = Path(corpus)
    for index, component in enumerate(canonical.split("/")):
        path = path / component
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return False
        if stat.S_ISLNK(mode) or (index < len(canonical.split("/")) - 1 and not stat.S_ISDIR(mode)):
            return False
    return stat.S_ISREG(os.lstat(path).st_mode)


def audit_report(report, corpus, expectations):
    text = _visible_markdown(Path(report).read_text(encoding="utf-8", errors="replace"))
    expected = _load(expectations)
    failures = []
    if type(expected.get("timeline")) is not dict or not _timeline_valid(expected["timeline"]):
        failures.append("timeline_expectation_invalid")
    headings = _headings(text)
    findings = _sections(text, headings)
    ids = [finding[0] for finding in findings]
    if sorted(ids) != sorted(REQUIRED):
        if any(finding_id not in REQUIRED for finding_id in ids):
            failures.append("finding_id_extra")
        if any(ids.count(finding_id) == 0 for finding_id in REQUIRED):
            failures.append("finding_id_missing")
        if any(ids.count(finding_id) > 1 for finding_id in REQUIRED):
            failures.append("finding_id_duplicate")
    sections = {finding_id: section for finding_id, _line, _start, section in findings
                if finding_id in REQUIRED and ids.count(finding_id) == 1}
    for finding_id, line, _start, _section in findings:
        if not STRICT_HEADING.fullmatch(line):
            failures.append("finding_heading_invalid")
    fields = _expected_fields(expected)
    for finding_id, (required_label, required_fields) in REQUIRED.items():
        section = sections.get(finding_id)
        if section is None:
            continue
        values, duplicates, labels = _field_values(section)
        if labels != [required_label]:
            failures.append("finding_label_wrong")
        if set(values) != set(required_fields) or duplicates:
            failures.append("finding_fields_invalid")
            continue
        for field in required_fields:
            label, value = values[field]
            if field == required_fields[0] and label != required_label:
                failures.append("finding_label_wrong")
            if field != required_fields[0] and label is not None:
                failures.append("finding_label_wrong")
            if finding_id == "F-AUTH-EXTERNAL" and field == "external_ips" and \
                    type(value) is list and "-" in value:
                failures.append("external_predicate_includes_local_dash")
            elif value != fields[finding_id][field]:
                failures.append("finding_value_wrong")
        section_citations = [match.group(1) for match in CITATION.finditer(section)]
        if sorted(set(section_citations)) != sorted(values["citation_files"][1]):
            failures.append("citation_files_wrong")

    citations = [{"path": match.group(1), "line": int(match.group(2))}
                 for match in CITATION.finditer(text)]
    files = sorted({citation["path"] for citation in citations})
    if len(citations) < 6 or len(files) < 2:
        failures.append("citation_coverage_insufficient")
    if any(not _regular_corpus_path(corpus, citation["path"]) for citation in citations):
        failures.append("citation_corpus_path_invalid")
    verdicts = [heading for heading in headings if heading[0] == "## ВЕРДИКТ"]
    if len(verdicts) != 1 or not headings or headings[-1] != verdicts[0] or \
            (findings and verdicts[0][3] <= max(item[2] for item in findings)):
        failures.append("verdict_not_after_findings")
    return {"schema": 1, "accepted": not failures, "failures": sorted(set(failures)),
            "finding_ids": ids, "citations": citations, "files": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--report", required=True)
    audit.add_argument("--corpus", required=True)
    audit.add_argument("--expectations", required=True)
    audit.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = audit_report(args.report, args.corpus, args.expectations)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("accepted" if result["accepted"] else ", ".join(result["failures"]))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
