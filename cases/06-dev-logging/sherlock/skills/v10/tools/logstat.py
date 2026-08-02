#!/usr/bin/env python3
"""logstat — one cheap call per file, so the model can CHOOSE what to open.

    python3 logstat.py ./logs                 # every file under a corpus
    python3 logstat.py app.log nginx.log.gz   # specific files
    python3 logstat.py ./logs --json --top 8

Why this exists (measured). Recall on one 649 MB corpus, one model, went
100 % → 73 % → 18 % — decided almost entirely by *which files got opened*. The
worst run was the most careful one: it concluded "it's the DB" after five steps
and never opened 12 of 28 files, which held the only evidence for 9 of 11
defects. The cost of not opening a file is invisible from inside the run.

So the fix is not a smarter prompt, it is making the *decision* cheap: size,
line count, time span, severity mix and the repeated message shapes of every
file for the price of one tool call, without pulling a single log into context.

What it does NOT do, on purpose:

* **No parsing.** Timestamps are reported as the raw substring found, with the
  pattern that matched. Nothing is normalised to UTC and nothing is invented —
  the retired pipeline's year-1900 sentinel came from exactly that temptation.
  No timestamp ⇒ `null`, not a guess.
* **No severity dictionary as the only answer.** `severity` counts the usual
  words, but `vocabulary` reports the uppercase tokens actually present, so a
  team's invented vocabulary (`ALARM`, `FATALITY`, `ТРЕВОГА`) shows up even
  though no dictionary has ever heard of it (R2).

Stdlib only, zero config (AGENTS.md R1).
"""
import argparse
import gzip
import json
import os
import re
import sys
from collections import Counter

# ---- timestamps: recognised, never normalised -----------------------------
TS_PATTERNS = [
    ("iso", re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
                       r"(?:Z|[+-]\d{2}:?\d{2})?")),
    ("clf", re.compile(r"\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}")),
    ("logcat", re.compile(r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{1,6}")),
    ("bsd-syslog", re.compile(r"[A-Z][a-z]{2} [ \d]?\d \d{2}:\d{2}:\d{2}")),
    ("epoch-ms", re.compile(r"(?<![0-9.])1[0-9]{12}(?![0-9])")),
    ("time-only", re.compile(r"(?<![0-9:])\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?(?![0-9])")),
]

# Longer names first: alternation is leftmost-first, so CRITICAL must be offered
# before CRIT and WARNING before WARN or the histogram silently truncates.
LEVELS = ("EMERG", "ALERT", "FATAL", "CRITICAL", "CRIT", "PANIC", "SEVERE",
          "ERROR", "ERR", "WARNING", "WARN", "NOTICE", "INFO", "DEBUG", "TRACE",
          "VERBOSE")
LEVEL_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(LEVELS) +
                      r")(?![A-Za-z0-9_])", re.IGNORECASE)
VOCAB_RE = re.compile(r"(?<![A-Za-z0-9_\-])([A-Z]{3,12})(?![A-Za-z0-9_\-])")

# ---- shape masking: format-agnostic, no templates, no learning ------------
# One pass per class of noise, not one pass per pattern: this loop runs once per
# line of a multi-GB corpus, so the constant factor is the whole cost model.
TS_MASK = re.compile("|".join(
    "(?:%s)" % rx.pattern for _n, rx in
    [TS_PATTERNS[0], TS_PATTERNS[1], TS_PATTERNS[2], TS_PATTERNS[3],
     TS_PATTERNS[5]]))
UUID_MASK = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                       r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
IP_MASK = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# hex ids and plain numbers collapse to the same `#`, so they share one pass with
# a constant replacement — a sub with a Python callback measured 30 % slower.
HEXNUM_MASK = re.compile(r"\b(?=[0-9a-fA-F]{6,}\b)(?=[a-fA-F]*[0-9])"
                         r"[0-9a-fA-F]{6,}\b|\d+")

