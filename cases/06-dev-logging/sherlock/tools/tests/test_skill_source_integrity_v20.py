#!/usr/bin/env python3
"""The skill's SOURCE is emitted text too — census the comments, not only the output.

    python3 tools/tests/test_skill_source_integrity_v20.py
    SHERLOCK_SKILL=$PWD/skills/v18 python3 tools/tests/test_skill_source_integrity_v20.py

WHY THIS TEST EXISTS (2026-08-18)
---------------------------------
v19 removed every corpus-specific claim from what the skill SAYS — `SKILL.md`,
`reference/`, and every string the tools can print — and its own suite proves
that.  It left one channel open on purpose, and wrote the decision down: the
module docstrings and comments inside `tools/*.py` still named the corpora and
still stated which file was attack traffic.  The argument for leaving them was
that they are never emitted.

The argument does not survive contact with how the skill is delivered.
`eval/bench/run-bench.sh` does

    cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca"

so EVERY BYTE of the arm directory lands in the workspace of the agent under
measurement.  `logmap.py` is a file in that workspace like any other; an agent
that opens it reads "on AIT-LDS the apache access log that is 90.2% attack
traffic" — a labelled answer about the corpus it is being scored on.  A
measurement whose subject can read that is not blind, and "the model probably
will not read the tool source" is a hope, not a property.  So the rule the v19
suite pins gets its scope corrected:

    a general tool may state a general property; it may not state a fact about
    a corpus it is measured on — in ANY byte that ships with it.

WHAT WAS NOT DONE, AND WHY
--------------------------
Deleting those comments would have been the cheap fix and the wrong one.  They
are the only record of why each axis, gate, cap and floor exists, each with the
measurement that forced it — the sort of reasoning whose absence lets a fixed
bug come back.  So the evidence was RELOCATED, not deleted: it lives in
`skills/DESIGN-EVIDENCE.md`, one directory ABOVE the arms, which is outside
everything `cp -r skills/<arm>` copies.  Each site keeps the general principle
and gains a pointer (`EVIDENCE §E<n>`), so a reader of the source still knows
that a measurement exists and where to find it.  Three tests below hold that
bargain up: the pointers must resolve, the numbers must be in the document, and
the document must not be inside an arm.

WHAT THIS ADDS TO THE v19 CHECKS
--------------------------------
Same four shapes of leak, now over three channels instead of one.

  * CHANNEL `docs`      — every non-`.py` file in the arm (`SKILL.md`,
                          `reference/*.md`, and any shipped script).  This is
                          v19's coverage.
  * CHANNEL `emitted`   — every non-docstring string literal in a `.py`, i.e.
                          what the tools can print.  Also v19's.
  * CHANNEL `prose`     — NEW: the comments and docstrings of every `.py`.
                          Never printed, always copied.

and two corrections to the checkers themselves, both of which are why this file
does not simply import v19's tests and point them at more files:

  1. `1,038` HID BEHIND ITS COMMA.  v19's number regex accepted the Russian
     spaced form (`1 038`) only, because `SKILL.md` is Russian.  The source
     comments are English and write `7,464 files` — so the same tally that fails
     the test in prose passed it in code.  Numbers here are comma-aware.
  2. A BARE NUMBER IN SOURCE IS USUALLY ARITHMETIC.  Applied unchanged to
     comments, the census fires on `23:50 against 00:10`, `and not 23 hours 40`
     and `` `22:22` and `127.0.0.1:8317` all fail `` — three lines about clocks
     and ports that collide with a testbed that happens to hold 22 hosts.  A
     test that cries wolf on a 24-hour clock trains people to ignore it.  So a
     census number counts only when it is next to the UNIT the census counted:
     files, hosts/machines, names, directories.  The numbers still come from
     walking the corpora — this cannot be satisfied by editing a list here.

Run it against any arm with `SHERLOCK_SKILL=`.  The frozen versions FAIL it, and
that is the correct answer: v14 is where the first corpus name entered the
source, and it grew monotonically to v18.  They are reported, never edited.
"""
import ast
import filecmp
import glob
import hashlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
SKILLS = os.path.join(SHERLOCK, "skills")
V19 = os.path.join(SKILLS, "v19")
V20 = os.path.join(SKILLS, "v20")
EVIDENCE = os.path.join(SKILLS, "DESIGN-EVIDENCE.md")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V20)


