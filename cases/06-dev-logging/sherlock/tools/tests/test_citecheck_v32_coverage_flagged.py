#!/usr/bin/env python3
"""v32 P7: a coverage row must quote a line the mapper actually flagged.

The failure this locks down: the run that missed the intrusion wrote a coverage
row for `System.jsonl` and proved it with line 192 — a real line, a true quote,
71 lines away from the service install at 263 that `logmap` had flagged. Any
line used to pass, so "I looked at this file" was unfalsifiable.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
TOOL = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v32" / "tools" / "citecheck.py"

FLAGGED = "2036-02-03T04:05:06Z type=SERVICE_START component=demo unit=put code=200"
BORING = "2036-02-03T04:06:00Z component=demo state=quiet code=200"
CONTROL = "2036-02-03T04:07:00Z component=demo state=idle code=200"
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def load():
    spec = importlib.util.spec_from_file_location("citecheck_v32_p7", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CITE = load()


def corpus(root):
    host = Path(root) / "host"
    host.mkdir(parents=True, exist_ok=True)
    (host / "app.log").write_text(BORING + "\n" + FLAGGED + "\n" + CONTROL + "\n",
                                  encoding="utf-8")
    return root


def worklist(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
        fh.write("g001\tN #R1 фон\todd\thost/app.log:2\tn=1\t%s\n" % FLAGGED)


def report(cov_line):
    return "\n".join([
        "# Отчёт",
        "## Находки",
        "### Н-1 · проверяемое наблюдение",
        "что сломано: проверка держит адрес.",
        "улики: host/app.log:2 «%s»" % FLAGGED,
        "чем опровергал: host/app.log:3 «%s»" % CONTROL,
        "атрибуция: не установлена",
        "исход: норма",
        "## Отклонённые кандидаты",
        "### К-1 · штатный фон",
        "что выглядело как причина: похожий запуск.",
        "улики: host/app.log:3 «%s»" % CONTROL,
        "исход: норма",
        "## Покрытие",
        "| path | status | detail |",
        "| --- | --- | --- |",
        cov_line,
    ]) + "\n"


def evidence(text, root, ledger=None):
    d = CITE.check(text, root, 0.34, 3, True)
    if ledger:
        d["flagged"] = CITE.flagged_lines(ledger)
    return CITE.report_evidence(text, d)


def main():
    with tempfile.TemporaryDirectory() as td:
        corpus(td)
        wl = os.path.join(td, "worklist.tsv")
        worklist(wl)

        idx = CITE.flagged_lines(wl)
        check("the flagged index reads the worklist reference column",
              idx.get("app.log") == {2}, repr(idx))

        off = report("| host/app.log | наблюдение | host/app.log:1 «%s» |" % BORING)
        ev = evidence(off, td, wl)
        check("a coverage quote on an unflagged line is blocking",
              len(ev["coverage"]["unflagged_citation"]) == 1,
              repr(ev["coverage"]))
        check("it counts toward blocking", ev["blocking"] > 0)

        on = report("| host/app.log | наблюдение | host/app.log:2 «%s» |" % FLAGGED)
        ev = evidence(on, td, wl)
        check("a coverage quote on the flagged line passes",
              not ev["coverage"]["unflagged_citation"], repr(ev["coverage"]))

        # Without a ledger there is no index, so the rule cannot fire: citecheck
        # run bare must behave exactly as before.
        ev = evidence(off, td, None)
        check("no ledger, no new failure", not ev["coverage"]["unflagged_citation"])

        # A file the mapper flagged nothing for cannot be held to a flagged line.
        (Path(td) / "host" / "quiet.log").write_text(CONTROL + "\n", encoding="utf-8")
        unflagged_file = report("| host/quiet.log | наблюдение | host/quiet.log:1 «%s» |" % CONTROL)
        ev = evidence(unflagged_file, td, wl)
        check("a file with no flagged lines is not held to one",
              not ev["coverage"]["unflagged_citation"], repr(ev["coverage"]))

        # End to end, exit code and message.
        rep = os.path.join(td, "report.md")
        open(rep, "w", encoding="utf-8").write(off)
        p = subprocess.run([sys.executable, str(TOOL), rep, "--corpus", td,
                            "--require-quote", "--ledger", wl],
                           capture_output=True, text=True)
        check("the CLI exits non-zero", p.returncode != 0, "exit %d" % p.returncode)
        check("the CLI names the offending coverage row",
              "не попала ни в одну строку, отмеченную logmap" in p.stdout,
              p.stdout[-400:])

    print()
    if FAILED:
        print("✗ citecheck P7: %d проверок упало" % len(FAILED))
        return 1
    print("✓ citecheck P7: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
