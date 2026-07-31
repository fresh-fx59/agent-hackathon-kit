#!/usr/bin/env python3
"""logmap — turn a directory of raw logs into three small files the model can work
from: a map, a worklist of anomalies, and a rate table.

    python3 logmap.py ./logs --out ./work
    python3 logmap.py ./logs --out ./work --worklist-cap 250 --per-file-cap 40

Writes `<out>/map.txt`, `<out>/worklist.tsv`, `<out>/axis3.tsv`. Nothing is written
next to the corpus (it is often read-only). stdout is a bounded summary — never the
raw material.

Why this exists (measured, and every number here came from the corpus, not from a
summary of it).

* A run's result is decided almost entirely by WHICH lines it looked at. Selecting
  them by hand costs a dozen wide tool calls and still misses whole files.
* Severity is not a word. Across one 649 MB corpus the level axis was, per file, the
  6th pipe column, a Cyrillic `KEY=value` field, a numeric JSON field, and the 3rd
  whitespace token. Any fixed vocabulary is blind to at least one of them, so this
  tool carries NO severity dictionary at all: the axis is discovered from the data,
  by shape, and its whole value histogram is printed.
* An hour extractor that only understands `HH:MM:SS` silently returns nothing on the
  two files that hold the only rate-shaped defects in that corpus. Silence, not an
  error. So seven time shapes are probed, the numeric-epoch one is key-agnostic and
  float-aware, and a file with no usable time axis is announced loudly instead of
  being quietly dropped from the rate pass.
* Sampling and rare-event detection are contradictory. The previous map tool moved
  to 1-in-N sampling on big files, which is exactly where the single decisive record
  lives. Every record is read here; the cost is bounded by capping OUTPUT, not input.

Three axes, in one streaming pass per file:

  axis 1  rarity      — records collapse to a template; the rarest templates are the
                        residue where one-off events live.
  axis 2  category    — any group carrying a rare value of the discovered level axis,
                        even when its template is common.
  axis 3  rate        — per (file, template, hour): share of the hour and p50/p90/p99
                        of every numeric slot, first comparable hour vs last. Also an
                        explicit BACKGROUND list: the flattest high-volume templates,
                        so "this did not change" is a written measurement rather than
                        absent work.

Stdlib only, no network, no config.
"""
import argparse
import gzip
import os
import random
import re
import sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# knobs — every one of them bounds OUTPUT, never input
# ---------------------------------------------------------------------------
PROBE_LINES = 4000            # how much of a file decides its framing and time shape
SMALL_FILE_BYTES = 4096       # below this a file is quoted whole into the map
TEMPLATE_MAX = 180            # per LINE; a multi-line record gets proportionally more
TEMPLATE_MAX_FACTOR = 4       # ... up to this multiple
DISPLAY_MAX = 300             # how much of a record a worklist row shows
DISTINCT_RATIO_GATE = 0.25    # distinct templates / records above this ⇒ not a log
DEFAULT_WORKLIST_CAP = 250
DEFAULT_PER_FILE_CAP = 40
RATE_ROW_CAP = 70             # slots inside the worklist cap reserved for axis 3
MAX_SLOTS = 10                # numeric positions tracked per template
MASK_INPUT_MAX = 800          # how much of one record is masked (see note below)
HOUR_SCAN_MAX = 600           # how far into a record the time stamp is looked for
RESERVOIR = 1000              # values kept per (template, hour, slot) for percentiles
RATE_MIN_N = 60               # a template needs this many records before rate talk
RATE_MIN_HOUR_N = 20          # ... and this many inside an hour for it to count
RATE_FACTOR = 3.0             # first vs last comparable hour
FLAT_LO, FLAT_HI = 0.8, 1.25  # what "did not move" means for the background list
FLAT_MIN_N = 800              # background list is high-volume only
AXIS3_CAP = 60
AXIS3_BG_CAP = 20
LEVEL_AXES_SHOWN = 3
RARE_LEVEL_SHARE = 0.01       # a level value below this share of the file is rare
RARE_LEVEL_MAX_N = 200        # ... and rare in absolute terms too: 0.7% of 833k
                              # records is 5.8k lines, which is not a needle
RARE_MAX_N = 5                # axis 1 residue: a group this small or smaller.
                              # Independently measured on a 649 MB corpus: every
                              # one of 13 answer cards is anchored at this cut.
AXIS3_PER_FILE = 4            # one busy file must not own the whole rate table
AXIS3_BG_PER_FILE = 1         # background: one row per FILE, so every file gets a
                              # written "this did not move" instead of the biggest
                              # files taking every slot
AXIS3_BG_SLOTS = 24           # ... roughly one per file in a mid-size corpus
RATE_MIN_HOUR_SHARE = 0.05    # an hour holding <5% of a template is an edge, not
                              # a comparable hour
STDOUT_MAX_LINES = 400
SEED = 20260728

EPOCH_LO, EPOCH_HI = 946684800, 4102444800     # 2000-01-01 .. 2100-01-01

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv"}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def deansi(raw):
    """Colour codes are the second-commonest reason a "plain text" log is not.
    The containment test is ~40x cheaper than the substitution on a clean line,
    and most lines in most corpora are clean."""
    line = raw.rstrip("\n")
    return ANSI_RE.sub("", line) if "\x1b" in line else line

# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------
CRI_RE = re.compile(r"^\S+Z (stdout|stderr) [PF] ")
BLOCK_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# a timestamp that a record STARTS with — used to tell a new record from a
# continuation line (a stack frame, a wrapped payload, a goroutine dump)
LEAD_TS_RE = re.compile(
    r"^\W{0,3}(?:"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}"
    r"|[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2}"
    r"|\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}"
    r"|\d{8}\|\d{6}\.\d{3}\|"
    r"|\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}"
    r"|\{\s*\"?[A-Za-z_]"
    r")")

