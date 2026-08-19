#!/usr/bin/env python3
"""v25 — a finding block is opened by a heading, not by a wrapped sentence.

    python3 tools/tests/test_finding_block_parse_v25.py
    SHERLOCK_SKILL=$PWD/skills/v24 python3 tools/tests/test_finding_block_parse_v25.py

ONE CEILING, MEASURED ON THE ARMS THAT WERE ALREADY SCORED
-----------------------------------------------------------
v24 gave every finding block a mandatory outcome line and made a missing one a
delivery defect. That put weight on a question nothing had ever had to answer
precisely: WHERE DOES A FINDING BLOCK START?

`citecheck.FINDING_HEAD_RE` answered it with «any line that starts with `Н-n`»,
and required no heading marker of any kind. Prose refers to a finding by its
number, and prose gets wrapped: a hand-over written to a file is hard-wrapped
and the same report pasted into a message is not, so a cross-reference that
lands at a line break in one channel opens a phantom block there and not in the
other. Measured before this change, on the reports this project already holds:
one report parsed a different number of findings in its two channels, and the
difference was exactly one wrapped cross-reference. Every other report agreed.

The phantom is not a cosmetic miscount. A sentence is not a finding, so it
carries no «исход:» line, so it is counted as a block that forgot one — and a
report that labelled every real finding it wrote is told it did not.

THE FIX IS THE FORMAT, NOT A HEURISTIC
---------------------------------------
`reference/report-format.md` has always told the model to write

    Н-n · заголовок

so a head is the number, the interpunct, and a title. The interpunct is what
makes the line a heading: prose does not punctuate with it, so a line carrying
it was written as a title and a line without it was not. v25 requires it, and
requires a non-empty title after it. Nothing in FRONT of the number changes — a
markdown heading, a bullet, a quote marker, bold, indentation or nothing at all
all still open a block, because the format's own example is indented and
unmarked and reports rely on every one of those shapes.

WHAT THIS FILE PROVES
----------------------
1.  Which lines open a block and which do not — every prefix the old pattern
    tolerated, and every prose shape that must not open one.
2.  The tripwire: hard-wrapping a report may not change how many findings it
    has. Synthetic here, and re-run against the two channels of every report
    on disk where those are present.
3.  Nothing else moved: same citation verdicts as v24 on synthetic corpora and
    on the real ones where they exist, byte-identical Step-1 artefacts,
    byte-identical files everywhere except the one parser.

Synthetic throughout unless a test says otherwise — every corpus and report
below is built by this file.
"""
import filecmp
import glob
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
SKILLS = os.path.join(SHERLOCK, "skills")
BENCH = os.path.join(SHERLOCK, "eval", "bench")
V24 = os.path.join(SKILLS, "v24")
V25 = os.path.join(SKILLS, "v25")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V25)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


V19T = load_module(os.path.join(HERE, "test_skill_integrity_v19.py"),
                   "skill_integrity_v19")
V20T = load_module(os.path.join(HERE, "test_skill_source_integrity_v20.py"),
                   "skill_source_integrity_v20")
V21T = load_module(os.path.join(HERE, "test_skill_integrity_v21.py"),
                   "skill_integrity_v21")

CITE = os.path.join(UNDER_TEST, "tools", "citecheck.py")
CC = load_module(CITE, "citecheck_v25_under_test")
CC24 = load_module(os.path.join(V24, "tools", "citecheck.py"), "citecheck_v24_ref")

LEDGER = os.path.join(BENCH, "runs-bench.jsonl")


def heads(text, mod=CC):
    """-> [finding number] in the order the parser opens the blocks."""
    return [num for num, _lo, _hi in mod.finding_blocks(text)]


# ===========================================================================
# PART 1 — what opens a finding block
# ===========================================================================
TITLE = "потерянный ключ ротации"

