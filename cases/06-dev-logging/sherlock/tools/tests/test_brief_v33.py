#!/usr/bin/env python3
"""v33: the phase briefs must be complete, absolute and self-contained.

A brief is what a subagent reads instead of carrying the skill body. Measured
2026-08-24 with a logging proxy: the skill body is ~68 KB of every request
(83,705 bytes without it, 152,245 with it), while a whole subagent run costs the
parent 1,570 bytes. So the brief is the interface between those two facts, and
if it is wrong the worker either guesses a path or falls back to the body.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SKILL = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v33"
TOOL = SKILL / "tools" / "brief.py"
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(work, corpus, *extra):
    return subprocess.run([sys.executable, str(TOOL), "--work", work,
                           "--corpus", corpus, "--skill-root", str(SKILL)] + list(extra),
                          capture_output=True, text=True)


def main():
    with tempfile.TemporaryDirectory() as td:
        corpus = os.path.join(td, "logs")
        os.makedirs(corpus)
        Path(corpus, "app.log").write_text("2036-01-01T00:00:00Z hello\n", encoding="utf-8")
        work = os.path.join(td, "work")

        p = run(work, corpus)
        check("brief.py exits 0 and creates the work dir", p.returncode == 0, p.stderr[-200:])
        triage = Path(work, "brief-triage.md")
        draft = Path(work, "brief-draft.md")
        check("both briefs are written", triage.is_file() and draft.is_file(), p.stdout)

        t, d = triage.read_text(encoding="utf-8"), draft.read_text(encoding="utf-8")

        # A brief that is as big as the skill body defeats its own purpose.
        for name, text in (("triage", t), ("draft", d)):
            n = len(text.encode("utf-8"))
            check("the %s brief stays small (%d bytes < 8192)" % (name, n), n < 8192, str(n))

        # Every path a worker needs must be absolute: it cannot guess a cwd.
        for name, text in (("triage", t), ("draft", d)):
            rel = [ln for ln in text.splitlines()
                   if "`./" in ln or "`work/" in ln or "`tools/" in ln]
            check("the %s brief has no relative path" % name, not rel, "; ".join(rel[:2]))
            check("the %s brief names the corpus absolutely" % name,
                  os.path.abspath(corpus) in text)
            check("the %s brief names the worklist absolutely" % name,
                  os.path.abspath(os.path.join(work, "worklist.tsv")) in text)
            check("the %s brief points at the real tools dir" % name,
                  str((SKILL / "tools").resolve()) in text)

        # Each worker must know exactly what to hand back, or the parent has to
        # go read the files itself — which is the cost we are removing.
        check("the triage brief demands its gate", "triagecheck.py" in t)
        check("the triage brief fixes the reply shape", "РАЗОБРАНО:" in t and "TRIAGECHECK:" in t)
        check("the draft brief demands all three gates",
              all(x in d for x in ("citecheck.py", "triagecheck.py", "statecheck.py")))
        check("the draft brief points at the report format",
              str((SKILL / "reference" / "report-format.md").resolve()) in d)
        check("the draft brief fixes the reply shape",
              "ОТЧЁТ:" in d and "STATECHECK:" in d and "ВЕРДИКТ:" in d)
        check("the draft brief tells the worker not to redo the investigation",
              "НЕ повторяешь" in d or "не повторяй" in d.lower())

        # Nothing here may quietly re-inline the skill body.
        skill_body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        longest = max((ln for ln in skill_body.splitlines() if len(ln) > 60),
                      key=len, default="")
        check("the briefs do not copy the skill body",
              longest not in t and longest not in d, longest[:60])
        check("the briefs point at the skill body instead",
              str((SKILL / "SKILL.md").resolve()) in t)

        # --phase selects one.
        work2 = os.path.join(td, "work2")
        p = run(work2, corpus, "--phase", "draft")
        check("--phase draft writes only that brief",
              p.returncode == 0 and Path(work2, "brief-draft.md").is_file()
              and not Path(work2, "brief-triage.md").exists(), p.stdout)

        # A missing corpus is a usage error, not a silent empty brief.
        p = run(os.path.join(td, "work3"), os.path.join(td, "nope"))
        check("a missing corpus exits 2", p.returncode == 2, "exit %d" % p.returncode)

        # Re-running is safe and idempotent.
        before = triage.read_bytes()
        run(work, corpus)
        check("re-running rewrites the same bytes", triage.read_bytes() == before)

    print()
    if FAILED:
        print("✗ brief: %d проверок упало" % len(FAILED))
        return 1
    print("✓ brief: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
