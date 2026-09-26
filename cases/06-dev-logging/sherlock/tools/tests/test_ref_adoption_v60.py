#!/usr/bin/env python3
"""v60 — reference adoption: the model must SEE, and be pushed to, `file:N#Field`.

Evidence (vault docs/run-reports/2026-09-26-v60-ref-adoption.md): the v59 small
test (sherlock-v59-smalltest-dsv4flash-20260926a) wrote 0 references and 37
typed «…» quotes. What the model saw:
  * `cite.py file:N --contains X` printed `file:N — «<mid-token window>»` — a
    typed quote — and the model pasted it («Now let me get the cite.py references»);
  * report-format.md (read in the draft) had 112 «» examples and 0 references;
    SKILL.md 70 «» vs 3 references;
  * citecheck graded a verbatim typed quote `ok` in silence, and its `no-quote`
    hint said «оберни её кусок в «…»».
And P2: reportcheck's `label_position` never said where the label goes; the
model spent 7 of 11 reportcheck runs moving `[!PROVEN]` around.

Run against another package to see the gaps:  SHERLOCK_PKG=v59 python3 <this>
No network, no LLM.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
PKG = os.environ.get("SHERLOCK_PKG", "v60")
ROOT = SHERLOCK / "skills" / PKG
TOOLS = ROOT / "tools"
FIX = Path(__file__).resolve().parent / "fixtures" / "v59-smalltest"
CORPUS = FIX / "corpus"
V59_DRAFT = FIX / "v59-draft-final-report.md"


def run(name, *args, cwd=None):
    return subprocess.run([sys.executable, str(TOOLS / (name + ".py"))] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=300, cwd=cwd)


class Workspace:
    def __init__(self, corpus=CORPUS):
        self.root = Path(tempfile.mkdtemp(prefix="v60-ref-"))
        shutil.copytree(corpus, self.root / "corpus")
        self.work = self.root / "work"
        self.work.mkdir()
        self.report = self.work / "report.md"

    def show(self, *addresses):
        r = run("cite", *addresses, cwd=self.root)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def cite(self, text):
        self.report.write_text(text, encoding="utf-8")
        r = run("citecheck", self.report, "--corpus", self.root / "corpus",
                "--require-quote", "--json", cwd=self.root)
        return json.loads(r.stdout), r

    def cite_text(self, text):
        self.report.write_text(text, encoding="utf-8")
        return run("citecheck", self.report, "--corpus", self.root / "corpus",
                   "--require-quote", cwd=self.root)


def first_line(out):
    return out.strip().splitlines()[0].strip()


# ------------------------------------------------------------------ cite.py prints refs
class CitePrintsReferences(unittest.TestCase):
    def test_contains_prints_a_field_reference_not_a_window(self):
        """v59 printed `Security.jsonl:3 — «onPackageName":"NTLM",...LmPackageNam»`."""
        ws = Workspace()
        out = ws.show("Security.jsonl:15", "--contains", "KATE")
        self.assertEqual(first_line(out), "Security.jsonl:15#TargetUserName", out)

    def test_contains_on_an_address_picks_the_address_field(self):
        ws = Workspace()
        out = ws.show("Security.jsonl:13", "--contains", "91.220.163.170")
        self.assertEqual(first_line(out), "Security.jsonl:13#IpAddress", out)

    def test_field_reference_first_line_is_the_bare_paste_line(self):
        ws = Workspace()
        out = ws.show("Security.jsonl:15#TargetUserName,Status")
        self.assertEqual(first_line(out), "Security.jsonl:15#TargetUserName,Status", out)
        self.assertIn('"TargetUserName":"KATE"', out)       # still shows the evidence

    def test_bare_address_prints_a_reference_with_fields(self):
        ws = Workspace()
        out = ws.show("Security.jsonl:15")
        self.assertRegex(first_line(out), r"^Security\.jsonl:15#EventRecordID,[A-Za-z]", out)
        self.assertNotIn("«", first_line(out))

    def test_find_prints_the_field_that_holds_the_value(self):
        ws = Workspace()
        out = ws.show("--find", "KATE", "--file", "Security.jsonl")
        self.assertEqual(first_line(out), "Security.jsonl:15#TargetUserName", out)


# ------------------------------------------------------------------ citecheck pushes refs
TYPED = '- [!PROVEN] попытка входа под KATE — Security.jsonl:15 «"TargetUserName":"KATE"»\n'


class TypedQuoteIsNotAcceptedSilently(unittest.TestCase):
    def test_verbatim_typed_quote_on_a_json_line_blocks_with_the_exact_ref(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        d, r = ws.cite(TYPED)
        v = [c["verdict"] for c in d["citations"]]
        self.assertEqual(v, ["typed-quote"], d["citations"])
        self.assertEqual(d["citations"][0].get("suggest"), "Security.jsonl:15#TargetUserName")
        self.assertNotEqual(r.returncode, 0)

    def test_text_output_names_the_ref_to_paste(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        r = ws.cite_text(TYPED)
        self.assertIn("Security.jsonl:15#TargetUserName", r.stdout)
        self.assertIn("typed-quote", r.stdout)

    def test_the_suggested_ref_passes(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        d, r = ws.cite("- [!PROVEN] попытка входа под KATE — Security.jsonl:15#TargetUserName\n")
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ok"], d["citations"])

    def test_typed_quote_on_a_wrong_line_suggests_find(self):
        """v59 draft: «TargetUserName":"KATE"» at Security.jsonl:17 (KATE is 15)."""
        ws = Workspace()
        ws.show("Security.jsonl:17")
        r = ws.cite_text('- [!PROVEN] вход KATE — Security.jsonl:17 «"TargetUserName":"KATE"»\n')
        self.assertIn("cite.py --find KATE --file Security.jsonl", r.stdout)

    def test_plain_text_lines_keep_typed_quotes(self):
        """Guard: a non-JSON line has no fields — a typed quote stays the only form."""
        tmp = Path(tempfile.mkdtemp(prefix="v60-plain-"))
        (tmp / "app.log").write_text("2024-01-01 12:00:00 ERROR payment gateway timeout after 30s\n",
                                     encoding="utf-8")
        ws = Workspace(tmp)
        ws.show("app.log:1")
        d, _r = ws.cite("- [!PROVEN] таймаут шлюза — app.log:1 «payment gateway timeout after 30s»\n")
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ok"], d["citations"])


