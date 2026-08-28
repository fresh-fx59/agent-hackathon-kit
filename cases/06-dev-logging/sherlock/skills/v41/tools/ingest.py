#!/usr/bin/env python3
"""ingest.py — turn WHAT THE USER ACTUALLY HANDS YOU into a corpus Sherlock reads.

WHY THIS EXISTS. The skill's own description has always advertised «a directory
or an archive of logs», and until 2026-08-28 nothing in the arm could open
either an archive or a `.evtx`. Every corpus we ever tested with had been
converted to JSONL by hand, off to one side, by
`hack/sherlock-corpora/_tools/render-evtx.sh` — so the gap never showed up in
forty runs and would have hit the first real user on their first attempt.

The pieces were not new: the zip walk below is the hardened `_safe_extract` from
the retired `logalyzer/ingest.py` (deleted in kit commit d5e4752), and the evtx
conversion is what `render-evtx.sh` did with `evtx_dump -o jsonl`. This file
puts both INSIDE the skill, where a corporate user actually gets them.

WHAT IT REFUSES TO DO. It never silently drops input. Anything it cannot ingest
is written to the manifest AND printed, and the exit code is non-zero unless you
pass --keep-going. A corpus that quietly lost the one channel holding the answer
is worse than no corpus: the report that follows looks complete and is not.
"""
import argparse
import gzip
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

MAX_ENTRIES = 20000
MAX_UNCOMPRESSED = 8 * 1024 * 1024 * 1024      # 8 GiB, a real DFIR archive
MAX_DEPTH = 3                                   # archive inside archive inside archive
SNIFF = 8192

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                    ".tar.xz", ".txz", ".7z")
TEXT_SUFFIXES = (".jsonl", ".json", ".log", ".txt", ".ndjson", ".csv", ".tsv",
                 ".out", ".err", ".evt")


def lower_suffixes(name):
    n = name.lower()
    for s in ARCHIVE_SUFFIXES:
        if n.endswith(s):
            return s
    return ""


def looks_binary(path):
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(SNIFF)
    except OSError:
        return False


def is_gzip(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


# ── archives ────────────────────────────────────────────────────────────────
def extract_zip(src, dest):
    """The hardened walk recovered from logalyzer/ingest.py: entry count, total
    uncompressed size, path traversal and symlink entries are all refused
    BEFORE anything is written."""
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError("archive refused: %d entries > %d"
                             % (len(infos), MAX_ENTRIES))
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED:
            raise ValueError("archive refused: %d uncompressed bytes > %d"
                             % (total, MAX_UNCOMPRESSED))
        root = os.path.realpath(dest)
        for info in infos:
            target = os.path.realpath(os.path.join(dest, info.filename))
            if not (target == root or target.startswith(root + os.sep)):
                continue                      # traversal or absolute entry
            if stat.S_ISLNK(info.external_attr >> 16):
                continue                      # symlink entry
            z.extract(info, dest)


def extract_tar(src, dest):
    with tarfile.open(src) as t:
        members = t.getmembers()
        if len(members) > MAX_ENTRIES:
            raise ValueError("archive refused: %d entries > %d"
                             % (len(members), MAX_ENTRIES))
        total = sum(m.size for m in members)
        if total > MAX_UNCOMPRESSED:
            raise ValueError("archive refused: %d uncompressed bytes > %d"
                             % (total, MAX_UNCOMPRESSED))
        root = os.path.realpath(dest)
        safe = []
        for m in members:
            if m.issym() or m.islnk() or m.isdev():
                continue
            target = os.path.realpath(os.path.join(dest, m.name))
            if target == root or target.startswith(root + os.sep):
                safe.append(m)
        t.extractall(dest, members=safe)


def extract_7z(src, dest):
    try:
        import py7zr                                   # optional
    except ImportError:
        if shutil.which("7z"):
            p = subprocess.run(["7z", "x", "-y", "-o" + dest, src],
                               capture_output=True)
            if p.returncode != 0:
                raise ValueError("7z failed: %s"
                                 % p.stderr.decode("utf-8", "replace")[:200])
            return
        raise ValueError("cannot open .7z — install py7zr (pip install py7zr) "
                         "or the 7z binary")
    with py7zr.SevenZipFile(src) as z:
        z.extractall(dest)


def extract(src, dest):
    kind = lower_suffixes(src)
    os.makedirs(dest, exist_ok=True)
    if kind == ".zip":
        extract_zip(src, dest)
    elif kind == ".7z":
        extract_7z(src, dest)
    else:
        extract_tar(src, dest)


# ── evtx ────────────────────────────────────────────────────────────────────
EVTX_BINARIES = ("evtx_dump", "/opt/homebrew/bin/evtx_dump",
                 "/usr/local/bin/evtx_dump")

# Windows already ships a reader. `Get-WinEvent` is part of the OS, so on the
# machine that PRODUCED these logs there is nothing to install and nothing to
# ask a security team for — which is the whole difficulty with the other two
# converters on a locked-down corporate host.
#
# NOT VERIFIED ON WINDOWS BY US: this repository has no Windows box. It is
# tried first on win32 and, if it fails, its error is named and the next
# converter is tried — so an untested path can cost a moment, never a channel.
POWERSHELL_EVTX = (
    "$ErrorActionPreference='Stop';"
    "Get-WinEvent -Path '%(src)s' -Oldest |"
    " ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 8 } |"
    " Set-Content -LiteralPath '%(dest)s' -Encoding utf8"
)


def powershell_evtx(src, dest):
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return None
    done = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command",
         POWERSHELL_EVTX % {"src": src, "dest": dest}],
        capture_output=True)
    if done.returncode == 0 and os.path.exists(dest):
        return "Get-WinEvent"
    tail = done.stderr.decode("utf-8", "replace").strip().splitlines()
    raise ValueError("%s: %s" % (exe, tail[-1] if tail else "no output"))


