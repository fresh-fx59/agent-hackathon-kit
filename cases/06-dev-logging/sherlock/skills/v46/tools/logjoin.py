#!/usr/bin/env python3
"""logjoin — follow one identifier across every file, and name where it ISN'T.

    python3 logjoin.py ORD-77421 --corpus ./logs
    python3 logjoin.py c-8f3a2b91 10.42.12.31 --corpus ./logs --json

Why this exists. Every "measured" note here is a POINTER: the numbers, the
corpus and the date live in `skills/DESIGN-EVIDENCE.md` in the repo, which is
deliberately not copied into a workspace with the skill.

Measured: the skill's coverage problem is fixed — most files of a benchmark
corpus cited, against none without the skill — but recall stayed at a fraction
of its bar. The remaining gap is **multi-hop depth**: the model walks wide and
shallow because each extra hop costs another round of grep-and-read, and the
context budget punishes it. This is the hop, done once, cheaply. EVIDENCE §E29.

Three things it does that a plain grep does not:

1. **Canonicalises the spelling.** The same id is `ORD-77421` in the app log and
   `ord_77421` at the gateway. Case and `-`/`_`/`.` are folded by default;
   `--no-canon` turns that off.
2. **Reports absence.** `absent_in` lists the files the id never appears in.
   Measured: on one benchmark corpus the decisive evidence for a whole card was
   an entity missing where it had to be — and a model cannot notice a thing that
   is not there. Absence has to be handed to it. EVIDENCE §E29.
3. **Refuses invented edges.** Give it two ids and it answers, corpus-wide,
   whether any single line contains both. One run asserted a pod↔IP relationship
   that has zero co-occurrences anywhere in the corpus: two real citations
   bridged by a fabricated edge. `verdict: not-in-corpus` is that guard.

Boundary-aware by default (`7742` does not match `ORD-77421`); `--substring`
opts out. Streams every file, holds nothing but the hits. Stdlib only, zero
config (AGENTS.md R1). Exit 0 if every id was found somewhere, 1 if any id is
absent from the whole corpus.

Hits are reported as RECORDS, not lines. A record is often several physical
lines — a stack trace, a goroutine dump, an 18-line journald block — and a
citation to its first line alone is not a citation to the evidence. When
`logmap.py` sits next to this file, its framing detector is reused and every hit
comes back as `file:N` or `file:N-M`, which is the form the checker accepts.
Without it the tool degrades to one line per record and says so.
"""
import argparse
import gzip
import json
import os
import re
import sys
import time

