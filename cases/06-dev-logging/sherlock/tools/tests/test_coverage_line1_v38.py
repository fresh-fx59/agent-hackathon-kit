#!/usr/bin/env python3
"""v37 P8: a coverage row may not answer a file with its first line.

THE MEASUREMENT. The v37 run at sherlock-winevtx-runs-v37-full-r1/
20260825T173021Z-v37 passed all three gates with 143 coverage rows for 143
corpus files. 81 of its 93 «наблюдение» rows quoted LINE 1, and every one of
them passed P7, because `logmap` names the FIRST member of a group as that
group's reference: `path:1` is on the worklist for almost every file. Among the
81 were the Opera launcher, the daily PowerShell script and DPAPI — none of
which reached the findings. Line 1 of a log is the oldest, dullest record; it
is what a tool reaches for when it needs *a* line.

THE RULE, and why it does not merely move the cliff to line 2:
`citecheck.coverage_admissible_lines` returns a CLOSED set of lines per file —
the flagged lines above 1, or line 1 when line 1 is the only flag, or all of a
file of two lines or fewer, or the single last quotable line when the mapper
flagged nothing. `covermap.py` picks from that same set, so the honest row
costs one tool call and every other line is refused by enumeration, line 2
included.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
V38 = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v38" / "tools"
TOOL = V38 / "citecheck.py"
COVERMAP = V38 / "covermap.py"
ROLLOVER = V38 / "rollover.py"

FAILED = []

# v38 (PR #79) makes a report that cites corpus files owe an «Окно записей»
# section. Without it `report_evidence()` counts a blocking defect and these
# tests would measure the rollover term instead of the coverage-line one.
# Built by the REAL producer, never hand-written.
CORP = None
_ROLL = {}


def use_corpus(corp, *cites):
    global CORP
    CORP = (corp, tuple(cites))
    return corp


def rollover_section():
    if CORP is None:
        return ""
    if CORP not in _ROLL:
        corp, cites = CORP
        argv = [sys.executable, str(ROLLOVER), "--corpus", corp,
                "--report", "--required-only"]
        for c in cites:
            argv += ["--cite", c]
        p = subprocess.run(argv, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        _ROLL[CORP] = p.stdout.strip()
    return _ROLL[CORP]


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def load():
    spec = importlib.util.spec_from_file_location("citecheck_v37_p8", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CITE = load()


def line(path, n):
    return "2036-02-03T04:05:06Z file=%s record=%d component=demo code=200 msg=r%d" % (
        os.path.basename(path), n, n)


FILES = {
    # name              lines  flagged
    "big.log":          (30, [1, 12]),
    "onlyfirst.log":    (30, [1]),
    "two.log":          (2, [1]),
    "one.log":          (1, [1]),
    "noflag.log":       (8, []),
}


def build(root):
    host = Path(root) / "corpus" / "host"
    host.mkdir(parents=True, exist_ok=True)
    for name, (n, _flags) in FILES.items():
        (host / name).write_text(
            "".join(line(name, i) + "\n" for i in range(1, n + 1)), encoding="utf-8")
    wl = os.path.join(root, "worklist.tsv")
    with open(wl, "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        k = 0
        for name, (_n, flags) in sorted(FILES.items()):
            for f in flags:
                k += 1
                fh.write("g%03d\tN #R1 фон\todd\thost/%s:%d\tn=1\t%s\n"
                         % (k, name, f, line(name, f)))
    return wl


def row(name, n):
    return "| host/%s | наблюдение | host/%s:%d «%s» |" % (name, name, n, line(name, n))


def report(rows):
    """A report that is clean apart from whatever the caller put in `rows`."""
    body = [
        "# Отчёт",
        "## Находки",
        "### Н-1 · проверяемое наблюдение",
        "что сломано: проверка держит адрес.",
        "улики: host/big.log:12 «%s»" % line("big.log", 12),
        "чем опровергал: host/big.log:13 «%s»" % line("big.log", 13),
        "атрибуция: не установлена",
        "исход: норма",
        "## Отклонённые кандидаты",
        "### К-1 · штатный фон",
        "что выглядело как причина: похожий запуск.",
        "улики: host/big.log:13 «%s»" % line("big.log", 13),
        "исход: норма",
        "## Покрытие",
        "| path | status | detail |",
        "| --- | --- | --- |",
    ]
    tail = rollover_section()
    return "\n".join(body + rows) + "\n" + (("\n" + tail + "\n") if tail else "")


def default_rows(override=None):
    """One correct row per corpus file, with `override` swapped in by name."""
    correct = {"big.log": 12, "onlyfirst.log": 1, "two.log": 1,
               "one.log": 1, "noflag.log": 8}
    out = []
    for name in sorted(FILES):
        if override and name in override:
            out.append(override[name])
        else:
            out.append(row(name, correct[name]))
    return out


def evidence(text, root, wl):
    d = CITE.check(text, root, 0.34, 3, True)
    d["flagged"] = CITE.flagged_lines(wl)
    return CITE.report_evidence(text, d)


def cov(text, root, wl):
    return evidence(text, root, wl)["coverage"]


def main():
    with tempfile.TemporaryDirectory() as td:
        corp = os.path.join(td, "corpus")
        wl = build(td)
        use_corpus(corp, "host/big.log")
        flagged = CITE.flagged_lines(wl)

        # ---- the rule itself -------------------------------------------
        def adm(name):
            return CITE.coverage_admissible_lines(
                os.path.join(corp, "host", name),
                CITE._flagged_for(flagged, "host/" + name), "host/" + name)

        check("many-line flagged file: line 1 is NOT admissible, 12 is",
              adm("big.log") == {12}, adm("big.log"))
        check("one-line file: line 1 is admissible",
              adm("one.log") == {1}, adm("one.log"))
        check("two-line file: both lines are admissible",
              adm("two.log") == {1, 2}, adm("two.log"))
        check("only line 1 flagged: line 1 is admissible",
              adm("onlyfirst.log") == {1}, adm("onlyfirst.log"))
        check("no flags at all: exactly the last line, nothing else",
              adm("noflag.log") == {8}, adm("noflag.log"))

        # ---- the same rule through the grader --------------------------
        base = cov(report(default_rows()), corp, wl)
        check("the correct table has no coverage defect",
              not base["inadmissible_line"] and not base["unflagged_citation"]
              and evidence(report(default_rows()), corp, wl)["blocking"] == 0,
              base)

        d = cov(report(default_rows({"big.log": row("big.log", 1)})), corp, wl)
        check("BLOCKS: line 1 quoted on a 30-line file with another flag",
              len(d["inadmissible_line"]) == 1, d["inadmissible_line_detail"])

        d = cov(report(default_rows({"big.log": row("big.log", 2)})), corp, wl)
        check("BLOCKS: line 2 instead — the cliff does not move",
              len(d["unflagged_citation"]) == 1, d)

        d = cov(report(default_rows({"big.log": row("big.log", 30)})), corp, wl)
        check("BLOCKS: the last line of a FLAGGED file is not a way out",
              len(d["unflagged_citation"]) == 1, d)

        d = cov(report(default_rows({"one.log": row("one.log", 1)})), corp, wl)
        check("PASSES: line 1 on a one-line file",
              not d["inadmissible_line"], d["inadmissible_line_detail"])

        d = cov(report(default_rows({"two.log": row("two.log", 1)})), corp, wl)
        check("PASSES: line 1 on a two-line file",
              not d["inadmissible_line"], d["inadmissible_line_detail"])

        d = cov(report(default_rows({"onlyfirst.log": row("onlyfirst.log", 1)})),
                corp, wl)
        check("PASSES: line 1 when line 1 is the only line the mapper flagged",
              not d["inadmissible_line"], d["inadmissible_line_detail"])

        for n in (1, 2, 4, 7):
            d = cov(report(default_rows({"noflag.log": row("noflag.log", n)})),
                    corp, wl)
            check("BLOCKS: unflagged file answered with line %d, not the last" % n,
                  len(d["inadmissible_line"]) == 1, d["inadmissible_line_detail"])
        d = cov(report(default_rows({"noflag.log": row("noflag.log", 8)})), corp, wl)
        check("PASSES: unflagged file answered with its last line",
              not d["inadmissible_line"], d["inadmissible_line_detail"])

        # ---- fail closed ------------------------------------------------
        bad = "| host/big.log | наблюдение | host/big.log:abc «%s» |" % line("big.log", 12)
        d = cov(report(default_rows({"big.log": bad})), corp, wl)
        check("FAILS CLOSED: a line number that does not parse still blocks",
              bool(d["missing_citation"] or d["invalid_citation"]
                   or d["inadmissible_line"]), d)

        bad = "| host/big.log | наблюдение | host/big.log:0 «%s» |" % line("big.log", 12)
        d = cov(report(default_rows({"big.log": bad})), corp, wl)
        check("FAILS CLOSED: line number 0 still blocks",
              bool(d["invalid_citation"] or d["missing_citation"]
                   or d["inadmissible_line"]), d)

        real = CITE.coverage_admissible_lines

        def boom(*a, **k):
            raise RuntimeError("synthetic failure inside the check")

        CITE.coverage_admissible_lines = boom
        try:
            d = cov(report(default_rows()), corp, wl)
        finally:
            CITE.coverage_admissible_lines = real
        check("FAILS CLOSED: an exception in the check blocks every row",
              len(d["inadmissible_line"]) == len(FILES), d["inadmissible_line_detail"])

        def oserr(*a, **k):
            raise OSError(13, "Permission denied")

        CITE.coverage_admissible_lines = oserr
        try:
            d = cov(report(default_rows()), corp, wl)
        finally:
            CITE.coverage_admissible_lines = real
        check("FAILS CLOSED: an unreadable file is not a clean file",
              len(d["inadmissible_line"]) == len(FILES), d["inadmissible_line_detail"])

        # ---- the next-cheapest lie, closed at the same time --------------
        lie = "| host/big.log | нечитабельно | ошибка=EACCES |"
        d = cov(report(default_rows({"big.log": lie})), corp, wl)
        check("BLOCKS: «нечитабельно» on a file that reads and quotes fine",
              len(d["false_unreadable"]) == 1, d)

        # ---- one ledger: the defect reaches the exit code -----------------
        path = os.path.join(td, "bad.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report(default_rows({"big.log": row("big.log", 1)})))
        r = subprocess.run([sys.executable, str(TOOL), path, "--corpus", corp,
                            "--require-quote", "--ledger", wl, "--json"],
                           capture_output=True, text=True)
        got = json.loads(r.stdout)
        check("the lazy row reaches the JSON blocking count",
              got["blocking"] >= 1, got.get("blocking"))
        # WITH `--ledger`, because that is the gate's own argv and because
        # without it citecheck does not KNOW what logmap flagged: the empty map
        # is not the fact «nothing was flagged». See the `flagged_known` note in
        # citecheck.report_evidence and test_coverage_line1_review_v38.py.
        r2 = subprocess.run([sys.executable, str(TOOL), path, "--corpus", corp,
                             "--require-quote", "--ledger", wl],
                            capture_output=True, text=True)
        check("the lazy row reaches the EXIT CODE", r2.returncode == 1, r2.returncode)
        check("the refusal names the tool that fixes it",
              "covermap.py" in r2.stdout, r2.stdout[-400:])

        # ---- producer and grader do not drift -----------------------------
        r = subprocess.run([sys.executable, str(COVERMAP), "--corpus", corp,
                            "--worklist", wl], capture_output=True, text=True)
        rows = [l for l in r.stdout.splitlines() if l.startswith("| ")]
        check("covermap emits one row per corpus file",
              len(rows) == len(FILES), rows)
        check("covermap never quotes line 1 of a many-line flagged file",
              not any("big.log:1 " in l or "big.log:1 —" in l for l in rows), rows)
        drift = []
        for l in rows:
            cells = [c.strip() for c in l.strip("|").split("|")]
            if cells[1] != "наблюдение":
                continue
            rel = cells[0]
            n = int(cells[2].split(rel + ":")[1].split(" ")[0])
            ok = CITE.coverage_admissible_lines(
                os.path.join(corp, rel), CITE._flagged_for(flagged, rel), rel)
            if n not in ok:
                drift.append((rel, n, sorted(ok)))
        check("every line covermap chose is one citecheck admits", not drift, drift)

        # round trip: covermap's own table, graded by citecheck
        rt = os.path.join(td, "roundtrip.md")
        with open(rt, "w", encoding="utf-8") as fh:
            fh.write(report(rows))
        r = subprocess.run([sys.executable, str(TOOL), rt, "--corpus", corp,
                            "--require-quote", "--ledger", wl, "--json"],
                           capture_output=True, text=True)
        got = json.loads(r.stdout)["report_evidence"]["coverage"]
        bad_keys = {k: v for k, v in got.items()
                    if isinstance(v, list) and v and not k.endswith("_detail")}
        check("ROUND TRIP: what covermap emits, citecheck grades clean",
              not bad_keys, bad_keys)

    print()
    if FAILED:
        print("НЕ ПРОЙДЕНО: %d" % len(FAILED))
        for n in FAILED:
            print("  - " + n)
        return 1
    print("все проверки пройдены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