FRAME_LINE, FRAME_CRI, FRAME_BLOCK, FRAME_ANCHOR = "line", "cri", "block", "anchor"

# ---------------------------------------------------------------------------
# time — seven shapes, probed in this order. The numeric one is key-agnostic: it
# does not care whether the field is called `time`, `ts`, `__REALTIME_TIMESTAMP`
# or nothing at all, and it accepts seconds-as-float, milliseconds and
# microseconds. A naive HH:MM:SS reader returns NOTHING on three of these shapes.
# ---------------------------------------------------------------------------
TIME_SHAPES = [
    ("iso", re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[T ](\d{2}):\d{2}:\d{2}")),
    ("dmy", re.compile(r"\d{2}\.\d{2}\.\d{4} (\d{2}):\d{2}:\d{2}")),
    ("bsd", re.compile(r"[A-Z][a-z]{2} +\d{1,2} (\d{2}):\d{2}:\d{2}")),
    # combined-log-format: the hour sits behind a colon, so the bare-clock shape's
    # left guard rejects it. Measured: without this row the two largest files in a
    # 649 MB corpus (1.2M records, 28 % of it) get NO time axis at all.
    ("clf", re.compile(r"\d{2}/[A-Za-z]{3}/\d{4}:(\d{2}):\d{2}:\d{2}")),
    ("compact", re.compile(r"\|(\d{2})\d{4}\.\d{3}\|")),
    ("epoch", re.compile(r"(?<![0-9.eE+-])(\d{10}(?:\.\d{1,9})?|\d{13}|\d{16})"
                         r"(?![0-9])")),
    ("clock", re.compile(r"(?<![0-9:.])(\d{2}):\d{2}:\d{2}")),
]
TIME_SHAPE_MIN_SHARE = 0.30


def epoch_hour(raw):
    """A numeric field -> hour of day, or None when the number is not a time."""
    try:
        if "." in raw:
            sec = float(raw)
        else:
            n = int(raw)
            sec = n / 1000.0 if len(raw) == 13 else (
                n / 1000000.0 if len(raw) == 16 else float(n))
    except ValueError:
        return None
    if not (EPOCH_LO <= sec <= EPOCH_HI):
        return None
    return int(sec // 3600) % 24


_SHAPE_RX = dict(TIME_SHAPES)


EPOCH_MAX_SPAN_S = 90 * 86400     # a capture wider than this is not one capture
EPOCH_MIN_ORDERED = 0.70          # log lines advance in time; identifiers do not


def epoch_seconds(text):
    """First numeric token in the text that is plausibly a wall-clock time."""
    for m in _SHAPE_RX["epoch"].finditer(text[:HOUR_SCAN_MAX]):
        raw = m.group(1)
        try:
            if "." in raw:
                sec = float(raw)
            else:
                n = int(raw)
                sec = n / 1000.0 if len(raw) == 13 else (
                    n / 1000000.0 if len(raw) == 16 else float(n))
        except ValueError:
            continue
        if EPOCH_LO <= sec <= EPOCH_HI:
            return sec
    return None


def epoch_axis_is_real(series):
    """Is this column a CLOCK, or just long numbers that pass a range check?

    Measured on a corpus this tool had never seen: 16-digit block identifiers
    divide into a perfectly plausible microsecond epoch, so every record got an
    hour and the file was handed a time axis made of random numbers. A clock has
    two properties an identifier does not — it advances, and it covers one
    capture, not three centuries."""
    vals = [v for v in series if v is not None]
    if len(vals) < 20:
        return False
    if max(vals) - min(vals) > EPOCH_MAX_SPAN_S:
        return False
    ordered = sum(1 for a, b in zip(vals, vals[1:]) if b >= a)
    return ordered / float(len(vals) - 1) >= EPOCH_MIN_ORDERED


def hour_of(text, shape):
    """-> (hour, matched_substring) or (None, None)."""
    if shape is None:
        return None, None
    rx = _SHAPE_RX[shape]
    text = text[:HOUR_SCAN_MAX]
    if shape == "epoch":
        for m in rx.finditer(text):
            h = epoch_hour(m.group(1))
            if h is not None:
                return h, m.group(1)
        return None, None
    m = rx.search(text)
    if not m:
        return None, None
    try:
        return int(m.group(1)) % 24, m.group(0)
    except (ValueError, IndexError):
        return None, None


# ---------------------------------------------------------------------------
# masking: one pass, one alternation. Strings survive, numbers become slots.
# Keeping the string parts is what makes per-template percentiles group by
# endpoint / operation for free, without the tool ever learning a field name.
# ---------------------------------------------------------------------------
MASK_RE = re.compile(
    r"(?P<uuid>\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{12}\b)"
    r"|(?P<ts>\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"|\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?: [+-]\d{4})?"
    r"|[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2}"
    r"|(?<![0-9:.])\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)"
    r"|(?P<ip>\b\d{1,3}(?:\.\d{1,3}){3}\b)"
    r"|(?P<hex>\b(?=[0-9a-fA-F]{8,}\b)(?=[a-fA-F]*[0-9])[0-9a-fA-F]{8,}\b)"
    r"|(?P<num>(?:(?<![A-Za-z0-9_])-)?\d+(?:\.\d+)?)")

_PLACEHOLDER = {"uuid": "<uuid>", "ts": "<T>", "ip": "<ip>", "hex": "<hex>"}


def mask(text, slots):
    """-> masked text; appends every numeric slot VALUE to `slots`, in order.

    The slot list is what makes the rate axis format-agnostic: nobody has to know
    that the 6th number on this line is called `duration`."""
    def repl(m):
        g = m.lastgroup
        if g == "num":
            if len(slots) < MAX_SLOTS:
                slots.append(float(m.group(0)))
            return "#"
        return _PLACEHOLDER[g]
    return MASK_RE.sub(repl, text)


_MESSY = ("\n", "\t", "\r", "  ")


def squeeze(s):
    if any(ch in s for ch in _MESSY):
        return " ".join(s.split())
    return s


# ---------------------------------------------------------------------------
# level-axis discovery — structural only. No word list lives in this file.
# ---------------------------------------------------------------------------
JSON_FIELD_RE = re.compile(
    r'"([A-Za-z_][A-Za-z0-9_]{0,30})"\s*:\s*'
    r'(?:"([^"\\]{1,24})"|(-?\d{1,6}(?:\.\d{1,6})?)|(true|false))')
KV_FIELD_RE = re.compile(
    r"(?<![\w\-])([A-Za-z_\u0400-\u04FF][\w\u0400-\u04FF]{0,30})="
    r"([^\s|,;'\"]{1,24})")
MAX_WS_FIELDS = 12
MAX_PIPE_FIELDS = 12
VALUE_MAX = 24


def field_candidates(text):
    """-> {axis_name: value} for one record. Four generic extractors."""
    out = {}
    for m in JSON_FIELD_RE.finditer(text):
        key = m.group(1)
        val = m.group(2) or m.group(3) or m.group(4)
        out.setdefault("json:" + key, val)
    for m in KV_FIELD_RE.finditer(text):
        out.setdefault("kv:" + m.group(1), m.group(2))
    if text.count("|") >= 3:
        for i, cell in enumerate(text.split("|")[:MAX_PIPE_FIELDS]):
            cell = cell.strip()
            if 0 < len(cell) <= VALUE_MAX:
                out.setdefault("pipe:%d" % i, cell)
    for i, tok in enumerate(text.split()[:MAX_WS_FIELDS]):
        if 0 < len(tok) <= VALUE_MAX:
            out.setdefault("ws:%d" % i, tok)
    return out


def make_extractor(axis):
    """One axis name -> a cheap function record_text -> value|None.

    The probe uses the broad four-extractor sweep once; the full pass must not,
    so each chosen axis is compiled down to a single targeted matcher."""
    kind, _sep, key = axis.partition(":")
    if kind == "json":
        rx = re.compile(r'"%s"\s*:\s*(?:"([^"\\]{1,24})"'
                        r'|(-?\d{1,6}(?:\.\d{1,6})?)|(true|false))'
                        % re.escape(key))

        def get_json(text):
            m = rx.search(text)
            return (m.group(1) or m.group(2) or m.group(3)) if m else None
        return get_json
    if kind == "kv":
        rx = re.compile(r"(?<![\w\-])%s=([^\s|,;'\"]{1,24})" % re.escape(key))

        def get_kv(text):
            m = rx.search(text)
            return m.group(1) if m else None
        return get_kv
    idx = int(key)
    if kind == "pipe":
        def get_pipe(text):
            parts = text.split("|", idx + 1)
            if len(parts) <= idx:
                return None
            cell = parts[idx].strip()
            return cell if 0 < len(cell) <= VALUE_MAX else None
        return get_pipe

    def get_ws(text):
        parts = text.split(None, idx + 1)
        if len(parts) <= idx:
            return None
        tok = parts[idx]
        return tok if 0 < len(tok) <= VALUE_MAX else None
    return get_ws


def axis_score(present_share, hist):
    """Structural only: broadly present, few values, one of them dominant, and the
    values look like words rather than payload."""
    total = sum(hist.values())
    card = len(hist)
    if total == 0 or not (2 <= card <= 40):
        return 0.0
    dominant = max(hist.values()) / float(total)
    if present_share < 0.60 or dominant < 0.25:
        return 0.0
    wordish = sum(c for v, c in hist.items()
                  if v and not v[0].isdigit() and len(v) <= 12) / float(total)
    return present_share * (1.0 - (card - 2) / 40.0) * (0.25 + 0.75 * wordish)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def opener(path):
    return gzip.open if path.endswith(".gz") else open


def read_text(path):
    return opener(path)(path, "rt", encoding="utf-8", errors="replace")


def looks_binary(path):
    try:
        with opener(path)(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True


def walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            ap = os.path.join(dirpath, fn)
            out.append((ap, os.path.relpath(ap, root).replace(os.sep, "/")))
    return sorted(out, key=lambda t: t[1])


def probe(path):
    """-> (framing, time_shape, level_axes, probe_note). Reads a prefix only."""
    lines = []
    try:
        with read_text(path) as fh:
            for i, raw in enumerate(fh):
                if i >= PROBE_LINES:
                    break
                lines.append(deansi(raw))
    except OSError as e:
        return FRAME_LINE, None, [], "нечитаем: %s" % e
    if not lines:
        return FRAME_LINE, None, [], "пустой файл"

    n = len(lines)
    nonblank = [l for l in lines if l.strip()]
    nb = len(nonblank) or 1
    blank_share = (n - len(nonblank)) / float(n)
    cri_share = sum(1 for l in nonblank if CRI_RE.match(l)) / float(nb)
    key_share = sum(1 for l in nonblank if BLOCK_KEY_RE.match(l)) / float(nb)
    lead_share = sum(1 for l in nonblank if LEAD_TS_RE.match(l)) / float(nb)

    if cri_share > 0.90:
        framing = FRAME_CRI
    elif blank_share > 0.02 and key_share > 0.50:
        framing = FRAME_BLOCK
    elif lead_share >= 0.55:
        framing = FRAME_ANCHOR
    else:
        framing = FRAME_LINE

    # time shape: probed on RECORDS, so a block record is judged by its whole body
    recs = [t for _s, _e, t in assemble(lines, framing)][:PROBE_LINES]
    rn = len(recs) or 1
    # Widest coverage wins, ties broken by the order above. Taking the FIRST shape
    # over the floor is wrong: one 649 MB corpus has a file where a nested
    # payload carries an ISO stamp on 30 % of records while the record's own
    # syslog stamp is on 100 % of them.
    shape = None
    shape_hits = {}
    best = None
    for i, (name, _rx) in enumerate(TIME_SHAPES):
        if name == "epoch":
            series = [epoch_seconds(t) for t in recs]
            cov = (sum(1 for v in series if v is not None) / float(rn)
                   if epoch_axis_is_real(series) else 0.0)
        else:
            cov = sum(1 for t in recs if hour_of(t, name)[0] is not None) / float(rn)
        shape_hits[name] = cov
        if cov >= TIME_SHAPE_MIN_SHARE and (best is None or cov > best[0]):
            best = (cov, i, name)
    if best:
        shape = best[2]

    # level axis
    seen = Counter()
    hists = defaultdict(Counter)
    for t in recs:
        for k, v in field_candidates(t).items():
            seen[k] += 1
            hists[k][v] += 1
    axes = []
    for k, c in seen.items():
        s = axis_score(c / float(rn), hists[k])
        if s > 0:
            axes.append((s, k, hists[k]))
    axes.sort(key=lambda t: (-t[0], t[1]))
    note = "формы времени: " + ", ".join(
        "%s %.0f%%" % (k, 100 * v) for k, v in sorted(shape_hits.items(),
                                                      key=lambda kv: -kv[1])[:3])
    return framing, shape, axes[:LEVEL_AXES_SHOWN], note


def assemble(lines, framing):
    """[(start_line, end_line, text)] — 1-based line numbers, ANSI already gone."""
    out = []
    if framing == FRAME_LINE:
        for i, l in enumerate(lines, 1):
            if l.strip():
                out.append((i, i, l))
        return out
    if framing == FRAME_CRI:
        start = None
        buf = []
        for i, l in enumerate(lines, 1):
            m = CRI_RE.match(l)
            if not m:
                if start is None:
                    if l.strip():
                        out.append((i, i, l))
                else:
                    buf.append(l)
                continue
            payload = l[m.end():]
            if start is None:
                start = i
                buf = [l]
            else:
                buf.append(payload)
            if m.group(1) and l[m.end() - 2] == "F":
                out.append((start, i, buf[0] if len(buf) == 1
                            else buf[0] + "".join(buf[1:])))
                start, buf = None, []
        if start is not None:
            out.append((start, len(lines), "".join(buf)))
        return out
    if framing == FRAME_BLOCK:
        start = None
        buf = []
        for i, l in enumerate(lines, 1):
            if not l.strip():
                if buf:
                    out.append((start, i - 1, "\n".join(buf)))
                start, buf = None, []
                continue
            if start is None:
                start = i
            buf.append(l)
        if buf:
            out.append((start, len(lines), "\n".join(buf)))
        return out
    # anchor
    start = None
    buf = []
    for i, l in enumerate(lines, 1):
        if LEAD_TS_RE.match(l):
            if buf:
                out.append((start, i - 1, " ".join(buf)))
            start, buf = i, [l]
        elif not l.strip():
            continue
        else:
            if start is None:
                start, buf = i, [l]
            else:
                buf.append(l.strip())
    if buf:
        out.append((start, len(lines), " ".join(buf)))
    return out


def stream_records(path, framing):
    """Same framing as assemble(), but streaming — never holds the file."""
    pending = []          # list of (lineno, text)
    with read_text(path) as fh:
        if framing == FRAME_LINE:
            for i, raw in enumerate(fh, 1):
                line = deansi(raw)
                if line.strip():
                    yield i, i, line
            return
        if framing == FRAME_BLOCK:
            for i, raw in enumerate(fh, 1):
                line = deansi(raw)
                if not line.strip():
                    if pending:
                        yield pending[0][0], pending[-1][0], \
                            "\n".join(t for _n, t in pending)
                        pending = []
                    continue
                pending.append((i, line))
            if pending:
                yield pending[0][0], pending[-1][0], \
                    "\n".join(t for _n, t in pending)
            return
        if framing == FRAME_CRI:
            for i, raw in enumerate(fh, 1):
                line = deansi(raw)
                m = CRI_RE.match(line)
                if not m:
                    if pending:
                        pending.append((i, line))
                    elif line.strip():
                        yield i, i, line
                    continue
                tag = line[m.end() - 2]
                payload = line[m.end():]
                if not pending:
                    pending.append((i, line))
                else:
                    pending.append((i, payload))
                if tag == "F":
                    yield pending[0][0], pending[-1][0], \
                        pending[0][1] + "".join(t for _n, t in pending[1:])
                    pending = []
            if pending:
                yield pending[0][0], pending[-1][0], \
                    "".join(t for _n, t in pending)
            return
        # anchor
        for i, raw in enumerate(fh, 1):
            line = deansi(raw)
            if LEAD_TS_RE.match(line):
                if pending:
                    yield pending[0][0], pending[-1][0], \
                        " ".join(t for _n, t in pending)
                pending = [(i, line)]
            elif not line.strip():
                continue
            else:
                pending.append((i, line.strip()))
        if pending:
            yield pending[0][0], pending[-1][0], \
                " ".join(t for _n, t in pending)


# ---------------------------------------------------------------------------
# block records: drop fields whose value is pure noise, so the informative field
# is inside the display budget instead of behind 200 characters of cursor.
# ---------------------------------------------------------------------------
NOISE_VALUE_RE = re.compile(r"^[#<>a-zA-Z_ ;=:,.\-]*$")
FIELD_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)


def informative_fields(masked_record):
    """For a block record: keep only fields that still carry text after masking."""
    keep = []
    for part in masked_record.split("\n"):
        m = FIELD_LINE_RE.match(part)
        if not m:
            if part.strip():
                keep.append(part.strip())
            continue
        val = m.group(2).strip()
        if not val:
            continue
        stripped = re.sub(r"<[a-z]+>|#|[\s;:=,.\-]", "", val)
        if not stripped:
            continue
        keep.append("%s=%s" % (m.group(1), val))
    return " ".join(keep)


def percentile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def fmt_num(x):
    if x is None:
        return "-"
    if abs(x - round(x)) < 1e-9:
        return "%d" % round(x)
    return "%.3f" % x


class Reservoir(object):
    """Bounded, deterministic, unbiased. Percentiles must not cost the corpus."""
    __slots__ = ("vals", "n", "rng")

    def __init__(self, rng):
        self.vals = []
        self.n = 0
        self.rng = rng

    def add(self, v):
        self.n += 1
        if len(self.vals) < RESERVOIR:
            self.vals.append(v)
        else:
            j = self.rng.randrange(self.n)
            if j < RESERVOIR:
                self.vals[j] = v


# ---------------------------------------------------------------------------
# one file
# ---------------------------------------------------------------------------
class FileReport(object):
    def __init__(self, rel, path):
        self.rel = rel
        self.path = path
        self.bytes = 0
        self.lines = 0
        self.records = 0
        self.framing = FRAME_LINE
        self.time_shape = None
        self.time_note = ""
        self.level_axis = None
        self.rare_levels = []
        self.level_axes_shown = []
        self.distinct = 0
        self.ratio = 0.0
        self.gated = None
        self.groups = []          # [(count, first_start, first_end, template, display)]
        self.rate_rows = []
        self.bg_rows = []
        self.verbatim = None
        self.error = None
        self.hours = []


def analyse(path, rel, args):
    # A fresh, seeded generator per file: the result must not depend on how many
    # files ran before this one, nor on whether they ran in parallel.
    rng = random.Random(args.seed)
    rep = FileReport(rel, path)
    try:
        rep.bytes = os.path.getsize(path)
    except OSError as e:
        rep.error = str(e)
        return rep
    if looks_binary(path):
        rep.error = "двоичный файл — читать нечем"
        return rep
    if rep.bytes < SMALL_FILE_BYTES and not path.endswith(".gz"):
        try:
            with read_text(path) as fh:
                rep.verbatim = fh.read()
        except OSError as e:
            rep.error = str(e)
            return rep

    rep.framing, rep.time_shape, axes, rep.time_note = probe(path)
    rep.level_axes_shown = [(name, hist) for _s, name, hist in axes]
    rep.level_axis = axes[0][1] if axes else None
    # EVERY qualifying axis is carried, not just the best-scoring one. Ranking
    # among them can only be structural (this file owns no severity words), and a
    # structural ranking put a 3-value `channel` field above a 3-value numeric
    # level field on one real corpus. Taking the union costs one extra targeted
    # regex per record and removes the guess.
    extractors = [(name, make_extractor(name)) for name, _h in rep.level_axes_shown]

    counts = Counter()
    first_seen = {}
    display = {}
    per_hour_total = Counter()
    tmpl_hour = defaultdict(Counter)
    slot_res = {}
    level_hist = defaultdict(Counter)
    level_tmpl = defaultdict(Counter)
    lines_seen = 0

    try:
        for start, end, text in stream_records(path, rep.framing):
            lines_seen = end
            rep.records += 1
            slots = []
            span = max(1, end - start + 1)
            tmax = TEMPLATE_MAX * min(TEMPLATE_MAX_FACTOR, span)
            masked = mask(text[:MASK_INPUT_MAX], slots)
            if rep.framing == FRAME_BLOCK:
                masked = informative_fields(masked)
                shown = masked
            else:
                shown = text
            tmpl = squeeze(masked)[:tmax]
            counts[tmpl] += 1
            if tmpl not in first_seen:
                first_seen[tmpl] = (start, end)
                display[tmpl] = squeeze(shown)[:DISPLAY_MAX]
            for name, get in extractors:
                v = get(text)
                if v is not None:
                    level_hist[name][v] += 1
                    level_tmpl[(name, v)][tmpl] += 1
            h, _raw = hour_of(text, rep.time_shape)
            if h is not None:
                per_hour_total[h] += 1
                tmpl_hour[tmpl][h] += 1
                for si, sv in enumerate(slots):
                    key = (tmpl, h, si)
                    r = slot_res.get(key)
                    if r is None:
                        r = slot_res[key] = Reservoir(rng)
                    r.add(sv)
    except OSError as e:
        rep.error = str(e)
        return rep

    rep.lines = lines_seen
    rep.distinct = len(counts)
    rep.ratio = (rep.distinct / float(rep.records)) if rep.records else 0.0
    rep.hours = sorted(per_hour_total)
    if rep.records and rep.ratio > DISTINCT_RATIO_GATE:
        rep.gated = ("доля уникальных форм %.4f > %.2f — это не потоковый лог, "
                     "ось редкости отключена" % (rep.ratio, DISTINCT_RATIO_GATE))
        return rep

    # ---- axis 1 + 2 -------------------------------------------------------
    rep.rare_levels = []
    boosted = set()
    for name, hist in level_hist.items():
        tot = sum(hist.values()) or 1
        for v, c in hist.items():
            if c <= RARE_LEVEL_MAX_N and c / float(tot) < RARE_LEVEL_SHARE:
                rep.rare_levels.append((name, v, c))
                boosted.update(level_tmpl[(name, v)])
    rep.rare_levels.sort(key=lambda t: t[2])

    rows = []
    for tmpl, c in counts.items():
        is_cat = tmpl in boosted
        # A group that is neither rare nor carrying a rare level value is BACKGROUND.
        # Listing it as an anomaly spends a worklist slot on a line that says
        # nothing — and the slot is the scarce thing here, not the line.
        if c > RARE_MAX_N and not is_cat:
            continue
        st, en = first_seen[tmpl]
        rows.append((c, st, en, "cat" if is_cat else "rare", tmpl, display[tmpl]))
    # the categorical axis goes first: it exists precisely to surface groups the
    # rarity axis cannot see. Inside each axis, rarest first.
    rows.sort(key=lambda r: (0 if r[3] == "cat" else 1, r[0], r[1]))
    rep.groups = rows[:max(args.per_file_cap * 4, 200)]
    rep.group_total = len(rows)

    # ---- axis 3 -----------------------------------------------------------
    if len(rep.hours) >= 2:
        rep.rate_rows, rep.bg_rows = rate_candidates(
            counts, tmpl_hour, per_hour_total, slot_res, first_seen, display)
    return rep


def comparable_hours(per_hour_total):
    """First and last hour of the FILE that is a real hour.

    The floor belongs on the hour, not on the template. Putting it on the template
    silently deletes exactly the shape worth finding: a ramp is rare at the start
    by definition, so a relative floor throws away its first hour and then reports
    the flattened remainder."""
    hours = sorted(per_hour_total)
    tot = sum(per_hour_total.values()) or 1
    floor = max(RATE_MIN_HOUR_N * 5, RATE_MIN_HOUR_SHARE * tot)
    usable = [h for h in hours if per_hour_total[h] >= floor]
    if len(usable) < 2:
        return None, None
    return usable[0], usable[-1]


def rate_candidates(counts, tmpl_hour, per_hour_total, slot_res, first_seen,
                    display):
    moved, flat = [], []
    h0_h1 = comparable_hours(per_hour_total)
    for tmpl, total in counts.items():
        if total < RATE_MIN_N:
            continue
        hc = tmpl_hour.get(tmpl)
        if not hc:
            continue
        if h0_h1[0] is None:
            continue
        h0, h1 = h0_h1
        s0 = hc[h0] / float(per_hour_total[h0])
        s1 = hc[h1] / float(per_hour_total[h1])
        # a share built on one or two records is noise, not a rate
        share_ratio = (s1 / s0) if (s0 and hc[h0] >= 5 and hc[h1] >= 5) else None
        best = None
        for si in range(MAX_SLOTS):
            a = slot_res.get((tmpl, h0, si))
            b = slot_res.get((tmpl, h1, si))
            if not a or not b or a.n < RATE_MIN_HOUR_N or b.n < RATE_MIN_HOUR_N:
                continue
            p99a, p99b = percentile(a.vals, 0.99), percentile(b.vals, 0.99)
            p50a, p50b = percentile(a.vals, 0.50), percentile(b.vals, 0.50)
            p90a, p90b = percentile(a.vals, 0.90), percentile(b.vals, 0.90)
            if p99a in (None, 0) or p99b is None:
                continue
            r = p99b / p99a
            cand = (r, si, p50a, p50b, p90a, p90b, p99a, p99b)
            if best is None or abs(r - 1.0) > abs(best[0] - 1.0):
                best = cand
        st, en = first_seen[tmpl]
        base = {"template": tmpl, "display": display[tmpl], "n": total,
                "h0": h0, "h1": h1, "n0": hc[h0], "n1": hc[h1],
                "share0": s0, "share1": s1, "share_ratio": share_ratio,
                "line_start": st, "line_end": en, "slot": None}
        if best:
            base.update({"slot": best[1], "p50": (best[2], best[3]),
                         "p90": (best[4], best[5]), "p99": (best[6], best[7]),
                         "p99_ratio": best[0]})
        share_moved = share_ratio is not None and (
            share_ratio >= RATE_FACTOR or share_ratio <= 1.0 / RATE_FACTOR)
        p99_moved = best is not None and (
            best[0] >= RATE_FACTOR or best[0] <= 1.0 / RATE_FACTOR)
        base["factor"] = max(abs_dev(share_ratio),
                             abs_dev(best[0] if best else None))
        base["driver"] = ("доля" if abs_dev(share_ratio) >=
                          abs_dev(best[0] if best else None) else "слот")
        if share_moved or p99_moved:
            moved.append((base["factor"], base))
        elif total >= FLAT_MIN_N and share_ratio is not None \
                and FLAT_LO <= share_ratio <= FLAT_HI:
            # BACKGROUND is judged on SHARE alone. Requiring the percentiles to be
            # flat too silently dropped the one template a refutation needs — it
            # then appears in neither list, and "it did not change" never gets
            # written down.
            flat.append((total, base))
    moved.sort(key=lambda t: -t[0])
    flat.sort(key=lambda t: -t[0])
    return [b for _f, b in moved[:AXIS3_CAP]], [b for _n, b in flat[:AXIS3_BG_CAP]]


def abs_dev(r):
    if r is None:
        return 0.0
    return max(r, 1.0 / r) if r > 0 else 0.0


# ---------------------------------------------------------------------------
# assembling the three artefacts
# ---------------------------------------------------------------------------
def build_worklist(reports, cap, per_file_cap):
    """Round-robin across files, rarest first.

    Pure global rarity ranking lets one chatty file eat the whole budget, and the
    single most expensive failure this project ever measured was a run that never
    opened 12 of 28 files. So every file gets its turn before any file gets a
    second row."""
    queues = {}
    for r in reports:
        if r.error or r.gated:
            continue
        queues[r.rel] = list(r.groups)
    order = sorted(queues)
    taken = {k: 0 for k in order}
    rows = []
    progress = True
    while progress and len(rows) < cap:
        progress = False
        for rel in order:
            if len(rows) >= cap:
                break
            q = queues[rel]
            if not q or taken[rel] >= per_file_cap:
                continue
            count, s, e, kind, tmpl, disp = q.pop(0)
            taken[rel] += 1
            rows.append({"file": rel, "kind": kind, "count": count,
                         "line_start": s, "line_end": e, "display": disp})
            progress = True
    trunc = {}
    for r in reports:
        if r.error or r.gated:
            continue
        left = getattr(r, "group_total", 0) - taken.get(r.rel, 0)
        if left > 0:
            trunc[r.rel] = left
    return rows, trunc


def cite(rel, s, e):
    return "%s:%d" % (rel, s) if e <= s else "%s:%d-%d" % (rel, s, e)


def write_worklist(out_dir, rows, rate_rows):
    path = os.path.join(out_dir, "worklist.tsv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        fh.write("# вердикт: ? не разобрано · D дефект · N норма (только с цифрой) "
                 "· X данных не хватает\n")
        for i, r in enumerate(rows, 1):
            fh.write("g%03d\t?\t%s\t%s\tn=%d\t%s\n"
                     % (i, r["kind"], cite(r["file"], r["line_start"],
                                           r["line_end"]), r["count"],
                        r["display"]))
        for i, r in enumerate(rate_rows, 1):
            fh.write("%s\t?\t%s\t%s\tn=%d\t%s\n"
                     % (r["id"], r["kind"], cite(r["file"], r["line_start"],
                                                 r["line_end"]), r["n"],
                        r["summary"] + " | " + r["display"]))
    return path


def _spread(items, per_file, per_slot=2):
    """Take the strongest candidates while keeping the table diverse.

    Without this one busy file contributed 14 near-identical rows for the same
    numeric slot and pushed every other file out of the table."""
    seen_file = Counter()
    seen_slot = Counter()
    out = []
    for f, rel, b in items:
        slot = b.get("slot")
        if seen_file[rel] >= per_file or seen_slot[(rel, slot)] >= per_slot:
            continue
        seen_file[rel] += 1
        seen_slot[(rel, slot)] += 1
        out.append((f, rel, b))
    return out


def rate_worklist_rows(reports, cap):
    """axis-3 candidates, as adjudicable worklist rows. Background rows are
    included on purpose: a refutation nobody was asked for never gets written."""
    moved, flat = [], []
    for r in reports:
        for b in r.rate_rows:
            moved.append((b.get("factor", 1.0), r.rel, b))
        for b in r.bg_rows:
            flat.append((b["n"], r.rel, b))
    moved.sort(key=lambda t: -t[0])
    flat.sort(key=lambda t: -t[0])
    moved = _spread(moved, AXIS3_PER_FILE)
    flat = _spread(flat, AXIS3_BG_PER_FILE, per_slot=AXIS3_BG_PER_FILE)
    # Both halves must fit UNDER cap, and neither may go negative. Previously
    # `cap - n_bg` went negative for a small cap, and `moved[:negative]` is a
    # drop-last slice rather than an empty one — so a smaller --worklist-cap
    # produced a LARGER file (cap 20 -> 80 rows) and, worse, left build_worklist
    # with max(0, cap-80)=0 rows, deleting the rarity axis that anchors every card.
    # Non-monotonic and silent: the exact shape of bug a model economising context
    # walks straight into.
    n_bg = max(0, min(len(flat), AXIS3_BG_SLOTS, cap))
    n_moved = max(0, min(len(moved), cap - n_bg))
    rows = []
    for i, (_f, rel, b) in enumerate(moved[:n_moved], 1):
        rows.append(_rate_row("S%03d" % i, "rate", rel, b))
    for i, (_f, rel, b) in enumerate(flat[:n_bg], 1):
        rows.append(_rate_row("B%03d" % i, "bg", rel, b))
    return rows


def _rate_row(rid, kind, rel, b):
    bits = ["%02dh→%02dh" % (b["h0"], b["h1"]),
            "доля %.2f%%→%.2f%%" % (100 * b["share0"], 100 * b["share1"])]
    if b.get("slot") is not None:
        bits.append("слот#%d p50 %s→%s p99 %s→%s"
                    % (b["slot"] + 1, fmt_num(b["p50"][0]), fmt_num(b["p50"][1]),
                       fmt_num(b["p99"][0]), fmt_num(b["p99"][1])))
    bits.append("n %d→%d" % (b["n0"], b["n1"]))
    return {"id": rid, "kind": kind, "file": rel,
            "line_start": b["line_start"], "line_end": b["line_end"],
            "n": b["n"], "summary": " ".join(bits), "display": b["display"],
            "raw": b}


def write_axis3(out_dir, rate_rows):
    path = os.path.join(out_dir, "axis3.tsv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# id\tось\tсдвиг\tфайл\tметрика\tчас_от\tчас_до\tизмерение\tформа\n")
        fh.write("# ось rate = сдвинулось, ось bg = ФОН, не сдвинулось "
                 "(это тоже измерение, и оно опровергает)\n")
        for r in rate_rows:
            b = r["raw"]
            metric = "доля" if b.get("driver") == "доля" or b.get("slot") is None \
                else "слот#%d" % (b["slot"] + 1)
            factor = b.get("factor", 1.0)
            fh.write("%s\t%s\tx%.2f\t%s\t%s\t%02dh\t%02dh\t%s\t%s\n"
                     % (r["id"], r["kind"], factor, r["file"], metric,
                        b["h0"], b["h1"], r["summary"], b["display"][:200]))
    return path


def render_map(reports, rows, trunc, rate_rows, args, corpus):
    out = []
    a = out.append
    a("КАРТА КОРПУСА  %s" % os.path.abspath(corpus))
    a("файлов: %d · рабочий список: work/worklist.tsv · таблица темпа: work/axis3.tsv"
      % len(reports))
    a("")
    a("Как читать: «форм» — сколько различных шаблонов записей в файле; «ось»")
    a("— поле, из которого выведена серьёзность ИМЕННО В ЭТОМ файле (никакого")
    a("словаря уровней у инструмента нет); «время» — распознанная форма отметки")
    a("времени. «время: НЕТ» значит, что анализ темпа по этому файлу невозможен.")
    a("")
    for r in sorted(reports, key=lambda x: x.rel):
        a("=" * 78)
        a("%s  %s" % (r.rel, human(r.bytes)))
        if r.error:
            a("  ! %s" % r.error)
            if r.verbatim is None:
                a("")
                continue
        if r.verbatim is not None:
            a("  файл меньше %d Б — приведён ДОСЛОВНО:" % SMALL_FILE_BYTES)
            for line in r.verbatim.splitlines():
                a("  | %s" % line[:300])
            a("")
            continue
        a("  строк %d · записей %d · кадрирование %s · форм %d (доля %.4f)"
          % (r.lines, r.records, r.framing, r.distinct, r.ratio))
        if r.time_shape:
            a("  время: %s · часы %s"
              % (r.time_shape, ",".join("%02d" % h for h in r.hours)))
        else:
            a("  время: НЕТ — %s" % r.time_note)
            a("  ВНИМАНИЕ: по этому файлу темп/долю посчитать нельзя. "
              "Если в нём есть улики, их придётся брать глазами.")
        if r.gated:
            a("  ОСЬ РЕДКОСТИ ОТКЛЮЧЕНА: %s" % r.gated)
        for name, hist in r.level_axes_shown:
            tot = sum(hist.values()) or 1
            vals = ", ".join("%s=%d (%.1f%%)" % (v, c, 100.0 * c / tot)
                             for v, c in hist.most_common(12))
            a("  ось «%s» (%d значений): %s" % (name, len(hist), vals))
        if r.rare_levels:
            a("  редкие значения оси по всему файлу: "
              + ", ".join("%s→%s=%d" % (n, v, c)
                          for n, v, c in r.rare_levels[:12]))
        left = trunc.get(r.rel)
        if left:
            a("  TRUNC=%d — форм в файле больше, чем попало в рабочий список" % left)
        a("")
    a("=" * 78)
    a("РАБОЧИЙ СПИСОК: %d строк (потолок %d, на файл %d)"
      % (len(rows) + len(rate_rows), args.worklist_cap, args.per_file_cap))
    a("  из них ось редкости/категории: %d, ось темпа: %d"
      % (len(rows), len(rate_rows)))
    if trunc:
        a("  НЕ ВОШЛО (по файлам): "
          + ", ".join("%s=%d" % kv for kv in sorted(trunc.items())))
    a("")
    a("Каждая строка рабочего списка начинается со статуса `?`. Замени его на")
    a("D / N / X и запиши файл обратно. `citecheck.py --ledger work/worklist.tsv`")
    a("печатает, сколько `?` осталось.")
    return out


def human(n):
    x = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if x < 1024 or unit == "T":
            return "%d%s" % (x, unit) if unit == "B" else "%.1f%s" % (x, unit)
        x /= 1024.0


def default_jobs():
    try:
        n = os.cpu_count() or 1
    except Exception:
        n = 1
    return max(1, min(4, n))


def _one(job):
    """Top-level so it is picklable. -> a FileReport, never an exception."""
    path, rel, args = job
    try:
        return analyse(path, rel, args)
    except Exception as e:            # one unreadable file must not kill the map
        r = FileReport(rel, path)
        r.error = "%s: %s" % (type(e).__name__, e)
        return r


def analyse_all(files, args):
    """Files are independent, so they are analysed in parallel when that is
    allowed. Every worker seeds its own generator from --seed, so the output does
    not depend on the number of workers — verify with `--jobs 1`."""
    jobs = [(p, rel, args) for p, rel in files]
    if args.jobs <= 1 or len(jobs) < 2:
        return [_one(j) for j in jobs]
    try:
        import multiprocessing
        with multiprocessing.Pool(min(args.jobs, len(jobs))) as pool:
            return list(pool.imap(_one, jobs))
    except Exception as e:            # sandboxes that forbid fork must still work
        sys.stderr.write("! параллельный разбор недоступен (%s), иду "
                         "последовательно\n" % e)
        return [_one(j) for j in jobs]


def main():
    ap = argparse.ArgumentParser(
        description="Построить карту корпуса, рабочий список аномалий и таблицу "
                    "темпа — не втаскивая сырьё в контекст.")
    ap.add_argument("corpus", help="каталог с логами")
    ap.add_argument("--out", default="./work", help="куда писать (по умолчанию ./work)")
    ap.add_argument("--worklist-cap", type=int, default=DEFAULT_WORKLIST_CAP)
    ap.add_argument("--per-file-cap", type=int, default=DEFAULT_PER_FILE_CAP)
    ap.add_argument("--rate-cap", type=int, default=RATE_ROW_CAP,
                    help="сколько строк потолка отдать оси темпа")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--jobs", type=int, default=default_jobs(),
                    help="файлов параллельно (1 = строго последовательно)")
    args = ap.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    os.makedirs(args.out, exist_ok=True)

    reports = analyse_all(walk(args.corpus), args)

    rate_rows = rate_worklist_rows(reports, min(args.rate_cap, args.worklist_cap))
    rows, trunc = build_worklist(reports, max(0, args.worklist_cap - len(rate_rows)),
                                 args.per_file_cap)
    wl = write_worklist(args.out, rows, rate_rows)
    ax = write_axis3(args.out, rate_rows)

    body = render_map(reports, rows, trunc, rate_rows, args, args.corpus)
    with open(os.path.join(args.out, "map.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")

    shown = body
    if len(shown) > STDOUT_MAX_LINES:
        head = shown[:STDOUT_MAX_LINES - 12]
        shown = head + ["", "… карта обрезана на %d строк — целиком она лежит в %s"
                        % (len(body) - len(head), os.path.join(args.out, "map.txt"))]
    print("\n".join(shown))
    print("")
    print("написано: %s · %s · %s"
          % (os.path.join(args.out, "map.txt"), wl, ax))
    return 0


if __name__ == "__main__":
    sys.exit(main())
