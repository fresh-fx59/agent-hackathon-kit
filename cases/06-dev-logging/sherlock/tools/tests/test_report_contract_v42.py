#!/usr/bin/env python3
"""The operator's report contract is a GATE, not a hope — v42.

The paid run `20260827T173511Z-v41` exited 0 on citecheck, statecheck and
triagecheck and was called accepted. The 2026-08-28 review found five explicit,
written requirements from the customer's own prompt that the delivered report
broke, and that no gate had ever looked at: labels on assertions, an inventory
with the origin of each entry, a section for what the logs lack, ВЕРДИКТ as the
LAST section, and one of exactly three verdict words with line references.

Every defect below gets a failing case and a passing case, and the regression
test rebuilds the shape of the report that was actually delivered.

    python3 -m pytest tools/tests/test_report_contract_v42.py -q
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
V42 = SHERLOCK / "skills" / "v42"
CONTRACT = V42 / "reference" / "report-contract.corporate.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RC = load("reportcheck_v42", V42 / "tools" / "reportcheck.py")


def contract():
    return RC.load_contract(str(CONTRACT))


def defects(text):
    return sorted({d["defect"] for d in RC.check(text, contract())})


FINDINGS = "\n".join([
    "## Находки",
    "",
    "- PROVEN: вход по RDP с 203.0.113.7 — Security.jsonl:412 «LogonType=10».",
    "- REPORTED: служба 3proxy установлена — System.jsonl:263 «3proxy».",
    "- INFERENCE: закрепление удалось — Security.jsonl:412, System.jsonl:263.",
])

INVENTORY = "\n".join([
    "## Инвентарь наблюдаемых величин",
    "",
    "- 203.0.113.7 — адрес — Security.jsonl:412",
    "- svc-backup — имя учётной записи — Security.jsonl:412",
    "- C:\\Windows\\Temp\\3proxy.exe — путь — System.jsonl:263",
    "- d41d8cd98f00b204e9800998ecf8427e — хеш — System.jsonl:263",
])

MISSING = "\n".join([
    "## Чего не хватает в логах",
    "",
    "- Нет сетевых логов периметра, поэтому источник входа не подтверждается.",
    "- Экспорт Security начинается позже первого события.",
])

VERDICT = "\n".join([
    "## ВЕРДИКТ",
    "",
    "скомпрометирована — Security.jsonl:412, System.jsonl:263.",
])


def good(findings=FINDINGS, inventory=INVENTORY, missing=MISSING, verdict=VERDICT):
    parts = [p for p in ("# Отчёт", findings, inventory, missing, verdict) if p]
    return "\n\n".join(parts) + "\n"


class ContractIsSatisfiable(unittest.TestCase):
    def test_a_conforming_report_passes(self):
        self.assertEqual(defects(good()), [])

    def test_the_shipped_default_contract_is_the_one_used(self):
        self.assertEqual(RC.DEFAULT_CONTRACT, str(CONTRACT))
        self.assertTrue(CONTRACT.is_file())


class EachDefectHasItsOwnName(unittest.TestCase):
    def test_assertion_unlabelled(self):
        bad = good(findings="\n".join([
            "## Находки",
            "",
            "- Вход по RDP с 203.0.113.7 — Security.jsonl:412 «LogonType=10».",
        ]))
        self.assertIn("assertion_unlabelled", defects(bad))
        self.assertNotIn("assertion_unlabelled", defects(good()))

    def test_an_uncited_sentence_needs_no_label(self):
        # The contract labels ASSERTIONS — a line carrying a citation. Prose
        # that claims nothing checkable must not be dragged into the gate.
        ok = good(findings=FINDINGS + "\n\nНиже разбор по машинам.\n")
        self.assertEqual(defects(ok), [])

    def test_label_unknown(self):
        bad = good(findings="\n".join([
            "## Находки",
            "",
            "- CONFIRMED: вход по RDP — Security.jsonl:412 «LogonType=10».",
        ]))
        self.assertIn("label_unknown", defects(bad))
        self.assertNotIn("label_unknown", defects(good()))

    def test_label_unknown_fires_beside_a_good_label_too(self):
        bad = good(findings="\n".join([
            "## Находки",
            "",
            "- PROVEN / GUESSED: вход по RDP — Security.jsonl:412 «LogonType=10».",
        ]))
        found = defects(bad)
        self.assertIn("label_unknown", found)
        self.assertNotIn("assertion_unlabelled", found)

    def test_inventory_missing(self):
        self.assertIn("inventory_missing", defects(good(inventory="")))
        self.assertNotIn("inventory_missing", defects(good()))

    def test_inventory_unsourced(self):
        bad = good(inventory="\n".join([
            "## Инвентарь наблюдаемых величин",
            "",
            "- 203.0.113.7 — адрес — Security.jsonl:412",
            "- svc-backup — имя учётной записи",
        ]))
        self.assertIn("inventory_unsourced", defects(bad))
        self.assertNotIn("inventory_unsourced", defects(good()))

    def test_inventory_table_header_is_not_an_entry(self):
        ok = good(inventory="\n".join([
            "## Инвентарь наблюдаемых величин",
            "",
            "| величина | вид | источник |",
            "| --- | --- | --- |",
            "| 203.0.113.7 | адрес | Security.jsonl:412 |",
        ]))
        self.assertEqual(defects(ok), [])

    def test_missing_data_section_absent(self):
        self.assertIn("missing_data_section_absent", defects(good(missing="")))
        self.assertNotIn("missing_data_section_absent", defects(good()))

    def test_verdict_section_absent(self):
        found = defects(good(verdict=""))
        self.assertIn("verdict_section_absent", found)
        self.assertNotIn("verdict_section_absent", defects(good()))

    def test_verdict_not_last(self):
        text = "\n\n".join(["# Отчёт", VERDICT, FINDINGS, INVENTORY, MISSING]) + "\n"
        found = defects(text)
        self.assertIn("verdict_not_last", found)
        self.assertNotIn("verdict_not_last", defects(good()))

    def test_verdict_not_one_of_three_when_absent(self):
        bad = good(verdict="\n".join([
            "## ВЕРДИКТ", "",
            "машина, вероятно, пострадала — Security.jsonl:412.",
        ]))
        self.assertIn("verdict_not_one_of_three", defects(bad))

    def test_verdict_not_one_of_three_when_two_are_stated(self):
        bad = good(verdict="\n".join([
            "## ВЕРДИКТ", "",
            "скомпрометирована, хотя по второй машине чисто — Security.jsonl:412.",
        ]))
        self.assertIn("verdict_not_one_of_three", defects(bad))

    def test_each_of_the_three_verdicts_is_accepted(self):
        for word in ("скомпрометирована", "атаковали, но не доказано", "чисто"):
            text = good(verdict="## ВЕРДИКТ\n\n%s — Security.jsonl:412.\n" % word)
            self.assertEqual(defects(text), [], word)

    def test_verdict_uncited(self):
        bad = good(verdict="## ВЕРДИКТ\n\nскомпрометирована.\n")
        self.assertIn("verdict_uncited", defects(bad))
        self.assertNotIn("verdict_uncited", defects(good()))

    def test_a_clock_time_is_not_a_citation_for_the_verdict(self):
        bad = good(verdict="## ВЕРДИКТ\n\nчисто, по состоянию на 04:05:06 UTC.\n")
        self.assertIn("verdict_uncited", defects(bad))


class TheDeliveredReportIsRefused(unittest.TestCase):
    """Regression: the shape of 20260827T173511Z-v41's delivered report."""

    DELIVERED = "\n".join([
        "# Отчёт по инциденту",
        "",
        "## ВЕРДИКТ",
        "",
        "компрометация подтверждена.",
        "",
        "## Хронология",
        "",
        "- 2026-08-20T10:00:00Z вход по RDP с 203.0.113.7 — Security.jsonl:412.",
        "- 2026-08-20T10:04:00Z установлена служба 3proxy — System.jsonl:263.",
        "",
        "## Выводы",
        "",
        "Кто именно действовал — владелец или посторонний — по логам не видно.",
    ]) + "\n"

    def test_the_delivered_shape_is_refused(self):
        found = defects(self.DELIVERED)
        for name in ("assertion_unlabelled", "inventory_missing",
                     "missing_data_section_absent", "verdict_not_last",
                     "verdict_not_one_of_three", "verdict_uncited"):
            self.assertIn(name, found, name)

    def test_the_same_facts_written_to_contract_pass(self):
        repaired = "\n\n".join([
            "# Отчёт по инциденту",
            "\n".join([
                "## Хронология",
                "",
                "- PROVEN: 2026-08-20T10:00:00Z вход по RDP с 203.0.113.7 — "
                "Security.jsonl:412.",
                "- PROVEN: 2026-08-20T10:04:00Z установлена служба 3proxy — "
                "System.jsonl:263.",
            ]),
            INVENTORY,
            "\n".join([
                "## Чего не хватает в логах",
                "",
                "- Владельца учётной записи логи не называют, поэтому владелец и "
                "посторонний неразличимы.",
            ]),
            "## ВЕРДИКТ\n\nатаковали, но не доказано — Security.jsonl:412, "
            "System.jsonl:263.",
        ]) + "\n"
        self.assertEqual(defects(repaired), [])


