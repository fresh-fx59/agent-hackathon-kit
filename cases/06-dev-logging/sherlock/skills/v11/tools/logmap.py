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
* A COUNT is not an observation. Seven records of one shape inside 284 seconds of a
  ten-hour capture rendered as "4 in one file, 3 in another" — routine background,
  read the wrong way round. Every repeated group now carries first-seen, last-seen
  and the share of the capture window it occupies.
* A rotated file is not a file. `<name>.log` and `<name>.log.1.gz` are one stream cut
  by logrotate; analysed apart, every count is halved and a before/after lands on
  opposite sides of the cut, measuring the rotation instead of the incident.
* A number is not a measurement. The rate axis was reporting the digits out of an
  item code as p50 56323, and the `1.1` out of `HTTP/1.1` as a latency of 1.100 s for
  an endpoint whose real response time is 0.002 s. A confident wrong number is worse
  than a missing one, because it is what a hypothesis gets refuted with.

Four axes, in one streaming pass per file:

  axis 1  rarity      — records collapse to a template; the rarest templates are the
                        residue where one-off events live. Every repeated group also
                        carries its time SPREAD, so a burst is not read as a trickle.
  axis 2  category    — any group carrying a rare value of the discovered level axis,
                        even when its template is common.
  axis 3  rate        — per (stream, template, hour): share of the hour and p50/p90/p99
                        of every numeric slot that survives a plausibility check,
                        first comparable hour vs last. Also an explicit BACKGROUND
                        list: the flattest high-volume templates, so "this did not
                        change" is a written measurement rather than absent work.
  axis 4  outcome     — where records carry a status/result code, its count per code
                        class per bucket, so "started answering 5xx at 13:40" is a
                        number rather than a thing somebody had to notice.

