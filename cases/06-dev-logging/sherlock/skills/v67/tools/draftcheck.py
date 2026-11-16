#!/usr/bin/env python3
"""Provider-free structural preflight for a Sherlock draft."""
import argparse, json, re, sys, importlib.util
from pathlib import Path

_CITE = Path(__file__).with_name("citecheck.py")
_SPEC = importlib.util.spec_from_file_location("draftcheck_citecheck", _CITE)
_CC = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(_CC)
HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?([НHКK])\s*[-–—]\s*(\d+)\s*[·•∙]\s*\S")
LOOSE = re.compile(r"^\s*(?:#{1,6}\s*)?([НHКK])\s*[-–—]\s*\d+")
SECTIONS = ("инвентар", "пропущ", "покрыт", "окно запис", "вердикт")
AGG = re.compile(r"(?im)^\s*агрегат\s*:")
OWNERSHIP = re.compile(r"(?im)^\s*[-*|]*\s*(?:владелец|учётная запись|account|owner)\s*[:|]")

def check(text):
    lines = text.splitlines(); errors = []; blocks = []
    structure = _CC.structural_mask(lines)
    for n, line in enumerate(lines, 1):
        if structure[n - 1] and LOOSE.match(line) and not HEAD.match(line):
            errors.append({"line": n, "code": "missing_heading_separator", "text": line.strip()})
        m = HEAD.match(line) if structure[n - 1] else None
        if m: blocks.append((m.group(1).upper(), m.group(2), n))
    for i, (kind, num, start) in enumerate(blocks):
        end = blocks[i + 1][2] if i + 1 < len(blocks) else len(lines) + 1
        body = "\n".join(lines[start:end - 1])
        if kind in ("Н", "H"):
            for field in ("исход:", "атрибуция:"):
                count = len(re.findall(r"(?im)^\s*[-*]?\s*" + re.escape(field), body))
                if count != 1:
                    errors.append({"line": start, "code": "field_count", "field": field, "count": count})
    # Sections must be real markdown headings. A mention in prose cannot satisfy
    # the report contract.
    section_lines = [(n, re.sub(r"[#*_`]", "", line).strip().casefold())
                     for n, line in enumerate(lines, 1) if re.match(r"^\s*#{1,6}\s+", line)]
    lowered = text.casefold()
    for section in SECTIONS:
        if not any(section in title for _, title in section_lines):
            errors.append({"line": 0, "code": "missing_section", "section": section})
    # Let citecheck locate the real section and parse its six-column rows.  A
    # prose mention or a table elsewhere must not accidentally satisfy this.
    sections = _CC._sections(text, structure)
    own_spans = _CC._spans_for(sections, _CC.OWNERSHIP_SECTION_RE)
    for row in _CC._own_rows(text, own_spans, structure):
        n = row["report_line"]
        if row.get("problem"):
            errors.append({"line": n, "code": "invalid_ownership", "text": row.get("raw", "")})
            continue
        if not re.fullmatch(r"[^\\|\s]+\\[^\\|\s]+", row.get("account_raw", "")):
            errors.append({"line": n, "code": "invalid_ownership", "text": lines[n - 1].strip()})
        if not re.search(r"(?:[^\s|]+\.(?:log|jsonl|evtx)):\d+", row.get("evidence", ""), re.I):
            errors.append({"line": n, "code": "unwitnessed_ownership", "text": lines[n - 1].strip()})
    for n, line in enumerate(lines, 1):
        if AGG.match(line):
            body = line.split(":", 1)[1]
            try:
                parsed = _CC.agg_parse_line(body)
            except Exception:
                parsed = None
            if not parsed or "\\" in body or "\\\"" in body:
                errors.append({"line": n, "code": "invalid_aggregate_wrapper", "text": line.strip()})
    rollover_spans = _CC._spans_for(sections, _CC.ROLLOVER_SECTION_RE)
    if rollover_spans:
        rows, malformed = _CC.rollover_rows(lines, rollover_spans, structure)
        if malformed:
            errors.extend({"line": n, "code": "invalid_rollover_row"} for n in malformed)
        if not rows:
            errors.append({"line": 0, "code": "missing_rollover_rows"})
        seen = set()
        for row in rows:
            key = (row["path"].strip(), row["channel"].strip())
            if key in seen:
                errors.append({"line": row["report_line"], "code": "excess_rollover_row", "row": list(key)})
            seen.add(key)
    state_groups = ("rare", "new", "peak", "volume", "sequence")
    present_states = {s for s in state_groups if re.search(r"(?im)^\s*[-*|].*\b" + s + r"\b", text)}
    for state in state_groups:
        if state not in present_states:
            errors.append({"line": 0, "code": "missing_state_group", "state": state})
    return {"ok": not errors, "errors": errors, "counts": {
        "findings": sum(k in ("Н", "H") for k, _, _ in blocks),
        "candidates": sum(k in ("К", "K") for k, _, _ in blocks),
        "malformed_headings": sum(e["code"] == "missing_heading_separator" for e in errors)}}

def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("report"); p.add_argument("--json", action="store_true")
    a = p.parse_args(argv); result = check(Path(a.report).read_text(encoding="utf-8", errors="replace"))
    print(json.dumps(result, ensure_ascii=False, indent=2) if a.json else ("OK" if result["ok"] else "✗ " + str(result["errors"])))
    return 0 if result["ok"] else 1
if __name__ == "__main__": sys.exit(main())