MAX_SHAPE_LEN = 180
MAX_DISTINCT_SHAPES = 200000
TS_GIVE_UP_AFTER = 500          # lines without any recognised timestamp


def shape(line):
    s = TS_MASK.sub("<ts>", line)
    s = UUID_MASK.sub("<uuid>", s)
    s = IP_MASK.sub("<ip>", s)
    s = HEXNUM_MASK.sub("#", s)
    return " ".join(s.split())[:MAX_SHAPE_LEN]


def find_ts(line, fmt=None):
    if fmt:
        m = dict(TS_PATTERNS)[fmt].search(line)
        return (fmt, m.group(0)) if m else (None, None)
    for name, rx in TS_PATTERNS:
        m = rx.search(line)
        if m:
            return name, m.group(0)
    return None, None


def opener(path):
    return gzip.open if path.endswith(".gz") else open


def count_lines(path):
    n, last = 0, b"\n"
    with opener(path)(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            n += chunk.count(b"\n")
            last = chunk[-1:]
    if last != b"\n":
        n += 1
    return n


def looks_binary(path):
    """A NUL byte in the first 8 KB. Cheap, and it is the check that lets the
    skill close a file explicitly as «недоступен» instead of quoting mojibake."""
    with opener(path)(path, "rb") as fh:
        return b"\x00" in fh.read(8192)


def stat_file(path, top=5, rare=3, max_lines=100000, display=None):
    size = os.path.getsize(path)
    name = display if display is not None else path
    if looks_binary(path):
        return {"path": name, "bytes": size, "compressed": path.endswith(".gz"),
                "binary": True, "lines": 0, "sampled": False,
                "analysed_lines": 0, "sample_rate": 1.0, "time_format": None,
                "first_ts": None, "last_ts": None, "severity": {},
                "vocabulary": [], "distinct_shapes": 0, "top_shapes": [],
                "rare_shapes": []}
    total = count_lines(path)
    stride = 1 if total <= max_lines else (total // max_lines) + 1
    tail_from = max(0, total - 200)

    shapes = Counter()
    sev = Counter()
    vocab = Counter()
    fmt = first_ts = last_ts = None
    analysed = 0
    with opener(path)(path, "rt", encoding="utf-8", errors="replace") as fh:
        for i, raw in enumerate(fh, 1):
            in_tail = i > tail_from
            if stride > 1 and i % stride and not in_tail and i > 200:
                continue
            line = raw.rstrip("\n")
            analysed += 1
            if fmt is None:
                # Trying all six patterns on every line of a timestamp-less file
                # is the tool's worst case, so give up after a fixed prefix and
                # report `null` rather than paying for it forever.
                if analysed <= TS_GIVE_UP_AFTER:
                    fmt, ts = find_ts(line)
                    if fmt:
                        first_ts = last_ts = ts
                else:
                    fmt = False
            elif fmt:
                _f, ts = find_ts(line, fmt)
                if ts:
                    last_ts = ts
            if not line.strip():
                continue
            if len(shapes) < MAX_DISTINCT_SHAPES:
                shapes[shape(line)] += 1
            else:
                sh = shape(line)
                if sh in shapes:
                    shapes[sh] += 1
            for lvl in {m.group(1).upper() for m in LEVEL_RE.finditer(line)}:
                sev[lvl] += 1
            for tok in VOCAB_RE.findall(line):
                vocab[tok] += 1

    ordered = sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = {s for s, _c in ordered[:top]}
    # "rare" must mean rare. On a file with three shapes the bottom-N would
    # otherwise re-list the most common line under the heading "редкие формы"
    # — a small lie that costs the model a step.
    rarest = [kv for kv in sorted(shapes.items(), key=lambda kv: (kv[1], kv[0]))
              if kv[0] not in shown]
    return {
        "path": name,
        "bytes": size,
        "compressed": path.endswith(".gz"),
        "binary": False,
        "lines": total,
        "sampled": stride > 1,
        "analysed_lines": analysed,
        "sample_rate": round(1.0 / stride, 6),
        "time_format": fmt or None,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "severity": dict(sorted(sev.items(), key=lambda kv: -kv[1])),
        "vocabulary": [[t, c] for t, c in vocab.most_common(12)],
        "distinct_shapes": len(shapes),
        "top_shapes": [{"count": c, "shape": s} for s, c in ordered[:top]],
        "rare_shapes": [{"count": c, "shape": s} for s, c in rarest[:rare]],
    }


def walk(paths):
    """-> [(abspath, display)] — display is corpus-relative when given a dir."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames
                               if d not in (".git", "node_modules", "__pycache__")]
                for fn in sorted(filenames):
                    ap = os.path.join(dirpath, fn)
                    out.append((ap, os.path.relpath(ap, p).replace(os.sep, "/")))
        elif os.path.exists(p):
            out.append((p, p))
        else:
            sys.stderr.write("! нет такого пути: %s\n" % p)
    return out


def human(n):
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return "%d%s" % (n, unit) if unit == "B" else "%.1f%s" % (n, unit)
        n /= 1024.0


def render(d):
    out = []
    for f in d["files"]:
        head = "%s  %s  %s строк" % (f["path"], human(f["bytes"]),
                                     "{:,}".format(f["lines"]).replace(",", " "))
        if f["compressed"]:
            head += "  [gz]"
        if f.get("binary"):
            out.append("%s  %s  двоичный файл — анализ пропущен, читать нечем"
                       % (f["path"], human(f["bytes"])))
            out.append("")
            continue
        if f["sampled"]:
            head += "  [выборка %d строк, 1 из %d — счётчики НЕ масштабированы]" % (
                f["analysed_lines"], round(1 / f["sample_rate"]))
        out.append(head)
        if f["first_ts"]:
            out.append("  время: %s → %s  (%s)"
                       % (f["first_ts"], f["last_ts"], f["time_format"]))
        else:
            out.append("  время: не распознано")
        if f["severity"]:
            out.append("  уровни: " + ", ".join("%s=%d" % kv
                                                for kv in f["severity"].items()))
        if f["vocabulary"]:
            out.append("  словарь: " + ", ".join("%s=%d" % (t, c)
                                                 for t, c in f["vocabulary"]))
        out.append("  форм строк: %d" % f["distinct_shapes"])
        for s in f["top_shapes"]:
            out.append("    %6d × %s" % (s["count"], s["shape"][:120]))
        if f["rare_shapes"]:
            out.append("  редкие формы (там обычно и лежит инцидент):")
            for s in f["rare_shapes"]:
                out.append("    %6d × %s" % (s["count"], s["shape"][:120]))
        out.append("")
    t = d["totals"]
    out.append("итого: %d файлов, %s строк, %s"
               % (t["files"], "{:,}".format(t["lines"]).replace(",", " "),
                  human(t["bytes"])))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Дешёвая сводка по каждому файлу логов: объём, временной "
                    "охват, уровни, повторяющиеся формы строк.")
    ap.add_argument("paths", nargs="+", help="файлы и/или каталоги")
    ap.add_argument("--top", type=int, default=5, help="сколько частых форм показать")
    ap.add_argument("--rare", type=int, default=3, help="сколько редких форм показать")
    ap.add_argument("--max-lines", type=int, default=100000,
                    help="больше — переходим на выборку (и честно это помечаем)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    files = []
    for ap_, display in walk(args.paths):
        try:
            files.append(stat_file(ap_, args.top, args.rare, args.max_lines, display))
        except OSError as e:
            sys.stderr.write("! %s: %s\n" % (ap_, e))
    files.sort(key=lambda f: -f["bytes"])
    d = {"files": files,
         "totals": {"files": len(files),
                    "lines": sum(f["lines"] for f in files),
                    "bytes": sum(f["bytes"] for f in files)}}
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
