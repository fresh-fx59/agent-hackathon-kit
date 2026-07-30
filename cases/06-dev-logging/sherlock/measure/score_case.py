#!/usr/bin/env python3
"""score_case.py — ask the judge the ONE question deterministic checks cannot.

    JUDGE_API_KEY=... python3 score_case.py --case <case-dir> --run <run-dir>

Judge: gpt-5.5 via the cliproxyapi broker (subscription). It is neutral to both the
model under test (deepseek) and the skill's author (Claude), and it reproduces the
historical eval/scores.jsonl column, so numbers stay comparable across sessions.

Transport note: plain urllib with an explicit User-Agent works against this broker.
secret-curl.sh's -K config path returns "Missing API key" — do not use it here.
A transport failure RAISES; it must never be silently recorded as "not found",
which would look identical to a real miss.
"""
import argparse
import json
import os
import sys
import urllib.request

JUDGE_URL = os.environ.get("JUDGE_BASE_URL", "http://127.0.0.1:8317/v1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-5.5")

PROMPT = """You are grading an incident-investigation report against ONE known defect.

DEFECT {case_id}: {title}
ROOT CAUSE: {root_cause}

THE REPORT UNDER TEST:
{report}

Did the report identify THIS defect? Count it as found only if it identifies the same
underlying problem — different wording is fine, a vague gesture at the same area is
NOT. Partial credit does not exist.

Return STRICT JSON, no prose, no markdown fence:
{{"found": true|false, "why": "<one sentence>"}}"""


def build_prompt(case, report):
    return PROMPT.format(case_id=case.get("case_id", "?"), title=case.get("title", ""),
                         root_cause=case.get("root_cause", ""), report=(report or "")[:120000])


def parse_verdict(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        d = json.loads(t)
    except ValueError:
        raise ValueError("judge did not return JSON: %r" % (text or "")[:200])
    if "found" not in d:
        raise ValueError("judge JSON has no 'found' key: %r" % t[:200])
    return {"found": bool(d["found"]), "why": d.get("why", "")}


def http_call(prompt):
    key = os.environ.get("JUDGE_API_KEY")
    if not key:
        sys.exit("set JUDGE_API_KEY (with-secret.sh eval_broker_api_key --env JUDGE_API_KEY -- ...)")
    body = json.dumps({"model": JUDGE_MODEL,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(JUDGE_URL.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json",
                                          "User-Agent": "sherlock-measure/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    return out["choices"][0]["message"]["content"]


def score(case, report, call=http_call):
    v = parse_verdict(call(build_prompt(case, report)))
    return {"case_id": case.get("case_id"), "found": v["found"], "why": v["why"],
            "judge_model": JUDGE_MODEL}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    case = json.load(open(os.path.join(a.case, "case.json"), encoding="utf-8"))
    report = open(os.path.join(a.run, "report.md"), encoding="utf-8").read()
    r = score(case, report)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
