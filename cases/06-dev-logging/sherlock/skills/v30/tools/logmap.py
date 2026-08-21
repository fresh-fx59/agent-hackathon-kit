#!/usr/bin/env python3
"""logmap — turn a directory of raw logs into three small files the model can work
from: a map, a worklist of anomalies, and a rate table.

    python3 logmap.py ./logs --out ./work
    python3 logmap.py ./logs --out ./work --worklist-cap 250 --per-file-cap 40

Writes `<out>/map.txt`, `<out>/worklist.tsv`, `<out>/axis3.tsv`. Nothing is written
next to the corpus (it is often read-only). stdout is a bounded summary — never the
raw material.

Why this exists. Every "measured" note in this file is a POINTER: the number, the
corpus it was taken on and the date live in `skills/DESIGN-EVIDENCE.md` in the
repo — one directory above the arms, so it is not copied into a workspace with
the skill. A tool that is itself under measurement may state a general property;
it may not carry a fact about the corpus it is being scored on.

* A run's result is decided almost entirely by WHICH lines it looked at. Selecting
  them by hand costs a dozen wide tool calls and still misses whole files.
* Severity is not a word. The field that carries the level differs from file to
  file — a pipe column, a `KEY=value` field in any language, a JSON field, a
  whitespace token — so any fixed vocabulary is blind to some of them, and this
  tool carries NO severity dictionary at all: the axis is discovered from the
  data, by shape, and its whole value histogram is printed. EVIDENCE §E1.
* An hour extractor that only understands `HH:MM:SS` returns nothing — silently,
  not as an error — on whole files. So seven time shapes are probed, the
  numeric-epoch one is key-agnostic and float-aware, and a file with no usable
  time axis is announced loudly instead of being quietly dropped from the rate
  pass. EVIDENCE §E2.
* Sampling and rare-event detection are contradictory. The previous map tool moved
  to 1-in-N sampling on big files, which is exactly where the single decisive record
  lives. Every record is read here; the cost is bounded by capping OUTPUT, not input.
* A COUNT is not an observation. A handful of records of one shape inside a few
  minutes of a long capture reads as routine background when it is rendered as
  two small counts. Every repeated group now carries first-seen, last-seen and
  the share of the capture window it occupies. EVIDENCE §E3.
* A rotated file is not a file. `<name>.log` and `<name>.log.1.gz` are one stream cut
  by logrotate; analysed apart, every count is halved and a before/after lands on
  opposite sides of the cut, measuring the rotation instead of the incident.
* A number is not a measurement. The rate axis was reporting the digits out of an
  item code as a p50, and the `1.1` out of `HTTP/1.1` as a latency in seconds for
  an endpoint whose real response time is a fraction of it. A confident wrong
  number is worse than a missing one, because it is what a hypothesis gets
  refuted with. EVIDENCE §E4.

Six axes, in one streaming pass per file:

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
  axis 5  actor       — masking exists so records collapse to a template, and the
                        values it removes are the PARTIES: who, and from where.
                        A party that first appears after the stream has already
                        established its population is a state transition, and
                        that is a claim about WHEN, not about how rare it is.
                        Measured on a VPN log whose every template first occurs
                        early, so every rarity row points at the wrong end of the
                        file, while the session that matters is a party absent
                        from most of it. Rarest-first does NOT find that party —
                        several internal pool addresses are rarer. Latest-first
                        does. EVIDENCE §E23.
  axis 6  excursion   — a measurement's signal is a LEVEL, not a shape, and an
                        excursion is a departure that RETURNS. axis 3 compares
                        the first comparable hour with the last, which is a
                        DRIFT question: any spike that recovers inside the
                        window is invisible to it at every threshold. So the
                        hour whose median is furthest above the file's own
                        typical hour is offered too, and only when a baseline
                        hour sits on each side of it — which is also what keeps
                        a counter out, since a counter never comes back.
                        Measured on a sampled metric log whose CPU field sits
                        an order of magnitude above the file's typical hour for
                        exactly one hour, with baseline hours on both sides.
                        axis 3 saw nothing: a slow sampler produces fewer
                        records an hour than its comparable-hour floor.
                        EVIDENCE §E24.
  axis 0  floor       — the last resort, and the reason none of the four above can
                        end in silence. A file whose every record is a different
                        shape has no "rare" record, and until v17 that fell
                        through to no worklist rows at all — including, on a real
                        testbed, the two files that carried the most hostile
                        traffic in the whole bundle (EVIDENCE §E18). So a file
                        that produces no axis-1/2 row is given a FLOOR of at
                        least one row, picked by whatever still works when every
                        shape is unique — a non-dominant outcome code, the rarest
                        value of a level axis, the fullest hour, and failing all
                        of those the first and last record. Every such row says
                        `code` / `level` / `burst` / `edge` in its axis column,
                        because a floor row is a weaker claim than a rare one and
                        the reader has to be able to tell.

Two budgets, and they are the same idea one level apart:

* the WORKLIST is capped per host (v15) — a bundle from N machines is N corpora,
  not one, so one cap divided N ways is a handful of rows a machine (EVIDENCE
  §E15);
* the MAP is capped per host too (v17) — it was not, and on a large bundle
  `map.txt` came out at millions of bytes while SKILL.md told the model to read
  it, most of them small files quoted verbatim. Now every host gets its own
  `map-<host>.txt`, `map.txt` is the index, and anything folded to one line is
  counted and announced instead of vanishing. EVIDENCE §E8.

Stdlib only, no network, no config.
"""
import argparse
import calendar
import gzip
import json
import os
import random
import re
import secrets
import stat
import sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# knobs — every one of them bounds OUTPUT, never input
# ---------------------------------------------------------------------------
PROBE_LINES = 4000            # how much of a file decides its framing and time shape
SMALL_FILE_BYTES = 4096       # below this a file is quoted whole into the map
VERBATIM_LINE_CHARS = 300     # per quoted line; past this the quote is not the line
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
                              # The cut is not a taste: on a benchmark corpus
                              # every planted defect anchors at or below it,
                              # measured independently of the design.
                              # EVIDENCE §E7.
FALLBACK_PER_FILE = 6         # the floor is a floor, not a flood: a file the
                              # rarity axis cannot read must not be able to buy
                              # more attention than one it can
FALLBACK_CODE_MAX = 3         # non-dominant outcome codes offered per file
FALLBACK_LEVEL_MAX = 2        # rarest level values offered per file
MAP_HOST_BYTES = 150000       # per-host ceiling on the map, in BYTES. A single-
                              # host bundle stays under it and is untouched; the
                              # multi-host bundles this was measured on rendered
                              # to millions of bytes without it. ~37k tokens is a
                              # budget a model can actually spend on ONE machine.
                              # EVIDENCE §E8.
# --- axis 5, the ACTOR ------------------------------------------------------
# Every threshold here is a STRUCTURAL gate on the field, never on a value. The
# axis may not know an address from an address; all it knows is that a field has
# a population and that one member of it arrived late.
ACTOR_CARD_MAX = 64           # more distinct parties than this and the field has
                              # no population at all — an access log under a
                              # scanner has a fresh client on every line, and
                              # "new" there means nothing. Same idea as
                              # DISTINCT_RATIO_GATE, one column down.
ACTOR_MIN_N = 60              # records carrying a party. Below this "the first
                              # half of the stream" is not a baseline.
ACTOR_LATE_MAX = 3            # newcomers allowed before the axis gives up: a
                              # stream where four parties are new never
                              # established one, and silence beats a confident
                              # wrong row.
ACTOR_PER_FILE = 3            # ... and it may never spend more than this.

SHERLOCK_MARKER_DIR = ".sherlock"
SHERLOCK_MARKER_FILE = "active.json"


def _real(path):
    return os.path.realpath(os.path.abspath(path))


def _inside(path, root):
    try:
        return os.path.commonpath([_real(path), _real(root)]) == _real(root)
    except (TypeError, ValueError):
        return False


def _clean_relpath(value):
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or (len(raw) >= 3 and raw[1] == ":" and raw[2] == "/"):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _safe_out_file(out_dir, relpath):
    rel = _clean_relpath(relpath)
    if rel is None:
        return None
    path = os.path.join(out_dir, rel.replace("/", os.sep))
    if not _inside(path, out_dir) or os.path.islink(path) or not os.path.isfile(path):
        return None
    return path


def _open_out_dir(out_dir):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(out_dir, flags)
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        os.close(fd)
        raise OSError("output is not a directory")
    return fd


def _write_text_atomic(out_dir, relpath, text):
    rel = _clean_relpath(relpath)
    if rel is None or "/" in rel:
        raise OSError("unsafe output path: %s" % relpath)
    dir_fd = None
    tmp_name = None
    try:
        dir_fd = _open_out_dir(out_dir)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        for _attempt in range(16):
            tmp_name = ".%s.%s.tmp" % (rel, secrets.token_hex(16))
            try:
                fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
                break
            except FileExistsError:
                tmp_name = None
        else:
            raise OSError("cannot allocate output temp file")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, rel, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
    finally:
        if dir_fd is not None:
            if tmp_name:
                try:
                    os.unlink(tmp_name, dir_fd=dir_fd)
                except OSError:
                    pass
            os.close(dir_fd)
    return os.path.join(out_dir, rel)


def _valid_generated_host_artifact(worklist_rel, map_rel):
    wl = _clean_relpath(worklist_rel)
    mp = _clean_relpath(map_rel) if map_rel else ""
    if not wl or "/" in wl or not (wl.startswith("worklist-") and wl.endswith(".tsv")):
        return False
    if mp and ("/" in mp or not (mp.startswith("map-") and mp.endswith(".txt"))):
        return False
    return True


