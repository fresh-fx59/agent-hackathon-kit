#!/usr/bin/env python3
"""report-case.py — judge one run, compute the deterministic verdict, append a row."""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure, score_case  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--case", required=True)
ap.add_argument("--run", required=True)
ap.add_argument("--tier", default="1")
ap.add_argument("--results", required=True)
a = ap.parse_args()

case = json.load(open(os.path.join(a.case, "case.json"), encoding="utf-8"))
report = open(os.path.join(a.run, "report.md"), encoding="utf-8").read()
stream = os.path.join(a.run, "stream.jsonl")

judged = score_case.score(case, report)
v = measure.verdict(case, stream, report, judged["found"])
row = {"case_id": v["case_id"], "arm": json.load(
           open(os.path.join(a.run, "meta.json"), encoding="utf-8"))["arm"],
       "tier": a.tier, "diagnosis": v["diagnosis"], "judge_found": v["judge_found"],
       "why": judged["why"], "requires": v["requires"],
       "files_opened": len(v["reach"]["files_opened"]),
       "proofs_reached": len(v["reach"]["reached"]),
       "tool_calls": v["budget"]["tool_calls"], "run_dir": a.run}
with open(a.results, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("  %s %s -> %s" % (row["case_id"], row["arm"], row["diagnosis"]))
