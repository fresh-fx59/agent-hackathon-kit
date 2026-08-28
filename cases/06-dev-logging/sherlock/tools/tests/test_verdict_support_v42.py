#!/usr/bin/env python3
"""Вердикт обязан СЛЕДОВАТЬ из находок — gate, not hope. v42, fix 3.

Fix 2 made the operator's contract checkable as SHAPE: three legal words, the
section last, labels, an inventory, a section for what the logs lack. It caught
that the delivered report said «компрометация» — a noun outside the three
literals. It could not catch the worse half of the same paragraph:

  * the verdict «скомпрометирована» means PROOF a stranger got access, and the
    same sentence admitted «кто именно действовал под учёткой root (владелец или
    атакующий) — по корпусу не определяется»;
  * both `исход: успех` blocks carried `атрибуция: не установлена`, so nothing
    in the report pinned the successful access on an outsider at all.

This suite covers the semantic layer: `verdict_unsupported_by_outcomes`,
`verdict_contradicts_report`, `verdict_success_not_attributed_to_stranger` and
the fail-closed `verdict_outcomes_unreadable`, each failing and passing, plus
the regression built from the ВЕРДИКТ section that was actually delivered and a
PARAPHRASE of its admission written in words that appear nowhere in the gate.

    python3 tools/tests/test_verdict_support_v42.py
"""
import copy
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


RC = load("reportcheck_v42_support", V42 / "tools" / "reportcheck.py")


def contract():
    return RC.load_contract(str(CONTRACT))


def defects(text, c=None):
    return sorted({d["defect"] for d in RC.check(text, c or contract())})


UNSUPPORTED = "verdict_unsupported_by_outcomes"
CONTRADICTS = "verdict_contradicts_report"
STRANGER = "verdict_success_not_attributed_to_stranger"
UNREADABLE = "verdict_outcomes_unreadable"

INVENTORY = "\n".join([
    "## Инвентарь наблюдаемых величин",
    "",
    "- 192.99.186.31 — адрес — Security.jsonl:412",
    "- IPSERVER\\root — имя учётной записи — Security.jsonl:412",
    "- C:\\3proxy\\bin64\\3proxy.exe — путь — System.jsonl:263",
])

MISSING = "\n".join([
    "## Чего не хватает в логах",
    "",
    "- Security за 8–9 мая вытеснен кольцевым буфером.",
])


def finding(num, title, outcome, attribution, extra=""):
    body = [
        "### Н-%d · %s" % (num, title),
        "",
        "что сломано: событие зафиксировано в журнале.",
        "",
        "улики: PROVEN Security.jsonl:412 — «дословная цитата».",
        "",
        "атрибуция: %s" % attribution,
        "",
        "исход: %s" % outcome,
    ]
    if extra:
        body += ["", extra]
    return "\n".join(body)


def verdict(word, extra=""):
    line = "%s — Security.jsonl:412, System.jsonl:263." % word
    return "\n".join(["## ВЕРДИКТ", "", line] + (["", extra] if extra else []))


def report(findings, word, extra=""):
    parts = ["# Отчёт", "## Находки"] + list(findings)
    parts += [INVENTORY, MISSING, verdict(word, extra)]
    return "\n\n".join(parts) + "\n"


SUCCESS_ATTRIBUTED = finding(1, "Вход извне", "успех", "установлена")
SUCCESS_UNATTRIBUTED = finding(1, "Вход извне", "успех", "не установлена")
ATTEMPT = finding(2, "Перебор паролей RDP", "попытка", "не установлена")
NORMAL = finding(3, "Плановое обновление", "норма", "установлена")


class TheSuiteIsSatisfiable(unittest.TestCase):
    def test_a_supported_verdict_passes_clean(self):
        self.assertEqual(defects(report([NORMAL], "чисто")), [])

    def test_the_shipped_profile_carries_the_binding(self):
        c = contract()
        spec = RC._verdict_spec(c)
        self.assertIsNotNone(spec)
        self.assertEqual(spec["support"]["implies"], {
            "успех": "скомпрометирована",
            "попытка": "атаковали, но не доказано",
            "норма": "чисто"})


