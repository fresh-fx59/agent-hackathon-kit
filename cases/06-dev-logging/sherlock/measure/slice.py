#!/usr/bin/env python3
"""slice.py — turn one planted defect into its own small corpus.

    python3 slice.py --key <answer-key.json> --corpus <dir> --out <dir> [--only D01]

Why whole files, not line windows: the answer key's proof line numbers are 1-based
PHYSICAL lines. Copying a whole file keeps every one of them valid inside the slice
with no renumbering, and keeps the within-file noise that makes the task realistic.
A line-window slice would be smaller and would quietly invalidate proof_reach.

A slice is EASIER than the full corpus. Slice-green does not imply corpus-green;
see measure/README.md and the three-tier gate.
"""
import argparse
import json
import os
import shutil
import sys


def is_herring(defect):
    """Same rule as eval/score.py — the data carries no boolean flag."""
    blob = "%s %s" % (defect.get("title", ""), defect.get("description", ""))
    return bool(defect.get("red_herring")) or "RED HERRING" in blob.upper()


def proof_files(defect):
    return sorted({p["file"] for p in defect.get("proof_locations", [])})


def _count_lines(path):
    n = 0
    with open(path, "rb") as fh:
        for _ in fh:
            n += 1
    return n


def build_case(key, corpus_dir, out_dir, defect_id):
    defect = next((d for d in key["defects"] if d["id"] == defect_id), None)
    if defect is None:
        raise KeyError("no defect %s in the answer key" % defect_id)

    case_dir = os.path.join(out_dir, defect_id)
    if os.path.isdir(case_dir):
        shutil.rmtree(case_dir)
    os.makedirs(case_dir, exist_ok=True)

    files = proof_files(defect)
    for rel in files:
        src = os.path.join(corpus_dir, rel)
        if not os.path.isfile(src):
            raise FileNotFoundError("proof file missing from corpus: %s" % src)
        dst = os.path.join(case_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    # A proof that points past the end of its file means the key and the corpus have
    # drifted apart. Fail loudly: every downstream verdict is built on these numbers.
    # .gz is exempt — its numbers are in the DECOMPRESSED stream, which we do not expand.
    for p in defect.get("proof_locations", []):
        if p["file"].endswith(".gz"):
            continue
        n = _count_lines(os.path.join(case_dir, p["file"]))
        if p["line_end"] > n + 1:      # +1 tolerates a truncated tail (no trailing newline)
            raise ValueError("proof %s:%d is past EOF (%d lines) — key/corpus drift"
                             % (p["file"], p["line_end"], n))

    case = {
        "case_id": defect_id,
        "kind": "defect_slice",
        "defect_id": defect_id,
        "title": defect.get("title", ""),
        "root_cause": defect.get("root_cause") or defect.get("description", ""),
        "requires": defect.get("requires", ""),
        "files": files,
        "proof_locations": defect.get("proof_locations", []),
    }
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)
    return case


def build_all(key, corpus_dir, out_dir):
    ids = []
    for d in key["defects"]:
        if is_herring(d):
            continue
        build_case(key, corpus_dir, out_dir, d["id"])
        ids.append(d["id"])
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("SHERLOCK_ANSWER_KEY"), required=False)
    ap.add_argument("--corpus", default=os.environ.get("SHERLOCK_CORPUS"), required=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="build just this defect id")
    a = ap.parse_args()
    if not a.key or not a.corpus:
        sys.exit("set --key/--corpus or SHERLOCK_ANSWER_KEY/SHERLOCK_CORPUS")
    key = json.load(open(a.key, encoding="utf-8"))
    if a.only:
        c = build_case(key, a.corpus, a.out, a.only)
        print("%s: %d file(s)" % (c["case_id"], len(c["files"])))
    else:
        for cid in build_all(key, a.corpus, a.out):
            c = json.load(open(os.path.join(a.out, cid, "case.json"), encoding="utf-8"))
            print("%s: %d file(s)  [%s]" % (cid, len(c["files"]), c["requires"]))


if __name__ == "__main__":
    main()
