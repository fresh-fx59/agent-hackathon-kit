#!/usr/bin/env python3
"""The last emitted leak, and the shape of leak the census could not see.

    python3 tools/tests/test_skill_integrity_v21.py
    SHERLOCK_SKILL=$PWD/skills/v19 python3 tools/tests/test_skill_integrity_v21.py

WHY THIS TEST EXISTS (2026-08-18)
---------------------------------
v19 took every corpus-specific claim out of what the skill SAYS.  v20 took them
out of the tool SOURCE and relocated the measurements to `skills/DESIGN-EVIDENCE.md`.
Both left ONE site standing, and wrote down why: `logmap.py`'s multi-host
preamble prints

    «250 строк на 22 хоста — это 11 строк на машину, и улика из восьми строк в
     файле на 272 строки в них не попадает»

into `map.txt` on every multi-host bundle.  It was pinned rather than fixed
because fixing it moves a measured artefact, and two arms were mid-flight.  They
have landed, so it is fixed here.

THREE DEFECTS IN ONE SENTENCE, AND ONLY ONE OF THEM WAS GUARDED
---------------------------------------------------------------
1.  **A 22-host tally.**  Caught by v19's census, which counts files, hosts,
    distinct names and colliding names per corpus.  It is why the sentence was
    findable at all — and it is wrong arithmetic on any bundle that is not that
    testbed: a 5-host bundle was told about 22 hosts.

2.  **A needle shape.**  «улика из восьми строк в файле на 272 строки» is the
    answer key read out loud: how many lines the evidence occupies and how long
    the file holding it is.  THE CENSUS IS STRUCTURALLY BLIND TO IT.  It counts
    file/host/name tallies; a labelled-line count is neither.  And «восьми» is a
    WORD — v19's and v20's number regexes only ever look at digits.  So the half
    of the sentence that carries the most answer was the half nothing checked.

3.  **A hard-coded budget.**  `250` is printed next to the same paragraph's own
    `%d`-formatted cap.  Run with `--worklist-cap 40` and the map states both 40
    and 250 as the budget, one of which is false.

Each gets its own check below, and the third is the general one: a paragraph
that explains a budget may state only numbers this run measured.  It subsumes
the other two — a corpus tally and a needle shape are both numbers no run
produced — which is the point.  A census is a blocklist of known-bad numbers; the
budget check is an allowlist of numbers the run can justify.

SCOPE OF THE FIX, AND WHY IT IS TWO SITES
------------------------------------------
The brief named `render_index()`.  `grep` found the same two lines a second time
in `hosts_block()`, which `render_map()` uses whenever a multi-host bundle is run
with `--map-cap 0` — the switch v17 added precisely so the map budget can be
measured off.  Fixing one site would have left the sentence live on the path
used to measure the other.  Both checks below run both paths.

THE LESSON IS KEPT, RESTATED WITHOUT THE CORPUS
------------------------------------------------
Deleting the sentence is not the fix; the sentence was teaching something true —
a fixed worklist budget divided among machines is thin per machine, which is why
each host is given the cap whole.  That is a property of division, and it can be
said with no tally, no corpus and no needle.  `test_the_lesson_survived_as_a_
general_rule` holds the restatement in place so a later cleanup cannot quietly
drop it.

D09 — THE FAILURE CLASS THE ARMS NOW SHARE
-------------------------------------------
The Windows arm's remaining miss was NOT a budget failure and NOT a format
failure.  The worklist HAD a row on the evidence, the analyst READ it, marked it
a defect — and filed it under a neighbouring finding, because both touched the
same port.  A second finding was anchored correctly and then argued away as
predating the compromise.  Nothing was rejected in either case, so v19's rule
(«отказ доказывается так же, как утверждение») does not reach them: the evidence
was found, cited, and MIS-ASSIGNED.

That generalises without naming anything.  A record can be consistent with two
conclusions; choosing one is itself an assertion and carries the same burden of
proof.  Two records that share an address, a port or an identifier are not
thereby the same event — direction, initiator and window decide.  A finding with
a time window does not automatically own everything inside it.  And moving found
evidence into the background, or into "before the compromise", is a claim, not an
exemption from making one.  Written as four rules in `SKILL.md` §5, next to the
rejection rule they extend, and asserted below to name no corpus.

Synthetic corpora throughout: the suite needs no dataset and runs on a laptop.
The three real corpora are measured in the PR, not here.
"""
import ast
import filecmp
import glob
import hashlib
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
BENCH = os.path.join(SHERLOCK, "eval", "bench")
V20 = os.path.join(SKILLS, "v20")
V21 = os.path.join(SKILLS, "v21")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V21)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# What counts as corpus knowledge is defined once, in v19, and widened once, in
# v20. Importing both rather than copying them means a corpus added to the answer
# keys is covered in three files at once and they cannot drift apart.
V19T = load_module(os.path.join(HERE, "test_skill_integrity_v19.py"),
                   "skill_integrity_v19")
