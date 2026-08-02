#!/usr/bin/env python3
"""score-bench.py — turn one 649 MB bench answer into the number that gets quoted.

    JUDGE_API_KEY=... python3 score-bench.py                 # score the latest row
    JUDGE_API_KEY=... python3 score-bench.py --arm v11       # latest row for an arm

`run-bench.sh` records the answer and never scores it, so every bench figure this
project has quoted was read off a report by eye. It asks the SAME judge the SAME
question as every slice row — `score_case.build_prompt` / `parse_verdict` /
`http_call`, imported, not reimplemented — once per answer-key entry. The judge moves
the score more than the arm does, so a second judge implementation would silently
produce a second, incomparable scale.

RED HERRINGS ARE NOT DEFECTS. D12 and D13 are planted to be refuted. `found: true` on
one of them is a FALSE POSITIVE and must never count toward the score, and the
denominator is the real defects only — scoring 13 would let an arm that reports
everything it sees beat one that discriminates.

A transport failure RAISES. Recording it as "not found" would be indistinguishable
from a real miss, which is the same class of lie as an unmeasured cost recorded as 0.
"""
import argparse
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))
_spec = importlib.util.spec_from_file_location(
    "score_case", os.path.join(SHERLOCK, "measure", "score_case.py"))
score_case = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_case)

# The key marks these by prefixing the title. They are the discrimination test, not
# the recall test, so they are scored on the opposite axis.
HERRING_MARK = "RED HERRING"


def is_herring(d):
    return (d.get("title") or "").strip().upper().startswith(HERRING_MARK)


def load_key(raw):
    """The answer key as {id: entry}, from whichever shape is on disk.

    The shipped file is `{"scenario": …, "defects": [ {"id": "D01", …}, … ]}`. A
    scorer that iterates the top level instead would judge "scenario" and "seed" as
    defects and print a number — so the shape is normalised in one place and tested
    against the real file, not against a fixture written to match the code.
    """
    entries = raw.get("defects", raw) if isinstance(raw, dict) else raw
    if isinstance(entries, dict):
        return {cid: dict(e, case_id=e.get("case_id", cid))
                for cid, e in entries.items()}
    out = {}
    for e in entries:
        cid = e.get("id") or e.get("case_id")
        if not cid:
            raise ValueError("answer-key entry has no id: %r" % str(e)[:120])
        out[cid] = dict(e, case_id=cid)
    return out


def score(raw_key, answer, call):
    """One judged verdict per answer-key entry, plus the two totals that differ."""
    key = load_key(raw_key)
    rows = []
    for cid in sorted(key):
        d = dict(key[cid])
        d.setdefault("case_id", cid)
        v = score_case.parse_verdict(call(score_case.build_prompt(d, answer)))
        rows.append({"defect": cid, "herring": is_herring(d),
                     "title": d.get("title", ""), **v})
        # Progress as it happens: a loop that prints only at the end loses every
        # judged verdict when the transport dies on entry 12 of 13.
        print("  judged %s: found=%s" % (cid, v["found"]), flush=True)
    real = [r for r in rows if not r["herring"]]
    herr = [r for r in rows if r["herring"]]
    return {"rows": rows,
            "found": sum(1 for r in real if r["found"]), "total": len(real),
            "false_positives": sum(1 for r in herr if r["found"]),
            "herrings": len(herr)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "runs-bench.jsonl"))
    ap.add_argument("--key", default=os.path.join(HERE, "answer-key.json"))
    ap.add_argument("--arm")
    ap.add_argument("--out", default=os.path.join(HERE, "scores-bench.jsonl"))
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
    if a.arm:
        rows = [r for r in rows if r.get("arm") == a.arm]
    if not rows:
        sys.exit("no bench rows%s" % (" for arm %s" % a.arm if a.arm else ""))
    run = rows[-1]
    answer = run.get("answer") or ""
    if not answer.strip():
        sys.exit("the bench row carries no answer — nothing to score")

    key = json.load(open(a.key, encoding="utf-8"))

    def call(prompt, _inner=score_case.http_call):
        """Retry the TRANSPORT, never the verdict.

        The broker dropped a connection mid-loop on 2026-08-02 and took 13 judged
        calls with it. A dropped socket is not evidence about the report, so it is
        retried; a verdict that parses is used exactly once, because re-asking a
        judge until it agrees is how a score stops meaning anything.
        """
        last = None
        for attempt in range(1, 4):
            try:
                return _inner(prompt)
            except Exception as e:                      # transport only
                last = e
                print("  ⚠ judge transport failed (attempt %d): %s"
                      % (attempt, str(e)[:120]))
                time.sleep(2.0 * attempt)
        raise last
    stub = os.environ.get("SHERLOCK_JUDGE_STUB")
    if stub:
        print("⚠ JUDGE STUB ACTIVE (%s) — this score is NOT a measurement" % stub)

        def call(_prompt, _p=stub):
            return open(_p, encoding="utf-8").read()

    res = score(key, answer, call)
    for r in res["rows"]:
        mark = "✓" if r["found"] else "·"
        if r["herring"]:
            mark = "✗ FALSE POSITIVE" if r["found"] else "✓ refused"
        print("%-3s %-18s %s" % (r["defect"], mark, r["title"][:70]))
        print("      %s" % r["why"][:160])
    print("\nНАЙДЕНО %d из %d реальных дефектов (%.0f %%) · ложных срабатываний на "
          "приманках: %d из %d"
          % (res["found"], res["total"], 100.0 * res["found"] / res["total"],
             res["false_positives"], res["herrings"]))

    out = {"arm": run.get("arm"), "model": run.get("model"),
           "judge_model": score_case.JUDGE_MODEL, "dataset": run.get("dataset"),
           "trace_dir": run.get("trace_dir"), "judge_stub": bool(stub),
           "found": res["found"], "total": res["total"],
           "false_positives": res["false_positives"], "herrings": res["herrings"],
           "turns": run.get("turns"), "duration_s": run.get("duration_s"),
           "input_tokens": run.get("input_tokens"),
           "output_tokens": run.get("output_tokens"),
           "answer_chars": run.get("answer_chars"),
           "files_cited": run.get("files_cited"),
           "files_in_corpus": run.get("files_in_corpus"),
           "per_defect": [{k: r[k] for k in ("defect", "found", "herring", "why")}
                          for r in res["rows"]]}
    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(out, ensure_ascii=False) + "\n")
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