OPENS = [
    ("the format's own example: indented, no marker", "    Н-1 · " + TITLE),
    ("no indentation and no marker at all", "Н-1 · " + TITLE),
    ("a level-2 heading", "## Н-1 · " + TITLE),
    ("a level-3 heading", "### Н-1 · " + TITLE),
    ("a level-4 heading", "#### Н-1 · " + TITLE),
    ("a heading whose title is bold", "### Н-1 · **" + TITLE + "**"),
    ("a bulleted head", "- Н-1 · " + TITLE),
    ("a star-bulleted head", "* Н-1 · " + TITLE),
    ("a bold head", "**Н-1 · " + TITLE + "**"),
    ("a heading that is also bold", "### **Н-1 · " + TITLE + "**"),
    ("a quoted head", "> Н-1 · " + TITLE),
    ("a Latin H instead of the Cyrillic one", "### H-1 · " + TITLE),
    ("an en dash in the number", "### Н–1 · " + TITLE),
    ("an em dash in the number", "### Н—1 · " + TITLE),
    ("spaces around the number's dash", "### Н - 1 · " + TITLE),
    ("no spaces around the separator", "### Н-1·" + TITLE),
    ("a bullet glyph for the separator", "### Н-1 • " + TITLE),
    ("a dot-operator glyph for the separator", "### Н-1 ∙ " + TITLE),
    ("a trailing space after the title", "### Н-1 · " + TITLE + "   "),
]

# Every one of these is prose that names a finding. None is a heading.
DOES_NOT_OPEN = [
    ("the wrapped cross-reference measured on a real hand-over",
     "  Н-3: `ppid=1`, `auid=unset`, соседние остановки юнитов. Кроме того —"),
    ("the same reference with no indent",
     "Н-3: `ppid=1`, `auid=unset`, соседние остановки юнитов."),
    ("a wrapped sentence that continues with an em dash",
     "Н-3 — это следствие того же обрыва, а не отдельный дефект."),
    ("a wrapped sentence that continues with a comma",
     "Н-3, и по той же причине, ниже в разделе 2."),
    ("a wrapped sentence that continues with a bare word",
     "Н-3 описывает тот же процесс с другой стороны."),
    ("a bare number on its own line", "Н-3"),
    ("a bare number under a bullet", "- Н-3"),
    ("a number with a separator and no title", "### Н-3 ·"),
    ("a number with a separator and only spaces after it", "### Н-3 ·    "),
]


class WhatOpensAFindingBlock(unittest.TestCase):

    def _report(self, line):
        return ("# отчёт\n\n## 1. Находки\n\n"
                "### Н-9 · опорный блок\nисход: норма\n\n"
                + line + "\n"
                "улики: edge-7/logs/warden.log:12 «нечто»\n")

    def test_every_documented_heading_shape_opens_a_block(self):
        for why, line in OPENS:
            got = heads(self._report(line))
            self.assertEqual(["9", "1"], got,
                             "%s did not open a block: %r -> %r"
                             % (why, line, got))

    def test_no_prose_reference_opens_a_block(self):
        for why, line in DOES_NOT_OPEN:
            got = heads(self._report(line))
            self.assertEqual(["9"], got,
                             "%s opened a block: %r -> %r" % (why, line, got))

    def test_the_head_still_reports_the_number_it_matched(self):
        rep = self._report("### Н-12 · " + TITLE)
        self.assertEqual(["9", "12"], heads(rep))

    def test_a_block_runs_to_the_next_head_and_the_last_to_the_end(self):
        rep = ("### Н-1 · один\nа\nб\n"
               "### Н-2 · два\nв\n")
        self.assertEqual([("1", 1, 3), ("2", 4, 5)], CC.finding_blocks(rep))

    def test_the_frozen_previous_arm_does_not_hold_this_rule(self):
        """The characterisation, kept as proof that the fix is a real change."""
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V25):
            self.skipTest("only meaningful when v25 is under test")
        line = DOES_NOT_OPEN[0][1]
        self.assertEqual(["9", "3"], heads(self._report(line), CC24),
                         "v24 is supposed to open a block here — that is the "
                         "defect v25 closes")
        self.assertEqual(["9"], heads(self._report(line), CC))


