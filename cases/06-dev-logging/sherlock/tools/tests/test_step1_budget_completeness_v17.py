#!/usr/bin/env python3
"""Tests for skills/v17/tools/logmap.py — Step 1 must not go blind on the two
places where a budget silently ran out.

Both defects were MEASURED on 2026-08-18, on the first blind run of the whole
skill against AIT-LDS v2.1 (russellmitchell, 22 hosts, 7,464 files) and scored
with `eval/bench/score-ait.py` against the corpus's own line-numbered labels.

DEFECT 1 — the map never got the per-host budget the worklist got in v15.

    work/map.txt   7,762,064 bytes  ≈ 1.94M tokens
    SKILL.md:70    "все три надо прочитать"

    5,213,280 of those bytes (67 %) are files under 4 KB quoted VERBATIM, and
    one host (`monitoring`) owns 2,327,946 of them. The worklist was split per
    host and the map was left whole, so the tool now hands the model a worklist
    it can read and a map it cannot.

DEFECT 2 — the gate fires backwards on a compromised host, and takes three more
axes with it than its own message admits.

    map.txt:72228
      ОСЬ РЕДКОСТИ ОТКЛЮЧЕНА: доля уникальных форм 0.4587 > 0.25

    The file it says that about is
    `intranet_server/logs/apache2/intranet.smith.russellmitchell.com-access.log.2`
    — 7,695 of its 8,530 lines are labelled attack (90.2 %) — and it received
    ZERO worklist rows. Its sibling `…-error.log.2` is 36 of 36 labelled
    (100.0 %), ratio 1.0000, also zero rows. Scored:

        arm   FILES TOUCHED   access.log.2   error.log.2   system.cpu.log
        v16       4 of 8        0 of 7695      0 of 36        0 of 49

    Three things are wrong, and only the first is the one the message names.

    a) `analyse()` implements the gate as an early `return`. `rep.counts`,
       `rep.first_seen`, `rep.tmpl_hour`, `rep.level_hist` and `rep.slot_pct`
       are therefore never assigned, so axis 1 (rarity), axis 2 (category),
       axis 3 (rate) AND axis 4 (outcome) are all empty. The status histogram
       for that access log IS collected (`200=279, 404=37`) and IS printed in
       the map — and no row can ever be built from it.
    b) `build_worklist` and `rate_worklist_rows` both `continue` on `r.gated`,
       so "no axis applies" falls all the way through to "no attention". The
       more thoroughly a server is shelled, the less Step 1 looks at it.
    c) The gate is not the only door into zero rows.
       `monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log` is
       NOT gated — ratio 0.0010 — but it holds exactly 2 templates, both
       common, so axis 1 finds no rare group and it gets zero rows too.

    Measured against the alternative fix: the threshold is NOT the bug. Forced
    to line framing, that access log is 8,530 records / 7,502 distinct forms /
    ratio 0.8795 — twice as gated as the 0.4587 its key framing produced. A
    WordPress site under a scanner genuinely has near-unique lines. Moving 0.25
    trades one silent cliff for another.

So v17 gives every file that produces no axis-1/2 row a FLOOR of at least one
row, drawn from axes that still work when every shape is unique, and it says on
the row itself which axis produced it:

    code    a non-dominant outcome code (404/5xx) at the record where it first
            appears — computed from data the gate already collects
    level   the rarest value of a discovered level axis
    burst   the fullest hour of the file, cited at its first record there
    edge    the first and the last record — the last resort, and never silent

    python3 tools/tests/test_step1_budget_completeness_v17.py
"""
import contextlib
import filecmp
import importlib.util
import io
import os
import re
import shutil
import string
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)

FALLBACK_KINDS = {"code", "level", "burst", "edge"}


