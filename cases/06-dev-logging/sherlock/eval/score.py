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
# Pinned, not left to the provider's default. Measured: the same four answers,
# re-judged by the same model, swung one arm 18.2 -> 9.1 and another 9.1 -> 18.2.
# A comparison across arms is meaningless while the judge itself moves by a whole
# defect, so temperature and seed are part of the measurement, not of the weather.
JUDGE_TEMPERATURE = float(os.environ.get("JUDGE_TEMPERATURE", "0"))
JUDGE_SEED = int(os.environ.get("JUDGE_SEED", "20260728"))

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


# Offline stub, for testing THIS file without a metered judge call. Same contract as
# report-case.py's SHERLOCK_JUDGE_STUB: every row produced with it carries
# judge_stub:true, so a stubbed number can never be mistaken for a measured one. The
# alternative was leaving the scorer that produces every quoted recall figure
# completely untested.
STUB = os.environ.get("SHERLOCK_SCORE_STUB")


def judge(answer, key_text):
    if STUB:
        return _parse_verdict(open(STUB, encoding="utf-8").read())
    if not JUDGE_KEY:
        sys.exit("set JUDGE_API_KEY (use with-secret.sh eval_broker_api_key --env JUDGE_API_KEY -- ...)")
    body = json.dumps({
        "model": JUDGE_MODEL,
        "temperature": JUDGE_TEMPERATURE,
        "seed": JUDGE_SEED,
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
    return _parse_verdict(out["choices"][0]["message"]["content"])


def _parse_verdict(txt):
    txt = (txt or "").strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--dataset")
    ap.add_argument("--ledger", default=os.path.join(HERE, "runs.jsonl"),
                    help="run ledger to score (default eval/runs.jsonl; the 649MB "
                         "benchmark keeps its own at eval/bench/runs-bench.jsonl)")
    ap.add_argument("--all-rows", action="store_true",
                    help="score EVERY row, not just the latest per (dataset, arm). "
                         "Required for repetitions: this corpus produced 100/73/18 %% "
                         "on ONE arm, so a single run per arm cannot separate a real "
                         "improvement from run-to-run spread. Each score carries its "
                         "source line number so the number traces back to raw data.")
    args = ap.parse_args()

    key = json.load(open(args.key, encoding="utf-8"))
    defects = key if isinstance(key, list) else key.get("defects", [])

    # The key deliberately plants RED HERRINGS (D12, D13): things that look like the
    # cause but are not. They must NOT be in the recall denominator — "missing" a red
    # herring is correct behaviour — and reporting one is a false positive.
    def is_herring(d):
        blob = "%s %s" % (d.get("title", ""), d.get("description", ""))
        return bool(d.get("red_herring")) or "RED HERRING" in blob.upper()

    real     = [d for d in defects if not is_herring(d)]
    herrings = [d for d in defects if is_herring(d)]
    total = len(real)

    key_text = "\n".join(
        "- %s: %s | root cause: %s" % (d.get("id"), d.get("title", "")[:200],
                                       (d.get("root_cause") or d.get("description", ""))[:300])
        for d in real)
    if herrings:
        key_text += ("\n\nЛОВУШКИ (red herrings) — НЕ являются дефектами. Если отчёт "
                     "называет любую из них причиной, это ложное срабатывание:\n" +
                     "\n".join("- %s: %s" % (d.get("id"), d.get("title", "")[:160])
                               for d in herrings))
    print("answer key: %d real defects + %d red herrings (denominator = %d)"
          % (total, len(herrings), total))

    latest, rows = {}, []
    for lineno, line in enumerate(open(args.ledger, encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if args.dataset and r.get("dataset") != args.dataset:
            continue
        r["_line"] = lineno
        latest[(r["dataset"], r["arm"])] = r
        rows.append(r)

    if args.all_rows:
        selected = [((r["dataset"], r["arm"]), r) for r in rows]
    else:
        selected = sorted(latest.items())

    # rep index per (dataset, arm), in ledger order — so three v7 rows read r1/r2/r3
    # instead of three indistinguishable lines.
    seen = {}
    out_path = os.environ.get("SHERLOCK_SCORES_OUT") or os.path.join(HERE, "scores.jsonl")
    if STUB:
        print("  ⚠ JUDGE STUB ACTIVE (%s) — these rows are NOT measurements" % STUB)
    for (ds, arm), r in selected:
        seen[(ds, arm)] = seen.get((ds, arm), 0) + 1
        v = judge(r.get("answer", ""), key_text)
        found = v.get("found_ids", [])
        rec = {"dataset": ds, "arm": arm, "model": r.get("model"),
               "rep": seen[(ds, arm)], "ledger": os.path.basename(args.ledger),
               "ledger_line": r["_line"], "judge_model": JUDGE_MODEL,
               "judge_temperature": JUDGE_TEMPERATURE, "judge_seed": JUDGE_SEED,
               "found_ids": found, "missed_ids": v.get("missed_ids", []),
               "recall_pct": round(100.0 * len(found) / total, 1) if total else None,
               "false_positives": v.get("false_positives"),
               "turns": r.get("turns"), "duration_s": r.get("duration_s"),
               "notes": v.get("notes", ""), "judge_stub": bool(STUB)}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("%-16s %-5s recall=%5.1f%%  found=%2d/%d  FP=%s" % (
            ds, arm, rec["recall_pct"] or 0, len(found), total, rec["false_positives"]))


if __name__ == "__main__":
    main()