def _prior_host_artifacts(out_dir):
    hosts_path = os.path.join(out_dir, "hosts.tsv")
    if not os.path.lexists(hosts_path):
        return []
    if os.path.islink(hosts_path) or not os.path.isfile(hosts_path):
        raise OSError("untrusted prior hosts.tsv")
    targets = ["hosts.tsv"]
    rows = 0
    try:
        with open(hosts_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 7 or not _valid_generated_host_artifact(cols[5], cols[6]):
                    raise OSError("malformed prior hosts.tsv")
                if not _safe_out_file(out_dir, cols[5]):
                    raise OSError("prior hosts.tsv names unsafe worklist")
                targets.append(_clean_relpath(cols[5]))
                if cols[6].strip():
                    if not _safe_out_file(out_dir, cols[6]):
                        raise OSError("prior hosts.tsv names unsafe map")
                    targets.append(_clean_relpath(cols[6]))
                rows += 1
    except OSError:
        raise
    if not rows:
        raise OSError("empty prior hosts.tsv")
    return targets


def retire_prior_host_artifacts(out_dir):
    """Retire only generated files named by a previously valid hosts.tsv."""
    try:
        targets = _prior_host_artifacts(out_dir)
    except OSError as e:
        sys.exit("небезопасный прежний work/hosts.tsv: %s; убери его вручную" % e)
    for relpath in targets:
        path = os.path.join(out_dir, relpath.replace("/", os.sep))
        try:
            if _safe_out_file(out_dir, relpath):
                os.remove(path)
        except OSError:
            pass


def _open_marker_dir(workspace):
    marker_dir = os.path.join(workspace, SHERLOCK_MARKER_DIR)
    try:
        os.mkdir(marker_dir)
    except FileExistsError:
        pass
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(marker_dir, flags)
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        os.close(fd)
        raise OSError("marker parent is not a directory")
    return fd


def write_active_marker(corpus, out_dir, mode="single", worklists=None, hosts=None, hosts_manifest=None):
    """Best-effort Stop-hook state, written only after the map exists."""
    workspace = _real(os.getcwd())
    out_real = _real(out_dir)
    if not _inside(out_real, workspace):
        return
    rel_worklists = []
    for item in (worklists or ["worklist.tsv"]):
        rel = _clean_relpath(item)
        if rel is None:
            return
        rel_worklists.append(rel)
    if mode not in ("single", "multi"):
        return
    data = {
        "version": 30,
        "active": True,
        "workspace": workspace,
        "skill_root": _real(os.path.dirname(os.path.dirname(__file__))),
        "corpus": _real(corpus),
        "out": out_real,
        "mode": mode,
        "worklists": rel_worklists,
    }
    if hosts_manifest:
        data["hosts_manifest"] = _clean_relpath(hosts_manifest)
    if hosts:
        data["hosts"] = hosts
    dir_fd = None
    tmp_name = None
    try:
        dir_fd = _open_marker_dir(workspace)
        payload = (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        for _attempt in range(16):
            tmp_name = ".active.%s.tmp" % secrets.token_hex(16)
            try:
                fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
                break
            except FileExistsError:
                tmp_name = None
        else:
            raise OSError("cannot allocate marker temp file")
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, SHERLOCK_MARKER_FILE, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
    except OSError as e:
        sys.stderr.write("! маркер Sherlock не записан (%s); основной путь не меняется\n" % e)
    finally:
        if dir_fd is not None:
            if tmp_name:
                try:
                    os.unlink(tmp_name, dir_fd=dir_fd)
                except OSError:
                    pass
            os.close(dir_fd)

# --- axis 6, the EXCURSION --------------------------------------------------
METRIC_MIN_SHARE = 0.60       # a measurement is on most records or it is not the
                              # file's measurement
METRIC_MIN_DISTINCT = 20      # a field with few values is a CATEGORY (axis 2's
                              # job); a measurement moves continuously
METRIC_NUMERIC_SHARE = 0.95   # ... and its values parse as numbers
METRIC_MAX_DOMINANT = 0.25    # ... and NO value owns a quarter of the records.
                              # Deliberately the same 0.25 `axis_score` requires
                              # a level axis to EXCEED: the two axes partition
                              # the numeric fields on one threshold instead of
                              # each carrying its own. Measured on a reused
                              # thread handle — many values, one of them owning
                              # half the records — which without this line
                              # produced the only false peak row across three
                              # corpora. EVIDENCE §E9.
METRIC_AXES = 2               # how many measurements are followed per file
PEAK_MIN_HOURS = 4            # a peak needs a baseline on both sides to be a
                              # peak, and two hours cannot supply one
PEAK_BACK = 1.5               # what "came back to baseline" means, as a multiple
                              # of the typical hour. RATE_FACTOR decides that a
                              # metric departed; this decides that it returned,
                              # and the gap between them is what a counter can
                              # never cross.
PEAK_PER_FILE = 2

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

HOST_MAX_DEPTH = 3            # deeper than this and "host" has stopped meaning host
ROOT_BUCKET = "(root)"        # files that sit above every detected host root
HOST_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

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
# A key line inside a blank-line-separated block. `=` alone was too narrow, and
# so was a key of only word characters: apt's history.log writes `Start-Date: …`
# and wazuh's alerts.log writes `Rule: 80730 …`. Both were read one physical line
# at a time, so a six-line alert counted as six events and the tempo axis was
# measuring the log's line-wrapping rather than the host's behaviour.
# `-` and `.` in the key, and `:` as a separator, are shape — not vocabulary.
BLOCK_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*[:=]")

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

# A grammar that is one record per line BY CONSTRUCTION. Where a line parses end
# to end as a common-log-format request — host, ident, user, bracketed stamp,
# quoted request line, status, size — the format has already said where the
# record ends and no statistic gets a vote. A full parse or nothing: half a
# match is not a grammar.
#
# Deliberately consulted BELOW the other three framings, so it can only ever
# claim a file that would otherwise fall through to the last-resort key search
# — the branch that mistook a request's own timestamp for a transaction id and
# fused every request inside one second into a single record.
#
# Syslog is not listed here on purpose, though it is just as much a one-line
# grammar: LEAD_TS_RE already frames a strict `ts host proc[pid]: msg` line one
# record per line, AND it keeps the wrapped continuation lines that a strict
# grammar would split off into records of their own. Adding it here could only
# make that worse. EVIDENCE §E16 and the v23 section.
CLF_LINE_RE = re.compile(
    r'^\S+ \S+ \S+ \[\d{2}/[A-Za-z]{3}/\d{4}(?::\d{2}){3} [-+]\d{4}\]'
    r' "[^"]*" \d{3} (?:\d+|-)')
SINGLE_LINE_MIN_SHARE = 0.90

# A fifth framing: consecutive lines held together by a shared CORRELATION TOKEN
# rather than by a blank line or a leading timestamp. auditd is the case that
# forced it — one logical event is four physical lines that share
# `msg=audit(<epoch>.<ms>:<serial>)`:
#
#   type=SYSCALL   msg=audit(1758115137.498:5): … syscall=59 …
#   type=EXECVE    msg=audit(1758115137.498:5): argc=2 a0="wget" a1="http://…/x"
#   type=PATH      msg=audit(1758115137.498:5): item=0 name="/usr/bin/wget"
#   type=PROCTITLE msg=audit(1758115137.498:5): proctitle=2F62696E2F62617368
#
# Read line-by-line, the arguments of a command are never in the same record as
# the syscall that ran them, which is most of the reason to read auditd at all.
#
# The framing value carries the field it grouped on, as "key:<field>", so the map
# says WHICH token made the record and a reader can check it. Use frame_base()
# whenever you mean "what kind of framing is this".
FRAME_KEY = "key"

KEY_FRAME_MIN_LINES = 20      # below this a run length means nothing
KEY_FRAME_MIN_RUN = 1.5       # mean consecutive-equal run; 1.0 means no grouping
KEY_FRAME_MIN_DISTINCT = 0.05  # of n — a near-constant is a column, not a record id
KEY_FRAME_MIN_CONTIGUOUS = 0.95  # each value must form ONE run, see below
KEY_FRAME_MIN_SAME_INSTANT = 0.90  # the lines of one record share one timestamp
KEY_FRAME_JSON_GUARD = 0.90   # share of lines that are whole JSON objects
# How much of a candidate value the identity test reads. Long enough that a
# transaction id is never judged on a truncated prefix, short enough that a
# whole JSON payload is not held in a set. Only the clock test uses it.
IDENT_VALUE_MAX = 200

# Share of the worklist reserved for state artefacts (configs, rulesets, dropped
# tooling). Small, because they have no time axis and most of them are noise —
# never zero, because some of them are the payload.
STATE_SHARE = 0.10
# And a hard per-file cap inside that share. Measured: without it, two threat-intel
# IP blocklists took fifteen rows each. A file of nothing but hostile IPs is a
# rare-value goldmine to a format-blind counter and pure misdirection to a
# reader — the addresses are what the sensor was told to WATCH FOR, not what the
# host did. What matters about a state artefact is that it is there and what it
# is, not its fifteen rarest tokens. EVIDENCE §E10.
STATE_PER_FILE_CAP = 3


AXIS_RU = {
    "stream": "поток",
    "stream-sparse": "поток (разрежённый)",
    "stream-table": "поток (таблица)",
    "stream-unordered": "поток (без порядка)",
    "state": "состояние",
    "binary": "двоичный",
    "empty": "пустой",
}


def frame_base(framing):
    """`key:kv:msg` -> `key`; every other framing is already its own base."""
    return framing.split(":", 1)[0] if framing.startswith(FRAME_KEY + ":") else framing


def frame_field(framing):
    """The field a key-framed file grouped on, or None."""
    return framing.split(":", 1)[1] if framing.startswith(FRAME_KEY + ":") else None

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
    # left guard rejects it. Measured: without this row the two largest files of a
    # benchmark corpus — over a quarter of it — get NO time axis at all.
    # EVIDENCE §E2.
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

    Measured on a corpus this tool had never seen: long block identifiers divide
    into a perfectly plausible microsecond epoch, so every record got an hour and
    the file was handed a time axis made of random numbers. A clock has two
    properties an identifier does not — it advances, and it covers one capture,
    not three centuries. EVIDENCE §E5."""
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
# shape occurred a handful of times inside a few minutes of a long capture, and
# because the capture straddled a rotation it rendered as two small counts in two
# files — which reads as routine background and is the exact opposite of what it
# was. An hour label cannot tell a burst from a trickle; a first-seen, a
# last-seen and the share of the window they span can. EVIDENCE §E3.
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
# rate axis reported the `1.1` out of `HTTP/1.1` as a p50 latency in SECONDS for
# an endpoint answering in milliseconds, and the digits of an item code as a
# percentile pair. EVIDENCE §E4.
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


def mask(text, slots, glue=None, actors=None):
    """-> masked text; appends every numeric slot VALUE to `slots`, in order.

    The slot list is what makes the rate axis format-agnostic: nobody has to know
    that the 6th number on this line is called `duration`. When `glue` is passed
    it collects one flag per slot — True when those digits were part of a larger
    token, i.e. an identifier rather than a measurement.

    `actors` collects the values this pass replaces with `<ip>`. They are free
    here — the alternation already matched them — and they are the only thing in
    a record that names a PARTY in a format-agnostic way. Everything else that
    identifies who did something (a user, a host, a service) is spelled
    differently in every log; a dotted quad is not.

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
        if g == "ip" and actors is not None:
            actors.append(m.group(0))
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


def make_extractor(axis, cap=VALUE_MAX):
    """One axis name -> a cheap function record_text -> value|None.

    The probe uses the broad four-extractor sweep once; the full pass must not,
    so each chosen axis is compiled down to a single targeted matcher.

    `cap` is the length a value is cut to. The default is what every counting
    caller wants: a histogram of 24-character keys is cheap and a histogram of
    whole payloads is not. IDENTITY is a different question from frequency —
    a transaction id whose serial sits at the END is truncated away by a short
    cap, so the one caller that asks "is this field only a clock" reads the
    value whole. EVIDENCE §E16 and the v23 section."""
    kind, _sep, key = axis.partition(":")
    if kind == "json":
        rx = re.compile(r'"%s"\s*:\s*(?:"([^"\\]{1,%d})"'
                        r'|(-?\d{1,6}(?:\.\d{1,6})?)|(true|false))'
                        % (re.escape(key), cap))

        def get_json(text):
            m = rx.search(text)
            return (m.group(1) or m.group(2) or m.group(3)) if m else None
        return get_json
    if kind == "kv":
        rx = re.compile(r"(?<![\w\-])%s=([^\s|,;'\"]{1,%d})"
                        % (re.escape(key), cap))

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
            return cell if 0 < len(cell) <= cap else None
        return get_pipe

    def get_ws(text):
        parts = text.split(None, idx + 1)
        if len(parts) <= idx:
            return None
        tok = parts[idx]
        return tok if 0 < len(tok) <= cap else None
    return get_ws


_NUM_VALUE_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def metric_axes(seen, hists, rn):
    """-> the field names that carry a MEASUREMENT, at most METRIC_AXES of them.

    The mirror image of `axis_score`, and deliberately built out of the same
    probe. A LEVEL axis is a field with few values, one of them dominant, and
    words rather than payload; a MEASUREMENT is the opposite of all three — many
    values, none dominant, all of them numbers. Nothing here knows what the
    field is called, and that matters: on one corpus the measurement is a
    percentage nested under a host key, on the next it is `rt`, `took_ms` or the
    6th pipe column, and a name list would be blind to most of them.

    NAME-keyed, not position-keyed, and that is the whole reason this axis does
    not reuse the numeric SLOTS. A slot is the Nth number in the record, and a
    JSON writer that emits its keys in a different order on every line makes the
    same slot index a measurement on one record and the exponent of a float on
    the next — measured, and `judge_slots` correctly threw every slot away as
    "part of a name". A field looked up by its key survives the shuffle.
    EVIDENCE §E11."""
    out = []
    for k, c in seen.items():
        share = c / float(rn)
        if share < METRIC_MIN_SHARE:
            continue
        hist = hists[k]
        if len(hist) < METRIC_MIN_DISTINCT:
            continue
        total = sum(hist.values()) or 1
        num = sum(n for v, n in hist.items() if v and _NUM_VALUE_RE.match(v))
        if num / float(total) < METRIC_NUMERIC_SHARE:
            continue
        if max(hist.values()) / float(total) >= METRIC_MAX_DOMINANT:
            continue
        # RANKED BY REPETITION, not by variety, and the sign matters: a
        # measurement is a LEVEL, so records share it, while an identifier is
        # unique by construction and would win any "most distinct values" sort.
        # Measured on a metric log whose collection timer is nearly unique per
        # record while the CPU reading repeats: sorted the other way, the timer
        # takes the slot from the CPU. EVIDENCE §E11.
        out.append((share, len(hist) / float(c), k))
    out.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [k for _s, _r, k in out[:METRIC_AXES]]


# ---------------------------------------------------------------------------
# the OUTCOME axis — structural too, and it knows no protocol
#
# Measured on a large access log: the level axis came out as the request method,
# the path and the user-agent, and never the status code — while a plain count of
# codes >= 500 per ten minutes went from zero to four digits and landed exactly on
# the incident minute. The level axis cannot find it: `200` scores badly precisely
# BECAUSE it is a number, and the three slots it does win are taken.
# EVIDENCE §E6.
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
    is deliberate and it was measured: on a real access log every probed record
    is healthy, so a "needs at least two distinct codes" rule rejected the status
    column — and the thing worth finding was the 5xx that started an hour later
    (EVIDENCE §E6). A false positive costs one line of the map and no worklist rows,
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


# ---------------------------------------------------------------------------
# stream or state
#
# A real evidence bundle is not a folder of logs. It is a folder of logs NEXT TO
# a copy of /etc. Measured on two multi-host bundles, each host carrying a full
# /var/log and a full /etc: Step 1 spent nearly half its worklist on files under
# configs/, and a host can easily hold twenty times more config files than logs.
#
# It is worse than wasted budget. On one such host the loudest config file was a
# threat-intel RULESET, and what landed in the worklist was its third line: a
# rule that blocks a known hostile host, offered as a rare value.
#
# A threat-intel ruleset is a list of what to LOOK FOR. Handed to a model as a
# rare-value anomaly it is an actively misleading input, not merely a noisy one.
# EVIDENCE §E12.
#
# The separator must not be a filename list, a path rule or a word list — those
# are exactly the format knowledge this tool is forbidden to hold. So it is
# arithmetic: a LOG STREAM is a file whose lines carry timestamps and whose
# timestamps do not go backwards.
#
# Measured on two bundles against their own logs/ vs configs/ split: precision
# 1.000 on both, recall above 0.84, and the great majority of text files shed.
# Zero config files were called a stream in either corpus. EVIDENCE §E12.
#
# `state` NEVER means discard. On the corpora this was measured on, the files it
# returns include dropped tooling and dumped credentials — a planted
# authorized_keys, an edited sshd_config, a webshell are all evidence that
# legitimately has no clock. They get their own small budget instead of competing
# for the stream one.
# ---------------------------------------------------------------------------
AXIS_SHAPES = [
    ("iso", re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\s*\d{2}:\d{2}:\d{2}")),
    ("bsd", re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                       r"\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}")),
    ("epoch_ms", re.compile(r"\b1[0-9]{9}\.[0-9]{3}\b")),
    ("epoch", re.compile(r"\b1[0-9]{9}\b")),
    ("apache", re.compile(r"\[\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2}")),
    ("dotted", re.compile(r"\b\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}")),
    # Slash dates of no fixed width and no knowable field order. Suricata on one
    # host writes BOTH, in two files, in the same run: stats.log says
    # "Date: 9/17/2025 -- 13:13:45" (month first) and suricata.log says
    # "17/9/2025 -- 13:07:57" (day first). Nothing in the bytes tells them apart
    # until a value exceeds 12 — and nothing here needs to know, because the key
    # is only ever compared WITHIN one file.
    ("slash", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4},?\s+(?:--\s+)?\d{1,2}:\d{2}:\d{2}")),
    # kernel ring buffer and Xorg: seconds since boot, not a wall clock. Still an
    # axis, still monotone, which is all "appeared late" needs. The kernel writes
    # six decimals and Xorg three, so the fraction width cannot be pinned.
    ("rel", re.compile(r"^\[\s*\d+\.\d{1,6}\]")),
    ("time_only", re.compile(r"^\s*\d{2}:\d{2}:\d{2}[.,]\d+")),
]
_DIGITS_RE = re.compile(r"\d")

AXIS_SCAN_LINES = 4000
AXIS_COV_MIN = 0.50
AXIS_MONO_MIN = 0.90
AXIS_SPARSE_MIN = 20
AXIS_DELIMS = ("\t", ",", ";", "|")


def _axis_key(line):
    """(shape_name, comparable_key) for the FIRST timestamp on the line."""
    for name, rx in AXIS_SHAPES:
        m = rx.search(line)
        if m:
            return name, "".join(_DIGITS_RE.findall(m.group(0)))
    return None, None


def clock_residue(value):
    """The candidate value with the MOMENT taken out of it.

    A correlation key has to identify a transaction, not an instant. Removing
    the timestamp the time axis would have parsed out of a value leaves whatever
    else that value carries — a serial, a request id, a session — and the
    residue is the part that has to look like an identifier.

    A value that IS a clock leaves nothing behind. A value of the shape
    `<epoch>.<ms>:<serial>` leaves its serial, which is the field this framing
    exists to find. A value with no timestamp in it comes back unchanged, so
    this is a no-op for every candidate that was never clock-like."""
    if not value:
        return ""
    for _name, rx in AXIS_SHAPES:
        m = rx.search(value)
        if m:
            return value[:m.start()] + value[m.end():]
    return value


def _column_axis(path):
    """A delimited table can keep its clock in a COLUMN rather than in the line
    text. A stream-summary table is the case: its clock is a float column of
    seconds since the capture began, and no date appears anywhere in the file.
    Every line-substring shape misses it and a genuinely ordered stream reads as
    a config. Asking whether ANY column is numeric and non-decreasing is still
    arithmetic — it never needs to know what that column is called.
    EVIDENCE §E14."""
    try:
        with read_text(path) as fh:
            head = fh.readline()
            if not head:
                return None
            delim = max(AXIS_DELIMS, key=head.count)
            ncol = head.count(delim) + 1
            if ncol < 3:
                return None
            rows = []
            for line in fh:
                parts = line.rstrip("\n").split(delim)
                if len(parts) == ncol:
                    rows.append(parts)
                if len(rows) >= 2000:
                    break
            if len(rows) < 10:
                return None
            names = head.rstrip("\n").split(delim)
            best = None
            for i in range(ncol):
                nums = []
                for r in rows:
                    v = r[i].strip()
                    try:
                        nums.append(float(v))
                    except ValueError:
                        _s, key = _axis_key(v)
                        if key:
                            nums.append(float(key[:17]))
                if len(nums) < 0.9 * len(rows) or len(set(nums)) < 3:
                    continue
                back = sum(1 for a, b in zip(nums, nums[1:]) if b < a)
                mono = 1.0 - back / float(max(1, len(nums) - 1))
                if mono >= AXIS_MONO_MIN:
                    cand = (mono, names[i] if i < len(names) else "col%d" % i)
                    if best is None or cand > best:
                        best = cand
            return best
    except (OSError, ValueError):
        return None


def time_axis(path, framing=FRAME_LINE):
    """-> {verdict, coverage, monotonicity, shape, hits, lines}.

    verdict is one of:
      stream         most RECORDS carry time and time moves forward
      stream-sparse  few records carry time, but those that do are many and
                     ordered. Kept as a safety net for a file whose framing was
                     not detected: read line-by-line, wazuh's alerts.log puts its
                     timestamp on line 2 of a nine-line record, so coverage reads
                     0.16 while the file is a perfectly good event stream.
      stream-table   a delimited COLUMN is the clock
      state          no usable time axis: a config, a ruleset, a dump, key material
      empty / binary  nothing to read
    """
    out = {"verdict": "state", "coverage": 0.0, "monotonicity": 0.0,
           "shape": "-", "hits": 0, "lines": 0}
    try:
        if os.path.getsize(path) == 0:
            out["verdict"] = "empty"
            return out
    except OSError:
        out["verdict"] = "binary"
        return out
    if looks_binary(path):
        out["verdict"] = "binary"
        return out

    n = hits = 0
    prev = {}
    back = Counter()
    shapes = Counter()
    try:
        # Measured over RECORDS, not physical lines, whenever the framing is
        # known. wazuh's alerts.log is why: read line-by-line its dominant shape
        # is `epoch`, and TWO different epochs share that shape — the alert's own
        # id and the epoch inside the quoted auditd record it is reporting. They
        # interleave, so monotonicity read 0.67 and a live alert stream was
        # classified `state`. Per record, the first timestamp is the alert's, and
        # the sequence is clean.
        for _s, _e, line in stream_records(path, framing):
            n += 1
            name, key = _axis_key(line)
            if key:
                hits += 1
                shapes[name] += 1
                # Monotonicity is measured PER SHAPE. A block record can carry an
                # epoch on one line and a wall clock on the next; comparing across
                # shapes compares 17581151370 against 20250917131857 and back, and
                # scored a perfectly ordered alert stream at 0.50. Mixed shapes in
                # one file are the norm in real evidence, not an edge case.
                if name in prev and key < prev[name]:
                    back[name] += 1
                prev[name] = key
            if n >= AXIS_SCAN_LINES:
                break
    except OSError:
        return out

    out["lines"] = n
    out["hits"] = hits
    if n == 0:
        out["verdict"] = "empty"
        return out
    out["coverage"] = hits / float(n)
    if shapes:
        dom, dom_n = shapes.most_common(1)[0]
        out["shape"] = dom
        # One timestamp has nothing to compare against. Scoring that 0.0 demoted
        # several real, tiny event channels out of the stream class. Undefined is
        # not disorder. EVIDENCE §E13.
        out["monotonicity"] = (1.0 - back[dom] / float(dom_n - 1)) if dom_n > 1 else 1.0

    if out["coverage"] >= AXIS_COV_MIN and out["monotonicity"] >= AXIS_MONO_MIN:
        out["verdict"] = "stream"
    elif hits >= AXIS_SPARSE_MIN and out["monotonicity"] >= AXIS_MONO_MIN:
        out["verdict"] = "stream-sparse"
    elif out["coverage"] >= AXIS_COV_MIN:
        out["verdict"] = "stream-unordered"
    else:
        col = _column_axis(path)
        if col:
            out["verdict"] = "stream-table"
            out["monotonicity"] = col[0]
            out["shape"] = "col:" + col[1]
            out["coverage"] = 1.0
    return out


def walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            ap = os.path.join(dirpath, fn)
            out.append((ap, os.path.relpath(ap, root).replace(os.sep, "/")))
    return sorted(out, key=lambda t: t[1])


# ---------------------------------------------------------------------------
# the input gate: a multi-host bundle is N corpora, not one
# ---------------------------------------------------------------------------
def host_roots(rels, max_depth=HOST_MAX_DEPTH):
    """-> (depth, [root, ...], shared_child) — the host partition of a bundle,
    read off the SHAPE of the paths. (0, [], None) means "one host".

    Why this is a gate and not a heuristic buried downstream. A worklist is a
    fixed attention budget and every row it spends is a row the model reads.
    Round-robining that budget across FILES is right inside one machine and
    wrong across machines, because the arithmetic stops working: divided by the
    machines of a large bundle, the cap comes out at barely one row per file,
    while the evidence that opens an intrusion can be a handful of lines in one
    small file. Measured: pointed at the whole bundle, the tool touched almost
    none of it; pointed at one machine, the same code touched all of it. Same
    tool, same corpus, same budget — only the denominator changed. So the
    denominator is what gets fixed, and it gets fixed at the boundary, before
    any ranking code has to know about it. EVIDENCE §E15.

    The rule is structural on purpose: a hostname word list is a per-corpus
    maintenance cost and it is exactly the kind of spine this project keeps
    that REPEAT THE SAME INTERNAL LAYOUT, because one collector wrote all of
    them. So the SIGNATURE of a root is the set of its immediate child directory
    names, and the partition is the shallowest depth at which one non-empty
    signature is repeated by at least two roots and by at least half of them.
    Rename every host to gibberish and the answer does not move.

    A repeated NAME is not enough, and that was measured too: inside one host
    two sibling trees BOTH contained a directory of the same name, so "two roots
    share a child name" declared a single machine a two-host bundle. Requiring
    the whole signature to repeat rejects it — the two trees hold wildly
    different numbers of children, and no collector wrote them as peers.

    The majority clause is what keeps a single-host bundle single: a corpus of
    three roots with three different signatures, two of them empty, gets no
    partition and its worklist does not move. `--host-depth` / `--single-host`
    override this; the operator has the last word, because the cost of a wrong
    guess here is the whole budget.
    """
    parts = [r.split("/") for r in rels]
    for d in range(1, max_depth + 1):
        roots = {}
        for p in parts:
            if len(p) <= d:                     # a file above this level
                continue
            kids = roots.setdefault("/".join(p[:d]), set())
            if len(p) > d + 1:                  # ... and only DIRECTORY children
                kids.add(p[d])
        if len(roots) < 2:
            continue
        shapes = Counter(frozenset(kids) for kids in roots.values() if kids)
        if not shapes:
            continue
        # sorted() first so a tie resolves the same way on every machine
        sig, k = max(sorted(shapes.items(), key=lambda kv: sorted(kv[0])),
                     key=lambda kv: kv[1])
        if k >= 2 and 2 * k >= len(roots):
            return d, sorted(roots), "+".join(sorted(sig))
    return 0, [], None


def host_of(rel, depth):
    """Which host owns this path. Files above the host level are not dropped —
    they get their own bucket, because a bundle's stray top-level MANIFEST is
    still evidence and silence is how budgets go missing."""
    p = rel.split("/")
    if depth <= 0 or len(p) <= depth:
        return ROOT_BUCKET
    return "/".join(p[:depth])


def host_slug(name, taken):
    """A host name as a file name. Collisions get a numeric suffix rather than
    overwriting each other — losing a host's worklist to a name clash would be
    the silent drop this whole change exists to prevent."""
    base = HOST_SLUG_RE.sub("_", name.replace("/", "__")).strip("_") or "host"
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = "%s-%d" % (base, n)
    taken.add(slug)
    return slug


def probe(path):
    """-> (framing, time_shape, level_axes, probe_note, outcome_axis,
    metric_axes). Reads a prefix only."""
    lines = []
    try:
        with read_text(path) as fh:
            for i, raw in enumerate(fh):
                if i >= PROBE_LINES:
                    break
                lines.append(deansi(raw))
    except OSError as e:
        return FRAME_LINE, None, [], "нечитаем: %s" % e, None, []
    if not lines:
        return FRAME_LINE, None, [], "пустой файл", None, []

    n = len(lines)
    nonblank = [l for l in lines if l.strip()]
    nb = len(nonblank) or 1
    blank_share = (n - len(nonblank)) / float(n)
    cri_share = sum(1 for l in nonblank if CRI_RE.match(l)) / float(nb)
    key_share = sum(1 for l in nonblank if BLOCK_KEY_RE.match(l)) / float(nb)
    lead_share = sum(1 for l in nonblank if LEAD_TS_RE.match(l)) / float(nb)
    clf_share = sum(1 for l in nonblank if CLF_LINE_RE.match(l)) / float(nb)

    if cri_share > 0.90:
        framing = FRAME_CRI
    elif blank_share > 0.02 and key_share > 0.50:
        framing = FRAME_BLOCK
    elif lead_share >= 0.55:
        framing = FRAME_ANCHOR
    elif clf_share >= SINGLE_LINE_MIN_SHARE:
        # The grammar decides. One fully-parsed request line is one record, so
        # there is nothing here for a statistic to be wrong about.
        framing = FRAME_LINE
    else:
        # Last resort only. Reaching here means no blank lines separate the
        # records, no line starts with a timestamp and no one-line grammar
        # parses — so if anything groups these lines it is a token they share.
        # Deliberately below the other three: suricata's eve.json and sshd's
        # auth.log both have a token that would group them, and both are
        # correctly claimed by anchor framing before this line is ever reached.
        field = detect_key_framing(lines)
        framing = ("%s:%s" % (FRAME_KEY, field)) if field else FRAME_LINE

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
    return (framing, shape, axes[:LEVEL_AXES_SHOWN], note,
            status_axis(hists, rn), metric_axes(seen, hists, rn))


def looks_like_json_object(text):
    s = text.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return False
    depth = 0
    in_str = False
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_str


def detect_key_framing(lines):
    """-> the field whose value groups consecutive lines into records, or None.

    Only ever reached when no other framing applies, which is what keeps it safe.
    Three tests, all arithmetic:

      1. mean run of consecutive equal values >= KEY_FRAME_MIN_RUN. A field that
         changes every line groups nothing.
      2. distinct values >= KEY_FRAME_MIN_DISTINCT of n. A near-constant is a
         column (a hostname, a facility), not a record identifier.
      3. EVERY distinct value forms exactly ONE contiguous run.

    Test 3 is the one that matters, and suricata's eve.json is why it exists.
    `flow_id` there has a mean run of 2.9 and plenty of distinct values, so tests
    1 and 2 both pass — but a flow's events come back later in the file, so its
    values form many runs, not one. Grouping on it would fuse unrelated events
    into a single record. auditd's serial never returns: 0.99 of its values form
    one run. The measured gap between 0.99 and 0.60 is the whole discriminator.

    Belt and braces: a file whose lines are already whole JSON objects is never
    grouped, because those lines are already records.
    """
    nb = [l for l in lines if l.strip()]
    n = len(nb)
    if n < KEY_FRAME_MIN_LINES:
        return None

    sample = nb[:200]
    if (sum(1 for l in sample if looks_like_json_object(l))
            >= KEY_FRAME_JSON_GUARD * len(sample)):
        return None

    cols = defaultdict(lambda: [None] * n)
    present = Counter()
    stamps = [_axis_key(line)[1] for line in nb]
    for i, line in enumerate(nb):
        for k, v in field_candidates(line).items():
            cols[k][i] = v
            present[k] += 1

    # Grouping needs corroboration, and the clock is the corroborating witness.
    # Without a clock on most lines there is nothing to check the grouping
    # against, so nothing is grouped.
    if sum(1 for s in stamps if s) < 0.5 * n:
        return None

    best = None
    for k, vals in cols.items():
        if present[k] < 0.9 * n:
            continue
        runs = []
        cur = 1
        for a, b in zip(vals, vals[1:]):
            if a == b:
                cur += 1
            else:
                runs.append(cur)
                cur = 1
        runs.append(cur)
        mean_run = sum(runs) / float(len(runs))
        if mean_run < KEY_FRAME_MIN_RUN:
            continue
        starts = Counter()
        prev = object()
        for v in vals:
            if v != prev:
                starts[v] += 1
            prev = v
        distinct = len(starts)
        if distinct < KEY_FRAME_MIN_DISTINCT * n:
            continue
        contiguous = sum(1 for _v, c in starts.items() if c == 1) / float(distinct)
        if contiguous < KEY_FRAME_MIN_CONTIGUOUS:
            continue
        # A KEY MUST IDENTIFY A TRANSACTION, NOT A MOMENT. The same-instant test
        # below cannot see this one on its own: when the candidate IS the clock,
        # "the lines of one record happened at one instant" is true by
        # construction — the corroborating witness and the accused are the same
        # object, so the test answers itself, and every request that shared a
        # second became one record. Take the moment out of each value and ask
        # the distinct-value question all over again: is what is LEFT still
        # enough to be a record identifier? A bare stamp leaves nothing.
        # `<epoch>.<ms>:<serial>` leaves its serial, which is the field this
        # framing exists to find. A candidate carrying no timestamp at all is
        # returned unchanged by clock_residue and cannot move. Read UNCAPPED,
        # because the part of an id that varies is often its tail.
        # EVIDENCE §E16 and the v23 section.
        whole = make_extractor(k, cap=IDENT_VALUE_MAX)
        if (len({clock_residue(whole(line)) for line in nb})
                < KEY_FRAME_MIN_DISTINCT * n):
            continue
        # The decisive test: the lines of ONE record happened at ONE instant.
        # Contiguity alone cannot tell a record identifier from a slowly-changing
        # attribute — both group consecutive lines. Measured on two auditd files:
        # the real audit id scores 1.00 while pid, session and positional
        # candidates score below 0.45. On a file that is nearly one event per
        # line, grouping by pid produced a quarter of the records, inventing
        # multi-line events out of unrelated consecutive activity by one process.
        # EVIDENCE §E16.
        groups = []
        cur = [0]
        for i in range(1, n):
            if vals[i] == vals[i - 1]:
                cur.append(i)
            else:
                groups.append(cur)
                cur = [i]
        groups.append(cur)
        same_instant = sum(1 for g in groups
                           if len({stamps[i] for i in g}) <= 1) / float(len(groups))
        if same_instant < KEY_FRAME_MIN_SAME_INSTANT:
            continue
        # Prefer the field that yields the MOST records — i.e. the tightest
        # grouping that still groups. Sorting by run length instead picked the
        # pid over the audit id on a real auditd file, fusing every consecutive
        # event of one process into a single record and losing most of the
        # events. Under-grouping loses a little context; over-grouping invents
        # events that never happened. EVIDENCE §E16.
        score = (distinct, mean_run)
        if best is None or score > best[0]:
            best = (score, k)
    return best[1] if best else None


def assemble(lines, framing):
    """[(start_line, end_line, text)] — 1-based line numbers, ANSI already gone."""
    out = []
    if frame_base(framing) == FRAME_KEY:
        field = frame_field(framing)
        pending = []          # [(lineno, text)] of the group being built
        prev = object()
        for i, l in enumerate(lines, 1):
            if not l.strip():
                continue
            val = field_candidates(l).get(field)
            if pending and val == prev:
                pending.append((i, l))
                continue
            if pending:
                out.append((pending[0][0], pending[-1][0],
                            "\n".join(t for _n, t in pending)))
            pending, prev = [(i, l)], val
        if pending:
            out.append((pending[0][0], pending[-1][0],
                        "\n".join(t for _n, t in pending)))
        return out
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
        if frame_base(framing) == FRAME_KEY:
            field = frame_field(framing)
            prev = object()
            for i, raw in enumerate(fh, 1):
                line = deansi(raw)
                if not line.strip():
                    continue
                val = field_candidates(line).get(field)
                if pending and val == prev:
                    pending.append((i, line))
                    continue
                if pending:
                    yield pending[0][0], pending[-1][0], \
                        "\n".join(t for _n, t in pending)
                pending, prev = [(i, line)], val
            if pending:
                yield pending[0][0], pending[-1][0], \
                    "\n".join(t for _n, t in pending)
            return
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
        # stream | stream-sparse | stream-table | stream-unordered | state |
        # empty | binary — see time_axis(). Decides WHICH budget this file draws
        # its worklist rows from, never whether it is read at all.
        self.axis = "stream"
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
        # axis 0. Same row shape as `groups`, and used ONLY when `groups` is
        # empty — a floor, never a supplement. `floor_why` is the sentence the
        # map prints so the reader knows these rows are a weaker claim.
        self.floor = []
        self.floor_why = ""
        # axes 5 and 6, kept separately from `groups` for one reason: a rotation
        # family folds four slices into one stream, and `stitch` rebuilds
        # `groups` from scratch. Keeping the rows themselves (never the
        # histograms they came out of — 64 addresses and their exemplars per
        # file across a bundle of thousands of files is not a memory budget
        # worth spending)
        # lets the stitched stream carry each slice's claims forward. The cost
        # is that "new" is judged inside the slice it was seen in, which is a
        # narrower window than the stream, and therefore a weaker claim than the
        # unrotated case. Written down rather than hidden.
        self.new_rows = []
        self.peak_rows = []
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
        self.metric_axes = []
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

    (rep.framing, rep.time_shape, axes, rep.time_note, rep.outcome_axis,
     rep.metric_axes) = probe(path)
    rep.axis = time_axis(path, rep.framing)["verdict"]
    rep.level_axes_shown = [(name, hist) for _s, name, hist in axes]
    rep.level_axis = axes[0][1] if axes else None
    # EVERY qualifying axis is carried, not just the best-scoring one. Ranking
    # among them can only be structural (this file owns no severity words), and a
    # structural ranking put a 3-value `channel` field above a 3-value numeric
    # level field on one real corpus. Taking the union costs one extra targeted
    # regex per record and removes the guess.
    extractors = [(name, make_extractor(name)) for name, _h in rep.level_axes_shown]
    get_status = make_extractor(rep.outcome_axis) if rep.outcome_axis else None
    get_metric = [(name, make_extractor(name)) for name in rep.metric_axes]
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
    # The floor axis needs three cheap things the four real axes never kept: an
    # exemplar per HOUR (<=24 entries), the first record and the last one. All
    # three survive a file whose every template is unique, which is exactly the
    # case the floor exists for.
    hour_first = {}
    first_rec = last_rec = None
    # axis 5: the population of parties, and the record each one entered on.
    # Bounded by ACTOR_CARD_MAX — past it the field is churn and the axis is off
    # for this file, which is recorded rather than inferred.
    actor_hist = Counter()
    actor_first = {}
    actor_ord = {}
    actor_seen = 0
    actor_over = False
    # axis 6: one bounded reservoir per (measurement, hour). 2 axes x 24 hours x
    # RESERVOIR values is the whole cost, whatever the file weighs.
    #
    # ITS OWN generator, and that is not tidiness. Sharing `rng` with the slot
    # reservoirs would change the draw sequence they see, and axis 3's
    # percentiles would move on every file big enough to overflow one — measured,
    # and a p50 moved by 6 % for no reason but this. A new axis may add rows; it
    # may not silently restate an old axis's numbers. EVIDENCE §E17.
    metric_rng = random.Random(~args.seed)
    metric_res = {}

    try:
        for start, end, text in stream_records(path, rep.framing):
            lines_seen = end
            rep.records += 1
            slots = []
            glue = []
            actors = []
            span = max(1, end - start + 1)
            tmax = TEMPLATE_MAX * min(TEMPLATE_MAX_FACTOR, span)
            masked = mask(text[:MASK_INPUT_MAX], slots, glue, actors)
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
            if actors:
                actor_seen += 1
                for v in set(actors):
                    if v not in actor_hist:
                        if actor_over or len(actor_hist) >= ACTOR_CARD_MAX:
                            actor_over = True
                            continue
                        actor_ord[v] = actor_seen
                        actor_first[v] = (start, end,
                                          squeeze(shown)[:DISPLAY_MAX])
                    actor_hist[v] += 1
            if first_rec is None:
                first_rec = (start, end, squeeze(shown)[:DISPLAY_MAX])
            last_rec = (start, end, squeeze(shown)[:DISPLAY_MAX])
            h, at = clock.read(text)
            if h is not None and h not in hour_first:
                hour_first[h] = (start, end, squeeze(shown)[:DISPLAY_MAX])
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
                for name, get in get_metric:
                    raw = get(text)
                    if raw is None:
                        continue
                    try:
                        fv = float(raw)
                    except ValueError:
                        continue
                    mr = metric_res.get((name, h))
                    if mr is None:
                        mr = metric_res[(name, h)] = Reservoir(metric_rng)
                    mr.add(fv)
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
        # Everything below this line is per TEMPLATE, and on a file with one
        # template per record that is the file again. So the early return stays
        # — it is a memory bound, not a verdict — but it no longer ends in
        # silence: the floor is built here, out of the locals the loop already
        # has, before any of them go out of scope.
        rep.gated = ("доля уникальных форм %.4f > %.2f — почти каждая запись "
                     "своей формы, так что «редкая форма» в этом файле ничего "
                     "не значит. ВСЕ ЧЕТЫРЕ оси по формам (редкость, категория, "
                     "темп, исход) по нему выключены"
                     % (rep.ratio, DISTINCT_RATIO_GATE))
        if rep.verbatim is None and draws_stream_budget(rep.axis):
            rep.floor, rep.floor_why = floor_rows(
                status_hist, status_first, level_hist, level_tmpl, first_seen,
                display, per_hour_total, hour_first, first_rec, last_rec,
                ts_lo, ts_hi, "оси по формам отключены")
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

    # ---- axis 5 + 6 -------------------------------------------------------
    # STREAMS ONLY, for the same measured reason the floor is stream-only: a
    # bundle is a copy of /etc next to a copy of /var/log, configs outnumber logs
    # by two orders of magnitude, and an allow-list of addresses would put three
    # "new participant" rows on every one of them. EVIDENCE §E19.
    if draws_stream_budget(rep.axis):
        rep.new_rows = actor_rows(actor_hist, actor_first, actor_ord,
                                  actor_seen, actor_over)
        rep.peak_rows = peak_rows(metric_res, hour_first)
        rep.groups, rep.group_total = merge_ranked(
            rep.new_rows + rep.peak_rows, rep.groups, rep.group_total,
            args.per_file_cap)

    # ---- axis 0 -----------------------------------------------------------
    # The gate is not the only way to reach zero rows, and the other way is
    # quieter. A sampled metric log can hold a couple of thousand records in
    # exactly two common templates: not gated, a tiny distinct ratio, and no rare
    # group either — so it got nothing, while carrying hostile activity. Same
    # floor, same reason. EVIDENCE §E18.
    if (not rep.groups and rep.records and rep.verbatim is None
            and draws_stream_budget(rep.axis)):
        rep.floor, rep.floor_why = floor_rows(
            status_hist, status_first, level_hist, level_tmpl, first_seen,
            display, per_hour_total, hour_first, first_rec, last_rec,
            ts_lo, ts_hi, "ни одной редкой формы: %d форм на %d записей"
            % (rep.distinct, rep.records))
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


def draws_stream_budget(axis):
    """The one definition of "this file is a log", used by the worklist budget and
    by the floor. `state` and `stream-unordered` draw from the small state share;
    everything else is a stream."""
    return axis not in ("state", "stream-unordered")


def floor_rows(status_hist, status_first, level_hist, level_tmpl, first_seen,
               display, per_hour_total, hour_first, first_rec, last_rec, lo, hi,
               why):
    """axis 0 — what to look at in a file none of the four axes can rank.

    -> ([row, ...], why) in the same row shape as `group_rows`, capped at
    FALLBACK_PER_FILE and never empty while the file holds a single record.

    The order is the order of evidential strength, and it is not a matter of
    taste — each axis here is one that survives the exact condition that killed
    the other four, namely every record having its own shape:

      code   a NON-DOMINANT outcome code. `status_hist` and `status_first` are
             filled inside the read loop, before the gate, so this costs nothing
             new. On an access log that is typically a `404`, and the model is
             handed the record where it first appears rather than a count it
             cannot open. Strongest, because "this request answered differently"
             is a claim about the record, not about its shape.
      level  the rarest value of a discovered level axis. Same idea one column
             over, and it is what surfaces the malformed requests — a method
             column whose rarest value is not a verb — that a template count
             cannot see. EVIDENCE §E18.
      burst  the fullest hour, cited at its first record there. Volume is a real
             axis when shape is not: a scan is a lot of DIFFERENT lines inside a
             short window.
      edge   the first and the last record. The weakest claim in the tool, and
             the one that makes "zero rows" impossible: a file the model has
             never seen a single line of is worse than one it has seen two of.

    Every row is de-duplicated by the record it cites, so a 404 that is also the
    last record spends one slot, not two.

    ONLY STREAM FILES GET A FLOOR, and that is measured, not tidiness. Three of
    these four axes are meaningless on a `state` artefact — a config has no
    fullest hour and its "first record" is its first line — and the arithmetic
    of letting them in is brutal: on a bundle that is mostly /etc, thousands of
    configs are gated precisely because a config's lines are all different.
    Measured with state files included, the config share of that worklist went
    from about a tenth to well over half — which is exactly the defect v14 was
    written to remove. A state artefact is not left silent: it keeps its own
    budget share, its level histogram in the map, and under SMALL_FILE_BYTES its
    whole text. EVIDENCE §E18.
    """
    rows, seen = [], set()

    def add(n, cite, kind, disp, spread=""):
        if cite is None or cite[0] in seen or len(rows) >= FALLBACK_PER_FILE:
            return
        seen.add(cite[0])
        rows.append((n, cite[0], cite[1], kind, None, disp, None, spread))

    # --- code ---------------------------------------------------------------
    if len(status_hist) > 1:
        dominant = status_hist.most_common(1)[0][0]
        offered = 0
        for code, n in sorted(status_hist.items(), key=lambda kv: (kv[1], kv[0])):
            if code == dominant or code not in status_first or \
                    offered >= FALLBACK_CODE_MAX:
                continue
            st, en, disp, _rel = status_first[code]
            add(n, (st, en), "code", "код %d · %s" % (code, disp))
            offered += 1

    # --- level --------------------------------------------------------------
    offered = 0
    for name in sorted(level_hist):
        hist = level_hist[name]
        if len(hist) < 2 or offered >= FALLBACK_LEVEL_MAX:
            continue
        value, n = min(hist.items(), key=lambda kv: (kv[1], kv[0]))
        tmpls = level_tmpl.get((name, value))
        if not tmpls:
            continue
        tmpl = tmpls.most_common(1)[0][0]
        cite = first_seen.get(tmpl)
        if cite is None:
            continue
        add(n, cite, "level", "ось «%s»=%s (%d из %d) · %s"
            % (name, value, n, sum(hist.values()), display.get(tmpl, "")))
        offered += 1

    # --- burst --------------------------------------------------------------
    if per_hour_total and hour_first:
        hour = max(sorted(per_hour_total), key=lambda h: per_hour_total[h])
        cite = hour_first.get(hour)
        if cite is not None:
            add(per_hour_total[hour], (cite[0], cite[1]), "burst",
                "самый полный час %02d: %d записей из %d · %s"
                % (hour, per_hour_total[hour], sum(per_hour_total.values()),
                   cite[2]))

    # --- edge ---------------------------------------------------------------
    for rec, label in ((first_rec, "первая запись"), (last_rec, "последняя запись")):
        if rec is not None:
            add(1, (rec[0], rec[1]), "edge", "%s · %s" % (label, rec[2]))
    return rows[:FALLBACK_PER_FILE], why


def actor_rows(actor_hist, actor_first, actor_ord, seen, over):
    """axis 5 — the parties that entered late.

    -> rows in `group_rows` shape, at most ACTOR_PER_FILE, and empty far more
    often than not. Three structural gates, and every one of them is about the
    FIELD, never about a value:

      * the field has a population at all (`over` — see ACTOR_CARD_MAX). An
        access log has a fresh client on nearly every line; nothing there can be
        new, and pretending otherwise would put three rows of churn on every
        web server in a bundle.
      * the stream is long enough for "the first half" to be a baseline.
      * the newcomers are few (ACTOR_LATE_MAX). Four of them means the
        population was still forming, and "new" stops carrying a claim.

    RANKED BY LATENESS, NOT BY RARITY, and that is the difference between this
    axis and axis 2. Measured on a real VPN log: rarest-first returns several
    internal pool addresses ahead of the one that matters, because a party that
    only ever gets a handful of records is ordinary background churn.
    Latest-first returns exactly one — the peer that is absent from most of the
    file. EVIDENCE §E23."""
    if over or seen < ACTOR_MIN_N or len(actor_hist) < 2:
        return []
    half = seen / 2.0
    late = sorted(((o, v) for v, o in actor_ord.items() if o > half),
                  reverse=True)
    if not late or len(late) > ACTOR_LATE_MAX:
        return []
    rows = []
    for ordinal, value in late[:ACTOR_PER_FILE]:
        st, en, disp = actor_first[value]
        rows.append((actor_hist[value], st, en, "new", None,
                     "НОВЫЙ УЧАСТНИК %s: впервые на записи %d из %d "
                     "(%.0f%% потока), всего %d · %s"
                     % (value, ordinal, seen, 100.0 * ordinal / seen,
                        actor_hist[value], disp), None, ""))
    return rows


def peak_rows(metric_res, hour_first):
    """axis 6 — the hour a measurement left its own baseline and came back.

    -> rows in `group_rows` shape, at most PEAK_PER_FILE.

    The definition is the whole axis, so it is spelled out: an EXCURSION is an
    hour whose median is at least RATE_FACTOR above the median of the file's own
    hourly medians, with at least one hour before it and one after it back
    within PEAK_BACK of that typical. Three things follow from the return test,
    and all three are the point:

      * a counter cannot produce a row. It ends the window at its maximum and
        never comes back, so there is no "after" — and drift is what axis 3
        already measures, first comparable hour against last.
      * an excursion in the first or the last hour cannot produce a row either.
        With nothing on one side, the tail of a trend and a spike look the same,
        and `hourly_onset` already refuses to guess for exactly this reason.
      * the hour floor that governs axis 3 does NOT govern this one, and that is
        deliberate rather than lax. That floor exists so a SHARE has a
        trustworthy denominator; a median needs only enough samples to be a
        median (RATE_MIN_HOUR_N). A sampler that fires slower than once a minute
        produces fewer records an hour than that floor, which is why axis 3 is
        silent on every metric log of a sampled bundle. EVIDENCE §E24.

    HIGH SIDE ONLY. A drop is not symmetric with a spike: a bounded metric's
    floor is its normal state (an `idle` field is one minus the busy one, and
    every quiet hour sits at zero for half the fields on the record), so a low
    excursion fires everywhere and says nothing. What it costs is a service that
    stops: throughput falling to zero would be an axis-3 drift row, not a peak.
    Written down so the gap is a decision."""
    best = []
    for name in sorted({k[0] for k in metric_res}):
        series = []
        for (n, h), r in metric_res.items():
            if n != name or r.n < RATE_MIN_HOUR_N:
                continue
            series.append((h, percentile(r.vals, 0.50), r.n))
        if len(series) < PEAK_MIN_HOURS:
            continue
        series.sort()
        typical = percentile([v for _h, v, _n in series], 0.50)
        if not typical or typical <= 0:
            continue
        i = max(range(len(series)), key=lambda j: series[j][1])
        hour, peak, samples = series[i]
        if peak < typical * RATE_FACTOR:
            continue
        if i == 0 or i == len(series) - 1:
            continue
        back = typical * PEAK_BACK
        if not any(v <= back for _h, v, _n in series[:i]):
            continue
        if not any(v <= back for _h, v, _n in series[i + 1:]):
            continue
        cite = hour_first.get(hour)
        if cite is None:
            continue
        best.append((peak / typical, samples, cite, name, hour, peak, typical))
    best.sort(key=lambda t: -t[0])
    rows = []
    for factor, samples, cite, name, hour, peak, typical in best[:PEAK_PER_FILE]:
        rows.append((samples, cite[0], cite[1], "peak", None,
                     "ВЫБРОС «%s»: медиана часа %02d = %s против обычного %s "
                     "по файлу (x%.1f), до и после — норма · %s"
                     % (name, hour, fmt_num(peak), fmt_num(typical), factor,
                        cite[2]), None, ""))
    return rows


def merge_ranked(extra, groups, total, per_file_cap):
    """-> (rows, total) with axes 5/6 in front of axes 1/2.

    In front, and not merely present, because the per-file queue is drained in
    order: a file with 300 rare groups would bury an axis that fires on one file
    in a hundred. Deduplicated against the rows already there by the record they
    cite — the same record reached twice is one row, and the stronger claim
    keeps the slot."""
    if not extra:
        return groups, total
    taken = {(g[6], g[1], g[2]) for g in groups}
    head = []
    for row in extra:
        key = (row[6], row[1], row[2])
        if key in taken:
            continue
        taken.add(key)
        head.append(row)
    rows = head + list(groups)
    return rows[:max(per_file_cap * 4, 200)], total + len(head)


def comparable_hours(per_hour_total):
    """First and last hour of the STREAM that is a real hour.

    The floor belongs on the hour, not on the template. Putting it on the template
    silently deletes exactly the shape worth finding: a ramp is rare at the start
    by definition, so a relative floor throws away its first hour and then reports
    the flattened remainder.

    And the floor is measured against a TYPICAL hour, not against the whole
    window. Against the whole window it tightens as the window widens: measured
    on a stitched stream whose quiet half ran at a fifth of the busy half, a
    5 %-of-total floor threw away every quiet hour and compared the incident with
    itself. EVIDENCE §E22."""
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
# measures the rotation rather than the incident. Measured: one shape occurring a
# handful of times inside a few minutes rendered as two small counts in two files
# and read as routine background. EVIDENCE §E3.
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
        # axes 5/6 are carried, not recomputed: the histograms they came out of
        # are not kept on a report (see FileReport). Each slice's claims move to
        # the stream and keep citing the slice they were seen in.
        extra = []
        for r in order:
            for row in list(r.new_rows) + list(r.peak_rows):
                extra.append(row[:6] + (r.rel,) + row[7:])
        primary.new_rows = [row for row in extra if row[3] == "new"]
        primary.peak_rows = [row for row in extra if row[3] == "peak"]
        primary.groups, primary.group_total = merge_ranked(
            extra, primary.groups, primary.group_total, args.per_file_cap)
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
                # same stream to the worklist. Its floor goes with it: the floor
                # exists so a FILE is never invisible, and the file is not
                # invisible — it is being read as part of the stream above it.
                r.groups, r.group_total = [], 0
                r.floor, r.floor_why = [], ""
                r.new_rows, r.peak_rows = [], []
                r.rate_rows, r.bg_rows, r.out_rows = [], [], []
        notes.append((name, [r.rel for r in order], lo, hi))
    return notes


# ---------------------------------------------------------------------------
# assembling the three artefacts
# ---------------------------------------------------------------------------
def build_worklist(reports, cap, per_file_cap):
    """Round-robin across files, rarest first — but streams and state artefacts
    draw from SEPARATE budgets.

    Pure global rarity ranking lets one chatty file eat the whole budget, and the
    single most expensive failure this project ever measured was a run that never
    opened 12 of 28 files. So every file gets its turn before any file gets a
    second row.

    Round-robin alone is not enough on real evidence, and the reason is arithmetic.
    A bundle is a copy of /var/log NEXT TO a copy of /etc, and /etc wins on count
    by two orders of magnitude, so giving every file an equal turn gives the
    configs almost all of the turns. Measured before this split: nearly half the
    worklist cited a config rather than a log, and more than half of it for the
    format-blind arm.

    So the budget is split first, and only then round-robined within each class.
    STATE_SHARE is small but never zero, because a state artefact is often the
    most incriminating thing in the bundle — on the corpora this was measured on
    the `state` files include the intruder's own tooling and dumped key material.
    `state` is a budget, never a bin. EVIDENCE §E19.
    """
    streams, states = [], []
    queues = {}
    for r in reports:
        if r.error:
            continue
        # `groups` OR `floor`, never both: the floor is what a file gets when the
        # ranked axes had nothing to say about it. Until v17 a gated file was
        # skipped outright here, and that is how the two most heavily attacked
        # files of a real testbed received zero rows each while thousands of rows
        # went to files carrying no hostile traffic at all. EVIDENCE §E18.
        q = list(r.groups) or list(r.floor)
        if r.gated and not q:
            continue
        queues[r.rel] = q
        (states if r.axis in ("state", "stream-unordered") else streams).append(r.rel)

    state_cap = min(int(round(cap * STATE_SHARE)),
                    len(states) * min(per_file_cap, STATE_PER_FILE_CAP))
    if not streams:
        state_cap = cap

    # Pass 1 decides HOW MANY rows each file is allowed. Two classes, two budgets.
    quota = {rel: 0 for rel in queues}
    carry = 0
    for order, budget, file_cap in ((sorted(streams), cap - state_cap, per_file_cap),
                                    (sorted(states), state_cap,
                                     min(per_file_cap, STATE_PER_FILE_CAP))):
        budget += carry
        spent = 0
        progress = True
        while progress and spent < budget:
            progress = False
            for rel in order:
                if spent >= budget:
                    break
                if quota[rel] >= min(file_cap, len(queues[rel])):
                    continue
                quota[rel] += 1
                spent += 1
                progress = True
        carry = budget - spent

    # Pass 2 emits them in the ORIGINAL round-robin order, across both classes at
    # once. The split must decide which rows make the cut, never where they sit:
    # measured, emitting state last pushed the rows that carried the intruder's
    # own tooling from the top of the worklist into its second half. Same rows,
    # and a worse read. With this pass that worklist is byte-identical to v13's.
    # EVIDENCE §E19.
    rows = []
    taken = {rel: 0 for rel in queues}
    order = sorted(queues)
    progress = True
    while progress and len(rows) < cap:
        progress = False
        for rel in order:
            if len(rows) >= cap:
                break
            q = queues[rel]
            if not q or taken[rel] >= quota[rel]:
                continue
            count, s, e, kind, tmpl, disp, cite_rel, spread = q.pop(0)
            taken[rel] += 1
            # the queue is keyed by the file that OWNS the budget (the stream's
            # newest slice); the citation belongs to whichever slice the first
            # occurrence physically sits in.
            rows.append({"file": cite_rel or rel, "kind": kind, "count": count,
                         "line_start": s, "line_end": e, "display": disp,
                         "spread": spread, "template": tmpl, "owner": rel})
            progress = True
    trunc = {}
    for r in reports:
        if r.error or r.gated or not r.groups:
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


CLOCK_RE = re.compile(r"\b([0-2]?\d):([0-5]\d):([0-5]\d)\b")


def _clock_of(text):
    """Seconds-of-day from the first wall clock in a record, or None.

    Read off the record itself rather than threaded through the pipeline: the
    line already carries its own time in every format this tool accepts, and a
    candidate with no clock must simply get no note.
    """
    m = CLOCK_RE.search(text or "")
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return None if h > 23 else h * 3600 + mi * 60 + s


def _gap(seconds):
    h, m = divmod(abs(int(seconds)) // 60, 60)
    return ("%d ч %02d м" % (h, m)) if h else ("%d м" % m)


SIBLING_MIN_TOKENS = 4


def _sibling_bg(owner, tmpl, bg_by_tmpl):
    """A background row for the same message SHAPE, when it is not the same template.

    Measured on a benchmark corpus whose decoy line is one occurrence of a message
    that carries an extra parenthetical, while tens of thousands of lines carry the
    same message without it and are measured flat all day. Different templates, so
    an exact join can never connect them, and the rate argument that refutes the
    decoy sits in a row the candidate never mentions. EVIDENCE §E21.

    The match is a token-boundary PREFIX in either direction, at least four tokens
    deep. That is deliberately narrow: a looser rule would stamp «background» onto
    genuinely distinct messages, and suppressing a real defect is a worse failure
    than leaving a decoy unannotated. The note says «родственный» for the same
    reason — it reports a neighbour, it does not pronounce the candidate normal.
    """
    if not tmpl:
        return None
    exact = bg_by_tmpl.get((owner, tmpl))
    if exact:
        return exact + (True,)
    want = tmpl.split()
    if len(want) < SIBLING_MIN_TOKENS:
        return None
    for (own, cand), val in bg_by_tmpl.items():
        if own != owner or not cand:
            continue
        have = cand.split()
        n = min(len(want), len(have))
        if n < SIBLING_MIN_TOKENS or want[:n] != have[:n]:
            continue
        return val + (False,)
    return None


def _refutations(row, bg_by_tmpl, peak_at):
    """The facts already in the residue that bear on THIS candidate.

    Both planted decoys survived two full-corpus runs because these facts existed
    in other rows of other files and the comparison was left to the reader. A
    cache-eviction line was offered as a rare candidate while a background row
    measured the same template as flat all day; a kernel alarm was offered with a
    timestamp five and a half hours from a peak recorded on the outcome axis.

    Neither note is a verdict — a genuine defect can precede its outage by hours,
    and a real fault can hide inside a flat stream. They are the two measurements
    a reader would otherwise have to remember to go and make.
    """
    notes = []
    bg = _sibling_bg(row.get("owner"), row.get("template"), bg_by_tmpl)
    if bg:
        rid, s0, s1, exact = bg
        label = "фон" if exact else "родственный фон"
        if s0 is not None and s1 is not None:
            notes.append("%s %s: доля %.2f%%→%.2f%%, не сдвинулось"
                         % (label, rid, 100 * s0, 100 * s1))
        else:
            notes.append("%s %s: не сдвинулось" % (label, rid))
    if peak_at is not None:
        t = _clock_of(row.get("display"))
        if t is not None:
            # `peak_at` is a bucket index times a bucket width, so it counts from
            # an arbitrary epoch, while a clock read off a record counts from
            # midnight. Both are day-less faces — compare them as such, and take
            # the short way round the dial so 23:50 against 00:10 is 20 minutes
            # and not 23 hours 40.
            d = (t % 86400) - (int(peak_at) % 86400)
            if d > 43200:
                d -= 86400
            elif d < -43200:
                d += 86400
            when = "до" if d < 0 else "после"
            notes.append("%s, за %s %s пика %s"
                         % (hhmm(t), _gap(d), when, hhmm(peak_at))
                         if abs(d) >= 60 else
                         "%s, на пике %s" % (hhmm(t), hhmm(peak_at)))
    return notes


WORKLIST_HEADER = (
    "# id\tвердикт\tось\tссылка\tчастота\tзапись\n",
    "# вердикт: ? не разобрано · D дефект · N норма (только с цифрой) "
    "· X данных не хватает\n",
    "# частота: n=<сколько> · <первая>→<последняя> <разброс>=<доля "
    "окна захвата>; ВСПЛЕСК = всё уместилось в узкое окно\n",
)

FLOOR_KINDS = ("code", "level", "burst", "edge")

# Printed only when such a row is actually in this file. A legend for a value
# that never occurs is noise — and it would also rewrite every worklist of every
# corpus that has no file needing a floor.
FLOOR_LEGEND = (
    "# ось «опора» (code · level · burst · edge): у файла НЕТ редких форм — либо "
    "почти каждая запись своей формы, либо форм всего две. Строку выбрала "
    "запасная ось,\n",
    "#   и это более слабое утверждение, чем `rare`/`cat`: адрес «открой и "
    "посмотри вокруг», а не «вот аномалия». Без него такой файл не получал НИ "
    "ОДНОЙ строки.\n",
)


# Same rule as FLOOR_LEGEND: printed only when such a row is really in this
# file. A legend for a value that never occurs is noise, and it would rewrite
# every worklist of every corpus where neither axis fires.
EXTRA_LEGEND = (
    ("new",
     ("# ось «новый» (new): участник (адрес), которого НЕ БЫЛО в первой "
      "половине потока. Это утверждение про ВРЕМЯ, а не про редкость: "
      "самый редкий адрес\n",
      "#   обычно просто фоновая мелочь, а вот появившийся поздно — смена "
      "состояния. Ось молчит, когда у поля нет популяции (новый адрес почти "
      "на каждой записи).\n")),
    ("peak",
     ("# ось «выброс» (peak): час, в котором медиана измерения ушла вверх "
      "минимум в 3 раза от обычной по файлу И ВЕРНУЛАСЬ — соседние часы в "
      "норме. Ось темпа\n",
      "#   сравнивает первый час с последним и такой всплеск не видит вовсе. "
      "Счётчик сюда не попадает: он не возвращается.\n")),
)


def has_kind(lines, kind):
    for line in lines:
        f = line.split("\t")
        if len(f) > 2 and f[2] == kind:
            return True
    return False


def has_floor_rows(lines):
    for line in lines:
        f = line.split("\t")
        if len(f) > 2 and f[2] in FLOOR_KINDS:
            return True
    return False


def worklist_body(rows, rate_rows, first_g=1):
    """-> the data lines of a worklist, without the header.

    Split out of `write_worklist` so ONE host's rows can be written twice — once
    into its own `worklist-<host>.tsv`, which is what the model reads, and once
    into the combined `worklist.tsv`, which is the ledger `citecheck --ledger`
    counts. The same lines, with the same ids, in both places: a row that means
    something different depending on which file you opened would be worse than
    no per-host split at all.

    `first_g` continues the numbering across hosts. Ids close rows in the ledger,
    so two hosts both starting at g001 would let one host's verdict close
    another's row.
    """
    out = []
    # What already bears on each candidate, joined onto its own line. The
    # background rows and the outcome peak are computed by the time this runs;
    # leaving the join to the reader is what let both decoys through.
    bg_by_tmpl = {}
    peak_at = None
    for rr in rate_rows:
        if rr.get("kind") == "bg" and rr.get("template") is not None:
            s0, s1 = rr.get("share") or (None, None)
            bg_by_tmpl.setdefault((rr.get("owner"), rr["template"]),
                                  (rr["id"], s0, s1))
        elif rr.get("kind") == "out" and peak_at is None:
            # out rows arrive sorted by factor, so the first is the sharpest
            # burst in the corpus — the moment everything else is timed from.
            peak_at = rr.get("peak_at")
    for i, r in enumerate(rows, first_g):
        notes = _refutations(r, bg_by_tmpl, peak_at)
        out.append("g%03d\t?\t%s\t%s\t%s\t%s%s\n"
                   % (i, r["kind"], cite(r["file"], r["line_start"],
                                         r["line_end"]),
                      freq_cell(r["count"], r.get("spread")), r["display"],
                      ("  ⟨%s⟩" % " · ".join(notes)) if notes else ""))
    for r in rate_rows:
        out.append("%s\t?\t%s\t%s\tn=%d\t%s\n"
                   % (r["id"], r["kind"], cite(r["file"], r["line_start"],
                                               r["line_end"]), r["n"],
                      r["summary"] + " | " + r["display"]))
    return out


def write_worklist(out_dir, rows, rate_rows, name="worklist.tsv", body=None):
    lines = worklist_body(rows, rate_rows) if body is None else body
    out = []
    out.extend(WORKLIST_HEADER)
    if has_floor_rows(lines):
        out.extend(FLOOR_LEGEND)
    for kind, legend in EXTRA_LEGEND:
        if has_kind(lines, kind):
            out.extend(legend)
    out.extend(lines)
    return _write_text_atomic(out_dir, name, "".join(out))


def write_host_map(out_dir, name, mine, trunc, rows, rate_rows, args, wl):
    """-> (path, folded reports, bytes withheld)."""
    lines, folded, withheld = render_host_map(name, mine, trunc, rows, rate_rows,
                                              args, args.corpus, wl)
    slug = os.path.splitext(wl)[0]
    slug = slug[len("worklist-"):] if slug.startswith("worklist-") else slug
    path = _write_text_atomic(out_dir, "map-%s.txt" % slug, "\n".join(lines) + "\n")
    return path, folded, withheld


def write_hosts(out_dir, hosts, depth, shape, args):
    """The host index — and the honest bill for the per-host budget.

    Every detected host is on this list. Nothing is capped to a top-N and
    nothing is quietly folded together: a fix that moves the cliff instead of
    removing it is the failure mode this file keeps being rewritten to avoid.
    The cost is real and is printed here rather than discovered later — N hosts
    cost N budgets, so a large bundle costs N × the rows of a single-host run.
    EVIDENCE §E8."""
    total = sum(h["rows"] for h in hosts)
    out = []
    out.append("# хост\tфайлов\tстрок\tиз них темп\tне вошло (форм)\t"
               "рабочий список\tкарта\tсвёрнуто файлов\n")
    out.append("# разбиение по хостам: уровень пути %d%s. У КАЖДОГО хоста свой "
               "полный потолок %d строк — не %d, поделённых на %d.\n"
               % (depth, (", общая ветка «%s»" % shape) if shape else "",
                  args.worklist_cap, args.worklist_cap, len(hosts)))
    out.append("# ни один хост не выброшен: их %d, и все %d строк лежат также "
               "в worklist.tsv (общий леджер).\n" % (len(hosts), total))
    for h in hosts:
        out.append("%s\t%d\t%d\t%d\t%d\t%s\t%s\t%d\n"
                   % (h["name"], h["files"], h["rows"], h["rate"],
                      h["trunc"], h["path"], h.get("map", ""),
                      h.get("folded", 0)))
    return _write_text_atomic(out_dir, "hosts.tsv", "".join(out))


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


def rate_worklist_rows(reports, cap, ids=None):
    """axis-3 and axis-4 candidates, as adjudicable worklist rows. Background rows
    are included on purpose: a refutation nobody was asked for never gets written.

    `ids` is a counter carried ACROSS hosts so O/S/B numbering never repeats in
    the combined ledger; left out, numbering starts at 1 exactly as it always
    did, which is what keeps a single-host run byte-identical."""
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
    if ids is None:
        ids = Counter()
    rows = []
    for _f, rel, b in outs[:n_out]:
        ids["O"] += 1
        rows.append(_out_row("O%03d" % ids["O"], rel, b))
    for _f, rel, b in moved[:n_moved]:
        ids["S"] += 1
        rows.append(_rate_row("S%03d" % ids["S"], "rate", rel, b))
    for _f, rel, b in flat[:n_bg]:
        ids["B"] += 1
        rows.append(_rate_row("B%03d" % ids["B"], "bg", rel, b))
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
            "template": b.get("template"), "owner": rel,
            "share": (b.get("share0"), b.get("share1")),
            "t0": "%02dh" % b["h0"], "t1": "%02dh" % b["h1"]}


def _out_row(rid, rel, b):
    return {"id": rid, "kind": "out", "peak_at": b.get("peak_at"),
            "file": b.get("file") or rel,
            "line_start": b["line_start"], "line_end": b["line_end"],
            "n": b["n"], "summary": "ось «%s» %s" % (b["axis"], b["summary"]),
            "display": b["display"], "factor": b["factor"],
            "metric": "исход %dxx" % b["cls"],
            "t0": hhmm(b["peak_at"]), "t1": hhmm(b["peak_at"])}


def write_axis3(out_dir, rate_rows):
    out = []
    out.append("# id\tось\tсдвиг\tфайл\tметрика\tчас_от\tчас_до\tизмерение\tформа\n")
    out.append("# ось rate = сдвинулось, ось bg = ФОН, не сдвинулось "
               "(это тоже измерение, и оно опровергает), ось out = исход/код "
               "результата по интервалам\n")
    for r in rate_rows:
        out.append("%s\t%s\tx%.2f\t%s\t%s\t%s\t%s\t%s\t%s\n"
                   % (r["id"], r["kind"], r["factor"], r["file"], r["metric"],
                      r["t0"], r["t1"], r["summary"], r["display"][:200]))
    return _write_text_atomic(out_dir, "axis3.tsv", "".join(out))


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


def map_head(reports, corpus, worklist_name="work/worklist.tsv"):
    return ["КАРТА КОРПУСА  %s" % os.path.abspath(corpus),
            "файлов: %d · рабочий список: %s · таблица темпа: work/axis3.tsv"
            % (len(reports), worklist_name)]


MAP_LEGEND = [
    "",
    "Как читать: «форм» — сколько различных шаблонов записей в файле; «ось»",
    "— поле, из которого выведена серьёзность ИМЕННО В ЭТОМ файле (никакого",
    "словаря уровней у инструмента нет); «время» — распознанная форма отметки",
    "времени. «время: НЕТ» значит, что анализ темпа по этому файлу невозможен.",
    "«ПОТОК» — файл сшит с ротацией и посчитан вместе с ней, одним потоком.",
    "«ось исхода» — поле с кодом результата, найденное по форме, а не по имени.",
    "«род» — есть ли у файла ось времени. «поток» — записи идут во времени и",
    "время не идёт назад. «состояние» — оси времени нет: конфиг, набор правил,",
    "выгрузка, ключевой материал, забытый на диске скрипт. Это НЕ значит",
    "«неважно»: самая тяжёлая улика вполне может лежать именно там — у неё",
    "просто нет часов. У «состояния» отдельная, малая доля рабочего списка —",
    "потому что оси «редкое», «поздно» и «всплеск» без времени ничего не",
    "значат, а НЕ потому, что такой файл можно пропустить. «Состояние»",
    "никогда не значит «выбросить»: подброшенный ключ в authorized_keys",
    "— тоже улика.",
    "",
]


def file_block(r, trunc):
    """One file's page of the map — the block v16 wrote inline, extracted so the
    budget can decide whether this file gets it or its one-line form."""
    out = []
    a = out.append
    a("=" * 78)
    a("%s  %s" % (r.rel, human(r.bytes)))
    if r.error:
        a("  ! %s" % r.error)
        if r.verbatim is None:
            a("")
            return out
    if r.verbatim is not None:
        # WITH LINE NUMBERS (v19). Anything the model is shown has to be
        # citable, because a finding it cannot cite is a finding `citecheck`
        # must reject. Until v19 this block was the file's whole text with no
        # addresses at all: the model could read a 343-byte artefact, see
        # exactly what it was, and have nothing legal to write next to the
        # claim. Measured on a real corpus: a quarter of its files came out this
        # way, and some of them hold the entire proof of a planted defect.
        # EVIDENCE §E20.
        # The numbers are the physical lines of the file on disk, which is the
        # currency `citecheck` reads and the same currency a line in the
        # worklist is written in.
        body = r.verbatim.splitlines()
        w = len("%d" % max(1, len(body)))
        # The path is on the line above; repeating it here would cost the map
        # budget one full path per quoted file, and on a deep bundle that is
        # hundreds of files' worth of quoting.
        a("  файл меньше %d Б — приведён ДОСЛОВНО, слева НОМЕР СТРОКИ "
          "(ссылка: путь:N):" % SMALL_FILE_BYTES)
        for i, line in enumerate(body, 1):
            cut = line[:VERBATIM_LINE_CHARS]
            # A truncated quote is NOT the line. Saying so is the difference
            # between the model re-reading the file and the model citing a
            # string that will come back `wrong-content`.
            tail = (" …⟨обрезано, перечитай строку целиком⟩"
                    if len(line) > VERBATIM_LINE_CHARS else "")
            a("  %*d | %s%s" % (w, i, cut, tail))
        a("")
        return out
    a("  строк %d · записей %d · кадрирование %s · род %s · форм %d (доля %.4f)"
      % (r.lines, r.records, r.framing, AXIS_RU.get(r.axis, r.axis),
         r.distinct, r.ratio))
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
        a("  ОСИ ФОРМ ОТКЛЮЧЕНЫ: %s" % r.gated)
    if r.floor:
        # The floor is a weaker claim than a rare group and it says so HERE too,
        # not only in the worklist's axis column: the map is where the reader
        # decides how much weight a file's rows carry.
        a("  ОПОРНЫЕ СТРОКИ: %d — %s. Рабочий список получил их по запасным "
          "осям (%s), а не по редкости формы; это адреса «посмотри сюда», а "
          "не утверждения."
          % (len(r.floor), r.floor_why,
             ", ".join(sorted({row[3] for row in r.floor}))))
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
    return out


def file_line(r):
    """The one-line form. A folded file is NOT a dropped file: it keeps its name,
    its size, its record count, its род and its form count — everything needed to
    decide to open it — and loses only the level histograms and the verbatim
    quote."""
    if r.error:
        return "· %s  %s · ОШИБКА: %s" % (r.rel, human(r.bytes), r.error)
    notes = []
    if r.verbatim is not None:
        # It is NOT quoted here — that is the whole point of being folded. Say
        # so, because "read it whole in the map" is exactly what SKILL.md
        # promises about a file this size.
        notes.append("меньше %dБ — дословно НЕ приведён, открой сам"
                     % SMALL_FILE_BYTES)
    if not r.time_shape:
        notes.append("время НЕТ")
    if r.gated:
        notes.append("оси форм отключены")
    if r.floor:
        notes.append("опорных строк %d" % len(r.floor))
    return ("· %s  %s · строк %d · записей %d · род %s · форм %d (доля %.4f)%s"
            % (r.rel, human(r.bytes), r.lines, r.records,
               AXIS_RU.get(r.axis, r.axis), r.distinct, r.ratio,
               (" · " + " · ".join(notes)) if notes else ""))


def _cost(lines):
    return sum(len(l.encode("utf-8")) + 1 for l in lines)


def budget_blocks(reports, trunc, cap):
    """-> (kept rels, folded reports, bytes withheld).

    Two rules, and the first one is not negotiable: **a file that earned a
    worklist row always keeps its full block**, whatever it costs. The budget
    sheds background, never the evidence the model is being sent to read. What
    is left of the cap then goes to the rest, cheapest first, so the number of
    files shown in full is as large as the cap allows.

    Measured, which is why cheapest-first: on a real bundle two thirds of the map
    was sub-4 KB files quoted verbatim. Ordering by size puts hundreds of those
    inside a 150 KB budget instead of two 70 KB ones. EVIDENCE §E8.
    """
    blocks = {}
    cost = {}
    for r in reports:
        b = file_block(r, trunc)
        blocks[r.rel] = b
        cost[r.rel] = _cost(b)
    if cap <= 0:
        return blocks, [], 0
    keep = {r.rel for r in reports if trunc.get(r.rel) or _earned_rows(r)}
    rest = [r for r in reports if r.rel not in keep]
    spent = sum(cost[rel] for rel in keep)

    def order(r):
        return (0 if r.error else 1,
                0 if str(r.axis).startswith("stream") else 1,
                cost[r.rel], r.rel)

    for r in sorted(rest, key=order):
        if spent + cost[r.rel] > cap:
            continue
        keep.add(r.rel)
        spent += cost[r.rel]
    folded = [r for r in reports if r.rel not in keep]
    withheld = sum(cost[r.rel] - (len(file_line(r).encode("utf-8")) + 1)
                   for r in folded)
    return ({rel: b for rel, b in blocks.items() if rel in keep}, folded,
            max(0, withheld))


def _earned_rows(r):
    return bool(r.groups or r.floor or r.rate_rows or r.bg_rows or r.out_rows)


def fold_note(folded, total, withheld, cap):
    return ["=" * 78,
            "СВЁРНУТО ДО ОДНОЙ СТРОКИ: %d файлов из %d · %d Б (%s) не показано · "
            "потолок карты %d Б на хост" % (len(folded), total, withheld,
                                            human(withheld), cap),
            "Ни один файл не выброшен: каждый назван ниже — размер, род, число "
            "форм, есть ли часы. Полный блок вернёт `--map-cap 0`; строки "
            "рабочего списка эти файлы всё равно получили, если заслужили.",
            ""]


def render_blocks(reports, trunc, cap):
    """The body of a map: full blocks in path order, then the fold notice, then
    the folded files in path order. Nothing is folded until the cap is really
    exceeded, so a corpus under budget renders exactly as it did in v16."""
    kept, folded, withheld = budget_blocks(reports, trunc, cap)
    out = []
    for r in sorted(reports, key=lambda x: x.rel):
        if r.rel in kept:
            out.extend(kept[r.rel])
    if folded:
        out.extend(fold_note(folded, len(reports), withheld, cap))
        for r in sorted(folded, key=lambda x: x.rel):
            out.append(file_line(r))
        out.append("")
    return out, folded, withheld


def worklist_note(rows, rate_rows, trunc, args, hosts, ledger=None):
    out = []
    a = out.append
    a("=" * 78)
    a("РАБОЧИЙ СПИСОК: %d строк (потолок %d%s, на файл %d)"
      % (len(rows) + len(rate_rows), args.worklist_cap,
         (" НА ХОСТ × %d хостов" % len(hosts)) if hosts else "",
         args.per_file_cap))
    a("  из них ось редкости/категории: %d, ось темпа/исхода: %d"
      % (len(rows), len(rate_rows)))
    if trunc:
        a("  НЕ ВОШЛО (по файлам): "
          + ", ".join("%s=%d" % kv for kv in sorted(trunc.items())))
    a("")
    a("Каждая строка рабочего списка начинается со статуса `?`. Замени его на")
    if hosts:
        a("D / N / X и запиши файл обратно — В ФАЙЛ СВОЕГО ХОСТА. `citecheck.py")
        a("--ledger work/worklist-<хост>.tsv` печатает, сколько `?` осталось у")
        a("этого хоста; хостов %d, и закончить надо каждого." % len(hosts))
    elif ledger:
        a("D / N / X и запиши файл обратно — В ЭТОТ файл: work/%s." % ledger)
        a("`citecheck.py --ledger work/%s` печатает, сколько `?` осталось." % ledger)
    else:
        a("D / N / X и запиши файл обратно. `citecheck.py --ledger work/worklist.tsv`")
        a("печатает, сколько `?` осталось.")
    return out


def hosts_block(hosts, args):
    """The multi-host preamble v16 wrote into the one undivided map. Kept
    verbatim so `--map-cap 0` really does give back the v16 file."""
    out = []
    a = out.append
    total = sum(h["rows"] for h in hosts)
    a("")
    a("хостов: %d — связка собрана с НЕСКОЛЬКИХ машин, и это N корпусов, а не"
      % len(hosts))
    # Why the cap is handed out whole and not divided: a fixed budget split
    # across machines leaves each one a fraction too thin to hold a short piece
    # of evidence inside a long file. Stated as the property, never as a tally —
    # a count of hosts is a fact about ONE bundle and is false on every other.
    # EVIDENCE §E15.
    a("один. Потолок рабочего списка выдан КАЖДОМУ хосту целиком (%d строк на"
      % args.worklist_cap)
    a("хост), а не поделён между ними: общий потолок на все машины даёт каждой")
    a("долю, слишком тонкую, чтобы в неё попала короткая улика в длинном файле.")
    a("ЦЕНА ЧЕСТНО: строк всего %d вместо %d — это ×%.1f контекста, если читать"
      % (total, args.worklist_cap, float(total) / max(1, args.worklist_cap)))
    a("всё разом. НЕ читай всё разом: work/worklist.tsv — это общий леджер для")
    a("citecheck, а работать надо по одному хосту.")
    a("Список хостов и их файлов — work/hosts.tsv. Ни один хост не выброшен.")
    for h in hosts:
        a("  %-30s файлов %5d · строк %4d (темп %3d) · work/%s"
          % (h["name"][:30], h["files"], h["rows"], h["rate"], h["path"]))
    return out


def render_map(reports, rows, trunc, rate_rows, args, corpus, hosts=None):
    """The undivided map: byte for byte what v16 wrote, plus a budget that only
    speaks when it has something to say. Reached for a single-host corpus, and
    for any corpus when the operator sets `--map-cap 0`."""
    body, _folded, _w = render_blocks(reports, trunc, getattr(args, "map_cap",
                                                              MAP_HOST_BYTES))
    return (map_head(reports, corpus)
            + (hosts_block(hosts, args) if hosts else [])
            + MAP_LEGEND + body
            + worklist_note(rows, rate_rows, trunc, args, hosts))


def render_host_map(name, mine, trunc, rows, rate_rows, args, corpus, wl):
    """-> (lines, folded, withheld) for ONE machine.

    The map got the treatment the worklist got in v15, and for the same
    arithmetic. An N-machine bundle is N corpora; handing the model one map of
    all of them is the same defect as handing it one worklist cap split N ways,
    except it costs a million tokens instead of losing an anomaly.
    EVIDENCE §E8."""
    body, folded, withheld = render_blocks(mine, trunc,
                                           getattr(args, "map_cap",
                                                   MAP_HOST_BYTES))
    head = ["КАРТА ХОСТА «%s»  %s" % (name, os.path.abspath(corpus)),
            "файлов на этом хосте: %d · его рабочий список: work/%s"
            % (len(mine), wl),
            "Это ОДНА машина из связки. Указатель по всем — work/map.txt."]
    return (head + MAP_LEGEND + body
            + worklist_note(rows, rate_rows, trunc, args, None, ledger=wl)),\
        folded, withheld


def render_index(reports, rows, trunc, rate_rows, args, corpus, hosts):
    """work/map.txt on a multi-host bundle: an index, not the map.

    Measured on a many-host bundle: the undivided map ran to millions of bytes —
    on the order of a million tokens — and SKILL.md told the model to read it. It
    is now this file, a few kilobytes, plus one map per host. EVIDENCE §E8."""
    out = []
    a = out.append
    out.extend(map_head(reports, corpus))
    total_rows = sum(h["rows"] for h in hosts)
    map_bytes = sum(h["map_bytes"] for h in hosts)
    folded = sum(h["folded"] for h in hosts)
    a("")
    a("хостов: %d — связка собрана с НЕСКОЛЬКИХ машин, и это N корпусов, а не"
      % len(hosts))
    # Why the cap is handed out whole and not divided: a fixed budget split
    # across machines leaves each one a fraction too thin to hold a short piece
    # of evidence inside a long file. Stated as the property, never as a tally —
    # a count of hosts is a fact about ONE bundle and is false on every other.
    # EVIDENCE §E15.
    a("один. Потолок рабочего списка выдан КАЖДОМУ хосту целиком (%d строк на"
      % args.worklist_cap)
    a("хост), а не поделён между ними: общий потолок на все машины даёт каждой")
    a("долю, слишком тонкую, чтобы в неё попала короткая улика в длинном файле.")
    a("ЦЕНА ЧЕСТНО: строк всего %d вместо %d — это ×%.1f контекста, если читать"
      % (total_rows, args.worklist_cap,
         float(total_rows) / max(1, args.worklist_cap)))
    a("всё разом. НЕ читай всё разом: work/worklist.tsv — это общий леджер для")
    a("citecheck, а работать надо по одному хосту.")
    a("")
    a("КАРТА ТОЖЕ РАЗБИТА ПО ХОСТАМ, и по той же причине. Этот файл — только")
    a("указатель; сами карты лежат по машинам и весят вместе %s. Читай карту"
      % human(map_bytes))
    a("ОДНОГО хоста — work/map-<хост>.txt — вместе с его рабочим списком.")
    if folded:
        a("Свёрнуто до одной строки суммарно %d файлов: каждый назван в карте"
          % folded)
        a("своего хоста, ни один не выброшен. Потолок карты — %d Б на хост,"
          % getattr(args, "map_cap", MAP_HOST_BYTES))
        a("снимается ключом `--map-cap 0`.")
    a("Список хостов и их файлов — work/hosts.tsv. Ни один хост не выброшен.")
    a("")
    for h in hosts:
        a("  %-30s файлов %5d · строк %4d (темп %3d)"
          % (h["name"][:30], h["files"], h["rows"], h["rate"]))
        a("      карта  work/%s  (%s%s)"
          % (h["map"], human(h["map_bytes"]),
             ", свёрнуто %d" % h["folded"] if h["folded"] else ""))
        a("      список work/%s" % h["path"])
    out.extend(MAP_LEGEND)
    out.extend(worklist_note(rows, rate_rows, trunc, args, hosts))
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
    ap.add_argument("--map-cap", type=int, default=MAP_HOST_BYTES,
                    help="потолок карты в БАЙТАХ на хост; что не влезло, "
                         "остаётся в карте одной строкой. 0 — бюджет карты "
                         "выключен целиком: одна общая map.txt, как до v17")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--jobs", type=int, default=default_jobs(),
                    help="файлов параллельно (1 = строго последовательно)")
    ap.add_argument("--host-depth", type=int, default=None,
                    help="сколько компонентов пути образуют ХОСТ (0 — весь корпус "
                         "один хост); по умолчанию определяется по структуре")
    ap.add_argument("--single-host", action="store_true",
                    help="весь корпус — одна машина, разбиение по хостам выключено")
    args = ap.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    os.makedirs(args.out, exist_ok=True)
    if os.path.islink(args.out) or not os.path.isdir(args.out):
        sys.exit("небезопасный каталог вывода: %s" % args.out)
    retire_prior_host_artifacts(args.out)

    # THE INPUT GATE. The partition is decided on the file list, before a single
    # byte is analysed, so nothing downstream has to disambiguate a bundle that
    # holds several machines. Constrain the input at the boundary; keep the
    # ranking code the same shape it has always had.
    files = walk(args.corpus)
    if args.single_host:
        depth, shape = 0, None
    elif args.host_depth is not None:
        # the override is a DEPTH, not a list: whatever sits at that level is a
        # host, including a level the detector would never have chosen
        depth, shape = max(0, args.host_depth), None
    else:
        depth, _roots, shape = host_roots([rel for _p, rel in files])

    reports = analyse_all(files, args)
    # A rotated stream is re-counted as one BEFORE anything is ranked: counting it
    # per slice halves every count and puts a before/after on opposite sides of
    # the cut. Nothing is re-read — every file kept its per-template residue.
    # Rotation families are keyed by directory, so they never cross a host.
    stitch(reports, args)

    hosts = None
    hp = None
    if depth <= 0:
        rate_rows = rate_worklist_rows(reports,
                                       min(args.rate_cap, args.worklist_cap))
        rows, trunc = build_worklist(
            reports, max(0, args.worklist_cap - len(rate_rows)), args.per_file_cap)
        wl = write_worklist(args.out, rows, rate_rows)
        ax = write_axis3(args.out, rate_rows)
    else:
        buckets = {}
        for r in reports:
            buckets.setdefault(host_of(r.rel, depth), []).append(r)
        # the stray-files bucket sorts last: it is a leftover, not a machine
        names = sorted(buckets, key=lambda n: (n == ROOT_BUCKET, n))
        ids, taken, first_g = Counter(), set(), 1
        rows, rate_rows, trunc, body, hosts = [], [], {}, [], []
        for name in names:
            mine = buckets[name]
            h_rate = rate_worklist_rows(mine, min(args.rate_cap,
                                                  args.worklist_cap), ids)
            h_rows, h_trunc = build_worklist(
                mine, max(0, args.worklist_cap - len(h_rate)), args.per_file_cap)
            lines = worklist_body(h_rows, h_rate, first_g)
            slug = host_slug(name, taken)
            p = write_worklist(args.out, h_rows, h_rate,
                               name="worklist-%s.tsv" % slug, body=lines)
            body.append("# ── хост «%s» · файлов %d · строк %d · отдельно: %s ──\n"
                        % (name, len(mine), len(lines), os.path.basename(p)))
            body.extend(lines)
            # The map is split with the worklist, not after it. Each machine's
            # map is written here, next to the worklist it belongs with, and
            # map.txt below becomes the index over both.
            h_map = {}
            if args.map_cap > 0:
                mp, folded, withheld = write_host_map(
                    args.out, name, mine, h_trunc, h_rows, h_rate, args,
                    os.path.basename(p))
                h_map = {"map": os.path.basename(mp),
                         "map_bytes": os.path.getsize(mp),
                         "folded": len(folded), "withheld": withheld}
            hosts.append(dict({"name": name, "files": len(mine),
                               "rows": len(lines), "rate": len(h_rate),
                               "trunc": sum(h_trunc.values()),
                               "path": os.path.basename(p)}, **h_map))
            rows.extend(h_rows)
            rate_rows.extend(h_rate)
            trunc.update(h_trunc)
            first_g += len(h_rows)
        wl = write_worklist(args.out, rows, rate_rows, body=body)
        ax = write_axis3(args.out, rate_rows)
        hp = write_hosts(args.out, hosts, depth, shape, args)

    if hosts and args.map_cap > 0:
        body = render_index(reports, rows, trunc, rate_rows, args, args.corpus,
                            hosts)
    else:
        # `--map-cap 0` turns the map budget off WHOLE — no per-host split and no
        # folding — so the operator can always get back the one undivided map
        # v16 wrote, and so the two v17 fixes can be measured apart.
        body = render_map(reports, rows, trunc, rate_rows, args, args.corpus,
                          hosts)
    _write_text_atomic(args.out, "map.txt", "\n".join(body) + "\n")
    if hosts:
        marker_hosts = [{"name": h["name"], "worklist": h["path"],
                         "map": h.get("map", "")} for h in hosts]
        write_active_marker(args.corpus, args.out, mode="multi",
                            worklists=[h["path"] for h in hosts],
                            hosts=marker_hosts, hosts_manifest="hosts.tsv")
    else:
        write_active_marker(args.corpus, args.out, mode="single",
                            worklists=[os.path.basename(wl)])

    shown = body
    if len(shown) > STDOUT_MAX_LINES:
        head = shown[:STDOUT_MAX_LINES - 12]
        shown = head + ["", "… карта обрезана на %d строк — целиком она лежит в %s"
                        % (len(body) - len(head), os.path.join(args.out, "map.txt"))]
    print("\n".join(shown))
    print("")
    print("написано: %s · %s · %s%s"
          % (os.path.join(args.out, "map.txt"), wl, ax,
             (" · " + hp) if hp else ""))
    if hosts:
        print("хостов %d · строк всего %d · %s в worklist.tsv — читай ПО ОДНОМУ "
              "хосту (work/worklist-<хост>.tsv + work/map-<хост>.txt), не всё "
              "разом. Карт хостов %d, вместе %s; map.txt — только указатель."
              % (len(hosts), len(rows) + len(rate_rows),
                 human(os.path.getsize(wl)), len(hosts),
                 human(sum(h.get("map_bytes", 0) for h in hosts))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
