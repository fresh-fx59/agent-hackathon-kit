#!/usr/bin/env python3
"""Tests for the three v19 integrity fixes in the shipped skill.

    python3 tools/tests/test_skill_integrity_v19.py

WHY THIS TEST EXISTS (2026-08-18)
---------------------------------
The skill is the thing under measurement. Three separate defects, all of them
found by the skill's own blind arms, all of them the same shape: something the
skill hands the model is not what the tool's own behaviour says it is.

D1 — THE SKILL CARRIED AN ANSWER ABOUT ITS OWN TEST CORPUS.  `SKILL.md` and
`logmap.py`'s map legend both stated, as a lesson, that one named corpus holds
exactly eight `state`-kind files and that all eight turned out to be the
attacker's tooling.  It is emitted into every `map.txt`, so every arm since v14
was handed it.  Three problems, each fatal on its own:

  * it PRE-CLASSIFIES a directory of that corpus as attacker tooling, and that
    directory is where a third of the planted defects are proved;
  * it is STALE — the same tool's own map on that corpus classifies four files
    as `state`, not eight;
  * it is CORPUS knowledge inside a GENERAL tool.

And it was not alone.  Five more sites named a measured corpus, and one of them
quoted, as a neutral example of how to write a citation, a path and line number
that is a LABELLED ATTACK LINE in that corpus's shipped ground truth.

The lesson each site was carrying is real and is kept — restated as a property
of logs, with no corpus name, no corpus tally and no claim about what some
directory turned out to contain.

The rule this pins: **a general tool may state a general property; it may not
state a fact about a corpus it is measured on.**  The check has two halves.  A
token half — the name of every corpus this project holds an answer key for is
forbidden, and the list is READ FROM THE ANSWER KEYS, so a corpus added later is
covered without touching this file.  And an on-disk half — no path written in
the skill may resolve to a real file inside any of those corpora.  The second is
what caught the citation example.

D2 — `citecheck` CALLED EVERY `.gz` A BINARY.  v13 added a binary guard so a
citation could not be certified into an `.evtx` or a `.pcap`, where "line 40"
does not exist as text.  That guard is right.  But it read the RAW bytes, and a
gzip stream is full of NULs, so every gzipped text log was rejected — while the
same tool's `read_lines()` opens exactly those files with `gzip.open` and reads
them fine.  Measured on the negative-control corpus: 7 of 7 `.gz` files, all of
them plain text once decompressed, `binary-file`, ok 0.  `logmap.looks_binary`
already had it right (it goes through `opener(path)`), and `citecheck`'s own
docstring claimed the two tools agreed.  They did not.

D3 — WHAT THE MODEL IS SHOWN MUST BE CITABLE.  Under 4 KB a file is quoted whole
into `map.txt`, and it was quoted WITHOUT LINE NUMBERS — and, because the map
already showed it, `analyse()` also suppressed its axis-0 floor row.  So the
model could read the content and had nothing legal to cite, and no worklist row
obliged anyone to reach a verdict on it.  Measured on the Windows corpus: 26 of
108 files quoted verbatim, 25 of them with no worklist row at all, and three of
those carry the whole proof of one planted defect and part of another.  A
finding that cannot be cited is a finding `citecheck` must reject, so being
shown and being citable have to be the same thing.

D4 — A REJECTION NEEDS THE SAME EVIDENCE AS AN ASSERTION.  The Windows arm's
single miss aggregated the right channel, listed the right records with the
right count, and rejected them for carrying no download string — while the
discriminator sat in the same record, inside an escaped `\r\n\t` key=value blob
that the template never projected.  That generalises without naming anything:
an un-projected field is not evidence of absence.  It went into `SKILL.md` as a
rule, not into the tool as a new projection — projecting every embedded pair is
an axis-design change with unmeasured effects on three corpora, and this is an
integrity fix with a fixed regression bar.

The synthetic corpora here are invented end to end, so the suite needs no
dataset and runs anywhere; the real corpora are used as an extra bar when they
happen to be present.
"""
import ast
import glob
import gzip
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
SKILLS = os.path.join(SHERLOCK, "skills")
V18 = os.path.join(SKILLS, "v18")
V19 = os.path.join(SKILLS, "v19")
BENCH = os.path.join(SHERLOCK, "eval", "bench")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V19)


