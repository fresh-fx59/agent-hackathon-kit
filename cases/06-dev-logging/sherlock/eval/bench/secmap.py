#!/usr/bin/env python3
"""secmap.py — the SECURITY Step 1: a map and a working list for intrusion evidence.

    python3 secmap.py <CORPUS_DIR> --out ./work

Emits the same three files, with the same column contract, as the dev-log
`logmap.py`, so every downstream rule of the skill (§5 verdicts, §7
`citecheck --ledger`, §8 stopping condition) keeps working unchanged:

    work/map.txt        per-file summary + the axes that were derived
    work/worklist.tsv   <=250 rows, every one starting at verdict `?`
    work/axis3.tsv      tempo: what moved and what did not

WHY THIS EXISTS — measured, 2026-08-18, on the BlueSky DFIR corpus
------------------------------------------------------------------
`logmap.py` runs on this corpus without error and inverts the answer: its
172-row working list contains ZERO rows touching the intrusion (18456, 15457,
xp_cmdshell, MSFConsole, the Defender snooze) and eleven rows nominating benign
providers (Search, VMTools, edgeupdate, VSS, EventSystem, Complus, MSDTC, User
Profiles, WMI).

The cause is field extraction, not statistics.  `logmap.py` derives its axes
from FLAT JSON keys (`json:Channel`, `json:Name`, `json:EventID`).  Every
DFIR-relevant field in a Windows event sits one level deeper:

  * inside an `EventData` array whose meaning is POSITIONAL
    (`["xp_cmdshell", "0", "1"]` — a configuration flip, 2 records in 90,736);
  * inside a `\\t`-delimited `key=value` blob in a payload string
    (`HostName=MSFConsole`, `HostApplication=winlogon.exe` — 9 records each,
    minority values of a 4- and an 8-value axis).

So secmap explodes those two shapes into first-class fields and then applies
ordinary rare-value logic.  NOTHING here is a vocabulary: there is no list of
event ids, no ATT&CK table, no Sigma pack.  That is deliberate — the arm's own
answer key IS the event-id list, so a tool that shipped one would print the
answer key and make any recall gain unfalsifiable.

THE FIVE AXES
-------------
T  state transition   an EventData tuple carrying `<name>, <n>, <m>` with n != m
F  rare field value   rare value of an exploded field (positional or key=value)
P  peer x identity     one globally-routable peer seen with one identity under
                      >= 2 distinct event ids — i.e. the failure->success shape
S  port fan-out       one peer touching many distinct destination ports
O  numeric outlier    a numeric column whose top value dwarfs its own median

Row ids carry the axis letter, so the report can say which axis found what.
"""
import argparse
import collections
import gzip
import ipaddress
import json
import os
import re
import sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".idea", ".vscode"}
MAX_ROWS = 250
SMALL_FILE_BYTES = 4096
SMALL_TABLE_ROWS = 200
RECORD_SNIPPET = 400
AXIS_MAX_CARDINALITY = 50          # more distinct values than this is an id, not an axis
RARE_SHARE = 0.005                 # <= 0.5% of a file's records is rare
PORT_FANOUT = 50                   # distinct destination ports that make a sweep
F_ROWS_PER_FILE = 6                # one noisy file may not eat the whole budget
AXIS_PRIORITY = {"T": 0, "C": 1, "P": 2, "S": 3, "B": 4, "O": 5, "V": 6, "F": 7}
SHAPE_BURST = 20                   # identical-shape rows that make a burst
NUMERIC_SAMPLES = 2000             # median from a sample: memory must not grow with rows
SHAPE_SLOTS = 5000                 # bounded shape table — a 4 M-row CSV must not OOM the box
F_MAX_AXIS_VALUES = 8              # "minority of a small axis" only makes sense when small
LATE_ARRIVAL_SHARE = 0.30          # a value first seen after this much of the file is a change
OUTLIER_FACTOR = 10                # top value vs median

KV_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]{2,31})=([^\r\n\t]{0,120})")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
QUOTED_RE = re.compile(r"'([^']{1,64})'")
CONST_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,})\b")
DIGITS_RE = re.compile(r"^\d{1,10}$")
HEX_RE = re.compile(r"\b[0-9a-fA-F]{6,}\b")
NUM_RE = re.compile(r"\d+")


