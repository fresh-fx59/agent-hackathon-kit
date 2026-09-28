import importlib.util
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "skills" / "v65" / "tools" / "draftcheck.py"
spec = importlib.util.spec_from_file_location("draftcheck_v65", TOOL)
dc = importlib.util.module_from_spec(spec); spec.loader.exec_module(dc)

BASE = """# Инвентарь
## Пропущенные данные
## Покрытие
## Окно записей
| путь | канал | окно | записи | нет |
| a.log | x | окно: 1–2 | записей: 2 | нет: 0 |
## Вердикт
| rare | new | peak | volume | sequence |
### Н-1 · событие
- исход: успех
- атрибуция: установлена
### К-1 · кандидат
- исход: норма
"""

class DraftcheckV65(unittest.TestCase):
    def test_canonical_finding_and_candidate(self):
        result = dc.check(BASE)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["counts"]["findings"], 1)
        self.assertEqual(result["counts"]["candidates"], 1)

    def test_malformed_heading_is_explicit(self):
        result = dc.check(BASE.replace("Н-1 · событие", "Н-1 событие"))
        self.assertTrue(any(e["code"] == "missing_heading_separator" for e in result["errors"]))

    def test_missing_sections_and_rollover_are_rejected(self):
        result = dc.check("### Н-1 · событие\n- исход: успех\n- атрибуция: установлена\n")
        codes = {e["code"] for e in result["errors"]}
        self.assertIn("missing_section", codes)

    def test_ownership_and_aggregate_forms(self):
        bad = BASE + "\n## Принадлежность учётных записей\n| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше |\n| alice | first | no witness | профиль | владелец | - |\n\nагрегат: `path` · count = 1 · \\jq\n"
        codes = {e["code"] for e in dc.check(bad)["errors"]}
        self.assertIn("invalid_ownership", codes)
        self.assertIn("invalid_aggregate_wrapper", codes)

    def test_valid_six_column_ownership_with_witness(self):
        text = BASE + """
## Принадлежность учётных записей
| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше |
| ACME\\alice | first | Security.evtx:42 «logon» | профиль | владелец | - |
"""
        codes = {e["code"] for e in dc.check(text)["errors"]}
        self.assertNotIn("invalid_ownership", codes)
        self.assertNotIn("unwitnessed_ownership", codes)

    def test_each_state_group_is_required(self):
        for state in ("rare", "new", "peak", "volume", "sequence"):
            text = BASE.replace(state, "absent")
            codes = {e["code"] for e in dc.check(text)["errors"]}
            self.assertIn("missing_state_group", codes, state)

    def test_literal_aggregate_is_accepted_and_escaped_is_rejected(self):
        literal = BASE + "\nагрегат: Security.jsonl · count(EventID=1) = 2 · `jq -r .`\n"
        self.assertNotIn("invalid_aggregate_wrapper", {e["code"] for e in dc.check(literal)["errors"]})
        escaped = BASE + "\nагрегат: Security.jsonl · count(EventID=1) = 2 · \\`jq -r .\\`\n"
        self.assertIn("invalid_aggregate_wrapper", {e["code"] for e in dc.check(escaped)["errors"]})

    def test_excess_rollover_row_is_rejected(self):
        extra = BASE.replace("## Вердикт", "| a.log | x | окно: 1–2 | записей: 2 | нет: 0 |\n## Вердикт")
        self.assertIn("excess_rollover_row", {e["code"] for e in dc.check(extra)["errors"]})

if __name__ == "__main__": unittest.main()
