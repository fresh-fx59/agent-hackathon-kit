#!/usr/bin/env python3
"""Score a regression selection.json against expected-selection.json.

    python3 cases/testing-regression/benchmark.py selection.json
    python3 cases/testing-regression/benchmark.py --self-test

selection.json shape (produced by the agent, see tracks/testing/skill.md):
    {"selected": [{"id": "TC-3", "reason": "..."}], "strategy": "..."}

Metrics:
  * recall     = |selected ∩ must_run| / |must_run|   (target: 1.0)
  * efficiency = 1 - |selected| / |all testcases|     (higher = fewer tests)
  * estimated run time saved vs running the whole map (avg_duration_min).

A missed must_run case is a missed-defect proxy; a missed *P1* case is
flagged loudly.  Exit code: 0 when every must_run case was selected, 1
otherwise (so the score can gate CI).

--self-test replays expected-selection.json's must_run list as the
selection and requires recall == 1.0.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_PATH = os.path.join(HERE, "expected-selection.json")
MAP_PATH = os.path.join(HERE, "testcase-map.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_selection(path):
    """Return (raw_data, ordered unique selected ids)."""
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("selected"), list):
        raise SystemExit('%s: expected {"selected": [{"id", "reason"}, ...], '
                         '"strategy": "..."}' % path)
    ids = []
    for i, item in enumerate(data["selected"]):
        if isinstance(item, dict) and "id" in item:
            case_id = str(item["id"])
        elif isinstance(item, str):  # tolerate a plain list of ids
            case_id = item
        else:
            raise SystemExit('%s: selected[%d] must be {"id": ..., '
                             '"reason": ...}' % (path, i))
        if case_id not in ids:
            ids.append(case_id)
    return data, ids


def score(selected_ids, expected, cases, verbose=True):
    """Compute and (optionally) print the score; returns a metrics dict."""
    by_id = {c["id"]: c for c in cases}
    must = list(expected.get("must_run", []))
    nice = set(expected.get("nice_to_run", []))
    unknown = [i for i in selected_ids if i not in by_id]
    selected = set(i for i in selected_ids if i in by_id)

    missed = [i for i in must if i not in selected]
    missed_p1 = [i for i in missed
                 if by_id.get(i, {}).get("priority") == "P1"]
    recall = ((len(must) - len(missed)) / len(must)) if must else 1.0
    total = len(cases)
    efficiency = (1.0 - len(selected) / total) if total else 0.0

    total_min = sum(c.get("avg_duration_min", 0) for c in cases)
    selected_min = sum(by_id[i].get("avg_duration_min", 0) for i in selected)

    if verbose:
        print("Testcases in map  : %d (full run ~%d min)" % (total, total_min))
        print("Selected          : %d (~%d min)" % (len(selected), selected_min))
        print("Expected must_run : %d" % len(must))
        print()
        print("%-6s %-4s %-14s %-8s %-9s %s"
              % ("id", "prio", "area", "expected", "selected", "verdict"))
        print("%-6s %-4s %-14s %-8s %-9s %s"
              % ("-" * 5, "-" * 4, "-" * 12, "-" * 8, "-" * 8, "-" * 7))
        for case in cases:
            case_id = case["id"]
            in_must = case_id in must
            in_nice = case_id in nice
            is_sel = case_id in selected
            expected_mark = "MUST" if in_must else ("nice" if in_nice else "-")
            if in_must and is_sel:
                verdict = "ok"
            elif in_must:
                verdict = ("!! MISSED P1" if case.get("priority") == "P1"
                           else "MISSED")
            elif is_sel and in_nice:
                verdict = "ok (nice)"
            elif is_sel:
                verdict = "extra"
            else:
                verdict = ""
            print("%-6s %-4s %-14s %-8s %-9s %s"
                  % (case_id, case.get("priority", "?"), case.get("area", "?"),
                     expected_mark, "yes" if is_sel else "no", verdict))
        print()
        print("recall (must_run)  = %.2f   (target 1.00)" % recall)
        print("efficiency         = %.2f   (1 - selected/total)" % efficiency)
        print("time: ~%d of ~%d min (saved ~%d min)"
              % (selected_min, total_min, total_min - selected_min))
        if missed_p1:
            print()
            print("!" * 66)
            print("!!! MISSED P1 CASES -- these are your likely escaped defects:")
            for case_id in missed_p1:
                print("!!!   %s  %s" % (case_id,
                                        by_id.get(case_id, {}).get("title", "")))
            print("!" * 66)
        elif missed:
            print()
            print("Missed (must_run, not selected): %s" % ", ".join(missed))
        if unknown:
            print()
            print("WARNING: selected ids not present in testcase-map.json: %s"
                  % ", ".join(unknown))

    return {"recall": recall, "efficiency": efficiency,
            "missed": missed, "missed_p1": missed_p1, "unknown": unknown,
            "selected_count": len(selected), "total": total}


def main():
    parser = argparse.ArgumentParser(
        description="Score selection.json against expected-selection.json")
    parser.add_argument("selection", nargs="?",
                        help="path to the selection.json produced by the agent")
    parser.add_argument("--expected", default=EXPECTED_PATH,
                        help="override the expected-selection.json path")
    parser.add_argument("--map", dest="case_map", default=MAP_PATH,
                        help="override the testcase-map.json path")
    parser.add_argument("--self-test", action="store_true",
                        help="replay expected must_run as the selection "
                             "(must give recall = 1.0)")
    parser.add_argument("--score-only", action="store_true",
                        help="print just the recall number on stdout (CI gate)")
    args = parser.parse_args()

    expected = load_json(args.expected)
    cases = load_json(args.case_map)

    if args.self_test:
        selected_ids = list(expected.get("must_run", []))
        result = score(selected_ids, expected, cases, verbose=False)
        ok = (abs(result["recall"] - 1.0) < 1e-9 and not result["missed"]
              and not result["unknown"])
        print("self-test: must_run replay -> recall = %.2f, efficiency = %.2f,"
              " unknown = %d : %s"
              % (result["recall"], result["efficiency"],
                 len(result["unknown"]), "PASS" if ok else "FAIL"))
        return 0 if ok else 1

    if not args.selection:
        parser.error("give a selection.json path (or --self-test)")
    result = score(load_selection(args.selection)[1], expected, cases,
                   verbose=not args.score_only)
    if args.score_only:
        print("%.2f" % result["recall"])
    return 0 if not result["missed"] else 1


if __name__ == "__main__":
    sys.exit(main())
