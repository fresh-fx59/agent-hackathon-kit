#!/usr/bin/env python3
"""v32: every documented command must resolve without naming the vendor's dir.

We do not run vanilla Qwen Code everywhere. A port can install skills under
`.agents/skills/`, `.codex/skills/`, or its own name, and it can call the skill
something other than `log-rca`. Hard-coding `.qwen/skills/log-rca` made the
documented commands unresolvable in exactly those places — and an unresolvable
command is invoked zero times, which is the defect
`test_documented_commands_run.py` was written for after it cost four metered
cells. `$QWEN_SKILL_ROOT` is not a fix either: it is exported by our own
`run-bench.sh`, not by the CLI.

So the paths are DISCOVERED: any dotted directory, any skill name, project first
then home. This test builds three different layouts and executes every command
SKILL.md prints in each of them.
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
V32 = SHERLOCK / "skills" / "v32"
FAILED = []

# (dotted directory, skill name, install into HOME instead of the project)
LAYOUTS = [
    (".qwen", "log-rca", False),      # what run-bench.sh builds
    (".agents", "sherlock", False),   # a port, different dir AND different name
    (".mycli", "log-rca", True),      # home-installed under an unknown vendor
]


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def documented_commands():
    out = []
    for raw in (V32 / "SKILL.md").read_text(encoding="utf-8").splitlines():
        line = raw.strip().strip("`")
        if not re.match(r"^(python3|bash|sh)\s", line):
            continue
        if ".py" not in line and ".sh" not in line:
            continue
        out.append(line)
    return out


def hook_command():
    text = (V32 / "SKILL.md").read_text(encoding="utf-8")
    fm = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
    for line in fm.splitlines():
        if line.strip().startswith("command:"):
            raw = line.split("command:", 1)[1].strip()
            return raw[1:-1].replace('\\"', '"')
    raise AssertionError("no Stop hook command")


def stage(td, dotdir, skill, in_home):
    home = Path(td) / "home"
    work = Path(td) / "work"
    home.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    base = (home if in_home else work) / dotdir / "skills" / skill
    shutil.copytree(str(V32), str(base))
    corpus = work / "logs"
    corpus.mkdir(exist_ok=True)
    (corpus / "app.log").write_text(
        "2036-02-03T04:05:06Z component=demo code=200 msg=start\n"
        "2036-02-03T04:06:06Z component=demo code=200 msg=stop\n", encoding="utf-8")
    (work / "work").mkdir(exist_ok=True)
    (work / "work" / "worklist.tsv").write_text(
        "# id\tвердикт\tось\tссылка\tчастота\tзапись\n", encoding="utf-8")
    (work / "work" / "rules.tsv").write_text("", encoding="utf-8")
    (work / "work" / "report.md").write_text("# Отчёт\n", encoding="utf-8")
    return home, work


def runnable(line):
    """SKILL.md prints placeholders; fill them with the staged fixture."""
    line = line.replace("<КАТАЛОГ_ЛОГОВ>", "./logs")
    if "<" in line and ">" in line:
        return None
    return line


def main():
    cmds = [c for c in (runnable(x) for x in documented_commands()) if c]
    check("SKILL.md still documents commands to run", len(cmds) >= 8, str(len(cmds)))
    check("no command names the vendor directory",
          not [c for c in cmds if ".qwen/" in c], "\n".join(c for c in cmds if ".qwen/" in c))

    hook = hook_command()
    check("the Stop hook does not name the vendor directory either",
          ".qwen/" not in hook, hook)

    for dotdir, skill, in_home in LAYOUTS:
        with tempfile.TemporaryDirectory() as td:
            home, work = stage(td, dotdir, skill, in_home)
            env = dict(os.environ)
            env.pop("QWEN_SKILL_ROOT", None)
            env["HOME"] = str(home)
            bad = []
            for c in cmds:
                p = subprocess.run(["bash", "-c", c], cwd=str(work), env=env,
                                   capture_output=True, text=True)
                err = (p.stderr or "")
                if ("No such file or directory" in err
                        or "can't open file" in err
                        or "Traceback" in err):
                    bad.append((c.split()[1][:60], err.strip().splitlines()[-1][:120]))
            check("every documented command resolves under %s/skills/%s%s"
                  % (dotdir, skill, " in HOME" if in_home else ""),
                  not bad, "; ".join("%s -> %s" % b for b in bad[:3]))

            p = subprocess.run(["bash", "-c", hook], cwd=str(work), env=env,
                               capture_output=True, text=True, input="{}")
            check("the Stop hook resolves under %s/skills/%s" % (dotdir, skill),
                  "No such file" not in (p.stderr or "")
                  and "Traceback" not in (p.stderr or ""),
                  (p.stderr or "")[:200])

    # The runner's own variable still wins when it is set.
    with tempfile.TemporaryDirectory() as td:
        home, work = stage(td, ".qwen", "log-rca", False)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["QWEN_SKILL_ROOT"] = str(work / ".qwen" / "skills" / "log-rca")
        p = subprocess.run(["bash", "-c", hook], cwd=str(work), env=env,
                           capture_output=True, text=True, input="{}")
        check("QWEN_SKILL_ROOT still works when the runner sets it",
              "No such file" not in (p.stderr or ""), (p.stderr or "")[:200])

    print()
    if FAILED:
        print("✗ v32 paths: %d проверок упало" % len(FAILED))
        return 1
    print("✓ v32 paths: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