V20T = load_module(os.path.join(HERE, "test_skill_source_integrity_v20.py"),
                   "skill_source_integrity_v20")


# ---------------------------------------------------------------------------
# the shape the census could not see
# ---------------------------------------------------------------------------
# A tally says how big the haystack is. A SHAPE says how big the needle is, and
# how much hay is around it — «N строк улики в файле на M строк». That is a
# strictly worse leak than a tally, and it is the one nothing was checking.
#
# Read from the answer keys, like everything else here: `labelled_lines` and
# `file_total_lines` are the two fields every mechanically-derived key carries
# per defect, so a corpus added later is covered without editing this file.
def needle_shapes():
    """-> ({n: what it is}, [(labelled, total, dataset, defect id)])."""
    single, pairs = {}, []
    for path in sorted(glob.glob(os.path.join(BENCH, "answer-key*.json"))):
        try:
            with io.open(path, encoding="utf-8") as fh:
                key = json.load(fh)
        except (OSError, ValueError):
            continue
        ds = key.get("dataset") or os.path.basename(path)
        for d in key.get("defects") or []:
            lab, tot = d.get("labelled_lines"), d.get("file_total_lines")
            for n, what in ((lab, "labelled lines"), (tot, "lines in the file")):
                if isinstance(n, int):
                    single.setdefault(n, "%s: %s" % (ds, what))
            if isinstance(lab, int) and isinstance(tot, int):
                pairs.append((lab, tot, ds, d.get("id") or "?"))
    return single, pairs


