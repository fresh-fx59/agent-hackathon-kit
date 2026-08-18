#!/usr/bin/env python3
"""Tests for skills/v15/tools/logmap.py — the worklist budget is PER HOST.

Every assertion here is a defect MEASURED on 2026-08-18 on AIT-LDS v2.1
(russellmitchell testbed, 22 hosts) and scored against the corpus's own
line-numbered labels with `_tools/score-ait.py`:

    arm   scope                     labelled files touched   auth.log   audit.log
    v13   whole testbed (22 hosts)         1 of 8              0 of 8     0 of 9
    v14   whole testbed (22 hosts)         1 of 8              0 of 8     0 of 9
    v13   ONE host (intranet_server)       3 of 8              8 of 8     5 of 9
    v14   ONE host (intranet_server)       3 of 8              8 of 8     9 of 9

The tool is not blind. Its budget is diluted. 250 rows round-robined across 173
distinct files is 1.4 rows per file, and the needle is 8 labelled lines inside a
272-line `auth.log`. A multi-host evidence bundle is N corpora sharing one
budget — the same defect as configs sharing the budget with logs (v14), one
level up.

So v15 partitions the bundle AT THE INPUT BOUNDARY and gives every host its own
full budget. What that pins here:

* host roots are found STRUCTURALLY — the shallowest depth at which most sibling
  subtrees repeat the same internal layout — never by a hostname word list;
* a single-host bundle must be untouched, byte for byte, or v15 is a regression
  dressed as a fix. `worklist.tsv` and `axis3.tsv` are compared against v14's
  own output on the same input;
* nothing is silently dropped: every detected host appears in `hosts.tsv`, and
  the rows in the per-host files add up to the rows in `worklist.tsv`;
* the operator can always override the detector (`--host-depth`, `--single-host`).

    python3 tools/tests/test_per_host_budget_v15.py
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)


def _load(name, version):
    path = os.path.join(SHERLOCK, "skills", version, "tools", "logmap.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("logmap_v15", "v15")
V14 = _load("logmap_v14_ref", "v14")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def put(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    return p


def run(mod, corpus, out, extra=()):
    """Drive the tool exactly as the operator does — through main()."""
    argv = sys.argv
    sys.argv = ["logmap.py", corpus, "--out", out, "--jobs", "1"] + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.main()
    finally:
        sys.argv = argv
    assert rc == 0, "logmap exited %r" % rc
    return buf.getvalue()


def rows_of(path):
    """The data rows of a worklist — comments and blanks are not rows."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [l for l in fh if l.strip() and not l.startswith("#")]


LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _word(i):
    """A distinct ALPHABETIC token per group. Digits would be masked into a
    numeric slot and collapse every group into one template — which is the
    correct behaviour of the masker and the wrong fixture for a budget test."""
    return LETTERS[(i // 26) % 26] + LETTERS[i % 26] + "task"


def logfile(host, bulk=500, rare=80):
    """One chatty background template plus `rare` one-off ones.

    Shaped to clear two gates that are doing their job: over `SMALL_FILE_BYTES`
    (a small file is quoted verbatim and yields NO rows) and under
    `DISTINCT_RATIO_GATE` (a file whose every record is unique is not a log and
    its rarity axis is switched off). What is left is `rare` groups of n=1 —
    more candidates than any per-file cap will take, which is what a budget
    test needs."""
    out = []
    for i in range(bulk):
        out.append("2026-08-18 %02d:%02d:%02d %s cron[%d]: session opened for "
                   "user backup\n" % (i % 24, i % 60, (i * 7) % 60, host, 2000 + i))
    for i in range(rare):
        out.append("2026-08-18 %02d:%02d:%02d %s daemon[%d]: %s finished with "
                   "code %d\n" % (i % 24, i % 60, (i * 11) % 60, host,
                                  1000 + i, _word(i), i % 3))
    return "".join(out)


def conffile(i):
    return "# generated\nsetting_%d = value_%d\nother = %d\n" % (i, i, i)


# ---------------------------------------------------------------------------
# structural host detection
# ---------------------------------------------------------------------------
class HostDetection(unittest.TestCase):
    """`host_roots(rels)` -> (depth, [root, ...], shared_child_name)."""

    # AIT-LDS: gather/<host>/{logs,configs}/..., 22 of them.
    AIT = ["intranet_server/logs/auth.log",
           "intranet_server/logs/audit/audit.log",
           "intranet_server/configs/etc/hosts",
           "intranet_server/facts.json",
           "inet-firewall/logs/dnsmasq.log",
           "inet-firewall/configs/etc/hosts",
           "inet-firewall/facts.json",
           "mail/logs/mail.log",
           "mail/configs/etc/postfix/main.cf"]

    # BlueSky: ONE Windows host. evtx/ is split by provenance, not by machine.
    BLUESKY = ["MANIFEST.tsv",
               ".sherlock-cyber-corpus",
               "evtx/host/Application.jsonl",
               "evtx/host/Security.jsonl",
               "evtx/incident/Sysmon.jsonl",
               "pcap/dns.tsv",
               "pcap/http.tsv",
               "toolkit/del.ps1.txt",
               "toolkit/checking.ps1.txt"]

    def test_ait_shape_is_partitioned_at_depth_one(self):
        d, roots, shape = L.host_roots(self.AIT)
        self.assertEqual(d, 1)
        self.assertEqual(roots, ["inet-firewall", "intranet_server", "mail"])
        self.assertEqual(shape, "configs+logs")

    def test_cam_shape_is_partitioned_at_depth_one(self):
        rels = []
        for h in ("attacker", "corpdns", "inetfw", "videoserver", "wazuh"):
            rels += ["%s/logs/syslog" % h, "%s/configs/etc/hosts" % h,
                     "%s/facts.json" % h]
        d, roots, _shape = L.host_roots(rels)
        self.assertEqual(d, 1)
        self.assertEqual(len(roots), 5)

    def test_single_host_bundle_is_not_partitioned(self):
        """The regression bar. `evtx/` and `pcap/` are not two machines, and
        nothing in their shape says they are: no child directory repeats."""
        d, roots, _shape = L.host_roots(self.BLUESKY)
        self.assertEqual(d, 0)
        self.assertEqual(roots, [])

    def test_detection_never_reads_a_hostname(self):
        """Rename every host to a meaningless token — the partition must not
        move. This is what 'structural, not a word list' has to mean."""
        ren = {"intranet_server": "aaa", "inet-firewall": "bbb", "mail": "ccc"}
        rels = [r.split("/", 1)[0] and ren[r.split("/")[0]] + "/" + r.split("/", 1)[1]
                for r in self.AIT]
        d, roots, _shape = L.host_roots(rels)
        self.assertEqual(d, 1)
        self.assertEqual(roots, ["aaa", "bbb", "ccc"])

    def test_one_host_is_not_two_because_two_of_its_dirs_share_a_name(self):
        """MEASURED false positive. Inside `gather/intranet_server` both
        `configs/` and `logs/` contain an `apache2/`, and a rule that accepted a
        repeated child NAME split one machine into a two-host bundle — handing
        `configs/` its own 250-row budget. The signature has to repeat whole:
        `configs/` holds 60 directories here and `logs/` holds 4."""
        rels = ["configs/apache2/apache2.conf", "configs/etc/hosts",
                "configs/cron.d/std", "configs/audit/auditd.conf",
                "logs/apache2/access.log", "logs/audit/audit.log",
                "logs/suricata/eve.json", "logs/auth.log"]
        d, roots, _shape = L.host_roots(rels)
        self.assertEqual((d, roots), (0, []))

    def test_a_lone_repeated_subtree_is_not_a_partition(self):
        """Two roots, only one of which has any structure under it: not hosts."""
        d, _roots, _shape = L.host_roots(
            ["a/logs/x.log", "a/logs/y.log", "b/z.log"])
        self.assertEqual(d, 0)

    def test_deeper_partition_is_found_when_the_root_is_a_wrapper(self):
        """Point the tool one level too high and it still finds the hosts."""
        rels = ["gather/" + r for r in self.AIT]
        d, roots, _shape = L.host_roots(rels)
        self.assertEqual(d, 2)
        self.assertTrue(all(r.startswith("gather/") for r in roots))


# ---------------------------------------------------------------------------
# the budget itself
# ---------------------------------------------------------------------------
class PerHostBudget(unittest.TestCase):
    HOSTS = ("alpha", "bravo", "charlie")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v15-budget-")
        self.corpus = os.path.join(self.tmp, "corpus")
        for h in self.HOSTS:
            put(self.corpus, "%s/logs/app.log" % h, logfile(h))
            put(self.corpus, "%s/logs/auth.log" % h, logfile(h + "-auth"))
            for i in range(3):
                put(self.corpus, "%s/configs/etc/conf%d.cfg" % (h, i), conffile(i))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, mod, tag, extra=()):
        out = os.path.join(self.tmp, tag)
        run(mod, self.corpus, out, extra)
        return out

    def test_v14_dilutes_the_budget_across_hosts(self):
        """The defect, stated as a test so the fix has something to beat."""
        out = self._run(V14, "v14", ["--worklist-cap", "30", "--rate-cap", "0"])
        rows = rows_of(os.path.join(out, "worklist.tsv"))
        self.assertLessEqual(len(rows), 30)
        mine = [r for r in rows if "charlie/" in r]
        self.assertLess(len(mine), 15,
                        "v14 is supposed to dilute; if it does not, this whole "
                        "change has no premise")

    def test_each_host_gets_the_whole_budget(self):
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0"])
        for h in self.HOSTS:
            p = os.path.join(out, "worklist-%s.tsv" % h)
            self.assertTrue(os.path.exists(p), "missing per-host worklist %s" % p)
            rows = rows_of(p)
            self.assertLessEqual(len(rows), 30, "%s blew its own cap" % h)
            self.assertGreaterEqual(len(rows), 25,
                                    "%s did not receive a full budget" % h)
            self.assertTrue(all(("%s/" % h) in r for r in rows),
                            "%s's worklist cites another host" % h)

    def test_combined_worklist_is_the_sum_of_the_per_host_ones(self):
        """Nothing may be dropped between the per-host files and the ledger."""
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0"])
        total = rows_of(os.path.join(out, "worklist.tsv"))
        parts = sum(len(rows_of(os.path.join(out, "worklist-%s.tsv" % h)))
                    for h in self.HOSTS)
        self.assertEqual(len(total), parts)

    def test_row_ids_are_unique_across_the_whole_bundle(self):
        """citecheck's ledger closes rows BY ID. Two hosts numbering from g001
        would let one verdict close another host's row."""
        out = self._run(L, "v15", ["--worklist-cap", "30"])
        ids = [r.split("\t")[0] for r in rows_of(os.path.join(out, "worklist.tsv"))]
        self.assertEqual(len(ids), len(set(ids)))

    def test_host_index_lists_every_host_with_its_row_count(self):
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0"])
        p = os.path.join(out, "hosts.tsv")
        self.assertTrue(os.path.exists(p))
        body = [l for l in open(p, encoding="utf-8") if not l.startswith("#")
                and l.strip()]
        self.assertEqual(len(body), len(self.HOSTS))
        names = {l.split("\t")[0] for l in body}
        self.assertEqual(names, set(self.HOSTS))
        for l in body:
            cells = l.rstrip("\n").split("\t")
            host = cells[0]
            rows = rows_of(os.path.join(out, "worklist-%s.tsv" % host))
            self.assertEqual(int(cells[2]), len(rows),
                             "hosts.tsv row count disagrees with the file")

    def test_map_names_the_partition_and_its_cost(self):
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0"])
        body = open(os.path.join(out, "map.txt"), encoding="utf-8").read()
        for h in self.HOSTS:
            self.assertIn("worklist-%s.tsv" % h, body)
        self.assertIn("хостов: 3", body)

    def test_single_host_override_restores_one_budget(self):
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0",
                                   "--single-host"])
        self.assertFalse(os.path.exists(os.path.join(out, "hosts.tsv")))
        self.assertFalse(os.path.exists(os.path.join(out, "worklist-alpha.tsv")))
        self.assertLessEqual(len(rows_of(os.path.join(out, "worklist.tsv"))), 30)

    def test_host_depth_override_forces_a_level(self):
        """--host-depth 2 splits by <host>/logs and <host>/configs — nonsense as
        a host partition, and exactly why it must be reachable: the operator,
        not the heuristic, has the last word."""
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0",
                                   "--host-depth", "2"])
        body = [l for l in open(os.path.join(out, "hosts.tsv"), encoding="utf-8")
                if not l.startswith("#") and l.strip()]
        self.assertEqual(len(body), 6)

    def test_stray_root_files_get_a_bucket_rather_than_disappearing(self):
        put(self.corpus, "MANIFEST.tsv", "a\tb\n" * 40)
        out = self._run(L, "v15", ["--worklist-cap", "30", "--rate-cap", "0"])
        names = [l.split("\t")[0] for l in
                 open(os.path.join(out, "hosts.tsv"), encoding="utf-8")
                 if not l.startswith("#") and l.strip()]
        self.assertEqual(len(names), 4)
        self.assertIn(L.ROOT_BUCKET, names)


