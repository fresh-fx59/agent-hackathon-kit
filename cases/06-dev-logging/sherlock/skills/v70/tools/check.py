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
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
BAD = ("wrong-content", "out-of-range", "missing-file", "no-quote", "binary-file",
       "ambiguous", "weak-quote", "typed-quote", "not-shown", "missing-record", "missing-field")


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


def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _defect(gate, kind, value, index, report=""):
    """Normalize one raw gate item without dropping its source location."""
    if isinstance(value, dict):
        line = value.get("report_line") or value.get("line") or value.get("строка")
        detail = value.get("detail") or value.get("text") or value.get("weak")
        code = value.get("defect") or value.get("verdict") or value.get("id") or kind
        if not detail:
            detail = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        line, detail, code = None, str(value), kind
    if not line and report and detail:
        line = _locate(report, str(detail))
    location = "%s.%s[%d]" % (gate, kind, index)
    if line not in (None, "?"):
        location += ":report.md:%s" % line
    return {"location": location, "source": "/%s/%s/%d" % (gate, kind.replace(".", "/"), index),
            "gate": gate, "kind": kind,
            "code": code, "report_line": line, "detail": str(detail or "")}


def _items(value):
    """Gate `rows` can be a count; only a JSON array contains defect items."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "rows"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def _evidence_defects(raw, report):
    """Flatten blocking report-evidence families, keeping their field provenance."""
    sub = raw.get("report_evidence") or {}
    fields = {
        "attribution": ("missing", "invalid", "duplicate_ids"),
        "rejected": ("invalid_citation", "missing_citation", "missing_outcome",
                     "invalid_outcome", "malformed_items", "duplicate_ids"),
        "coverage": ("missing_citation", "invalid_citation", "mismatched_citation",
                     "unflagged_citation", "inadmissible_line", "traversal_path",
                     "ambiguous_path", "missing_path", "false_empty", "false_binary",
                     "false_unreadable", "duplicate_paths", "uncovered_paths",
                     "unexamined_paths", "unsupported_no_address", "invalid_no_address_detail",
                     "malformed"),
        "enum_decode": ("items", "table_problems"),
        "logon_reason": ("items", "problems"),
        "ownership": ("unverifiable_first", "characterised", "malformed", "duplicate_rows",
                      "missing_rows", "invalid_verdict", "invalid_kind", "invalid_first",
                      "bad_evidence", "not_earliest", "false_undetermined", "contradiction",
                      "no_earlier_owner", "scan_problems"),
        "rollover": ("undeclared", "wrong", "spurious", "duplicate_rows", "malformed",
                     "scan_errors", "misplaced_rows"),
    }
    result = []
    for family, keys in fields.items():
        data = sub.get(family) or {}
        for key in keys:
            for i, item in enumerate(_items(data.get(key))):
                result.append(_defect("citecheck", "report_evidence.%s.%s" % (family, key),
                                      item, i, report))
        flags = {"rejected": ("missing_section", "empty_section"),
                 "coverage": ("missing_section", "empty_section", "index_truncated"),
                 "ownership": ("missing_section",),
                 "rollover": ("missing_section", "summary_missing", "summary_duplicate",
                              "summary_mismatch", "scan_failed")}.get(family, ())
        for key in flags:
            if data.get(key):
                result.append(_defect("citecheck", "report_evidence.%s.%s" % (family, key),
                                      {"detail": data[key]}, 0, report))
    return result


def diagnostics(attempt, report_text="", corpus=None, package=PACKAGE):
    """Return a complete, stable repair receipt from one finalize attempt.

    Every raw gate result remains available through its artifact path.  The
    normalized list is deliberately unbounded; callers paginate it explicitly.
    """
    attempt = Path(attempt)
    meta = _json(attempt / "metadata.json") or {}
    gates = meta.get("gates") or {}
    defects = []
    for gate in ("reportcheck", "citecheck", "statecheck", "triagecheck"):
        raw = _json(attempt / (gate + ".stdout")) or {}
        if gate == "citecheck":
            for key in ("citations", "aggregates", "ledger"):
                for i, item in enumerate(_items(raw.get(key))):
                    if isinstance(item, dict):
                        verdict = item.get("verdict")
                        if key == "citations" and verdict not in BAD:
                            continue
                        if key == "aggregates" and verdict == "ok":
                            continue
                    defects.append(_defect(gate, key, item, i, report_text))
            for key in ("non_references",):
                for i, item in enumerate(_items(raw.get(key))):
                    defects.append(_defect(gate, key, item, i, report_text))
            for key in ("missing", "invalid"):
                for i, item in enumerate(_items((raw.get("outcomes") or {}).get(key))):
                    defects.append(_defect(gate, "outcomes." + key, item, i, report_text))
            outcome = raw.get("outcomes") or {}
            if outcome.get("contradiction"):
                defects.append(_defect(gate, "outcomes.contradiction",
                                       {"detail": "implied=%s stated=%s" %
                                        (outcome.get("implied"), outcome.get("stated"))},
                                       0, report_text))
            for i, item in enumerate(_items((raw.get("structure") or {}).get("malformed_headings"))):
                defects.append(_defect(gate, "structure.malformed_headings", item, i, report_text))
            for i, item in enumerate(_items((raw.get("ledger") or {}).get("removed_rows"))):
                defects.append(_defect(gate, "ledger.removed_rows", item, i, report_text))
            defects.extend(_evidence_defects(raw, report_text))
        elif gate == "reportcheck":
            for i, item in enumerate(raw.get("defects") or []):
                defects.append(_defect(gate, "defects", item, i, report_text))
        elif gate == "triagecheck":
            for key in ("bad_inline", "bad_receipts", "junk", "rules"):
                vals = raw.get(key) or []
                if key == "rules":
                    expanded = []
                    for rule in vals:
                        for problem in rule.get("проблемы") or []:
                            expanded.append({"id": rule.get("id"), "detail": problem})
                    vals = expanded
                for i, item in enumerate(vals):
                    defects.append(_defect(gate, key, item, i, report_text))
        elif gate == "statecheck" and gates.get(gate, {}).get("parsed_blocking"):
            defects.append(_defect(gate, "result", {"detail": raw.get("error") or raw.get("message") or "blocking state check"}, 0, report_text))
    raw_paths = {g: str(attempt / (g + ".stdout")) for g in gates}
    report_path = Path(attempt).parent.parent / "report.md"
    work = report_path.parent
    package_path = Path(package)
    ledger = work / "worklist.tsv"
    ledger_data = next((d for d in ((_json(attempt / "citecheck.stdout") or {}).get("ledger"),) if isinstance(d, dict)), {})
    if isinstance(ledger_data.get("rows"), list):
        open_rows = sum(1 for row in ledger_data["rows"]
                        if isinstance(row, dict) and not row.get("resolved"))
    else:
        open_rows = sum(1 for line in ledger.read_text(encoding="utf-8").splitlines()[1:]
                        if line.strip() and "open" in line.lower()) if ledger.is_file() else None
    return {"validation_dir": str(attempt), "report_sha256": _sha256(report_path),
            "input_digest": meta.get("evidence_after") or meta.get("evidence_before"),
            "package_sha256": meta.get("package_tree_sha256_after") or meta.get("package_tree_sha256") or _sha256(package_path),
            "gates": {g: {"exit": (gates.get(g) or {}).get("exit_code"),
                          "blocking": (gates.get(g) or {}).get("parsed_blocking"),
                          "raw": raw_paths.get(g)} for g in ("reportcheck", "citecheck", "statecheck", "triagecheck")},
            "defects": defects, "defect_total": len(defects),
            "ledger": {"unresolved_total": ledger_data.get("unresolved_total"), "open_worklist_rows": open_rows}}
def summarize(attempt, report_text="", corpus=None, offset=0, limit=None):
    receipt = diagnostics(attempt, report_text, corpus)
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
    out.append("validation_dir: %s" % receipt["validation_dir"])
    out.append("report_sha256: %s" % receipt["report_sha256"])
    out.append("package_sha256: %s" % receipt["package_sha256"])
    out.append("defects: %d total; showing offset %d" % (receipt["defect_total"], offset))
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
            for x in (d.get("defects") or []):
                detail = str(x.get("text") or x.get("detail") or "")
                out.append("  ✗ стр.%s %s %s" % (x.get("line") or _locate(report, detail),
                                                 x.get("defect"), detail[:120]))
                if x.get("defect") == "absolute_uncited" and corpus:
                    out.extend(census_hint(detail, corpus))
        elif gate == "triagecheck":
            for k in (d.get("bad_inline") or []) + (d.get("bad_receipts") or []):
                out.append("  ✗ %s" % k)
            for rule in d.get("rules") or []:
                for p in (rule.get("проблемы") or []):
                    out.append("  ✗ %s %s" % (rule.get("id"), p[:140]))
            for j in d.get("junk") or []:
                out.append("  ✗ rules.tsv:%s %s" % (j.get("строка"), j.get("что")))
        elif gate == "statecheck":
            out.append("  подробности: %s" % (Path(attempt) / "statecheck.stdout"))
    selected = receipt["defects"][offset:] if limit is None else receipt["defects"][offset:offset + limit]
    out.append("--- complete repair defects ---")
    out.extend("  ✗ %s %s %s" % (d["location"], d["code"], d["detail"][:220]) for d in selected)
    end = offset + len(selected)
    if end < receipt["defect_total"]:
        out.append("continuation: --offset %d --limit %d (total %d)" % (end, limit or 100, receipt["defect_total"]))
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


def _report_sha(work):
    try:
        return hashlib.sha256((Path(work) / "report.md").read_bytes()).hexdigest()
    except OSError:
        return None


def begin_repair_round(work, phase):
    """v70. -> (count, counted). A round counts only when work/report.md differs
    from the report left by the last check (a no-edit rerun is free). A missing
    report cannot be compared, so it counts. The cap of two applies to counted
    rounds only."""
    if not re.fullmatch(r"repair-[1-9][0-9]*", phase):
        raise ValueError("repair phase must be repair-N")
    path = Path(work) / "validation" / "repair-check-counts.json"
    counts = _json(path) or {}
    count = int(counts.get(phase, 0) or 0)
    shas = _json(Path(work) / "validation" / "repair-check-shas.json") or {}
    sha = _report_sha(work)
    if count >= 1 and sha is not None and shas.get(phase) == sha:
        return count, False
    if count >= 2:
        raise RuntimeError("repair phase %s reached the two-check cap" % phase)
    counts[phase] = count + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")
    return counts[phase], True


def end_repair_round(work, phase):
    """v70. Remember the report as the check left it (it may rewrite it)."""
    sha = _report_sha(work)
    if sha is None:
        return
    path = Path(work) / "validation" / "repair-check-shas.json"
    shas = _json(path) or {}
    shas[phase] = sha
    path.write_text(json.dumps(shas, sort_keys=True) + "\n", encoding="utf-8")


def record_repair_check(work, phase):
    """Record one host-side check for ``repair-N`` and enforce the two-check cap."""
    return begin_repair_round(work, phase)[0]


def repair_phase_for_work(work):
    """Derive the trusted repair-N key from checkpoint.json, when in repair."""
    row = _json(Path(work) / "checkpoint.json") or {}
    if row.get("stage") != "repair":
        return None
    seq = row.get("boundary_seq")
    return "repair-%d" % seq if isinstance(seq, int) and seq > 0 else None


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
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--repair-phase", default=None,
                    help="host-side repair-N counter (maximum two checks)")
    args = ap.parse_args(argv)
    phase = args.repair_phase or repair_phase_for_work(args.work)
    counted = True
    if phase:
        try:
            _, counted = begin_repair_round(args.work, phase)
        except (ValueError, RuntimeError) as exc:
            print("check.py: %s" % exc, file=sys.stderr)
            return 2
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
    last = Path(args.work) / "validation" / "last-check.txt"
    head = "ИТОГ: %s — full output: %s" % (res.get("verdict", "unknown"), last)
    body = []
    if not counted:
        body.append("no edit since last check — not counted; fix the listed "
                    "items, then re-check")
    if (res.get("verdict_detail") or {}).get("reason"):
        body.append(res["verdict_detail"]["reason"])
    if attempt:
        try:
            text = (Path(args.work) / "report.md").read_text(encoding="utf-8")
        except OSError:
            text = ""
        receipt = diagnostics(attempt, text, corpus)
        if args.as_json:
            body.append(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        else:
            body.append(summarize(attempt, text, corpus, args.offset, args.limit))
    elif proc.stderr:
        print(proc.stderr[-2000:], file=sys.stderr)
    out = "\n".join([head] + body + [head]) + "\n"
    try:
        last.parent.mkdir(parents=True, exist_ok=True)
        last.write_text(out, encoding="utf-8")
    except OSError:
        pass
    sys.stdout.write(out)
    if phase:
        end_repair_round(args.work, phase)
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
