#!/usr/bin/env python3
"""v34: SKILL.md fits Anthropic's 500-line ceiling, and nothing got lost.

The body is re-sent on EVERY model request. v33 measured 769 lines / 46,401
bytes; over ~55 turns that alone walks the request up to ~1.1 MB, which is where
the provider stops answering and returns an empty HTTP 200. v34 moves the axis
prose and the bulk-closure doctrine into reference files.

A split can fail four ways, and each one is a check here:
  * the body is still too long — nothing was gained;
  * a reference file points at another reference file (Anthropic's rule is ONE
    level deep, so a second hop is never taken);
  * a long reference has no `## Contents`, so the model reads all of it or none;
  * a literal the python gates parse verbatim, or a command, went missing in the
    move — the skill still looks right and silently stops working.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
V34 = SHERLOCK / "skills" / "v34"
V33 = SHERLOCK / "skills" / "v33"
SKILL_MD = V34 / "SKILL.md"
REFDIR = V34 / "reference"
LINE_CEILING = 500
TOC_THRESHOLD = 100
FAILED = []

# Every literal a python gate parses verbatim. Translating or re-spelling any of
# them fails the check at runtime, so the move must not drop one.
RUSSIAN_LITERALS = [
    "## Находки", "## Отклонённые кандидаты", "## Покрытие",
    "что сломано:", "улики:", "чем опровергал:", "атрибуция:", "исход:",
    "успех", "попытка", "норма",
    "Н-n", "К-n", "·",
    "пусто", "двоичный", "нечитабельно", "не смотрел",
    "байт=0", "формат=двоичный", "#R1",
]


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def ref_files():
    return sorted(p for p in REFDIR.glob("*.md"))


def documented_commands(text):
    out = []
    for raw in text.splitlines():
        line = raw.strip().strip("`")
        if not re.match(r"^(python3|bash|sh)\s", line):
            continue
        if ".py" not in line and ".sh" not in line:
            continue
        out.append(line)
    return out


def stage(td):
    """A skill copy plus the minimal corpus/work fixture every command needs."""
    work = Path(td) / "work-root"
    work.mkdir(parents=True)
    base = work / ".qwen" / "skills" / "log-rca"
    base.parent.mkdir(parents=True)
    shutil.copytree(str(V34), str(base))
    corpus = work / "logs"
    corpus.mkdir()
    (corpus / "app.log").write_text(
        "2036-02-03T04:05:06Z component=demo code=200 msg=start\n"
        "2036-02-03T04:06:06Z component=demo code=200 msg=stop\n", encoding="utf-8")
    (work / "work").mkdir()
    (work / "work" / "worklist.tsv").write_text(
        "# id\tвердикт\tось\tссылка\tчастота\tзапись\n", encoding="utf-8")
    (work / "work" / "rules.tsv").write_text("", encoding="utf-8")
    (work / "work" / "report.md").write_text("# Отчёт\n", encoding="utf-8")
    return work, base, corpus


def runnable(line, base, corpus):
    line = line.replace("<SKILL_BASE_DIR>", str(base)).replace("<LOG_DIR>", str(corpus))
    if "<" in line and ">" in line:
        return None
    return line


def main():
    body = SKILL_MD.read_text(encoding="utf-8")
    lines = body.splitlines()

    # 1. the ceiling
    check("SKILL.md is under Anthropic's %d-line ceiling" % LINE_CEILING,
          len(lines) < LINE_CEILING, "%d lines" % len(lines))
    print("  SKILL.md: %d lines / %d bytes (v33: %d / %d)"
          % (len(lines), len(body.encode("utf-8")),
             len((V33 / "SKILL.md").read_text(encoding="utf-8").splitlines()),
             len((V33 / "SKILL.md").read_bytes())))
    check("the split actually shrank the body versus v33",
          len(body.encode("utf-8")) < len((V33 / "SKILL.md").read_bytes()))

    refs = ref_files()
    check("the new bulk-closure reference exists",
          (REFDIR / "bulk-closure.md").is_file())
    check("at least four reference files ship", len(refs) >= 4, [p.name for p in refs])

    # 2. a table of contents on every long reference
    for p in refs:
        n = len(p.read_text(encoding="utf-8").splitlines())
        head = "\n".join(p.read_text(encoding="utf-8").splitlines()[:12])
        if n > TOC_THRESHOLD:
            check("%s (%d lines) opens with a `## Contents` block" % (p.name, n),
                  "## Contents" in head, head[:200])
        else:
            print("  %s: %d lines — no TOC required" % (p.name, n))

    # 3. references are ONE level deep
    names = {p.name for p in refs}
    for p in refs:
        hits = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for other in names:
                if other in line:
                    hits.append("%s:%d -> %s" % (p.name, i, other))
        check("%s points at no other reference file" % p.name, not hits, "; ".join(hits))

    # 4. SKILL.md names every reference file, with an imperative pointer
    for p in refs:
        check("SKILL.md names reference/%s" % p.name, p.name in body)
        pointing = [l for l in lines if p.name in l]
        window = []
        for i, l in enumerate(lines):
            if p.name in l:
                window.append("\n".join(lines[max(0, i - 3):i + 4]))
        blob = "\n".join(window)
        check("SKILL.md pointer to %s is imperative" % p.name,
              bool(re.search(r"MUST read|READ IT|read it in that case|read it|Read ",
                             blob)),
              "\n".join(pointing)[:200])

    # 5. every parsed literal survived somewhere in the skill
    corpus_text = body + "\n" + "\n".join(
        p.read_text(encoding="utf-8") for p in refs)
    for lit in RUSSIAN_LITERALS:
        check("literal %r still ships" % lit, lit in corpus_text)
    for letter in ("D", "N", "X"):
        check("worklist letter %r is still documented" % letter,
              re.search(r"`%s`" % letter, corpus_text) is not None)

    # 6. every command in SKILL.md still resolves and runs
    raw = documented_commands(body)
    check("SKILL.md still documents commands to run", len(raw) >= 6, len(raw))
    with tempfile.TemporaryDirectory() as td:
        work, base, corpus = stage(td)
        env = dict(os.environ)
        env.pop("QWEN_SKILL_ROOT", None)
        env["HOME"] = str(work)
        bad = []
        ran = 0
        for c in raw:
            cmd = runnable(c, base, corpus)
            if not cmd:
                continue
            ran += 1
            p = subprocess.run(["bash", "-c", cmd], cwd=str(work), env=env,
                               capture_output=True, text=True)
            err = p.stderr or ""
            if ("No such file or directory" in err or "can't open file" in err
                    or "Traceback" in err):
                bad.append("%s -> %s" % (cmd.split()[1][-40:],
                                         err.strip().splitlines()[-1][:120]))
        check("every documented command resolves (%d run)" % ran, not bad,
              "; ".join(bad[:3]))

    # 7. version markers
    markers = [
        ("tools/logmap.py", r'"version": (\d+),'),
        ("tools/stopcheck.py", r"if version != (\d+):"),
        ("tools/statecheck.py", r"^VERSION = (\d+)$"),
        ("tools/brief.py", r"^VERSION = (\d+)$"),
        ("SKILL.md", r"MANDATORY AUTOMATON v(\d+):"),
    ]
    for rel, pat in markers:
        for skdir, want in ((V34, "34"), (V33, "33")):
            text = (skdir / rel).read_text(encoding="utf-8")
            got = re.findall(pat, text, re.M)
            check("%s/%s reads %s" % (skdir.name, rel, want),
                  got and all(g == want for g in got), got)

    print()
    if FAILED:
        print("✗ v34 reference split: %d проверок упало" % len(FAILED))
        return 1
    print("✓ v34 reference split: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
