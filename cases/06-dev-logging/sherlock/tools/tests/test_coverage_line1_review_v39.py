#!/usr/bin/env python3
"""The seven defects an adversarial review found in the P8 coverage-line rule.

Each block reproduces the review finding first and then asserts the fix, so a
regression reads as the original bug rather than as an anonymous red line.

  #1 a gzipped text file was permanently unwedgeable — `coverage_admissible_lines`
     enumerated the RAW COMPRESSED BYTES while `read_lines` decoded the stream,
     so no line of a `.gz` could pass. 1486 `.gz` files sit in the corpora.
  #2 producer/grader drift — `covermap`'s own «нечитабельно» row was graded
     `cov_false_unreadable` and blocked, and SKILL.md forbids the hand-typed
     citation that was the only escape.
  #3 `cov_false_unreadable` could be dropped from the blocking sum and the whole
     suite stayed green: nothing isolated it to `blocking == 1`.
  #4 same for `report_evidence`'s term in `_blocking_total`: the test asserted
     `blocking >= 1` on a report blocking for other reasons.
  #5 deleting worklist rows took the recorded v37 report from 59 blocking to 7
     with triagecheck still at 0 — the admissible set is derived from a file the
     model writes, and nothing checked it against what logmap emitted.
  #6 without `--ledger` every honest coverage row was false-blocked, because an
     empty flagged map was read as «logmap flagged nothing».
  #7 flags were keyed by lowercased basename, so `hostA/System.jsonl` authorized
     a citation into `hostB/System.jsonl`.
"""
import gzip
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
V39 = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v39" / "tools"
TOOL = V39 / "citecheck.py"
COVERMAP = V39 / "covermap.py"
LOGMAP = V39 / "logmap.py"
ROLLOVER = V39 / "rollover.py"

FAILED = []

# v39 (PR #79): a report citing corpus files owes an «Окно записей» section.
# Built by the REAL producer so these tests keep measuring the coverage rule.
CORP = None


def rollover_section():
    if CORP is None:
        return ""
    p = subprocess.run([sys.executable, str(ROLLOVER), "--corpus", CORP,
                        "--report", "--required-only", "--cite", ANCHOR_REL],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CITE = load("citecheck_v37_review", TOOL)
LMAP = load("logmap_v37_review", LOGMAP)


def line(name, n):
    return ("2036-02-03T04:05:06Z file=%s record=%d component=demo code=200 msg=r%d"
            % (name, n, n))


ANCHOR = "anchor.log"
ANCHOR_REL = "host/" + ANCHOR


def make_anchor(corp):
    """A 30-line file every fixture report hangs its finding and candidate on.

    Without it the report is missing a «Отклонённые кандидаты» block, which is
    itself one blocking defect — and a fixture that blocks for a second reason
    cannot ISOLATE the term under test. That non-isolation is review finding #4.
    """
    global CORP
    CORP = corp
    d = os.path.join(corp, "host")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ANCHOR), "w", encoding="utf-8") as fh:
        for i in range(1, 31):
            fh.write(line(ANCHOR, i) + "\n")


ANCHOR_REFS = [(ANCHOR_REL + ":12", line(ANCHOR, 12)),
               (ANCHOR_REL + ":13", line(ANCHOR, 13))]


def report(rows):
    body = ["# Отчёт", "## Находки",
            "### Н-1 · проверяемое наблюдение",
            "что сломано: проверка держит адрес.",
            "улики: %s:12 «%s»" % (ANCHOR_REL, line(ANCHOR, 12)),
            "чем опровергал: %s:13 «%s»" % (ANCHOR_REL, line(ANCHOR, 13)),
            "атрибуция: не установлена",
            "исход: норма",
            "## Отклонённые кандидаты",
            "### К-1 · штатный фон",
            "что выглядело как причина: похожий запуск.",
            "улики: %s:13 «%s»" % (ANCHOR_REL, line(ANCHOR, 13)),
            "исход: норма",
            "## Покрытие", "| path | status | detail |", "| --- | --- | --- |"]
    # The anchor needs a coverage row too, but a round-trip caller passes
    # covermap's own table, which already has one — two rows for one path is
    # `cov_duplicate_paths` and would block for a reason that is not under test.
    anchor = [] if any(ANCHOR_REL in r for r in rows) else [obs(ANCHOR_REL, 12, ANCHOR)]
    tail = rollover_section()
    return ("\n".join(body + anchor + rows) + "\n"
            + (("\n" + tail + "\n") if tail else ""))