def arm_version(path):
    m = re.match(r"^v(\d+)$", os.path.basename(os.path.normpath(path)))
    return int(m.group(1)) if m else 0


def current_arms_at_least(n):
    out = []
    for arm in sorted(glob.glob(os.path.join(SKILLS, "v*"))):
        if os.path.isdir(arm) and arm_version(arm) >= n:
            out.append(arm)
    return out


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The definition of "corpus knowledge" lives in ONE place. Importing v19's suite
# rather than copying its checkers means a corpus added to the answer keys is
# covered in both files at once, and the two can never drift into disagreeing
# about what a leak is.
V19T = load_module(os.path.join(HERE, "test_skill_integrity_v19.py"),
                   "skill_integrity_v19")


# ---------------------------------------------------------------------------
# the checkers, with the two corrections
# ---------------------------------------------------------------------------
# comma-thousands OR the space-thousands form v19 already understood
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d[\d   ]*\d|\d")

# What the census counts is what a leaked tally counts. A number is a corpus
# tally when one of these sits next to it; `250 rows` and `hour 04` do not.
UNIT_WORDS = ("file", "файл", "host", "хост", "machine", "машин", "name",
              "имён", "имен", "director", "каталог", "basename")
UNIT_BEFORE = 16      # "one host, 108 files" — the unit can precede the number
UNIT_AFTER = 30       # "1,038 of that testbed's 2,092 basenames"

GT_EN = re.compile(r"\b(?:labell?ed|ground truth|answer key)\b", re.I)
GT_RU = ("размеч", "разметк", "ключ ответов", "эталонная разметка")


def census_hits(text, roots):
    """-> [(number, what it counts, line)] for every corpus tally in `text`."""
    census = V19T.corpus_census(roots)
    lines = text.splitlines()
    hits = []
    for m in NUMBER.finditer(text):
        try:
            n = int(re.sub(r"[,   ]", "", m.group(0)))
        except ValueError:
            continue
        if n not in census:
            continue
        window = text[max(0, m.start() - UNIT_BEFORE):m.end() + UNIT_AFTER].lower()
        if not any(u in window for u in UNIT_WORDS):
            continue
        ln = text[:m.start()].count("\n") + 1
        hits.append((n, census[n], lines[ln - 1].strip()[:100]))
    return sorted(set(hits))


def gt_hits(text):
    """Ground-truth vocabulary. English words are matched on word boundaries —
    `mislabelled .gz` is a fact about gzip, not about an answer key."""
    lines = text.splitlines()
    hits = []
    for m in GT_EN.finditer(text):
        ln = text[:m.start()].count("\n") + 1
        hits.append((m.group(0).lower(), ln, lines[ln - 1].strip()[:100]))
    low = text.lower()
    for w in GT_RU:
        for m in re.finditer(re.escape(w), low):
            ln = low[:m.start()].count("\n") + 1
            hits.append((w, ln, lines[ln - 1].strip()[:100]))
    return sorted(set(hits))