def _load(name, version):
    path = os.path.join(SHERLOCK, "skills", version, "tools", "logmap.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("logmap_v17", "v17")
V16 = _load("logmap_v16_ref", "v16")


# ---------------------------------------------------------------------------
# helpers — the tool is always driven through main(), exactly as the operator
# drives it, so nothing here can pass by calling an internal the skill never
# reaches.
# ---------------------------------------------------------------------------
def put(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    return p


def run(mod, corpus, out, extra=()):
    argv = sys.argv
    sys.argv = ["logmap.py", corpus, "--out", out, "--jobs", "1"] + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.main()
    finally:
        sys.argv = argv
    if rc:
        raise AssertionError("logmap exited %s\n%s" % (rc, buf.getvalue()))
    return buf.getvalue()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def rows_of(path):
    """-> [(id, verdict, kind, cite, freq, record)] from a worklist."""
    out = []
    for line in read(path).splitlines():
        if line.startswith("#") or not line.strip():
            continue
        out.append(line.split("\t"))
    return out


def cited_files(path):
    return [r[3].split(":")[0] for r in rows_of(path) if len(r) > 3]


ALPHA = string.ascii_lowercase


def access_log(n=200, bad_every=40):
    """A WordPress-shaped access log where every path is unique — the exact shape
    that trips the gate, with a handful of 404s from a different client."""
    out = []
    for i in range(n):
        p = ALPHA[(i // 26) % 26] + ALPHA[i % 26] + ALPHA[(i * 7) % 26]
        bad = i % bad_every == 7
        out.append('10.0.0.5 - - [23/Jan/2022:06:%02d:%02d +0000] '
                   '"GET /shop/%s/index HTTP/1.1" %d 6203 "-" "%s"'
                   % ((i // 60) % 60, i % 60, p, 404 if bad else 200,
                      "curl/7.68.0" if bad else "Mozilla/5.0"))
    return "\n".join(out) + "\n"


def flat_log(n=400):
    """Two templates, both common, no rare anything — not gated, and still worth
    zero rows under v16."""
    out = []
    for i in range(n):
        out.append("2022-01-24T06:%02d:%02d cpu.user %d.5" % (i // 60, i % 60, i % 9))
        out.append("2022-01-24T06:%02d:%02d cpu.sys %d.5" % (i // 60, i % 60, i % 7))
    return "\n".join(out) + "\n"


def ordinary_log(n=300, marker=1):
    """An ordinary log with a clock and a genuine rare residue — the shape that
    must come out of v17 byte for byte as it came out of v16."""
    out = []
    for i in range(n):
        out.append("2022-01-24 07:%02d:%02d INFO worker=%d handled request ok"
                   % ((i // 60) % 60, i % 60, i % 5))
    out.append("2022-01-24 07:59:59 ERROR worker=%d segmentation fault in module"
               % marker)
    return "\n".join(out) + "\n"


# ===========================================================================
# DEFECT 2 — a disabled axis must never mean zero attention
# ===========================================================================
class TheGateNoLongerMeansInvisible(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.corpus = os.path.join(self.d, "corpus")
        self.out = os.path.join(self.d, "work")
        put(self.corpus, "logs/access.log", access_log())
        put(self.corpus, "logs/app.log", ordinary_log())
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_the_gated_file_is_gated_at_all(self):
        """The premise of every other test in this class, asserted rather than
        assumed: v16 really does refuse this file."""
        import argparse
        args = argparse.Namespace(seed=V16.SEED, per_file_cap=40)
        rep = V16.analyse(os.path.join(self.corpus, "logs", "access.log"),
                          "logs/access.log", args)
        self.assertTrue(rep.gated, "fixture no longer trips the gate")
        self.assertEqual(rep.groups, [])

    def test_v16_gives_the_gated_file_zero_rows(self):
        run(V16, self.corpus, self.out)
        self.assertNotIn("logs/access.log",
                         cited_files(os.path.join(self.out, "worklist.tsv")))

    def test_v17_gives_the_gated_file_at_least_one_row(self):
        run(L, self.corpus, self.out)
        cited = cited_files(os.path.join(self.out, "worklist.tsv"))
        self.assertIn("logs/access.log", cited,
                      "a file whose every line is unique still has to be looked at")

    def test_the_floor_rows_say_which_axis_produced_them(self):
        """A fallback row is a different KIND of claim from a rarity row. The
        model must not have to guess which it is holding."""
        run(L, self.corpus, self.out)
        kinds = {r[2] for r in rows_of(os.path.join(self.out, "worklist.tsv"))
                 if r[3].startswith("logs/access.log")}
        self.assertTrue(kinds, "no rows at all on the gated file")
        self.assertTrue(kinds <= FALLBACK_KINDS,
                        "gated file produced non-fallback kinds %s — the rarity "
                        "axis cannot have run" % (kinds - FALLBACK_KINDS))
        self.assertNotIn("rare", kinds)
        self.assertNotIn("cat", kinds)

    def test_the_minority_outcome_code_is_one_of_them(self):
        """404 is 5 records of 200 here, and its exemplars are collected BEFORE
        the gate returns. Nothing but the early return was stopping them."""
        run(L, self.corpus, self.out)
        rows = [r for r in rows_of(os.path.join(self.out, "worklist.tsv"))
                if r[3].startswith("logs/access.log") and r[2] == "code"]
        self.assertTrue(rows, "no outcome-code row on a file with 200s and 404s")
        self.assertTrue(any("404" in r[5] for r in rows),
                        "the code row must show the record carrying the code")

    def test_the_code_row_cites_a_line_that_really_carries_that_code(self):
        run(L, self.corpus, self.out)
        body = read(os.path.join(self.corpus, "logs", "access.log")).splitlines()
        for r in rows_of(os.path.join(self.out, "worklist.tsv")):
            if r[2] != "code" or not r[3].startswith("logs/access.log"):
                continue
            m = re.search(r":(\d+)", r[3])
            line = body[int(m.group(1)) - 1]
            self.assertIn(" 404 ", line,
                          "code row cites %s, which is not a 404" % r[3])

    def test_a_file_with_no_rare_group_also_gets_a_floor(self):
        """The gate is not the only door into zero rows: two common templates and
        nothing else is the other one, and it is how a 1,920-line metric log with
        49 labelled lines got no attention."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        put(corpus, "logs/cpu.log", flat_log())
        put(corpus, "logs/app.log", ordinary_log())
        run(V16, corpus, out)
        self.assertNotIn("logs/cpu.log", cited_files(os.path.join(out, "worklist.tsv")))
        out2 = os.path.join(d, "w2")
        run(L, corpus, out2)
        self.assertIn("logs/cpu.log", cited_files(os.path.join(out2, "worklist.tsv")))

    def test_the_floor_is_a_floor_not_a_flood(self):
        """One gated file must not be able to spend the whole budget: the floor
        exists so nothing is invisible, not so noise becomes loud."""
        run(L, self.corpus, self.out)
        n = len([r for r in rows_of(os.path.join(self.out, "worklist.tsv"))
                 if r[3].startswith("logs/access.log")])
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, L.FALLBACK_PER_FILE,
                             "%d fallback rows on one file" % n)

    def test_a_file_quoted_whole_into_the_map_gets_no_floor_row(self):
        """A file under SMALL_FILE_BYTES is already 100 % in front of the model.
        A worklist row would be an address for text it already holds — and 633 of
        one AIT host's 695 files are that shape, so spending the budget there
        would take it from everything else. Excluded on purpose, asserted so it
        stays a decision."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        tiny = "\n".join("uniq-key-%s = value-%s" % (ALPHA[i % 26] * 3, i)
                         for i in range(30)) + "\n"
        self.assertLess(len(tiny), L.SMALL_FILE_BYTES)
        put(corpus, "etc/tiny.conf", tiny)
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertNotIn("etc/tiny.conf", cited_files(os.path.join(out, "worklist.tsv")))
        self.assertIn("ДОСЛОВНО", read(os.path.join(out, "map.txt")))

    def test_a_state_artefact_gets_no_floor_row(self):
        """Deliberate, and measured. Three of the four floor axes mean nothing
        without a clock, and a bundle is mostly configs: CAM-LDS scenario 1 is
        9,059 files, and letting `state` files draw a floor took the config share
        of its worklist from 62/475 (13.1 %) to 716/1250 (57.3 %) — the defect
        v14 exists to prevent. A config keeps its own budget share and its map
        entry; what it does not get is a row it cannot earn."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        cfg = "".join(" ".join(ALPHA[(i + j) % 26] * (1 + (i + j) % 5)
                               for j in range(2 + i % 9)) + "\n"
                      for i in range(400))
        self.assertGreater(len(cfg), L.SMALL_FILE_BYTES)
        put(corpus, "etc/big.conf", cfg)
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        m = read(os.path.join(out, "map.txt"))
        self.assertIn("ОСИ ФОРМ ОТКЛЮЧЕНЫ", m, "fixture must be gated")
        self.assertIn("род состояние", m, "fixture must be a state artefact")
        self.assertNotIn("etc/big.conf",
                         cited_files(os.path.join(out, "worklist.tsv")))

    def test_the_map_stops_claiming_only_the_rarity_axis_is_off(self):
        """The v16 wording says one axis. The code returns early and disables
        four. A message narrower than the behaviour is how this defect survived
        a full blind run while printing itself on line 72228."""
        run(L, self.corpus, self.out)
        m = read(os.path.join(self.out, "map.txt"))
        self.assertNotIn("ось редкости отключена", m)
        self.assertIn("ОСИ ФОРМ ОТКЛЮЧЕНЫ", m)

    def test_the_map_says_how_many_rows_the_floor_produced(self):
        run(L, self.corpus, self.out)
        m = read(os.path.join(self.out, "map.txt"))
        self.assertRegex(m, r"ОПОРНЫЕ СТРОКИ: \d+")

    def test_the_worklist_legend_explains_the_new_kinds_only_when_they_occur(self):
        """A legend for a value that never appears is noise — and it would also
        change every worklist of every corpus that has no gated file."""
        run(L, self.corpus, self.out)
        self.assertIn("опора", read(os.path.join(self.out, "worklist.tsv")))
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertNotIn("опора", read(os.path.join(out, "worklist.tsv")))

    def test_a_corpus_with_no_gated_and_no_rowless_file_is_untouched(self):
        """The regression bar: where v16 already had something to say, v17 says
        exactly the same thing, byte for byte."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus = os.path.join(d, "c")
        put(corpus, "logs/app.log", ordinary_log())
        put(corpus, "logs/other.log", ordinary_log(200, marker=3))
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        run(V16, corpus, a)
        run(L, corpus, b)
        for name in ("map.txt", "worklist.tsv", "axis3.tsv"):
            self.assertTrue(filecmp.cmp(os.path.join(a, name),
                                        os.path.join(b, name), shallow=False),
                            "%s changed on a corpus with nothing to fix" % name)


# ===========================================================================
# DEFECT 1 — the map gets the budget the worklist already has
# ===========================================================================
def multi_host_corpus(root, hosts=("alpha", "beta", "gamma"), per_host=40):
    for h in hosts:
        put(root, "%s/logs/app.log" % h, ordinary_log())
        for i in range(per_host):
            put(root, "%s/configs/mod-%02d.conf" % (h, i),
                "".join("setting_%02d_%02d = value-%s\n" % (i, j, ALPHA[j % 26])
                        for j in range(60)))
    return root


class TheMapIsBudgetedPerHost(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.corpus = multi_host_corpus(os.path.join(self.d, "corpus"))
        self.out = os.path.join(self.d, "work")
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_v16_writes_one_undivided_map(self):
        run(V16, self.corpus, self.out)
        self.assertTrue(os.path.exists(os.path.join(self.out, "map.txt")))
        self.assertEqual([], [f for f in os.listdir(self.out)
                              if f.startswith("map-")])

    def test_v17_writes_one_map_per_host(self):
        run(L, self.corpus, self.out)
        for h in ("alpha", "beta", "gamma"):
            self.assertTrue(os.path.exists(os.path.join(self.out, "map-%s.txt" % h)),
                            "host %s has no map of its own" % h)

    def test_map_txt_becomes_the_index_and_names_every_host_map(self):
        run(L, self.corpus, self.out)
        m = read(os.path.join(self.out, "map.txt"))
        for h in ("alpha", "beta", "gamma"):
            self.assertIn("map-%s.txt" % h, m)

    def test_the_index_carries_no_per_file_blocks(self):
        """1.94M tokens is what happens when it does."""
        run(L, self.corpus, self.out)
        m = read(os.path.join(self.out, "map.txt"))
        self.assertNotIn("configs/mod-00.conf", m)
        self.assertLess(len(m.encode()), 40000)

    def test_every_file_is_in_exactly_one_host_map(self):
        """Nothing silently dropped. Every analysed file appears by name, once."""
        run(L, self.corpus, self.out)
        blob = "".join(read(os.path.join(self.out, "map-%s.txt" % h))
                       for h in ("alpha", "beta", "gamma"))
        for h in ("alpha", "beta", "gamma"):
            self.assertEqual(1, blob.count("%s/logs/app.log " % h) +
                             blob.count("%s/logs/app.log\n" % h),
                             "%s/logs/app.log appears the wrong number of times" % h)
            for i in range(40):
                self.assertIn("%s/configs/mod-%02d.conf" % (h, i), blob)

    def test_a_host_over_budget_says_so_and_says_how_much(self):
        run(L, self.corpus, self.out, extra=["--map-cap", "6000"])
        body = read(os.path.join(self.out, "map-alpha.txt"))
        self.assertIn("СВЁРНУТО", body)
        self.assertRegex(body, r"СВЁРНУТО[^\n]*?\d+ файл[^\n]*?\d+")

    def test_over_budget_still_names_every_file(self):
        run(L, self.corpus, self.out, extra=["--map-cap", "6000"])
        body = read(os.path.join(self.out, "map-alpha.txt"))
        for i in range(40):
            self.assertIn("alpha/configs/mod-%02d.conf" % i, body,
                          "a compressed file must still be named")

    def test_the_budget_never_compresses_a_file_that_earned_a_worklist_row(self):
        run(L, self.corpus, self.out, extra=["--map-cap", "6000"])
        body = read(os.path.join(self.out, "map-alpha.txt"))
        head, _sep, tail = body.partition("СВЁРНУТО")
        self.assertIn("alpha/logs/app.log", head,
                      "the one file with rows was compressed while configs were "
                      "shown in full")

    def test_no_truncation_notice_when_nothing_is_truncated(self):
        run(L, self.corpus, self.out)
        self.assertNotIn("СВЁРНУТО", read(os.path.join(self.out, "map-alpha.txt")))

    def test_a_single_host_corpus_under_budget_is_byte_identical_to_v16(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus = os.path.join(d, "c")
        put(corpus, "logs/app.log", ordinary_log())
        put(corpus, "logs/other.log", ordinary_log(200, marker=3))
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        run(V16, corpus, a)
        run(L, corpus, b)
        self.assertTrue(filecmp.cmp(os.path.join(a, "map.txt"),
                                    os.path.join(b, "map.txt"), shallow=False))
        self.assertEqual([], [f for f in os.listdir(b) if f.startswith("map-")])

    def test_a_single_host_corpus_over_budget_is_truncated_and_says_so(self):
        """The cliff is removed, not moved: a one-host bundle of 9,000 files gets
        the same treatment as a host inside a 22-host bundle. CAM-LDS scenario 1
        is 9,059 files and 11.3 MB of map — the shape this covers."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        put(corpus, "logs/app.log", ordinary_log())
        for i in range(40):
            put(corpus, "configs/mod-%02d.conf" % i,
                "".join("setting_%02d_%02d = value-%s\n" % (i, j, ALPHA[j % 26])
                        for j in range(60)))
        run(L, corpus, out, extra=["--map-cap", "6000"])
        m = read(os.path.join(out, "map.txt"))
        self.assertIn("СВЁРНУТО", m)
        for i in range(40):
            self.assertIn("configs/mod-%02d.conf" % i, m)

    def test_the_operator_can_turn_the_budget_off(self):
        """`--map-cap 0` turns the budget off WHOLE — the undivided map v16 wrote.
        Half a switch would be a switch that lies, and it is also what makes the
        two v17 fixes measurable apart: the map arm is this flag, the floor arm
        is what the flag cannot touch."""
        run(L, self.corpus, self.out, extra=["--map-cap", "0"])
        self.assertEqual([], [f for f in os.listdir(self.out)
                              if f.startswith("map-")])
        m = read(os.path.join(self.out, "map.txt"))
        self.assertNotIn("СВЁРНУТО", m)
        self.assertIn("alpha/configs/mod-00.conf", m)

    def test_map_cap_zero_reproduces_the_v16_map_exactly(self):
        """The ablation's control: with the budget off, v17 writes v16's map byte
        for byte on a corpus where the floor has nothing to add either."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus = os.path.join(d, "c")
        for h in ("alpha", "beta"):
            put(corpus, "%s/logs/app.log" % h, ordinary_log())
            put(corpus, "%s/logs/other.log" % h, ordinary_log(200, marker=3))
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        run(V16, corpus, a)
        run(L, corpus, b, extra=["--map-cap", "0"])
        self.assertTrue(filecmp.cmp(os.path.join(a, "map.txt"),
                                    os.path.join(b, "map.txt"), shallow=False))

    def test_stdout_points_at_the_host_map_not_the_index(self):
        got = run(L, self.corpus, self.out)
        self.assertIn("map-", got)


# ===========================================================================
# the arms stay arms
# ===========================================================================
class TheOlderArmsAreUntouched(unittest.TestCase):
    """v1…v16 each define what a measured arm ran. v17 is additive."""

    def test_v16_is_still_a_byte_copy_of_v15_where_it_was_one(self):
        for rel in ("reference/report-format.md", "reference/code-and-spec.md",
                    "tools/logmap.py", "tools/logjoin.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(SHERLOCK, "skills", "v15", rel),
                            os.path.join(SHERLOCK, "skills", "v16", rel),
                            shallow=False),
                "v16/%s changed — a frozen arm just moved" % rel)

    def test_v17_changed_no_tool_but_logmap(self):
        """SKILL.md and reference/tools.md move with the behaviour — a split the
        model is never told about is not a split. The other two tools and the two
        other reference pages must not."""
        for rel in ("reference/report-format.md", "reference/code-and-spec.md",
                    "tools/logjoin.py", "tools/citecheck.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(SHERLOCK, "skills", "v16", rel),
                            os.path.join(SHERLOCK, "skills", "v17", rel),
                            shallow=False),
                "v17 changed %s, which is not what it is for" % rel)

    def test_v17_did_change_logmap(self):
        self.assertFalse(
            filecmp.cmp(os.path.join(SHERLOCK, "skills", "v16", "tools", "logmap.py"),
                        os.path.join(SHERLOCK, "skills", "v17", "tools", "logmap.py"),
                        shallow=False))

    def test_v17_skill_tells_the_model_to_read_its_host_map(self):
        body = read(os.path.join(SHERLOCK, "skills", "v17", "SKILL.md"))
        self.assertIn("map-", body,
                      "the split is only real if SKILL.md sends the model there")


# ===========================================================================
# the real evidence, when it is on this machine
# ===========================================================================
AIT = "/Users/a/hack/sherlock-corpora/ait-lds-v2/extracted/gather"
APACHE = os.path.join(AIT, "intranet_server", "logs", "apache2")


@unittest.skipUnless(os.path.isdir(APACHE), "AIT-LDS not on this machine")
class TheTwoAttackedApacheFiles(unittest.TestCase):
    """The defect on the file it was found on. Both are small enough to analyse
    in a unit test (1.5 MB and 8.5 KB), and both are the actual evidence: 90.2 %
    and 100.0 % labelled attack, zero worklist rows under v16."""

    ACCESS = "intranet.smith.russellmitchell.com-access.log.2"
    ERROR = "intranet.smith.russellmitchell.com-error.log.2"

    def _rep(self, mod, name):
        import argparse
        args = argparse.Namespace(seed=mod.SEED, per_file_cap=40)
        return mod.analyse(os.path.join(APACHE, name), name, args)

    def test_v16_gives_both_of_them_nothing(self):
        for name in (self.ACCESS, self.ERROR):
            r = self._rep(V16, name)
            self.assertTrue(r.gated, "%s is no longer gated" % name)
            self.assertEqual([], r.groups)
            self.assertEqual([], r.rate_rows)
            self.assertEqual([], r.out_rows, "axis 4 dies with the gate too")

    def test_v17_gives_both_of_them_a_floor(self):
        for name in (self.ACCESS, self.ERROR):
            r = self._rep(L, name)
            self.assertTrue(r.gated, "the gate itself is not what changed")
            self.assertTrue(r.floor, "%s still gets nothing" % name)
            self.assertTrue({row[3] for row in r.floor} <= FALLBACK_KINDS)

    def test_the_access_log_floor_reaches_its_404s(self):
        """`ось исхода «ws:8» (2 значений): 200=279 (88.3%), 404=37 (11.7%)` is
        printed in v16's own map for this file, and v16 can build no row from
        it."""
        r = self._rep(L, self.ACCESS)
        self.assertIn("code", {row[3] for row in r.floor})


if __name__ == "__main__":
    unittest.main(verbosity=2)
