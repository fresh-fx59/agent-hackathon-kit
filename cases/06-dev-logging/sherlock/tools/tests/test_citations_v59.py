#!/usr/bin/env python3
"""v59 deterministic citations — cite by reference, the gate cuts the quote.

Spec: vault docs/run-reports/2026-09-26-v59-deterministic-citations.md.
Evidence: the v58 small test (sherlock-v58-smalltest-dsv4flash-20260926a) spent
~55-60 % of its post-draft repair on checker friction: typed quotes that were
not verbatim, verbatim quotes graded weak (a unique EventRecordID, a bare value
the claim is about), account names harvested out of quotes by the ownership
check, a failing reference printed without its report line, a bare repeat
mention graded no-quote, triagecheck refusing without a reason.

Fixture: fixtures/v59-smalltest/corpus = the real 2 x 20-line small-test corpus.

Run against another package to see the gaps:  SHERLOCK_PKG=v58 python3 <this>
Every test states "the gap is closed", so on v58 the new-behaviour tests are RED.
No network, no LLM.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import importlib.util

SHERLOCK = Path(__file__).resolve().parents[2]
PKG = os.environ.get("SHERLOCK_PKG", "v59")
TOOLS = SHERLOCK / "skills" / PKG / "tools"
FIX = Path(__file__).resolve().parent / "fixtures" / "v59-smalltest"
CORPUS = FIX / "corpus"
SEC = (CORPUS / "Security.jsonl").read_text(encoding="utf-8").splitlines()
SYS = (CORPUS / "System.jsonl").read_text(encoding="utf-8").splitlines()


def load(name):
    path = TOOLS / (name + ".py")
    spec = importlib.util.spec_from_file_location("%s_%s_t59" % (name, PKG), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(name, *args, cwd=None):
    return subprocess.run([sys.executable, str(TOOLS / (name + ".py"))] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=300, cwd=cwd)


class Workspace:
    """/work layout: <root>/corpus (the logs) and <root>/work (report, shown log)."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="v59-cite-"))
        shutil.copytree(CORPUS, self.root / "corpus")
        self.work = self.root / "work"
        self.work.mkdir()
        self.report = self.work / "report.md"

    def show(self, *addresses):
        r = run("cite", *addresses, cwd=self.root)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def cite(self, text, *extra):
        self.report.write_text(text, encoding="utf-8")
        r = run("citecheck", self.report, "--corpus", self.root / "corpus",
                "--require-quote", "--json", *extra, cwd=self.root)
        return json.loads(r.stdout), r

    def verdicts(self, text):
        d, _r = self.cite(text)
        return {(c["path"].split("/")[-1], c["line"]): c["verdict"] for c in d["citations"]}


# ------------------------------------------------------------------ cite.py
class CiteTool(unittest.TestCase):
    def test_corpus_defaults_to_the_fixed_layout(self):
        """v58: `cite.py System.jsonl:1` without --corpus was a usage error (3x)."""
        ws = Workspace()
        out = ws.show("System.jsonl:14")
        self.assertIn("System.jsonl:14", out)

    def test_field_reference_prints_verbatim_slices(self):
        ws = Workspace()
        out = ws.show("Security.jsonl:15#TargetUserName,Status")
        self.assertIn('"TargetUserName":"KATE"', out)
        self.assertIn('"Status":"0xc000006d"', out)
        for piece in ('"TargetUserName":"KATE"', '"Status":"0xc000006d"'):
            self.assertIn(piece, SEC[14])

    def test_unknown_field_is_refused(self):
        ws = Workspace()
        r = run("cite", "Security.jsonl:15#NoSuchField", cwd=ws.root)
        self.assertNotEqual(r.returncode, 0)

    def test_record_id_reference(self):
        ws = Workspace()
        out = ws.show("Security.jsonl@417272#TargetUserName")
        self.assertIn("Security.jsonl:15", out)
        self.assertIn('"TargetUserName":"KATE"', out)
        r = run("cite", "Security.jsonl@999999", cwd=ws.root)
        self.assertNotEqual(r.returncode, 0)

    def test_find_lists_addresses_by_content(self):
        """v58: the model mapped names to line numbers from memory (KATE on 13)."""
        ws = Workspace()
        out = ws.show("--find", "KATE", "--file", "Security.jsonl")
        self.assertIn("Security.jsonl:15", out)
        self.assertNotIn("Security.jsonl:13", out)

    def test_shown_lines_are_recorded(self):
        ws = Workspace()
        ws.show("Security.jsonl:15#TargetUserName")
        shown = (ws.work / "cite-shown.tsv").read_text(encoding="utf-8")
        self.assertIn("Security.jsonl\t15", shown)


