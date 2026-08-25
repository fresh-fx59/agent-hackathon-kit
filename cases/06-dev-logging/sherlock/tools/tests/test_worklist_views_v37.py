#!/usr/bin/env python3
"""Defect 3 of the 2026-08-25 v36 audit: one 135 KB file was 40% of the run.

MEASURED on sherlock-winevtx-runs-v36-full-r1. `work/worklist.tsv` is 134,772
bytes and 250 rows. Four independent contexts each paginated it:

    parent            21 reads    3,580,782 amplified tokens
    sherlock-triage   12 reads    1,676,897
    sherlock-draft     5 reads    1,313,889
    general-purpose    8 reads      854,603
                                 ~7,430,000 = 40% of the whole run

Every one of them started with a full read, which the harness truncates at
~25,062 bytes, and therefore had to re-page in overlapping windows anyway.

THE SKILL ALREADY TOLD THEM NOT TO. logmap's own map head prints «НЕ читай всё
разом» and all four did it regardless. That is the evidence that this is not an
instruction problem: a file that CAN be read whole will be read whole. The fix is
the input-gate principle from docs/conventions.md — make the sliced view the only
thing worth reading, sized so a slice always fits in one call.

The split is by BYTES, not by a row count: rows vary from ~200 to ~2000 bytes, so
"40 rows per slice" would still overflow on a dense axis. Deriving it from the
read budget is what makes this a whole-class fix rather than a cap that moves the
cliff to the next unusually wide worklist.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
LOGMAP = os.path.join(SKILLS, "v37", "tools", "logmap.py")
V36_LOGMAP = os.path.join(SKILLS, "v36", "tools", "logmap.py")

# The measured harness ceiling on one tool result.
READ_CAP = 25062


def corpus_with(dirname, files):
    for name, lines in files.items():
        with open(os.path.join(dirname, name), "w", encoding="utf-8") as fh:
            fh.writelines(lines)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.corpus = os.path.join(self.dir, "corpus")
        self.out = os.path.join(self.dir, "work")
        os.makedirs(self.corpus)
        os.makedirs(self.out)
        # Enough variety that several axes fire and the worklist is wide.
        files = {}
        for f in range(6):
            rows = []
            for i in range(400):
                rows.append('{"ts":"2021-06-0%dT00:%02d:00Z","lvl":"INFO",'
                            '"msg":"routine %d","pad":"%s"}\n'
                            % (1 + f % 3, i % 60, i, "x" * 120))
            for i in range(12):
                rows.append('{"ts":"2021-06-02T03:%02d:00Z","lvl":"ERROR",'
                            '"code":%d,"msg":"unusual thing %d","pad":"%s"}\n'
                            % (i, 500 + i, i, "y" * 400))
            files["f%d.jsonl" % f] = rows
        corpus_with(self.corpus, files)

    def run_logmap(self, tool):
        done = subprocess.run([sys.executable, tool, self.corpus, "--out", self.out],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return done

    def slices(self):
        return sorted(f for f in os.listdir(self.out)
                      if f.startswith("view-") and f.endswith(".tsv"))


class TestSlicedViews(Base):
    def test_slices_are_written(self):
        self.run_logmap(LOGMAP)
        self.assertTrue(self.slices(),
                        "logmap must write per-axis worklist views; got %s"
                        % sorted(os.listdir(self.out)))

    def test_every_slice_fits_one_read(self):
        """A slice that overflows the cap re-creates the very defect."""
        self.run_logmap(LOGMAP)
        for name in self.slices():
            size = os.path.getsize(os.path.join(self.out, name))
            self.assertLessEqual(size, READ_CAP,
                                 "%s is %d bytes, over the %d read cap"
                                 % (name, size, READ_CAP))

    def test_slices_lose_no_rows(self):
        """A view that drops rows would be a silent cap. Union must be exact."""
        self.run_logmap(LOGMAP)
        def data_rows(path):
            with open(path, encoding="utf-8") as fh:
                return {l for l in fh if l.strip() and not l.startswith("#")}
        whole = data_rows(os.path.join(self.out, "worklist.tsv"))
        union = set()
        for name in self.slices():
            union |= data_rows(os.path.join(self.out, name))
        self.assertEqual(union, whole,
                         "slices must reproduce worklist.tsv exactly: "
                         "%d missing, %d extra"
                         % (len(whole - union), len(union - whole)))

    def test_index_lets_you_choose_without_reading_a_slice(self):
        self.run_logmap(LOGMAP)
        index = os.path.join(self.out, "worklist-index.tsv")
        self.assertTrue(os.path.exists(index), "need an index of the views")
        self.assertLessEqual(os.path.getsize(index), READ_CAP)
        text = open(index, encoding="utf-8").read()
        for name in self.slices():
            self.assertIn(name, text, "index must name every view")

    def test_canonical_worklist_still_exists(self):
        """citecheck --ledger and triagecheck --worklist read it. Views are additive."""
        self.run_logmap(LOGMAP)
        self.assertTrue(os.path.exists(os.path.join(self.out, "worklist.tsv")))

    def test_views_do_not_collide_with_per_host_worklists(self):
        """`worklist-<host>.tsv` is a real artifact; a view must not look like one."""
        self.run_logmap(LOGMAP)
        for name in self.slices():
            self.assertFalse(name.startswith("worklist-"), name)


class TestSlicingEdges(unittest.TestCase):
    """Two silent-loss modes a review found in the first version of this fix."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("lm", LOGMAP)
        self.lm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lm)
        self.out = tempfile.mkdtemp()

    def rows_in(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as fh:
            return {l for l in fh if not l.startswith("#") and l.strip()}

    def test_non_ascii_axes_get_distinct_files(self):
        """`всплеск`, `редкое` and the fallback label `прочее` all slugged to
        "x" and overwrote each other — half the rows vanished with no complaint.
        `прочее` is _axis_of's own default, so this was reachable by default."""
        rows = ["g%s%d\t?\t%s\tf.jsonl:%d\tn=1\tзапись\n" % (a[:2], i, a, i)
                for a in ("всплеск", "редкое", "прочее") for i in range(3)]
        written = self.lm.write_worklist_views(self.out, rows, ["# hdr\n"])
        self.assertEqual(len({w[0] for w in written}), 3, written)
        union = set()
        for name, *_ in written:
            union |= self.rows_in(name)
        self.assertEqual(union, set(rows), "rows lost to a slug collision")

    def test_long_ascii_axes_sharing_a_prefix_do_not_collide(self):
        a, b = "x" * 30 + "aaa", "x" * 30 + "bbb"
        rows = ["r%d\t?\t%s\tf.jsonl:1\tn=1\tz\n" % (i, ax)
                for i, ax in enumerate((a, b))]
        written = self.lm.write_worklist_views(self.out, rows, ["# hdr\n"])
        self.assertEqual(len({w[0] for w in written}), 2, written)

    def test_a_row_over_the_budget_is_announced_not_dropped(self):
        """Dropping it would be a silent cap; shipping it silently makes the
        index's promise false. So it ships and says so."""
        big = ["B001\t?\tax\tf.jsonl:1\tn=1\t%s\n" % ("q" * 60000)]
        written = self.lm.write_worklist_views(self.out, big, ["# hdr\n"])
        self.lm.write_worklist_index(self.out, written, 1)
        self.assertEqual(self.rows_in(written[0][0]), set(big), "row was dropped")
        self.assertGreater(written[0][3], self.lm.VIEW_READ_CAP)
        index = open(os.path.join(self.out, "worklist-index.tsv"),
                     encoding="utf-8").read()
        self.assertIn("ЦЕЛИКОМ-НЕ-ВЛЕЗЕТ", index)
        self.assertIn("ВНИМАНИЕ", index)


class TestV36StillCarriesTheDefect(Base):
    def test_v36_writes_no_views(self):
        self.run_logmap(V36_LOGMAP)
        self.assertEqual(self.slices(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