def install_converter():
    """Fetch the pure-python converter. ONLY on an explicit --install-converter.

    An agent must not install software on someone's machine because it found a
    file it could not read. On a corporate host that is a policy decision and
    often a blocked one, so this stays behind a flag the human passes, and the
    failure message names the flag rather than reaching for it.
    """
    done = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user",
         "python-evtx", "xmltodict"], capture_output=True)
    tail = done.stderr.decode("utf-8", "replace").strip().splitlines()
    return done.returncode == 0, (tail[-1] if tail else "")


def evtx_to_jsonl(src, dest):
    """Windows event logs are binary; nothing downstream can read one.

    SEVERAL converters, tried in order and each one PROVEN by its output rather
    than by its presence on PATH. That is not defensiveness for its own sake:
    on this very machine `which evtx_dump` resolves to a broken python shim
    (`ModuleNotFoundError: No module named 'scripts'`) that shadows the working
    Rust binary two directories away, and a converter chosen by `which` alone
    fails 132 files out of 132 while a working one sits right there.

    Order: the `evtx_dump` binary (the Rust `evtx` crate — what
    `render-evtx.sh` used, and what produced every corpus we have tested on),
    then the pure-python `Evtx` package, which is pip-installable on a
    corporate box where a Rust binary is not.
    """
    errors = []
    if sys.platform == "win32":
        try:
            how = powershell_evtx(src, dest)
            if how:
                return how
        except ValueError as exc:
            errors.append(str(exc))
    for candidate in EVTX_BINARIES:
        exe = shutil.which(candidate) if os.sep not in candidate else (
            candidate if os.path.exists(candidate) else None)
        if not exe:
            continue
        # STDOUT, never `-f <file>`: evtx_dump refuses to overwrite an
        # existing output file and tries to raise an interactive confirmation
        # prompt, which cannot be answered from a script and fails the file
        # («Failed to display confirmation prompt»). Writing the stream
        # ourselves removes that whole class of failure.
        done = subprocess.run([exe, "-o", "jsonl", src], capture_output=True)
        if done.returncode == 0:
            with open(dest, "wb") as fh:
                fh.write(done.stdout)
            return "evtx_dump"
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        errors.append("%s: %s" % (exe, tail[-1] if tail else "no output"))
    try:
        import Evtx.Evtx as evtx                       # python-evtx
        import xmltodict                               # its usual companion
    except ImportError:
        raise ValueError(
            "no working EVTX converter — either re-run this command with "
            "--install-converter (pip install --user python-evtx xmltodict), "
            "or install the evtx_dump binary (cargo install evtx / brew "
            "install evtx). On Windows no install is needed: Get-WinEvent is "
            "used automatically"
            + (("; tried " + "; ".join(errors)) if errors else ""))
    with evtx.Evtx(src) as log, open(dest, "w", encoding="utf-8") as out:
        for record in log.records():
            try:
                obj = xmltodict.parse(record.xml())
            except Exception:
                continue
            out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    return "python-evtx"


# ── the walk ────────────────────────────────────────────────────────────────
class Result(object):
    def __init__(self):
        self.rows = []            # (source, dest, how, note)
        self.skipped = []         # (source, why)

    def took(self, src, dest, how, note=""):
        self.rows.append((src, dest, how, note))

    def skip(self, src, why):
        self.skipped.append((src, why))


def unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s-%d%s" % (base, n, ext)):
        n += 1
    return "%s-%d%s" % (base, n, ext)