class FailClosed(unittest.TestCase):
    def run_cli(self, argv):
        return RC.main(argv)

    def test_missing_report_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            rc = self.run_cli([os.path.join(d, "nope.md")])
        self.assertEqual(rc, 2)

    def test_empty_report_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "report.md")
            open(p, "w", encoding="utf-8").write("   \n")
            self.assertEqual(self.run_cli([p]), 2)

    def test_unreadable_report_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "adir.md")
            os.mkdir(p)
            self.assertEqual(self.run_cli([p]), 2)

    def test_unparseable_contract_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            rep = os.path.join(d, "report.md")
            open(rep, "w", encoding="utf-8").write(good())
            broken = os.path.join(d, "c.json")
            open(broken, "w", encoding="utf-8").write("{ not json")
            self.assertEqual(self.run_cli([rep, "--contract", broken]), 2)

    def test_contract_missing_a_key_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            rep = os.path.join(d, "report.md")
            open(rep, "w", encoding="utf-8").write(good())
            thin = os.path.join(d, "c.json")
            json.dump({"citation": r"(\S+):(\d+)"},
                      open(thin, "w", encoding="utf-8"))
            self.assertEqual(self.run_cli([rep, "--contract", thin]), 2)

    def test_absent_contract_file_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            rep = os.path.join(d, "report.md")
            open(rep, "w", encoding="utf-8").write(good())
            self.assertEqual(
                self.run_cli([rep, "--contract", os.path.join(d, "no.json")]), 2)


