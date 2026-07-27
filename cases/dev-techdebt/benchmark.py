#!/usr/bin/env python3
"""Score a findings.json against expected-findings.json.

    python3 cases/dev-techdebt/benchmark.py path/to/findings.json
    python3 cases/dev-techdebt/benchmark.py --self-test

A reported finding matches an expected one when BOTH:
  * same file      -- compared by basename, so "db.py", "src/db.py" and
                      "cases/dev-techdebt/src/db.py" are all the same file;
  * same category  -- security|bug|smell|duplication|dead-code|style;
  * line within +/-5 of the expected line.

Each expected finding matches at most one reported finding (closest line
wins).  Output: precision / recall / F1, a per-category table, and the
missed / extra lists.  --self-test scores the expected file against itself
and fails unless F1 == 1.0.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_PATH = os.path.join(HERE, "expected-findings.json")

LINE_TOLERANCE = 5
CATEGORIES = ("security", "bug", "smell", "duplication", "dead-code", "style")


def load_findings(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit("%s: expected a JSON array of findings" % path)
    findings = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise SystemExit("%s: finding #%d is not an object" % (path, i))
        for field in ("file", "line", "category"):
            if field not in item:
                raise SystemExit("%s: finding #%d lacks %r" % (path, i, field))
        findings.append(item)
    return findings


def file_key(path):
    """Normalize a file reference to its basename for tolerant matching."""
    return os.path.basename(str(path).replace("\\", "/")).lower()


def match(expected, actual):
    """Greedy one-to-one matching; returns (pairs, missed, extra)."""
    unmatched_actual = list(range(len(actual)))
    pairs = []   # (expected_index, actual_index)
    missed = []  # expected indices with no match
    for ei, exp in enumerate(expected):
        best = None   # (line_distance, actual_index)
        for ai in unmatched_actual:
            act = actual[ai]
            if file_key(act["file"]) != file_key(exp["file"]):
                continue
            if str(act["category"]).lower() != str(exp["category"]).lower():
                continue
            try:
                distance = abs(int(act["line"]) - int(exp["line"]))
            except (TypeError, ValueError):
                continue
            if distance > LINE_TOLERANCE:
                continue
            if best is None or distance < best[0]:
                best = (distance, ai)
        if best is None:
            missed.append(ei)
        else:
            pairs.append((ei, best[1]))
            unmatched_actual.remove(best[1])
    return pairs, missed, unmatched_actual


def describe(finding):
    return "%s:%s [%s/%s] %s" % (
        finding.get("file"), finding.get("line"), finding.get("category"),
        finding.get("severity", "?"),
        str(finding.get("description", ""))[:80])


def score(expected, actual, verbose=True):
    pairs, missed, extra = match(expected, actual)
    tp = len(pairs)
    precision = tp / len(actual) if actual else 0.0
    recall = tp / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)

    if verbose:
        print("Expected findings : %d" % len(expected))
        print("Reported findings : %d" % len(actual))
        print("Matched (TP)      : %d" % tp)
        print()
        print("precision = %.2f   recall = %.2f   F1 = %.2f"
              % (precision, recall, f1))
        print()

        # Per-category table over every category seen on either side.
        seen = [c for c in CATEGORIES
                if any(str(f["category"]).lower() == c
                       for f in expected + actual)]
        matched_by_cat = {}
        for ei, _ai in pairs:
            cat = str(expected[ei]["category"]).lower()
            matched_by_cat[cat] = matched_by_cat.get(cat, 0) + 1
        print("%-12s %9s %8s %7s %6s" % ("category", "expected", "matched",
                                         "missed", "extra"))
        print("%-12s %9s %8s %7s %6s" % ("-" * 10, "-" * 8, "-" * 7,
                                         "-" * 6, "-" * 5))
        for cat in seen:
            n_expected = sum(1 for f in expected
                             if str(f["category"]).lower() == cat)
            n_matched = matched_by_cat.get(cat, 0)
            n_extra = sum(1 for ai in extra
                          if str(actual[ai]["category"]).lower() == cat)
            print("%-12s %9d %8d %7d %6d" % (cat, n_expected, n_matched,
                                             n_expected - n_matched, n_extra))
        print()

        if missed:
            print("MISSED (expected but not reported):")
            for ei in missed:
                print("  - " + describe(expected[ei]))
        if extra:
            print("EXTRA (reported but not expected -- check manually, "
                  "they may still be real):")
            for ai in extra:
                print("  - " + describe(actual[ai]))
        if not missed and not extra:
            print("Perfect match: nothing missed, nothing extra.")

    return {"precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "missed": len(missed), "extra": len(extra)}


def main():
    parser = argparse.ArgumentParser(
        description="Score findings.json against expected-findings.json")
    parser.add_argument("findings", nargs="?",
                        help="path to the findings.json produced by the agent")
    parser.add_argument("--expected", default=EXPECTED_PATH,
                        help="override the expected-findings.json path")
    parser.add_argument("--self-test", action="store_true",
                        help="score the expected file against itself "
                             "(must give F1 = 1.0)")
    parser.add_argument("--score-only", action="store_true",
                        help="print just the F1 number on stdout (CI gate)")
    args = parser.parse_args()

    expected = load_findings(args.expected)

    if args.self_test:
        result = score(expected, load_findings(args.expected), verbose=False)
        ok = (abs(result["f1"] - 1.0) < 1e-9 and result["missed"] == 0
              and result["extra"] == 0)
        print("self-test: expected vs itself -> F1 = %.2f, missed = %d, "
              "extra = %d : %s" % (result["f1"], result["missed"],
                                   result["extra"], "PASS" if ok else "FAIL"))
        return 0 if ok else 1

    if not args.findings:
        parser.error("give a findings.json path (or --self-test)")
    result = score(expected, load_findings(args.findings),
                   verbose=not args.score_only)
    if args.score_only:
        print("%.2f" % result["f1"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
