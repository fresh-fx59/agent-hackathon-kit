#!/usr/bin/env python3
"""Append one finished section to work/report.md (v68). No model prose is changed.

  reportappend.py append   --work ./work < section.md     # one `### Н-n` block or one `##` section
  reportappend.py coverage --work ./work --corpus DIR      # covermap.py output as «Покрытие»
  reportappend.py rollover --work ./work --corpus DIR --cite F [--cite F...]   # «Окно записей»
  reportappend.py finish   --work ./work                   # delete the СИНТЕЗ НЕ ЗАВЕРШЁН line

The only file it ever writes is <work>/report.md. The first write replaces the
init stub; the marker line is kept as the LAST line until `finish`, so a run
that dies mid-synthesis still leaves a report the Stop hook refuses to deliver.
"""
import argparse
import os
import subprocess
import sys

MARKER = "СИНТЕЗ НЕ ЗАВЕРШЁН — удали эту строку последним действием синтеза."
MARKER_KEY = "СИНТЕЗ НЕ ЗАВЕРШЁН"
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_SECTION = 12000  # bytes: one block or one section, never a whole report


def report_path(work):
    return os.path.join(work, "report.md")


def split(text):
    """-> (body without marker lines, had_marker_or_stub)"""
    lines = text.splitlines()
    body = [l for l in lines if MARKER_KEY not in l]
    stub = len(body) != len(lines) or not text.strip()
    return body, stub


def is_stub_body(body):
    """The init stub: heading + status lines, no real section yet."""
    return not any(l.startswith(("## ", "### ")) for l in body) and not any(
        l.startswith("# ") and "Окно записей" in l for l in body)


def write_section(work, section):
    if not os.path.isdir(work):
        sys.exit("reportappend.py: нет каталога work: %s" % work)
    section = section.strip("\n")
    if not section.strip():
        sys.exit("reportappend.py: пустая секция")
    if MARKER_KEY in section:
        sys.exit("reportappend.py: секция содержит маркер синтеза")
    if len(section.encode("utf-8")) > MAX_SECTION:
        sys.exit("reportappend.py: секция %d B > %d B — пиши по одному блоку "
                 "`### Н-n` или одному разделу за вызов"
                 % (len(section.encode("utf-8")), MAX_SECTION))
    path = report_path(work)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    body, had_marker = split(old)
    if is_stub_body(body):
        body = ["# Отчёт Sherlock"] if not any(
            l.startswith("# ") for l in body) else [
            next(l for l in body if l.startswith("# "))]
    while body and not body[-1].strip():
        body.pop()
    out = "\n".join(body) + "\n\n" + section + "\n"
    if had_marker or not old:
        out += "\n" + MARKER + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, path)
    print("reportappend: +%d B -> %s (%d B)" % (
        len(section.encode("utf-8")), path, len(out.encode("utf-8"))))


def run_tool(script, args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.stderr.write(r.stderr)
        sys.exit("reportappend.py: %s exit %d" % (script, r.returncode))
    sys.stderr.write(r.stderr)
    return r.stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("append", "coverage", "rollover", "finish"):
        p = sub.add_parser(name)
        p.add_argument("--work", required=True)
        if name in ("coverage", "rollover"):
            p.add_argument("--corpus", required=True)
        if name == "rollover":
            p.add_argument("--cite", action="append", default=[])
    a = ap.parse_args(argv)
    if a.cmd == "append":
        write_section(a.work, sys.stdin.read())
    elif a.cmd == "coverage":
        wl = os.path.join(a.work, "worklist.tsv")
        args = ["--corpus", a.corpus, "--header"]
        if os.path.exists(wl):
            args += ["--worklist", wl]
        write_section(a.work, "## Покрытие\n\n" + run_tool("covermap.py", args))
    elif a.cmd == "rollover":
        args = ["--corpus", a.corpus, "--report", "--required-only"]
        for c in a.cite:
            args += ["--cite", c]
        out = run_tool("rollover.py", args)
        # rollover --report prints "# Окно записей" (h1): keep it, a top-level heading
        write_section(a.work, out)
    elif a.cmd == "finish":
        path = report_path(a.work)
        if not os.path.exists(path):
            sys.exit("reportappend.py: нет %s" % path)
        body, _ = split(open(path, encoding="utf-8").read())
        while body and not body[-1].strip():
            body.pop()
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body) + "\n")
        print("reportappend: маркер удалён")
    return 0


if __name__ == "__main__":
    sys.exit(main())
