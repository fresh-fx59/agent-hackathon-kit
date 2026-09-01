#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rollover.py — окно записей: сколько записей ПРОПАЛО внутри окна канала.

WHY THIS EXISTS. A Windows event channel is a ring buffer. When it wraps, the
oldest records are evicted and what the analyst holds is a WINDOW, not a
history. Nothing in the report used to say so, and nothing in the harness
checked — so «в журнале нет X» was written as if it meant «X не было».

The project has already been burned here. An auditing agent asserted «~402 000
записей вытеснено из Security.jsonl». That was WRONG. EventRecordID is
monotonic per channel, so one subtraction settles it: Security.jsonl runs
402275…437190 — a span of 34 916 — and the file holds exactly 34 916 records.
Nothing was lost inside that window. This tool is that subtraction, made
routine.

WHAT A "GAP" IS, AND WHAT IT IS NOT. missing = (max - min + 1) - unique ids.
It says one thing only: *this file does not contain every record of that
channel inside its own id window*. It does NOT say "eviction". Eviction
truncates the HEAD of a ring buffer, it does not punch interior holes — so an
interior hole is almost always a FILTERED EXPORT, not a wrap. Measured on
/home/claude-developer/hack/sherlock-corpora/evtx-attack-samples-jsonl (278
files, 282 channels): 84 channels in 84 files show holes, all of them handpicked
attack samples — 75 of the 84 hold fewer than 50 records (one is 15 records over
a span of 289 607). Cry wolf on those and the check gets disabled.

So the tool reports the FACT and lets the report state the consequence: inside
a window with holes, negative reasoning («такого события не было») is unsound,
whatever caused the holes.

PER CHANNEL, NEVER PER FILE. Several sample files merge Sysmon and Security
records into one file; their ids interleave and a per-file span is meaningless.
The unit is (file, Event.System.Channel).

STATUSES
  сплошной      — span == unique ids. Negative reasoning inside the window is sound.
  с-пропусками  — span > unique ids. State the loss.
  неприменимо   — empty, binary, not JSONL, or JSONL with no EventRecordID.
  ошибка        — unreadable, corrupt JSONL, non-integer id. NEVER "clean":
                  the grader counts every one of these as a blocking defect.

USAGE
    python3 rollover.py --corpus <LOG_DIR>                 # human table
    python3 rollover.py --corpus <LOG_DIR> --report >> report.md
    python3 rollover.py --corpus <LOG_DIR> --json
"""
import argparse
import gzip
import json
import os
import re
import sys

SAMPLE_LINES = 20      # how many NON-COMMENT lines to sniff before "not JSONL"
COMMENT_RE = re.compile(r"^\s*(?:#|//|;)")   # a header banner is not a verdict
NBSP = " "
NNBSP = " "
THINSP = " "

OK = "сплошной"
GAP = "с-пропусками"
NA = "неприменимо"
ERR = "ошибка"


def _record_id(obj):
    """EventRecordID wherever the shipping renderers put it."""
    if not isinstance(obj, dict):
        return None
    ev = obj.get("Event")
    if isinstance(ev, dict):
        sysblk = ev.get("System")
        if isinstance(sysblk, dict) and sysblk.get("EventRecordID") is not None:
            return sysblk.get("EventRecordID")
    for key in ("EventRecordID", "RecordNumber", "record_id", "RecordId"):
        if obj.get(key) is not None:
            return obj.get(key)
    return None


def _channel(obj):
    if not isinstance(obj, dict):
        return "-"
    ev = obj.get("Event")
    if isinstance(ev, dict):
        sysblk = ev.get("System")
        if isinstance(sysblk, dict) and sysblk.get("Channel"):
            return str(sysblk.get("Channel"))
    for key in ("Channel", "channel"):
        if obj.get(key):
            return str(obj.get(key))
    return "-"


def _as_int(value):
    """Ids arrive as int and as "417258"; one renderer writes "0x65e2a".

    Anything else is an ERROR, not a skip: an id we cannot read is an id we
    cannot prove present, and guessing is how a check fails open.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        s = value.strip().replace(NBSP, "").replace(NNBSP, "").replace(THINSP, "")
        # A space is a THOUSANDS SEPARATOR, never filler: «1 234» is 1234, but
        # «1 0» is not 10 — stripping every space made a typo look like a number.
        if " " in s:
            if not re.fullmatch(r"\d{1,3}(?: \d{3})+", s):
                return None
            s = s.replace(" ", "")
        try:
            return int(s, 16) if s.lower().startswith("0x") else int(s)
        except ValueError:
            return None
    return None


