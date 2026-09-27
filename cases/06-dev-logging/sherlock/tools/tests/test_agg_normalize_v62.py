#!/usr/bin/env python3
"""v62 — near-miss aggregate syntax is normalized; prose enum decodes are fixed.

Evidence: citation micro-benchmark 2026-09-26 (vault
docs/run-reports/artifacts/2026-09-26-citation-microbench/, run dirs
citebench-micro-v6{0,1}-r{1,2,3}-20260926 on contabo). 3 of 6 first rounds had
`agg:malformed`; v61-r2 kept `enum:missing_decode` on a value the writer had
decoded in prose. Every string below is copied from those runs (REAL_*), plus
the operator-named near-miss spellings (NEAR_*). The small-test corpus used by
the bench is byte-identical to fixtures/v60-smalltest/corpus.

    SHERLOCK_PKG=v61 python3 test_agg_normalize_v62.py   # red
    python3 test_agg_normalize_v62.py                    # green (v62)
No network, no LLM.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
PKG = os.environ.get("SHERLOCK_PKG", "v62")
TOOLS = SHERLOCK / "skills" / PKG / "tools"
CORPUS = Path(__file__).resolve().parent / "fixtures" / "v60-smalltest" / "corpus"


def load(name):
    spec = importlib.util.spec_from_file_location("%s_%s_v62t" % (PKG, name), TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CC = load("citecheck")


def run(name, *args):
    return subprocess.run([sys.executable, str(TOOLS / (name + ".py"))] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=300)


def aggregates(text):
    tmp = Path(tempfile.mkdtemp(prefix="v62-cc-")) / "report.md"
    tmp.write_text(text, encoding="utf-8")
    r = run("citecheck", tmp, "--corpus", CORPUS, "--require-quote", "--json")
    return json.loads(r.stdout[r.stdout.find("{"):])["aggregates"]["items"]


def autofix(text):
    if not hasattr(CC, "agg_autofix"):
        return text, []
    return CC.agg_autofix(text)


# --- real rejected report lines -------------------------------------------
# v60-r3 line 5, v61-r1 lines 5/14: the citation stops after `= N`.
REAL_NO_CMD = [
    ("- [!PROVEN] Зафиксировано 20 неудачных попыток входа (EventID 4625, Status 0xc000006d) "
     "с 12 различных IP-адресов — агрегат: Security.jsonl · distinct(Event.EventData.IpAddress) = 12", [12]),
    ("- [!PROVEN] 20 неудачных попыток входа (EventID 4625, Status 0xc000006d) зафиксировано в "
     "Security.jsonl — агрегат: Security.jsonl · distinct(Event.EventData.TargetUserName) = 13", [13]),
    # v60-r3 line 14: two citations on one line, neither with a command.
    ("- [!PROVEN] 14 попыток имеют SubStatus 0xc0000064 (учётная запись не существует), 6 попыток — "
     "SubStatus 0xc000006a (неверный пароль) — агрегат: Security.jsonl · "
     "count(Event.EventData.SubStatus=0xc0000064) = 14; агрегат: Security.jsonl · "
     "count(Event.EventData.SubStatus=0xc000006a) = 6", [14, 6]),
]
# v60-r1 line 5: no path, «даёт 20» for «= 20» — ambiguous (which file?).
REAL_NO_PATH = ("- [!PROVEN] В Security.jsonl зафиксировано 20 событий EventID 4625 (неудачный вход) — "
                "агрегат: `count(Event.System.EventID=4625)` даёт 20 совпадений — все строки "
                "Security.jsonl — это неудачные входы через NTLM по сети.")
# cite.py --aggregate refusals in the same runs — genuinely ambiguous.
REAL_CITE_REFUSED = ["count()", "distinct_over(Event.EventData.IpAddress, 0)", "count(line)",
                     "count(TargetUserName)", "count(Event.EventData.Status)"]
# operator-named near-miss spellings; all mean Status=0xc000006d.
NEAR = ['count(Event.EventData.SubStatus ~= "0xc0000064")',
        "count(Event.EventData.SubStatus == '0xC0000064')",
        "count(Event.EventData.SubStatus = 0xC0000064)",
        "count(Event.EventData.SubStatus=«0xc0000064»)",
        "count( Event.EventData.SubStatus==0xc0000064 , Event.System.EventID = 4625 )"]


class RealNoCommandCitationsAreCompleted(unittest.TestCase):
    def test_each_real_line_verifies_after_render(self):
        for line, counts in REAL_NO_CMD:
            text = "## Находки\n\n### Н-1 · перебор\n\n" + line + "\n"
            before = aggregates(text)
            self.assertTrue(all(i["verdict"] == "malformed" for i in before), before)
            new, fixed = autofix(text)
            self.assertEqual(len(fixed), len(counts), (line, fixed))
            after = aggregates(new)
            self.assertEqual([i["verdict"] for i in after], ["ok"] * len(counts), after)
            self.assertEqual([i["actual"] for i in after], counts)
            self.assertEqual(autofix(new), (new, []), "must be idempotent")

    def test_wrong_number_still_blocks_after_render(self):
        line = REAL_NO_CMD[0][0].replace("= 12", "= 11")
        new, _ = autofix(line + "\n")
        self.assertEqual(aggregates(new)[0]["verdict"], "count-mismatch")


class AmbiguousStaysRefusedWithCanonicalSyntax(unittest.TestCase):
    def test_no_path_line(self):
        new, fixed = autofix(REAL_NO_PATH + "\n")
        self.assertEqual(fixed, [])
        it = aggregates(new)[0]
        self.assertEqual(it["verdict"], "malformed")
        self.assertIn("канонический синтаксис", it["detail"])
        self.assertIn("cite.py --file <путь> --aggregate 'count(Event.System.EventID=4625)'", it["detail"])

    def test_cite_refusals(self):
        for q in REAL_CITE_REFUSED:
            r = run("cite", "--corpus", CORPUS, "--file", "Security.jsonl", "--aggregate", q)
            self.assertEqual(r.returncode, 1, q)
            self.assertIn("канонический синтаксис", r.stderr, (q, r.stderr))
        r = run("cite", "--corpus", CORPUS, "--file", "Security.jsonl", "--aggregate", "count(TargetUserName)")
        self.assertIn("distinct(TargetUserName)", r.stderr)


class NearMissPredicatesNormalize(unittest.TestCase):
    def test_same_count_as_canonical(self):
        canon = CC.agg_parse_predicate("count(Event.EventData.SubStatus=0xc0000064)")
        want = CC.agg_evaluate(str(CORPUS / "Security.jsonl"), canon)
        self.assertEqual(want[0], "ok")
        for q in NEAR:
            p = CC.agg_parse_predicate(q)
            self.assertTrue(p.get("normalized"), q)
            if "EventID" not in q:
                self.assertIn(p["filters"][0][1], ("=", "~="))
                self.assertEqual(p["filters"][0][2], "0xc0000064", q)
            self.assertEqual(CC.agg_evaluate(str(CORPUS / "Security.jsonl"), p)[1], want[1], q)

    def test_cite_reports_normalization(self):
        r = run("cite", "--corpus", CORPUS, "--file", "Security.jsonl", "--aggregate", NEAR[1])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("нормализовано", r.stderr)
        self.assertIn("count(Event.EventData.SubStatus=0xc0000064)", r.stdout)

    def test_near_miss_in_report_is_rewritten_and_verifies(self):
        p = CC.agg_parse_predicate("count(Event.EventData.SubStatus=0xc0000064)")
        n = CC.agg_evaluate(str(CORPUS / "Security.jsonl"), p)[1]
        cmd = CC.agg_render_command("Security.jsonl", p)
        line = "- [!PROVEN] x — агрегат: Security.jsonl · %s = %d · `%s`\n" % (NEAR[1], n, cmd)
        new, fixed = autofix(line)
        self.assertEqual(len(fixed), 1)
        self.assertIn("count(Event.EventData.SubStatus=0xc0000064) = %d" % n, new)
        self.assertEqual(aggregates(new)[0]["verdict"], "ok")


class ProseEnumDecodeIsCompleted(unittest.TestCase):
    # v61-r2 line 5 (real): decoded in prose, field not beside the value.
    REAL = ("## Находки\n\n### Н-1 · перебор\n\n"
            "- [!PROVEN] Все 20 записей в Security.jsonl — события аудита с EventID 4625, код статуса для "
            "всех — 0xc000006d (неверное имя пользователя или пароль) — Security.jsonl:1#Status "
            "«\"EventRecordID\":417258» «\"Status\":\"0xc000006d\"»\n")

    def blocks(self, text):
        lines = text.split("\n")
        st = CC.structural_mask(lines)
        return [("Н-%s" % n, lo, hi) for n, lo, hi in CC.finding_blocks(text, st)], st

    def test_real_prose_decode(self):
        b, st = self.blocks(self.REAL)
        kinds = [i["kind"] for i in CC.enum_decode_check(self.REAL, b, st)["items"]]
        self.assertEqual(kinds, ["missing_decode"])
        new, fixed = CC.enum_decode_autofix(self.REAL)
        self.assertEqual(len(fixed), 1, fixed)
        self.assertIn("status=0xc000006d (неверное имя пользователя или пароль)", new)
        b, st = self.blocks(new)
        self.assertEqual(CC.enum_decode_check(new, b, st)["items"], [])
        self.assertEqual(CC.enum_decode_autofix(new), (new, []))

    def test_prose_decode_naming_another_value_is_not_fixed(self):
        wrong = self.REAL.replace("(неверное имя пользователя или пароль)", "(неверный пароль)")
        self.assertEqual(CC.enum_decode_autofix(wrong)[1], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