class VerdictUnsupportedByOutcomes(unittest.TestCase):
    """Сильнейший исход решает ответ отчёта. Ровно три связки."""

    def test_all_normal_cannot_end_compromised(self):
        self.assertIn(UNSUPPORTED, defects(report([NORMAL], "скомпрометирована")))

    def test_all_normal_ends_clean(self):
        self.assertNotIn(UNSUPPORTED, defects(report([NORMAL], "чисто")))

    def test_an_attempt_cannot_end_clean(self):
        self.assertIn(UNSUPPORTED, defects(report([NORMAL, ATTEMPT], "чисто")))

    def test_an_attempt_ends_attacked_not_proven(self):
        found = defects(report([NORMAL, ATTEMPT], "атаковали, но не доказано"))
        self.assertEqual(found, [])

    def test_a_success_cannot_end_attacked_not_proven(self):
        found = defects(report([ATTEMPT, SUCCESS_ATTRIBUTED],
                               "атаковали, но не доказано"))
        self.assertIn(UNSUPPORTED, found)

    def test_a_success_ends_compromised(self):
        found = defects(report([ATTEMPT, SUCCESS_ATTRIBUTED],
                               "скомпрометирована"))
        self.assertEqual(found, [])

    def test_the_strongest_outcome_wins_not_the_last_one(self):
        # успех сначала, норма последней — ответ всё равно «скомпрометирована».
        found = defects(report([SUCCESS_ATTRIBUTED, NORMAL], "чисто"))
        self.assertIn(UNSUPPORTED, found)


class SuccessMustBeAttributedToAStranger(unittest.TestCase):
    """«Скомпрометирована» = доказан ЧУЖОЙ, а не просто удавшееся действие."""

    def test_unattributed_success_cannot_carry_compromised(self):
        self.assertIn(STRANGER,
                      defects(report([SUCCESS_UNATTRIBUTED], "скомпрометирована")))

    def test_attributed_success_carries_it(self):
        self.assertNotIn(STRANGER,
                         defects(report([SUCCESS_ATTRIBUTED], "скомпрометирована")))

    def test_a_success_without_any_attribution_line_is_refused(self):
        block = "\n".join(["### Н-1 · Вход извне", "",
                           "улики: PROVEN Security.jsonl:412 — «цитата».", "",
                           "исход: успех"])
        self.assertIn(STRANGER, defects(report([block], "скомпрометирована")))

    def test_a_malformed_attribution_line_is_refused(self):
        block = SUCCESS_ATTRIBUTED.replace("атрибуция: установлена",
                                           "атрибуция: скорее всего владелец")
        self.assertIn(STRANGER, defects(report([block], "скомпрометирована")))

    def test_the_check_is_silent_on_the_other_two_verdicts(self):
        found = defects(report([SUCCESS_UNATTRIBUTED, ATTEMPT],
                               "атаковали, но не доказано"))
        self.assertNotIn(STRANGER, found)


class AnUnattributedSuccessCountsOneStepLess(unittest.TestCase):
    """`support.unattributed_strongest_counts_as` — связка, а не поблажка.

    Успех, которого не на кого записать, честно доказывает только «атаковали».
    """

    def test_an_unattributed_success_supports_the_middle_verdict(self):
        self.assertEqual(
            defects(report([SUCCESS_UNATTRIBUTED], "атаковали, но не доказано")),
            [])

    def test_it_does_not_support_clean(self):
        self.assertIn(UNSUPPORTED,
                      defects(report([SUCCESS_UNATTRIBUTED], "чисто")))

    def test_an_attributed_success_still_demands_the_strongest_verdict(self):
        self.assertIn(UNSUPPORTED,
                      defects(report([SUCCESS_ATTRIBUTED],
                                     "атаковали, но не доказано")))


