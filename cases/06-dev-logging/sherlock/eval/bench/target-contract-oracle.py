#!/usr/bin/env python3
"""Independent exact-oracle checks for the sealed Sherlock contract probe."""
import argparse
import json
import re
from pathlib import Path


REQUIRED = {
    "F-AUTH-EXTERNAL": "PROVEN",
    "F-INVENTORY-SERVICE": "PROVEN",
    "F-REPORTED-CONTEXT": "REPORTED",
    "F-TIMELINE-LINK": "INFERENCE",
}
HEADING = re.compile(r"^#{1,6}\s+(F-[A-Z-]+)\s*$", re.MULTILINE)
CITATION = re.compile(r"(?<![\w.-])([^\s`'\"(),;\[\]<>]+?):(\d+)")
VERDICT = re.compile(r"^#{1,6}\s+ВЕРДИКТ\b", re.MULTILINE)


def _load(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("EXPECTATIONS_UNREADABLE") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("EXPECTATIONS_SCHEMA")
    return value


def _sections(text, headings):
    return {match.group(1): text[match.end():headings[index + 1].start()
                                  if index + 1 < len(headings) else len(text)]
            for index, match in enumerate(headings)}


def audit_report(report, corpus, expectations):
    text = Path(report).read_text(encoding="utf-8", errors="replace")
    expected = _load(expectations)
    headings = list(HEADING.finditer(text))
    ids = [match.group(1) for match in headings]
    failures = []
    for finding_id, label in REQUIRED.items():
        if ids.count(finding_id) == 0:
            failures.append("finding_id_missing")
        elif ids.count(finding_id) > 1:
            failures.append("finding_id_duplicate")
    if any(finding_id not in REQUIRED for finding_id in ids):
        failures.append("finding_id_extra")
    sections = _sections(text, headings)
    for finding_id, label in REQUIRED.items():
        if finding_id in sections and not re.search(r"\[!%s\]" % re.escape(label), sections[finding_id]):
            failures.append("finding_label_wrong")

    auth_line = next((line for line in sections.get("F-AUTH-EXTERNAL", "").splitlines()
                      if "external authentication endpoints" in line.lower()), "")
    if "-" in auth_line.lstrip("-•* "):
        failures.append("external_predicate_includes_local_dash")
    for ip in expected["authentication"]["external_ips"]:
        if ip not in text:
            failures.append("authentication_value_missing")
    for value in expected["inventory"]["services"] + expected["inventory"]["processes"]:
        if value not in text:
            failures.append("inventory_value_missing")
    if expected["reported_context"] not in text:
        failures.append("reported_context_missing")
    timeline = expected["timeline"]
    if not (timeline["authentication_first"] < timeline["inventory_first"] and
            "preceded" in sections.get("F-TIMELINE-LINK", "").lower()):
        failures.append("timeline_link_missing")

    citations = [{"path": match.group(1), "line": int(match.group(2))}
                 for match in CITATION.finditer(text)]
    files = sorted({citation["path"] for citation in citations})
    if len(citations) < 6 or len(files) < 2:
        failures.append("citation_coverage_insufficient")
    verdict = VERDICT.search(text)
    if verdict is None or (headings and verdict.start() < headings[-1].start()):
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