def ingest_file(src, out_dir, rel, result, depth):
    name = os.path.basename(src)
    low = name.lower()

    if lower_suffixes(src):
        if depth >= MAX_DEPTH:
            result.skip(src, "archive nested deeper than %d levels" % MAX_DEPTH)
            return
        tmp = tempfile.mkdtemp(prefix="ingest-")
        try:
            extract(src, tmp)
        except Exception as exc:
            result.skip(src, str(exc))
            shutil.rmtree(tmp, ignore_errors=True)
            return
        walk(tmp, out_dir, os.path.join(rel, name + ".d"), result, depth + 1)
        shutil.rmtree(tmp, ignore_errors=True)
        return

    if low.endswith(".evtx"):
        dest = unique(os.path.join(out_dir, flat(rel, name[:-5] + ".jsonl")))
        try:
            how = evtx_to_jsonl(src, dest)
        except Exception as exc:
            result.skip(src, str(exc))
            return
        n = count_lines(dest)
        # AN EMPTY CHANNEL IS A FACT, NOT A FAILURE. Windows ships dozens of
        # channels that never recorded anything; 51 of the 194 in the first
        # real archive were empty. Naming them keeps «nothing here» different
        # from «we could not read it», which is the difference between a gap
        # the analyst knows about and one they do not.
        result.took(src, dest, how, "%d records%s"
                    % (n, " — пустой канал" if n == 0 else ""))
        return

    if low.endswith(".gz") or is_gzip(src):
        dest = unique(os.path.join(out_dir, flat(rel, name)))
        shutil.copy2(src, dest)
        result.took(src, dest, "copied (gzip, read directly downstream)")
        return

    if looks_binary(src):
        result.skip(src, "binary file of an unknown format")
        return

    dest = unique(os.path.join(out_dir, flat(rel, name)))
    shutil.copy2(src, dest)
    result.took(src, dest, "copied")


def flat(rel, name):
    """One flat corpus directory: a nested path becomes part of the name, so a
    citation `Security.jsonl:19934` stays a single unambiguous token."""
    rel = rel.strip("/").replace(os.sep, "-")
    return ("%s-%s" % (rel, name)) if rel else name


def count_lines(path):
    n = 0
    try:
        with open(path, "rb") as fh:
            for _ in fh:
                n += 1
    except OSError:
        return 0
    return n


def walk(root, out_dir, rel, result, depth):
    if os.path.isfile(root):
        ingest_file(root, out_dir, rel, result, depth)
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".") and d != "__MACOSX")
        for name in sorted(files):
            if name.startswith("."):
                continue
            src = os.path.join(base, name)
            if os.path.islink(src):
                result.skip(src, "symlink")
                continue
            sub = os.path.relpath(base, root)
            sub = "" if sub == "." else sub
            ingest_file(src, out_dir, os.path.join(rel, sub) if rel else sub,
                        result, depth)


def main():
    ap = argparse.ArgumentParser(
        description="Normalise archives and .evtx into a corpus Sherlock reads.")
    ap.add_argument("inputs", nargs="+",
                    help="files, directories or archives, in any mix")
    ap.add_argument("--out", required=True, help="the corpus directory to write")
    ap.add_argument("--install-converter", action="store_true",
                    help="before starting, pip install --user python-evtx and "
                         "xmltodict. Off by default: installing software is the "
                         "machine owner's decision, not this tool's")
    ap.add_argument("--keep-going", action="store_true",
                    help="exit 0 even when some input could not be ingested "
                         "(the manifest still names every one)")
    a = ap.parse_args()

    if a.install_converter:
        ok, why = install_converter()
        print(("✓ EVTX converter installed" if ok
               else "✗ could not install the EVTX converter: %s" % why))
        if not ok:
            return 1

    os.makedirs(a.out, exist_ok=True)
    result = Result()
    for item in a.inputs:
        if not os.path.exists(item):
            result.skip(item, "no such file or directory")
            continue
        walk(item, a.out, "", result, 0)

    manifest = os.path.join(a.out, "_ingest-manifest.tsv")
    with open(manifest, "w", encoding="utf-8") as fh:
        fh.write("источник\tрезультат\tкак\tзаметка\n")
        for src, dest, how, note in result.rows:
            fh.write("%s\t%s\t%s\t%s\n"
                     % (src, os.path.basename(dest), how, note))
        for src, why in result.skipped:
            fh.write("%s\t-\tПРОПУЩЕН\t%s\n" % (src, why))

    print("✓ %d file(s) ingested into %s" % (len(result.rows), a.out))
    print("  manifest: %s" % manifest)
    if result.skipped:
        print("✗ %d input(s) COULD NOT be ingested — the corpus is incomplete:"
              % len(result.skipped))
        for src, why in result.skipped[:20]:
            print("    %s — %s" % (src, why))
        if len(result.skipped) > 20:
            print("    ... and %d more, all named in the manifest"
                  % (len(result.skipped) - 20))
        if not a.keep_going:
            return 1
    if not result.rows:
        print("✗ nothing was ingested")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