class VerdictContradictsTheReport(unittest.TestCase):
    """Признание «кто действовал — неизвестно» убивает «скомпрометирована».

    Ловится конъюнкцией трёх словарей внутри предложения (владелец И
    посторонний И неопределённость), а не заученной фразой из одного отчёта.
    """

    ADMISSION = ("кто именно действовал под учёткой root (владелец или "
                 "атакующий) — по корпусу не определяется")

    def test_the_admission_blocks_the_stranger_verdict(self):
        text = report([SUCCESS_ATTRIBUTED], "скомпрометирована", self.ADMISSION)
        self.assertIn(CONTRADICTS, defects(text))

    def test_without_the_admission_the_same_report_passes(self):
        text = report([SUCCESS_ATTRIBUTED], "скомпрометирована")
        self.assertEqual(defects(text), [])

    def test_a_paraphrase_of_the_same_admission_is_caught(self):
        """ДРУГИЕ слова, тот же смысл — и это ловится.

        Ни одно слово этой фразы не совпадает с фразой из сданного отчёта, кроме
        служебных. Гейт видит её потому, что она несёт все три смысла: хозяин,
        посторонний, «нельзя различить».
        """
        para = ("по журналам нельзя различить, работал ли под этой учётной "
                "записью сам хозяин или посторонний")
        self.assertNotIn("владелец", para)
        self.assertNotIn("атакующий", para)
        self.assertNotIn("не определяется", para)
        text = report([SUCCESS_ATTRIBUTED], "скомпрометирована", para)
        self.assertIn(CONTRADICTS, defects(text))

    def test_a_second_paraphrase_in_yet_other_words(self):
        para = ("установить, был ли это легитимный сотрудник или злоумышленник, "
                "по корпусу невозможно определить")
        self.assertIn(CONTRADICTS,
                      defects(report([SUCCESS_ATTRIBUTED], "скомпрометирована",
                                     para)))

    def test_the_admission_may_live_anywhere_in_the_report(self):
        block = finding(1, "Вход извне", "успех", "установлена",
                        extra="чем опровергал: владелец или посторонний — "
                              "по корпусу не определяется.")
        self.assertIn(CONTRADICTS, defects(report([block], "скомпрометирована")))

    def test_two_of_the_three_meanings_are_not_an_admission(self):
        # Неопределённость есть, посторонний есть, а владельца-альтернативы нет:
        # это не признание «кто из двоих», а обычная оговорка.
        near = "адрес злоумышленника по корпусу не определяется"
        self.assertNotIn(CONTRADICTS,
                         defects(report([SUCCESS_ATTRIBUTED],
                                        "скомпрометирована", near)))

    def test_owner_and_uncertainty_without_an_outsider_is_not_an_admission(self):
        near = "владелец сервера по корпусу не определяется"
        self.assertNotIn(CONTRADICTS,
                         defects(report([SUCCESS_ATTRIBUTED],
                                        "скомпрометирована", near)))

    def test_owner_and_outsider_without_uncertainty_is_not_an_admission(self):
        near = "владелец и посторонний работали с этой машиной в разные дни"
        self.assertNotIn(CONTRADICTS,
                         defects(report([SUCCESS_ATTRIBUTED],
                                        "скомпрометирована", near)))

    def test_the_admission_is_harmless_under_the_weaker_verdict(self):
        text = report([SUCCESS_ATTRIBUTED, ATTEMPT], "атаковали, но не доказано",
                      self.ADMISSION)
        self.assertNotIn(CONTRADICTS, defects(text))