class ExitCodes(unittest.TestCase):
    def test_conforming_report_exits_zero_and_defective_exits_one(self):
        with tempfile.TemporaryDirectory() as d:
            ok = os.path.join(d, "ok.md")
            open(ok, "w", encoding="utf-8").write(good())
            self.assertEqual(RC.main([ok]), 0)
            bad = os.path.join(d, "bad.md")
            open(bad, "w", encoding="utf-8").write(
                TheDeliveredReportIsRefused.DELIVERED)
            self.assertEqual(RC.main([bad]), 1)


class ContractIsData(unittest.TestCase):
    def test_a_different_customer_needs_a_new_profile_not_a_new_gate(self):
        """Swap the vocabulary in the profile; the engine follows it."""
        with tempfile.TemporaryDirectory() as d:
            spec = json.load(open(str(CONTRACT), encoding="utf-8"))
            for sec in spec["sections"]:
                if sec["role"] == "verdict":
                    sec["one_of"] = ["CLEAN", "BREACHED"]
                    # The verdict vocabulary and the outcome→verdict binding are
                    # one contract: swapping the words means swapping the map
                    # too, and the engine refuses a profile where they disagree.
                    sec["support"]["implies"] = {"норма": "CLEAN",
                                                 "попытка": "CLEAN",
                                                 "успех": "BREACHED"}
                    sec["support"]["stranger_verdict"] = "BREACHED"
            spec["labels"]["allowed"] = ["FACT", "GUESS"]
            spec["labels"]["ignore"] = spec["labels"]["ignore"] + ["CLEAN",
                                                                   "BREACHED"]
            p = os.path.join(d, "other.json")
            json.dump(spec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            other = RC.load_contract(p)

            text = good(
                findings="## Находки\n\n- FACT: вход — Security.jsonl:412.",
                verdict="## ВЕРДИКТ\n\nBREACHED — Security.jsonl:412.")
            self.assertEqual(RC.check(text, other), [])
            # The corporate profile refuses exactly the same text.
            self.assertNotEqual(RC.check(text, contract()), [])


if __name__ == "__main__":
    unittest.main()
