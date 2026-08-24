#!/usr/bin/env python3
"""v36: four gates that printed failure and exited 0.

Every check below was MEASURED on v35's own copy of the tool before v36 existed,
and every one of them is asserted twice: once that v36 now fails closed, and once
that v35 STILL CARRIES THE DEFECT VERBATIM. v35 has a paid result attached to it
(run sherlock-winevtx-runs-v35-r3/20260824T164623Z-v35 — report.md 24,956 bytes,
all three gates independently green on a hash-exact reconstruction of the staged
corpus), so v35 must never be "fixed" by editing it. The arm stays frozen and the
repair lands one version later; this file is what keeps that true.

The four defects, all of the shape "a gate prints failure and exits 0":

  1. citecheck.py --ledger — the ledger branch returned on the ledger counters
     alone (`0 if (left == 0 and not bad_delivery) else 1`), so the BAD /
     outcomes / report_evidence verdicts reached the exit code only through
     `ledger()`'s own `total`. MEASURED: in v35 that total is arithmetically a
     superset, so no report could slip through by that route — the loophole was
     latent, not reachable. What WAS reachable, and is measured here on the real
     r3 report, is the empty ledger: a header-only worklist.tsv made v35 print
     «ИТОГ: можно отдавать отчёт.» and exit 0. A ledger with no rows is not a
     resolved ledger; it is a ledger that was never built. v36 fails on it, and
     v36 also states the blocking-defect invariant in ONE place used by BOTH
     exits instead of relying on the arithmetic of another function.
  2. triagecheck.py — a MISSING rules.tsv went into `junk`, printed with a ✗,
     and was never summed into `blocking`. It also exited 0 with rows still
     open and 0 on a worklist with no rows at all. A gate that cannot find its
     input must fail.
  3. stopcheck.py — `except Exception: sys.exit(allow(...))`, and `allow()`
     sets suppressOutput. A crashed gate, a session with no marker, and a
     genuine clean pass were byte-identical afterwards. v36 still fails open
     (blocking a session because the gate broke is worse) but does it loudly:
     no suppressOutput, a `failedOpen` flag, stderr with the traceback, and an
     appended record under .sherlock/stopcheck-failed-open.jsonl.
  4. statecheck.py — `bad` was gated on `and a.report` and the exit was
     `1 if bad else 0`, so a census-only run and an empty corpus both printed
     «не отвечено: 0 из 0» and exited 0.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
KIT = os.path.normpath(os.path.join(SHERLOCK, "..", "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
V35 = os.path.join(SKILLS, "v35", "tools")
V36 = os.path.join(SKILLS, "v36", "tools")
FAILED = []

# The real worklist.tsv writes its column names as a `#` comment, and a resolved
# row's verdict cell reads `N <path>:<line> «quote» n=k фон` — copied from
# sherlock-winevtx-runs-v35-r3/20260824T164623Z-v35/work/worklist.tsv so the
# fixtures are graded by the same code path the paid run was.
HEADER = u"# id\tвердикт\tось\tчастота\tзапись\n"
CLOSED_ROW = u"W-1\tN a.log:1 «alpha beta gamma delta» n=1 фон\todd\tn=1\talpha beta gamma delta\n"
CLOSED_ROW_TRIAGE = u"W-1\tN a.log:1 «alpha beta» n=1 фон\todd\tn=1\talpha beta\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(tool_dir, name, args, cwd=None, env=None, stdin=b""):
    e = dict(os.environ)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        e.update(env)
    p = subprocess.Popen([sys.executable, os.path.join(tool_dir, name)] + args,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, cwd=cwd, env=e)
    out, err = p.communicate(stdin)
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def w(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    io.open(path, "w", encoding="utf-8").write(text)
    return path


# ---------------------------------------------------------------------------
# 1. citecheck: the ledger branch
# ---------------------------------------------------------------------------
REPORT = u"""# Отчёт

## Находки

### Н-1 Событие

a.log:1 — «alpha beta gamma delta»
Исход: подтверждено.

## Отклонённые кандидаты

К-1 ничего
a.log:2 — «second line here»
Исход: отклонён.

## Покрытие

