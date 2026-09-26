#!/usr/bin/env python3
"""microcheck.py — the checker for the citation micro-benchmark.

Runs the PACKAGE's own code on work/report.md: finalize.render_report (render
+ any auto-fixes that version has), then citecheck --require-quote and
reportcheck. Only claim-level defects count (labels, absolutes, citations,
aggregates, enum decodes) — the micro task writes one findings section, not a
whole report. Every call appends one row to work/bench-rounds.jsonl.

    python3 /work/bench/microcheck.py            # defaults for the sandbox
Exit 0 = CLEAN, 2 = defects left.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CLAIM_KINDS = {"assertion_unlabelled", "label_unknown", "label_position",
               "label_conflict", "absolute_uncited"}
BAD_CITES = {"wrong-content", "out-of-range", "missing-file", "no-quote", "binary-file",
             "ambiguous", "weak-quote", "not-shown", "missing-record", "missing-field",
             "typed-quote"}
REF_RE = re.compile(r"[\w.\-]+\.jsonl:\d+#[A-Za-z]")
QUOTE_RE = re.compile(r"«[^»]*»")
CITED_BULLET_RE = re.compile(r"^\s*[-*]\s.*[\w.\-]+\.jsonl:\d+", re.M)
MIN_CLAIMS = 5


def _load(tools, name):
    spec = importlib.util.spec_from_file_location("bench_" + name, str(Path(tools) / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _json(text):
    i = (text or "").find("{")
    return json.JSONDecoder().raw_decode(text[i:])[0] if i >= 0 else {}


def source_stats(text):
    """References vs typed quotes in what the MODEL wrote (before render)."""
    return {"refs": len(REF_RE.findall(text)), "typed_quotes": len(QUOTE_RE.findall(text)),
            "claims": len(CITED_BULLET_RE.findall(text))}


def grade(pkg, work, corpus):
    tools = Path(pkg) / "tools"
    report = Path(work) / "report.md"
    text = report.read_text(encoding="utf-8") if report.is_file() else ""
    stats = source_stats(text)
    fin = _load(tools, "finalize")
    render = fin.render_report(str(pkg), str(work), str(corpus)) if text else {}
    rc = _json(subprocess.run([sys.executable, str(tools / "reportcheck.py"), str(report), "--json"],
                              capture_output=True, text=True).stdout) if text else {}
    cc = _json(subprocess.run([sys.executable, str(tools / "citecheck.py"), str(report), "--corpus",
                               str(corpus), "--require-quote", "--json"],
                              capture_output=True, text=True).stdout) if text else {}
    defects = []
    ck = _load(tools, "check")
    for x in rc.get("defects") or []:
        if x.get("defect") in CLAIM_KINDS:
            defects.append((x["defect"], str(x.get("detail") or "")[:160]))
            # the version's own help, if it has any (v61: a census line)
            if x["defect"] == "absolute_uncited" and hasattr(ck, "census_hint"):
                for h in ck.census_hint(str(x.get("detail") or ""), str(corpus))[:2]:
                    defects.append(("  hint", h.strip()))
    for c in cc.get("citations") or []:
        if c.get("verdict") in BAD_CITES:
            defects.append(("cite:" + c["verdict"], "стр.%s %s:%s %s" % (
                c.get("report_line"), c.get("path"), c.get("line"),
                str(c.get("detail") or c.get("weak") or "")[:140])))
    for it in (cc.get("aggregates") or {}).get("items") or []:
        if it.get("verdict") != "ok":
            defects.append(("agg:" + it["verdict"], "строка %s %s %s" % (
                it.get("report_line"), it.get("detail", ""), it.get("suggest") or "")))
    enum = ((cc.get("report_evidence") or {}).get("enum_decode") or {}).get("items") or []
    for it in enum:
        if it.get("kind") in ("missing_decode", "wrong_decode"):
            defects.append(("enum:" + it["kind"], "стр.%s %s=%s → (%s)" % (
                it.get("line"), it.get("field"), it.get("display") or it.get("value"), it.get("expected"))))
    for ln, ref, why in (render.get("refused") or []):
        defects.append(("render:refused", "стр.%s %s — %s" % (ln, ref, why)))
    if stats["claims"] < MIN_CLAIMS:
        defects.append(("too-few-claims", "нужно ≥%d пунктов со ссылкой, есть %d"
                        % (MIN_CLAIMS, stats["claims"])))
    return {"stats": stats, "defects": defects, "autofixed": len(render.get("autofixed") or []),
            "render_error": render.get("error")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", default=os.environ.get("BENCH_PKG", "/work/skills-root/sherlock"))
    ap.add_argument("--work", default="/work/work")
    ap.add_argument("--corpus", default="/work/corpus")
    a = ap.parse_args(argv)
    g = grade(Path(a.pkg).resolve(), a.work, a.corpus)
    kinds = {}
    g["defects_n"] = sum(1 for k, _ in g["defects"] if k != "  hint")
    for k, _ in g["defects"]:
        if k != "  hint":
            kinds[k] = kinds.get(k, 0) + 1
    log = Path(a.work) / "bench-rounds.jsonl"
    n = sum(1 for _ in open(log, encoding="utf-8")) + 1 if log.is_file() else 1
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"round": n, "t": time.time(), "blocking": g["defects_n"],
                             "kinds": kinds, "autofixed": g["autofixed"], **g["stats"]},
                            ensure_ascii=False) + "\n")
    if not g["defects_n"]:
        print("CLEAN — %d пунктов, ссылок %d" % (g["stats"]["claims"], g["stats"]["refs"]))
        return 0
    print("NOT CLEAN — %d дефектов (раунд %d)" % (g["defects_n"], n))
    for k, d in g["defects"][:25]:
        print("  ✗ %s %s" % (k, d))
    return 2


if __name__ == "__main__":
    sys.exit(main())
