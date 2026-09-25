"""Load-time data index for citecheck (v53, spec item 1). Stdlib only.

Productionized from the 2026-09-24 prototype (vault
docs/run-reports/artifacts/2026-09-24-index-prototype/ccindex.py +
citecheck_idx.py; 0 differences on r5, 1 GB and 288 fuzz cases).

Build (never inside the Stop hook): tools/buildindex.py.
Query: citecheck.py calls attach_for(...) which, for a FRESH index, replaces
its 7 corpus readers with index lookups. Anything the index cannot answer
exactly falls back to the original reader, so answers never change.

Layout of <index_root>/<data_sha256>/ (every file 0444):
  manifest.json      schema, checker version, code sha256, root, data sha256,
                     walk (every file: rel, size, mtime_ns, sha256), files, build stats
  f<id>.off          packed little-endian uint64 line-start offsets + EOF sentinel
  f<id>.meta.pkl     per-file counts, bitmaps of JSON / dict rows, path ids,
                     container paths, coverage facts
  f<id>.p<pid>.pkl   one leaf field path: exists / nonnull bitmaps +
                     value -> posting (int bitmap when dense, array('I') when sparse)
  first.pkl          first-appearance facts (walk replay + per-candidate hits)
  whole.pkl          report-independent whole-corpus answers (rollover, logon reason)

Chunked build: postings are held in memory only up to a cap (bytes estimated
per appended row id / new value), then spilled per path to a temp file and
merged path by path at the end. Merge preserves first-appearance order, so a
chunked build writes the same bytes as a single-pass build.
"""
import array
import copy
import hashlib
import importlib.util
import json
import os
import pickle
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heartbeat as HB  # noqa: E402

