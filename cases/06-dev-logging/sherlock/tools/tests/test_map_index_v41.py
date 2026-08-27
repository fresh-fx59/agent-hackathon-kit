#!/usr/bin/env python3
"""FIX 4b: a map INDEX, because 24.8 % of the map is derivation debug.

MEASURED on the paid run: `work/map.txt` is 125,882 bytes over 143 per-file
blocks, and 31,199 of them (24.8 %) are material the triage step never acts on —
axis value-distributions 18,692 B, rejected-numeric-slot diagnostics 9,825 B,
rare-value listings 2,682 B. The child paginated it at six offsets, each landing
on the 25,000-character truncation cap.

What triage actually needs per file is five short fields. `map-index.tsv` carries
exactly those, one line per file, and says where to go for the rest — so the map
becomes a thing you consult about ONE file instead of a thing you read.

`map.txt` itself is unchanged: the draft stage and a human still get the full map.
"""
import io
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
LOGMAP = os.path.join(SKILLS, "v41", "tools", "logmap.py")
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    root = tempfile.mkdtemp(prefix="mapidx-")
    corpus = os.path.join(root, "corpus")
    out = os.path.join(root, "work")
    os.makedirs(corpus)
    for n in range(6):
        with io.open(os.path.join(corpus, "svc%d.log" % n), "w",
                     encoding="utf-8") as fh:
            for i in range(300):
                fh.write(u"2036-02-03T04:%02d:%02dZ svc=api level=INFO "
                         u"msg=request id=%d code=200\n" % (i // 6, i % 60, i))
            fh.write(u"2036-02-03T05:00:00Z svc=api level=ERROR msg=boom "
                     u"id=999 code=500\n")
    p = subprocess.run([sys.executable, LOGMAP, corpus, "--out", out],
                       capture_output=True, text=True,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    check("logmap still exits 0", p.returncode == 0, p.stderr[-400:])
    mp = os.path.join(out, "map.txt")
    ix = os.path.join(out, "map-index.tsv")
    check("map.txt is still written — the draft stage and a human need it",
          os.path.exists(mp))
    check("map-index.tsv is written", os.path.exists(ix),
          sorted(os.listdir(out))[:12])
    if not os.path.exists(ix):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1
    text = io.open(ix, encoding="utf-8").read()
    body = [l for l in text.splitlines() if l and not l.startswith("#")]
    check("one line per corpus file", len(body) == 6, len(body))
    check("every line names the path", all(".log" in l for l in body), body[:1])
    check("every line carries the same column count",
          len(set(len(l.split("\t")) for l in body)) == 1,
          [len(l.split("\t")) for l in body])
    check("it carries the record count and the framing a triage row needs",
          all(len(l.split("\t")) >= 5 for l in body), body[:1])
    check("the index is far smaller than the map",
          len(text.encode()) * 3 < len(io.open(mp, encoding="utf-8").read().encode()),
          "index %d vs map %d" % (len(text.encode()),
                                  len(io.open(mp, encoding="utf-8").read().encode())))
    check("the header points at map.txt for the detail, so nothing is hidden",
          "map.txt" in text, text[:200])
    check("v40 does not write it — the fix lands in v41 only",
          "map-index.tsv" not in io.open(
              os.path.join(SKILLS, "v40", "tools", "logmap.py"),
              encoding="utf-8").read())

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the map is an index first")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