def _is_gzip(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _looks_binary(path):
    """Gzip is NOT «binary» here. citecheck's own index docstring records a
    production corpus that shipped `.gz`; calling those «формат=двоичный» made
    a real gap disappear behind «неприменимо» — a fail-OPEN in a fail-closed
    check. Decompress and scan them like any other JSONL."""
    if _is_gzip(path):
        return False
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(8192)
    except OSError:
        return False


def _open_text(path):
    if _is_gzip(path):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def _na(rel, detail):
    return {"path": rel, "channel": "-", "status": NA, "lo": None, "hi": None,
            "records": 0, "missing": 0, "rows": 0, "without_id": 0,
            "detail": detail}


def _err(rel, detail):
    return {"path": rel, "channel": "-", "status": ERR, "lo": None, "hi": None,
            "records": 0, "missing": 0, "rows": 0, "without_id": 0,
            "detail": detail}


def scan_file(path, rel):
    """-> list of entries for one file. Never raises: an exception is an entry."""
    try:
        if os.path.getsize(path) == 0:
            return [_na(rel, "байт=0")]
        if _looks_binary(path):
            return [_na(rel, "формат=двоичный")]
    except OSError as exc:
        return [_err(rel, "ошибка=stat:%s" % (getattr(exc, "errno", "io") or "io"))]

    per = {}            # channel -> {"ids": set, "rows": int, "noid": int}
    parsed = 0
    seen = 0
    sniffed = 0
    try:
        with _open_text(path) as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                seen += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    if parsed:
                        # Already proven to be JSONL, so this line is damage,
                        # not a different format. An id may be hiding in it.
                        return [_err(rel, "ошибка=json:строка-%d" % seen)]
                    if COMMENT_RE.match(line):
                        continue      # a banner is not evidence of a format
                    sniffed += 1
                    if sniffed >= SAMPLE_LINES:
                        return [_na(rel, "формат=не-jsonl")]
                    continue
                parsed += 1
                rid = _record_id(obj)
                bucket = per.setdefault(_channel(obj),
                                        {"ids": set(), "rows": 0, "noid": 0})
                bucket["rows"] += 1
                if rid is None:
                    bucket["noid"] += 1
                    continue
                n = _as_int(rid)
                if n is None:
                    return [_err(rel, "ошибка=id:строка-%d" % seen)]
                bucket["ids"].add(n)
    except OSError as exc:
        return [_err(rel, "ошибка=io:%s" % (getattr(exc, "errno", "io") or "io"))]
    except Exception as exc:                       # fail closed, never clean
        return [_err(rel, "ошибка=исключение:%s" % type(exc).__name__)]

    if not parsed:
        return [_na(rel, "формат=не-jsonl")]
    live = {ch: b for ch, b in per.items() if b["ids"]}
    mute = {ch: b for ch, b in per.items() if not b["ids"]}
    if not per:
        return [_na(rel, "поле=нет-EventRecordID")]

    out = []
    # A channel with records but NO EventRecordID used to be dropped on the
    # floor: not a row, not «неприменимо», not an error — a file could hold
    # 1 000 records of channel B and the report would never mention B. Silence
    # is the one thing this check exists to stop, so say it out loud.
    for ch in sorted(mute):
        e = _na(rel, "поле=нет-EventRecordID")
        e["channel"] = ch
        e["rows"] = mute[ch]["rows"]
        e["without_id"] = mute[ch]["noid"]
        out.append(e)
    for ch in sorted(live):
        ids = live[ch]["ids"]
        lo, hi = min(ids), max(ids)
        span = hi - lo + 1
        missing = span - len(ids)
        out.append({"path": rel, "channel": ch, "status": GAP if missing else OK,
                    "lo": lo, "hi": hi, "records": len(ids), "missing": missing,
                    "rows": live[ch]["rows"], "without_id": live[ch]["noid"],
                    "detail": ""})
    return out


def scan_corpus(root):
    entries = []
    files = 0
    # followlinks=True on purpose: a symlinked sub-directory inside the corpus
    # used to be neither scanned nor counted in «файлов» — invisible, which is
    # the failure mode this whole check exists to remove. `seen_dirs` keeps the
    # walk finite when a link points at an ancestor.
    seen_dirs = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(real)
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__", ".venv")]
        for name in sorted(filenames):
            ap = os.path.join(dirpath, name)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            files += 1
            try:
                entries.extend(scan_file(ap, rel))
            except Exception as exc:               # belt and braces: fail closed
                entries.append(_err(rel, "ошибка=исключение:%s" % type(exc).__name__))
    entries.sort(key=lambda e: (e["path"], e["channel"]))
    return summarize(entries, files)


def summarize(entries, files):
    contiguous = sum(1 for e in entries if e["status"] == OK)
    gapped = sum(1 for e in entries if e["status"] == GAP)
    na = sum(1 for e in entries if e["status"] == NA)
    errors = sum(1 for e in entries if e["status"] == ERR)
    return {"files": files, "entries": entries,
            "channels": contiguous + gapped, "contiguous": contiguous,
            "gapped": gapped, "na": na, "errors": errors,
            "lost": sum(e["missing"] for e in entries)}


# --- the exact strings the report must carry, and the grader re-derives ------
SUMMARY_FMT = ("итог: файлов=%(files)d каналов=%(channels)d сплошных=%(contiguous)d "
               "с-пропусками=%(gapped)d неприменимо=%(na)d ошибок=%(errors)d")


def row_for(e):
    """One table row. `записей` is the count of UNIQUE ids, which is what the
    span arithmetic is about; `rows` (physical lines) can be larger when a file
    repeats an id, and is reported separately, never here."""
    return "| %s | %s | окно=%d–%d | записей=%d | нет=%d |" % (
        cell_safe(_norm_path(e["path"])), cell_safe(e["channel"]),
        e["lo"], e["hi"], e["records"], e["missing"])


# A markdown cell cannot carry `|` or a newline — and a CHANNEL NAME COMES FROM
# LOG DATA, i.e. from the adversary in a forensics tool. An unescaped `|` used to
# split one row into extra cells, so the grader read back a different channel than
# the producer wrote and the run wedged: permanent `undeclared` + `spurious`, with
# no report the producer could emit to satisfy it. The escape is injective —
# backslash is escaped too — so distinct channels never collapse onto one key.
_CELL_ESCAPES = {"\\": "\\5c", "|": "\\7c", "\r": "\\0d", "\n": "\\0a"}


def cell_safe(text):
    """Text -> a string safe inside a markdown cell. Injective; see above."""
    return "".join(_CELL_ESCAPES.get(c, c) for c in (text or ""))


def _norm_path(p):
    """A path as it comes off the DISK: separators unified, no leading «./»."""
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _norm_cell_path(p):
    """A path as it comes out of a REPORT CELL — already escaped, never re-escape.

    In particular a backslash here is part of an escape («\\7c»), not a Windows
    separator, so this must NOT do the backslash->slash rewrite `_norm_path` does.
    """
    p = (p or "").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def key_of(path, channel):
    """Canonical key for a DISK entry — exactly what `row_for` writes in the row."""
    return (cell_safe(_norm_path(path)), cell_safe((channel or "-").strip()))


def key_of_cells(path_cell, channel_cell):
    """Canonical key for a REPORT ROW. The cells are already escaped."""
    return (_norm_cell_path(path_cell), (channel_cell or "-").strip())


def required_keys(scan, cited_paths=()):
    """Which (path, channel) rows the report OWES.

    Two sets, and deliberately no more:
      * every channel with a gap — the fact that changes what the report may
        conclude;
      * every channel of a file a FINDING cites — the anchor case: the wrong
        «402 000 evicted» claim was about the very channel the findings rest on.

    NOT every channel. The coverage table already demands one row per corpus
    file, and whether that survives a 10 000-file corpus is an open question in
    this project; a second full-corpus table would double a cost already in
    doubt.

    HOW BIG THIS GETS, measured, not guessed: on the clean 143-file winevtx
    corpus with the recorded v37 report it is 6 rows (0 gaps + 6 finding
    channels). It is NOT corpus-independent: on
    /home/claude-developer/hack/sherlock-corpora/evtx-attack-samples-jsonl
    (278 files, 282 channels) 84 channels are gapped, so a report there owes
    84 rows before a single finding. Those 84 are handpicked exports — 75 of
    them hold fewer than 50 records — so the bound is findings + gaps, and
    gaps do scale with the corpus.
    """
    want = {}
    cited = {_norm_path(p) for p in cited_paths}
    for e in scan["entries"]:
        if e["status"] not in (OK, GAP):
            continue
        if e["status"] == GAP or _norm_path(e["path"]) in cited:
            want[key_of(e["path"], e["channel"])] = e
    return want


def render(scan, only_required=False, cited_paths=()):
    out = [SUMMARY_FMT % scan]
    if only_required:
        want = required_keys(scan, cited_paths)
        rows = [e for e in scan["entries"]
                if key_of(e["path"], e["channel"]) in want]
    else:
        rows = [e for e in scan["entries"] if e["status"] in (OK, GAP)]
    if rows:
        out.append("")
        out.append("| путь | канал | окно | записей | нет |")
        out.append("| --- | --- | --- | --- | --- |")
        out.extend(row_for(e) for e in rows)
    bad = [e for e in scan["entries"] if e["status"] == ERR]
    if bad:
        out.append("")
        out.append("ОШИБКИ СКАНИРОВАНИЯ (каждая блокирует):")
        out.extend("  %s — %s" % (e["path"], e["detail"]) for e in bad)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Окно записей канала: span(EventRecordID) против числа записей.")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="печатать готовый раздел «Окно записей» для отчёта")
    ap.add_argument("--required-only", action="store_true",
                    help="только строки, которых требует гейт")
    ap.add_argument("--cite", action="append", default=[],
                    help="путь файла, на который ссылается находка; повторяемо")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.corpus):
        sys.exit("rollover.py: нет такого каталога: %s" % args.corpus)
    scan = scan_corpus(args.corpus)
    if args.json:
        print(json.dumps(scan, ensure_ascii=False, indent=1))
    else:
        if args.report:
            print("# Окно записей")
            print("")
        print(render(scan, args.required_only, args.cite))
    return 1 if scan["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