TS_PATTERNS = [
    ("iso", re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
                       r"(?:Z|[+-]\d{2}:?\d{2})?")),
    ("clf", re.compile(r"\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}")),
    ("logcat", re.compile(r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{1,6}")),
    ("bsd-syslog", re.compile(r"[A-Z][a-z]{2} [ \d]?\d \d{2}:\d{2}:\d{2}")),
    ("epoch-ms", re.compile(r"(?<![0-9.])1[0-9]{12}(?![0-9])")),
    ("time-only", re.compile(r"(?<![0-9:])\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?(?![0-9])")),
]

SEP = r"[-_.:/]?"
MAX_SAMPLE = 3
SAMPLE_CHARS = 220

# Framing comes from logmap when it is installed alongside; the tool must still
# run on its own, so the import is optional and its absence is REPORTED, never
# silently swallowed (a silent degrade is how this project loses defects).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import logmap as _logmap
except Exception:                                     # noqa: BLE001 - optional
    _logmap = None
try:
    import statecheck as _statecheck
except Exception:                                     # noqa: BLE001 - optional
    _statecheck = None


def ref(rel, start, end):
    return "%s:%d" % (rel, start) if end <= start else "%s:%d-%d" % (rel, start, end)


def records_of(path, rel):
    """-> iterator of (start_line, end_line, text). Falls back to one per line."""
    if _logmap is not None:
        try:
            # Take the framing by POSITION, not by unpacking the whole tuple. It
            # was unpacked whole, so the day logmap.probe() grew a fifth return
            # value every citation here silently collapsed from a record range to
            # a single line — the `except` below swallowed the arity mismatch and
            # the fallback looks exactly like a log that has no multi-line records.
            framing = _logmap.probe(path)[0]
            return _logmap.stream_records(path, framing), framing
        except Exception:                             # noqa: BLE001
            pass
    def by_line():
        with opener(path)(path, "rt", encoding="utf-8", errors="replace") as fh:
            for n, raw in enumerate(fh, 1):
                yield n, n, raw.rstrip("\n")
    return by_line(), "line"


def find_ts(line):
    for name, rx in TS_PATTERNS:
        m = rx.search(line)
        if m:
            return name, m.group(0)
    return None, None


def build_pattern(ident, canon=True, substring=False):
    """One id -> one regex covering its plausible respellings."""
    if canon:
        parts = [re.escape(p) for p in re.split(r"[-_.\s]+", ident) if p]
        body = SEP.join(parts)
        flags = re.IGNORECASE
    else:
        body = re.escape(ident)
        flags = 0
    if substring:
        return re.compile(body, flags)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", flags)


def opener(path):
    return gzip.open if path.endswith(".gz") else open


SKIPPED_BINARY = []


def looks_binary(path):
    """A NUL byte in the first 8 KB.  Same test as logmap.py, so all three tools
    agree on what "unreadable as text" means.

    WHY THIS EXISTS (2026-08-18).  Both readers below used to open every file in
    text mode with errors="replace", which never fails: an .evtx, a .pcap or a PE
    decodes into mojibake and a citation into it could be reported `ok` against a
    line that does not exist as text.  The gate that exists to stop fabrication
    was able to launder it.  Binary evidence must be RENDERED to text first
    (prepare-corpus.sh) and cited there.
    """
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True


def walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__")]
        for fn in sorted(filenames):
            ap = os.path.join(dirpath, fn)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            if looks_binary(ap):
                SKIPPED_BINARY.append(rel)
                continue
            out.append((ap, rel))
    return sorted(out, key=lambda t: t[1])


def scan(files, patterns, max_hits):
    """One pass over the corpus for ALL ids at once.

    Per file per id: line numbers (capped), true hit count, first/last timestamp
    of the MATCHING lines, and a couple of sample lines. Plus, for every pair of
    ids, the lines where both occur — the corpus-wide co-occurrence check."""
    per_id = {i: {} for i in range(len(patterns))}
    pairs = {}
    for a in range(len(patterns)):
        for b in range(a + 1, len(patterns)):
            pairs[(a, b)] = {}
    for path, rel in files:
        try:
            stream, framing = records_of(path, rel)
        except OSError as e:
            sys.stderr.write("! %s: %s\n" % (rel, e))
            continue
        try:
            for start, end, line in stream:
                hit_here = []
                for i, rx in enumerate(patterns):
                    if not rx.search(line):
                        continue
                    hit_here.append(i)
                    e = per_id[i].setdefault(
                        rel, {"path": rel, "hits": 0, "lines": [], "refs": [],
                              "sample": [], "first_ts": None, "last_ts": None,
                              "time_format": None, "framing": framing,
                              "truncated": False})
                    e["hits"] += 1
                    if len(e["lines"]) < max_hits:
                        e["lines"].append(start)
                        e["refs"].append(ref(rel, start, end))
                        if len(e["sample"]) < MAX_SAMPLE:
                            e["sample"].append(line.strip()[:SAMPLE_CHARS])
                    else:
                        e["truncated"] = True
                    fmt, ts = find_ts(line)
                    if ts:
                        if e["first_ts"] is None:
                            e["first_ts"], e["time_format"] = ts, fmt
                        e["last_ts"] = ts
                for a in range(len(hit_here)):
                    for b in range(a + 1, len(hit_here)):
                        key = (hit_here[a], hit_here[b])
                        d = pairs[key].setdefault(rel, {"path": rel, "lines": [],
                                                        "refs": []})
                        if len(d["lines"]) < max_hits:
                            d["lines"].append(start)
                            d["refs"].append(ref(rel, start, end))
                        d["hits"] = d.get("hits", 0) + 1
        except OSError as e:
            sys.stderr.write("! %s: %s\n" % (rel, e))
    return per_id, pairs


def run(ids, root, canon=True, substring=False, max_hits=20):
    files = walk(root)
    patterns = [build_pattern(i, canon, substring) for i in ids]
    per_id, pairs = scan(files, patterns, max_hits)

    all_rel = [rel for _ap, rel in files]
    out_ids = []
    for i, ident in enumerate(ids):
        hits = per_id[i]
        entry = {
            "id": ident,
            "pattern": patterns[i].pattern,
            "total_hits": sum(f["hits"] for f in hits.values()),
            "files": [hits[r] for r in all_rel if r in hits],
            "absent_in": [r for r in all_rel if r not in hits],
            "not_searched_binary": list(SKIPPED_BINARY),
        }
        out_ids.append(entry)

    co = []
    for (a, b), d in sorted(pairs.items()):
        hits = sum(f["hits"] for f in d.values())
        co.append({
            "ids": [ids[a], ids[b]],
            "hits": hits,
            "files": [d[r] for r in all_rel if r in d],
            "verdict": "confirmed" if hits else "not-in-corpus",
            "note": ("подтверждено корпусом: обе сущности встречаются в одной строке"
                     if hits else
                     "СВЯЗЬ НЕ ПОДТВЕРЖДЕНА КОРПУСОМ: ни одной строки, где есть обе "
                     "сущности. Не строй на этой связи вывод."),
        })
    return {"corpus": os.path.abspath(root), "files_scanned": len(files),
            "canon": canon, "substring": substring,
            "framing_available": _logmap is not None,
            "per_id": out_ids, "cooccurrence": co}


def render(d):
    out = []
    if not d.get("framing_available", True):
        out.append("! logmap.py рядом не найден — ссылки даны по одной строке, "
                   "многострочная запись НЕ свёрнута в диапазон")
    for e in d["per_id"]:
        out.append("%s — %d совпадений в %d файлах из %d"
                   % (e["id"], e["total_hits"], len(e["files"]), d["files_scanned"]))
        for f in e["files"]:
            span = ("%s → %s" % (f["first_ts"], f["last_ts"])
                    if f["first_ts"] else "время не распознано")
            refs = ", ".join(f.get("refs") or
                             ["%s:%d" % (f["path"], n) for n in f["lines"]])
            out.append("  %-28s %3d стр.  %s" % (f["path"], f["hits"], span))
            out.append("    %s%s" % (refs, " …" if f["truncated"] else ""))
            for s in f["sample"]:
                out.append("    | %s" % s)
        if e["absent_in"]:
            out.append("  ОТСУТСТВУЕТ в: %s" % ", ".join(e["absent_in"]))
        if e.get("not_searched_binary"):
            out.append("  НЕ ИСКАЛ (двоичные, нужен рендер): %s"
                       % ", ".join(e["not_searched_binary"]))
            out.append("    (отсутствие — тоже улика: проверь, должен ли он был "
                       "там быть)")
        out.append("")
    for c in d["cooccurrence"]:
        out.append("связь %s ↔ %s: %s (%d строк)"
                   % (c["ids"][0], c["ids"][1], c["verdict"], c["hits"]))
        for f in c["files"]:
            out.append("  %s" % ", ".join(
                f.get("refs") or ["%s:%d" % (f["path"], n) for n in f["lines"]]))
        out.append("  %s" % c["note"])
    return "\n".join(out)



# --- --window: sessions, and what changed inside them ------------------------
#
# Why this mode exists. The corpus's decisive fact was not a record, it was an
# INTERVAL: an inbound RDP session, and twelve minutes into it a service
# install. Both halves were in the corpus, in two different channels, and the
# only thing joining them was a model noticing two timestamps. Measured: neither
# arm noticed unaided under v31.
#
# So the join is done here, deterministically. Sessions come from the two places
# Windows records them; the changes come from `statecheck`'s catalogue, which is
# the same census the report must answer to. No severity words, no ranking — an
# interval and a membership test.
SESSION_OPEN = {
    ("Microsoft-Windows-TerminalServices-LocalSessionManager", 21): "вход в сеанс",
    ("Microsoft-Windows-TerminalServices-LocalSessionManager", 25): "переподключение",
}
SESSION_CLOSE = {
    ("Microsoft-Windows-TerminalServices-LocalSessionManager", 23): "выход",
    ("Microsoft-Windows-TerminalServices-LocalSessionManager", 24): "отключение",
}
# 4624 logon types that mean a human at a keyboard or a remote desktop:
# 2 interactive, 7 unlock, 10 RemoteInteractive, 11 cached interactive.
INTERACTIVE_LOGON_TYPES = (2, 7, 10, 11)
SESSION_MAX_S = 12 * 3600     # an unclosed session is not an open-ended licence
PRIVATE_RE = re.compile(
    r"^(?:127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|::1$|fe80:|-$|$)")


def _stamp(ts):
    y, mo, d, h, mi, sec = time.gmtime(ts)[:6]
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (y, mo, d, h, mi, sec)


def routable(addr):
    """A source address that came from outside this machine's own network."""
    if not addr:
        return False
    return not PRIVATE_RE.match(str(addr).strip())


def _walk_json(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__")]
        for fn in sorted(filenames):
            ap = os.path.join(dirpath, fn)
            if looks_binary(ap):
                continue
            yield ap, os.path.relpath(ap, root).replace(os.sep, "/")


def _event_data(ev):
    for key in ("EventData", "UserData"):
        d = ev.get(key)
        if isinstance(d, dict):
            if key == "UserData":
                inner = d.get("EventXML")
                if isinstance(inner, dict):
                    d = inner
            flat = {k: v for k, v in d.items()
                    if isinstance(v, (str, int, float))}
            if flat:
                return flat
    return {}


def sessions_of(root):
    """-> list of session dicts, each with a window and where it is written."""
    if _statecheck is None:
        return []
    opens, closes = [], []
    for path, rel in _walk_json(root):
        try:
            fh = opener(path)(path, "rt", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw.startswith("{"):
                    continue
                try:
                    ev = json.loads(raw).get("Event")
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                system = ev.get("System")
                if not isinstance(system, dict):
                    continue
                eid = _statecheck._eventid(system)
                prov = _statecheck._provider(system)
                ts = _statecheck.record_time(system)
                if eid is None or ts is None:
                    continue
                data = _event_data(ev)
                key = (prov, eid)
                if key in SESSION_OPEN:
                    opens.append({"file": rel, "line": n, "ts": ts,
                                  "kind": SESSION_OPEN[key],
                                  "user": str(data.get("User") or "-"),
                                  "address": str(data.get("Address") or "-"),
                                  "handle": "session:%s" % data.get("SessionID")})
                elif key in SESSION_CLOSE:
                    closes.append({"ts": ts, "line": n, "file": rel,
                                   "handle": "session:%s" % data.get("SessionID")})
                elif eid == 4624 and prov == "Microsoft-Windows-Security-Auditing":
                    try:
                        lt = int(data.get("LogonType"))
                    except (TypeError, ValueError):
                        continue
                    if lt not in INTERACTIVE_LOGON_TYPES:
                        continue
                    opens.append({"file": rel, "line": n, "ts": ts,
                                  "kind": "вход 4624 тип %d" % lt,
                                  "user": str(data.get("TargetUserName") or "-"),
                                  "address": str(data.get("IpAddress") or "-"),
                                  "handle": "logon:%s" % data.get("TargetLogonId")})
                elif eid == 4634 and prov == "Microsoft-Windows-Security-Auditing":
                    closes.append({"ts": ts, "line": n, "file": rel,
                                   "handle": "logon:%s" % data.get("TargetLogonId")})

    closes.sort(key=lambda c: c["ts"])
    out = []
    for o in sorted(opens, key=lambda x: x["ts"]):
        end = None
        for c in closes:
            if c["handle"] == o["handle"] and c["ts"] >= o["ts"]:
                end = c
                break
        o["end"] = end
        o["end_ts"] = end["ts"] if end else o["ts"] + SESSION_MAX_S
        o["closed"] = end is not None
        o["routable"] = routable(o["address"])
        o["start_text"] = _stamp(o["ts"])
        o["end_text"] = _stamp(o["end_ts"])
        out.append(o)
    return out


def window_join(root, only_routable=True):
    """Sessions × state changes. -> the join, as data."""
    sessions = sessions_of(root)
    changes = []
    if _statecheck is not None:
        for g in _statecheck.census(root):
            for rec in g["records"]:
                if rec["ts"] is None:
                    continue
                changes.append({"file": g["file"], "line": rec["line"],
                                "ts": rec["ts"], "class": g["class"],
                                "actor": g["actor"], "eventid": rec["eventid"],
                                "subject": rec["subject"]})
    changes.sort(key=lambda c: c["ts"])
    rows = []
    for s in sessions:
        if only_routable and not s["routable"]:
            continue
        inside = [dict(c, after_s=c["ts"] - s["ts"]) for c in changes
                  if s["ts"] <= c["ts"] <= s["end_ts"]]
        rows.append({"session": s, "changes": inside,
                     "groups": [{"class": b["class"], "actor": b["actor"],
                                 "n": b["n"],
                                 "first": "%s:%d" % (b["first"]["file"], b["first"]["line"]),
                                 "after_s": b["first"]["after_s"],
                                 "subject": b["first"]["subject"]}
                                for b in group_changes(inside)]})
    return {"corpus": os.path.abspath(root),
            "sessions_total": len(sessions),
            "sessions_reported": len(rows),
            "state_changes_total": len(changes),
            "only_routable": only_routable,
            "windows": rows,
            "statecheck_available": _statecheck is not None}


def _hhmm(seconds):
    seconds = int(seconds)
    if seconds < 3600:
        return "%d мин" % (seconds // 60)
    return "%d ч %02d мин" % (seconds // 3600, (seconds % 3600) // 60)


WINDOW_GROUPS_SHOWN = 12
WINDOW_MEMBERS_SHOWN = 3


def group_changes(changes):
    """Collapse a window's changes the same way `statecheck` collapses a corpus.

    A first RDP logon registers several hundred firewall rules in the same
    second, all of them by the platform. Printed one per line that burst buries
    the single record that matters. Grouped by (class, actor) it is one line,
    and — rarest group first — the odd one is at the top where it belongs.
    """
    buckets = {}
    for c in changes:
        key = (c["class"], c["actor"])
        b = buckets.setdefault(key, {"class": c["class"], "actor": c["actor"],
                                     "n": 0, "first": c, "members": []})
        b["n"] += 1
        if len(b["members"]) < WINDOW_MEMBERS_SHOWN:
            b["members"].append(c)
    return sorted(buckets.values(), key=lambda b: (b["n"], b["first"]["ts"]))


def render_window(d):
    out = []
    if not d["statecheck_available"]:
        return ["! statecheck.py рядом не найден — окна построить нечем"]
    out.append("сеансов всего: %d, показано: %d%s; изменений состояния в корпусе: %d"
               % (d["sessions_total"], d["sessions_reported"],
                  " (только маршрутизируемые адреса)" if d["only_routable"] else "",
                  d["state_changes_total"]))
    out.append("группы внутри окна — от самой редкой к самой частой; редкая группа "
               "и есть повод смотреть")
    if not d["windows"]:
        out.append("ни одного сеанса с внешнего адреса не найдено")
    for w in d["windows"]:
        s = w["session"]
        out.append("")
        out.append("%s · %s · %s · %s → %s%s"
                   % (s["kind"], s["user"], s["address"], s["start_text"],
                      ref(s["file"], s["line"], s["line"]),
                      "" if s["closed"] else "  (закрытие не найдено, окно ограничено 12 ч)"))
        groups = group_changes(w["changes"])
        if not groups:
            out.append("  изменений состояния внутри сеанса нет")
        for b in groups[:WINDOW_GROUPS_SHOWN]:
            out.append("  %-22s ×%-4d субъект %s" % (b["class"], b["n"], b["actor"]))
            for c in b["members"]:
                out.append("    +%-9s %s  %s"
                           % (_hhmm(c["after_s"]), ref(c["file"], c["line"], c["line"]),
                              c["subject"]))
            if b["n"] > len(b["members"]):
                out.append("    … ещё %d записей этой же группы"
                           % (b["n"] - len(b["members"])))
        if len(groups) > WINDOW_GROUPS_SHOWN:
            out.append("  … ещё %d групп не показано"
                       % (len(groups) - WINDOW_GROUPS_SHOWN))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Проследить идентификатор по всем файлам корпуса: где есть, "
                    "в каких строках, в каком временном окне — и где его НЕТ.")
    ap.add_argument("ids", nargs="*", help="id заказа, correlation_id, IP, что угодно")
    ap.add_argument("--window", action="store_true",
                    help="не искать id, а построить окна сеансов и сложить в них "
                         "изменения состояния (нужен statecheck.py рядом)")
    ap.add_argument("--all-addresses", action="store_true",
                    help="--window: показывать и локальные адреса тоже")
    ap.add_argument("--corpus", default=".", help="корень корпуса логов")
    ap.add_argument("--no-canon", dest="canon", action="store_false",
                    help="искать буквально, без сворачивания регистра и -_.")
    ap.add_argument("--substring", action="store_true",
                    help="разрешить совпадение внутри слова (по умолчанию нет)")
    ap.add_argument("--max-hits", type=int, default=20,
                    help="сколько номеров строк показывать на файл (счётчик точный)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    if args.window:
        w = window_join(args.corpus, not args.all_addresses)
        print(json.dumps(w, ensure_ascii=False, indent=1) if args.json
              else "\n".join(render_window(w)))
        return 0 if w["statecheck_available"] else 2
    if not args.ids:
        sys.exit("нужен хотя бы один id, либо --window")
    d = run(args.ids, args.corpus, args.canon, args.substring, args.max_hits)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(d))
    return 1 if any(e["total_hits"] == 0 for e in d["per_id"]) else 0


if __name__ == "__main__":
    sys.exit(main())