# ===========================================================================
# PART 2 — the tripwire: wrapping a report may not change its findings
# ===========================================================================
def hard_wrap(text, width=78):
    """Wrap prose the way a report written to a file is wrapped: every
    paragraph line broken at `width`, headings and evidence lines left alone."""
    out = []
    for line in text.splitlines():
        if len(line) <= width or line.lstrip().startswith(("#", "улики:",
                                                           "исход:")):
            out.append(line)
            continue
        words, cur = line.split(" "), ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > width:
                out.append(cur)
                cur = "  " + w
            else:
                cur = (cur + " " + w) if cur else w
        if cur:
            out.append(cur)
    return "\n".join(out) + "\n"


# Two paragraphs whose wrap lands a cross-reference at the start of a line —
# the shape measured on a real hand-over. The lead of each is sized so the
# break falls exactly before the number, and a test asserts that it did.
_LEAD_A = "чем опровергал: сравнил частоту до окна и внутри него, и это тот же процесс"
_LEAD_B = "что делать сейчас: ничего, это штатное поведение, и объяснение лежит выше в"

WRAPPABLE = (
    "# отчёт\n\n"
    "## 1. Находки\n\n"
    "### Н-1 · обрыв ротации на пограничном узле\n"
    "исход: норма\n"
    "улики: edge-7/logs/warden.log:12 «нечто»\n"
    + _LEAD_A + " Н-3: `ppid=1`, `auid=unset`, соседние остановки юнитов, "
    "поэтому отдельным дефектом это не является.\n\n"
    "### Н-2 · вторая находка, чтобы блоков было больше одного\n"
    "исход: норма\n"
    "улики: edge-7/logs/warden.log:14 «иное»\n"
    + _LEAD_B + " Н-1, который стоит выше по тексту этого самого отчёта.\n\n"
    "## 5. Чего я не знаю\n"
    "ничего существенного\n")


class WrappingAReportDoesNotChangeItsFindings(unittest.TestCase):

    def test_the_fixture_really_wraps_a_reference_onto_its_own_line(self):
        """Without this the rest of the class would pass vacuously."""
        wrapped = hard_wrap(WRAPPABLE)
        self.assertNotEqual(WRAPPABLE, wrapped, "the fixture must actually wrap")
        starts = [l for l in wrapped.splitlines()
                  if l.strip().startswith(("Н-3", "Н-1,"))]
        self.assertEqual(2, len(starts),
                         "the wrap must put a cross-reference first on a line, "
                         "got:\n" + wrapped)

    def test_the_wrapped_copy_parses_the_same_blocks(self):
        flat = WRAPPABLE
        wrapped = hard_wrap(flat)
        self.assertEqual(heads(flat), heads(wrapped))
        self.assertEqual(["1", "2"], heads(wrapped))

    def test_the_wrapped_copy_loses_no_outcome(self):
        wrapped = hard_wrap(WRAPPABLE)
        rows = CC.finding_outcomes(wrapped)
        self.assertEqual(["норма", "норма"], [r["outcome"] for r in rows])
        self.assertEqual([], [r["finding"] for r in rows if not r["outcome"]])

    def test_the_two_channels_imply_the_same_verdict(self):
        self.assertEqual(CC.implied_verdict(WRAPPABLE),
                         CC.implied_verdict(hard_wrap(WRAPPABLE)))

    def test_the_previous_arm_disagrees_with_itself_on_this_fixture(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V25):
            self.skipTest("only meaningful when v25 is under test")
        self.assertNotEqual(heads(WRAPPABLE, CC24),
                            heads(hard_wrap(WRAPPABLE), CC24),
                            "the fixture must reproduce the defect on v24")


