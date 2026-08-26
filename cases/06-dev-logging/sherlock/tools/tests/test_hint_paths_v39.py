#!/usr/bin/env python3
"""fix 5a — a hint that cannot be followed from the arm's cwd is not a hint.

MEASURED, v38 paid run 20260826T132832Z-v38, last 90 minutes. `citecheck` told
the arm «добавь строку в reference/enum-tables.tsv с источником». The tool LOADS
that table from an absolute path under the skill root
(`os.path.normpath(os.path.join(reference_dir, ENUM_TABLE_FILE))`), but it PRINTED
the path relative to that root — and the arm's cwd is the bench scratch dir, which
has no `reference/` in it. The arm answered the way a maze is answered: 8
`read_file` + 4 `grep_search` on `citecheck.py`, grepping
`ENUM_DECODE_RE|def enum_decode_ok`, hunting for the file. It never found it and
the run died with a 192-byte report.

NOTE CORRECTION, recorded here so it is not lost: the project note says the skill
"never taught the required `Field=value (decode)` form". That is FALSE. SKILL.md
teaches it with worked examples, and `render_enum_decode()` prints the exact
required string at the point of failure. The teaching was there; the RESOLVABLE
PATH was missing.

So this suite holds two lines:

  * functionally — run the gate from a cwd with no `reference/` on a report that
    quotes an enum value the table lacks, and insist the hint names a path that
    exists from where the arm is standing;
  * statically — no non-docstring string literal in any v39 gate may name a
    skill-root-relative file or invoke a bare `python3 <tool>.py`. The functional
    test only covers the hint it exercises; the static one covers the class.
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
V39 = os.path.join(SKILLS, "v39", "tools")
REFERENCE = os.path.normpath(os.path.join(SKILLS, "v39", "reference"))
CITECHECK = os.path.join(V39, "citecheck.py")

# `python3 covermap.py` — a basename the arm's cwd does not contain. `{`, `<`,
# `$` and `/` are the three forms that DO resolve: a template slot, a documented
# placeholder and an absolute path.
BARE_TOOL_CMD_RE = re.compile(r"python3\s+(?![/{$<])[\w.-]*\.py")
# `reference/enum-tables.tsv`, `tools/citecheck.py` — a concrete file under the
# SKILL ROOT, spelled relative to it. A glob (`reference/*.md`) is prose about the
# layout, not an address the arm is told to open, so it is not matched.
# The filename is often interpolated (`"reference/%s" % ENUM_TABLE_FILE` is the
# exact line that cost the paid run its last 90 minutes), so the alternatives
# include the format slots as well as a literal basename.
SKILL_REL_FILE_RE = re.compile(
    r"(?<![\w/{<])(?:reference|tools)/"
    r"(?:[\w.-]+\.(?:tsv|md|py)|%s|%\([\w]+\)s|\{[\w]*\})")


def non_docstring_literals(path):
    """-> [(lineno, text)] for every string constant that is not a docstring.

    Docstrings and `#` comments are documentation for a human reading the source;
    they are not printed at the arm. Only what can reach stdout is in scope.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                docs.add(id(node.body[0].value))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            out.append((node.lineno, node.value))
    return out


def gate_sources():
    return sorted(os.path.join(V39, n) for n in os.listdir(V39)
                  if n.endswith(".py"))


class StaticHintPaths(unittest.TestCase):
    def test_no_gate_prints_a_bare_tool_invocation(self):
        bad = []
        for path in gate_sources():
            for lineno, text in non_docstring_literals(path):
                for m in BARE_TOOL_CMD_RE.finditer(text):
                    bad.append("%s:%d %r" % (os.path.basename(path), lineno,
                                             m.group(0)))
        self.assertEqual([], bad,
                         "a printed `python3 <basename>.py` does not resolve from "
                         "the arm's cwd — use tool_cmd():\n" + "\n".join(bad))

    def test_no_gate_prints_a_skill_root_relative_file(self):
        bad = []
        for path in gate_sources():
            for lineno, text in non_docstring_literals(path):
                for m in SKILL_REL_FILE_RE.finditer(text):
                    bad.append("%s:%d %r" % (os.path.basename(path), lineno,
                                             m.group(0)))
        self.assertEqual([], bad,
                         "a printed skill-root-relative path is a maze from the "
                         "arm's cwd — print the resolved absolute path:\n"
                         + "\n".join(bad))

    def test_the_static_guard_can_actually_fail(self):
        """Negative control: the regexes are not toothless."""
        self.assertTrue(BARE_TOOL_CMD_RE.search("почини так: python3 covermap.py --corpus X"))
        self.assertTrue(SKILL_REL_FILE_RE.search("добавь строку в reference/enum-tables.tsv"))
        self.assertTrue(SKILL_REL_FILE_RE.search("строку в reference/%s с источником"))
        self.assertFalse(BARE_TOOL_CMD_RE.search("python3 /abs/tools/covermap.py"))
        self.assertFalse(BARE_TOOL_CMD_RE.search("python3 {tools}/covermap.py"))
        self.assertFalse(SKILL_REL_FILE_RE.search("`reference/*.md`"))
        self.assertFalse(SKILL_REL_FILE_RE.search("/abs/root/reference/enum-tables.tsv"))


