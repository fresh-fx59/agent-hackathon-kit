#!/usr/bin/env python3
"""v61 — cut repair churn without weakening a gate.

Evidence (vault docs/run-reports/2026-09-26-v61-repair-friction.md): the v60
small test ran out of its 1800 s draft limit in repair — 69 calls, 8.13M
tokens, ~118k context per call. Blocking went 34→27→23→3→5. Replaying its five
report versions (fixtures/v60-smalltest/, rebuilt from the draft stream) shows
most of that was format friction, not content:
  * 16 assertion_unlabelled on the ownership table (13 rows) and worklist lines
    (3) — sections that other gates already grade row by row;
  * label_position + assertion_unlabelled on «[!INFERENCE] text» (a label at
    the start of a paragraph — unambiguous);
  * 3 enum decodes the tool itself spelled out («напиши …»);
  * 2 command-mismatch where the command WAS the rendering but prose followed
    it on the same line;
  * `rollover --required-only --cite Security.jsonl:1` printed an empty table;
  * check.py hid citecheck sub-gates behind «подробности: …», so the model ran
    citecheck by hand 13 times.
The protections stay: zero-match, absolute_uncited, labels on prose findings,
wrong decodes, command must equal the rendering.

    SHERLOCK_PKG=v60 python3 test_repair_friction_v61.py   # red
    python3 test_repair_friction_v61.py                    # green (v61)
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
PKG = os.environ.get("SHERLOCK_PKG", "v61")
TOOLS = SHERLOCK / "skills" / PKG / "tools"
FIX = Path(__file__).resolve().parent / "fixtures" / "v60-smalltest"
CORPUS = FIX / "corpus"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def load(name):
    spec = importlib.util.spec_from_file_location("%s_%s_v61t" % (PKG, name), TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(name, *args):
    return subprocess.run([sys.executable, str(TOOLS / (name + ".py"))] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=300)


def reportcheck(text):
    tmp = Path(tempfile.mkdtemp(prefix="v61-rc-")) / "report.md"
    tmp.write_text(text, encoding="utf-8")
    return json.loads(run("reportcheck", tmp, "--json").stdout)


def aggregates(text):
    tmp = Path(tempfile.mkdtemp(prefix="v61-cc-")) / "report.md"
    tmp.write_text(text, encoding="utf-8")
    r = run("citecheck", tmp, "--corpus", CORPUS, "--require-quote", "--json")
    return json.loads(r.stdout[r.stdout.find("{"):])["aggregates"]["items"]


OWNERSHIP = ("## Принадлежность учётных записей\n\n"
             "| учётная запись | первое появление | path:line#Поле | как | вывод | раньше |\n"
             "|---|---|---|---|---|---|\n"
             "| IPSERVER\\ADMINI | 2021-06-01T18:36:04Z | Security.jsonl:1#TargetUserName | неизвестно | не определяется | — |\n"
             "| IPSERVER\\Test | 2021-06-01T18:36:23Z | Security.jsonl:3#TargetUserName | неизвестно | не определяется | — |\n")
WORKLIST = ("## Разбор рабочего списка\n\n"
            "- g002 — N (System.jsonl:1, EventID 6009 — нормальная запись о версии ОС)\n")
PROSE = ("## Находки\n\n### К-2 · ProcessID=0\n\n"
         "Строка System.jsonl:1#ProcessID имеет ProcessID=0 — норма для загрузки.\n")


class SectionsGradedElsewhereNeedNoLabel(unittest.TestCase):
    def test_ownership_rows_and_worklist_lines_are_not_unlabelled(self):
        d = reportcheck(OWNERSHIP + "\n" + WORKLIST)
        self.assertEqual(d["counts"].get("assertion_unlabelled", 0), 0, d["defects"])

    def test_prose_finding_still_needs_a_label(self):
        d = reportcheck(PROSE)
        self.assertEqual(d["counts"].get("assertion_unlabelled", 0), 1)


class LeadingLabelIsAutofixed(unittest.TestCase):
    def test_leading_label_becomes_list_item_and_passes(self):
        fin = load("finalize")
        self.assertTrue(hasattr(fin, "autofix_labels"), "no autofix in finalize's render step")
        text = PROSE.replace("Строка System", "[!INFERENCE] Строка System")
        self.assertIn("LABEL_POSITION", run_rc_text(text))
        new, fixed = fin.autofix_labels(text)
        self.assertEqual(len(fixed), 1)
        self.assertIn("- [!INFERENCE] Строка System.jsonl:1#ProcessID", new)
        d = reportcheck(new)
        self.assertEqual(d["counts"].get("label_position", 0) + d["counts"].get("assertion_unlabelled", 0), 0)

    def test_verdict_and_code_are_not_touched(self):
        fin = load("finalize")
        text = "## ВЕРДИКТ\n\n[!INFERENCE] атаковали, но не доказано — Security.jsonl:1#IpAddress\n" \
               "```\n[!PROVEN] x\n```\n"
        self.assertEqual(fin.autofix_labels(text)[1], [])


def run_rc_text(text):
    tmp = Path(tempfile.mkdtemp(prefix="v61-rc-")) / "report.md"
    tmp.write_text(text, encoding="utf-8")
    return run("reportcheck", tmp).stdout.upper()


class EnumDecodeIsInsertedFromTheTable(unittest.TestCase):
    TEXT = ("## Находки\n\n### Н-1 · перебор\n\n"
            "- [!PROVEN] проверено по SubStatus — 14 попыток на несуществующие учётные записи "
            "(SubStatus=0xc0000064) — Security.jsonl:3#SubStatus «\"SubStatus\":\"0xc0000064\"»\n")

    def test_decode_inserted_quote_untouched(self):
        cc = load("citecheck")
        self.assertTrue(hasattr(cc, "enum_decode_autofix"))
        new, fixed = cc.enum_decode_autofix(self.TEXT)
        self.assertEqual(len(fixed), 1, fixed)
        self.assertIn("SubStatus=0xc0000064 (нет такой учётной записи)", new)
        self.assertIn("«\"SubStatus\":\"0xc0000064\"»", new)
        again, fixed2 = cc.enum_decode_autofix(new)
        self.assertEqual((again, fixed2), (new, []), "must be idempotent")

    def test_wrong_decode_is_not_rewritten(self):
        cc = load("citecheck")
        wrong = self.TEXT.replace("(SubStatus=0xc0000064)", "(SubStatus=0xc0000064 (неверный пароль))")
        self.assertEqual(cc.enum_decode_autofix(wrong)[1], [])


class AggregateCommandEndsAtItsBacktick(unittest.TestCase):
    CMD = "grep -c -F -- 'Rdesktop' 'Security.jsonl'"

    def test_prose_after_command_is_not_a_mismatch(self):
        it = aggregates("### Н-1 · x\n\nодна запись — агрегат: Security.jsonl · count(line~=Rdesktop) = 1 · `%s`. "
                        "Единичное указание не образует паттерна.\n" % self.CMD)
        self.assertEqual(it[0]["verdict"], "ok", it)

    def test_wrong_command_still_mismatches(self):
        it = aggregates("агрегат: Security.jsonl · count(line~=Rdesktop) = 1 · `grep -c Rdesktop Security.jsonl`. x\n")
        self.assertEqual(it[0]["verdict"], "command-mismatch")

    def test_zero_match_still_blocks(self):
        it = aggregates("агрегат: Security.jsonl · count(Event.System.EventID=4624) = 0 · `x`\n")
        self.assertEqual(it[0]["verdict"], "zero-match")


class RolloverAcceptsAddressCites(unittest.TestCase):
    def test_required_only_with_line_address(self):
        r = run("rollover", "--corpus", CORPUS, "--report", "--required-only",
                "--cite", "Security.jsonl:1", "--cite", "System.jsonl:1#ProcessID")
        self.assertIn("| Security.jsonl | Security |", r.stdout)
        self.assertIn("| System.jsonl | System |", r.stdout)


class CheckPrintsWhatToDo(unittest.TestCase):
    def test_window_defect_without_cross_is_shown(self):
        ck = load("check")
        self.assertTrue(hasattr(ck, "_blocking_lines"), "check.py hides sub-gate details")
        block = ("ОТЧЁТНЫЕ НАБЛЮДЕНИЯ v26: 1 блокирующих дефектов\n"
                 "РАСШИФРОВКА ПЕРЕЧИСЛЕНИЙ (6a): 0 блокирующих, таблица 72 пар\n"
                 "  ОКНО ЗАПИСЕЙ v38: 1 блокирующих дефектов\n"
                 "    на диске: файлов 2\n    не объявлены окна: Security.jsonl | Security\n")
        out = "\n".join(ck._blocking_lines(block))
        self.assertIn("не объявлены окна: Security.jsonl | Security", out)
        self.assertNotIn("РАСШИФРОВКА", out)

    def test_absolute_gets_a_census_line_that_verifies(self):
        ck = load("check")
        hint = ck.census_hint("«Ни одна попытка» — «Ни одна попытка не завершилась успешно "
                              "(EventID 4624 в корпусе отсутствует).»", str(CORPUS))
        line = next(h for h in hint if "Security.jsonl" in h)
        agg = line.split("): ", 1)[1].split("   [", 1)[0]
        self.assertIn("4625×20", line)
        it = aggregates("### Н-1 · x\n\nНи одна попытка не удалась.\n> " + agg + "\n")
        self.assertEqual(it[0]["verdict"], "ok", it)
        d = reportcheck("## Находки\n\n### Н-1 · x\n\n- [!PROVEN] Ни одна попытка не завершилась успешно "
                        "(EventID 4624 нет) — Security.jsonl:1#EventID\n> " + agg + "\n")
        self.assertEqual(d["counts"].get("absolute_uncited", 0), 0)

    def test_absolute_without_aggregate_still_blocks(self):
        d = reportcheck("## Находки\n\n### Н-1 · x\n\n- [!PROVEN] Ни одна попытка не завершилась "
                        "успешно (EventID 4624 нет) — Security.jsonl:1#EventID\n")
        self.assertEqual(d["counts"].get("absolute_uncited", 0), 1)


class ReplayOfTheV60SmallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import replay_v60_smalltest as rp
        cls.res = rp.replay(PKG)

    def test_first_check_only_real_content_and_proof_left(self):
        first = self.res[0]["kinds"]
        friction = {k: first.get(k, 0) for k in ("label_position", "agg:command-mismatch")}
        self.assertLessEqual(first.get("assertion_unlabelled", 0), 3, first)
        self.assertEqual(sum(friction.values()), 0, first)
        self.assertLessEqual(self.res[0]["blocking"], 17, first)

    def test_last_check_has_no_label_defects(self):
        last = self.res[-1]["kinds"]
        self.assertEqual(last.get("label_position", 0) + last.get("assertion_unlabelled", 0), 0, last)


if __name__ == "__main__":
    unittest.main(verbosity=1)