def prose_of(py_path):
    """-> the comments and docstrings of a Python file, as text whose line
    numbers still match the file. Everything else is blanked, so a hit's line
    number is the line a reader would open."""
    src = io.open(py_path, encoding="utf-8").read()
    keep = set()
    with io.open(py_path, encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type == tokenize.COMMENT:
                keep.add(tok.start[0])
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                first = body[0].lineno
                last = body[0].end_lineno or first
                for ln in range(first, last + 1):
                    keep.add(ln)
    lines = src.split("\n")
    return "\n".join(l if (i + 1) in keep else "" for i, l in enumerate(lines))


def channels(skill):
    """-> [(channel, relpath, text)] for every byte that ships in an arm."""
    out = []
    for dirpath, dirnames, filenames in os.walk(skill):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, skill)
            try:
                raw = io.open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            if fn.endswith(".py"):
                out.append(("prose", rel, prose_of(path)))
                out.append(("emitted", rel,
                            "\n".join(s for _l, s in V19T.emitted_strings(path))))
            else:
                out.append(("docs", rel, raw))
    return out


def leaks(skill, channel, kinds=("NAME", "GT", "CENSUS", "PATH")):
    """-> [(kind, relpath, detail, line)] — every leak of `channel` in `skill`."""
    _names, roots = V19T.measured_corpora()
    out = []
    for ch, rel, text in channels(skill):
        if ch != channel:
            continue
        if "NAME" in kinds:
            for tok, ln, line in V19T.forbidden_hits(text):
                out.append(("NAME", rel, tok, ln, line[:100]))
        if "GT" in kinds:
            for tok, ln, line in gt_hits(text):
                out.append(("GT", rel, tok, ln, line))
        if "CENSUS" in kinds and roots:
            for n, what, line in census_hits(text, roots):
                out.append(("CENSUS", rel, "%d (%s)" % (n, what), 0, line))
        if "PATH" in kinds and roots:
            for tok, relp, root in V19T.corpus_paths_in(text, roots):
                out.append(("PATH", rel, tok, 0, "%s (%s)" % (relp, root)))
    return sorted(set(out))


def report(hits):
    return "\n".join("  %-6s %-18s %-44s %s" % (k, r, str(d)[:44], l[:80])
                     for k, r, d, _n, l in hits)


# ===========================================================================
# the new channel: what the model can READ but is never SHOWN
# ===========================================================================
class SourceProseCarriesNoCorpusKnowledge(unittest.TestCase):
    """Comments and docstrings of every shipped `.py`."""

    def test_no_corpus_is_named(self):
        hits = leaks(UNDER_TEST, "prose", ("NAME",))
        self.assertEqual([], hits,
                         "a comment names a corpus this project measures on:\n"
                         + report(hits))

    def test_no_ground_truth_is_stated(self):
        """"90.2% labelled attack" is the answer, written in the tool that is
        supposed to find it."""
        hits = leaks(UNDER_TEST, "prose", ("GT",))
        self.assertEqual([], hits,
                         "a comment states ground truth:\n" + report(hits))

    def test_no_corpus_tally_is_quoted(self):
        _names, roots = V19T.measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        hits = leaks(UNDER_TEST, "prose", ("CENSUS",))
        self.assertEqual([], hits,
                         "a comment quotes a corpus tally:\n" + report(hits))

    def test_no_path_resolves_into_a_measured_corpus(self):
        """An example path that is a real file in a real corpus is a proof
        address, whatever the sentence around it says."""
        _names, roots = V19T.measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        hits = leaks(UNDER_TEST, "prose", ("PATH",))
        self.assertEqual([], hits,
                         "a comment writes a path that exists in a measured "
                         "corpus:\n" + report(hits))


class ShippedDocsCarryNoCorpusKnowledge(unittest.TestCase):
    """v19's checks, re-run against whatever arm is under test — and over EVERY
    non-`.py` file, not only `SKILL.md` and `reference/*.md`."""

    def test_docs_are_clean(self):
        hits = leaks(UNDER_TEST, "docs")
        self.assertEqual([], hits, "shipped text carries corpus knowledge:\n"
                         + report(hits))


