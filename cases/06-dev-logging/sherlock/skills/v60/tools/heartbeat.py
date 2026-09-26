"""Shared checker heartbeat (v53, spec item 4).

One line per unit of work, written to stderr and flushed at once, so a parent
watchdog can tell a slow-but-alive checker from a hung one:

    SHERLOCK-HB <checker> <unit> <n>

<checker> is citecheck | statecheck | reportcheck | triagecheck | buildindex;
<unit> is one of UNITS; <n> is a running count for that (checker, unit).
`rows` beats come every ROW_BEAT lines inside a long file scan, so no single
scan can stay silent for a whole file. Enabled when SHERLOCK_HEARTBEAT=1
(finalize.py / stopcheck.py set it for their children); otherwise silent, so
shell use and stdout/stderr byte-identity tests are unchanged.
"""
import os
import sys

PREFIX = "SHERLOCK-HB"
ENV = "SHERLOCK_HEARTBEAT"
UNITS = ("start", "file", "citation", "aggregate", "rows", "step", "done")
ROW_BEAT = 65536
_counts = {}


def enabled():
    return os.environ.get(ENV) == "1"


def beat(checker, unit):
    if not enabled():
        return
    key = (checker, unit)
    n = _counts.get(key, 0) + 1
    _counts[key] = n
    try:
        sys.stderr.write("%s %s %s %d\n" % (PREFIX, checker, unit, n))
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def lines(fh, checker):
    """Iterate a file, beating `rows` every ROW_BEAT lines."""
    if not enabled():
        yield from fh
        return
    i = 0
    for line in fh:
        i += 1
        if i % ROW_BEAT == 0:
            beat(checker, "rows")
        yield line


def parse(line):
    """-> (checker, unit, n) for a heartbeat line, else None."""
    parts = line.strip().split(" ")
    if len(parts) != 4 or parts[0] != PREFIX or parts[2] not in UNITS:
        return None
    try:
        return parts[1], parts[2], int(parts[3])
    except ValueError:
        return None