# ===========================================================================
# PART 3 — the same tripwire on every report this project holds
# ===========================================================================
def ledger_reports():
    """-> [(arm, dataset, message text, file text)] from the bench ledger.

    Gitignored trajectories are not needed: the ledger carries both channels of
    every scored run verbatim."""
    out = []
    if not os.path.isfile(LEDGER):
        return out
    with io.open(LEDGER, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            msg, art = rec.get("answer") or "", rec.get("artifact") or ""
            if not msg or not art:
                continue
            if not (CC24.finding_blocks(msg) or CC24.finding_blocks(art)):
                continue
            out.append((rec.get("arm"), rec.get("dataset"), msg, art))
    return out


class TheTwoChannelsOfEveryRealReportAgree(unittest.TestCase):
    """THE TRIPWIRE. Real text, no answer key, no corpus needed.

    A report handed over twice — once wrapped into a file, once flat into the
    message — is the same report. If the two channels disagree on how many
    findings it has, the parser is reading a wrap as structure."""

    def setUp(self):
        self.reports = ledger_reports()
        if not self.reports:
            self.skipTest("no two-channel report in the bench ledger")

    def test_every_report_parses_the_same_findings_in_both_channels(self):
        bad = []
        for arm, dataset, msg, art in self.reports:
            a, b = heads(msg), heads(art)
            if a != b:
                bad.append("%s/%s: message %d block(s) %s, file %d block(s) %s"
                           % (arm, dataset, len(a), a, len(b), b))
        self.assertEqual([], bad, "channels disagree:\n  " + "\n  ".join(bad))

    def test_every_head_the_parser_keeps_carries_a_title(self):
        """Nothing that survives is a bare number or a sentence opener."""
        for arm, dataset, msg, art in self.reports:
            for text in (msg, art):
                for line in text.splitlines():
                    m = CC.FINDING_HEAD_RE.match(line)
                    if not m:
                        continue
                    tail = line[m.end() - 1:].strip()
                    self.assertTrue(tail, "%s/%s: head with no title: %r"
                                    % (arm, dataset, line))

    def test_the_previous_arm_had_at_least_one_disagreement(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V25):
            self.skipTest("only meaningful when v25 is under test")
        bad = [1 for _arm, _ds, msg, art in self.reports
               if heads(msg, CC24) != heads(art, CC24)]
        self.assertTrue(bad, "this suite is supposed to be a regression test — "
                             "v24 disagreed with itself on at least one report")

    def test_no_report_loses_a_finding_to_the_new_rule(self):
        """Every block v25 drops must be one v24 should never have opened.

        A dropped head is only allowed when the same report states that number
        in another block that both arms agree on — i.e. it was a reference to a
        finding, not the finding itself."""
        for arm, dataset, msg, art in self.reports:
            for text in (msg, art):
                old, new = heads(text, CC24), heads(text, CC)
                dropped = [n for n in old if n not in new]
                self.assertEqual(
                    [], [n for n in dropped if n not in new],
                    "%s/%s dropped a finding number entirely: %s"
                    % (arm, dataset, dropped))


# ===========================================================================
# PART 4 — v25 is v24 plus this one parser change, and nothing else
# ===========================================================================
class V25IsV24PlusOneChange(unittest.TestCase):

    def setUp(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V25):
            self.skipTest("only meaningful for v25")

    def test_only_citecheck_differs(self):
        moved = []
        for dp, dn, fns in os.walk(V24):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), V24)
                a, b = os.path.join(V24, rel), os.path.join(V25, rel)
                if not filecmp.cmp(a, b, shallow=False):
                    moved.append(rel)
        self.assertEqual(["tools/citecheck.py"], sorted(moved),
                         "v25 changes where a finding block starts and nothing "
                         "else")

    def test_no_file_is_added_or_removed(self):
        a = {os.path.relpath(os.path.join(dp, fn), V24)
             for dp, dn, fns in os.walk(V24) for fn in fns
             if "__pycache__" not in dp}
        b = {os.path.relpath(os.path.join(dp, fn), V25)
             for dp, dn, fns in os.walk(V25) for fn in fns
             if "__pycache__" not in dp}
        self.assertEqual(set(), b - a)
        self.assertEqual(set(), a - b)

    def test_the_report_format_already_prescribed_this_separator(self):
        """The rule is not new policy — it is the format, now enforced."""
        with io.open(os.path.join(V25, "reference", "report-format.md"),
                     encoding="utf-8") as fh:
            fmt = fh.read()
        self.assertIn("Н-n · заголовок", fmt)

    def test_step_one_is_untouched(self):
        for name in ("logmap.py", "logjoin.py", "triagecheck.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V24, "tools", name),
                            os.path.join(V25, "tools", name), shallow=False),
                "%s moved — v25 changes the report parse, not Step 1" % name)

    def test_the_ranked_artefacts_are_byte_identical_on_a_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            V21T.make_bundle(corpus, 4)
            outs = {}
            for tag, skill in (("v24", V24), ("v25", V25)):
                outs[tag] = os.path.join(tmp, tag)
                p = V19T.run_logmap(skill, corpus, outs[tag])
                self.assertEqual(0, p.returncode, p.stderr)
            names = sorted(os.listdir(outs["v24"]))
            self.assertEqual(names, sorted(os.listdir(outs["v25"])))
            moved = [fn for fn in names
                     if not filecmp.cmp(os.path.join(outs["v24"], fn),
                                        os.path.join(outs["v25"], fn),
                                        shallow=False)]
            self.assertEqual([], moved, "Step 1 output moved: %s" % moved)