LINE = ('{"Event":{"System":{"Provider":{"#attributes":{"Name":"Microsoft-Windows-'
        'Windows Firewall With Advanced Security"}},"EventID":{"#text":2004}},'
        '"EventData":{"Action":"77","RuleName":"allow-3proxy"}}}')


class HintFromTheArmsCwd(unittest.TestCase):
    """Reproduce the arm's situation, exactly: a cwd with no `reference/`."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hintpath-v39-")
        self.corpus = os.path.join(self.dir, "corpus")
        self.work = os.path.join(self.dir, "work")
        os.makedirs(self.corpus)
        os.makedirs(self.work)
        with open(os.path.join(self.corpus, "Firewall.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write(LINE + "\n")
        self.report = os.path.join(self.work, "report.md")
        with open(self.report, "w", encoding="utf-8") as fh:
            fh.write("# Отчёт\n\n## Находки\n\n### Н-1 · Правило firewall\n\n"
                     "что сломано: правило изменено\n\n"
                     "улики: Firewall.jsonl:1 «allow-3proxy»\n\n"
                     "чем опровергал: —\n\nатрибуция: не установлена\n\n"
                     "исход: успех\n\nAction=77 (нет такого)\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_gate(self):
        # cwd = the WORK dir, the one place the arm actually stands, and the one
        # place with no `reference/` anywhere below it.
        self.assertFalse(os.path.exists(os.path.join(self.work, "reference")))
        p = subprocess.run([sys.executable, CITECHECK, self.report,
                            "--corpus", self.corpus],
                           cwd=self.work, capture_output=True, text=True)
        return p.stdout

    def test_unknown_enum_hint_names_a_path_that_exists_from_that_cwd(self):
        out = self.run_gate()
        hint = [l for l in out.splitlines() if "значения нет в таблице" in l]
        self.assertTrue(hint, out[-2000:])
        line = hint[0]
        cited = re.findall(r"(/[\w./-]*enum-tables\.tsv)", line)
        self.assertTrue(cited, "the hint must name an ABSOLUTE path, got: %s" % line)
        self.assertTrue(os.path.isabs(cited[0]), line)
        self.assertTrue(os.path.exists(cited[0]),
                        "the arm is told to edit %s and it is not there" % cited[0])
        self.assertEqual(os.path.realpath(os.path.join(REFERENCE, "enum-tables.tsv")),
                         os.path.realpath(cited[0]), line)

    def test_the_hint_still_prints_the_form_it_always_did(self):
        """The NOTE CORRECTION, as an assertion: the teaching was never missing."""
        out = self.run_gate()
        self.assertIn("РАСШИФРОВКА ПЕРЕЧИСЛЕНИЙ (6a)", out)


class SiblingHintsAreAbsolute(unittest.TestCase):
    """The other actionable hints in the same class of defect."""

    @staticmethod
    def load(name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "v39_%s" % name.replace(".", "_"), os.path.join(V39, name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_citecheck_tool_cmd_is_absolute(self):
        cc = self.load("citecheck.py")
        for name in ("rollover.py", "covermap.py", "logmap.py"):
            self.assertEqual("python3 " + os.path.join(V39, name),
                             cc.tool_cmd(name))

    def test_rollover_hint_is_absolute(self):
        cc = self.load("citecheck.py")
        out = cc.render_rollover({"missing_section": True, "blocking": 1})
        self.assertIn("НЕТ РАЗДЕЛА", out)
        self.assertIn(os.path.join(V39, "rollover.py"), out)

    def test_enum_render_names_the_resolved_table(self):
        cc = self.load("citecheck.py")
        table_path = cc.enum_table_path()
        self.assertTrue(os.path.isabs(table_path))
        out = cc.render_enum_decode(
            {"blocking": 1, "table_size": 1, "table_problems": [],
             "table_path": table_path,
             "items": [{"block": "\u041d-1", "line": 3, "field": "action",
                        "value": 77, "kind": "unknown_value", "text": None,
                        "expected": None}]})
        self.assertIn(table_path, out)

    def test_logmap_ledger_hints_are_absolute(self):
        lm = self.load("logmap.py")
        self.assertEqual("python3 " + os.path.join(V39, "citecheck.py"),
                         lm.tool_cmd("citecheck.py"))


class FrozenArmsUntouched(unittest.TestCase):
    def test_v38_still_prints_the_relative_path(self):
        """v38 carries a paid result; the maze stays recorded there."""
        with open(os.path.join(SKILLS, "v38", "tools", "citecheck.py"),
                  encoding="utf-8") as fh:
            self.assertIn("строку в reference/%s с источником", fh.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