SCHEMA = 2
CHECKER_VERSION = "sherlock-v53"
INDEX_DIRNAME = "index"
INDEX_ROOT_ENV = "SHERLOCK_INDEX_ROOT"
REQUIRE_ENV = "SHERLOCK_REQUIRE_INDEX"      # "1" -> missing/stale index is an error
STRICT_ENV = "SHERLOCK_STRICT_INDEX"        # "1" -> freshness also re-hashes every file (gate mode)
LIFT_CAPS_ENV = "SHERLOCK_LIFT_SCAN_CAPS"   # "1" -> lift 512 MB / 5 M-line caps (index path only)
STOP_HOOK_ENVS = ("SHERLOCK_FINALIZE_STOP_RUNNING", "SHERLOCK_IN_STOP_HOOK")
STALE_MESSAGE = "data index missing or stale — run `tools/buildindex.py`"
STALE_RC = 3
# Spec v4.1 [SD]: build peak 207 MB at 1 GB x 2 -> 512 MB (power of two).
INDEX_BUILD_MEM_CAP_MB = 512
# Spec v4.1 [SD]: 0.47 KB/row -> 250,000 rows ~ 118 MB < cap/2; spill + beat per chunk.
INDEX_BUILD_CHUNK_ROWS = 250000
TOOLS = os.path.dirname(os.path.abspath(__file__))
CODE_FILES = ("ccindex.py", "citecheck.py", "rollover.py")
NAME_MAX = 60
TERM_ASCII = set("\\ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
# memory estimate (bytes) used for the spill trigger
EST_ROW = 4          # one uint32 row id in an array('I')
EST_KEY = 120        # a new value key: str + dict slot + empty array
EST_PATH = 400       # a new path: 3 containers


# ---------------------------------------------------------------- helpers
def _s_ok(c):
    return c.isalnum() or c in "._-"


def name_ok(n):
    return isinstance(n, str) and 0 < len(n) <= NAME_MAX and n == n.lower() \
        and all(_s_ok(c) for c in n)


GEN1 = re.compile(r'(?=("(?:TargetUserName|SubjectUserName|AccountName|UserName|LogonUser|User)"'
                  r'\s*:\s*"([^"]*)"))', re.IGNORECASE)
GEN2 = re.compile(r'(?<![A-Za-z0-9_:\\])(?=[A-Za-z][A-Za-z0-9._-]{1,30}\\{1,4}(.{0,%d}))'
                  % (NAME_MAX + 1), re.IGNORECASE)
GEN3 = re.compile(r'(?<![A-Za-z0-9_])(?=Users\\{1,2}(.{0,%d}))' % (NAME_MAX + 1), re.IGNORECASE)
XRUN = re.compile(r"^[A-Za-z0-9._-]{1,32}$", re.IGNORECASE)


def _tail_cands(t, out):
    for k in range(1, min(len(t), NAME_MAX) + 1):
        if not _s_ok(t[k - 1]):
            break
        if k == len(t) or t[k] not in TERM_ASCII:
            out.add(t[:k].lower())


def candidates(line):
    """Every account-name candidate the three ACCOUNT_CTX contexts can match."""
    out = set()

    def add(c):
        if 0 < len(c) <= NAME_MAX and all(_s_ok(x) for x in c):
            out.add(c.lower())
    if '"' in line:
        for m in GEN1.finditer(line):
            v = m.group(2)
            add(v)
            i = v.find("\\")
            if i >= 1 and XRUN.match(v[:i]):
                rest = v[i + 1:]
                add(rest)
                if rest.startswith("\\"):
                    add(rest[1:])
    if "\\" in line:
        for m in GEN2.finditer(line):
            _tail_cands(m.group(1), out)
        if "users" in line.lower():
            for m in GEN3.finditer(line):
                _tail_cands(m.group(1), out)
    return out


def load_checker(path=None):
    path = path or os.path.join(TOOLS, "citecheck.py")
    mod = sys.modules.get("citecheck")
    if mod is not None and os.path.abspath(getattr(mod, "__file__", "")) == os.path.abspath(path):
        return mod
    spec = importlib.util.spec_from_file_location("citecheck", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["citecheck"] = mod
    spec.loader.exec_module(mod)
    return mod


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def code_sha256():
    h = hashlib.sha256()
    for name in CODE_FILES:
        h.update(name.encode() + b"\0")
        h.update(sha256_file(os.path.join(TOOLS, name)).encode() + b"\n")
    return h.hexdigest()


def rows_to_bitmap(rows, n):
    ba = bytearray((n + 7) >> 3)
    for r in rows:
        ba[r >> 3] |= 1 << (r & 7)
    return int.from_bytes(ba, "little")


def _setbit(ba, i):
    j = i >> 3
    if j >= len(ba):
        ba.extend(bytes(max(j + 1 - len(ba), len(ba))))
    ba[j] |= 1 << (i & 7)


def flatten(rec, prefix, leaves, containers):
    for k, v in rec.items():
        if "." in k:
            continue               # unreachable by _agg_get's split(".")
        p = prefix + k
        if isinstance(v, dict):
            containers.add(p)
            flatten(v, p + ".", leaves, containers)
        else:
            leaves.append((p, v))


def walk(root):
    """The checker's own walk order (corpus_first_appearance / logon scan)."""
    for dirpath, _dirs, fns in sorted(os.walk(root)):
        for fn in sorted(fns):
            full = os.path.join(dirpath, fn)
            yield full, os.path.relpath(full, root).replace(os.sep, "/")


def walk_stats(root):
    out = []
    for full, rel in walk(root):
        try:
            st = os.stat(full)
            out.append([rel, st.st_size, st.st_mtime_ns])
        except OSError:
            out.append([rel, None, None])
    return out


def profile_key(prof):
    return json.dumps({k: v for k, v in prof.items() if not k.startswith("_")},
                      sort_keys=True, default=str)


def _write(path, data):
    with open(path, "wb") as fh:
        pickle.dump(data, fh, 4)


# ---------------------------------------------------------------- build
class _Postings:
    """Per-file posting accumulator with a memory cap and per-path spill."""

    def __init__(self, spill_dir, cap_bytes):
        self.spill_dir = spill_dir
        self.cap = cap_bytes
        self.paths = {}            # path -> [exists rows, null rows, {value: rows}]
        self.slot = {}             # path -> spill slot (stable for the file)
        self.est = 0
        self.spills = 0
        self.peak_est = 0

    def add(self, p, i, s):
        e = self.paths.get(p)
        if e is None:
            e = self.paths[p] = [array.array("I"), array.array("I"), {}]
            self.est += EST_PATH
            if p not in self.slot:
                self.slot[p] = len(self.slot)
        e[0].append(i)
        self.est += EST_ROW
        if s is None:
            e[1].append(i)
            self.est += EST_ROW
        else:
            d = e[2]
            a = d.get(s)
            if a is None:
                a = d[s] = array.array("I")
                self.est += EST_KEY + len(s)
            a.append(i)
            self.est += EST_ROW
        if self.est > self.peak_est:
            self.peak_est = self.est

    def maybe_spill(self):
        if self.est > self.cap:
            self.spill()

    def spill(self):
        if not self.paths:
            return
        os.makedirs(self.spill_dir, exist_ok=True)
        for p, e in self.paths.items():
            with open(os.path.join(self.spill_dir, "s%d.spl" % self.slot[p]), "ab") as fh:
                pickle.dump(e, fh, 4)
        self.paths = {}
        self.est = 0
        self.spills += 1

    def merged(self):
        """Yield (path, exists rows, null rows, {value: rows}) in sorted path order."""
        if not self.spills:
            for p in sorted(self.paths):
                ex, nul, vals = self.paths[p]
                yield p, ex, nul, vals
            return
        self.spill()
        for p in sorted(self.slot):
            ex, nul, vals = array.array("I"), array.array("I"), {}
            with open(os.path.join(self.spill_dir, "s%d.spl" % self.slot[p]), "rb") as fh:
                while True:
                    try:
                        e = pickle.load(fh)
                    except EOFError:
                        break
                    ex.extend(e[0])
                    nul.extend(e[1])
                    for s, rows in e[2].items():
                        a = vals.get(s)
                        if a is None:
                            vals[s] = rows
                        else:
                            a.extend(rows)
            yield p, ex, nul, vals
        shutil.rmtree(self.spill_dir, ignore_errors=True)


def build_file(C, ap, fid, out, quotable, cap_bytes, stats):
    """One streaming pass over one data file. -> (manifest entry, first hits or None)."""
    st = os.stat(ap)
    ent = {"id": fid, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "supported": True}
    h = hashlib.sha256()
    offs = array.array("Q")
    offpath = os.path.join(out, "f%d.off" % fid)
    offh = open(offpath, "wb")
    post = _Postings(os.path.join(out, ".spill-f%d" % fid), cap_bytes)
    containers = set()
    jsonrows, dictrows = bytearray(), bytearray()
    total = blank = unparsed = parsed = 0
    last_q = None
    small = set()
    first_hits = {}
    pos = 0

    def flush_offs():
        if sys.byteorder != "little":
            offs.byteswap()
        offs.tofile(offh)
        del offs[:]

    unsupported = False
    with open(ap, "rb") as fh:
        for i, raw in enumerate(HB.lines(fh, "buildindex")):
            h.update(raw)
            if unsupported:
                continue
            if b"\r" in raw:
                unsupported = True
                continue
            offs.append(pos)
            if len(offs) >= 65536:
                flush_offs()
            pos += len(raw)
            total += 1
            text = raw.decode("utf-8", "replace")
            no = i + 1
            if quotable(text.rstrip("\n")):
                last_q = no
                if no <= C.COVERAGE_SMALL_FILE:
                    small.add(no)
            cs = candidates(text)
            if cs:
                ts = None
                tsd = False
                for c in cs:
                    if not tsd:
                        ts = C._own_line_time(text)
                        tsd = True
                    hh = first_hits.get(c)
                    if hh is None:
                        hh = first_hits[c] = [None, []]
                    if ts is None:
                        hh[1].append(no)
                    elif hh[0] is None or ts < hh[0][0]:
                        hh[0] = (ts, no, text.strip()[:200])
            line = text.strip()
            if not line:
                blank += 1
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                unparsed += 1
                continue
            except Exception:
                unsupported = True             # checker would say unreadable
                continue
            _setbit(jsonrows, i)
            if not isinstance(rec, dict):
                unparsed += 1
                continue
            parsed += 1
            _setbit(dictrows, i)
            leaves = []
            flatten(rec, "", leaves, containers)
            for p, v in leaves:
                post.add(p, i, C._agg_str(v))
            post.maybe_spill()
            if parsed % INDEX_BUILD_CHUNK_ROWS == 0:
                post.spill()
                HB.beat("buildindex", "chunk")
    ent["sha256"] = h.hexdigest()
    if unsupported:
        offh.close()
        os.unlink(offpath)
        shutil.rmtree(post.spill_dir, ignore_errors=True)
        ent["supported"] = False
        return ent, None
    offs.append(pos)
    flush_offs()
    offh.close()
    n = total
    pids = {}
    for pid, (p, ex, nul, vals) in enumerate(post.merged()):
        pids[p] = pid
        exists = rows_to_bitmap(ex, n)
        nonnull = exists & ~rows_to_bitmap(nul, n) if nul else exists
        pm = {}
        for s, rows in vals.items():
            pm[s] = rows_to_bitmap(rows, n) if len(rows) * 32 >= n else rows
        _write(os.path.join(out, "f%d.p%d.pkl" % (fid, pid)),
               {"exists": exists, "nonnull": nonnull, "post": pm})
    meta = {"lines": n, "blank": blank, "unparsed": unparsed, "parsed": parsed,
            "jsonrows": int.from_bytes(bytes(jsonrows), "little"),
            "dictrows": int.from_bytes(bytes(dictrows), "little"),
            "paths": pids, "containers": containers,
            "last_q": last_q, "small": small}
    _write(os.path.join(out, "f%d.meta.pkl" % fid), meta)
    ent["lines"] = n
    stats["spills"] += post.spills
    stats["peak_postings_est_bytes"] = max(stats["peak_postings_est_bytes"], post.peak_est)
    return ent, first_hits


def build(corpus, index_root, mem_cap_mb=INDEX_BUILD_MEM_CAP_MB, checker=None, log=None):
    """Build the index for `corpus` under index_root/<data_sha256>/. -> manifest dict."""
    t0 = time.time()
    C = load_checker(checker)
    root = os.path.abspath(corpus)
    os.makedirs(index_root, exist_ok=True)
    tmp = os.path.join(index_root, ".build-%d" % os.getpid())
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    cap = int(mem_cap_mb * 1024 * 1024)
    quotable = lambda t: C.quote_example({"path": "x", "line": 1, "text": t}) is not None  # noqa: E731
    stats = {"spills": 0, "peak_postings_est_bytes": 0, "mem_cap_mb": mem_cap_mb}
    files, walk_list, first_files, cand = {}, [], [], {}
    fid = 0
    for full, rel in walk(root):
        HB.beat("buildindex", "file")
        st = os.stat(full)
        wl = {"rel": rel, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": None}
        walk_list.append(wl)
        info = {"rel": os.path.relpath(full, root), "size": st.st_size, "probe_err": None,
                "binary": False, "supported": True}
        try:
            with open(full, "rb") as _p:
                _p.read(1)
        except Exception as e:
            info["probe_err"] = "%s: %s" % (type(e).__name__, e)
        if info["probe_err"] is None:
            info["binary"] = C.looks_binary(full)
        order = len(first_files)
        first_files.append(info)
        if info["probe_err"]:
            continue
        if info["binary"] or full.endswith(".gz"):
            wl["sha256"] = sha256_file(full)
            if full.endswith(".gz") and not info["binary"]:
                info["supported"] = False
                files[rel] = {"id": None, "supported": False}
            continue
        ent, hits = build_file(C, full, fid, tmp, quotable, cap, stats)
        wl["sha256"] = ent["sha256"]
        files[rel] = ent
        fid += 1
        if hits is None:
            info["supported"] = False
            continue
        for c, (best, unt) in hits.items():
            cand.setdefault(c, []).append((order, best, unt))
    _write(os.path.join(tmp, "first.pkl"), {"files": first_files, "cand": cand})
    t1 = time.time()
    # Report-independent whole-corpus answers computed by the checker's own
    # code (2 extra passes; not folded into the main pass so answers stay the
    # checker's own, byte for byte).
    HB.beat("buildindex", "step")
    whole = {"rollover": C.load_rollover().scan_corpus(corpus), "logon": {}}
    HB.beat("buildindex", "step")
    try:
        prof = C.load_reason_profile(None, None)
        whole["logon"][profile_key(prof)] = C.logon_reason_scan(corpus, prof)
    except Exception as e:
        whole["logon_error"] = str(e)
    _write(os.path.join(tmp, "whole.pkl"), whole)
    t2 = time.time()
    dh = hashlib.sha256()
    for wl in walk_list:
        dh.update(("%s\t%d\t%s\n" % (wl["rel"], wl["size"], wl["sha256"] or "unreadable")).encode())
    data_sha = dh.hexdigest()
    man = {"schema": SCHEMA, "checker_version": CHECKER_VERSION, "code_sha256": code_sha256(),
           "built_at_root": root, "data_sha256": data_sha, "walk": walk_list, "files": files,
           "build": dict(stats, main_pass_s=round(t1 - t0, 3), whole_scans_s=round(t2 - t1, 3))}
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
    for fn in os.listdir(tmp):
        os.chmod(os.path.join(tmp, fn), 0o444)
    final = os.path.join(index_root, data_sha)
    if os.path.isdir(final):
        _make_writable(final)
        shutil.rmtree(final)
    os.rename(tmp, final)
    os.chmod(final, 0o555)
    man["dir"] = final
    return man


def _make_writable(d):
    os.chmod(d, 0o755)
    for fn in os.listdir(d):
        os.chmod(os.path.join(d, fn), 0o644)


# ---------------------------------------------------------------- locate
def index_roots(corpus, report=None):
    """Candidate index roots, most specific first."""
    out = []
    env = os.environ.get(INDEX_ROOT_ENV)
    if env:
        out.append(os.path.abspath(env))
    if report and report != "-":
        d = os.path.dirname(os.path.abspath(report))
        for _ in range(4):
            out.append(os.path.join(d, INDEX_DIRNAME))
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    out.append(os.path.join(os.path.dirname(os.path.abspath(corpus)), INDEX_DIRNAME))
    seen, uniq = set(), []
    for r in out:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def find_index(corpus, report=None, roots=None, strict=None):
    """-> ("fresh", dir, manifest) | ("stale", dir_or_None, why) | ("missing", None, why).

    An index is identified by CONTENT and corpus-relative paths, never by the
    absolute root it was built at: a container builds for /work/corpus and the
    host gate reads the same bytes at <run>/work/corpus. The caller's `corpus`
    is the root that is walked. Fresh <=> same relative file set, sizes and
    mtime_ns; strict (gate mode) also re-hashes every file against walk[].sha256.
    A manifest that exists but does not match is reported `stale` with its
    reason, never silently skipped into `missing`."""
    if strict is None:
        strict = os.environ.get(STRICT_ENV) == "1"
    stale = None
    code = None
    cur_walk = None
    cur_sha = {}
    for r in (roots if roots is not None else index_roots(corpus, report)):
        if not os.path.isdir(r):
            continue
        for name in sorted(os.listdir(r)):
            d = os.path.join(r, name)
            mp = os.path.join(d, "manifest.json")
            if name.startswith(".") or not os.path.isfile(mp):
                continue
            try:
                with open(mp, encoding="utf-8") as fh:
                    man = json.load(fh)
            except (OSError, ValueError):
                stale = stale or (d, "manifest unreadable")
                continue
            if man.get("schema") != SCHEMA or man.get("checker_version") != CHECKER_VERSION:
                stale = (d, "schema/checker version changed")
                continue
            if code is None:
                code = code_sha256()
            if man.get("code_sha256") != code:
                stale = (d, "checker code changed")
                continue
            if cur_walk is None:
                cur_walk = walk_stats(corpus)
            want = [[w["rel"], w["size"], w["mtime_ns"]] for w in man.get("walk", ())]
            if want != cur_walk:
                stale = (d, "data files changed (size/mtime_ns/set)")
                continue
            if strict and not _content_matches(corpus, man, cur_sha):
                stale = (d, "data content changed (sha256 differs, size/mtime equal)")
                continue
            return "fresh", d, man
    if stale:
        return "stale", stale[0], stale[1]
    return "missing", None, "no index for this corpus"


def _content_matches(corpus, man, cache):
    for w in man.get("walk", ()):
        if w.get("sha256") is None:
            continue
        rel = w["rel"]
        if rel not in cache:
            try:
                cache[rel] = sha256_file(os.path.join(corpus, *rel.split("/")))
            except OSError:
                cache[rel] = None
        if cache[rel] != w["sha256"]:
            return False
    return True


# ---------------------------------------------------------------- query
C = None
IDX = None
MAN = None
ROOT = REALROOT = None
STATS = {"fallback": {}, "hits": {}}
ORIG = {}
READERS = ("agg_evaluate", "agg_locality_scan", "read_lines", "coverage_admissible_lines",
           "corpus_first_appearance", "logon_reason_scan", "load_rollover")
_meta, _path = {}, {}
_first = None
_whole = None


def attach(checker_module, idx_dir, man, lift_caps=False, corpus=None):
    """Install the index-backed readers into the checker module.

    `corpus` is the LIVE corpus root the checker reads (the manifest stores only
    corpus-relative paths; its built_at_root is informational)."""
    global C, IDX, MAN, ROOT, REALROOT, _first, _whole
    C, IDX, MAN = checker_module, idx_dir, man
    ROOT = corpus or man.get("built_at_root") or man.get("root")
    REALROOT = os.path.realpath(ROOT)
    _meta.clear()
    _path.clear()
    _first = _whole = None
    for n in READERS:
        ORIG.setdefault(n, getattr(C, n))
    for n in READERS:
        setattr(C, n, globals()["q_" + n])
    if lift_caps:
        C.MAX_AGG_LINES = 1 << 62
        C.OWN_SCAN_MAX_BYTES = 1 << 62
        C.OWN_SCAN_MAX_FILES = 1 << 62


def _fb(name, why):
    k = name + ":" + why
    STATS["fallback"][k] = STATS["fallback"].get(k, 0) + 1


def _hit(name):
    STATS["hits"][name] = STATS["hits"].get(name, 0) + 1


def _ent(abspath):
    rel = os.path.relpath(os.path.realpath(abspath), REALROOT).replace(os.sep, "/")
    e = MAN["files"].get(rel)
    if not e or not e.get("supported") or e.get("id") is None:
        return rel, None
    try:
        st = os.stat(abspath)
    except OSError:
        return rel, None
    if st.st_size != e["size"] or st.st_mtime_ns != e["mtime_ns"]:
        return rel, None
    return rel, e


def _fresh_all(root):
    return bool(root) and os.path.realpath(root) == REALROOT


def meta(fid):
    m = _meta.get(fid)
    if m is None:
        with open(os.path.join(IDX, "f%d.meta.pkl" % fid), "rb") as fh:
            m = _meta[fid] = pickle.load(fh)
    return m


def col(fid, path):
    key = (fid, path)
    if key not in _path:
        pid = meta(fid)["paths"].get(path)
        if pid is None:
            _path[key] = None
        else:
            with open(os.path.join(IDX, "f%d.p%d.pkl" % (fid, pid)), "rb") as fh:
                _path[key] = pickle.load(fh)
    return _path[key]


def post_or(posts, n):
    acc = 0
    ba = None
    for p in posts:
        if isinstance(p, int):
            acc |= p
        else:
            if ba is None:
                ba = bytearray((n + 7) >> 3)
            for r in p:
                ba[r >> 3] |= 1 << (r & 7)
    if ba is not None:
        acc |= int.from_bytes(ba, "little")
    return acc


def post_and_count(p, mask, mbytes):
    if isinstance(p, int):
        return (p & mask).bit_count()
    return sum(1 for r in p if (mbytes[r >> 3] >> (r & 7)) & 1)


def lowbit(x):
    return (x & -x).bit_length() - 1


def filter_mask(fid, n, f, o, v):
    c = col(fid, f)
    if c is None:
        return 0
    return post_or([p for s, p in c["post"].items() if C._agg_cmp(s, o, v)], n)


def _paths_ok(fid, paths):
    cont = meta(fid)["containers"]
    return not any(p in cont for p in paths)


def _value_counts(fid, n, field, mask, nonempty=True):
    c = col(fid, field)
    if c is None or not mask:
        return {}
    mb = mask.to_bytes((n + 7) >> 3 or 1, "little")
    out = {}
    for s, p in c["post"].items():
        if nonempty and s == "":
            continue
        k = post_and_count(p, mask, mb)
        if k:
            out[s] = k
    return out


def q_agg_evaluate(abspath, pred):
    rel, e = _ent(abspath)
    if e is None:
        _fb("agg_evaluate", "no-index")
        return ORIG["agg_evaluate"](abspath, pred)
    if pred["mode"] != "json":
        _fb("agg_evaluate", "line-mode")
        return ORIG["agg_evaluate"](abspath, pred)
    fid = e["id"]
    m = meta(fid)
    fields = ([pred["field"]] if pred["field"] else []) + [f for f, _o, _v in pred["filters"]]
    if not _paths_ok(fid, fields):
        _fb("agg_evaluate", "container-path")
        return ORIG["agg_evaluate"](abspath, pred)
    _hit("agg_evaluate")
    n = m["lines"]
    if n > C.MAX_AGG_LINES:
        return ("unreadable", None, "файл длиннее %d строк" % C.MAX_AGG_LINES)
    parsed, unparsed = m["parsed"], m["unparsed"]
    seen_field = dict((f, False) for f in fields)
    F = m["dictrows"]
    present = m["dictrows"]
    for f, o, v in pred["filters"]:
        c = col(fid, f)
        nn = c["nonnull"] if c else 0
        if nn & m["dictrows"]:
            seen_field[f] = True
        present &= nn
        F &= filter_mask(fid, n, f, o, v)
    values = {}
    if pred["field"]:
        values = _value_counts(fid, n, pred["field"], F)
        if values:
            seen_field[pred["field"]] = True
        matched = sum(values.values())
    else:
        matched = F.bit_count()
    present = present.bit_count()
    if parsed == 0:
        return ("not-tabular", None,
                "в файле нет ни одной JSON-записи (%d строк не разобрано); "
                "агрегат по полям JSON требует JSONL" % unparsed)
    if unparsed > parsed:
        return ("not-tabular", None, "разобрано %d записей, не разобрано %d — это не JSONL"
                % (parsed, unparsed))
    for f, was in seen_field.items():
        if not was:
            return ("unknown-field", None, "поля «%s» нет ни в одной записи файла" % f)
    if pred["kind"] == "count":
        actual, population = matched, present
    elif pred["kind"] == "distinct":
        actual, population = len(values), parsed
    else:
        actual = sum(1 for v in values.values() if v > pred["threshold"])
        population = len(values)
    if actual == 0:
        return "zero-match", 0, "предикат не совпал ни с чем"
    if population and actual == population:
        return ("too-broad", actual,
                "предикат совпал со ВСЕЙ популяцией (%d из %d) — "
                "утверждение «все записи» ничего не доказывает" % (actual, population))
    return "ok", actual, ""


def q_agg_locality_scan(abspath, pred, cls):
    if pred["mode"] != "json":
        return None
    rel, e = _ent(abspath)
    if e is None:
        _fb("agg_locality_scan", "no-index")
        return ORIG["agg_locality_scan"](abspath, pred, cls)
    fid = e["id"]
    m = meta(fid)
    allp = [f for f, _o, _v in pred["filters"]] + list(cls["fields"]) + \
        ([pred["field"]] if pred["field"] else [])
    if not _paths_ok(fid, allp):
        _fb("agg_locality_scan", "container-path")
        return ORIG["agg_locality_scan"](abspath, pred, cls)
    _hit("agg_locality_scan")
    n = m["lines"]
    M = m["jsonrows"] if not pred["filters"] else m["dictrows"]
    for f, o, v in pred["filters"]:
        M &= filter_mask(fid, n, f, o, v)
    best, field = None, None
    for cand in cls["fields"]:
        c = col(fid, cand)
        x = M & c["exists"] if c else 0
        if x:
            lb = lowbit(x)
            if best is None or lb < best:
                best = lb
    if best is None:
        return None
    for cand in cls["fields"]:
        c = col(fid, cand)
        if c and (c["exists"] >> best) & 1:
            field = cand
            break
    fc = col(fid, field)
    present = (M & fc["exists"]).bit_count()
    if present == 0:
        return None
    matched = M.bit_count()
    L = M & ~fc["nonnull"]
    local_values = set()
    if L:
        local_values.add("")
    lposts = []
    mb = M.to_bytes((n + 7) >> 3 or 1, "little")
    for s, p in fc["post"].items():
        if C._scope_is_local(s, cls):
            if post_and_count(p, M, mb):
                local_values.add(s)
                lposts.append(p)
    L |= M & post_or(lposts, n)
    local = L.bit_count()
    if pred["kind"] == "count":
        honest = matched - local
    else:
        vals = _value_counts(fid, n, pred["field"], M & ~L)
        honest = len(vals) if pred["kind"] == "distinct" else \
            sum(1 for v in vals.values() if v > pred["threshold"])
    return {"field": field, "matched": matched, "local": local,
            "local_values": sorted(local_values), "honest": honest}


def _read_line_bytes(fid, lineno):
    with open(os.path.join(IDX, "f%d.off" % fid), "rb") as fh:
        fh.seek(8 * (lineno - 1))
        a = array.array("Q")
        a.frombytes(fh.read(16))
        if sys.byteorder != "little":
            a.byteswap()
    return a[0], a[1]


def _lines(abspath, fid, nos):
    out = {}
    with open(abspath, "rb") as fh:
        for no in sorted(nos):
            s, t = _read_line_bytes(fid, no)
            fh.seek(s)
            out[no] = fh.read(t - s).decode("utf-8", "replace")
    return out


def q_read_lines(path, wanted):
    if not wanted:
        return {}, None
    rel, e = _ent(path)
    if e is None:
        _fb("read_lines", "no-index")
        return ORIG["read_lines"](path, wanted)
    _hit("read_lines")
    n = e["lines"]
    top = max(wanted)
    got = _lines(path, e["id"], [i for i in wanted if isinstance(i, int) and 1 <= i <= n])
    out = {i: t.rstrip("\n") for i, t in got.items()}
    return out, (n if top > n else None)


def q_coverage_admissible_lines(abs_path, flagged=None, rel=None):
    r, e = _ent(abs_path)
    if e is None:
        _fb("coverage_admissible_lines", "no-index")
        return ORIG["coverage_admissible_lines"](abs_path, flagged, rel)
    _hit("coverage_admissible_lines")
    m = meta(e["id"])
    total = m["lines"]
    marks = {x for x in (flagged or ()) if isinstance(x, int) and x > 0}
    need_last = not marks
    small = set(m["small"])
    if need_last:
        last_q = m["last_q"]
        quotable_marks = set()
    else:
        want = sorted(x for x in (marks | set(range(1, C.COVERAGE_SMALL_FILE + 1))) if x <= total)
        got = _lines(abs_path, e["id"], want)
        last_q, quotable_marks = None, set()
        for i in want:
            if not C.quote_example({"path": rel or abs_path, "line": i,
                                    "text": got[i].rstrip("\n")}):
                continue
            last_q = i
            if i in marks:
                quotable_marks.add(i)
    if total == 0:
        return set()
    if total <= C.COVERAGE_SMALL_FILE:
        return small
    alts = {x for x in quotable_marks if x > 1}
    if alts:
        return alts
    if 1 in quotable_marks:
        return {1}
    return {last_q} if last_q else set()


def q_corpus_first_appearance(root, names, max_bytes=None, max_files=None):
    global _first
    if not names or not root or not os.path.isdir(root) or not _fresh_all(root) \
            or not all(name_ok(x) for x in names):
        _fb("corpus_first_appearance", "unsupported")
        return ORIG["corpus_first_appearance"](root, names, max_bytes, max_files)
    if _first is None:
        with open(os.path.join(IDX, "first.pkl"), "rb") as fh:
            _first = pickle.load(fh)
    max_bytes = C.OWN_SCAN_MAX_BYTES if max_bytes is None else max_bytes
    max_files = C.OWN_SCAN_MAX_FILES if max_files is None else max_files
    files = _first["files"]
    if any(not f["supported"] for f in files):
        _fb("corpus_first_appearance", "unsupported-file")
        return ORIG["corpus_first_appearance"](root, names, max_bytes, max_files)
    _hit("corpus_first_appearance")
    found, problems, untimed = {}, [], {}
    nf, nb, stop = 0, 0, len(files)
    for order, f in enumerate(files):
        if nf >= max_files or nb >= max_bytes:
            problems.append({
                "kind": "scan_truncated", "path": f["rel"],
                "text": "бюджет сканирования исчерпан: %d файлов / %d байт "
                        "(лимит %d / %d). Сузь корпус или число учёток — "
                        "непросканированный корпус не доказывает «раньше "
                        "никого не было»." % (nf, nb, max_files, max_bytes)})
            stop = order
            break
        nf += 1
        nb += f["size"]
        if f["probe_err"]:
            problems.append({"kind": "scan_error", "path": f["rel"], "text": f["probe_err"]})
    else:
        stop = len(files)
    for n in names:
        for order, best, unt in _first["cand"].get(n, ()):
            if order >= stop:
                break
            rel = files[order]["rel"]
            for no in unt:
                untimed.setdefault(n, []).append("%s:%d" % (rel, no))
            if best is not None:
                cur = found.get(n)
                if cur is None or best[0] < cur[0]:
                    found[n] = (best[0], rel, best[1], best[2])
    problems.sort(key=lambda p: 1 if p["kind"] == "scan_truncated" else 0)
    return found, problems, untimed


def _load_whole():
    global _whole
    if _whole is None:
        with open(os.path.join(IDX, "whole.pkl"), "rb") as fh:
            _whole = pickle.load(fh)
    return _whole


def q_logon_reason_scan(root, profile):
    whole = _load_whole()
    key = profile_key(profile)
    if not _fresh_all(root) or key not in whole["logon"]:
        _fb("logon_reason_scan", "unsupported")
        return ORIG["logon_reason_scan"](root, profile)
    _hit("logon_reason_scan")
    return copy.deepcopy(whole["logon"][key])


def q_load_rollover():
    mod = ORIG["load_rollover"]()
    orig_scan = getattr(mod, "_sherlock_orig_scan_corpus", mod.scan_corpus)
    mod._sherlock_orig_scan_corpus = orig_scan

    def scan_corpus(root):
        if not _fresh_all(root):
            _fb("rollover", "unsupported")
            return orig_scan(root)
        _hit("rollover")
        return copy.deepcopy(_load_whole()["rollover"])
    mod.scan_corpus = scan_corpus
    return mod


def attach_for(checker_module, corpus, report=None, require=None):
    """citecheck entry point. -> (status, detail). status "fresh" = attached."""
    if require is None:
        require = os.environ.get(REQUIRE_ENV) == "1"
    status, d, info = find_index(corpus, report)
    if status == "fresh":
        attach(checker_module, d, info, lift_caps=os.environ.get(LIFT_CAPS_ENV) == "1",
               corpus=os.path.abspath(corpus))
        return status, d
    return status, info
