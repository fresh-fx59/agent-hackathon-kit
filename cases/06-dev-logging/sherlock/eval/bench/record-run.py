#!/usr/bin/env python3
"""record-run.py — write a bench ledger row for a run `run-bench.sh` cannot drive.

    record-run.py --arm v16-claude --dataset fleet-negative \
        --corpus /path/to/corpus --model claude-opus-5 \
        --answer final-message.txt [--artifact work/report.md] \
        --duration-s 812 [--turns N] [--input-tokens N] [--output-tokens N] \
        --trace-dir eval/bench/runs/2026...-v16-claude-fleetneg

WHY A SECOND RECORDER EXISTS, AND WHY IT IS NOT A SECOND SCALE
--------------------------------------------------------------
`run-bench.sh` is the only way a row has ever been written, and it drives
**qwen-code against a metered provider** (`SHERLOCK_BASE_URL`, default
`https://linkapi.ai/v1`) — real money per call. The 2026-08-18 arm puts a
*subscription* model under test instead, so the runner cannot produce the run at
all. The row still has to be identical in shape, or the first non-`bench649` rows
in this ledger would be unreadable by the two scorers that consume it.

So everything that DECIDES anything is imported from the same modules the runner
imports:

  * `deliverable.compose` / `.channel` — what the run handed over, across both
    channels. A run that answers in 101 chars beside a complete `work/report.md`
    is `delivered_in: "file"`, here exactly as there.
  * the `files_cited` count is matched on the RELATIVE path, never the basename —
    this corpus family ships `auth.log` on ten hosts.

WHAT IT REFUSES TO INVENT
-------------------------
Token counts. A Claude subagent does not hand its parent a usage record, so
`input_tokens` and `output_tokens` are `null` unless measured and passed in, and
they are never 0. → [[eval-must-measure-cost-not-just-quality]]: "an unmeasured
cost recorded as 0 is the same class of lie as an unasked question recorded as a
negative answer". `duration_s` IS measurable from the wall clock, so it is
required.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))

_spec = importlib.util.spec_from_file_location(
    "deliverable", os.path.join(SHERLOCK, "measure", "deliverable.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)


def read(path):
    if not path:
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--client-model")
    ap.add_argument("--answer", help="file holding the run's FINAL MESSAGE")
    ap.add_argument("--artifact", help="the run's work/report.md, if it wrote one")
    ap.add_argument("--duration-s", type=int, required=True)
    ap.add_argument("--turns", type=int)
    ap.add_argument("--input-tokens", type=int)
    ap.add_argument("--output-tokens", type=int)
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--note")
    ap.add_argument("--ledger", default=os.path.join(HERE, "runs-bench.jsonl"))
    a = ap.parse_args()

    if not os.path.isdir(a.corpus):
        sys.exit("no such corpus: %s" % a.corpus)
    answer, artifact = read(a.answer), read(a.artifact)
    if not (answer.strip() or artifact.strip()):
        sys.exit("the run produced neither a final message nor a report — there is "
                 "nothing to record, and a row of zeroes would read as a bad "
                 "investigation instead of an absent one")

    deliv = D.compose(answer, artifact)
    rels = set()
    for root, _d, fs in os.walk(a.corpus):
        for f in fs:
            rels.add(os.path.relpath(os.path.join(root, f), a.corpus)
                     .replace(os.sep, "/"))
    cited = {r for r in rels if r in deliv}

    rec = {"arm": a.arm, "model": a.model, "client_model": a.client_model or a.model,
           "turns": a.turns, "duration_s": a.duration_s,
           "input_tokens": a.input_tokens, "output_tokens": a.output_tokens,
           "answer_chars": len(answer), "artifact_chars": len(artifact),
           "deliverable_chars": len(deliv),
           "delivered_in": D.channel(answer, artifact), "artifact_only": False,
           "files_in_corpus": len(rels), "files_cited": len(cited),
           "cited_files": sorted(cited),
           "line_refs": len(re.findall(r":\d+", deliv)),
           "dataset": a.dataset, "corpus_dir": os.path.abspath(a.corpus),
           "trace_dir": a.trace_dir, "note": a.note,
           "answer": answer, "artifact": artifact}
    with open(a.ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("  ✓ arm=%s dataset=%s turns=%s %ss in/out=%s/%s delivered_in=%s "
          "msg=%d file=%d files_cited=%d/%d line_refs=%d"
          % (rec["arm"], rec["dataset"], rec["turns"], rec["duration_s"],
             rec["input_tokens"], rec["output_tokens"], rec["delivered_in"],
             rec["answer_chars"], rec["artifact_chars"], rec["files_cited"],
             rec["files_in_corpus"], rec["line_refs"]))
    print("  wrote %s" % a.ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