# ---------------------------------------------------------------------------
# the regression bar: a single-host corpus must be untouched
# ---------------------------------------------------------------------------
class SingleHostIsByteIdentical(unittest.TestCase):
    """v14 shipped on this bar and v15 ships on it too. A one-host bundle has no
    partition to make, so v15 must produce v14's bytes — not 'equivalent'
    output, the same output."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v15-regress-")
        self.corpus = os.path.join(self.tmp, "corpus")
        put(self.corpus, "MANIFEST.tsv", "name\tsha\n" + "".join(
            "f%d\tdeadbeef%d\n" % (i, i) for i in range(30)))
        put(self.corpus, "evtx/host/Application.jsonl", "".join(
            '{"EventID":%d,"ts":"2026-08-18T%02d:%02d:00","msg":"kind-%03d"}\n'
            % (100 + i % 17, i % 24, i % 60, i % 30) for i in range(120)))
        put(self.corpus, "evtx/incident/Sysmon.jsonl", "".join(
            '{"EventID":%d,"ts":"2026-08-18T%02d:%02d:00","img":"c:\\\\w%d.exe"}\n'
            % (1 + i % 25, i % 24, i % 60, i % 40) for i in range(120)))
        put(self.corpus, "pcap/dns.tsv", "".join(
            "%d\t10.0.0.%d\tq%d.example.test\n" % (i, i % 8, i % 35)
            for i in range(120)))
        put(self.corpus, "toolkit/del.ps1.txt", "Remove-Item -Force x\n" * 5)
        put(self.corpus, "toolkit/ichigo.ps1.txt",
            "".join("Invoke-Thing -Arg %d\n" % i for i in range(30)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bytes(self, mod, tag, name):
        out = os.path.join(self.tmp, tag)
        if not os.path.isdir(out):
            run(mod, self.corpus, out)
        with open(os.path.join(out, name), "rb") as fh:
            return fh.read()

    def test_worklist_is_byte_identical_to_v14(self):
        self.assertEqual(self._bytes(L, "v15", "worklist.tsv"),
                         self._bytes(V14, "v14", "worklist.tsv"))

    def test_axis3_is_byte_identical_to_v14(self):
        self.assertEqual(self._bytes(L, "v15", "axis3.tsv"),
                         self._bytes(V14, "v14", "axis3.tsv"))

    def test_no_per_host_artefacts_are_written(self):
        out = os.path.join(self.tmp, "v15")
        if not os.path.isdir(out):
            run(L, self.corpus, out)
        stray = [f for f in os.listdir(out)
                 if f.startswith("worklist-") or f == "hosts.tsv"]
        self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