class RenderWritesFieldsAndConverts(unittest.TestCase):
    def test_bare_reference_renders_with_named_fields(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        ws.report.write_text("- [!PROVEN] вход KATE — Security.jsonl:15\n", encoding="utf-8")
        run("cite", "--render", ws.report, cwd=ws.root)
        text = ws.report.read_text(encoding="utf-8")
        self.assertIn("Security.jsonl:15#EventRecordID", text)
        d, _ = ws.cite(text)
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ok"], d["citations"])

    def test_render_converts_a_verbatim_typed_quote_and_logs_it(self):
        ws = Workspace()
        ws.show("Security.jsonl:15")
        ws.report.write_text(TYPED, encoding="utf-8")
        r = run("cite", "--render", ws.report, cwd=ws.root)
        text = ws.report.read_text(encoding="utf-8")
        self.assertIn("Security.jsonl:15#TargetUserName", text)
        self.assertIn("заменено", r.stdout + r.stderr)
        d, _ = ws.cite(text)
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ok"], d["citations"])


class V59DraftReplay(unittest.TestCase):
    """Every typed quote of the real v59 draft is refused with a suggestion."""

    def test_no_typed_quote_on_a_json_line_passes_silently(self):
        ws = Workspace()
        text = V59_DRAFT.read_text(encoding="utf-8")
        for n in range(1, 21):
            ws.show("Security.jsonl:%d" % n, "System.jsonl:%d" % n)
        d, _r = ws.cite(text)
        lines = text.splitlines()
        cov = next(i for i, l in enumerate(lines, 1) if l.startswith("## Покрытие"))
        end = next(i for i, l in enumerate(lines, 1) if i > cov and l.startswith("## "))
        typed_ok = [c for c in d["citations"]
                    if c["verdict"] == "ok" and not c.get("ref") and "«" in c["claim"]
                    and not cov < c["report_line"] < end]   # coverage rows name a file
        self.assertEqual(typed_ok, [], [c["citation"] for c in typed_ok])
        sugg = [c for c in d["citations"] if c["verdict"] == "typed-quote"]
        self.assertTrue(sugg)
        for c in sugg:
            self.assertRegex(c.get("suggest") or "", r"^(Security|System)\.jsonl:\d+(#\w+(,\w+)*)?$")


# ------------------------------------------------------------------ docs teach refs
DOCS = ["SKILL.md", "reference/report-format.md", "reference/draft-and-verify.md",
        "reference/tools.md"]
TYPED_EXAMPLE = re.compile(r"(\.jsonl|файл|путь|\.log):(\d+|строка|N)\s*(—\s*)?«")
TYPED_BEFORE = re.compile(r"«(цитата|дословная цитата)»\s*(файл|путь):строка")


class DocsTeachReferences(unittest.TestCase):
    def test_no_doc_shows_a_typed_quote_after_a_line_citation(self):
        bad = []
        for rel in DOCS:
            for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if "наблюдение" in line or "\t" in line:
                    continue       # coverage rows and worklist/rules cells keep «…»
                if TYPED_EXAMPLE.search(line) or TYPED_BEFORE.search(line):
                    bad.append("%s:%d %s" % (rel, i, line.strip()[:100]))
        self.assertEqual(bad, [], "\n".join(bad[:15]))

    def test_report_format_shows_references(self):
        text = (ROOT / "reference" / "report-format.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(re.findall(r"\.jsonl:\d+#[A-Za-z]", text)), 5)


# ------------------------------------------------------------------ reportcheck says where
class LabelPositionSaysWhere(unittest.TestCase):
    def test_bare_label_line_gets_the_exact_fix(self):
        tmp = Path(tempfile.mkdtemp(prefix="v60-rc-"))
        rep = tmp / "report.md"
        rep.write_text("## Находки\n\n### Н-1 · x\n\n"
                       "[!PROVEN] System.jsonl:1#ProcessID — EventLog, ProcessID=0\n",
                       encoding="utf-8")
        r = run("reportcheck", rep)
        self.assertIn("label_position".upper(), r.stdout.upper())
        self.assertIn("- [!PROVEN] System.jsonl:1#ProcessID", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
