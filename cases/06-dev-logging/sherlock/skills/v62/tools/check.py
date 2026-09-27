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
import re
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


def summarize(attempt, report_text="", corpus=None):
    out = []
    report = report_text
    meta = _json(Path(attempt) / "metadata.json") or {}
    r = meta.get("render") or {}
    if r.get("expanded") or r.get("refused") or r.get("converted"):
        out.append("render: развёрнуто %s, отказано %d" % (r.get("expanded", 0),
                                                           len(r.get("refused") or [])))
        for ln, ref, why in (r.get("refused") or [])[:20]:
            out.append("  ✗ стр.%s %s — %s" % (ln, ref, why))
    for ln, was, now in (r.get("autofixed") or [])[:20]:
        out.append("  исправлено само стр.%s: %s → %s" % (ln, was, now))
    for ln, typed, ref in (r.get("converted") or [])[:20]:
        out.append("  заменено стр.%s %s → %s — дальше пиши ссылку сам" % (ln, typed, ref))
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
                        (c.get("detail") or c.get("weak") or "")[:220]))
            out.extend(_inline_subgates(d))
        elif gate == "reportcheck":
            for x in (d.get("defects") or [])[:30]:
                detail = str(x.get("text") or x.get("detail") or "")
                out.append("  ✗ стр.%s %s %s" % (x.get("line") or _locate(report, detail),
                                                 x.get("defect"), detail[:120]))
                if x.get("defect") == "absolute_uncited" and corpus:
                    out.extend(census_hint(detail, corpus))
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


# --------------------------------------------------------------------- v61
# WHY: the v60 small test (69 calls, 8.13M tokens) hid every citecheck sub-gate
# behind «подробности: python3 citecheck.py …». The model then ran citecheck
# itself 13 times, with `| head`/`| grep`/`| tail` guesses, each printing a
# 200-char JSON preview per citation (~10-25k chars) — and still missed the
# «ОКНО ЗАПИСЕЙ» defect, which has no ✗ line. check.py now prints only the
# blocking lines of those sub-gates, with their fix hints, and a report line
# number for every reportcheck defect.
_SUB_HEAD = ("ИСХОДЫ", "ОТЧЁТНЫЕ", "РАСШИФРОВКА", "ПРИНАДЛЕЖНОСТЬ", "ОКНО", "АГРЕГАТЫ")


def _cc():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sherlock_cc_check", HERE / "citecheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _blocking_lines(block, limit=40):
    """Blocking lines of a rendered sub-gate block plus their indented hints."""
    out, keep, depth = [], False, 0
    for raw in (block or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        ind = len(raw) - len(raw.lstrip())
        if s.startswith("✗"):
            keep, depth = True, ind
            out.append("  " + s)
        elif s.startswith("✓") or s.startswith("·") or s.startswith("{"):
            keep = False
        elif s.startswith(_SUB_HEAD) or s.lstrip("  ").startswith(_SUB_HEAD):
            m = re.search(r"(\d+)\s+блокирующ", s)
            keep = bool(m and m.group(1) != "0")
            depth = ind
            if keep:
                out.append("  ✗ " + s)
        elif keep and ind > depth and not s.startswith("подробности"):
            out.append("      " + s[:220])
        if len(out) >= limit:
            out.append("      … (обрезано)")
            break
    return out


def _inline_subgates(d):
    out = []
    try:
        cc = _cc()
    except Exception as error:  # noqa: BLE001
        return ["  (подробности citecheck недоступны: %s)" % error]
    for key, fn in (("outcomes", "render_outcomes"),
                    ("report_evidence", "render_report_evidence"),
                    ("aggregates", "render_aggregates")):
        sub = d.get(key) or {}
        if not sub.get("blocking"):
            continue
        try:
            block = getattr(cc, fn)(sub)
        except Exception:  # noqa: BLE001
            block = ""
        lines = _blocking_lines(block)
        out.extend(lines or ["  ✗ %s: %s блокирующих" % (key, sub.get("blocking"))])
    return out


def _locate(report, detail):
    """Report line of a defect whose detail quotes (a prefix of) that line."""
    frags = re.findall(r"«([^«»]{12,})", detail) + [detail]
    for frag in frags:
        frag = frag.split(" — ")[0].strip()[:60].rstrip("…")
        if len(frag) < 12:
            continue
        for n, line in enumerate(report.splitlines(), 1):
            if frag in line:
                return n
    return "?"


_FIELD_WORDS = ("EventID", "LogonType", "SubStatus", "Status", "ProcessID",
                "WorkstationName", "IpAddress", "TargetUserName", "LogonProcessName")
_FILE_RE = re.compile(r"([\w.\-]+\.jsonl)\b")


def _leaf_paths(rec, prefix=""):
    if isinstance(rec, dict):
        for k, v in rec.items():
            yield from _leaf_paths(v, prefix + k + ".")
    else:
        yield prefix[:-1]


def census_hint(detail, corpus, cc=None):
    """v61: an absolute («все/ни одна/только … EventID») needs an `агрегат:`,
    and zero counts are refused by design — so the provable form of «ни одного
    4624» is a CENSUS: `distinct(Event.System.EventID) = 1` plus the value.
    Offer the exact paste-ready line for each field the sentence names, graded
    by the same evaluator citecheck uses. Nothing is written into the report."""
    words = [w for w in _FIELD_WORDS if re.search(r"\b%s\b" % w, detail)] or ["EventID"]
    files = sorted(set(_FILE_RE.findall(detail)))
    root = Path(corpus)
    if not files:
        files = sorted(p.name for p in root.glob("*.jsonl"))[:3]
    out = []
    try:
        cc = cc or _cc()
    except Exception:  # noqa: BLE001
        return out
    for name in files[:3]:
        path = root / name
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                first = json.loads(fh.readline())
        except (OSError, ValueError):
            continue
        leaves = list(_leaf_paths(first))
        for w in words[:3]:
            field = next((p for p in leaves if p.split(".")[-1] in (w, "#text")
                          and w in p), None)
            if not field:
                continue
            if field.endswith(".#text"):
                field = field[:-len(".#text")]
            try:
                pred = cc.agg_parse_predicate("distinct(%s)" % field)
                verdict, actual, _ = cc.agg_evaluate(str(path), pred)
            except Exception:  # noqa: BLE001
                continue
            if verdict != "ok":
                continue
            vals = {}
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    try:
                        v = cc._agg_get(json.loads(raw), field)
                    except ValueError:
                        continue
                    if v is not None:
                        v = cc._agg_str(v) if hasattr(cc, "_agg_str") else str(v)
                        vals[v] = vals.get(v, 0) + 1
            top = ", ".join("%s×%d" % kv for kv in sorted(vals.items(), key=lambda kv: -kv[1])[:6])
            out.append("      вставь строкой ниже (перепись, а не «все/ни одной»): "
                       "%s   [значения: %s]" % (cc.agg_render_citation(name, pred, actual), top))
    return out


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
        try:
            text = (Path(args.work) / "report.md").read_text(encoding="utf-8")
        except OSError:
            text = ""
        print(summarize(attempt, text, corpus))
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
