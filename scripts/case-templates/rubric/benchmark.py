#!/usr/bin/env python3
"""Score a markdown artifact against rubric.json (rubric-mode case).

Usage:
    python3 benchmark.py path/to/artifact.md            # score an agent artifact
    python3 benchmark.py path/to/artifact.md --score-only  # just the number (CI gate)
    python3 benchmark.py --self-test    # score expected-output.md, require >= 95

Scoring (0-100):
    - required sections (30%): each rubric section name must appear in some
      markdown heading of the artifact (case-insensitive substring);
    - required facts (70%): a fact counts as covered when ANY of its keywords
      occurs in the document text (case-insensitive substring); facts are
      weighted by their rubric weight.

Stdlib only, Python >= 3.9.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUBRIC_PATH = os.path.join(HERE, "rubric.json")
EXPECTED_PATH = os.path.join(HERE, "expected-output.md")

SECTION_SHARE = 30.0
FACT_SHARE = 70.0
SELF_TEST_THRESHOLD = 95.0

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")


def load_rubric(path=RUBRIC_PATH):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def find_headings(text):
    """All markdown heading texts, lowercased."""
    headings = []
    for line in text.splitlines():
        hit = HEADING_RE.match(line)
        if hit:
            headings.append(hit.group(1).strip().lower())
    return headings


def score(text, rubric):
    """Return a result dict: per-section and per-fact hits + total 0-100."""
    headings = find_headings(text)
    lowered = text.lower()

    sections = []
    for name in rubric.get("required_sections", []):
        present = any(name.lower() in h for h in headings)
        sections.append({"name": name, "present": present})
    n_sections = len(sections)
    n_found = sum(1 for s in sections if s["present"])
    section_frac = (float(n_found) / n_sections) if n_sections else 1.0

    facts = []
    total_weight = 0.0
    covered_weight = 0.0
    for fact in rubric.get("required_facts", []):
        weight = float(fact.get("weight", 1.0))
        total_weight += weight
        matched = None
        for keyword in fact.get("keywords", []):
            if keyword.lower() in lowered:
                matched = keyword
                break
        if matched is not None:
            covered_weight += weight
        facts.append({"id": fact.get("id", "?"),
                      "title": fact.get("title", ""),
                      "keywords": fact.get("keywords", []),
                      "weight": weight,
                      "matched": matched})
    fact_frac = (covered_weight / total_weight) if total_weight else 1.0

    total = SECTION_SHARE * section_frac + FACT_SHARE * fact_frac
    return {"sections": sections, "n_sections_found": n_found,
            "facts": facts, "covered_weight": covered_weight,
            "total_weight": total_weight, "total": round(total, 1)}


def print_report(doc_path, result):
    print("Artifact file: %s" % doc_path)
    print()
    print("Sections (%d/%d found, %.0f%% of the score):"
          % (result["n_sections_found"], len(result["sections"]), SECTION_SHARE))
    for section in result["sections"]:
        mark = "ok  " if section["present"] else "MISS"
        print("  [%s] %s" % (mark, section["name"]))
    print()
    print("Facts (weight %.1f/%.1f covered, %.0f%% of the score):"
          % (result["covered_weight"], result["total_weight"], FACT_SHARE))
    for fact in result["facts"]:
        if fact["matched"] is not None:
            print("  [ok  ] %-13s w=%.1f  matched: %r"
                  % (fact["id"], fact["weight"], fact["matched"]))
        else:
            print("  [MISS] %-13s w=%.1f  %s"
                  % (fact["id"], fact["weight"], fact["title"]))
    missing = [f for f in result["facts"] if f["matched"] is None]
    if missing:
        print()
        print("Missing facts (add these to the artifact):")
        for fact in missing:
            print("  - %s: %s  (any of keywords: %s)"
                  % (fact["id"], fact["title"], ", ".join(fact["keywords"])))
    print()
    print("TOTAL SCORE: %.1f / 100" % result["total"])


def main(argv):
    args = list(argv[1:])
    score_only = "--score-only" in args
    args = [a for a in args if a != "--score-only"]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__)
        return 2

    self_test = args[0] == "--self-test"
    doc_path = EXPECTED_PATH if self_test else args[0]
    if not os.path.exists(doc_path):
        print("error: artifact file not found: %s" % doc_path)
        return 2

    rubric = load_rubric()
    result = score(read_text(doc_path), rubric)
    if score_only:
        # Machine-readable: exactly one number on stdout (for CI gates).
        print("%.1f" % result["total"])
    else:
        print_report(doc_path, result)

    if self_test:
        if result["total"] >= SELF_TEST_THRESHOLD:
            print("SELF-TEST PASS (>= %.0f)" % SELF_TEST_THRESHOLD)
            return 0
        print("SELF-TEST FAIL: expected-output.md scored %.1f, need >= %.0f"
              % (result["total"], SELF_TEST_THRESHOLD))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