a.log:1 — «alpha beta gamma delta»
"""


def citecheck_checks(tmp):
    corpus = os.path.join(tmp, "corpus")
    w(os.path.join(corpus, "a.log"), u"alpha beta gamma delta\nsecond line here\n")
    report = w(os.path.join(tmp, "report.md"), REPORT)
    empty = w(os.path.join(tmp, "empty-worklist.tsv"), HEADER)

    args = [report, "--corpus", corpus, "--ledger", empty]
    rc35, out35, _ = run(V35, "citecheck.py", args)
    rc36, out36, _ = run(V36, "citecheck.py", args)
    # v35 never had the concept: it cannot say a ledger is empty, so on a
    # report it otherwise grades clean it prints «ИТОГ: можно отдавать отчёт.»
    # and exits 0. That exact exit-0 is measured on the real paid report in
    # real_data_checks(); here we pin that the notion is absent from v35 at all.
    check("citecheck: v35 never mentions an empty ledger (frozen defect)",
          u"ЛЕДЖЕР ПУСТ" not in out35
          and u"ЛЕДЖЕР ПУСТ" not in io.open(
              os.path.join(V35, "citecheck.py"), encoding="utf-8").read(),
          out35[-120:])
    check("citecheck: v36 exits non-zero on an empty ledger", rc36 != 0,
          "rc=%d" % rc36)
    check("citecheck: v36 names the empty ledger in its output",
          u"ЛЕДЖЕР ПУСТ" in out36,
          out36[-200:])

    # A NON-empty ledger must be graded exactly as v35 graded it: the new guard
    # fires on emptiness only, and must not move any other verdict. (That the
    # genuinely passing case still passes is measured on real data — see
    # real_data_checks() below and the two corpus regressions.)
    ledger = w(os.path.join(tmp, "worklist.tsv"), HEADER + CLOSED_ROW)
    rc36ok, _, _ = run(V36, "citecheck.py",
                       [report, "--corpus", corpus, "--ledger", ledger])
    rc35ok, _, _ = run(V35, "citecheck.py",
                       [report, "--corpus", corpus, "--ledger", ledger])
    check("citecheck: v36 grades a NON-empty ledger exactly as v35 did",
          rc36ok == rc35ok, "v36=%d v35=%d" % (rc36ok, rc35ok))

    # the invariant, stated once and used by both exits
    src = io.open(os.path.join(V36, "citecheck.py"), encoding="utf-8").read()
    old = io.open(os.path.join(V35, "citecheck.py"), encoding="utf-8").read()
    check("citecheck: v36 computes blocking_defects once for BOTH exits",
          src.count("blocking_defects") >= 3, src.count("blocking_defects"))
    check("citecheck: v36's ledger exit tests blocking_defects",
          "and not blocking_defects" in src)
    check("citecheck: v35 STILL has the ledger-only exit (frozen defect)",
          "return 0 if (left == 0 and not bad_delivery) else 1" in old)
    check("citecheck: v36 no longer has the ledger-only exit",
          "return 0 if (left == 0 and not bad_delivery) else 1" not in src)


# ---------------------------------------------------------------------------
# 2. triagecheck: a missing input, open rows, an empty worklist
# ---------------------------------------------------------------------------
def triagecheck_checks(tmp):
    corpus = os.path.join(tmp, "corpus")
    w(os.path.join(corpus, "a.log"), u"alpha beta\n")
    empty = w(os.path.join(tmp, "wl-empty.tsv"), HEADER)
    openwl = w(os.path.join(tmp, "wl-open.tsv"),
               HEADER + u"W-1\t?\todd\tn=1\talpha beta\n")
    missing = os.path.join(tmp, "no-such-rules.tsv")

    cases = [
        ("a MISSING rules.tsv",
         ["--worklist", openwl, "--rules", missing, "--corpus", corpus]),
        ("an EMPTY worklist",
         ["--worklist", empty, "--corpus", corpus]),
        ("rows still unresolved",
         ["--worklist", openwl, "--corpus", corpus]),
    ]
    for label, args in cases:
        rc35, _, _ = run(V35, "triagecheck.py", args)
        rc36, out36, _ = run(V36, "triagecheck.py", args)
        check("triagecheck: v35 STILL exits 0 on %s (frozen defect)" % label,
              rc35 == 0, "rc=%d" % rc35)
        check("triagecheck: v36 exits non-zero on %s" % label, rc36 != 0,
              "rc=%d" % rc36)
        check("triagecheck: v36 says НЕ ЗАКОНЧЕНО on %s" % label,
              u"НЕ ЗАКОНЧЕНО" in out36, out36[-160:])

    # the missing-file complaint must now be inside `blocking`, not only in junk
    rc, out, _ = run(V36, "triagecheck.py",
                     ["--worklist", openwl, "--rules", missing,
                      "--corpus", corpus, "--json"])
    d = json.loads(out)
    check("triagecheck: v36 counts junk rules.tsv lines in blocking",
          d["totals"].get(u"мусорных строк rules.tsv") == 1
          and d["blocking"] >= 1, d["blocking"])
    check("triagecheck: v36 counts unresolved rows in blocking",
          d["totals"].get(u"неразобранных строк") == 1,
          d["totals"])

    # and the good case still passes
    good = w(os.path.join(tmp, "wl-good.tsv"), HEADER + CLOSED_ROW_TRIAGE)
    rc36ok, out36ok, _ = run(V36, "triagecheck.py",
                             ["--worklist", good, "--corpus", corpus])
    check("triagecheck: v36 keeps a fully closed worklist at exit 0",
          rc36ok == 0, "rc=%d\n%s" % (rc36ok, out36ok[-400:]))


# ---------------------------------------------------------------------------
# 3. stopcheck: fail open, but loudly and on the record
# ---------------------------------------------------------------------------
WRAPPER = u'''import sys
sys.path.insert(0, %r)
import stopcheck
def boom():
    raise RuntimeError("synthetic stopcheck crash")
stopcheck.read_hook_input = boom
sys.exit(stopcheck.run())
'''


def stopcheck_checks(tmp):
    old = io.open(os.path.join(V35, "stopcheck.py"), encoding="utf-8").read()
    new = io.open(os.path.join(V36, "stopcheck.py"), encoding="utf-8").read()
    check("stopcheck: v35 STILL has the silent fail-open (frozen defect)",
          'sys.exit(allow("Sherlock stopcheck failed open"))' in old)
    check("stopcheck: v36's process guard is sys.exit(run()), not a bare allow",
          'if __name__ == "__main__":\n    sys.exit(run())' in new
          and 'except Exception:\n        sys.exit(allow('
          '"Sherlock stopcheck failed open"))' not in new)
    check("stopcheck: v35 has no failed_open() at all",
          "def failed_open(" not in old)
    check("stopcheck: v36 defines failed_open() and run()",
          "def failed_open(" in new and "def run(" in new)

    work = os.path.join(tmp, "stopws")
    os.makedirs(work)
    wrapper = w(os.path.join(tmp, "crash.py"), WRAPPER % V36)
    p = subprocess.Popen([sys.executable, wrapper], cwd=work,
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                  SHERLOCK_FAILOPEN_DIR=work))
    out, err = p.communicate(b"")
    out = out.decode("utf-8", "replace")
    err = err.decode("utf-8", "replace")
    check("stopcheck: v36 still fails OPEN (exit 0, decision allow)",
          p.returncode == 0 and json.loads(out.strip())["decision"] == "allow",
          "rc=%d out=%r" % (p.returncode, out))
    payload = json.loads(out.strip())
    check("stopcheck: v36 does NOT suppress the fail-open output",
          "suppressOutput" not in payload, payload)
    check("stopcheck: v36 flags the decision as failedOpen",
          payload.get("failedOpen") is True, payload)
    check("stopcheck: v36 says FAILED OPEN in the reason",
          "FAILED OPEN" in payload.get("reason", ""), payload.get("reason"))
    check("stopcheck: v36 writes the crash and traceback to stderr",
          "FAILED OPEN" in err and "synthetic stopcheck crash" in err
          and "Traceback" in err, err[-300:])
    rec = os.path.join(work, ".sherlock", "stopcheck-failed-open.jsonl")
    check("stopcheck: v36 leaves a durable record on disk", os.path.isfile(rec),
          rec)
    if os.path.isfile(rec):
        line = json.loads(io.open(rec, encoding="utf-8").read().splitlines()[0])
        check("stopcheck: the record names the event, the tool and the version",
              line["event"] == "failed_open" and line["version"] == 36
              and "synthetic stopcheck crash" in line["detail"], line)

    # a real clean pass in a workspace with no marker stays silent
    rc, out2, err2 = run(V36, "stopcheck.py", [], cwd=work)
    check("stopcheck: v36 keeps the no-marker allow silent (suppressOutput)",
          rc == 0 and json.loads(out2.strip()).get("suppressOutput") is True
          and "FAILED OPEN" not in out2,
          "rc=%d out=%r err=%r" % (rc, out2, err2))


# ---------------------------------------------------------------------------
# 4. statecheck: an empty census and a missing --report
# ---------------------------------------------------------------------------
def statecheck_checks(tmp):
    empty_corpus = os.path.join(tmp, "empty-corpus")
    os.makedirs(empty_corpus)
    corpus = os.path.join(tmp, "sc-corpus")
    w(os.path.join(corpus, "sec.jsonl"), u"")
    report = w(os.path.join(tmp, "sc-report.md"), u"ничего\n")

    args = ["--corpus", empty_corpus, "--report", report]
    rc35, out35, _ = run(V35, "statecheck.py", args)
    rc36, out36, err36 = run(V36, "statecheck.py", args)
    check("statecheck: v35 STILL exits 0 on an empty corpus (frozen defect)",
          rc35 == 0, "rc=%d" % rc35)
    check("statecheck: v35 prints the indistinguishable «не отвечено: 0 из 0»",
          u"не отвечено: 0 из 0" in out35, out35[-120:])
    check("statecheck: v36 exits non-zero on an empty corpus", rc36 != 0,
          "rc=%d" % rc36)
    check("statecheck: v36 explains the empty census on stderr",
          u"перепись пуста" in err36, err36)

    args = ["--corpus", empty_corpus]
    rc35b, _, _ = run(V35, "statecheck.py", args)
    rc36b, _, err36b = run(V36, "statecheck.py", args)
    check("statecheck: v35 STILL exits 0 with --report omitted (frozen defect)",
          rc35b == 0, "rc=%d" % rc35b)
    check("statecheck: v36 exits non-zero with --report omitted", rc36b != 0,
          "rc=%d" % rc36b)

    src = io.open(os.path.join(V36, "statecheck.py"), encoding="utf-8").read()
    old = io.open(os.path.join(V35, "statecheck.py"), encoding="utf-8").read()
    check("statecheck: v35 STILL has `return 1 if bad else 0` as its only exit",
          "return 1 if bad else 0" in old and "empty_census" not in old)
    check("statecheck: v36 has explicit no_report / empty_census exits",
          "no_report" in src and "empty_census" in src)


# ---------------------------------------------------------------------------
# 5. version markers, the SKILL.md ceiling, and the frozen arms
# ---------------------------------------------------------------------------
MARKERS = [
    ("tools/logmap.py", '"version": 36,'),
    ("tools/stopcheck.py", "if version != 36:"),
    ("tools/statecheck.py", "VERSION = 36"),
    ("tools/brief.py", "VERSION = 36"),
    ("SKILL.md", "MANDATORY AUTOMATON v36:"),
]
LINE_CEILING = 500


def version_and_freeze_checks():
    v36 = os.path.join(SKILLS, "v36")
    for rel, marker in MARKERS:
        body = io.open(os.path.join(v36, rel), encoding="utf-8").read()
        check("v36 marker in %s reads 36" % rel, marker in body, marker)
    for rel, _ in MARKERS:
        body = io.open(os.path.join(v36, rel), encoding="utf-8").read()
        check("v36 %s carries no leftover 35 marker" % rel,
              "VERSION = 35" not in body and '"version": 35,' not in body
              and "version != 35" not in body and "AUTOMATON v35" not in body)

    skill = io.open(os.path.join(v36, "SKILL.md"), encoding="utf-8").read()
    lines = skill.splitlines()
    check("v36 SKILL.md fits the %d-line ceiling" % LINE_CEILING,
          len(lines) < LINE_CEILING, "%d lines" % len(lines))
    print("  v36 SKILL.md: %d lines / %d bytes"
          % (len(lines), len(skill.encode("utf-8"))))

    # v1..v35 must be byte-for-byte what HEAD's merge-base already had.
    base = subprocess.check_output(
        ["git", "-C", KIT, "merge-base", "HEAD", "origin/main"]
    ).decode().strip()
    names = ["v%d" % n for n in range(1, 36)] + ["v4.1"]
    drift = []
    for name in names:
        rel = "cases/06-dev-logging/sherlock/skills/%s" % name
        try:
            want = subprocess.check_output(
                ["git", "-C", KIT, "rev-parse", "%s:%s" % (base, rel)],
                stderr=subprocess.STDOUT).decode().strip()
        except subprocess.CalledProcessError:
            drift.append("%s: not in merge-base" % name)
            continue
        got = subprocess.check_output(
            ["git", "-C", KIT, "rev-parse", "HEAD:%s" % rel]).decode().strip()
        if want != got:
            drift.append("%s: %s != %s" % (name, want[:12], got[:12]))
    check("skills/v1..v35 tree hashes are unchanged from the merge-base",
          not drift, "; ".join(drift))


# ---------------------------------------------------------------------------
# 6. the real-data proof (contabo only; skipped elsewhere)
# ---------------------------------------------------------------------------
# The paid v35 r3 run. Its staged corpus is rebuilt from work/path-map.tsv,
# which is a pure rename — 143/143 sha256 verified. This is where "v36 did not
# break the passing case" is actually measured: the same report, the same
# ledger, the same corpus that made all three gates green under v35 must still
# be green under v36.
R3 = ("/home/claude-developer/hack/sherlock-winevtx-runs-v35-r3/"
      "20260824T164623Z-v35")
STAGED = "/tmp/v36-staged-corpus"


def real_data_checks():
    report = os.path.join(R3, "work", "report.md")
    worklist = os.path.join(R3, "work", "worklist.tsv")
    if not (os.path.isfile(report) and os.path.isdir(STAGED)):
        print("  (skipped: the v35 r3 trace / rebuilt staged corpus is not on "
              "this host)")
        return
    args = [report, "--corpus", STAGED, "--ledger", worklist]
    rc35, _, _ = run(V35, "citecheck.py", args)
    rc36, _, _ = run(V36, "citecheck.py", args)
    check("real data: v36 citecheck still passes the v35 r3 report (was 0)",
          rc36 == 0 and rc35 == 0, "v36=%d v35=%d" % (rc36, rc35))

    # THE false green, on the real report: strip every data row out of the
    # ledger and v35 still says «можно отдавать отчёт» and exits 0.
    hollow = os.path.join(tempfile.mkdtemp(prefix="v36-hollow-"),
                          "worklist.tsv")
    keep = [ln for ln in io.open(worklist, encoding="utf-8", errors="replace")
            if ln.startswith("#")]
    io.open(hollow, "w", encoding="utf-8").write(u"".join(keep))
    hargs = [report, "--corpus", STAGED, "--ledger", hollow]
    rc35h, out35h, _ = run(V35, "citecheck.py", hargs)
    rc36h, out36h, _ = run(V36, "citecheck.py", hargs)
    check("real data: v35 STILL exits 0 on a hollowed ledger (frozen defect)",
          rc35h == 0 and u"можно отдавать отчёт" in out35h,
          "rc=%d" % rc35h)
    check("real data: v36 exits non-zero on the same hollowed ledger",
          rc36h != 0 and u"ЛЕДЖЕР ПУСТ" in out36h, "rc=%d" % rc36h)
    rc36t, _, _ = run(V36, "triagecheck.py",
                      ["--worklist", worklist, "--rules",
                       os.path.join(R3, "work", "rules.tsv"),
                       "--corpus", STAGED])
    check("real data: v36 triagecheck still passes the v35 r3 worklist",
          rc36t == 0, "rc=%d" % rc36t)
    rc36s, _, _ = run(V36, "statecheck.py",
                      ["--corpus", STAGED, "--report", report])
    check("real data: v36 statecheck still passes the v35 r3 report",
          rc36s == 0, "rc=%d" % rc36s)


def main():
    tmp = tempfile.mkdtemp(prefix="v36-gates-")
    citecheck_checks(tmp)
    triagecheck_checks(tmp)
    stopcheck_checks(tmp)
    statecheck_checks(tmp)
    version_and_freeze_checks()
    real_data_checks()
    if FAILED:
        print("✗ v36 gates: %d проверок упало" % len(FAILED))
        return 1
    print("✓ v36 gates: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