# ---------------------------------------------------------------- file access
def opener(path):
    return gzip.open if path.endswith(".gz") else open


def looks_binary(path):
    """Same test as logmap.py:822 — a NUL byte in the first 8 KB."""
    try:
        with opener(path)(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True


def read_lines(path):
    with opener(path)(path, "rt", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            yield i, line.rstrip("\n")


def walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            ap = os.path.join(dirpath, fn)
            out.append((ap, os.path.relpath(ap, root).replace(os.sep, "/")))
    return sorted(out, key=lambda t: t[1])


def is_global_ip(text):
    try:
        return ipaddress.ip_address(text).is_global
    except ValueError:
        return False


# ------------------------------------------------------------------- explode
def explode_evtx(ev):
    """One Windows event record -> {field: value} with the buried fields lifted.

    Three sources, in order of how deeply they are buried:
      1. System/*        — Channel, Provider, EventID, Computer  (logmap sees these)
      2. EventData/Data  — positional list OR named dict         (logmap does not)
      3. key=value blobs inside any string value                 (logmap does not)
    """
    fields = {}
    sys_ = ev.get("System") or {}

    prov = ((sys_.get("Provider") or {}).get("#attributes") or {}).get("Name")
    if prov:
        fields["provider"] = prov
    chan = sys_.get("Channel")
    if isinstance(chan, str):
        fields["channel"] = chan
    eid = sys_.get("EventID")
    if isinstance(eid, dict):
        eid = eid.get("#text")
    if eid is not None:
        fields["event_id"] = str(eid)
    comp = sys_.get("Computer")
    if isinstance(comp, str):
        fields["computer"] = comp

    when = ((sys_.get("TimeCreated") or {}).get("#attributes") or {}).get("SystemTime")
    rec = sys_.get("EventRecordID")

    data = (ev.get("EventData") or {}).get("Data")
    positional = []
    if isinstance(data, dict):
        txt = data.get("#text")
        if isinstance(txt, list):
            positional = [x for x in txt if isinstance(x, str)]
        elif isinstance(txt, str):
            positional = [txt]
        for k, v in sorted(data.items()):
            if k.startswith("#") or not isinstance(v, str):
                continue
            fields["data." + k] = v
    elif isinstance(data, list):
        positional = [x for x in data if isinstance(x, str)]
    elif isinstance(data, str):
        positional = [data]

    for i, v in enumerate(positional):
        if len(v) <= 120:
            fields["data[%d]" % i] = v
        blob = v
        if "=" in blob:
            for k, kv in KV_RE.findall(blob):
                fields["kv." + k] = kv.strip()

    # A long payload string never becomes an axis (too high-cardinality), so the
    # state constant inside it would be invisible.  Lift it out by SHAPE — an
    # ALL_CAPS_WITH_UNDERSCORES token — not by matching any known name.
    consts = []
    for v in positional:
        for c in CONST_RE.findall(v):
            if c not in consts:
                consts.append(c)

    ident = set()
    for v in positional:
        for q in QUOTED_RE.findall(v):
            if 1 <= len(q) <= 64 and " " not in q:
                ident.add(q)
    for key in ("data.TargetUserName", "data.SubjectUserName", "kv.User", "kv.UserName"):
        if fields.get(key):
            ident.add(fields[key])

    peers = set()
    for v in positional:
        for m in IPV4_RE.findall(v):
            if is_global_ip(m):
                peers.add(m)

    return fields, positional, consts, sorted(ident), sorted(peers), when, rec


def transition(positional):
    """`['xp_cmdshell', '0', '1']` — a named thing changing from one number to
    another.  Structural: >=2 numeric members, the last two differ."""
    if not 2 <= len(positional) <= 5:
        return None
    nums = [p for p in positional if DIGITS_RE.match(p)]
    if len(nums) < 2 or nums[-2] == nums[-1]:
        return None
    name = next((p for p in positional if not DIGITS_RE.match(p)), "")
    return "%s: %s -> %s" % (name[:60], nums[-2], nums[-1])


# --------------------------------------------------------------- file reports
class Report:
    def __init__(self, rel, path):
        self.rel, self.path = rel, path
        self.bytes = 0
        self.error = None
        self.kind = None
        self.records = 0
        self.verbatim = None
        self.axes = {}          # field -> Counter(value)
        self.first_at = {}      # (field, value) -> line
        self.seen_order = {}    # (field, value) -> ordinal of its first record
        self.hours = collections.Counter()
        self.window = [None, None]
        self.notes = []


def scan_jsonl(rep):
    axes = collections.defaultdict(collections.Counter)
    first_at = {}
    seen_order = {}                                # (field, value) -> record ordinal
    peer_ident = collections.defaultdict(set)      # (peer, ident) -> event ids
    peer_lines = {}
    transitions = []
    for ln, line in read_lines(rep.path):
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        ev = ev.get("Event", ev)
        rep.records += 1
        fields, positional, consts, idents, peers, when, _rec = explode_evtx(ev)

        for k, v in fields.items():
            if v is None or v == "":
                continue
            axes[k][v] += 1
            first_at.setdefault((k, v), ln)
            seen_order.setdefault((k, v), rep.records)
        for c in consts:
            axes["const"][c] += 1
            first_at.setdefault(("const", c), ln)
            seen_order.setdefault(("const", c), rep.records)

        tr = transition(positional)
        if tr:
            transitions.append((ln, tr))

        eid = fields.get("event_id", "?")
        for p in peers:
            peer_lines.setdefault(p, ln)
            if idents:
                for it in idents:
                    peer_ident[(p, it)].add(eid)
            else:
                peer_ident[(p, "")].add(eid)

        if isinstance(when, str) and len(when) >= 13:
            rep.hours[when[11:13]] += 1
            if rep.window[0] is None or when < rep.window[0]:
                rep.window[0] = when
            if rep.window[1] is None or when > rep.window[1]:
                rep.window[1] = when

    rep.axes = dict(axes)
    rep.first_at = first_at
    rep.seen_order = seen_order
    return transitions, peer_ident, peer_lines


def scan_table(rep, sep="\t"):
    """TSV/CSV renderings (a pcap becomes tables).  Columns are axes; numeric
    columns get an outlier check; peers get a port fan-out check."""
    axes = collections.defaultdict(collections.Counter)
    first_at = {}
    numeric = {}                  # col -> [count, max, max_line, samples]
    fanout = collections.defaultdict(set)
    peer_lines = {}
    header = None
    rows = []                     # capped at SMALL_TABLE_ROWS — see below
    shapes = {}                   # shape -> [count, first_line], bounded
    for ln, line in read_lines(rep.path):
        cells = line.split(sep)
        if header is None:
            header = [c.strip() or "col%d" % i for i, c in enumerate(cells)]
            continue
        if not line.strip():
            continue
        rep.records += 1
        if len(rows) < SMALL_TABLE_ROWS:
            rows.append((ln, line))
        shape = NUM_RE.sub("#", HEX_RE.sub("#", line))[:200]
        slot = shapes.get(shape)
        if slot is not None:
            slot[0] += 1
        elif len(shapes) < SHAPE_SLOTS:
            shapes[shape] = [1, ln]
        for i, c in enumerate(cells):
            name = header[i] if i < len(header) else "col%d" % i
            c = c.strip()
            if not c:
                continue
            axes[name][c] += 1
            first_at.setdefault((name, c), ln)
            if DIGITS_RE.match(c):
                slot = numeric.setdefault(name, [0, -1, 0, []])
                v = int(c)
                slot[0] += 1
                if v > slot[1]:
                    slot[1], slot[2] = v, ln
                if len(slot[3]) < NUMERIC_SAMPLES:
                    slot[3].append(v)
        src = dst = dport = None
        for i, c in enumerate(cells):
            name = (header[i] if i < len(header) else "").lower()
            c = c.strip()
            if "src" in name and IPV4_RE.fullmatch(c):
                src = c
            elif "dst" in name and IPV4_RE.fullmatch(c):
                dst = c
            elif (name in ("dport", "dstport", "dst_port", "destport",
                           "destination_port")
                  or (name.endswith("port") and not name.startswith(("s", "src")))):
                dport = c
        if src and dport:
            fanout[src].add(dport)
            peer_lines.setdefault(src, ln)
        if dst and is_global_ip(dst):
            peer_lines.setdefault(dst, ln)
    rep.axes = dict(axes)
    rep.first_at = first_at
    if rep.records <= SMALL_TABLE_ROWS:
        rep.verbatim = "\n".join(l for _, l in rows[:SMALL_TABLE_ROWS])

    # One shape repeated many times is the opposite of a rare value, and it is
    # how automation looks: 68 near-identical statements staging a payload read
    # as ordinary traffic on every rarity axis.  Counted in a BOUNDED table, so
    # a 4 M-row corpus costs the same memory as a 70-row one.
    bursts = sorted((slot[1], slot[0], sh) for sh, slot in shapes.items()
                    if slot[0] >= SHAPE_BURST)
    if len(shapes) >= SHAPE_SLOTS:
        rep.notes.append("таблица форм переполнена (%d) — серии могли быть пропущены"
                         % SHAPE_SLOTS)
    return numeric, fanout, peer_lines, bursts


def scan_text(rep):
    if rep.bytes <= SMALL_FILE_BYTES * 8:
        with opener(rep.path)(rep.path, "rt", encoding="utf-8", errors="replace") as fh:
            rep.verbatim = fh.read()
    rep.records = sum(1 for _ in read_lines(rep.path))


# ------------------------------------------------------------------ the rows
class Rows:
    def __init__(self):
        self.rows = []
        self.counters = collections.Counter()
        self.dropped = 0

    def add(self, axis, letter, rel, line, freq, record):
        if len(self.rows) >= MAX_ROWS:
            self.dropped += 1
            return
        self.counters[letter] += 1
        rid = "%s%03d" % (letter, self.counters[letter])
        rec = (record or "").replace("\t", " ").replace("\n", " ")[:RECORD_SNIPPET]
        self.rows.append((rid, axis, "%s:%d" % (rel, line), freq, rec))


def line_text(path, want):
    out = {}
    if not want:
        return out
    top = max(want)
    for ln, line in read_lines(path):
        if ln in want:
            out[ln] = line
        if ln >= top:
            break
    return out


def build(root, out_dir):
    reports, rows = [], Rows()
    pending = collections.defaultdict(dict)     # rel -> {line: (axis, letter, freq)}

    for path, rel in walk(root):
        rep = Report(rel, path)
        try:
            rep.bytes = os.path.getsize(path)
        except OSError as e:
            rep.error = str(e)
            reports.append(rep)
            continue
        if looks_binary(path):
            rep.error = "двоичный файл — читать нечем (нужен рендер в текст)"
            reports.append(rep)
            continue

        low = rel.lower()
        if low.endswith(".jsonl") or low.endswith(".jsonl.gz"):
            rep.kind = "evtx-jsonl"
            transitions, peer_ident, peer_lines = scan_jsonl(rep)
            for ln, tr in transitions:
                pending[rel][ln] = ("T переход состояния", "T", "n=1 · %s" % tr)
            for (peer, ident), eids in sorted(peer_ident.items()):
                if len(eids) >= 2:
                    ln = peer_lines.get(peer)
                    if ln:
                        pending[rel][ln] = (
                            "P пир×личность", "P",
                            "peer=%s ident=%s eid=%s" % (peer, ident or "-",
                                                         ",".join(sorted(eids))))
            _state_change_rows(rep, pending[rel])
            _rare_field_rows(rep, pending[rel])
        elif low.endswith(".tsv") or low.endswith(".csv"):
            rep.kind = "table"
            numeric, fanout, peer_lines, bursts = scan_table(
                rep, "\t" if low.endswith(".tsv") else ",")
            for ln, n, shape in bursts:
                pending[rel][ln] = ("B серия одной формы", "B",
                                    "n=%d из %d одинаковой формы" % (n, rep.records))
            for peer, ports in sorted(fanout.items()):
                if len(ports) >= PORT_FANOUT:
                    ln = peer_lines.get(peer)
                    if ln:
                        pending[rel][ln] = ("S развёртка портов", "S",
                                            "peer=%s портов=%d" % (peer, len(ports)))
            for name, slot in sorted(numeric.items()):
                count, top_v, top_ln, samples = slot
                if count < 8 or not samples:
                    continue
                ordered = sorted(samples)
                med = ordered[len(ordered) // 2]
                if med > 0 and top_v >= med * OUTLIER_FACTOR:
                    pending[rel][top_ln] = ("O числовой выброс", "O",
                                            "%s=%d медиана=%d ×%d" % (name, top_v, med,
                                                                      top_v // max(med, 1)))
            _state_change_rows(rep, pending[rel])
            _rare_field_rows(rep, pending[rel])
        else:
            rep.kind = "text"
            scan_text(rep)
            if rep.verbatim is not None and rep.records:
                pending[rel][1] = ("V файл целиком", "V",
                                   "строк %d · файл прочитан целиком в map.txt"
                                   % rep.records)
        reports.append(rep)

    # Emit in AXIS PRIORITY order, not directory order.  A working list truncated
    # at 250 rows must not lose the two state-transition rows because an earlier
    # benign file contributed 250 rare values first — that is exactly how the
    # dev-log map buried this intrusion.
    cand = []
    for rel in sorted(pending):
        items = sorted(pending[rel].items())
        strong = [(ln, t) for ln, t in items if t[1] != "F"]
        weak = [(ln, t) for ln, t in items if t[1] == "F"]

        def strength(item):
            m = re.search(r"n=(\d+) из (\d+)", item[1][2])
            if not m:
                return (10 ** 9, item[0])
            n, tot = int(m.group(1)), max(int(m.group(2)), 1)
            return (round(n / tot, 6), n, item[0])

        weak.sort(key=strength)
        rows.dropped += max(0, len(weak) - F_ROWS_PER_FILE)
        for ln, (axis, letter, freq) in strong + weak[:F_ROWS_PER_FILE]:
            cand.append((AXIS_PRIORITY.get(letter, 9), rel, ln, axis, letter, freq))
    cand.sort()
    keep = cand[:MAX_ROWS]
    rows.dropped += len(cand) - len(keep)

    by_file = collections.defaultdict(set)
    for _, rel, ln, _, _, _ in keep:
        by_file[rel].add(ln)
    text = {}
    for rel, wanted in sorted(by_file.items()):
        path = os.path.join(root, *rel.split("/"))
        text[rel] = line_text(path, wanted)

    for _, rel, ln, axis, letter, freq in keep:
        rows.add(axis, letter, rel, ln, freq, text.get(rel, {}).get(ln, ""))

    _write(out_dir, root, reports, rows)
    return reports, rows


def _state_change_rows(rep, sink):
    """A value that appears for the first time deep inside the stream, on an axis
    with only a handful of values, is a STATE CHANGE — not a rare value.

    Measured motivation: `SECURITY_PRODUCT_STATE_ON` x8 then
    `SECURITY_PRODUCT_STATE_SNOOZED` x4 is 33% of its axis, so no rarity
    threshold can flag it; and `HostName=MSFConsole` is 9 of 133, likewise not
    rare.  Both are unmistakable in TIME: they simply were not there before.
    """
    if rep.records < 6 or not rep.seen_order:
        return
    for field, counter in sorted(rep.axes.items()):
        if not 2 <= len(counter) <= F_MAX_AXIS_VALUES:
            continue
        entries = []
        for value, n in counter.items():
            ordinal = rep.seen_order.get((field, value))
            if ordinal:
                entries.append((ordinal, value, n))
        if len(entries) < 2:
            continue
        entries.sort()
        earliest = entries[0][1]
        for ordinal, value, n in entries[1:]:
            if ordinal < rep.records * LATE_ARRIVAL_SHARE:
                continue
            if n >= rep.records * 0.5:
                continue
            ln = rep.first_at.get((field, value))
            if ln and ln not in sink:
                sink[ln] = ("C смена состояния", "C",
                            "%s: %s → %s (впервые на записи %d из %d, n=%d)"
                            % (field, str(earliest)[:28], str(value)[:40],
                               ordinal, rep.records, n))


def _rare_field_rows(rep, sink):
    """Ordinary rare-value logic — the point is only that the FIELDS EXIST."""
    if rep.records < 4:
        return
    limit = max(2, int(rep.records * RARE_SHARE))
    for field, counter in sorted(rep.axes.items()):
        if not 2 <= len(counter) <= AXIS_MAX_CARDINALITY:
            continue
        total = sum(counter.values())
        small_axis = len(counter) <= F_MAX_AXIS_VALUES
        for value, n in sorted(counter.items(), key=lambda kv: (kv[1], kv[0])):
            minority = small_axis and total and n <= total * 0.15
            if n > limit and not minority:
                continue
            ln = rep.first_at.get((field, value))
            if ln and ln not in sink:
                sink[ln] = ("F редкое значение", "F",
                            "n=%d из %d · %s=%s" % (n, rep.records, field, value[:60]))
            elif ln:
                # already claimed by T/P/S/O — keep the stronger axis, note the overlap
                pass


def _write(out_dir, root, reports, rows):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "map.txt"), "w", encoding="utf-8") as fh:
        fh.write("КАРТА УЛИК (secmap) — корпус %s\n" % root)
        fh.write("Оси: T переход · C смена состояния · P пир×личность · S развёртка портов · "
                 "B серия одной формы · O выброс · V файл целиком · F редкое значение · "
                 "P пир×личность · S развёртка портов · O числовой выброс\n")
        fh.write("Внешнего словаря событий здесь НЕТ: ни списка EventID, ни ATT&CK, "
                 "ни Sigma.\n\n")
        for rep in reports:
            fh.write("=" * 78 + "\n%s  %d B\n" % (rep.rel, rep.bytes))
            if rep.error:
                fh.write("  ОШИБКА: %s\n" % rep.error)
                continue
            fh.write("  вид %s · записей %d\n" % (rep.kind, rep.records))
            if rep.window[0]:
                fh.write("  окно: %s → %s · часы %s\n"
                         % (rep.window[0], rep.window[1],
                            ",".join(sorted(rep.hours))))
            for field, counter in sorted(rep.axes.items()):
                if not 2 <= len(counter) <= AXIS_MAX_CARDINALITY:
                    continue
                top = ", ".join("%s=%d" % (v[:40], n)
                                for v, n in counter.most_common(6))
                fh.write("  ось «%s» (%d знач.): %s\n" % (field, len(counter), top))
            for note in rep.notes:
                fh.write("  ⚠ %s\n" % note)
            if rep.verbatim:
                fh.write("  --- файл целиком ---\n")
                for line in rep.verbatim.splitlines():
                    fh.write("  | %s\n" % line[:400])
        fh.write("\nстрок в рабочем списке: %d" % len(rows.rows))
        if rows.dropped:
            fh.write(" (отброшено по лимиту: %d — ЭТО ПОТЕРЯ ПОКРЫТИЯ)" % rows.dropped)
        fh.write("\n")

    with open(os.path.join(out_dir, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        fh.write("# вердикт: ? не разобрано · D дефект · N норма (только с цифрой) "
                 "· X данных не хватает\n")
        fh.write("# оси: T переход состояния · C смена состояния · F редкое значение · P пир×личность "
                 "· S развёртка портов · O числовой выброс\n")
        for rid, axis, ref, freq, rec in rows.rows:
            fh.write("%s\t?\t%s\t%s\t%s\t%s\n" % (rid, axis, ref, freq, rec))

    with open(os.path.join(out_dir, "axis3.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# файл\tзаписей\tокно\tчасы (записей в час)\n")
        for rep in reports:
            if not rep.records or rep.error:
                continue
            hours = " ".join("%s=%d" % (h, n) for h, n in sorted(rep.hours.items()))
            fh.write("%s\t%d\t%s→%s\t%s\n"
                     % (rep.rel, rep.records, rep.window[0] or "-",
                        rep.window[1] or "-", hours))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus")
    ap.add_argument("--out", default="./work")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.corpus):
        sys.stderr.write("нет такого каталога: %s\n" % args.corpus)
        return 2
    reports, rows = build(args.corpus, args.out)
    binaries = [r for r in reports if r.error and "двоичный" in r.error]
    print("secmap: файлов %d (двоичных пропущено %d) · записей %d · строк списка %d"
          % (len(reports), len(binaries),
             sum(r.records for r in reports), len(rows.rows)))
    by = collections.Counter(r[0][0] for r in rows.rows)
    print("  по осям: " + ", ".join("%s=%d" % (k, by[k]) for k in sorted(by)))
    print("  написано: %s/map.txt · %s/worklist.tsv · %s/axis3.tsv"
          % (args.out, args.out, args.out))
    if binaries:
        print("  ⚠ двоичные файлы не читаются напрямую — сначала рендер в текст "
              "(prepare-corpus.sh)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
