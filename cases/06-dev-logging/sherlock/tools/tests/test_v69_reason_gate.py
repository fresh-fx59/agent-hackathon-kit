"""Provider-free v69 tests: the stock-reason gate (changes 1-5).

1 live #Rn rows are exempt, named rows keep max(3, 5 %)
2 triagecheck human render prints the repeated-reason block
3 worklist.py verdict refuses an over-limit batch, writes nothing
4 checkpoint.py handoff (full, triage) refuses while triagecheck blocks
5 handoff prints the RECEIPT line first
Regression: the v68 full run's worklist + rules: 159 -> 16.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
PKG = SHERLOCK / "skills" / "v69"
TOOLS = PKG / "tools"
FIX = Path(__file__).resolve().parent / "fixtures" / "v68-full-reasons"
HARNESS = SHERLOCK / "measure" / "citebench" / "harness" / "v2"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TC = load(TOOLS / "triagecheck.py", "tc69")


def run(tool, *args, stdin=None, pkg=PKG):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(pkg / "tools" / tool), *args],
                          input=stdin, capture_output=True, text=True, env=env)


HEAD = "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"


def mkwork(verdicts, rules=None, axis="cat", corpus=False):
    d = Path(tempfile.mkdtemp(prefix="v69rg-"))
    w = d / "work"
    w.mkdir()
    if corpus:
        (d / "corpus").mkdir()
    body = HEAD
    for i, v in enumerate(verdicts, 1):
        body += "g%03d\t%s\t%s\ta.log:%d\tn=1\talpha %d\n" % (i, v, axis, i, i)
    (w / "worklist.tsv").write_text(body, encoding="utf-8")
    if rules:
        (w / "rules.tsv").write_text(rules, encoding="utf-8")
    return d, w


RULE = "R1\tось=cat\tN\tтокен<=64\tоднократные события без последствий для доступа\n"
SAME = "N a.log:%d «alpha» n=1 фон"


class Exempt(unittest.TestCase):
    def rows(self, n_tagged, n_named):
        out = []
        for i in range(n_tagged):
            out.append({"id": "t%d" % i, "verdict": "N #R1 фон"})
        for i in range(n_named):
            out.append({"id": "n%d" % i, "verdict": "N a.log:%d «x» фон" % i})
        return out

    def test_tagged_rows_exempt_named_rows_limited(self):
        rows = self.rows(40, 5)                    # limit = max(3, 2) = 3
        self.assertEqual(len(TC.stock_reasons(rows)[0]["rows"]), 45)
        got = TC.stock_reasons(rows, exempt={r["id"] for r in rows if "#R1" in r["verdict"]})
        self.assertEqual([len(g["rows"]) for g in got], [5])

    def test_named_limit_unchanged(self):
        rows = self.rows(0, 3)
        self.assertEqual(TC.stock_reasons(rows), [])
        self.assertEqual(len(TC.stock_reasons(self.rows(0, 4))), 1)

    def test_live_liveness(self):
        d, w = mkwork(["N #R1 фон", "N #R1 фон", "N #R9 фон", "?"], RULE)
        rows = TC.read_worklist(str(w / "worklist.tsv"))
        live = TC.live_rule_row_ids(rows, str(w / "rules.tsv"))
        self.assertEqual(live, {"g001", "g002"})   # R9 does not exist; g004 open
        d2, w2 = mkwork(["N #R1 фон"], RULE, axis="odd")   # rule no longer covers
        rows = TC.read_worklist(str(w2 / "worklist.tsv"))
        self.assertEqual(TC.live_rule_row_ids(rows, str(w2 / "rules.tsv")), set())


class Fixture(unittest.TestCase):
    """The v68 full run: 159 blocking -> 16."""

    def analyse(self, pkg=PKG):
        empty = tempfile.mkdtemp(prefix="v69-emptycorpus-")
        r = run("triagecheck.py", "--worklist", str(FIX / "worklist.tsv"),
                "--rules", str(FIX / "rules.tsv"), "--corpus", empty, "--json",
                pkg=pkg)
        # triagecheck may print a checkpoint line to stderr only; stdout is JSON
        return json.loads(r.stdout), r.stdout

    def test_v69_is_16(self):
        d, _ = self.analyse()
        self.assertEqual(d["totals"]["строк с шаблонным основанием"], 16)
        self.assertEqual([len(g["rows"]) for g in d["stock_reasons"]], [16])
        self.assertEqual(d["buckets"]["по правилу"], 143)
        self.assertEqual(d["stock_limit"], 12)

    def test_v68_was_159(self):
        v68 = SHERLOCK / "skills" / "v68"
        if not v68.exists():
            self.skipTest("v68 tree absent")
        d, _ = self.analyse(v68)
        self.assertEqual(d["totals"]["строк с шаблонным основанием"], 159)

    def test_human_render_block(self):
        empty = tempfile.mkdtemp(prefix="v69-emptycorpus-")
        r = run("triagecheck.py", "--worklist", str(FIX / "worklist.tsv"),
                "--rules", str(FIX / "rules.tsv"), "--corpus", empty)
        out = r.stdout
        self.assertIn("ОДИНАКОВОЕ ОСНОВАНИЕ", out)
        self.assertIn("строк: 16 (лимит 12)", out)
        self.assertIn("g030, g038, g056, g092, g099, g105, g122, g142, g161, g176", out)
        self.assertIn("give each row its own reason, or close them through one bulk "
                      "rule with a specific why", out)

    @unittest.skipUnless((HARNESS / "triagegate.py").exists(), "harness v2 absent")
    def test_harness_triagegate_same_16(self):
        tmp = Path(tempfile.mkdtemp(prefix="v69-hg-"))
        work = tmp / "work"
        work.mkdir()
        for n in ("worklist.tsv", "rules.tsv"):
            shutil.copy(FIX / n, work / n)
        corpus = tmp / "corpus"
        corpus.mkdir()
        (tmp / "run").mkdir()
        r = subprocess.run([sys.executable, str(HARNESS / "triagegate.py"),
                            "--work", str(work), "--corpus", str(corpus),
                            "--package", str(PKG), "--run-dir", str(tmp / "run")],
                           capture_output=True, text=True)
        res = json.loads(r.stdout)["checks"]["triagecheck"]["result"]
        self.assertEqual(res["totals"]["строк с шаблонным основанием"], 16)


class WorklistVerdict(unittest.TestCase):
    def setUp(self):
        self.d, self.w = mkwork(["?"] * 20, RULE, corpus=True)
        self.corpus = self.d / "corpus"
        (self.corpus / "a.log").write_text("".join("alpha %d\n" % i for i in range(1, 21)))

    def verdict(self, pairs):
        return run("worklist.py", "verdict", "--work", str(self.w), "--corpus",
                   str(self.corpus), "--from-stdin",
                   stdin="".join("%s\t%s\n" % p for p in pairs))

    def test_refuses_over_limit_batch_and_writes_nothing(self):
        before = (self.w / "worklist.tsv").read_bytes()
        ids = ["g%03d" % i for i in range(1, 6)]          # limit 3, five rows
        r = self.verdict([(i, "N a.log:%d «alpha %d» фон" % (int(i[1:]), int(i[1:]))) for i in ids])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("give each row its own reason", r.stderr + r.stdout)
        self.assertIn("g001", r.stderr + r.stdout)
        self.assertEqual((self.w / "worklist.tsv").read_bytes(), before)
        self.assertFalse(list(self.w.glob("worklist.provenance.jsonl")))

    def test_within_limit_and_distinct_reasons_pass(self):
        r = self.verdict([("g%03d" % i, "N a.log:%d «alpha %d» фон" % (i, i)) for i in range(1, 4)])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.verdict([("g%03d" % i, "N a.log:%d «alpha %d» причина %s" % (i, i, "x" * i)) for i in range(4, 9)])
        # distinct only by letters x*i -> residuals differ
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_accumulated_batches_refused(self):
        self.assertEqual(self.verdict([("g%03d" % i, "N a.log:%d «alpha %d» фон" % (i, i)) for i in range(1, 4)]).returncode, 0)
        r = self.verdict([("g004", "N a.log:4 «alpha 4» фон")])
        self.assertNotEqual(r.returncode, 0)

    def test_rule_tagged_rows_exempt(self):
        r = self.verdict([("g%03d" % i, "N #R1 фон") for i in range(1, 11)])
        self.assertEqual(r.returncode, 0, r.stderr)


class Handoff(unittest.TestCase):
    def handoff(self, w, *extra):
        return run("checkpoint.py", "handoff", "--work", str(w), "--done", "triage", *extra)

    def stage(self, w):
        return json.loads((w / "checkpoint.json").read_text())["stage"]

    def test_refuses_stock_reasons_no_stage_change(self):
        d, w = mkwork([SAME % i for i in range(1, 11)])
        run("checkpoint.py", "init", "--work", str(w))
        r = self.handoff(w)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("triagecheck blocking", r.stdout)
        self.assertIn("give each row its own reason", r.stdout)
        self.assertEqual(self.stage(w), "triage")
        self.assertNotIn("RECEIPT id=", r.stdout)
        self.assertFalse((w / "handoff.txt").exists())

    def test_rule_tagged_pass_and_receipt_first(self):
        d, w = mkwork(["N #R1 фон"] * 10, RULE)
        run("checkpoint.py", "init", "--work", str(w))
        r = self.handoff(w)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("RECEIPT id="), r.stdout[:80])
        self.assertEqual(self.stage(w), "draft")
        # truncation cannot drop it
        ck = load(TOOLS / "checkpoint.py", "ck69")
        head = r.stdout[:200]
        self.assertTrue(ck.RECEIPT_LINE_RE.search(head))
        self.assertTrue(ck.RECEIPT_META_RE.search(r.stdout[:600]))

    def test_full_gate_with_corpus_refuses_other_classes(self):
        # corpus present: the whole triagecheck runs, not only the stock class
        d, w = mkwork(["N #R1 фон"] * 10, RULE, axis="odd", corpus=True)
        run("checkpoint.py", "init", "--work", str(w))
        r = self.handoff(w)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("без опоры", r.stdout)
        self.assertEqual(self.stage(w), "triage")

    def test_partial_is_not_gated(self):
        d, w = mkwork([SAME % i for i in range(1, 11)])
        run("checkpoint.py", "init", "--work", str(w))
        r = self.handoff(w, "--partial")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("RECEIPT id="))

    def test_json_mode_receipt_first(self):
        d, w = mkwork(["N #R1 фон"] * 4, RULE)
        run("checkpoint.py", "init", "--work", str(w))
        r = self.handoff(w, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("RECEIPT id="))

    def test_journalcheck_finds_receipt_in_new_order(self):
        d, w = mkwork(["N #R1 фон"] * 4, RULE)
        run("checkpoint.py", "init", "--work", str(w))
        out = self.handoff(w).stdout
        use = {"id": "u1", "name": "bash",
               "input": {"command": "python3 checkpoint.py handoff --work w --done triage | head -c 900"}}
        for jc_path in (TOOLS / "journalcheck.py", HARNESS / "journalcheck.py"):
            if not jc_path.exists():
                continue
            if jc_path.parent == HARNESS:
                continue                       # harness wraps the package's
            jc = load(jc_path, "jc69")
            got = jc.printed_receipts([("s", 1, use)], {"u1": out[:900]})
            self.assertEqual(len(got), 1)
            entry = next(iter(got.values()))
            self.assertEqual(len(entry["receipt"][0]), 64)
        if (HARNESS / "journalcheck.py").exists():
            hj = load(HARNESS / "journalcheck.py", "hj69")
            jc = hj.load_pkg(str(PKG / "tools"))
            if jc is not None:
                got = jc.printed_receipts([("s", 1, use)], {"u1": out[:900]})
                self.assertIsNotNone(hj.receipt_pair(next(iter(got.values()))))


if __name__ == "__main__":
    unittest.main()
