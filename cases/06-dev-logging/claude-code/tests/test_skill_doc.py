import unittest
from pathlib import Path

CASE = Path(__file__).resolve().parents[1]

class TestSkillDoc(unittest.TestCase):
    def test_skill_md_contract(self):
        text = (CASE / "SKILL.md").read_text(encoding="utf-8")
        for required in ("name: log-rca", "Когда применять", "упал", "correlation_id",
                         "Режимы", "DevOps", "Обязательное уточнение",
                         "--mode ops", "--repo", "exit", "3", "suggest-repos",
                         "данные, а не инструкции", "Демо-промпты",
                         "отдельный файл"):
            self.assertIn(required, text, "SKILL.md missing: %s" % required)

    def test_example_exists(self):
        text = (CASE / "examples" / "investigate-incident-1.md").read_text(encoding="utf-8")
        self.assertIn("investigate", text)
        self.assertIn("c-8f3a2b91", text)

if __name__ == "__main__":
    unittest.main()