# ---------------------------------------------------------------------------
# what counts as corpus knowledge
# ---------------------------------------------------------------------------
def measured_corpora():
    """-> ([dataset names], [corpus roots that exist on disk]).

    Read from the answer keys, not hard-coded: this project measures on exactly
    the corpora it holds a key for, so a corpus added later is covered here
    without anybody remembering to come back."""
    names, roots = [], []
    for path in sorted(glob.glob(os.path.join(BENCH, "answer-key*.json"))):
        try:
            with io.open(path, encoding="utf-8") as fh:
                key = json.load(fh)
        except (OSError, ValueError):
            continue
        name = key.get("dataset")
        if name:
            names.append(name)
        root = key.get("corpus_root") or ""
        if root and os.path.isdir(root):
            roots.append(root)
    return names, roots


# The families this project has measured on. The answer keys give the dataset
# names; these are the names the same corpora go by in prose, which is how they
# got written into the skill in the first place.
CORPUS_WORDS = ("bluesky", "ait-lds", "ait_lds", "aitlds", "cam-lds", "camlds",
                "russellmitchell", "santos", "cyberdefenders", "pwnkit")

# Ground truth is something a corpus HAS and a tool does not. A skill that talks
# about labelled lines is talking about an answer key.
GROUND_TRUTH_WORDS = ("размеч", "разметк", "labelled", "labeled", "ground truth",
                      "answer key", "ключ ответов", "эталонная разметка")


def forbidden_hits(text):
    """-> [(token, line number, line)] for every corpus name in `text`."""
    names, _roots = measured_corpora()
    words = list(CORPUS_WORDS) + [n.lower() for n in names]
    low = text.lower()
    lines = text.splitlines()
    hits = []
    for w in sorted(set(words)):
        for m in re.finditer(re.escape(w), low):
            ln = low[:m.start()].count("\n") + 1
            hits.append((w, ln, lines[ln - 1].strip()))
    # `AIT` on its own is a corpus name; `ait` inside a Russian word is not.
    for m in re.finditer(r"\bAIT\b", text):
        ln = text[:m.start()].count("\n") + 1
        hits.append(("AIT", ln, lines[ln - 1].strip()))
    return sorted(set(hits), key=lambda h: (h[1], h[0]))


def ground_truth_hits(text):
    low = text.lower()
    lines = text.splitlines()
    hits = []
    for w in GROUND_TRUTH_WORDS:
        for m in re.finditer(re.escape(w), low):
            ln = low[:m.start()].count("\n") + 1
            hits.append((w, ln, lines[ln - 1].strip()))
    return sorted(set(hits), key=lambda h: (h[1], h[0]))


PATH_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+")

# Below this a census number is too ordinary to be a leak — "три оси", "40 строк"
# are the tool's own constants, not facts about a testbed. And a corpus of a few
# dozen files has a census made entirely of such ordinary numbers, so only the
# real testbeds are censused: their tallies are distinctive enough to be a tell.
CENSUS_FLOOR = 20
CENSUS_MIN_FILES = 100
NUMBER = re.compile(r"\d[\d  \u00a0\u2009]*")


def corpus_census(roots):
    """-> {number: what it counts}. The tallies a writer reaches for when they
    describe a corpus instead of a mechanism: how many files it holds, how many
    machines, how many distinct file names, how many of those names collide.
    Anything derived from a corpus by counting is a fact about that corpus."""
    import collections
    census = {}
    for root in roots:
        files, bases = 0, collections.Counter()
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
            for f in fn:
                files += 1
                bases[f] += 1
        if files < CENSUS_MIN_FILES:
            continue
        tops = len([d for d in os.listdir(root)
                    if os.path.isdir(os.path.join(root, d))])
        collide = sum(1 for _b, c in bases.items() if c > 1)
        name = os.path.basename(root.rstrip("/"))
        for n, what in ((files, "files"), (tops, "machines"),
                        (len(bases), "distinct file names"),
                        (collide, "colliding file names")):
            if n >= CENSUS_FLOOR:
                # ±1: an earlier hand count can be off by a dotfile, and the
                # sentence is just as much a corpus fact when it is
                for d in (-1, 0, 1):
                    census.setdefault(n + d, "%s: %s" % (name, what))
    return census


def census_hits(text, roots):
    """-> [(number, what it counts, line)] for every corpus tally in `text`."""
    census = corpus_census(roots)
    lines = text.splitlines()
    hits = []
    for m in NUMBER.finditer(text):
        raw = m.group(0).strip()
        try:
            n = int(re.sub(r"[  \u00a0\u2009]", "", raw))
        except ValueError:
            continue
        if n in census:
            ln = text[:m.start()].count("\n") + 1
            hits.append((n, census[n], lines[ln - 1].strip()))
    return sorted(set(hits))