# ===========================================================================
# PART 5 — the citation verdicts did not move
# ===========================================================================
def cite_corpus(root):
    V19T._write(os.path.join(root, "edge-7", "logs", "warden.log"),
                "".join("2031-04-09T11:%02d:00+00:00 warden[58]: sweep %d "
                        "clean\n" % (i, i) for i in range(30)))
    V19T._write(os.path.join(root, "edge-9", "logs", "warden.log"),
                "".join("2031-04-09T12:%02d:00+00:00 warden[58]: sweep %d "
                        "clean\n" % (i, i) for i in range(30)))
    V19T._write(os.path.join(root, "edge-7", "configs", "spool.conf"),
                "owner: quill\nqueue: 3\n")


CITE_REPORT = (
    "# отчёт\n\n## 1. Находки\n\n"
    "### Н-1 · один\n"
    "исход: норма\n"
    "улики: edge-7/logs/warden.log:3 «warden[58]: sweep 2 clean»\n"
    "чем опровергал: сравнил, и это тот же случай, что и Н-2: он описан "
    "ниже и на него ссылается этот блок.\n\n"
    "### Н-2 · два\n"
    "исход: норма\n"
    "улики: warden.log:5 «sweep 4 clean»\n"
    "ещё: edge-7/logs/warden.log:900 «нет такой строки»\n"
    "и: edge-7/configs/spool.conf:1 «owner: quill»\n"
    "и: edge-7/logs/warden.log:4 «совсем не то, что там написано»\n\n"
    "## 5. Чего я не знаю\nничего\n")


class TheCitationVerdictsDidNotMove(unittest.TestCase):

    def test_every_verdict_is_identical_to_the_previous_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cite_corpus(tmp)
            a = CC24.check(CITE_REPORT, tmp)
            b = CC.check(CITE_REPORT, tmp)
        self.assertEqual(a["summary"], b["summary"])
        self.assertEqual([(c["verdict"], c.get("cite"), c.get("report_line"))
                          for c in a["citations"]],
                         [(c["verdict"], c.get("cite"), c.get("report_line"))
                          for c in b["citations"]])

    def test_the_fixture_exercises_more_than_one_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            cite_corpus(tmp)
            got = CC.check(CITE_REPORT, tmp)["summary"]
        live = sorted(k for k, v in got.items() if v)
        self.assertGreaterEqual(len(live), 3,
                                "a one-verdict fixture proves nothing: %r" % got)

    def test_the_ambiguity_verdict_still_comes_out(self):
        """The property the scorer refuses to run without."""
        with tempfile.TemporaryDirectory() as tmp:
            cite_corpus(tmp)
            got = CC.check("warden.log:1 «warden[58]: sweep 0 clean»\n", tmp)
        self.assertTrue(got["summary"].get("ambiguous"))