class EmittedStringsCarryNoCorpusKnowledge(unittest.TestCase):

    def test_no_name_no_ground_truth_no_real_path(self):
        hits = leaks(UNDER_TEST, "emitted", ("NAME", "GT", "PATH"))
        self.assertEqual([], hits, "a printed string carries corpus knowledge:\n"
                         + report(hits))

    def test_the_one_known_emitted_tally_has_not_grown(self):
        """AN OPEN LEAK, PINNED RATHER THAN FIXED, and the reason is written
        down so nobody has to guess.

        `render_index()` prints, on any multi-host bundle, «250 строк на 22
        хоста — это 11 строк на машину, и улика из восьми строк в файле на 272
        строки в них не попадает».  Both halves are facts about one testbed: it
        has 22 hosts, and its opening privilege escalation is 8 marked lines in
        a 272-line file.  On a 5-host bundle the sentence is also simply WRONG.

        It is not fixed here because fixing it changes `map.txt` on every
        multi-host bundle, and `map.txt` is a measured artefact of arms that are
        being scored while this lands — the same reason the frozen arms are not
        edited.  Pinned instead: the inventory may not grow, and the next arm
        that re-measures the multi-host map owns the fix.

        2026-08-18, v21: that arm landed and the inventory is EMPTY — the
        sentence is now a general statement of the same lesson with no tally.
        The assertion is a SUBSET check rather than equality, which is what the
        contract above always said ("may not grow"): equality would turn the fix
        into a failure and force whoever fixed it to edit this test to be
        allowed to.  `test_skill_integrity_v21.py` asserts the empty inventory
        for arms from v21 on."""
        _names, roots = V19T.measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        hits = leaks(UNDER_TEST, "emitted", ("CENSUS",))
        known = {("tools/logmap.py", "22 (ait-russellmitchell: machines)")}
        got = {(r, d) for _k, r, d, _n, _l in hits}
        self.assertLessEqual(got, known,
                             "the emitted-tally inventory grew:\n" + report(hits))


# ===========================================================================
# relocate, do not delete
# ===========================================================================
POINTER = re.compile(r"EVIDENCE §(E\d+)")

# One number out of each relocated measurement. If the document still carries
# these, the reasoning came with it; if it does not, somebody deleted evidence
# and called it a cleanup.
RELOCATED_NUMBERS = (
    "90.2",          # the apache log the axis-0 floor was written for
    "8,486",         # configs against logs, the stream/state split
    "0.847",         # recall of that split
    "1,918",         # the collection timer that would have taken the metric slot
    "5,213,280",     # sub-4 KB files quoted verbatim, the map budget
    "7,762,064",     # the undivided map, in bytes
    "2,316",         # the auditd file that framing gets wrong by pid
    "1,920",         # the metric log axis 3 is silent on
    "5,537",         # the VPN log axis 5 was written for
    "113 of 250",    # the worklist the configs ate
    "15 rows",       # the blocklists the per-file state cap exists for
    "21 of 108",     # the citations the old extension gate dropped
    "7 of 7",        # the .gz files the binary guard refused
)


