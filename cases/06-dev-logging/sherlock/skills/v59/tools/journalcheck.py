#!/usr/bin/env python3
"""journalcheck — host-side provenance check of the checkpoint journal (v56, G1).

Runs OUTSIDE the sandbox, after the agent phase has ended, on the harness-
captured Qwen stream (`out/<phase>/stream.jsonl`). The agent can write any file
in its workspace, but it cannot rewrite output the harness already captured.

    python3 journalcheck.py --work RUN/work/work --stream RUN/out/*/stream.jsonl

Checks (each violation is blocking):
  journal_*            the in-sandbox verifier (checkpoint.verify_journal)
  receipt_unprinted    a journal row whose receipt no `checkpoint.py handoff`
                       call printed (forged or copied receipt)
  receipt_unjournaled  a printed receipt missing from the journal (deleted row)
  control_write        a tool call that writes a control file other than
                       through its owning tool (r1 repair stream line 867)

Exit 0 = clean, 1 = violations, 2 = usage / unreadable input.
"""
import argparse
import importlib.util
import json
import os
import re
import sys


def _sibling(name, _here=os.path.dirname(os.path.abspath(__file__))):
    path = os.path.join(_here, name + ".py")
    st = os.stat(path)
    key = "_sherlock_%s_%x_%x_%x" % (name, abs(hash(os.path.realpath(path))),
                                     st.st_ino, int(st.st_mtime_ns))
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


CK = _sibling("checkpoint")

#: control file -> the only tool allowed to write it
CONTROL = {
    "checkpoint.json": "checkpoint.py",
    "checkpoint.jsonl": "checkpoint.py",
    "handoff.txt": "checkpoint.py",
    "worklist.provenance.jsonl": "worklist.py",
    "worklist.manifest.json": "worklist.py",
}
WRITE_TOOLS = {"write_file", "edit", "replace", "write", "multiedit",
               "notebookedit", "str_replace_editor"}


def _write_patterns(fname):
    """Constructs that WRITE `fname` (reads such as `cat`, `2>&1`, `open(f)` are
    not writes). Heuristic by nature; the receipt checks are the hard proof."""
    f = r"[^\s'\"]*%s" % re.escape(fname)
    q = r"['\"]%s['\"]" % f
    return [
        re.compile(r"(?<![0-9&])>>?\s*%s(?![\w.-])" % f),              # > f, >> f
        re.compile(r"open\(\s*%s\s*,\s*['\"][wax]" % q),              # open('f','a')
        re.compile(r"Path\(\s*%s\s*\)\.write_(text|bytes)" % q),      # Path('f').write_text
        re.compile(r"\btee\b(\s+-a)?\s+%s" % f),                      # tee f
        re.compile(r"\bsed\s+-i\S*\s.*%s" % f),                       # sed -i ... f
        re.compile(r"\b(mv|cp|install)\b[^;&|\n]*\s%s(?![\w.-])\s*($|[;&|\n])" % f),
        re.compile(r"\b(rm|truncate)\b[^;&|\n]*%s" % f),
        re.compile(r"os\.(replace|rename)\([^)]*%s\s*\)" % q),
    ]


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(c.get("text", "") if isinstance(c, dict) else c)
                         for c in content)
    return ""


def read_stream(paths):
    """-> ([(stream, line, tool_use dict)], {tool_use_id: result text})."""
    uses, results = [], {}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, raw in enumerate(fh, 1):
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                msg = ev.get("message") if isinstance(ev, dict) else None
                if not isinstance(msg, dict):
                    continue
                for part in msg.get("content") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_use":
                        uses.append((path, n, part))
                    elif part.get("type") == "tool_result":
                        results[part.get("tool_use_id")] = _text(part.get("content"))
    return uses, results


def _command(use):
    inp = use.get("input") or {}
    return str(inp.get("command") or inp.get("cmd") or "")


def control_writes(uses):
    out = []
    for path, n, use in uses:
        name = str(use.get("name") or "").lower()
        inp = use.get("input") or {}
        target = os.path.basename(str(inp.get("file_path") or inp.get("path") or ""))
        if name in WRITE_TOOLS and target in CONTROL:
            out.append((path, n, "%s via %s tool" % (target, name)))
            continue
        cmd = _command(use)
        if not cmd:
            continue
        for fname, owner in CONTROL.items():
            if not re.search(r"(?<![\w.-])%s(?![\w.-])" % re.escape(fname), cmd):
                continue
            # the owning tool may write its own file; strip its invocations
            rest = re.sub(r"\S*%s\b[^;&|\n]*" % re.escape(owner), " ", cmd)
            if any(p.search(rest) for p in _write_patterns(fname)):
                out.append((path, n, "%s written by a shell command, not %s"
                            % (fname, owner)))
                break
    return out


def printed_receipts(uses, results):
    got = {}
    for _path, _n, use in uses:
        cmd = _command(use)
        if "checkpoint.py" not in cmd or "handoff" not in cmd:
            continue
        for m in CK.RECEIPT_LINE_RE.finditer(results.get(use.get("id"), "")):
            got[m.group(1)] = (m.group(2), int(m.group(3)))
    return got


def journal_rows(work):
    path = os.path.join(work, CK.HISTORY)
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            if raw.strip():
                try:
                    rows.append(json.loads(raw))
                except ValueError:
                    pass
    return rows


def check(work, streams):
    bad = [{"code": c, "detail": m} for c, m in CK.verify_journal(work)]
    uses, results = read_stream(streams)
    printed = printed_receipts(uses, results)
    rows = journal_rows(work)
    journaled = set()
    for row in rows:
        rid = row.get("handoff_receipt_id")
        if not rid:
            continue                      # already reported by verify_journal
        journaled.add(rid)
        want = printed.get(rid)
        if want is None:
            bad.append({"code": "receipt_unprinted",
                        "detail": "journal row boundary_seq=%r: receipt %s was never "
                                  "printed by checkpoint.py handoff"
                                  % (row.get("boundary_seq"), rid)})
        elif want != (row.get("handoff_sha256"), row.get("boundary_seq")):
            bad.append({"code": "receipt_mismatch",
                        "detail": "journal row boundary_seq=%r does not match its "
                                  "printed receipt" % row.get("boundary_seq")})
    for rid, (_sha, seq) in sorted(printed.items(), key=lambda kv: kv[1][1]):
        if rid not in journaled:
            bad.append({"code": "receipt_unjournaled",
                        "detail": "printed receipt %s (seq %d) is missing from "
                                  "checkpoint.jsonl" % (rid, seq)})
    for path, n, what in control_writes(uses):
        bad.append({"code": "control_write",
                    "detail": "%s:%d %s" % (os.path.basename(path), n, what)})
    return {"work": os.path.abspath(work), "streams": [os.path.abspath(s) for s in streams],
            "rows": len(rows), "printed_receipts": len(printed),
            "violations": bad, "blocking": len(bad)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--stream", action="append", default=[], required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    for s in a.stream:
        if not os.path.isfile(s):
            print("✗ no such stream: %s" % s)
            return 2
    res = check(a.work, a.stream)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for v in res["violations"]:
            print("✗ %s: %s" % (v["code"], v["detail"]))
        print("journalcheck: %d rows, %d printed receipts, %d blocking"
              % (res["rows"], res["printed_receipts"], res["blocking"]))
    return 1 if res["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