Stdlib only, no network, no config.
"""
import argparse
import calendar
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
RATE_MIN_HOUR_OF_TYPICAL = 0.25   # an hour holding less than a quarter of what a
                              # TYPICAL hour holds is an edge, not a comparable
                              # hour. Measured against the total instead, as it
                              # was, the floor scales with the width of the
                              # window: at ten hours it deletes every hour of a
                              # quiet stretch, and at twenty-four it deletes them
                              # all. Stitching a rotation family is what produces
                              # windows that wide, so the two go together.
STDOUT_MAX_LINES = 400
SEED = 20260728

BURST_FACTOR = 4.0            # a group packed this many times tighter than the
                              # capture window is a burst, and is told to say so
STREAM_MAX_OVERLAP_S = 2.0    # a rotation cut is instantaneous; more overlap than
                              # this and the two files are not consecutive slices
SLOT_STAT_KEEP_MIN = 15       # slot statistics are kept only for templates that
                              # repeat — they exist to judge a rate, not a one-off
STATUS_LO, STATUS_HI = 100, 599
STATUS_MIN_SHARE = 0.90       # an outcome code is on nearly every record or it is
                              # not the outcome column
STATUS_MIN_CARD = 1           # a single value is allowed on purpose — see below
STATUS_MAX_CARD = 16          # ... but hundreds of values is a measurement
STATUS_MIN_DOMINANT = 0.25
STATUS_KEEP = 32              # exemplars remembered, one per distinct code
OUTCOME_BUCKET_S = 600        # ten minutes: fine enough to land on the minute a
                              # thing started, coarse enough to stay countable
OUTCOME_SERIES_SHOWN = 6
OUTCOME_ROW_CAP = 8
OUTCOME_MIN_PEAK = 5          # a bucket below this is noise, whatever the ratio

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


def epoch_value(raw):
    """A numeric token -> wall-clock seconds, or None when it is not a time."""
    try:
        if "." in raw:
            sec = float(raw)
        else:
            n = int(raw)
            sec = n / 1000.0 if len(raw) == 13 else (
                n / 1000000.0 if len(raw) == 16 else float(n))
    except ValueError:
        return None
    return sec if EPOCH_LO <= sec <= EPOCH_HI else None


def epoch_hour(raw):
    """A numeric field -> hour of day, or None when the number is not a time."""
    sec = epoch_value(raw)
    return None if sec is None else int(sec // 3600) % 24


_SHAPE_RX = dict(TIME_SHAPES)


EPOCH_MAX_SPAN_S = 90 * 86400     # a capture wider than this is not one capture
EPOCH_MIN_ORDERED = 0.70          # log lines advance in time; identifiers do not


def epoch_seconds(text):
    """First numeric token in the text that is plausibly a wall-clock time."""
    for m in _SHAPE_RX["epoch"].finditer(text[:HOUR_SCAN_MAX]):
        sec = epoch_value(m.group(1))
        if sec is not None:
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


# ---------------------------------------------------------------------------
# a POINT in time, not only an hour of the day
#
# Axis 1 has to be able to say how TIGHTLY a repeated shape sits. Measured: one
# shape occurred seven times inside 284 seconds of a ten-hour capture, and
# because the capture straddled a rotation it rendered as `n=4` in one file and
# `n=3` in another — which reads as routine background and is the exact opposite
# of what it was. An hour label cannot tell a burst from a trickle; a first-seen,
# a last-seen and the share of the window they span can.
# ---------------------------------------------------------------------------
MONTH3 = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

# Deliberately the SAME patterns as TIME_SHAPES, only with the parts captured:
# the hour a record is filed under must not move because the tool started
# reading whole stamps. `compact` is the one exception — its date prefix is
# optional in the hour pattern, so it is optional here too, and a stamp without
# one falls through to the day-less path.
STAMP_SHAPES = {
    "iso": re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})[T ](\d{2}):(\d{2}):(\d{2})"),
    "dmy": re.compile(r"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2})"),
    "bsd": re.compile(r"[A-Z][a-z]{2} +\d{1,2} (\d{2}):(\d{2}):(\d{2})"),
    "clf": re.compile(r"(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})"),
    "compact": re.compile(r"(\d{8})?\|(\d{2})(\d{2})(\d{2})\.\d{3}\|"),
    "clock": re.compile(r"(?<![0-9:.])(\d{2}):(\d{2}):(\d{2})"),
}

# Which shapes pin a record to a DATE. Only these can order a rotation family:
# two files stamped `13:05:01` with no date have unrelated origins, and lining
# them up would be a guess wearing a measurement's clothes. `bsd` is deliberately
# not here — it carries a month and a day but no year, and a family is ordered or
# it is left alone.
DATED_SHAPES = ("iso", "dmy", "clf", "epoch", "compact")

_DAY_CACHE = {}


def _days_from_civil(y, mo, d):
    """Days since 1970-01-01. Cached — a capture holds a handful of distinct
    dates, so this runs a few times rather than once per record."""
    key = (y, mo, d)
    v = _DAY_CACHE.get(key)
    if v is None:
        if len(_DAY_CACHE) > 4096:       # a mangled file must not grow it forever
            _DAY_CACHE.clear()
        v = _DAY_CACHE[key] = calendar.timegm((y, mo, d, 0, 0, 0, 0, 1, 0)) // 86400
    return v


class Clock(object):
    """One time shape -> (hour, reading) per record, out of a single regex.

    `reading` is seconds on ONE monotone scale. Where the stamp carries a date
    the scale is absolute UTC and readings from two files can be compared; where
    it does not, the reading is seconds-of-day carried forward over midnight, so
    the origin is arbitrary but differences still hold. Only differences are ever
    used, and `dated` says which of the two you were handed."""
    __slots__ = ("shape", "rx", "dated", "_carry", "_prev")

    def __init__(self, shape):
        self.shape = shape
        self.rx = STAMP_SHAPES.get(shape)
        self.dated = shape in DATED_SHAPES
        self._carry = 0.0
        self._prev = None

    def _wrapped(self, sod):
        """Seconds-of-day -> a reading that keeps rising across midnight."""
        if self._prev is not None and sod < self._prev - 43200:
            self._carry += 86400.0
        self._prev = sod
        return sod + self._carry

    def read(self, text):
        """-> (hour, reading); either may be None. The hour is exactly what
        hour_of() returns, so no bucket moved when stamps were added."""
        if self.shape is None:
            return None, None
        text = text[:HOUR_SCAN_MAX]
        if self.shape == "epoch":
            for m in _SHAPE_RX["epoch"].finditer(text):
                sec = epoch_value(m.group(1))
                if sec is not None:
                    return int(sec // 3600) % 24, sec
            return None, None
        m = self.rx.search(text) if self.rx else None
        if m is None:
            return None, None
        g = m.groups()
        try:
            if self.shape == "iso":
                y, mo, d, h, mi, s = (int(g[0]), int(g[1]), int(g[2]),
                                      int(g[3]), int(g[4]), int(g[5]))
            elif self.shape == "dmy":
                d, mo, y, h, mi, s = (int(g[0]), int(g[1]), int(g[2]),
                                      int(g[3]), int(g[4]), int(g[5]))
            elif self.shape == "clf":
                h = int(g[3])
                mo = MONTH3.get(g[1].lower())
                if mo is None:                  # a month name in another locale
                    return h % 24, None
                d, y, mi, s = int(g[0]), int(g[2]), int(g[4]), int(g[5])
            elif self.shape == "compact":
                h, mi, s = int(g[1]), int(g[2]), int(g[3])
                if g[0] is None:
                    self.dated = False
                    return h % 24, self._wrapped(h * 3600 + mi * 60 + s)
                y, mo, d = int(g[0][:4]), int(g[0][4:6]), int(g[0][6:])
            else:                               # bsd, clock — no year to trust
                h, mi, s = int(g[0]), int(g[1]), int(g[2])
                return h % 24, self._wrapped(h * 3600 + mi * 60 + s)
        except (ValueError, IndexError):
            return None, None
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return h % 24, None
        return h % 24, _days_from_civil(y, mo, d) * 86400.0 + h * 3600 + mi * 60 + s


def hhmmss(reading):
    """A reading -> a clock face. Day-less readings share the arithmetic, so the
    face is right even when the day behind it is arbitrary."""
    if reading is None:
        return "--:--:--"
    t = int(reading) % 86400
    return "%02d:%02d:%02d" % (t // 3600, (t % 3600) // 60, t % 60)


def hhmm(reading):
    if reading is None:
        return "--:--"
    t = int(reading) % 86400
    return "%02d:%02d" % (t // 3600, (t % 3600) // 60)


def span_text(seconds):
    """A span, in the largest unit that still reads as a number."""
    s = int(round(seconds))
    if s < 120:
        return "%dс" % s
    if s < 7200:
        return "%dм" % (s // 60)
    return "%.1fч" % (s / 3600.0)


def spread_note(n, t0, t1, lo, hi):
    """first-seen, last-seen, the span, and the span as a share of the window.

    A group whose occurrences all land inside a sliver of the capture has to look
    different from one spread evenly across it — and the share of the window is
    the one number that separates them without knowing anything about the log."""
    if n < 2 or t0 is None or t1 is None or lo is None or hi is None:
        return ""
    span = max(0.0, t1 - t0)
    window = max(0.0, hi - lo)
    head = "%s→%s %s" % (hhmmss(t0), hhmmss(t1), span_text(span))
    if window <= 0:
        return head
    tight = (window / span) if span > 0 else window
    burst = " ВСПЛЕСК ×%d" % int(tight) if tight >= BURST_FACTOR else ""
    return "%s=%.1f%% окна%s" % (head, 100.0 * span / window, burst)


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


# A number glued to a name is not a measurement. In `ITEM-40044`, `HTTP/1.1`,
# `exec-14`, `/api/v1` and `app.9f2a1c.js` the digits belong to an identifier or
# a version; in `rt=0.003`, `took 1490ms` and `"duration":8` they are preceded by
# punctuation that SEPARATES, and they survive. Measured: without this test the
# rate axis reported the `1.1` out of `HTTP/1.1` as p50 1.100 s for an endpoint
# whose real response time is 0.002 s, and the digits of an item code as
# p50 56323 / p99 99468.
_GLUE = "-_/."


def glued(text, i):
    """Is the number starting at `i` part of a larger non-numeric token?"""
    if i <= 0:
        return False
    c = text[i - 1]
    if c.isalpha():
        return True
    if c in _GLUE:
        return i >= 2 and text[i - 2].isalnum()
    return False


def mask(text, slots, glue=None):
    """-> masked text; appends every numeric slot VALUE to `slots`, in order.

    The slot list is what makes the rate axis format-agnostic: nobody has to know
    that the 6th number on this line is called `duration`. When `glue` is passed
    it collects one flag per slot — True when those digits were part of a larger
    token, i.e. an identifier rather than a measurement.

    The MASKING itself is unchanged either way, and that is deliberate: leaving an
    item code unmasked would give every item its own template and take the rarity
    axis apart. The judgement belongs to the rate axis, not to the template."""
    def repl(m):
        g = m.lastgroup
        if g == "num":
            if len(slots) < MAX_SLOTS:
                slots.append(float(m.group(0)))
                if glue is not None:
                    glue.append(glued(text, m.start()))
            return "#"
        return _PLACEHOLDER[g]
    return MASK_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# is a numeric slot a MEASUREMENT at all?
#
# The rate axis was printing "background did not shift" rows built on things that
# measure nothing. A confident wrong number is worse than a missing one, because
# it is what a hypothesis gets refuted with. Four structural disqualifiers, and
# doubt resolves against the slot:
#   * the digits are part of a larger token          (decided while masking)
#   * the value never moves
#   * it is drawn from a fixed set of two
#   * it only ever rises, with a distinct value per record — a counter or an id
# ---------------------------------------------------------------------------
SLOT_DISTINCT_CAP = 64        # how many distinct values are worth remembering
SLOT_MIN_N = 40               # below this there is nothing to judge
SLOT_MIN_DISTINCT = 3         # two values is a flag, not a measurement
SLOT_CONST_SHARE = 0.99       # one value this often ⇒ constant
SLOT_ID_ORDERED = 0.98        # ... and this ordered, with no ceiling on distinct
                              # values, ⇒ a counter or an identifier
SLOT_LABEL_MAX = 14


class SlotStat(object):
    """Enough about one numeric position to say whether it measures anything.

    Bounded on purpose. The distinct set stops at SLOT_DISTINCT_CAP, and the
    constant test watches the share of the FIRST value rather than the true mode
    — a column that is 99 % one value is 99 % its first value too, and a Counter
    per numeric token on a 134 MB file is not free."""
    __slots__ = ("n", "vals", "over", "glue", "first", "first_n", "rises", "prev")

    def __init__(self):
        self.n = 0
        self.vals = set()
        self.over = False
        self.glue = 0
        self.first = None
        self.first_n = 0
        self.rises = 0
        self.prev = None

    def add(self, v, is_glued):
        self.n += 1
        if is_glued or self.glue:
            # already disqualified and it cannot come back: stop paying for the
            # distinct set and the ordering counter. On an access log this is
            # roughly two numbers in five.
            self.glue += 1
            return
        if self.first is None:
            self.first = v
        if v == self.first:
            self.first_n += 1
        if not self.over:
            self.vals.add(v)
            if len(self.vals) > SLOT_DISTINCT_CAP:
                self.over = True
        if self.prev is not None and v > self.prev:
            self.rises += 1
        self.prev = v

    def merge(self, other):
        """Two slices of one rotated stream are one column. The first-value count
        is only carried when both halves agree on it, which can only make the
        column look LESS constant — the safe direction."""
        if self.first is None:
            self.first, self.first_n = other.first, other.first_n
        elif other.first == self.first:
            self.first_n += other.first_n
        self.n += other.n
        self.glue += other.glue
        self.rises += other.rises
        self.over = self.over or other.over
        if not self.over:
            self.vals |= other.vals
            if len(self.vals) > SLOT_DISTINCT_CAP:
                self.over = True
        return self

    def is_metric(self):
        """-> (True, "") or (False, why)."""
        if self.glue:
            return False, "часть имени"
        if self.n < SLOT_MIN_N:
            return False, "значений мало"
        if not self.over and len(self.vals) < SLOT_MIN_DISTINCT:
            return False, ("константа" if len(self.vals) < 2
                           else "всего 2 значения")
        if self.first_n / float(self.n) >= SLOT_CONST_SHARE:
            return False, "почти константа"
        if self.over and self.rises / float(self.n - 1) >= SLOT_ID_ORDERED:
            return False, "только растёт — счётчик/идентификатор"
        return True, ""


def slot_label(tmpl, si):
    """The characters just before a slot's placeholder in its template.

    `слот#4` on its own is exactly how a byte count gets read as a latency. This
    is a label, not a measurement: a template that contains a literal `#` will
    shift it, which costs a wrong caption and nothing else."""
    pos = -1
    for _ in range(si + 1):
        pos = tmpl.find("#", pos + 1)
        if pos < 0:
            return ""
    return tmpl[max(0, pos - SLOT_LABEL_MAX):pos].strip()[-SLOT_LABEL_MAX:]


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


# ---------------------------------------------------------------------------
# the OUTCOME axis — structural too, and it knows no protocol
#
# Measured: on a 134 MB access log the level axis came out as the request method,
# the path and the user-agent, and never the status code — while a plain count of
# codes >= 500 per ten minutes went from 0 to 1354 and landed exactly on the
# incident minute. The level axis cannot find it: `200` scores badly precisely
# BECAUSE it is a number, and the three slots it does win are taken.
#
# So the outcome axis is discovered separately, by the only shape a result code
# has: three digits, always three, inside one narrow range, drawn from a handful
# of values, on nearly every record. That is enough to keep out the fields it
# keeps being confused with — a duration in milliseconds leaves the range
# constantly, a byte count has hundreds of distinct values, a port has one, and a
# path is not a number at all. No field index and no field name is assumed.
# ---------------------------------------------------------------------------
_STATUS_TOKEN_RE = re.compile(r"^\d{3}$")


def status_axis(hists, n):
    """-> the axis name carrying a result code, or None.

    A column that answers `200` on all four thousand probed records is accepted,
    even though a constant column is indistinguishable from a port. The asymmetry
    is deliberate and it was measured: on the access log below, the first 4000
    records are healthy, so a "needs at least two distinct codes" rule rejected
    the status column — and the thing worth finding was the 5xx that started an
    hour later. A false positive costs one line of the map and no worklist rows,
    because a single class produces no per-bucket comparison; a false negative
    costs the finding. Candidates that already show more than one class win over
    ones that do not, so a real code column outranks a constant port."""
    best = None
    for name, hist in sorted(hists.items()):
        total = sum(hist.values())
        if total < 20 or total < STATUS_MIN_SHARE * n:
            continue
        codes, code_n = set(), 0
        for v, c in hist.items():
            if v and _STATUS_TOKEN_RE.match(v) and STATUS_LO <= int(v) <= STATUS_HI:
                codes.add(v)
                code_n += c
        card = len(codes)
        if not (STATUS_MIN_CARD <= card <= STATUS_MAX_CARD):
            continue
        # Nearly every value has to BE a code — the width test with teeth. A few
        # non-codes are tolerated because real code columns do carry a `-` when
        # the connection died, but a column that is only half codes is a
        # measurement wandering through the range, not an outcome.
        if code_n < STATUS_MIN_SHARE * total:
            continue
        if max(hist[v] for v in codes) / float(code_n) < STATUS_MIN_DOMINANT:
            continue
        classes = len({int(v) // 100 for v in codes})
        score = (1 if classes > 1 else 0, code_n / float(n), -card, name)
        if best is None or score > best[0]:
            best = (score, name)
    return best[1] if best else None


def outcome_rows(axis, hist, buckets, exemplars, lo, hi):
    """Per-bucket counts for every code class that is not the dominant one.

    The dominant class is what this service normally answers; every other class
    is an outcome worth counting over time. Nothing here decides that 5 is worse
    than 4 — the classes are reported in descending order and the reader adjudicates."""
    if not axis or not hist:
        return []
    per_class = Counter()
    for code, c in hist.items():
        per_class[code // 100] += c
    if len(per_class) < 2:
        return []
    dominant = per_class.most_common(1)[0][0]
    b_lo = int(lo // OUTCOME_BUCKET_S) if lo is not None else None
    b_hi = int(hi // OUTCOME_BUCKET_S) if hi is not None else None
    out = []
    for cls, total in sorted(per_class.items(), reverse=True):
        if cls == dominant:
            continue
        series = buckets.get(cls) or {}
        if b_lo is None or not series:
            continue
        span = [series.get(b, 0) for b in range(b_lo, b_hi + 1)]
        if not span:
            continue
        peak = max(span)
        peak_b = b_lo + span.index(peak)
        quiet = percentile(span, 0.50) or 0
        if peak < OUTCOME_MIN_PEAK or peak < RATE_FACTOR * max(quiet, 1):
            continue
        code = min(c for c in hist if c // 100 == cls)
        st, en, disp, rel = exemplars.get(
            code, (1, 1, "", None))
        top = sorted(((b, v) for b, v in series.items() if v), key=lambda t: -t[1])
        shown = " ".join("%s=%d" % (hhmm(b * OUTCOME_BUCKET_S), v)
                         for b, v in sorted(top[:OUTCOME_SERIES_SHOWN]))
        out.append({
            "cls": cls, "axis": axis, "n": total, "peak": peak,
            "peak_at": peak_b * OUTCOME_BUCKET_S, "quiet": quiet,
            "hot": sum(1 for v in span if v), "buckets": len(span),
            "file": rel, "line_start": st, "line_end": en, "display": disp,
            "factor": peak / float(max(quiet, 1)),
            "summary": "класс %dxx: фон %d/интервал → пик %d в %s · всего %d "
                       "в %d из %d интервалов по %s · %s"
                       % (cls, quiet, peak, hhmm(peak_b * OUTCOME_BUCKET_S),
                          total, sum(1 for v in span if v), len(span),
                          span_text(OUTCOME_BUCKET_S), shown),
        })
    return out


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
    """-> (framing, time_shape, level_axes, probe_note, outcome_axis). Reads a
    prefix only."""
    lines = []
    try:
        with read_text(path) as fh:
            for i, raw in enumerate(fh):
                if i >= PROBE_LINES:
                    break
                lines.append(deansi(raw))
    except OSError as e:
        return FRAME_LINE, None, [], "нечитаем: %s" % e, None
    if not lines:
        return FRAME_LINE, None, [], "пустой файл", None

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
    return framing, shape, axes[:LEVEL_AXES_SHOWN], note, status_axis(hists, rn)


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
        self.groups = []          # [(count, start, end, kind, tmpl, display,
                                  #   cite_rel, spread)]
        self.group_total = 0
        self.rate_rows = []
        self.bg_rows = []
        self.out_rows = []
        self.verbatim = None
        self.error = None
        self.hours = []
        # everything below is kept so a rotation family can be re-counted as the
        # one stream it is, without re-reading 160 MB. All of it is small: per
        # TEMPLATE, never per record.
        self.counts = Counter()
        self.first_seen = {}
        self.display = {}
        self.tmpl_ts = {}         # tmpl -> [first_reading, last_reading]
        self.tmpl_hour = {}
        self.per_hour_total = Counter()
        self.slot_pct = {}        # (tmpl, hour, slot) -> (n, p50, p90, p99)
        self.slot_stat = {}       # (tmpl, slot)       -> SlotStat
        self.slot_bad = []        # [(tmpl, slot, why)] — rejected, for the map
        self.level_hist = {}
        self.level_tmpl = {}
        self.ts_lo = None
        self.ts_hi = None
        self.clock_dated = False
        self.outcome_axis = None
        self.status_hist = Counter()
        self.status_bucket = {}   # class -> {bucket: n}
        self.status_first = {}    # code  -> (start, end, display, rel)
        self.stream = None        # the stitched stream this file belongs to
        self.stream_members = []
        self.stream_window = (None, None)


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

    rep.framing, rep.time_shape, axes, rep.time_note, rep.outcome_axis = probe(path)
    rep.level_axes_shown = [(name, hist) for _s, name, hist in axes]
    rep.level_axis = axes[0][1] if axes else None
    # EVERY qualifying axis is carried, not just the best-scoring one. Ranking
    # among them can only be structural (this file owns no severity words), and a
    # structural ranking put a 3-value `channel` field above a 3-value numeric
    # level field on one real corpus. Taking the union costs one extra targeted
    # regex per record and removes the guess.
    extractors = [(name, make_extractor(name)) for name, _h in rep.level_axes_shown]
    get_status = make_extractor(rep.outcome_axis) if rep.outcome_axis else None
    clock = Clock(rep.time_shape)

    counts = Counter()
    first_seen = {}
    display = {}
    tmpl_ts = {}
    per_hour_total = Counter()
    tmpl_hour = defaultdict(Counter)
    slot_res = {}
    slot_stat = {}
    level_hist = defaultdict(Counter)
    level_tmpl = defaultdict(Counter)
    status_hist = Counter()
    status_bucket = defaultdict(Counter)
    status_first = {}
    ts_lo = ts_hi = None
    lines_seen = 0

    try:
        for start, end, text in stream_records(path, rep.framing):
            lines_seen = end
            rep.records += 1
            slots = []
            glue = []
            span = max(1, end - start + 1)
            tmax = TEMPLATE_MAX * min(TEMPLATE_MAX_FACTOR, span)
            masked = mask(text[:MASK_INPUT_MAX], slots, glue)
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
            h, at = clock.read(text)
            if at is not None:
                if ts_lo is None or at < ts_lo:
                    ts_lo = at
                if ts_hi is None or at > ts_hi:
                    ts_hi = at
                w = tmpl_ts.get(tmpl)
                if w is None:
                    tmpl_ts[tmpl] = [at, at]
                elif at < w[0]:
                    w[0] = at
                elif at > w[1]:
                    w[1] = at
            if get_status is not None:
                code = get_status(text)
                if code is not None and len(code) == 3 and code.isdigit():
                    c = int(code)
                    if STATUS_LO <= c <= STATUS_HI:
                        status_hist[c] += 1
                        if c not in status_first and len(status_first) < STATUS_KEEP:
                            status_first[c] = (start, end,
                                               squeeze(shown)[:DISPLAY_MAX], rel)
                        if at is not None:
                            status_bucket[c // 100][int(at // OUTCOME_BUCKET_S)] += 1
            if h is not None:
                per_hour_total[h] += 1
                tmpl_hour[tmpl][h] += 1
                for si, sv in enumerate(slots):
                    key = (tmpl, h, si)
                    r = slot_res.get(key)
                    if r is None:
                        r = slot_res[key] = Reservoir(rng)
                    r.add(sv)
                    sk = (tmpl, si)
                    st = slot_stat.get(sk)
                    if st is None:
                        st = slot_stat[sk] = SlotStat()
                    st.add(sv, glue[si])
    except OSError as e:
        rep.error = str(e)
        return rep

    rep.lines = lines_seen
    rep.distinct = len(counts)
    rep.ratio = (rep.distinct / float(rep.records)) if rep.records else 0.0
    rep.hours = sorted(per_hour_total)
    rep.ts_lo, rep.ts_hi = ts_lo, ts_hi
    rep.clock_dated = bool(clock.dated and ts_lo is not None)
    rep.status_hist = status_hist
    rep.status_bucket = {cls: dict(b) for cls, b in status_bucket.items()}
    rep.status_first = status_first
    if rep.records and rep.ratio > DISTINCT_RATIO_GATE:
        rep.gated = ("доля уникальных форм %.4f > %.2f — это не потоковый лог, "
                     "ось редкости отключена" % (rep.ratio, DISTINCT_RATIO_GATE))
        return rep

    # Everything a rotation family needs to be re-counted as one stream, and
    # nothing else: these are per TEMPLATE, so they are three orders of magnitude
    # smaller than the file they came out of.
    rep.counts = counts
    rep.first_seen = first_seen
    rep.display = display
    rep.tmpl_ts = tmpl_ts
    rep.tmpl_hour = tmpl_hour
    rep.per_hour_total = per_hour_total
    rep.level_hist = level_hist
    rep.level_tmpl = level_tmpl
    rep.slot_pct = slot_summaries(counts, slot_res)
    rep.slot_stat = {k: v for k, v in slot_stat.items()
                     if counts.get(k[0], 0) >= SLOT_STAT_KEEP_MIN}

    slot_ok, rep.slot_bad = judge_slots(rep.slot_stat)

    # ---- axis 1 + 2 -------------------------------------------------------
    rep.groups, rep.group_total, rep.rare_levels = group_rows(
        counts, first_seen, display, tmpl_ts, level_hist, level_tmpl, {},
        ts_lo, ts_hi, args.per_file_cap)

    # ---- axis 3 -----------------------------------------------------------
    if len(rep.hours) >= 2:
        rep.rate_rows, rep.bg_rows = rate_candidates(
            counts, tmpl_hour, per_hour_total, rep.slot_pct, first_seen, display,
            slot_ok, {})

    # ---- axis 4 -----------------------------------------------------------
    rep.out_rows = outcome_rows(rep.outcome_axis, status_hist, rep.status_bucket,
                                status_first, ts_lo, ts_hi)
    return rep


def judge_slots(slot_stat):
    """-> (set of (template, slot) that measure something, [(t, slot, why)])."""
    ok, bad = set(), []
    for (tmpl, si), st in slot_stat.items():
        good, why = st.is_metric()
        if good:
            ok.add((tmpl, si))
        else:
            bad.append((tmpl, si, why))
    return ok, bad


def slot_summaries(counts, slot_res):
    """Per (template, hour, slot): n and the three percentiles.

    Reservoirs cannot be merged across a rotation cut without bias; their
    SUMMARIES do not have to be, because a stitched family is only ever stitched
    when each hour belongs to exactly one of its members."""
    out = {}
    for (tmpl, h, si), r in slot_res.items():
        if counts.get(tmpl, 0) < RATE_MIN_N or r.n < RATE_MIN_HOUR_N:
            continue
        out[(tmpl, h, si)] = (r.n, percentile(r.vals, 0.50),
                              percentile(r.vals, 0.90), percentile(r.vals, 0.99))
    return out


def group_rows(counts, first_seen, display, tmpl_ts, level_hist, level_tmpl,
               owner, lo, hi, per_file_cap):
    """axis 1 + 2 rows — for one file, or for one stitched stream.

    -> (rows, total, rare_levels). A row carries the file it CITES, which is not
    always the file it was counted in: a rotated stream is counted whole and
    cited where the first occurrence physically lives."""
    rare_levels = []
    boosted = set()
    for name, hist in level_hist.items():
        tot = sum(hist.values()) or 1
        for v, c in hist.items():
            if c <= RARE_LEVEL_MAX_N and c / float(tot) < RARE_LEVEL_SHARE:
                rare_levels.append((name, v, c))
                boosted.update(level_tmpl[(name, v)])
    rare_levels.sort(key=lambda t: t[2])

    rows = []
    for tmpl, c in counts.items():
        is_cat = tmpl in boosted
        # A group that is neither rare nor carrying a rare level value is BACKGROUND.
        # Listing it as an anomaly spends a worklist slot on a line that says
        # nothing — and the slot is the scarce thing here, not the line.
        if c > RARE_MAX_N and not is_cat:
            continue
        st, en = first_seen[tmpl]
        w = tmpl_ts.get(tmpl) or (None, None)
        rows.append((c, st, en, "cat" if is_cat else "rare", tmpl, display[tmpl],
                     owner.get(tmpl), spread_note(c, w[0], w[1], lo, hi)))
    # the categorical axis goes first: it exists precisely to surface groups the
    # rarity axis cannot see. Inside each axis, rarest first.
    rows.sort(key=lambda r: (0 if r[3] == "cat" else 1, r[0], r[1]))
    return rows[:max(per_file_cap * 4, 200)], len(rows), rare_levels


def comparable_hours(per_hour_total):
    """First and last hour of the STREAM that is a real hour.

    The floor belongs on the hour, not on the template. Putting it on the template
    silently deletes exactly the shape worth finding: a ramp is rare at the start
    by definition, so a relative floor throws away its first hour and then reports
    the flattened remainder.

    And the floor is measured against a TYPICAL hour, not against the whole
    window. Against the whole window it tightens as the window widens: on a
    stitched ten-hour stream whose quiet half ran at a fifth of the busy half, a
    5 %-of-total floor threw away all seven quiet hours and compared the incident
    with itself."""
    hours = sorted(per_hour_total)
    typical = percentile(list(per_hour_total.values()), 0.50) or 0
    floor = max(RATE_MIN_HOUR_N * 5, RATE_MIN_HOUR_OF_TYPICAL * typical)
    usable = [h for h in hours if per_hour_total[h] >= floor]
    if len(usable) < 2:
        return None, None
    return usable[0], usable[-1]


def rate_candidates(counts, tmpl_hour, per_hour_total, slot_pct, first_seen,
                    display, slot_ok, owner):
    """`slot_ok` is the set of (template, slot) that survived the plausibility
    check — a slot outside it is not offered as a measurement at all. A rate row
    with no usable slot is still a rate row: the SHARE is a measurement."""
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
            if (tmpl, si) not in slot_ok:
                continue
            a = slot_pct.get((tmpl, h0, si))
            b = slot_pct.get((tmpl, h1, si))
            if not a or not b:
                continue
            _na, p50a, p90a, p99a = a
            _nb, p50b, p90b, p99b = b
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
                "line_start": st, "line_end": en, "slot": None,
                "file": owner.get(tmpl)}
        if best:
            base.update({"slot": best[1], "p50": (best[2], best[3]),
                         "p90": (best[4], best[5]), "p99": (best[6], best[7]),
                         "p99_ratio": best[0], "label": slot_label(tmpl, best[1])})
            # WHEN, not just how much. Comparing h0 with h1 says a metric moved
            # and leaves its onset to be guessed — so on D08 the run saw a 5xx
            # outage timed to the interval, a p99 shift with no time at all, and
            # concluded the shift was the outage's consequence. It was its
            # earliest symptom, two hours earlier. The per-hour percentiles were
            # already computed; only the two endpoints were ever printed.
            series = []
            for h in sorted(hc):
                if h < h0 or h > h1:
                    continue
                v = slot_pct.get((tmpl, h, best[1]))
                if v and v[3] is not None:
                    series.append((h, v[3]))
            base["p99_series"] = series
            base["onset"] = hourly_onset(series)
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


def hourly_onset(series):
    """The first hour a metric departs from its own opening baseline.

    `RATE_FACTOR` decides that a metric moved; the same factor decides WHEN, so a
    row can never claim an onset for a shift it did not call significant.

    Returns None when the series never departs — and None is a real answer. A
    metric that was already high in the first hour did not START inside this
    window, and inventing an onset of h0 would place a cause before every event
    in the corpus. That is the failure this whole column exists to prevent, in
    the opposite direction.
    """
    if len(series) < 2:
        return None
    base = series[0][1]
    if not base:
        return None
    for hour, val in series[1:]:
        if val is None:
            continue
        if val >= base * RATE_FACTOR or val <= base / RATE_FACTOR:
            return hour
    return None


def abs_dev(r):
    if r is None:
        return 0.0
    return max(r, 1.0 / r) if r > 0 else 0.0


# ---------------------------------------------------------------------------
# rotation: `<name>.log`, `<name>.log.1`, `<name>.log.1.gz`, `<name>.log.2.gz`
# are ONE stream cut by logrotate. Analysed apart, every count is halved and —
# worse — a before/after lands on opposite sides of the cut, so the comparison
# measures the rotation rather than the incident. Measured: one shape occurring
# seven times inside 284 seconds rendered as `n=4` and `n=3` in two files and
# read as routine background.
#
# The family is found by NAME (any base plus a numeric rotation suffix,
# optionally gzipped — no base name is assumed), but it is ORDERED by the CLOCK,
# never by the suffix: `.1` is older than the live file under logrotate and
# newer under dateext, and getting that backwards inverts every before/after in
# the table. When the evidence will not order it, the family is left alone.
# ---------------------------------------------------------------------------
ROTATION_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\.(?P<idx>\d{1,10})(?:\.gz)?$")


def rotation_families(reports):
    """-> {(directory, base): [report, ...]} for every name-family above one."""
    fam = defaultdict(list)
    for r in reports:
        head, _sep, name = r.rel.rpartition("/")
        m = ROTATION_SUFFIX_RE.match(name)
        fam[(head, m.group("base") if m else name)].append(r)
    return {k: v for k, v in fam.items() if len(v) > 1}


def orderable(members):
    """-> members in chronological order, or None when the evidence will not
    order them confidently.

    Every member has to be a readable log, share the framing and the time shape,
    carry a DATED clock (two files stamped only `13:05:01` have unrelated
    origins), hold a real window, not overlap its neighbours, and claim hours of
    the day no other member claims — the rate axis buckets by hour of day, so two
    members sharing an hour cannot be told apart inside it. Anything short of all
    of that keeps the per-file behaviour: a wrong stitch is worse than none."""
    for r in members:
        if r.error or r.gated or not r.records or not r.clock_dated:
            return None
        if r.ts_lo is None or r.ts_hi is None or not r.hours:
            return None
    if len({r.framing for r in members}) != 1:
        return None
    if len({r.time_shape for r in members}) != 1:
        return None
    order = sorted(members, key=lambda r: (r.ts_lo, r.rel))
    for a, b in zip(order, order[1:]):
        if b.ts_lo < a.ts_hi - STREAM_MAX_OVERLAP_S:
            return None
    seen = set()
    for r in order:
        hrs = set(r.hours)
        if hrs & seen:
            return None
        seen |= hrs
    return order


def stitch(reports, args):
    """Fold every confidently-orderable rotation family into the one stream it is.

    Rows are attributed to the NEWEST member, so the round-robin in the worklist
    gives the stream one turn instead of one turn per slice; each row still cites
    the physical file its first occurrence lives in. Nothing is re-read: the
    per-template residue every file kept is all the arithmetic needs."""
    notes = []
    fams = rotation_families(reports)
    for key in sorted(fams):
        order = orderable(fams[key])
        if order is None:
            continue
        head, base = key
        name = "%s/%s" % (head, base) if head else base
        lo = min(r.ts_lo for r in order)
        hi = max(r.ts_hi for r in order)

        counts = Counter()
        first_seen, display, owner, tmpl_ts = {}, {}, {}, {}
        tmpl_hour = defaultdict(Counter)
        per_hour_total = Counter()
        slot_pct, slot_stat = {}, {}
        level_hist, level_tmpl = defaultdict(Counter), defaultdict(Counter)
        status_hist = Counter()
        status_bucket = defaultdict(dict)
        status_first = {}
        for r in order:
            counts.update(r.counts)
            for t, se in r.first_seen.items():
                if t not in first_seen:
                    first_seen[t] = se
                    display[t] = r.display[t]
                    owner[t] = r.rel
            for t, w in r.tmpl_ts.items():
                cur = tmpl_ts.get(t)
                if cur is None:
                    tmpl_ts[t] = [w[0], w[1]]
                else:
                    cur[0] = min(cur[0], w[0])
                    cur[1] = max(cur[1], w[1])
            for t, hc in r.tmpl_hour.items():
                tmpl_hour[t].update(hc)
            per_hour_total.update(r.per_hour_total)
            slot_pct.update(r.slot_pct)       # hours are disjoint: no collision
            for k, st in r.slot_stat.items():
                cur = slot_stat.get(k)
                if cur is None:
                    cur = slot_stat[k] = SlotStat()
                cur.merge(st)
            for axis, hist in r.level_hist.items():
                level_hist[axis].update(hist)
            for k, c in r.level_tmpl.items():
                level_tmpl[k].update(c)
            status_hist.update(r.status_hist)
            for cls, b in r.status_bucket.items():
                tgt = status_bucket[cls]
                for bk, c in b.items():
                    tgt[bk] = tgt.get(bk, 0) + c
            for code, ex in r.status_first.items():
                status_first.setdefault(code, ex)

        slot_ok, slot_bad = judge_slots(slot_stat)
        primary = order[-1]
        primary.groups, primary.group_total, _rare = group_rows(
            counts, first_seen, display, tmpl_ts, level_hist, level_tmpl, owner,
            lo, hi, args.per_file_cap)
        primary.slot_bad = slot_bad
        primary.rate_rows, primary.bg_rows = [], []
        if len(per_hour_total) >= 2:
            primary.rate_rows, primary.bg_rows = rate_candidates(
                counts, tmpl_hour, per_hour_total, slot_pct, first_seen, display,
                slot_ok, owner)
        primary.out_rows = outcome_rows(
            next((r.outcome_axis for r in order if r.outcome_axis), None),
            status_hist, status_bucket, status_first, lo, hi)
        primary.stream_members = [r.rel for r in order]
        primary.stream_window = (lo, hi)
        for r in order:
            r.stream = name
            if r is not primary:
                # the slice is still listed in the map with its own counts; what
                # it no longer does is contribute a SECOND, halved view of the
                # same stream to the worklist.
                r.groups, r.group_total = [], 0
                r.rate_rows, r.bg_rows, r.out_rows = [], [], []
        notes.append((name, [r.rel for r in order], lo, hi))
    return notes


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
            count, s, e, kind, tmpl, disp, cite_rel, spread = q.pop(0)
            taken[rel] += 1
            # the queue is keyed by the file that OWNS the budget (the stream's
            # newest slice); the citation belongs to whichever slice the first
            # occurrence physically sits in.
            rows.append({"file": cite_rel or rel, "kind": kind, "count": count,
                         "line_start": s, "line_end": e, "display": disp,
                         "spread": spread})
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


def freq_cell(n, spread):
    """`n=7` alone cannot tell a burst from a trickle, and the difference is the
    whole finding. Where there is a clock, the count carries its window."""
    return "n=%d · %s" % (n, spread) if spread else "n=%d" % n


def write_worklist(out_dir, rows, rate_rows):
    path = os.path.join(out_dir, "worklist.tsv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        fh.write("# вердикт: ? не разобрано · D дефект · N норма (только с цифрой) "
                 "· X данных не хватает\n")
        fh.write("# частота: n=<сколько> · <первая>→<последняя> <разброс>=<доля "
                 "окна захвата>; ВСПЛЕСК = всё уместилось в узкое окно\n")
        for i, r in enumerate(rows, 1):
            fh.write("g%03d\t?\t%s\t%s\t%s\t%s\n"
                     % (i, r["kind"], cite(r["file"], r["line_start"],
                                           r["line_end"]),
                        freq_cell(r["count"], r.get("spread")), r["display"]))
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
    """axis-3 and axis-4 candidates, as adjudicable worklist rows. Background rows
    are included on purpose: a refutation nobody was asked for never gets written."""
    # the fairness key is the report that OWNS the budget (one per stream, not one
    # per rotated slice); the citation inside each row points at the slice the
    # record physically lives in, which _rate_row / _out_row resolve.
    moved, flat, outs = [], [], []
    for r in reports:
        for b in r.rate_rows:
            moved.append((b.get("factor", 1.0), r.rel, b))
        for b in r.bg_rows:
            flat.append((b["n"], r.rel, b))
        for b in r.out_rows:
            outs.append((b["factor"], r.rel, b))
    moved.sort(key=lambda t: -t[0])
    flat.sort(key=lambda t: -t[0])
    outs.sort(key=lambda t: (-t[0], t[1]))
    moved = _spread(moved, AXIS3_PER_FILE)
    flat = _spread(flat, AXIS3_BG_PER_FILE, per_slot=AXIS3_BG_PER_FILE)
    # Every half must fit UNDER cap, and none may go negative. Previously
    # `cap - n_bg` went negative for a small cap, and `moved[:negative]` is a
    # drop-last slice rather than an empty one — so a smaller --worklist-cap
    # produced a LARGER file (cap 20 -> 80 rows) and, worse, left build_worklist
    # with max(0, cap-80)=0 rows, deleting the rarity axis that anchors every card.
    # Non-monotonic and silent: the exact shape of bug a model economising context
    # walks straight into.
    n_out = max(0, min(len(outs), OUTCOME_ROW_CAP, cap))
    n_bg = max(0, min(len(flat), AXIS3_BG_SLOTS, cap - n_out))
    n_moved = max(0, min(len(moved), cap - n_bg - n_out))
    rows = []
    for i, (_f, rel, b) in enumerate(outs[:n_out], 1):
        rows.append(_out_row("O%03d" % i, rel, b))
    for i, (_f, rel, b) in enumerate(moved[:n_moved], 1):
        rows.append(_rate_row("S%03d" % i, "rate", rel, b))
    for i, (_f, rel, b) in enumerate(flat[:n_bg], 1):
        rows.append(_rate_row("B%03d" % i, "bg", rel, b))
    return rows


def _rate_row(rid, kind, rel, b):
    bits = ["%02dh→%02dh" % (b["h0"], b["h1"]),
            "доля %.2f%%→%.2f%%" % (100 * b["share0"], 100 * b["share1"])]
    slot = b.get("slot")
    if slot is not None:
        # the caption is why `слот#4` stops being read as a latency when it is a
        # byte count: it is the template text immediately in front of the number.
        lab = b.get("label") or ""
        bits.append("слот#%d%s p50 %s→%s p99 %s→%s"
                    % (slot + 1, " «%s»" % lab if lab else "",
                       fmt_num(b["p50"][0]), fmt_num(b["p50"][1]),
                       fmt_num(b["p99"][0]), fmt_num(b["p99"][1])))
    bits.append("n %d→%d" % (b["n0"], b["n1"]))
    # The series is printed for the same reason the outcome axis prints its
    # intervals: an onset with nothing behind it is one more number to trust.
    series = b.get("p99_series") or []
    if len(series) > 2:
        bits.append("p99 по часам " + " ".join("%02dh=%s" % (h, fmt_num(v))
                                               for h, v in series))
    if b.get("onset") is not None:
        bits.append("сдвиг с %02dh" % b["onset"])
    metric = "доля" if b.get("driver") == "доля" or slot is None \
        else "слот#%d" % (slot + 1)
    return {"id": rid, "kind": kind, "file": b.get("file") or rel,
            "line_start": b["line_start"], "line_end": b["line_end"],
            "n": b["n"], "summary": " ".join(bits), "display": b["display"],
            "factor": b.get("factor", 1.0), "metric": metric,
            "t0": "%02dh" % b["h0"], "t1": "%02dh" % b["h1"]}


def _out_row(rid, rel, b):
    return {"id": rid, "kind": "out", "file": b.get("file") or rel,
            "line_start": b["line_start"], "line_end": b["line_end"],
            "n": b["n"], "summary": "ось «%s» %s" % (b["axis"], b["summary"]),
            "display": b["display"], "factor": b["factor"],
            "metric": "исход %dxx" % b["cls"],
            "t0": hhmm(b["peak_at"]), "t1": hhmm(b["peak_at"])}


def write_axis3(out_dir, rate_rows):
    path = os.path.join(out_dir, "axis3.tsv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# id\tось\tсдвиг\tфайл\tметрика\tчас_от\tчас_до\tизмерение\tформа\n")
        fh.write("# ось rate = сдвинулось, ось bg = ФОН, не сдвинулось "
                 "(это тоже измерение, и оно опровергает), ось out = исход/код "
                 "результата по интервалам\n")
        for r in rate_rows:
            fh.write("%s\t%s\tx%.2f\t%s\t%s\t%s\t%s\t%s\t%s\n"
                     % (r["id"], r["kind"], r["factor"], r["file"], r["metric"],
                        r["t0"], r["t1"], r["summary"], r["display"][:200]))
    return path


SLOT_BAD_SHOWN = 6


def _worst_bad(bad):
    """One line per rejected slot would be the log again. Keep one example per
    REASON — the reader needs to know a slot was dropped and why, not which of
    four hundred templates it was dropped in."""
    seen, out = set(), []
    for t, si, why in sorted(bad, key=lambda x: (x[2], x[0], x[1])):
        if why in seen:
            continue
        seen.add(why)
        out.append((t, si, why))
        if len(out) >= SLOT_BAD_SHOWN:
            break
    return out


def outcome_map_lines(r):
    """The outcome axis over time, one line per non-dominant code class.

    Only buckets that are not empty are printed, newest facts first: a class that
    fires in three of sixty intervals is a different animal from one spread over
    all sixty, and the row has to make that visible without printing sixty zeros."""
    out = []
    if not r.status_bucket or r.ts_lo is None:
        return out
    per_class = Counter()
    for code, c in r.status_hist.items():
        per_class[code // 100] += c
    if len(per_class) < 2:
        return out
    dominant = per_class.most_common(1)[0][0]
    b_lo, b_hi = (int(r.ts_lo // OUTCOME_BUCKET_S),
                  int(r.ts_hi // OUTCOME_BUCKET_S))
    for cls in sorted(per_class, reverse=True):
        if cls == dominant:
            continue
        series = r.status_bucket.get(cls) or {}
        hot = sorted((b for b in series if series[b]))
        if not hot:
            continue
        peak_b = max(hot, key=lambda b: series[b])
        shown = " ".join("%s=%d" % (hhmm(b * OUTCOME_BUCKET_S), series[b])
                         for b in hot[:12])
        out.append("    класс %dxx: %d за %d из %d интервалов по %s · пик %d в "
                   "%s · %s%s"
                   % (cls, per_class[cls], len(hot), b_hi - b_lo + 1,
                      span_text(OUTCOME_BUCKET_S), series[peak_b],
                      hhmm(peak_b * OUTCOME_BUCKET_S), shown,
                      " …" if len(hot) > 12 else ""))
    return out


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
    a("«ПОТОК» — файл сшит с ротацией и посчитан вместе с ней, одним потоком.")
    a("«ось исхода» — поле с кодом результата, найденное по форме, а не по имени.")
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
        if r.ts_lo is not None and r.ts_hi is not None:
            a("  окно: %s→%s (%s)" % (hhmmss(r.ts_lo), hhmmss(r.ts_hi),
                                      span_text(r.ts_hi - r.ts_lo)))
        if r.stream and r.stream_members:
            a("  ПОТОК «%s»: %s" % (r.stream, " → ".join(r.stream_members)))
            a("    сшит по ротации и упорядочен по часам; счёт, всплески и темп "
              "для всего потока считаны здесь, окно %s→%s (%s)"
              % (hhmmss(r.stream_window[0]), hhmmss(r.stream_window[1]),
                 span_text(r.stream_window[1] - r.stream_window[0])))
        elif r.stream:
            a("  ПОТОК «%s»: этот срез посчитан вместе с ротацией — его строки "
              "рабочего списка стоят под потоком" % r.stream)
        if r.gated:
            a("  ОСЬ РЕДКОСТИ ОТКЛЮЧЕНА: %s" % r.gated)
        for name, hist in r.level_axes_shown:
            tot = sum(hist.values()) or 1
            vals = ", ".join("%s=%d (%.1f%%)" % (v, c, 100.0 * c / tot)
                             for v, c in hist.most_common(12))
            a("  ось «%s» (%d значений): %s" % (name, len(hist), vals))
        if r.outcome_axis and r.status_hist:
            tot = sum(r.status_hist.values()) or 1
            a("  ось исхода «%s» (%d значений): %s"
              % (r.outcome_axis, len(r.status_hist),
                 ", ".join("%d=%d (%.1f%%)" % (v, c, 100.0 * c / tot)
                           for v, c in r.status_hist.most_common(12))))
            for line in outcome_map_lines(r):
                a(line)
        if r.rare_levels:
            a("  редкие значения оси по всему файлу: "
              + ", ".join("%s→%s=%d" % (n, v, c)
                          for n, v, c in r.rare_levels[:12]))
        if r.slot_bad:
            a("  числовые слоты, отклонённые как НЕ измерения (%d): %s"
              % (len(r.slot_bad),
                 ", ".join("слот#%d «%s» — %s" % (si + 1, slot_label(t, si), why)
                           for t, si, why in _worst_bad(r.slot_bad))))
        left = trunc.get(r.rel)
        if left:
            a("  TRUNC=%d — форм в файле больше, чем попало в рабочий список" % left)
        a("")
    a("=" * 78)
    a("РАБОЧИЙ СПИСОК: %d строк (потолок %d, на файл %d)"
      % (len(rows) + len(rate_rows), args.worklist_cap, args.per_file_cap))
    a("  из них ось редкости/категории: %d, ось темпа/исхода: %d"
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
    # A rotated stream is re-counted as one BEFORE anything is ranked: counting it
    # per slice halves every count and puts a before/after on opposite sides of
    # the cut. Nothing is re-read — every file kept its per-template residue.
    stitch(reports, args)

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