class TheEvidenceWasRelocatedNotDeleted(unittest.TestCase):

    def test_the_document_exists_and_is_not_inside_an_arm(self):
        self.assertTrue(os.path.isfile(EVIDENCE),
                        "the evidence document is gone: %s" % EVIDENCE)
        inside = os.path.dirname(EVIDENCE)
        self.assertEqual(SKILLS, inside,
                         "the evidence document must sit ABOVE the arms — "
                         "`cp -r skills/<arm>` must not be able to copy it")
        for arm in sorted(glob.glob(os.path.join(SKILLS, "v*"))):
            if os.path.isdir(arm):
                self.assertFalse(
                    os.path.exists(os.path.join(arm, os.path.basename(EVIDENCE))),
                    "a copy of the evidence document shipped inside %s"
                    % os.path.basename(arm))

    def test_every_pointer_in_the_source_resolves(self):
        body = io.open(EVIDENCE, encoding="utf-8").read()
        have = set(re.findall(r"^##\s+(E\d+)\b", body, re.M))
        want = set()
        for _ch, _rel, text in channels(UNDER_TEST):
            want |= set(POINTER.findall(text))
        self.assertTrue(want, "the source points at no evidence at all — the "
                              "comments were deleted, not relocated")
        self.assertEqual(set(), want - have,
                         "the source points at sections that do not exist: %s"
                         % sorted(want - have))
        max_e = arm_version(UNDER_TEST)
        future = {e for e in have if int(e[1:]) > max_e}
        self.assertEqual(set(), (have - future) - want,
                         "the document holds sections for this arm that nothing points at: %s"
                         % sorted((have - future) - want))

    @staticmethod
    def _flat(text):
        """Thousands separators are cosmetic: `8,486`, `8 486` and `8 486`
        are the same measurement, and the document is allowed to pick."""
        return re.sub(r"[,\u0020\u00a0\u2009]", "", text)

    def test_the_numbers_came_with_the_reasoning(self):
        body = self._flat(io.open(EVIDENCE, encoding="utf-8").read())
        missing = [n for n in RELOCATED_NUMBERS if self._flat(n) not in body]
        self.assertEqual([], missing,
                         "these measurements were dropped on the way out of the "
                         "source: %s" % missing)

    def test_every_section_says_which_corpus_and_when(self):
        """The comments carried the number, the corpus and the date. A document
        that keeps only the number is a worse record than the comment was."""
        body = io.open(EVIDENCE, encoding="utf-8").read()
        sections = re.split(r"^##\s+", body, flags=re.M)[1:]
        thin = [s.split("\n", 1)[0] for s in sections
                if not re.search(r"\b2026-\d{2}-\d{2}\b", s)]
        self.assertEqual([], thin,
                         "sections with no date: %s" % thin)


class CurrentArmCarriesNoObservedSemanticHints(unittest.TestCase):
    """v26 and later may describe the rule generically, but not ship the old
    domain-shaped phrase that taught the model a solved-looking conclusion."""

    SEMANTIC_HINTS = ("Штатный резолвинг", "штатный резолвинг")

    def test_current_arms_do_not_reintroduce_the_v25_hint_phrase(self):
        arms = current_arms_at_least(26)
        if not arms:
            self.skipTest("no v26+ arm in this checkout")
        hits = []
        for arm in arms:
            for ch, rel, text in channels(arm):
                for hint in self.SEMANTIC_HINTS:
                    if hint in text:
                        hits.append((os.path.basename(arm), ch, rel, hint))
        self.assertEqual([], hits,
                         "current shipped arms carry the v25 semantic hint phrase: %s"
                         % hits)


class NoDocstringIsEmitted(unittest.TestCase):
    """The premise the v19 decision rested on, checked instead of believed.

    If `argparse` ever gets `description=__doc__`, every relocated sentence
    would be printed to the model on `--help`, and the census above would go on
    passing because it only reads the file."""

    def _fragments(self, py):
        src = io.open(py, encoding="utf-8").read()
        doc = ast.get_docstring(ast.parse(src)) or ""
        return [l.strip() for l in doc.split("\n") if len(l.strip()) >= 40]

    def test_help_output_never_carries_the_module_docstring(self):
        for name in ("logmap.py", "citecheck.py", "logjoin.py"):
            py = os.path.join(UNDER_TEST, "tools", name)
            frags = self._fragments(py)
            self.assertTrue(frags, "%s has no module docstring at all" % name)
            p = subprocess.run([sys.executable, py, "--help"],
                               capture_output=True, text=True)
            blob = p.stdout + p.stderr
            for f in frags:
                self.assertNotIn(f, blob, "%s --help prints its docstring" % name)

    def test_a_real_run_never_carries_the_module_docstring(self):
        py = os.path.join(UNDER_TEST, "tools", "logmap.py")
        frags = self._fragments(py)
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            V19T.make_tiny_corpus(corpus)
            out = os.path.join(tmp, "work")
            p = V19T.run_logmap(UNDER_TEST, corpus, out)
            self.assertEqual(0, p.returncode, p.stderr)
            blob = p.stdout + p.stderr
            for fn in sorted(os.listdir(out)):
                blob += io.open(os.path.join(out, fn), encoding="utf-8").read()
        for f in frags:
            self.assertNotIn(f, blob, "logmap.py leaks its docstring into work/")