class TheDeliveredVerdictIsRefused(unittest.TestCase):
    """Регрессия: раздел ВЕРДИКТ прогона 20260827T173511Z-v41, дословно.

    Копия здесь, а не ссылка на каталог прогона: тест обязан жить без него.
    Исходы и атрибуции взяты из Н-2 и Н-3 того же отчёта.
    """

    DELIVERED_VERDICT = (
        "Сильнейший исход среди находок — «успех»: 9 мая 2021 г. выполнен "
        "успешный удалённый вход в учётную запись IPSERVER\\root с внешнего "
        "адреса 192.99.186.31, и через 42 минуты в её контексте развёрнут "
        "прокси-сервер 3proxy (служба с автозапуском, LocalSystem), вручную "
        "разрешённый в брандмауэре. Июньский перебор паролей RDP (33 456 "
        "отказов) успешных входов не дал. Ответ отчёта: **%s** — признаки "
        "удалённого доступа извне и закрепления на сервере присутствуют; кто "
        "именно действовал под учёткой root (владелец или атакующий) — по "
        "корпусу не определяется.")

    N2 = finding(2, "Успешный удалённый вход IPSERVER\\root по RDP",
                 "успех", "не установлена")
    N3 = finding(3, "Развёрнут прокси-сервер 3proxy", "успех", "не установлена")
    N1 = finding(1, "Массовый перебор паролей RDP", "попытка", "не установлена")

    def body(self, word):
        text = self.DELIVERED_VERDICT % word
        return "\n\n".join([
            "# Отчёт Sherlock — IPSERVER",
            "## Находки", self.N1, self.N2, self.N3,
            INVENTORY, MISSING,
            "## ВЕРДИКТ", text + " Security.jsonl:412, System.jsonl:263.",
        ]) + "\n"

    def test_the_word_as_delivered_is_outside_the_three(self):
        # Слой fix 2: «компрометация» — не один из трёх литералов.
        self.assertIn("verdict_not_one_of_three",
                      defects(self.body("компрометация")))

    def test_repairing_only_the_word_still_refuses(self):
        """Починить слово мало: тот же абзац сам себя опровергает."""
        found = defects(self.body("скомпрометирована"))
        self.assertIn(CONTRADICTS, found)
        self.assertIn(STRANGER, found)
        # И связка исходов тоже против: оба «успеха» неатрибутированы, значит
        # они тянут только на «атаковали, но не доказано».
        self.assertIn(UNSUPPORTED, found)

    def test_the_honest_verdict_on_the_same_findings_passes(self):
        honest = "\n\n".join([
            "# Отчёт Sherlock — IPSERVER",
            "## Находки", self.N1, self.N2, self.N3,
            INVENTORY, MISSING,
            "## ВЕРДИКТ",
            "атаковали, но не доказано — Security.jsonl:412, System.jsonl:263. "
            "Кто именно действовал под учёткой root (владелец или атакующий) — "
            "по корпусу не определяется.",
        ]) + "\n"
        self.assertEqual(defects(honest), [])

    def test_compromised_becomes_available_once_attribution_is_established(self):
        proven = "\n\n".join([
            "# Отчёт Sherlock — IPSERVER",
            "## Находки", self.N1,
            finding(2, "Успешный удалённый вход IPSERVER\\root по RDP",
                    "успех", "установлена"),
            finding(3, "Развёрнут прокси-сервер 3proxy", "успех", "установлена"),
            INVENTORY, MISSING,
            "## ВЕРДИКТ",
            "скомпрометирована — Security.jsonl:412, System.jsonl:263.",
        ]) + "\n"
        self.assertEqual(defects(proven), [])