# ------------------------------------------------------------------ citecheck
class ReferenceGrading(unittest.TestCase):
    def test_shown_field_reference_is_ok_without_a_typed_quote(self):
        ws = Workspace()
        ws.show("Security.jsonl:15#TargetUserName")
        d, _ = ws.cite("- попытка входа под KATE Security.jsonl:15#TargetUserName\n")
        v = [c["verdict"] for c in d["citations"]]
        self.assertEqual(v, ["ok"], d["citations"])

    def test_reference_to_a_line_never_shown_blocks(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        v = ws.verdicts("- попытка входа под ADMIN Security.jsonl:4#TargetUserName\n")
        self.assertEqual(v[("Security.jsonl", 4)], "not-shown")

    def test_fabricated_record_id_blocks(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        d, _ = ws.cite("- вход KATE Security.jsonl@999999#TargetUserName\n")
        self.assertTrue(d["citations"], d)
        self.assertIn(d["citations"][0]["verdict"], ("missing-record", "out-of-range"))
        self.assertGreater(d["summary"]["total"] - d["summary"]["ok"], 0)

    def test_prose_names_another_line_value_is_wrong_content(self):
        """The v58 KATE-on-13 catch must survive: the quote is now always right,
        so the gate compares the prose with the cited field instead."""
        ws = Workspace()
        ws.show("Security.jsonl:13")
        v = ws.verdicts("- попытка входа под KATE Security.jsonl:13#TargetUserName\n")
        self.assertEqual(v[("Security.jsonl", 13)], "wrong-content")

    def test_generic_word_equal_to_a_non_identity_value_is_not_a_conflict(self):
        """r6 g155: «трассировка обмена WNS» vs Namespace=QOS\\OPTIMALPNG while
        another line's Namespace is «wns» — a generic word, not a wrong line."""
        cc = load("citecheck")
        d = Path(tempfile.mkdtemp(prefix="v59-fc-"))
        f = d / "Push.jsonl"
        f.write_text('{"EventData":{"Verb":"PUT","Namespace":"QOS\\\\OPTIMALPNG 0"}}\n'
                     '{"EventData":{"Verb":"ATH","Namespace":"wns"}}\n', encoding="utf-8")
        pair = '"Namespace":"QOS\\\\OPTIMALPNG 0"'
        self.assertIsNone(cc.field_claim_conflict("N — трассировка обмена WNS", [pair], str(f)))
        g = d / "Sec.jsonl"
        g.write_text('{"EventData":{"TargetUserName":"АДМИН"}}\n{"EventData":{"TargetUserName":"KATE"}}\n',
                     encoding="utf-8")
        self.assertIsNotNone(cc.field_claim_conflict("вход KATE", ['"TargetUserName":"АДМИН"'], str(g)))

    def test_typed_quote_names_another_line_value_is_still_wrong_content(self):
        ws = Workspace()
        v = ws.verdicts('- попытка входа под KATE Security.jsonl:13 «"TargetUserName":"KATE"»\n')
        self.assertEqual(v[("Security.jsonl", 13)], "wrong-content")

    def test_unique_record_id_quote_is_strong(self):
        """v58 graded «"EventRecordID":417258» weak although it names one line."""
        ws = Workspace()
        v = ws.verdicts('- первая запись журнала Security.jsonl:1 «"EventRecordID":417258»\n')
        self.assertEqual(v[("Security.jsonl", 1)], "ok")

    def test_bare_value_the_prose_names_is_strong(self):
        ws = Workspace()
        v = ws.verdicts('- том проверен, CorruptionActionState=0 System.jsonl:14 «"CorruptionActionState":0»\n')
        self.assertEqual(v[("System.jsonl", 14)], "ok")

    def test_bare_eventid_stays_weak(self):
        ws = Workspace()
        v = ws.verdicts('- отказ входа EventID 4625 Security.jsonl:3 «"EventID":4625»\n')
        self.assertEqual(v[("Security.jsonl", 3)], "weak-quote")

    def test_bare_repeat_mention_is_not_a_failure(self):
        """v58 System:1 hunt: a prose re-mention of a quoted address = no-quote (10 runs)."""
        ws = Workspace()
        d, _ = ws.cite('- журнал запущен System.jsonl:1 «"EventRecordID":1»\n'
                       '\nЗапуск (EventID 6009, System.jsonl:1) штатный.\n')
        bad = [c for c in d["citations"] if c["verdict"] not in ("ok", "repeat")]
        self.assertEqual(bad, [], d["citations"])

    def test_range_without_quote_is_a_mention_not_a_failure(self):
        ws = Workspace()
        d, _ = ws.cite("Прочитаны строки Security.jsonl:1–20 целиком.\n")
        verdicts = {c["verdict"] for c in d["citations"]}
        self.assertEqual(verdicts, {"range-mention"}, d["citations"])

    def test_text_output_names_the_report_line(self):
        ws = Workspace()
        ws.report.write_text("# t\n\n- отказ EventID 4625 Security.jsonl:3 «\"EventID\":4625»\n",
                             encoding="utf-8")
        r = run("citecheck", ws.report, "--corpus", ws.root / "corpus", "--require-quote",
                cwd=ws.root)
        line = next(l for l in r.stdout.splitlines() if "weak-quote" in l and "Security.jsonl:3" in l)
        self.assertIn("стр.3", line)


class OwnershipProseOnly(unittest.TestCase):
    def test_account_inside_a_quote_creates_no_obligation(self):
        cc = load("citecheck")
        rep = ("## Находки\n\n### Н-1 · перебор паролей\n\n"
               "- [!PROVEN] отказы входа с внешних адресов Security.jsonl:1 "
               "«\"TargetUserName\":\"ADMINI\",\"TargetDomainName\":\"\"»\n")
        blocks = [("Н-1", 3, 5)]
        o = cc.ownership_check(rep, blocks, corpus=str(CORPUS))
        self.assertFalse(o.get("missing_section"), o)
        self.assertEqual(o.get("missing_rows") or [], [], o)

    def test_account_in_prose_still_needs_a_row(self):
        cc = load("citecheck")
        rep = ("## Находки\n\n### Н-1 · перебор паролей\n\n"
               "- [!PROVEN] посторонний ADMINI пытался войти Security.jsonl:1 "
               "«\"TargetUserName\":\"ADMINI\"»\n")
        o = cc.ownership_check(rep, [("Н-1", 3, 5)], corpus=str(CORPUS))
        self.assertTrue(o.get("missing_section") or o.get("missing_rows"), o)


# ------------------------------------------------------------------ render + check
class RenderAndCheck(unittest.TestCase):
    def test_render_expands_references_verbatim_and_is_idempotent(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        ws.report.write_text("- вход KATE Security.jsonl:15#TargetUserName,IpAddress\n",
                             encoding="utf-8")
        r = run("cite", "--render", ws.report, cwd=ws.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        once = ws.report.read_text(encoding="utf-8")
        self.assertIn('«"TargetUserName":"KATE"»', once)
        self.assertIn('«"IpAddress":"193.142.146.135"»', once)
        run("cite", "--render", ws.report, cwd=ws.root)
        self.assertEqual(once, ws.report.read_text(encoding="utf-8"))

    def test_render_refuses_an_unshown_reference(self):
        ws = Workspace()
        ws.report.write_text("- вход ADMIN Security.jsonl:4#TargetUserName\n", encoding="utf-8")
        r = run("cite", "--render", ws.report, cwd=ws.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("стр.1", r.stderr)
        self.assertNotIn("«", ws.report.read_text(encoding="utf-8"))

    def test_finalize_renders_before_gating(self):
        fin = load("finalize")
        self.assertTrue(hasattr(fin, "render_report"), "finalize must render references")

    def test_check_uses_finalize_flags(self):
        """v58: the model's own checks diverged from finalize (lost --require-quote)."""
        self.assertTrue((TOOLS / "check.py").is_file())
        chk = load("check")
        argv = chk.finalize_argv("work", "corpus")
        self.assertIn("--require-index", argv)
        self.assertTrue(argv[1].endswith("finalize.py"))


# ------------------------------------------------------------------ triagecheck
class TriageReasons(unittest.TestCase):
    def _work(self):
        ws = Workspace()
        row = SYS[13]
        (ws.work / "worklist.tsv").write_text(
            "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
            "g001\tN System.jsonl:14 «\"ThreadID\":124» n=1 — штатная проверка тома\trare\t"
            "System.jsonl:14\tn=1\t%s\n" % row[:200], encoding="utf-8")
        return ws

    def test_missing_rules_file_is_not_a_defect(self):
        ws = self._work()
        r = run("triagecheck", "--worklist", ws.work / "worklist.tsv", "--rules",
                ws.work / "rules.tsv", "--corpus", ws.root / "corpus", "--json", cwd=ws.root)
        d = json.loads(r.stdout)
        self.assertFalse([j for j in d["junk"] if "нет такого файла" in j["что"]], d["junk"])

    def test_failed_inline_reference_says_why(self):
        ws = self._work()
        r = run("triagecheck", "--worklist", ws.work / "worklist.tsv", "--rules",
                ws.work / "rules.tsv", "--corpus", ws.root / "corpus", cwd=ws.root)
        line = next(l for l in r.stdout.splitlines() if "row:g001" in l)
        self.assertIn("weak-quote", line)


if __name__ == "__main__":
    unittest.main(verbosity=1)
