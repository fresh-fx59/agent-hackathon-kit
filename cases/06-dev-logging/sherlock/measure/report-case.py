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
# meta.json is written LAST by run-case.sh, after the report, so a run that died in
# between has a report and no meta. The verdict comes from the case, the stream and the
# report — none of it from meta — so an unreadable meta must degrade to "unmeasured"
# and still emit the row, loudly. Dropping the row instead would lose a real judged
# result over a missing cost record.
try:
    meta = json.load(open(os.path.join(a.run, "meta.json"), encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError("meta.json is not an object")
except (OSError, ValueError) as e:
    print("  ⚠ unreadable meta.json (%s) — arm/model/cost recorded as null" % e)
    meta = {}


def cost(key):
    """One cost field from meta.json, or None if it was not measured.

    A cost that could not be measured is null, NEVER 0 — they mean opposite things
    here. `output_tokens: 0` is a real measurement (a provider that answered with
    nothing); "the final record carried no `usage` block" is an absence. Averaged
    together, the absence invents a free arm, which is exactly the comparison this
    column exists to make honest.
    """
    v = meta.get(key)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# `model` is load-bearing, not decoration: the provider under test changed mid-project
# (linkapi 400s -> the same deepseek-v4-flash reached via CloseRouter), and a row that
# does not name its engine can be silently averaged with one from another engine.
def _provenance(run_dir):
    """Re-derive from the raw stream whether the ARM actually drove this run.

    `skill_loaded` must come from a `skill` tool_use OR from the runner having
    INJECTED the arm's text into the prompt (meta.skill_delivery == "injected").
    Substring-searching the trajectory for the skill name looks equivalent and
    silently always passes, because the corpus path itself contains it.

    Injection exists because 4 of 9 v11 rows never called the tool — 44 %. A row
    whose arm text was injected DID have the arm in context, so reporting it as
    never-loaded would be the same lie in the opposite direction.
    """
    import os as _os
    out = {"skill_loaded": None, "map_tool_ran": None, "subagent_spawned": None}
    sp = _os.path.join(run_dir, "stream.jsonl")
    if not _os.path.exists(sp):
        return out
    skill, mapped, sub = False, 0, 0
    for raw in open(sp, encoding="utf-8", errors="replace"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        for part in msg.get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            name = part.get("name")
            if name == "skill":
                skill = True
            elif name == "agent":
                sub += 1
            cmd = str((part.get("input") or {}).get("command") or "")
            if "logmap.py" in cmd or "logstat.py" in cmd:
                mapped += 1
    injected = False
    mp = _os.path.join(run_dir, "meta.json")
    if _os.path.exists(mp):
        try:
            injected = json.load(open(mp, encoding="utf-8")
                                 ).get("skill_delivery") == "injected"
        except ValueError:
            pass
    return {"skill_loaded": bool(skill or injected), "map_tool_ran": mapped,
            "subagent_spawned": sub}


row = {"case_id": v["case_id"], "arm": meta.get("arm"), "model": meta.get("model"),
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
       # PROVENANCE — did this row come from the ARM, or from the bare model?
       # An `ok` scored with the skill never loaded and the arm's own first tool
       # never invoked is a fact about the base model, not evidence for the arm.
       # Two v11 rows (D01, D11) were exactly that and were reported as arm wins
       # until reconcile.py caught it, so the ledger now carries provenance itself
       # instead of leaving it to a later audit that may not happen.
       **_provenance(a.run),
       # Cost is the other half of the comparison: an arm that scores the same for 3×
       # the tokens and 7× the wall clock is not an improvement, it is a regression
       # nobody priced. run-case.sh has captured all four into meta.json from the
       # start, but they stopped at the run dir — so every A/B in this ledger so far
       # compared quality against no cost at all.
       "duration_s": cost("duration_s"), "input_tokens": cost("input_tokens"),
       "output_tokens": cost("output_tokens"), "turns": cost("turns"),
       "judge_stub": bool(stub)}
with open(a.results, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("  %s %s -> %s" % (row["case_id"], row["arm"], row["diagnosis"]))