# ===========================================================================
# behaviour must not have moved
# ===========================================================================
class V20IsV19WithTheProseMoved(unittest.TestCase):
    """This is the whole safety argument for the change, and it is structural
    rather than a diff review: two files with the same abstract syntax tree, once
    docstrings are stripped, run the same code. Comments are not in an AST at
    all, so a comment-only edit cannot fail this and a code edit cannot pass it."""

    def setUp(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V20):
            self.skipTest("only meaningful for v20")

    @staticmethod
    def _stripped_ast(path):
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(node, (ast.Module, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef))
                    and body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                del body[0]
        return ast.dump(tree)

    def test_the_code_is_identical(self):
        for name in ("logmap.py", "citecheck.py", "logjoin.py"):
            self.assertEqual(self._stripped_ast(os.path.join(V19, "tools", name)),
                             self._stripped_ast(os.path.join(V20, "tools", name)),
                             "%s is not a comment-only change" % name)

    def test_what_the_model_reads_is_byte_identical(self):
        for rel in ("SKILL.md", "reference/tools.md", "reference/report-format.md",
                    "reference/code-and-spec.md"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V19, rel), os.path.join(V20, rel),
                            shallow=False),
                "%s changed — v20 is a source-comment change only" % rel)

    def test_the_three_artefacts_are_byte_identical(self):
        """Synthetic corpora, so this bar travels: it needs no dataset and runs
        on a laptop. The real corpora are compared in the PR."""
        for maker in (V19T.make_tiny_corpus, V19T.make_gz_corpus):
            with tempfile.TemporaryDirectory() as tmp:
                corpus = os.path.join(tmp, "corpus")
                os.makedirs(corpus, exist_ok=True)
                maker(corpus)
                outs = {}
                for tag, skill in (("v19", V19), ("v20", V20)):
                    outs[tag] = os.path.join(tmp, tag)
                    p = V19T.run_logmap(skill, corpus, outs[tag])
                    self.assertEqual(0, p.returncode, p.stderr)
                names = sorted(os.listdir(outs["v19"]))
                self.assertEqual(names, sorted(os.listdir(outs["v20"])))
                for fn in names:
                    a = os.path.join(outs["v19"], fn)
                    b = os.path.join(outs["v20"], fn)
                    self.assertTrue(filecmp.cmp(a, b, shallow=False),
                                    "%s differs between v19 and v20 (%s)"
                                    % (fn, maker.__name__))


class FrozenArms(unittest.TestCase):
    """v19 is what PR #21's census was taken with. Pinned by content."""

    EXPECTED_V19 = {
        "SKILL.md": "839b89aa72fb1dc04ec4197259ef4d11",
        "tools/logmap.py": "d3f34acb0bdf3035a93b505a6b4de604",
        "tools/citecheck.py": "05d090170d7053b7ce2a79919fa1bac4",
        "tools/logjoin.py": "bbf1203367d13f7e4163a2a75fb74e5d",
        "reference/report-format.md": "d19a98be30ab2b52fd30cceca3860169",
        "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
        "reference/tools.md": "743808781ba175741c392f258d035065",
    }

    def test_v19_is_frozen(self):
        for rel, want in sorted(self.EXPECTED_V19.items()):
            path = os.path.join(V19, rel)
            got = hashlib.md5(open(path, "rb").read()).hexdigest()
            self.assertEqual(want, got, "%s changed in v19" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
