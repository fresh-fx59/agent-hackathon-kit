#!/usr/bin/env python3
"""Score a run's answer against a planted-defect answer key, with an LLM judge.

    JUDGE_API_KEY=... python3 eval/score.py --key <answer-key.json> [--dataset NAME]

The judge is a DIFFERENT model from the one under test (default: gpt-5.5 via the
local cliproxyapi broker), so nothing ever grades its own work.

Appends one line per scored run to eval/scores.jsonl:
    {dataset, arm, found_ids, missed_ids, recall_pct, false_positives, notes}

Stdlib only - no pip, consistent with the skill's own constraints.
"""
import argparse, json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

JUDGE_URL = os.environ.get("JUDGE_BASE_URL", "http://127.0.0.1:8317/v1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-5.5")
JUDGE_KEY = os.environ.get("JUDGE_API_KEY")

PROMPT = """You are grading an incident-investigation report against a known answer key.

ANSWER KEY — the defects that were deliberately planted in the log corpus:
{key}

THE REPORT UNDER TEST:
{answer}

For each planted defect id, decide whether the report FOUND it. Count it as found
only if the report identifies the same underlying problem — different wording is
fine, a vague gesture at the same area is NOT. Partial credit does not exist:
found or not found.

Also count FALSE POSITIVES: confident claims in the report about defects that are
not in the key and are not plausibly real.

Return STRICT JSON, no prose, no markdown fence:
{{"found_ids": ["D01", ...], "missed_ids": ["D02", ...], "false_positives": <int>,
  "notes": "<two sentences max on the quality of what was found>"}}"""


def judge(answer, key_text):
    if not JUDGE_KEY:
        sys.exit("set JUDGE_API_KEY (use with-secret.sh cliproxyapi_api_key --env JUDGE_API_KEY -- ...)")
    body = json.dumps({
        "model": JUDGE_MODEL,
        "messages": [{"role": "user",
                      "content": PROMPT.format(key=key_text, answer=answer[:120000])}],
    }).encode()
    req = urllib.request.Request(
        JUDGE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + JUDGE_KEY,
                 "Content-Type": "application/json",
                 "User-Agent": "sherlock-eval/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    txt = out["choices"][0]["message"]["content"].strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--dataset")
    args = ap.parse_args()

    key = json.load(open(args.key, encoding="utf-8"))
    defects = key if isinstance(key, list) else key.get("defects", [])
    key_text = "\n".join(
        "- %s: %s | root cause: %s" % (d.get("id"), d.get("title", "")[:200],
                                       (d.get("root_cause") or d.get("description", ""))[:300])
        for d in defects)
    total = len(defects)

    latest = {}
    for line in open(os.path.join(HERE, "runs.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if args.dataset and r.get("dataset") != args.dataset:
            continue
        latest[(r["dataset"], r["arm"])] = r

    out_path = os.path.join(HERE, "scores.jsonl")
    for (ds, arm), r in sorted(latest.items()):
        v = judge(r.get("answer", ""), key_text)
        found = v.get("found_ids", [])
        rec = {"dataset": ds, "arm": arm, "model": r.get("model"),
               "found_ids": found, "missed_ids": v.get("missed_ids", []),
               "recall_pct": round(100.0 * len(found) / total, 1) if total else None,
               "false_positives": v.get("false_positives"),
               "turns": r.get("turns"), "duration_s": r.get("duration_s"),
               "notes": v.get("notes", "")}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("%-16s %-5s recall=%5.1f%%  found=%2d/%d  FP=%s" % (
            ds, arm, rec["recall_pct"] or 0, len(found), total, rec["false_positives"]))


if __name__ == "__main__":
    main()