def corpus_paths_in(text, roots):
    """-> [(token, relpath it resolves to, root)].

    A path of two or more components that names a real file inside a corpus we
    measure on. A bare basename is not enough — `auth.log` is a fact about
    Linux, `intranet_server/logs/auth.log` is a fact about one testbed."""
    hits = []
    for root in roots:
        index = set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                index.add(rel.replace(os.sep, "/"))
        for m in PATH_TOKEN.finditer(text):
            tok = m.group(0).strip("./")
            if tok.count("/") < 1:
                continue
            for rel in index:
                if rel == tok or rel.endswith("/" + tok):
                    hits.append((tok, rel, os.path.basename(root)))
                    break
    return sorted(set(hits))


def emitted_strings(py_path):
    """-> [(line number, string)] for every string literal that is NOT a
    docstring. Comments never reach the model; a string literal can."""
    with io.open(py_path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            out.append((node.lineno, node.value))
    return out


# ---------------------------------------------------------------------------
# synthetic corpora — invented end to end
# ---------------------------------------------------------------------------
def _write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, mode, encoding=None if "b" in mode else "utf-8") as fh:
        fh.write(text)


def make_gz_corpus(root):
    """Four files: gzipped text, gzipped binary, plain text, plain binary."""
    # line 1 says something no other line says, so a citation to the wrong line
    # can be caught as wrong-content rather than passing on family resemblance
    text = ("2031-03-01T00:00:00+00:00 kestrel[3999]: rota handover accepted "
            "from the night warden\n") + "".join(
        "2031-03-0%dT00:%02d:11+00:00 kestrel[%d]: batch %d flushed to spool\n"
        % (1 + i // 20, i % 60, 4000 + i, i) for i in range(2, 40))
    with gzip.open(os.path.join(root, "kestrel.log.1.gz"), "wt",
                   encoding="utf-8") as fh:
        fh.write(text)
    with gzip.open(os.path.join(root, "capture.bin.gz"), "wb") as fh:
        fh.write(b"\x00\x01MAGIC\x00\x00" + bytes(range(256)) * 40)
    _write(os.path.join(root, "kestrel.log"), text)
    _write(os.path.join(root, "capture.bin"),
           b"\x00\x01MAGIC\x00\x00" + bytes(range(256)) * 40, mode="wb")
    return text.splitlines()


TINY = ("# spool-guard notes, kept next to the logs\n"
        "owner: quill\n"
        "escalation: page quill, then page the duty rota\n"
        "note: the spare token in vault/quill-spare is NOT rotated\n")


def make_tiny_corpus(root):
    """One file well under the inline threshold, one ordinary stream."""
    _write(os.path.join(root, "notes", "spool-guard.txt"), TINY)
    _write(os.path.join(root, "logs", "spool.log"), "".join(
        "2031-03-01T0%d:%02d:%02d+00:00 spool[77]: slice %d written ok\n"
        % (1 + i // 900, (i // 15) % 60, i % 60, i) for i in range(400)))
    return TINY.splitlines()


def run_logmap(skill, corpus, out, extra=()):
    p = subprocess.run(
        [sys.executable, os.path.join(skill, "tools", "logmap.py"), corpus,
         "--out", out, *extra], capture_output=True, text=True)
    return p


def run_citecheck(skill, report_text, corpus, extra=()):
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8",
                                     delete=False) as fh:
        fh.write(report_text)
        path = fh.name
    try:
        p = subprocess.run(
            [sys.executable, os.path.join(skill, "tools", "citecheck.py"), path,
             "--corpus", corpus, "--json", *extra],
            capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout or "{}"), p.stderr
    finally:
        os.unlink(path)


# ===========================================================================
# D1 — no corpus knowledge in what the skill says
# ===========================================================================
class SkillTextCarriesNoCorpusKnowledge(unittest.TestCase):

    def _docs(self):
        out = [os.path.join(UNDER_TEST, "SKILL.md")]
        out += sorted(glob.glob(os.path.join(UNDER_TEST, "reference", "*.md")))
        return out

    def test_no_corpus_is_named(self):
        for doc in self._docs():
            with io.open(doc, encoding="utf-8") as fh:
                hits = forbidden_hits(fh.read())
            self.assertEqual(
                [], hits,
                "%s names a corpus this project measures on:\n%s"
                % (os.path.relpath(doc, SHERLOCK),
                   "\n".join("  line %d: %s -> %s" % (l, t, s)
                             for t, l, s in hits)))

    def test_no_ground_truth_vocabulary(self):
        """A general tool has no labelled lines. Talking about them means the
        author was reading an answer key while writing the skill."""
        for doc in self._docs():
            with io.open(doc, encoding="utf-8") as fh:
                hits = ground_truth_hits(fh.read())
            self.assertEqual(
                [], hits,
                "%s talks about ground-truth labels:\n%s"
                % (os.path.relpath(doc, SHERLOCK),
                   "\n".join("  line %d: %s -> %s" % (l, t, s)
                             for t, l, s in hits)))

    def test_no_path_resolves_into_a_measured_corpus(self):
        """The half that caught the citation example: a path written in the
        skill as an illustration must not be a real file in a real corpus."""
        _names, roots = measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        for doc in self._docs():
            with io.open(doc, encoding="utf-8") as fh:
                hits = corpus_paths_in(fh.read(), roots)
            self.assertEqual(
                [], hits,
                "%s writes a path that exists in a measured corpus:\n%s"
                % (os.path.relpath(doc, SHERLOCK),
                   "\n".join("  %s -> %s (%s)" % h for h in hits)))

    def test_no_corpus_tally_is_quoted(self):
        """The third shape. «22 хоста», «1 038 имён из 2 092» describe a
        testbed, not a mechanism — and a count of the corpus is a small piece of
        its answer key, because it tells the model how big the haystack is
        before it has looked. The census is COMPUTED from the corpora on disk,
        so this cannot be satisfied by editing a list in this file."""
        _names, roots = measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        for doc in self._docs():
            with io.open(doc, encoding="utf-8") as fh:
                hits = census_hits(fh.read(), roots)
            self.assertEqual(
                [], hits,
                "%s quotes a corpus tally:\n%s"
                % (os.path.relpath(doc, SHERLOCK),
                   "\n".join("  %d (%s) -> %s" % h for h in hits)))

    def test_the_state_lesson_survived_as_a_general_rule(self):
        """Deleting the paragraph is not the fix. The property it taught —
        a `state` file can hold the heaviest evidence in the bundle, so it gets
        its own budget and is never discarded — has to still be there."""
        with io.open(os.path.join(UNDER_TEST, "SKILL.md"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("«Состояние» не значит «неважно»", body)
        self.assertIn("authorized_keys", body)
        self.assertIn("отдельная, малая доля", body)
        self.assertIn("никогда", body)

    def test_a_rejection_carries_the_same_evidence_as_an_assertion(self):
        """D4, and the only part of it that generalises. The Windows arm's one
        miss aggregated the right records, counted them right and REJECTED them
        for carrying no download string — while the discriminator sat in the
        same record, inside an escaped key=value blob the template never
        projected. That is a reasoning rule, not a corpus fact: an un-projected
        field is not evidence of absence, and a rejection needs a record that
        was actually read. Named here so it cannot be quietly dropped."""
        with io.open(os.path.join(UNDER_TEST, "SKILL.md"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("Отказ доказывается так же, как утверждение", body)
        self.assertIn("Непоказанное поле не есть отсутствующее поле", body)
        self.assertIn("ключ=значение", body)
        # and it must state the rule without naming what the answer was
        self.assertEqual([], forbidden_hits(body))


class EmittedTextCarriesNoCorpusKnowledge(unittest.TestCase):
    """Every string literal the tools can print, and then the real output."""

    TOOLS = ("logmap.py", "citecheck.py", "logjoin.py")

    def test_no_corpus_name_in_any_string_literal(self):
        for name in self.TOOLS:
            path = os.path.join(UNDER_TEST, "tools", name)
            bad = []
            for lineno, s in emitted_strings(path):
                hits = forbidden_hits(s) + ground_truth_hits(s)
                if hits:
                    bad.append((lineno, s.strip()[:90]))
            self.assertEqual([], bad,
                             "%s emits corpus knowledge:\n%s"
                             % (name, "\n".join("  line %d: %s" % b for b in bad)))

    def test_no_corpus_name_in_the_map_it_actually_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            make_tiny_corpus(corpus)
            out = os.path.join(tmp, "work")
            p = run_logmap(UNDER_TEST, corpus, out)
            self.assertEqual(0, p.returncode, p.stderr)
            blob = p.stdout
            for fn in sorted(os.listdir(out)):
                with io.open(os.path.join(out, fn), encoding="utf-8") as fh:
                    blob += fh.read()
            hits = forbidden_hits(blob) + ground_truth_hits(blob)
            self.assertEqual([], hits,
                             "the tool's own output names a corpus:\n%s"
                             % "\n".join("  %s -> %s" % (t, s) for t, _l, s in hits))


# ===========================================================================
# D2 — a gzipped text log is text
# ===========================================================================
class GzippedTextIsCitable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(cls.corpus)
        cls.lines = make_gz_corpus(cls.corpus)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _report(self, cite, quote):
        return ("# Отчёт\n\n## Н-1\n\nСпул сбросил партию на диск: «%s» — %s\n"
                % (quote, cite))

    def _verdict(self, cite, quote, skill=None):
        rc, data, err = run_citecheck(skill or UNDER_TEST,
                                      self._report(cite, quote), self.corpus)
        cites = data.get("citations") or data.get("cites") or []
        self.assertTrue(cites, "no citation parsed: %r %s" % (data, err))
        return cites[0].get("verdict")

    def test_gz_of_text_is_citable_by_line_number(self):
        quote = self.lines[11]
        self.assertEqual("ok", self._verdict("kestrel.log.1.gz:12", quote))

    def test_gz_line_numbers_are_the_decompressed_ones(self):
        """Line 12 of the gunzipped stream, not of the compressed bytes."""
        rc, data, err = run_citecheck(
            UNDER_TEST, self._report("kestrel.log.1.gz:12", self.lines[0]),
            self.corpus)
        v = (data.get("citations") or [{}])[0].get("verdict")
        self.assertEqual("wrong-content", v,
                         "line 1's text must not verify against line 12")

    def test_gz_out_of_range_is_still_out_of_range(self):
        self.assertEqual(
            "out-of-range",
            self._verdict("kestrel.log.1.gz:9999", self.lines[0]))

    def test_gz_of_binary_is_still_refused(self):
        """The v13 guard is right and stays: a `.gz` of a PE is a binary."""
        self.assertEqual("binary-file",
                         self._verdict("capture.bin.gz:12", "MAGIC batch"))

    def test_plain_binary_is_still_refused(self):
        self.assertEqual("binary-file",
                         self._verdict("capture.bin:12", "MAGIC batch"))

    def test_plain_text_is_unaffected(self):
        self.assertEqual("ok", self._verdict("kestrel.log:12", self.lines[11]))

    def test_the_two_tools_agree_on_what_is_unreadable(self):
        """citecheck's guard says it uses the same test as logmap's. Assert it,
        rather than believing the docstring — that claim was false in v18."""
        cc = load_module(os.path.join(UNDER_TEST, "tools", "citecheck.py"), "cc19")
        lm = load_module(os.path.join(UNDER_TEST, "tools", "logmap.py"), "lm19")
        for fn in ("kestrel.log.1.gz", "capture.bin.gz", "kestrel.log",
                   "capture.bin"):
            p = os.path.join(self.corpus, fn)
            self.assertEqual(lm.looks_binary(p), cc.looks_binary(p),
                             "the two tools disagree about %s" % fn)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# D3 — anything the model is shown must be citable
# ===========================================================================
class InlinedContentIsAddressed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.corpus = os.path.join(cls.tmp, "corpus")
        cls.lines = make_tiny_corpus(cls.corpus)
        cls.out = os.path.join(cls.tmp, "work")
        cls.proc = run_logmap(UNDER_TEST, cls.corpus, cls.out)
        with io.open(os.path.join(cls.out, "map.txt"), encoding="utf-8") as fh:
            cls.map_txt = fh.read()
        with io.open(os.path.join(cls.out, "worklist.tsv"), encoding="utf-8") as fh:
            cls.worklist = fh.read()

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_small_file_is_still_shown_whole(self):
        self.assertEqual(0, self.proc.returncode, self.proc.stderr)
        for line in self.lines:
            self.assertIn(line, self.map_txt,
                          "the map stopped showing the file: %r" % line)

    def test_every_shown_line_carries_its_line_number(self):
        """The map's verbatim block is a quote of a real file, so each quoted
        line has to say WHICH line it is — otherwise the model can read it and
        has nothing legal to cite."""
        block = self._verbatim_block()
        self.assertTrue(block, "no verbatim block in the map")
        for i, raw in enumerate(block, 1):
            self.assertRegex(
                raw, r"^\s*%d\s*\|" % i,
                "line %d of the inlined file has no address: %r" % (i, raw))

    def test_a_quoted_line_actually_verifies(self):
        """End to end: take a line out of the map the way a model would, cite
        it, and make citecheck certify it."""
        block = self._verbatim_block()
        m = re.match(r"^\s*(\d+)\s*\|\s?(.*)$", block[2])
        self.assertTrue(m, block[2])
        n, text = int(m.group(1)), m.group(2)
        report = ("# Отчёт\n\n## Н-1\n\nЗапасной токен не ротируется: «%s» "
                  "— notes/spool-guard.txt:%d\n" % (text.strip(), n))
        rc, data, err = run_citecheck(UNDER_TEST, report, self.corpus)
        v = (data.get("citations") or [{}])[0].get("verdict")
        self.assertEqual("ok", v, "%r %s" % (data, err))

    def test_a_truncated_quote_says_it_is_truncated(self):
        """A quote cut at 300 characters is not the line. If the map does not
        say so, the model quotes the cut string next to a correct line number
        and `citecheck` answers `wrong-content` — the gate rejecting a true
        finding, which is the same failure as certifying a false one."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            _write(os.path.join(corpus, "notes", "long.txt"),
                   "head\n" + ("q" * 900) + "\ntail\n")
            out = os.path.join(tmp, "work")
            p = run_logmap(UNDER_TEST, corpus, out)
            self.assertEqual(0, p.returncode, p.stderr)
            with io.open(os.path.join(out, "map.txt"), encoding="utf-8") as fh:
                body = fh.read()
        self.assertIn("обрезано", body,
                      "a cut quote is presented as if it were the whole line")

    def test_the_worklist_is_NOT_the_fix_here(self):
        """Measured, not assumed. The other option in the brief was to stop
        inlining and hand these files worklist rows instead. It cannot reach
        them: the axis-0 floor is STREAM-only, and every one of the artefacts
        that motivated this fix is `state` — a script, a dumped host list, a web
        root. Removing the `verbatim is None` clause from `analyse()` would give
        floor rows to small STREAM files and still leave the proof files with
        nothing. So the fix is the address, and this test pins the fact that
        made the choice, so a later reader does not re-litigate it from memory."""
        lm = load_module(os.path.join(UNDER_TEST, "tools", "logmap.py"), "lm19b")
        path = os.path.join(self.corpus, "notes", "spool-guard.txt")
        axis = lm.time_axis(path, lm.probe(path)[0])["verdict"]
        self.assertEqual("state", axis)
        self.assertFalse(lm.draws_stream_budget(axis),
                         "a state artefact cannot be reached by the axis-0 floor")

    def _verbatim_block(self):
        lines = self.map_txt.splitlines()
        for i, line in enumerate(lines):
            if "ДОСЛОВНО" in line:
                block = []
                for raw in lines[i + 1:]:
                    if not raw.strip():
                        break
                    block.append(raw)
                return block
        return []


# ===========================================================================
# the arms that were already measured must not move
# ===========================================================================
class FrozenArms(unittest.TestCase):

    def test_v18_is_a_measured_arm_and_is_frozen(self):
        """v18 is what PR #18's numbers were taken with. Pinned by content, not
        by a neighbour comparison, which can be satisfied by moving both."""
        import hashlib
        for rel, want in EXPECTED_V18.items():
            path = os.path.join(V18, rel)
            with open(path, "rb") as fh:
                got = hashlib.md5(fh.read()).hexdigest()
            self.assertEqual(want, got, "%s changed in v18" % rel)

    def test_v19_touches_only_what_it_had_to(self):
        """Everything v19 does NOT change must be byte-identical to v18."""
        import filecmp
        # reference/tools.md is NOT in this list: it carried one of the corpus
        # tallies, so D1 had to reach it too.
        for rel in ("reference/report-format.md", "reference/code-and-spec.md",
                    "tools/logjoin.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V18, rel), os.path.join(V19, rel),
                            shallow=False),
                "%s must not change in v19" % rel)


EXPECTED_V18 = {
    "SKILL.md": "11840369b8052ed1ee328208a57d0d5a",
    "tools/logmap.py": "3eceaaedaf6baffe1dd0b63af6df9e7b",
    "tools/citecheck.py": "35daf36156ee33fdd346065b460f6263",
    "tools/logjoin.py": "bbf1203367d13f7e4163a2a75fb74e5d",
    "reference/report-format.md": "d19a98be30ab2b52fd30cceca3860169",
    "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
    "reference/tools.md": "17fd2dbc149b4bf54563625d0de13cc2",
}


if __name__ == "__main__":
    unittest.main(verbosity=2)