# Spelled out, because the sentence that started this spells one of them out.
# Only 1-10: past ten a Russian writer reaches for the digits.
NUMBER_WORDS = {
    "один": 1, "одна": 1, "одной": 1, "одну": 1, "два": 2, "две": 2, "двух": 2,
    "три": 3, "трёх": 3, "трех": 3, "четыре": 4, "четырёх": 4, "четырех": 4,
    "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10,
    "десяти": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

LINE_UNITS = ("строк", "line")
SHAPE_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d[\d   ]*\d|\d+")

# Below this a line count is indistinguishable from the arithmetic a tool does
# about its OWN output — a 28-row range, a 40-line per-file cap, a 20-line read
# window. Above it, a bare line count sitting next to the word "строк" is a fact
# about a file somebody measured. The PAIR check below has no floor and does not
# need one: two numbers that happen to be one defect's needle and its haystack,
# on one line, is not a coincidence a tool produces by accident.
SHAPE_FLOOR = 50


def digits_on(line):
    """-> [(value, token)] — every number WRITTEN AS DIGITS on one line.

    Windows never cross a line: `## Шаг 2` and a `строк` on the NEXT line are
    not one sentence, and treating them as one is how a census starts crying
    wolf."""
    out = []
    for m in SHAPE_NUMBER.finditer(line):
        try:
            out.append((int(re.sub(r"[,   ]", "", m.group(0))),
                        m.group(0)))
        except ValueError:
            continue
    return out


def quantities_on(line):
    """-> [(value, token)] — digits AND spelled-out counts.

    Two alphabets, because two checks need different ones. Digits are how a tool
    states a measurement, so the budget check below reads only those: «это N
    корпусов, а не один» is rhetoric, not arithmetic, and a check that cannot
    tell the two apart is a check people switch off. Words matter for the needle
    shape, because the sentence this version replaces spelled its needle out."""
    out = list(digits_on(line))
    low = line.lower()
    for word, n in NUMBER_WORDS.items():
        if re.search(r"(?<![0-9A-Za-zА-Яа-яЁё])" + word
                     + r"(?![0-9A-Za-zА-Яа-яЁё])", low):
            out.append((n, word))
    return out


def shape_hits(text):
    """-> [(kind, detail, line number, line)] for every needle shape stated."""
    single, pairs = needle_shapes()
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        if not any(u in line.lower() for u in LINE_UNITS):
            continue
        qty = quantities_on(line)
        seen = {v for v, _tok in qty}
        for v, tok in qty:
            if tok.isdigit() and v >= SHAPE_FLOOR and v in single:
                hits.append(("SHAPE", "%d (%s)" % (v, single[v]), ln,
                             line.strip()[:100]))
        for lab, tot, ds, did in pairs:
            if lab in seen and tot in seen:
                hits.append(("NEEDLE", "%d of %d (%s %s)" % (lab, tot, ds, did),
                             ln, line.strip()[:100]))
    return sorted(set(hits))


def shape_leaks(skill):
    """-> [(channel, relpath, kind, detail, line number, line)]."""
    out = []
    for ch, rel, text in V20T.channels(skill):
        for kind, detail, ln, line in shape_hits(text):
            out.append((ch, rel, kind, detail, ln, line))
    return sorted(set(out))


def shape_report(hits):
    return "\n".join("  %-8s %-18s %-7s %-40s line %d: %s"
                     % (c, r, k, str(d)[:40], n, l[:70]) for c, r, k, d, n, l in hits)


# ===========================================================================
# defect 2 — the needle shape, in any channel
# ===========================================================================
class NoChannelStatesTheShapeOfTheNeedle(unittest.TestCase):
    """A tally tells the model how big the haystack is. A shape tells it how big
    the needle is. The census caught the first and could not see the second."""

    def setUp(self):
        single, pairs = needle_shapes()
        if not single:
            self.skipTest("no mechanically-derived answer key on this machine")

    def test_the_shipped_docs_state_no_needle_shape(self):
        hits = [h for h in shape_leaks(UNDER_TEST) if h[0] == "docs"]
        self.assertEqual([], hits, "shipped text states a needle shape:\n"
                         + shape_report(hits))

    def test_the_emitted_strings_state_no_needle_shape(self):
        """This is the one that was open: the sentence is printed into
        `map.txt`, so every multi-host arm since v14 was handed it."""
        hits = [h for h in shape_leaks(UNDER_TEST) if h[0] == "emitted"]
        self.assertEqual([], hits, "a printed string states a needle shape:\n"
                         + shape_report(hits))

    def test_the_source_prose_states_no_needle_shape(self):
        hits = [h for h in shape_leaks(UNDER_TEST) if h[0] == "prose"]
        self.assertEqual([], hits, "a comment states a needle shape:\n"
                         + shape_report(hits))

    def test_the_checker_actually_sees_the_sentence_it_was_written_for(self):
        """A negative-only test can pass because it is broken. Feed it the
        sentence and require both halves to fire — the digit and the word."""
        sentence = ("машину, и улика из восьми строк в файле на 272 строки в "
                    "них не попадает.")
        kinds = {k for k, _d, _n, _l in shape_hits(sentence)}
        self.assertIn("SHAPE", kinds, "the digit half is not caught")
        self.assertIn("NEEDLE", kinds, "the spelled-out half is not caught")

    def test_the_checker_does_not_fire_on_ordinary_arithmetic(self):
        """And it can pass because it fires on everything. These are real lines
        from the skill's own text that a naive census flags."""
        for benign in (
                "Диапазон допустим: `g041-g068 N доля 12,7%` закрывает 28 строк",
                "почти каждая запись своей формы, либо форм всего две. Строку",
                "timestamp on line 2 of a nine-line record, so coverage reads",
                "прочитай окрестность узким окном (offset N-20, limit 60 строк)"):
            self.assertEqual([], shape_hits(benign),
                             "the shape census cries wolf on: %s" % benign)


# ===========================================================================
# defect 1 — the tally inventory, now empty
# ===========================================================================
class TheEmittedTallyInventoryIsEmpty(unittest.TestCase):
    """v20 pinned exactly one emitted tally and said the next arm to re-measure
    the multi-host map owns the fix. This is that arm."""

    def test_no_emitted_string_quotes_a_corpus_tally(self):
        _names, roots = V19T.measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        hits = V20T.leaks(UNDER_TEST, "emitted", ("CENSUS",))
        self.assertEqual([], hits, "an emitted string quotes a corpus tally:\n"
                         + V20T.report(hits))

    def test_the_other_three_channels_did_not_regress(self):
        """v19 and v20 are still true of this arm — the fix must not have
        re-opened what they closed."""
        _names, roots = V19T.measured_corpora()
        for ch in ("docs", "emitted", "prose"):
            kinds = ("NAME", "GT") if not roots else ("NAME", "GT", "CENSUS",
                                                      "PATH")
            hits = V20T.leaks(UNDER_TEST, ch, kinds)
            self.assertEqual([], hits, "%s regressed:\n" % ch + V20T.report(hits))


# ===========================================================================
# defect 3 — the general one: a budget paragraph states only measured numbers
# ===========================================================================
def make_bundle(root, hosts, files_per_host=3):
    """A synthetic N-machine bundle: every host gets the same two directories,
    which is what makes the partition fire. Invented end to end."""
    for h in range(hosts):
        name = "node-%02d" % h
        V19T._write(os.path.join(root, name, "configs", "spool.conf"),
                    "owner: quill\nqueue: %d\n" % (h + 1))
        for f in range(files_per_host):
            V19T._write(
                os.path.join(root, name, "logs", "spool-%d.log" % f),
                "".join("2031-03-01T%02d:%02d:%02d+00:00 spool[%d]: slice %d "
                        "written ok\n" % (i // 900 % 24, (i // 15) % 60, i % 60,
                                          70 + h, i) for i in range(300)))
    return ["node-%02d" % h for h in range(hosts)]


BUDGET_START = "хостов:"
BUDGET_END = "ЦЕНА ЧЕСТНО"


def budget_paragraph(map_txt):
    """-> the lines that explain the per-host worklist budget.

    Bounded by the two markers the paragraph has always had: it opens with the
    host count and ends where the honest-cost line starts. Empty means the
    paragraph is gone, which is itself a failure — see the lesson test."""
    out, on = [], False
    for line in map_txt.splitlines():
        if line.startswith(BUDGET_START):
            on = True
        elif line.startswith(BUDGET_END):
            break
        if on:
            out.append(line)
    return out


class TheBudgetParagraphStatesOnlyMeasuredNumbers(unittest.TestCase):
    """The general rule, and the one that subsumes the census.

    A census is a blocklist: it can only catch numbers somebody thought to
    count. This is an allowlist: the paragraph that explains the budget may
    state the host count and the cap, both of which this run produced, and
    nothing else. A corpus tally, a needle shape and a stale default all fail it
    for the same reason — no run produced them."""

    HOSTS = 3
    CAP = 37

    def _map(self, tmp, cap, extra=()):
        corpus = os.path.join(tmp, "corpus")
        make_bundle(corpus, self.HOSTS)
        out = os.path.join(tmp, "work")
        p = V19T.run_logmap(UNDER_TEST, corpus, out,
                            ("--worklist-cap", str(cap)) + tuple(extra))
        self.assertEqual(0, p.returncode, p.stderr)
        with io.open(os.path.join(out, "map.txt"), encoding="utf-8") as fh:
            return fh.read()

    def _check(self, map_txt, cap):
        para = budget_paragraph(map_txt)
        self.assertTrue(para, "the multi-host budget paragraph is gone")
        allowed = {self.HOSTS, cap}
        bad = []
        for line in para:
            for value, tok in digits_on(line):
                if value not in allowed:
                    bad.append((value, tok, line.strip()[:90]))
        self.assertEqual([], bad,
                         "the budget paragraph states numbers this run did not "
                         "measure (allowed %s):\n%s"
                         % (sorted(allowed),
                            "\n".join("  %d (%r) -> %s" % b for b in bad)))
        # And the other alphabet. A count spelled out next to a line unit is
        # still a count, and the needle in the sentence this replaces was
        # spelled out — «улика из восьми строк».
        shape = shape_hits("\n".join(para))
        self.assertEqual([], shape,
                         "the budget paragraph states a needle shape:\n"
                         + "\n".join("  %s %s line %d: %s" % h for h in shape))

    def test_the_index_states_only_this_runs_numbers(self):
        """`render_index` — `map.txt` on any multi-host bundle."""
        with tempfile.TemporaryDirectory() as tmp:
            self._check(self._map(tmp, self.CAP), self.CAP)

    def test_the_undivided_map_states_only_this_runs_numbers(self):
        """`hosts_block` — the same paragraph on the `--map-cap 0` path, which
        exists so the map budget can be measured off. The brief named one site;
        `grep` found two."""
        with tempfile.TemporaryDirectory() as tmp:
            self._check(self._map(tmp, self.CAP, ("--map-cap", "0")), self.CAP)

    def test_the_paragraph_tracks_the_cap_rather_than_repeating_a_default(self):
        """Two runs, two caps. Every number in the paragraph must move with the
        run or be the host count — a constant that is neither is hard-coded."""
        seen = []
        for cap in (23, 91):
            with tempfile.TemporaryDirectory() as tmp:
                para = budget_paragraph(self._map(tmp, cap))
                self._check("\n".join(para), cap)
                seen.append({v for line in para for v, _t in digits_on(line)})
        self.assertNotEqual(seen[0], seen[1],
                            "the paragraph printed the same numbers for two "
                            "different caps — it is not computed at all")

    def test_the_lesson_survived_as_a_general_rule(self):
        """Deleting the sentence is not the fix. What it taught — the cap is
        handed to each machine WHOLE, because one cap divided among machines is
        thin per machine — has to still be there, said without the testbed."""
        with tempfile.TemporaryDirectory() as tmp:
            para = "\n".join(budget_paragraph(self._map(tmp, self.CAP)))
        for fragment in ("КАЖДОМУ хосту целиком", "не поделён между ними",
                         "улика"):
            self.assertIn(fragment, para,
                          "the per-host budget lesson lost %r" % fragment)


# ===========================================================================
# nothing else moved
# ===========================================================================
class V21IsV20WithOneSentenceGeneralised(unittest.TestCase):

    def setUp(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V21):
            self.skipTest("only meaningful for v21")

    @staticmethod
    def _blank_strings(path):
        """The AST with every string constant replaced by a placeholder. Two
        files that agree here differ ONLY in text a reader sees — control flow,
        constants, argument lists and call counts are all still compared."""
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                node.value = ""
        return ast.dump(tree)

    def test_only_text_changed_in_the_tools(self):
        for name in ("logmap.py", "citecheck.py", "logjoin.py"):
            self.assertEqual(self._blank_strings(os.path.join(V20, "tools", name)),
                             self._blank_strings(os.path.join(V21, "tools", name)),
                             "%s is not a text-only change" % name)

    def test_the_reference_pages_are_byte_identical(self):
        for rel in ("reference/tools.md", "reference/report-format.md",
                    "reference/code-and-spec.md"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V20, rel), os.path.join(V21, rel),
                            shallow=False),
                "%s changed — v21 touches SKILL.md and logmap's text only" % rel)

    def test_the_ranked_artefacts_are_byte_identical_on_a_bundle(self):
        """`worklist.tsv` and `axis3.tsv` are what every score is computed from.
        The sentence lives in the map; if a score moves, this change was not
        what it looks like."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            make_bundle(corpus, 4)
            outs = {}
            for tag, skill in (("v20", V20), ("v21", V21)):
                outs[tag] = os.path.join(tmp, tag)
                p = V19T.run_logmap(skill, corpus, outs[tag])
                self.assertEqual(0, p.returncode, p.stderr)
            names = sorted(os.listdir(outs["v20"]))
            self.assertEqual(names, sorted(os.listdir(outs["v21"])))
            moved = [fn for fn in names
                     if not filecmp.cmp(os.path.join(outs["v20"], fn),
                                        os.path.join(outs["v21"], fn),
                                        shallow=False)]
            self.assertEqual(["map.txt"], moved,
                             "expected only map.txt to move, got %s" % moved)

    def test_a_single_host_corpus_is_byte_identical_including_the_map(self):
        """The sentence is only ever printed when a bundle has more than one
        machine, so a single-host corpus must not move AT ALL — map included.
        Verified rather than assumed."""
        for maker in (V19T.make_tiny_corpus, V19T.make_gz_corpus):
            with tempfile.TemporaryDirectory() as tmp:
                corpus = os.path.join(tmp, "corpus")
                os.makedirs(corpus, exist_ok=True)
                maker(corpus)
                outs = {}
                for tag, skill in (("v20", V20), ("v21", V21)):
                    outs[tag] = os.path.join(tmp, tag)
                    p = V19T.run_logmap(skill, corpus, outs[tag])
                    self.assertEqual(0, p.returncode, p.stderr)
                names = sorted(os.listdir(outs["v20"]))
                self.assertEqual(names, sorted(os.listdir(outs["v21"])))
                for fn in names:
                    self.assertTrue(
                        filecmp.cmp(os.path.join(outs["v20"], fn),
                                    os.path.join(outs["v21"], fn),
                                    shallow=False),
                        "%s moved on a single-host corpus (%s)"
                        % (fn, maker.__name__))


# ===========================================================================
# D09 — evidence that is found, cited, and attached to the wrong conclusion
# ===========================================================================
class AssignmentIsAnAssertion(unittest.TestCase):
    """The class v19's rejection rule does not reach.

    v19 fixed «a rejection needs the same evidence as an assertion». Here
    nothing is rejected: the row is present, read, and marked a defect — and
    then filed under a neighbouring finding, or moved into the background. The
    discipline is a property of investigation, not of any corpus: a record can
    fit two conclusions, and picking one is an assertion."""

    def _skill(self):
        with io.open(os.path.join(UNDER_TEST, "SKILL.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_rule_is_stated(self):
        body = self._skill()
        for fragment in (
                "Приписать улику — это тоже утверждение",
                "Совпадение адреса — не совпадение события",
                "Окно вывода не поглощает всё",
                "почему А, а не Б"):
            self.assertIn(fragment, body,
                          "the assignment rule lost %r" % fragment)

    def test_the_rule_names_no_corpus_and_no_answer(self):
        """The whole risk of writing this rule is smuggling the answer in as
        guidance. It must survive every check v19, v20 and this file apply."""
        body = self._skill()
        self.assertEqual([], V19T.forbidden_hits(body))
        self.assertEqual([], V19T.ground_truth_hits(body))
        self.assertEqual([], shape_hits(body))

    def test_the_rule_carries_no_protocol_port_or_phase_specific_advice(self):
        """A rule that says "check port 443" is corpus advice wearing a rule's
        clothes. The section may not name a port, a protocol or a defect."""
        body = self._skill()
        start = body.index("Приписать улику — это тоже утверждение")
        section = body[start:start + 2000]
        end = section.find("\n## ")
        if end > 0:
            section = section[:end]
        for token in ("443", "TLS", "TCP", "SSL", "pcap", "frames.tsv",
                      "evtx", "ACK", "Continuation"):
            self.assertNotIn(token, section,
                             "the assignment rule names %r — that is advice "
                             "about one corpus, not a discipline" % token)


# ===========================================================================
# the frozen arms
# ===========================================================================
class FrozenArms(unittest.TestCase):
    """v20 is what PR #22's census was taken with, and v19 is what PR #21's was.
    Pinned by content, reported and never edited."""

    EXPECTED_V20 = {
        "SKILL.md": "839b89aa72fb1dc04ec4197259ef4d11",
        "tools/logmap.py": "3afd3862c857e2655c5dfba08aa40e32",
        "tools/citecheck.py": "82bece3c236a70de871da66c8e95758c",
        "tools/logjoin.py": "a0c1e11c9c52aaa814f1c26480ac37a4",
        "reference/report-format.md": "d19a98be30ab2b52fd30cceca3860169",
        "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
        "reference/tools.md": "743808781ba175741c392f258d035065",
    }

    def test_v20_is_frozen(self):
        for rel, want in sorted(self.EXPECTED_V20.items()):
            path = os.path.join(V20, rel)
            got = hashlib.md5(open(path, "rb").read()).hexdigest()
            self.assertEqual(want, got, "%s changed in v20" % rel)

    def test_no_frozen_arm_gained_or_lost_a_file(self):
        """A frozen arm is frozen as a directory, not as a list of hashes: a new
        file inside v1..v20 ships to a measured agent just as surely as an edit
        to an old one."""
        for n in range(1, 21):
            arm = os.path.join(SKILLS, "v%d" % n)
            if not os.path.isdir(arm):
                continue
            p = subprocess.run(
                ["git", "-C", SHERLOCK, "status", "--porcelain", "--",
                 os.path.relpath(arm, SHERLOCK)],
                capture_output=True, text=True).stdout
            self.assertEqual("", p.strip(),
                             "v%d has uncommitted changes — a frozen arm just "
                             "moved:\n%s" % (n, p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
