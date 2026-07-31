#!/usr/bin/env python3
"""report-case.py — judge one run, compute the deterministic verdict, append a row.

This is the ONLY thing that writes the measurement artifact, so every column it
emits is a number someone will later quote.

Judge economy (Important-10): the deterministic collapse check is free and the judge
is metered. A collapsed report — one that says "the report is above", or is far
shorter than any real investigation of this case KIND — has nothing in it for a
judge to find, so the call is skipped and the row records why.
"""
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

# Offline stub, for testing THIS file without a metered judge call. Any row produced
# with it carries judge_stub:true so a stubbed number can never be mistaken for a
# measured one — the alternative was leaving the artifact writer untested, which is
# how it shipped counting directories as files_opened.
judge_call = score_case.http_call
stub = os.environ.get("SHERLOCK_JUDGE_STUB")
if stub:
    print("  ⚠ JUDGE STUB ACTIVE (%s) — this row is NOT a measurement" % stub)

    def judge_call(_prompt, _p=stub):
        return open(_p, encoding="utf-8").read()

# Free deterministic checks FIRST; the judge only runs when there is something to judge.
checks = measure.report_checks(report, case.get("kind"))
if checks["collapsed"]:
    judged = {"found": False, "why": "judge skipped: %s" % checks["collapse_reason"]}
else:
    judged = score_case.score(case, report, call=judge_call)

# The corpus root is what makes a `coverage` diagnosis reachable: proof locations are
# corpus-relative, the directories a scan reports are absolute, and without the anchor
# every miss degrades to "cannot exclude" -> `inconclusive`. The rig BUILDS this path,
# so it is known exactly and must never be inferred from the stream.
v = measure.verdict(case, stream, report, judged["found"],
                    corpus_root=os.path.join(a.case, "corpus"))
meta = json.load(open(os.path.join(a.run, "meta.json"), encoding="utf-8"))
# `model` is load-bearing, not decoration: the provider under test changed mid-project
# (linkapi 400s -> the same deepseek-v4-flash reached via CloseRouter), and a row that
# does not name its engine can be silently averaged with one from another engine.
row = {"case_id": v["case_id"], "arm": meta["arm"], "model": meta.get("model"),
       "tier": a.tier, "diagnosis": v["diagnosis"], "judge_found": v["judge_found"],
       "why": judged["why"], "requires": v["requires"],
       "files_opened": len(v["reach"]["files_opened"]),
       "proofs_reached": len(v["reach"]["reached"]),
       # A `collapse` row used to carry no reason, and an `inconclusive` row gave no
       # way to see WHICH proofs were unresolvable — both are needed to act on a row
       # without re-opening the stream by hand.
       "reach_verdict": v["reach"]["verdict"],
       "proofs_unknown": v["reach"]["unknown"],
       "collapse_reason": v["report"]["collapse_reason"],
       "report_chars": v["report"]["chars"],
       "tool_calls": v["budget"]["tool_calls"], "run_dir": a.run,
       "judge_stub": bool(stub)}
with open(a.results, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("  %s %s -> %s" % (row["case_id"], row["arm"], row["diagnosis"]))