class FailClosed(unittest.TestCase):
    """Не разобрал — значит отказ, а не тихая сдача."""

    def test_a_fourth_outcome_on_the_line_is_unreadable(self):
        block = SUCCESS_ATTRIBUTED.replace("исход: успех",
                                           "исход: успех, но не доказан")
        found = defects(report([block], "скомпрометирована"))
        self.assertIn(UNREADABLE, found)
        self.assertNotIn(UNSUPPORTED, found)

    def test_two_outcome_lines_in_one_block_are_unreadable(self):
        block = SUCCESS_ATTRIBUTED + "\n\nисход: попытка"
        self.assertIn(UNREADABLE, defects(report([block], "скомпрометирована")))

    def test_an_outcome_inside_a_fence_is_a_sample_not_a_finding(self):
        block = "\n".join(["### Н-1 · Пример формата", "", "```",
                           "исход: успех", "```", "",
                           "улики: PROVEN Security.jsonl:412 — «цитата».", "",
                           "атрибуция: установлена", "", "исход: норма"])
        self.assertEqual(defects(report([block], "чисто")), [])

    def test_a_contract_without_support_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            spec = json.load(open(str(CONTRACT), encoding="utf-8"))
            for sec in spec["sections"]:
                sec.pop("support", None)
            p = os.path.join(d, "nosupport.json")
            json.dump(spec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            with self.assertRaises(RC.ContractError):
                RC.load_contract(p)
            rep = os.path.join(d, "r.md")
            open(rep, "w", encoding="utf-8").write(report([NORMAL], "чисто"))
            self.assertEqual(RC.main([rep, "--contract", p]), 2)

    def test_a_thin_support_block_is_a_refusal(self):
        for drop in ("implies", "outcome_rank", "attribution_line",
                     "stranger_verdict", "admission", "defects"):
            with tempfile.TemporaryDirectory() as d:
                spec = json.load(open(str(CONTRACT), encoding="utf-8"))
                for sec in spec["sections"]:
                    if sec.get("support"):
                        sec["support"].pop(drop, None)
                p = os.path.join(d, "thin.json")
                json.dump(spec, open(p, "w", encoding="utf-8"),
                          ensure_ascii=False)
                with self.assertRaises(RC.ContractError, msg=drop):
                    RC.load_contract(p)

    def test_an_incoherent_binding_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            spec = json.load(open(str(CONTRACT), encoding="utf-8"))
            for sec in spec["sections"]:
                if sec.get("support"):
                    sec["support"]["implies"]["успех"] = "разгромлена"
            p = os.path.join(d, "bad.json")
            json.dump(spec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            with self.assertRaises(RC.ContractError):
                RC.load_contract(p)

    def test_no_outcomes_at_all_follows_the_declared_policy(self):
        """Профиль решает, а не движок: skip — молчим, refuse — блокируем.

        Корпоративный профиль ставит `skip`, потому что находку без строки
        «исход:» блокирует леджер citecheck, который stopcheck гоняет по тому же
        отчёту; два гейта на один дефект дают только двойной шум.
        """
        c = contract()
        spec = RC._verdict_spec(c)
        self.assertEqual(spec["support"]["_no_outcomes"], "skip")
        plain = "\n\n".join(["# Отчёт", "## Находки", "",
                             "- PROVEN: вход — Security.jsonl:412.",
                             INVENTORY, MISSING,
                             verdict("чисто")]) + "\n"
        self.assertEqual(defects(plain, c), [])

        strict = copy.deepcopy(json.load(open(str(CONTRACT), encoding="utf-8")))
        for sec in strict["sections"]:
            if sec.get("support"):
                sec["support"]["when_no_outcomes"] = "refuse"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "strict.json")
            json.dump(strict, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            self.assertIn(UNREADABLE, defects(plain, RC.load_contract(p)))

    def test_an_unknown_policy_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            spec = json.load(open(str(CONTRACT), encoding="utf-8"))
            for sec in spec["sections"]:
                if sec.get("support"):
                    sec["support"]["when_no_outcomes"] = "как получится"
            p = os.path.join(d, "policy.json")
            json.dump(spec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            with self.assertRaises(RC.ContractError):
                RC.load_contract(p)


class ExitCodes(unittest.TestCase):
    def test_the_repaired_word_alone_still_exits_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.md")
            open(p, "w", encoding="utf-8").write(
                TheDeliveredVerdictIsRefused().body("скомпрометирована"))
            self.assertEqual(RC.main([p]), 1)

    def test_the_honest_report_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.md")
            open(p, "w", encoding="utf-8").write(report([NORMAL], "чисто"))
            self.assertEqual(RC.main([p]), 0)


class TheEngineCarriesNoCustomerProse(unittest.TestCase):
    def test_the_gate_source_holds_none_of_the_three_verdicts(self):
        src = (V42 / "tools" / "reportcheck.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        head, sep, _ = code.partition('"""')
        body = code.split('"""')[2] if sep else code
        for word in ("атаковали, но не доказано", "владелец", "атакующий",
                     "не определяется"):
            self.assertNotIn(word, body, word)

    def test_another_customer_gets_a_new_profile_not_a_new_gate(self):
        with tempfile.TemporaryDirectory() as d:
            spec = json.load(open(str(CONTRACT), encoding="utf-8"))
            for sec in spec["sections"]:
                if sec.get("support"):
                    sec["one_of"] = ["CLEAN", "BREACHED"]
                    sup = sec["support"]
                    sup["outcome_line"] = (
                        r"^\s*outcome\s*:\s*(win|try|none)\s*$")
                    sup["outcome_head"] = r"^\s*outcome\s*:"
                    sup["outcome_rank"] = ["none", "try", "win"]
                    sup["implies"] = {"none": "CLEAN", "try": "CLEAN",
                                      "win": "BREACHED"}
                    sup["attribution_line"] = (
                        r"^\s*actor\s*:\s*(known|unknown)\s*$")
                    sup["attribution_head"] = r"^\s*actor\s*:"
                    sup["attribution_established"] = "known"
                    sup["unattributed_strongest_counts_as"] = "try"
                    sup["stranger_verdict"] = "BREACHED"
            spec["labels"]["ignore"] = spec["labels"]["ignore"] + [
                "CLEAN", "BREACHED"]
            p = os.path.join(d, "other.json")
            json.dump(spec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            other = RC.load_contract(p)

        block = "\n".join(["### Н-1 · Remote logon", "",
                           "улики: PROVEN Security.jsonl:412 — «цитата».", "",
                           "actor: unknown", "", "outcome: win"])
        text = report([block], "BREACHED")
        self.assertIn(STRANGER, defects(text, other))
        ok = report([block.replace("actor: unknown", "actor: known")], "BREACHED")
        self.assertEqual(defects(ok, other), [])


if __name__ == "__main__":
    unittest.main()