def obs(rel, n, name=None):
    return "| %s | наблюдение | %s:%d «%s» |" % (
        rel, rel, n, line(name or os.path.basename(rel), n))


def worklist(path, refs):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        for i, (ref, disp) in enumerate(refs, 1):
            fh.write("g%03d\tN #R1 фон\todd\t%s\tn=1\t%s\n" % (i, ref, disp))


def coverage(text, corp, wl):
    d = CITE.check(text, corp, 0.34, 3, True)
    if wl is not None:
        d["flagged"] = CITE.flagged_lines(wl)
    return CITE.report_evidence(text, d)


def defects(c):
    return {k: len(v) for k, v in c.items()
            if isinstance(v, list) and v and not k.endswith("_detail")}


def run(*argv):
    return subprocess.run([sys.executable] + [str(a) for a in argv],
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
def finding_1_gzip():
    print("\n-- #1 a gzipped text file must have a passing row")
    with tempfile.TemporaryDirectory() as td:
        corp = os.path.join(td, "corpus")
        os.makedirs(os.path.join(corp, "host"))
        gz = os.path.join(corp, "host", "B.log.gz")
        with gzip.open(gz, "wt", encoding="utf-8") as fh:
            for i in range(1, 11):
                fh.write(line("B.log", i) + "\n")
        wl = os.path.join(td, "worklist.tsv")
        make_anchor(corp)
        worklist(wl, ANCHOR_REFS + [("host/B.log.gz:4", line("B.log", 4))])
        flagged = CITE.flagged_lines(wl)
        want = CITE._flagged_for(flagged, "host/B.log.gz")
        adm = CITE.coverage_admissible_lines(gz, want, "host/B.log.gz")
        check("gz: the admissible set is read through gzip, not off raw bytes",
              adm == {4}, adm)
        c = coverage(report([obs("host/B.log.gz", 4, "B.log")]), corp, wl)["coverage"]
        check("gz: the flagged line PASSES — a .gz is not unwedgeable",
              not defects(c), defects(c))
        c = coverage(report([obs("host/B.log.gz", 1, "B.log")]), corp, wl)["coverage"]
        check("gz: line 1 still blocks", bool(defects(c)), defects(c))
        r = run(COVERMAP, "--corpus", corp, "--worklist", wl)
        rows = [l for l in r.stdout.splitlines() if l.startswith("| ")]
        check("gz: covermap quotes decoded text, not compressed mojibake",
              any("B.log.gz:4" in l and "record=4" in l for l in rows), rows)
        c = coverage(report(rows), corp, wl)["coverage"]
        check("gz: ROUND TRIP — covermap's gz row grades clean",
              not defects(c), defects(c))


# ---------------------------------------------------------------------------
def _unquotable_corpus(td):
    """A file whose flagged lines cannot be quoted but whose last line can."""
    corp = os.path.join(td, "corpus")
    os.makedirs(os.path.join(corp, "host"))
    a = os.path.join(corp, "host", "A.log")
    with open(a, "w", encoding="utf-8") as fh:
        for i in range(1, 5):
            fh.write("x%d\n" % i)
        fh.write(line("A.log", 5) + "\n")
    wl = os.path.join(td, "worklist.tsv")
    make_anchor(corp)
    worklist(wl, ANCHOR_REFS + [("host/A.log:3", "x3")])
    return corp, a, wl


def finding_2_no_drift():
    print("\n-- #2 covermap's own output is never a blocking defect")
    with tempfile.TemporaryDirectory() as td:
        corp, a, wl = _unquotable_corpus(td)
        r = run(COVERMAP, "--corpus", corp, "--worklist", wl)
        rows = [l for l in r.stdout.splitlines() if l.startswith("| ")]
        check("the shape that drifted still produces «нечитабельно»",
              bool(rows) and "нечитабельно" in rows[0], rows)
        c = coverage(report(rows), corp, wl)["coverage"]
        check("covermap output is NOT cov_false_unreadable",
              not c["false_unreadable"], c["false_unreadable"])
        check("covermap output has no coverage defect at all",
              not defects(c), defects(c))
        check("the grader asks the producer's question",
              CITE.coverage_admissible_lines(a, {3}, "host/A.log") == set(),
              "flagged tier must agree with covermap")
        # and the lie it exists to catch still blocks
        lie = "| host/A.log | нечитабельно | ошибка=EACCES |"
        with open(a, "w", encoding="utf-8") as fh:
            for i in range(1, 9):
                fh.write(line("A.log", i) + "\n")
        c = coverage(report([lie]), corp, wl)["coverage"]
        check("«нечитабельно» on a file that reads AND quotes still blocks",
              len(c["false_unreadable"]) == 1, c["false_unreadable"])


# ---------------------------------------------------------------------------
def _false_unreadable_only(td):
    """A report whose ONLY defect is one «нечитабельно» lie."""
    corp = os.path.join(td, "corpus")
    os.makedirs(os.path.join(corp, "host"))
    for name in ("liar.log", "ok.log"):
        with open(os.path.join(corp, "host", name), "w", encoding="utf-8") as fh:
            for i in range(1, 9):
                fh.write(line(name, i) + "\n")
    wl = os.path.join(td, "worklist.tsv")
    make_anchor(corp)
    worklist(wl, ANCHOR_REFS + [("host/liar.log:4", line("liar.log", 4)),
                                ("host/ok.log:4", line("ok.log", 4))])
    rows = ["| host/liar.log | нечитабельно | ошибка=EACCES |",
            obs("host/ok.log", 4)]
    path = os.path.join(td, "r.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report(rows))
    return corp, wl, path, rows


def finding_3_false_unreadable_isolated():
    print("\n-- #3 cov_false_unreadable ALONE must move the blocking count to 1")
    with tempfile.TemporaryDirectory() as td:
        corp, wl, path, rows = _false_unreadable_only(td)
        ev = coverage(report(rows), corp, wl)
        c = ev["coverage"]
        check("the fixture's only defect is false_unreadable",
              defects(c) == {"false_unreadable": 1}, defects(c))
        check("report_evidence blocking is EXACTLY 1", ev["blocking"] == 1,
              ev["blocking"])
        r = run(TOOL, path, "--corpus", corp, "--require-quote",
                "--ledger", wl, "--json")
        got = json.loads(r.stdout)
        check("--json blocking is EXACTLY 1", got["blocking"] == 1, got["blocking"])
        r = run(TOOL, path, "--corpus", corp, "--require-quote", "--ledger", wl)
        check("--ledger exits 1 on it alone", r.returncode == 1, r.returncode)
        check("--ledger says «осталось 1»", "осталось 1" in r.stdout,
              r.stdout[-400:])
        r = run(TOOL, path, "--corpus", corp, "--require-quote")
        check("plain (no --ledger) exits 1 on it alone", r.returncode == 1,
              (r.returncode, r.stdout[-300:]))


def finding_4_report_evidence_isolated():
    print("\n-- #4 _blocking_total's report_evidence term ALONE must give 1")
    with tempfile.TemporaryDirectory() as td:
        corp, wl, path, rows = _false_unreadable_only(td)
        text = report(rows)
        d = CITE.check(text, corp, 0.34, 3, True)
        d["flagged"] = CITE.flagged_lines(wl)
        d["outcomes"] = CITE.outcomes_of(text)
        d["report_evidence"] = CITE.report_evidence(text, d)
        agg = d.get("aggregates") or {"blocking": 0}
        check("every OTHER term of _blocking_total is zero",
              sum(d["summary"].get(k, 0) for k in CITE.BAD) == 0
              and (d["summary"].get("не-ссылка") or 0) == 0
              and (d["outcomes"].get("blocking") or 0) == 0
              and (agg.get("blocking") or 0) == 0,
              (d["summary"], d["outcomes"].get("blocking"), agg.get("blocking")))
        total = CITE._blocking_total(d, text)
        check("_blocking_total is EXACTLY 1, so its ev term is load-bearing",
              total == 1, total)
        r = run(TOOL, path, "--corpus", corp, "--require-quote", "--json")
        got = json.loads(r.stdout)
        check("bare --json blocking is EXACTLY 1", got["blocking"] == 1,
              got["blocking"])


# ---------------------------------------------------------------------------
def finding_5_worklist_tamper():
    print("\n-- #5 deleting worklist rows must be caught, not rewarded")
    with tempfile.TemporaryDirectory() as td:
        corp = os.path.join(td, "corpus")
        os.makedirs(os.path.join(corp, "host"))
        with open(os.path.join(corp, "host", "big.log"), "w", encoding="utf-8") as fh:
            for i in range(1, 31):
                fh.write(line("big.log", i) + "\n")
        wl = os.path.join(td, "worklist.tsv")
        make_anchor(corp)
        worklist(wl, ANCHOR_REFS + [("host/big.log:1", line("big.log", 1)),
                                    ("host/big.log:12", line("big.log", 12))])
        path = os.path.join(td, "r.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report([obs("host/big.log", 12)]))

        r = run(TOOL, path, "--corpus", corp, "--require-quote", "--ledger", wl,
                "--json")
        got = json.loads(r.stdout)
        check("NO manifest: graded exactly as before, nothing new blocks",
              got["blocking"] == 0 and got["ledger"]["manifest"] is False,
              (got["blocking"], got["ledger"]))

        # what logmap now writes at emission time
        with open(wl, encoding="utf-8") as fh:
            LMAP.write_worklist_manifest(td, fh.readlines())
        man = os.path.join(td, "worklist.manifest.json")
        check("logmap writes worklist.manifest.json next to the worklist",
              os.path.isfile(man), man)
        with open(man, encoding="utf-8") as fh:
            data = json.load(fh)
        check("the manifest names every emitted id",
              data["ids"] == ["g001", "g002", "g003", "g004"]
              and data["rows"] == 4, data)

        r = run(TOOL, path, "--corpus", corp, "--require-quote", "--ledger", wl,
                "--json")
        got = json.loads(r.stdout)
        check("manifest present and intact: still 0 blocking",
              got["blocking"] == 0 and got["ledger"]["manifest"] is True,
              (got["blocking"], got["ledger"]))

        # the one-awk bypass: drop every reference that is not :1
        with open(wl, encoding="utf-8") as fh:
            keep = [l for l in fh
                    if l.startswith("#")
                    or "host/big.log:12" not in l]
        with open(wl, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        # with the row gone, line 1 is now the only flag and the lazy row passes
        lazy = os.path.join(td, "lazy.md")
        with open(lazy, "w", encoding="utf-8") as fh:
            fh.write(report([obs("host/big.log", 1)]))
        ev = coverage(report([obs("host/big.log", 1)]), corp, wl)["coverage"]
        check("the deletion really does buy a passing line-1 row",
              not ev["inadmissible_line"], ev["inadmissible_line_detail"])
        r = run(TOOL, lazy, "--corpus", corp, "--require-quote", "--ledger", wl,
                "--json")
        got = json.loads(r.stdout)
        check("BLOCKS: the deleted row is named and counted",
              got["blocking"] >= 1 and got["ledger"]["removed_rows"] == ["g004"],
              (got["blocking"], got["ledger"]))
        r = run(TOOL, lazy, "--corpus", corp, "--require-quote", "--ledger", wl)
        check("the refusal exits 1", r.returncode == 1, r.returncode)
        check("the refusal says the list was shortened",
              "укорочен" in r.stdout.lower() or "удалённых" in r.stdout,
              r.stdout[-500:])


# ---------------------------------------------------------------------------
def finding_6_no_ledger():
    print("\n-- #6 no --ledger must not false-block an honest row")
    with tempfile.TemporaryDirectory() as td:
        corp = os.path.join(td, "corpus")
        os.makedirs(os.path.join(corp, "host"))
        with open(os.path.join(corp, "host", "big.log"), "w", encoding="utf-8") as fh:
            for i in range(1, 31):
                fh.write(line("big.log", i) + "\n")
        wl = os.path.join(td, "worklist.tsv")
        make_anchor(corp)
        worklist(wl, ANCHOR_REFS + [("host/big.log:1", line("big.log", 1)),
                                    ("host/big.log:12", line("big.log", 12))])
        path = os.path.join(td, "r.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report([obs("host/big.log", 12)]))

        bare = json.loads(run(TOOL, path, "--corpus", corp, "--require-quote",
                              "--json").stdout)
        led = json.loads(run(TOOL, path, "--corpus", corp, "--require-quote",
                             "--ledger", wl, "--json").stdout)
        cb = bare["report_evidence"]["coverage"]
        cl = led["report_evidence"]["coverage"]
        check("bare: the correct row is NOT called inadmissible",
              not cb["inadmissible_line"], cb["inadmissible_line_detail"])
        check("bare and --ledger agree on the correct table",
              defects(cb) == defects(cl), (defects(cb), defects(cl)))
        check("bare SAYS the line rule was not applied",
              cb["line_rule"].startswith("пропущено"), cb["line_rule"])
        check("--ledger says the line rule WAS applied",
              cl["line_rule"] == "применено", cl["line_rule"])
        r = run(TOOL, path, "--corpus", corp, "--require-quote")
        check("the bare human output warns rather than silently skipping",
              "ПРАВИЛО ДОПУСТИМОЙ СТРОКИ НЕ ПРОВЕРЕНО" in r.stdout,
              r.stdout[-500:])
        # and with the ledger it is still a real rule
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report([obs("host/big.log", 1)]))
        led = json.loads(run(TOOL, path, "--corpus", corp, "--require-quote",
                             "--ledger", wl, "--json").stdout)
        cl = led["report_evidence"]["coverage"]
        check("--ledger still blocks the line-1 row",
              len(cl["inadmissible_line"]) == 1, cl["inadmissible_line_detail"])


# ---------------------------------------------------------------------------
def finding_7_per_path():
    print("\n-- #7 one file must not authorize another")
    with tempfile.TemporaryDirectory() as td:
        corp = os.path.join(td, "corpus")
        for host in ("hostA", "hostB"):
            os.makedirs(os.path.join(corp, host))
            with open(os.path.join(corp, host, "System.jsonl"), "w",
                      encoding="utf-8") as fh:
                for i in range(1, 31):
                    fh.write(line("System.jsonl", i) + "\n")
        wl = os.path.join(td, "worklist.tsv")
        make_anchor(corp)
        worklist(wl, ANCHOR_REFS + [("hostA/System.jsonl:12", line("System.jsonl", 12)),
                                    ("hostB/System.jsonl:20", line("System.jsonl", 20))])
        flagged = CITE.flagged_lines(wl)
        check("the index is keyed by path, not by basename",
              {k for k in flagged if "system" in k}
              == {"hosta/system.jsonl", "hostb/system.jsonl"},
              sorted(flagged))
        check("hostB resolves to hostB flags only",
              CITE._flagged_for(flagged, "hostB/System.jsonl") == {20},
              CITE._flagged_for(flagged, "hostB/System.jsonl"))
        check("hostA resolves to hostA flags only",
              CITE._flagged_for(flagged, "hostA/System.jsonl") == {12},
              CITE._flagged_for(flagged, "hostA/System.jsonl"))
        check("a shared basename with no path resolves to NOTHING",
              CITE._flagged_for(flagged, "System.jsonl") is None,
              CITE._flagged_for(flagged, "System.jsonl"))
        rows = [obs("hostA/System.jsonl", 12, "System.jsonl"),
                obs("hostB/System.jsonl", 12, "System.jsonl")]
        c = coverage(report(rows), corp, wl)["coverage"]
        check("BLOCKS: hostB citing the line only hostA flagged",
              len(c["unflagged_citation"]) + len(c["inadmissible_line"]) == 1,
              defects(c))
        rows = [obs("hostA/System.jsonl", 12, "System.jsonl"),
                obs("hostB/System.jsonl", 20, "System.jsonl")]
        c = coverage(report(rows), corp, wl)["coverage"]
        check("PASSES: each host answered with its own flagged line",
              not defects(c), defects(c))
        r = run(COVERMAP, "--corpus", corp, "--worklist", wl)
        rows = [l for l in r.stdout.splitlines() if l.startswith("| ")]
        check("covermap picks per-host lines, not the union",
              any("hostA/System.jsonl:12" in l for l in rows)
              and any("hostB/System.jsonl:20" in l for l in rows), rows)
        c = coverage(report(rows), corp, wl)["coverage"]
        check("ROUND TRIP: covermap multi-host output grades clean",
              not defects(c), defects(c))
        # a unique basename still resolves, so ordinary spelling drift works
        wl2 = os.path.join(td, "w2.tsv")
        worklist(wl2, [("rendered/only.jsonl:7", line("only.jsonl", 7))])
        f2 = CITE.flagged_lines(wl2)
        check("a UNIQUE basename still answers a bare spelling",
              CITE._flagged_for(f2, "only.jsonl") == {7}, sorted(f2))


# ---------------------------------------------------------------------------
def fail_closed_branches():
    """The two defensive returns inside `_coverage_line_admissible`.

    Both are unreachable through an ordinary report — an unresolved path is
    already counted as traversal/ambiguous/missing, and a file with no citable
    line normally trips `cov_unflagged_citation` first — so neither was covered
    by anything, and a mutation flipping either `return False` to `return True`
    survived the whole suite. Defensive code that nothing asserts is not
    defensive code. They are exercised directly, at the function.
    """
    print("\n-- fail-closed: the two defensive returns are load-bearing")
    cites = [{"verdict": "ok", "resolved": "host/x.log", "line": 4,
              "via": "quote"}]
    row = {"resolved_path": None, "path_problem": False, "path": "host/x.log"}
    check("an unresolved path with no path_problem BLOCKS",
          CITE._coverage_line_admissible(row, cites, {}, {4}) is False,
          row.get("inadmissible_why"))
    check("and it says why", "путь" in (row.get("inadmissible_why") or ""),
          row.get("inadmissible_why"))

    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "x.log")
        with open(f, "w", encoding="utf-8") as fh:
            for i in range(1, 9):
                fh.write(line("x.log", i) + "\n")
        row = {"resolved_path": "host/x.log", "path_problem": False,
               "path": "host/x.log"}
        real = CITE.coverage_admissible_lines
        CITE.coverage_admissible_lines = lambda *a, **k: set()
        try:
            got = CITE._coverage_line_admissible(row, cites,
                                                 {"host/x.log": f}, {4})
        finally:
            CITE.coverage_admissible_lines = real
        check("an EMPTY admissible set BLOCKS — it is not «anything goes»",
              got is False, (got, row.get("inadmissible_why")))
        check("and it says why",
              "цитируемой" in (row.get("inadmissible_why") or ""),
              row.get("inadmissible_why"))


def logmap_writes_the_manifest():
    """End to end: the tamper-evidence exists because `logmap` ran, not because
    a test called the writer."""
    print("\n-- #5 logmap itself emits the manifest on a real run")
    with tempfile.TemporaryDirectory() as td:
        corp = os.path.join(td, "logs")
        os.makedirs(corp)
        for name, n in (("app.log", 200), ("sys.log", 120)):
            with open(os.path.join(corp, name), "w", encoding="utf-8") as fh:
                for i in range(1, n + 1):
                    if name == "app.log" and i in (57, 58, 59):
                        fh.write("2036-02-03T04:%02d:06Z level=ERROR component=auth "
                                 "code=500 msg=DPAPI master key export failed for "
                                 "user root\n" % (i % 60))
                    else:
                        fh.write("2036-02-03T04:%02d:06Z level=INFO component=demo "
                                 "code=200 msg=heartbeat %d\n" % (i % 60, i))
        out = os.path.join(td, "work")
        r = run(LOGMAP, corp, "--out", out)
        wl = os.path.join(out, "worklist.tsv")
        check("logmap ran", r.returncode == 0 and os.path.isfile(wl),
              (r.returncode, r.stderr[-300:]))
        man = os.path.join(out, "worklist.manifest.json")
        check("logmap wrote worklist.manifest.json", os.path.isfile(man),
              sorted(os.listdir(out)) if os.path.isdir(out) else out)
        if not os.path.isfile(man) or not os.path.isfile(wl):
            return
        with open(man, encoding="utf-8") as fh:
            data = json.load(fh)
        with open(wl, encoding="utf-8") as fh:
            ids = [l.split("\t", 1)[0].strip() for l in fh
                   if not l.startswith("#") and l.strip()]
        check("the manifest lists exactly the ids in the worklist it shipped with",
              bool(ids) and data["ids"] == ids and data["rows"] == len(ids),
              (data.get("ids"), ids))
        if not ids:
            return
        check("nothing is reported removed on an untouched worklist",
              CITE.worklist_removed(wl) == ([], True), CITE.worklist_removed(wl))
        with open(wl, encoding="utf-8") as fh:
            keep = [l for l in fh if l.startswith("#") or not l.strip()
                    or not l.startswith(ids[0])]
        with open(wl, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        check("deleting one row is reported by id",
              CITE.worklist_removed(wl) == ([ids[0]], True),
              CITE.worklist_removed(wl))


def main():
    finding_1_gzip()
    finding_2_no_drift()
    finding_3_false_unreadable_isolated()
    finding_4_report_evidence_isolated()
    finding_5_worklist_tamper()
    finding_6_no_ledger()
    finding_7_per_path()
    fail_closed_branches()
    logmap_writes_the_manifest()
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