class TheCitationVerdictsDidNotMoveOnTheRealCorpora(unittest.TestCase):
    """Real reports against the real corpora, where both are on this machine."""

    def setUp(self):
        self.pairs = []
        for path in sorted(glob.glob(os.path.join(BENCH, "answer-key*.json"))):
            try:
                with io.open(path, encoding="utf-8") as fh:
                    key = json.load(fh)
            except (OSError, ValueError):
                continue
            root = key.get("corpus_root") or ""
            if root and os.path.isdir(root):
                self.pairs.append((key.get("dataset"), root))
        self.reports = ledger_reports()
        if not self.pairs or not self.reports:
            self.skipTest("no measured corpus with a report on this machine")

    def test_same_summary_and_same_verified_share(self):
        ran = 0
        for dataset, root in self.pairs:
            for arm, ds, msg, art in self.reports:
                if ds != dataset:
                    continue
                for text in (msg, art):
                    a = CC24.check(text, root)
                    b = CC.check(text, root)
                    self.assertEqual(a["summary"], b["summary"],
                                     "%s/%s summary moved" % (arm, ds))
                    self.assertEqual(
                        [(c["verdict"], c.get("cite")) for c in a["citations"]],
                        [(c["verdict"], c.get("cite")) for c in b["citations"]],
                        "%s/%s verdicts moved" % (arm, ds))
                    ran += 1
        if not ran:
            self.skipTest("no report matches a corpus on this machine")


# ===========================================================================
# PART 6 — the discipline every arm is held to
# ===========================================================================
class TheNewCodeCarriesNoCorpusKnowledge(unittest.TestCase):

    def test_no_channel_names_a_corpus_or_states_ground_truth(self):
        _names, roots = V19T.measured_corpora()
        for ch in ("docs", "emitted", "prose"):
            kinds = ("NAME", "GT") if not roots else ("NAME", "GT", "CENSUS",
                                                      "PATH")
            hits = V20T.leaks(UNDER_TEST, ch, kinds)
            self.assertEqual([], hits, "%s leaks:\n" % ch + V20T.report(hits))

    def test_no_channel_states_a_needle_shape(self):
        single, _pairs = V21T.needle_shapes()
        if not single:
            self.skipTest("no mechanically-derived answer key on this machine")
        hits = V21T.shape_leaks(UNDER_TEST)
        self.assertEqual([], hits, "a needle shape leaked:\n"
                         + V21T.shape_report(hits))

    def test_no_example_path_resolves_in_a_measured_corpus(self):
        _names, roots = V19T.measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        for _ch, rel, text in V20T.channels(UNDER_TEST):
            hits = V19T.corpus_paths_in(text, roots)
            self.assertEqual([], hits, "%s cites a real corpus path: %s"
                             % (rel, hits))


class FrozenArms(unittest.TestCase):

    EXPECTED_V24 = {
        "SKILL.md": "e07d51cbb2d76ebae7a2b8b56e2c6225",
        "tools/logmap.py": "b7f292211177bfe2975c01fb74ff8495",
        "tools/citecheck.py": "8d176793daf3af2c1b6eb9adbfa0eb04",
        "tools/logjoin.py": "a0c1e11c9c52aaa814f1c26480ac37a4",
        "tools/triagecheck.py": "859a091b59ca422f747ea351b05d6c59",
        "reference/report-format.md": "bd53a0a627302435f8341755b26861b6",
        "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
        "reference/tools.md": "9e3ed0cbf85d1543efef66a1d3e8c035",
    }

    def test_no_frozen_arm_moved(self):
        for n in range(1, 25):
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

    def test_v24_hashes_are_recorded(self):
        got = {}
        for rel in sorted(self.EXPECTED_V24):
            with open(os.path.join(V24, rel), "rb") as fh:
                got[rel] = hashlib.md5(fh.read()).hexdigest()
        want = {k: v for k, v in self.EXPECTED_V24.items() if v}
        if not want:
            self.skipTest("hashes recorded on the first green run: %r" % got)
        self.assertEqual(want, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
