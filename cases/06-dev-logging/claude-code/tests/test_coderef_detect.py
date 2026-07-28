import unittest, tempfile
from pathlib import Path
from logalyzer.coderef import is_code_dir, suggest_repos, resolve_mode

class TestDetect(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "workdir").mkdir()
        repo = self.root / "petstore-repo"
        (repo / "services").mkdir(parents=True)
        (repo / "pom.xml").write_text("<project/>")

    def tearDown(self):
        self.dir.cleanup()

    def test_markers(self):
        self.assertTrue(is_code_dir(self.root / "petstore-repo"))
        self.assertFalse(is_code_dir(self.root / "workdir"))

    def test_suggest_finds_sibling_repo(self):
        found = suggest_repos(self.root / "workdir")
        self.assertIn(self.root / "petstore-repo", found)

    def test_resolve_mode_ask_with_suggestions(self):
        mode, clar = resolve_mode("auto", [], [self.root / "petstore-repo"])
        self.assertEqual(mode, "ask")
        self.assertIn("petstore-repo", " ".join(clar["suggestions"]))
        self.assertIn("без кода", clar["question"])

    def test_resolve_mode_dev_and_ops(self):
        self.assertEqual(resolve_mode("auto", [self.root / "petstore-repo"], [])[0], "dev")
        self.assertEqual(resolve_mode("ops", [], [])[0], "ops")

if __name__ == "__main__":
    unittest.main()
