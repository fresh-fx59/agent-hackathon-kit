#!/usr/bin/env python3
"""check.py — the ONE self-check. It is finalize.py with the Stop hook's flags.

WHY (v58 small test): the model ran its own checker lines — citecheck without
`--require-quote`, triagecheck with the wrong arguments — and got verdicts the
final gate did not share; after a context compaction it lost the finalize argv
entirely. This command has no flags to get wrong: it runs exactly the finalize
invocation the Stop hook runs (same gates, same argv, same attempt evidence
under work/validation/), renders `path:line#Field` references first, and then
prints each blocking item WITH ITS REPORT LINE.

    python3 <SKILL_BASE_DIR>/tools/check.py            # ./work, ./corpus
    python3 <SKILL_BASE_DIR>/tools/check.py --work work --corpus /work/corpus

Exit code = finalize's (0 clean, 2 blocking).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
BAD = ("wrong-content", "out-of-range", "missing-file", "no-quote", "binary-file",
       "ambiguous", "weak-quote", "not-shown", "missing-record", "missing-field")


def finalize_argv(work, corpus, package=PACKAGE):
    """The Stop hook's finalize argv (stopcheck.run_finalize), minus its deadline."""
    return [sys.executable, str(HERE / "finalize.py"), "--work", str(work),
            "--corpus", str(corpus), "--package", str(package), "--require-index"]


def _json(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None


def summarize(attempt):
    out = []
    meta = _json(Path(attempt) / "metadata.json") or {}
    r = meta.get("render") or {}
    if r.get("expanded") or r.get("refused"):
        out.append("render: развёрнуто %s, отказано %d" % (r.get("expanded", 0),
                                                           len(r.get("refused") or [])))
        for ln, ref, why in (r.get("refused") or [])[:20]:
            out.append("  ✗ стр.%s %s — %s" % (ln, ref, why))
    for gate, detail in (meta.get("gates") or {}).items():
        blocking = detail.get("parsed_blocking")
        out.append("%s: blocking %s (rc %s)" % (gate, blocking, detail.get("exit_code")))
        if not blocking:
            continue
        d = _json(Path(attempt) / (gate + ".stdout")) or {}
        if gate == "citecheck":
            for c in d.get("citations") or []:
                if c.get("verdict") in BAD:
                    out.append("  ✗ стр.%s %s %s:%s %s" % (
                        c.get("report_line"), c["verdict"], c.get("path"), c.get("line"),
                        (c.get("detail") or c.get("weak") or "")[:140]))
            for key in ("outcomes", "report_evidence", "aggregates"):
                sub = d.get(key) or {}
                if sub.get("blocking"):
                    out.append("  ✗ %s: %s блокирующих — подробности: python3 %s "
                               "work/report.md --corpus <LOG_DIR> --require-quote"
                               % (key, sub.get("blocking"), HERE / "citecheck.py"))
        elif gate == "reportcheck":
            for x in (d.get("defects") or [])[:30]:
                out.append("  ✗ стр.%s %s %s" % (x.get("line", "?"), x.get("defect"),
                                                 str(x.get("text") or x.get("detail") or "")[:120]))
        elif gate == "triagecheck":
            for k in (d.get("bad_inline") or [])[:10] + (d.get("bad_receipts") or [])[:10]:
                out.append("  ✗ %s" % k)
            for rule in d.get("rules") or []:
                for p in (rule.get("проблемы") or [])[:2]:
                    out.append("  ✗ %s %s" % (rule.get("id"), p[:140]))
            for j in d.get("junk") or []:
                out.append("  ✗ rules.tsv:%s %s" % (j.get("строка"), j.get("что")))
        elif gate == "statecheck":
            out.append("  подробности: %s" % (Path(attempt) / "statecheck.stdout"))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--work", default="work")
    ap.add_argument("--corpus", default=None)
    args = ap.parse_args(argv)
    corpus = args.corpus
    if corpus is None:
        for cand in (os.environ.get("SHERLOCK_CORPUS"), "corpus", "/work/corpus"):
            if cand and os.path.isdir(cand):
                corpus = cand
                break
    if not corpus:
        print("check.py: корпус не найден — укажи --corpus", file=sys.stderr)
        return 2
    proc = subprocess.run(finalize_argv(args.work, corpus), capture_output=True, text=True)
    res = _json_text(proc.stdout) or {}
    attempt = res.get("attempt")
    print("ИТОГ: %s" % res.get("verdict", "unknown"))
    if (res.get("verdict_detail") or {}).get("reason"):
        print(res["verdict_detail"]["reason"])
    if attempt:
        print(summarize(attempt))
    elif proc.stderr:
        print(proc.stderr[-2000:], file=sys.stderr)
    return proc.returncode


def _json_text(text):
    i = (text or "").find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None


if __name__ == "__main__":
    sys.exit(main())
