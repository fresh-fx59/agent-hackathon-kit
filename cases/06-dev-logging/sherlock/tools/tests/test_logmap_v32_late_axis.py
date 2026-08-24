#!/usr/bin/env python3
"""v32 P5: an executable that arrives late in the CORPUS window earns a row.

Axis 5 asks "who entered this file late?" and therefore says nothing about a
tool installed once: inside its own file that record is not late, it is the only
one. Measured on the winevtx corpus, axis 5 emitted zero rows while `3proxy`
appeared in three files, all of them in the last third of the window.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
TOOLS = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v32" / "tools"
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def load():
    spec = importlib.util.spec_from_file_location("logmap_v32_late", str(TOOLS / "logmap.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LOG = load()


def corpus(root, late_at_day=9, tool="3proxy.exe", files=2):
    """Two channels over ten days; the tool shows up on day `late_at_day`."""
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    for ch in range(2):
        rows = []
        for day in range(1, 11):
            for k in range(12):
                rows.append("2021-05-%02dT%02d:00:00Z channel=ch%d event=routine "
                            "image=C:\\WINDOWS\\System32\\svchost.exe id=%d"
                            % (day, k, ch, k))
            if day == late_at_day and ch < files:
                rows.append("2021-05-%02dT13:00:00Z channel=ch%d event=install "
                            "image=C:\\tools\\%s id=99" % (day, ch, tool))
        (d / ("channel%d.log" % ch)).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


def run(corpus_dir, out):
    p = subprocess.run([sys.executable, str(TOOLS / "logmap.py"), corpus_dir,
                        "--out", out, "--jobs", "1"], capture_output=True, text=True)
    wl = Path(out) / "worklist.tsv"
    return p, wl.read_text(encoding="utf-8") if wl.exists() else ""


def main():
    with tempfile.TemporaryDirectory() as td:
        c = os.path.join(td, "corpus"); out = os.path.join(td, "work")
        corpus(c)
        p, wl = run(c, out)
        check("logmap still exits 0", p.returncode == 0, p.stderr[-300:])
        late = [l for l in wl.splitlines() if "\tlate\t" in l]
        check("the late executable earns its own row", len(late) >= 1, wl[-600:])
        if late:
            check("the row names the token and cites a real line",
                  "3proxy.exe" in late[0] and ":" in late[0].split("\t")[3], late[0])
            check("the row is not attributed to the platform binary",
                  "svchost" not in late[0].split("ПОЗДНИЙ ОБЪЕКТ")[-1][:40], late[0])
        check("the legend explains the axis when it fires",
              "ось «поздний»" in wl, "legend missing")

    # Present in only ONE file: that file's own four axes own it, not this one.
    with tempfile.TemporaryDirectory() as td:
        c = os.path.join(td, "corpus"); out = os.path.join(td, "work")
        corpus(c, files=1)
        p, wl = run(c, out)
        check("a token in a single file does not fire the axis",
              not [l for l in wl.splitlines() if "\tlate\t" in l], "fired on one file")

    # Present from the beginning: not late, whatever else it is.
    with tempfile.TemporaryDirectory() as td:
        c = os.path.join(td, "corpus"); out = os.path.join(td, "work")
        corpus(c, late_at_day=1)
        p, wl = run(c, out)
        check("a token present from the start does not fire the axis",
              not [l for l in wl.splitlines() if "\tlate\t" in l], "fired on an early token")

    print()
    if FAILED:
        print("✗ logmap late axis: %d проверок упало" % len(FAILED))
        return 1
    print("✓ logmap late axis: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
