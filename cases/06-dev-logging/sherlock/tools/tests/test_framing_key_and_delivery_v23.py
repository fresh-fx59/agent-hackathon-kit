#!/usr/bin/env python3
"""v23 — a record is a transaction, and what you deliver is what you checked.

    python3 tools/tests/test_framing_key_and_delivery_v23.py
    SHERLOCK_SKILL=$PWD/skills/v23 python3 tools/tests/test_framing_key_and_delivery_v23.py

WHY THIS TEST EXISTS
--------------------
Two defects, one shape.  Both are the tool doing what v21 forbade the MODEL to
do: treating a shared moment as a shared transaction, and treating a document it
did not check as the document it checked.

DEFECT 1 — the correlation key was allowed to be a clock
........................................................
`detect_key_framing()` is the last-resort framing: reached only when no blank
line separates the records and no line starts with a timestamp, it looks for a
field whose value groups consecutive lines.  It applies four arithmetic tests —
mean run length, distinct-value share, contiguity, and «the lines of one record
share one instant».

On a one-request-per-line access log the winning field was the request's own
timestamp, and all four tests passed:

  * mean run — every request inside one second shares the value, so the run is
    as long as the traffic is dense;
  * distinct — one value per second is plenty of values;
  * contiguity — an access log is written in time order, so each second forms
    exactly one run, by construction;
  * same-instant — **1.00 by construction**, because the field IS the instant.

That fourth test is the one the docstring calls «the decisive test» and «the
corroborating witness».  When the candidate key is the clock, the witness and
the accused are the same object, and the test answers itself.  So a file of N
requests became N/k records, and every count computed over records — the level
histograms, the outcome axis, the unique-shape share, the number the analyst
copies into the report — was wrong by the factor k.

THE FIX, in the language of the thing being fixed: **a correlation key must
identify a transaction, not a moment.**  Take the candidate's values, remove
from each the timestamp the time axis would parse out of it, and ask the
question the code already asks — are there still enough distinct values to be a
record identifier?  A clock leaves nothing behind and is rejected.  A real
correlation token that happens to CONTAIN a clock — `<epoch>.<ms>:<serial>`, the
shape this framing exists for — leaves its serial behind and survives.  The
residue test is a no-op for any candidate that carries no timestamp at all, so
nothing that was not a clock changes.

AND: where a grammar is one record per line by construction, grouping is not a
judgement call.  A line that parses END TO END as a common-log-format request —
host, ident, user, bracketed stamp, quoted request, status, size — is a whole
record; the format already said where the record ends, and no statistic gets a
vote.  That gate is deliberately narrow: it fires only on a full parse, and it
sits below the three framings that already exist, so it can only ever take a
file that would otherwise have reached the last-resort branch.

DEFECT 2 — the run delivered something other than what it checked
.................................................................
The same run scored 100 % on the artefact it checked and 77.9 % on the artefact
it handed over: the hand-over was a hand-written condensation, and the citations
inside it had never been through the checker in the form they were delivered.

The skill already says the last message is the hand-over, and it already names
this as the gap: «Пункт 5 не проверяется никакой командой, и именно поэтому его
забывают».  So the fix is a command, not a paragraph.

MEASURED, and it is why the mechanism is not just a subset test: of the failing
citations in that hand-over, 20 of 21 were ALREADY in the checked report's
verified set — same file, same line, re-typed under a different sentence.  A
subset test over citations alone would have caught 1 of 21.  Extending the key
to (citation, claim) separates perfectly but costs 45 false alarms among the
good citations.  So `--delivered` does BOTH and gates on the first: it re-checks
the delivered text against the corpus exactly as it checks the report, and it
also reports every delivered citation that was never in the verified set.

Everything below is synthetic — the tripwire writes its own logs, its own report
and its own hand-over.  A tripwire calibrated on a real corpus's numbers is a
crib sheet, and it stops being a tripwire the moment the corpus changes.
"""
import filecmp
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
V22 = os.path.join(SKILLS, "v22")
V23 = os.path.join(SKILLS, "v23")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V23)


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

LOGMAP = os.path.join(UNDER_TEST, "tools", "logmap.py")
CITECHECK = os.path.join(UNDER_TEST, "tools", "citecheck.py")


def logmap_module():
    return load_module(LOGMAP, "logmap_under_test_v23")


# ---------------------------------------------------------------------------
# synthetic logs. Every byte here is invented: the host, the paths, the routes,
# the process names and the clock are all outside any corpus this project
# measures on, and the tripwire asserts arithmetic (records == lines) rather
# than any particular number a dataset happens to have.
# ---------------------------------------------------------------------------
HOST = "node-77"


def write(path, text):
    V19T._write(path, text)


def gateway_log(n=300, per_second=4):
    """A one-request-per-line access log whose stamp repeats `per_second` times.

    This is the whole defect in one file: the timestamp is at whitespace field
    3, and `per_second` consecutive requests share it.  A human counting
    requests counts the lines.
    """
    rows = []
    for i in range(n):
        sec = i // per_second
        rows.append(
            '10.20.30.40 - - [04/Mar/2031:05:%02d:%02d +0000] '
            '"GET /route-%d HTTP/1.1" 200 %d "-" "probe/1.0"\n'
            % (sec // 60 % 60, sec % 60, i, 400 + i))
    return "".join(rows)


def correlated_log(events=80, per_second=3):
    """The counter-case: four physical lines ARE one record, and the token that
    says so contains a clock — `<epoch>.<ms>:<serial>`.  Grouping this file is
    the reason the last-resort framing exists, so the fix must not touch it."""
    rows = []
    for i in range(events):
        tag = "quill(%d.%03d:%d)" % (1927000000 + i // per_second,
                                     (i * 137) % 1000, 9000 + i)
        rows.append('kind=CALL tok=%s: arch=q7 op=59 pid=%d comm="loader"\n'
                    % (tag, 4000 + i % 7))
        rows.append('kind=ARGV tok=%s: argc=2 a0="loader" a1="--slice=%d"\n'
                    % (tag, i))
        rows.append('kind=PATH tok=%s: item=0 name="/opt/quill/loader"\n' % tag)
        rows.append('kind=TITLE tok=%s: title=2F6F70742F7175696C6C\n' % tag)
    return "".join(rows)


def clock_only_log(n=240, per_second=6):
    """No bracket, no CLF grammar, and the stamp is NOT at the start of the line
    — so neither the leading-timestamp framing nor the single-line grammar gate
    can claim this file.  It falls through to the last-resort key search, where
    the only thing standing between it and a 6× undercount is the rule that a
    key must identify a transaction rather than a moment."""
    rows = []
    for i in range(n):
        sec = i // per_second
        rows.append("q7 2031-03-04T05:%02d:%02dZ loader slice=%d rc=0 bytes=%d\n"
                    % (sec // 60 % 60, sec % 60, i, 400 + i))
    return "".join(rows)


MAP_LINE_RE = re.compile(r"строк (\d+) · записей (\d+) · кадрирование (\S+)")


def run_logmap(corpus, out, skill=None):
    p = subprocess.run(
        [sys.executable,
         os.path.join(skill or UNDER_TEST, "tools", "logmap.py"), corpus,
         "--out", out], capture_output=True, text=True)
    return p


def map_rows(out_dir):
    """-> {relpath: (lines, records, framing)} read from the map the tool wrote."""
    path = os.path.join(out_dir, "map.txt")
    rows, current = {}, None
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            m = MAP_LINE_RE.search(line)
            if m and current:
                rows[current] = (int(m.group(1)), int(m.group(2)), m.group(3))
                current = None
                continue
            stripped = line.rstrip("\n")
            if stripped and not stripped.startswith(" ") \
                    and not stripped.startswith("="):
                current = stripped.split("  ")[0].strip()
    return rows


def build(root, files):
    for rel, text in files.items():
        write(os.path.join(root, HOST, rel), text)


def mapped(files):
    """Run the tool under test over a synthetic bundle -> the map's own rows."""
    tmp = tempfile.mkdtemp()
    corpus = os.path.join(tmp, "corpus")
    out = os.path.join(tmp, "out")
    build(corpus, files)
    p = run_logmap(corpus, out)
    if p.returncode != 0:
        raise AssertionError("logmap failed: %s" % p.stderr)
    return map_rows(out), tmp


# ===========================================================================
# 1 — THE TRIPWIRE. A single-line grammar has as many records as it has lines.
# ===========================================================================
class ARecordIsNotAMoment(unittest.TestCase):

    def test_single_line_grammar_with_repeated_stamps_counts_every_line(self):
        """The one number a human can check by hand. `wc -l` is the answer."""
        n, per_second = 300, 4
        rows, _tmp = mapped({"logs/gateway.log": gateway_log(n, per_second)})
        key = "%s/logs/gateway.log" % HOST
        self.assertIn(key, rows, "the map lost the file: %r" % sorted(rows))
        lines, records, framing = rows[key]
        self.assertEqual(n, lines)
        self.assertEqual(
            n, records,
            "%d one-line requests were counted as %d records (кадрирование %s)"
            " — %d requests inside one second were fused into one transaction"
            % (n, records, framing, per_second))

    def test_a_bare_table_keyed_on_its_own_clock_is_not_grouped(self):
        """No grammar to fall back on: only the clock rejection saves this one."""
        n, per_second = 240, 6
        rows, _tmp = mapped({"logs/loader.tsv": clock_only_log(n, per_second)})
        key = "%s/logs/loader.tsv" % HOST
        lines, records, framing = rows[key]
        self.assertEqual(n, lines)
        self.assertEqual(n, records,
                         "grouped a table on its own timestamp: %d lines -> %d "
                         "records, кадрирование %s" % (lines, records, framing))

    def test_the_framing_of_a_single_line_grammar_is_not_a_key(self):
        rows, _tmp = mapped({"logs/gateway.log": gateway_log()})
        _l, _r, framing = rows["%s/logs/gateway.log" % HOST]
        self.assertFalse(
            framing.startswith("key:"),
            "a one-record-per-line format was grouped on a shared field (%s)"
            % framing)

    def test_records_never_exceed_lines_on_any_synthetic_shape(self):
        """The other half of the invariant. Grouping may only ever REDUCE."""
        files = {"logs/gateway.log": gateway_log(),
                 "logs/loader.tsv": clock_only_log(),
                 "logs/quill.log": correlated_log()}
        rows, _tmp = mapped(files)
        for rel, (lines, records, framing) in sorted(rows.items()):
            self.assertLessEqual(records, lines,
                                 "%s: %d records out of %d lines (%s)"
                                 % (rel, records, lines, framing))
            self.assertGreater(records, 0, rel)


# ===========================================================================
# 2 — the capability the fix must not break
# ===========================================================================
class ARealCorrelationTokenStillGroups(unittest.TestCase):

    def test_a_token_that_contains_a_clock_and_a_serial_still_frames(self):
        """`<epoch>.<ms>:<serial>` is a transaction id that happens to carry a
        moment. Strip the moment and the serial is still there, so it passes."""
        events, lines_per_event = 80, 4
        rows, _tmp = mapped({"logs/quill.log": correlated_log(events)})
        lines, records, framing = rows["%s/logs/quill.log" % HOST]
        self.assertEqual(events * lines_per_event, lines)
        self.assertEqual(
            events, records,
            "the four lines of one transaction stopped being one record: "
            "%d lines -> %d records, кадрирование %s" % (lines, records, framing))
        self.assertTrue(framing.startswith("key:"), framing)


# ===========================================================================
# 3 — the discriminator itself, called directly
# ===========================================================================
class TheClockIsNotAKey(unittest.TestCase):

    def setUp(self):
        self.lm = logmap_module()

    def test_detect_key_framing_refuses_a_field_that_is_only_a_clock(self):
        lines = clock_only_log().splitlines(True)
        self.assertIsNone(self.lm.detect_key_framing(lines))

    def test_detect_key_framing_refuses_the_bracketed_stamp_of_a_request_line(self):
        lines = gateway_log().splitlines(True)
        self.assertIsNone(self.lm.detect_key_framing(lines))

    def test_detect_key_framing_keeps_a_token_with_a_serial_behind_the_clock(self):
        lines = correlated_log().splitlines(True)
        self.assertEqual("kv:tok", self.lm.detect_key_framing(lines))

    def test_a_candidate_with_no_clock_in_it_is_judged_exactly_as_before(self):
        """The residue of a value that carries no timestamp is the value, so
        nothing that was not a clock can change verdict."""
        for v in ("abc-9f31-req", "sess42", "/opt/quill/loader", "q7"):
            self.assertEqual(v, self.lm.clock_residue(v),
                             "a value with no timestamp was altered: %r" % v)

    def test_a_bare_timestamp_leaves_nothing_behind(self):
        for v in ("[04/Mar/2031:05:01:15", "2031-03-04T05:06:07"):
            self.assertEqual(
                "", re.sub(r"[^0-9A-Za-z]", "", self.lm.clock_residue(v)),
                "a bare timestamp still looks like an identifier: %r" % v)


# ===========================================================================
# 4 — WHAT YOU DELIVER IS WHAT YOU CHECKED
# ===========================================================================
CORPUS_LINES = [
    "2031-03-04T05:06:01Z loader[41]: harness=quill-spare handover accepted",
    "2031-03-04T05:06:02Z loader[41]: slice 7 written ok",
    "2031-03-04T05:06:03Z loader[41]: standby rota took over queue 3",
    "2031-03-04T05:06:04Z loader[41]: checksum q7f1 verified",
]


def delivery_corpus(root):
    write(os.path.join(root, HOST, "logs", "loader.log"),
          "".join(l + "\n" for l in CORPUS_LINES))


def run_citecheck(report_text, corpus, delivered_text=None, extra=()):
    tmp = tempfile.mkdtemp()
    rp = os.path.join(tmp, "report.md")
    io.open(rp, "w", encoding="utf-8").write(report_text)
    argv = [sys.executable, CITECHECK, rp, "--corpus", corpus, "--json"]
    if delivered_text is not None:
        dp = os.path.join(tmp, "handover.md")
        io.open(dp, "w", encoding="utf-8").write(delivered_text)
        argv += ["--delivered", dp]
    p = subprocess.run(argv + list(extra), capture_output=True, text=True)
    try:
        d = json.loads(p.stdout or "{}")
    except ValueError:
        d = {}
    return p.returncode, d, p.stdout, p.stderr


REPORT = """# Отчёт

## 1. Находки

**Н-1.** Передача смены принята дежурной сменой —
«harness=quill-spare handover accepted» — %(h)s/logs/loader.log:1

**Н-2.** Очередь 3 перешла к резервной смене —
«standby rota took over queue 3» — %(h)s/logs/loader.log:3
""" % {"h": HOST}


class TheHandoverIsChecked(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        delivery_corpus(self.corpus)

    def test_the_report_alone_is_clean(self):
        rc, d, out, err = run_citecheck(REPORT, self.corpus)
        self.assertEqual(0, rc, out + err)
        self.assertEqual(2, d["summary"]["ok"], out)

    def test_a_handover_that_is_the_report_verbatim_passes(self):
        rc, d, out, err = run_citecheck(REPORT, self.corpus, REPORT)
        self.assertEqual(0, rc, out + err)
        self.assertIn("delivered", d, out)
        self.assertEqual(0, d["delivered"]["summary"]["wrong-content"], out)
        self.assertEqual([], d["delivered"]["not_in_checked"], out)

    def test_a_retyped_citation_under_a_new_claim_fails(self):
        """The measured failure, in miniature: a condensed inventory section
        packs several references into one sentence, and the words around a
        reference belong to a DIFFERENT line.  The citation itself is in the
        verified set — the claim wrapped around it never was."""
        handover = REPORT + (
            "\n## 5. Адреса и пути\n\n"
            "Сводка: `checksum q7f1 verified` и `slice 7 written ok` — "
            "%s/logs/loader.log:1\n" % HOST)
        rc, d, out, err = run_citecheck(REPORT, self.corpus, handover)
        self.assertNotEqual(0, rc,
                            "a hand-over with an unchecked claim was passed:\n"
                            + out + err)
        self.assertGreater(d["delivered"]["summary"]["wrong-content"], 0, out)

    def test_a_citation_the_report_never_carried_is_named(self):
        """The subset half. The line even supports the claim — it is still a
        claim that was never checked in the artefact the run verified."""
        handover = REPORT + (
            "\n**Н-3.** Контрольная сумма сверена — «checksum q7f1 verified» — "
            "%s/logs/loader.log:4\n" % HOST)
        rc, d, out, err = run_citecheck(REPORT, self.corpus, handover)
        self.assertEqual(0, d["delivered"]["summary"]["wrong-content"], out)
        self.assertEqual(1, len(d["delivered"]["not_in_checked"]),
                         "a citation absent from the checked set was not named:"
                         "\n" + out)
        self.assertNotEqual(0, rc, out + err)

    def test_a_condensation_that_drops_findings_passes(self):
        """A subset is allowed to be smaller. Only ADDING is a new claim."""
        short = REPORT.split("**Н-2.**")[0]
        rc, d, out, err = run_citecheck(REPORT, self.corpus, short)
        self.assertEqual(0, rc, out + err)
        self.assertEqual([], d["delivered"]["not_in_checked"], out)

    def test_the_render_names_both_channels(self):
        tmp = tempfile.mkdtemp()
        rp = os.path.join(tmp, "report.md")
        dp = os.path.join(tmp, "handover.md")
        io.open(rp, "w", encoding="utf-8").write(REPORT)
        io.open(dp, "w", encoding="utf-8").write(REPORT)
        p = subprocess.run([sys.executable, CITECHECK, rp, "--corpus",
                            self.corpus, "--delivered", dp],
                           capture_output=True, text=True)
        self.assertIn("ПОСТАВКА", p.stdout, p.stdout + p.stderr)

    def test_the_gate_survives_a_delivered_file_that_does_not_exist(self):
        tmp = tempfile.mkdtemp()
        rp = os.path.join(tmp, "report.md")
        io.open(rp, "w", encoding="utf-8").write(REPORT)
        p = subprocess.run([sys.executable, CITECHECK, rp, "--corpus",
                            self.corpus, "--delivered",
                            os.path.join(tmp, "nope.md")],
                           capture_output=True, text=True)
        self.assertNotEqual(0, p.returncode)


# ===========================================================================
# 5 — the skill says it, and says how to check it
# ===========================================================================
class TheSkillCarriesTheRule(unittest.TestCase):

    def setUp(self):
        self.skill = io.open(os.path.join(UNDER_TEST, "SKILL.md"),
                             encoding="utf-8").read()
        self.tools_doc = io.open(
            os.path.join(UNDER_TEST, "reference", "tools.md"),
            encoding="utf-8").read()

    def test_the_delivery_rule_is_stated(self):
        self.assertIn("--delivered", self.skill,
                      "the hand-over check is not in SKILL.md")

    def test_the_stopping_condition_names_the_check(self):
        """Point 5 of «Условие остановки» was the one nothing checked."""
        start = self.skill.find("Условие остановки")
        self.assertGreater(start, 0)
        tail = self.skill[start:start + 3000]
        self.assertIn("--delivered", tail,
                      "the stopping condition still leaves the hand-over "
                      "unchecked")

    def test_the_tools_reference_documents_the_flag(self):
        self.assertIn("--delivered", self.tools_doc)


# ===========================================================================
# 6 — v23 is the smallest change that carries both fixes
# ===========================================================================
class TheDeltaIsSmall(unittest.TestCase):

    def test_no_file_was_added_or_removed(self):
        a = {os.path.relpath(os.path.join(dp, fn), V22)
             for dp, dn, fns in os.walk(V22) for fn in fns
             if "__pycache__" not in dp}
        b = {os.path.relpath(os.path.join(dp, fn), V23)
             for dp, dn, fns in os.walk(V23) for fn in fns
             if "__pycache__" not in dp}
        self.assertEqual(set(), b - a, "v23 added a file")
        self.assertEqual(set(), a - b, "v23 lost a file")

    def test_only_the_two_tools_and_the_docs_moved(self):
        moved = set()
        for dp, dn, fns in os.walk(V23):
            if "__pycache__" in dp:
                continue
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), V23)
                if not filecmp.cmp(os.path.join(V22, rel),
                                   os.path.join(V23, rel), shallow=False):
                    moved.add(rel)
        self.assertEqual(
            {"SKILL.md", "reference/tools.md", "tools/logmap.py",
             "tools/citecheck.py"}, moved,
            "v23 touches more than the framing key and the hand-over check")

    def test_the_other_two_tools_are_byte_identical(self):
        for name in ("logjoin.py", "triagecheck.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V22, "tools", name),
                            os.path.join(V23, "tools", name), shallow=False),
                "%s moved — v23 fixes framing and delivery, nothing else" % name)


# ===========================================================================
# 7 — the anti-crib-sheet discipline, applied to v23
# ===========================================================================
class TheNewCodeCarriesNoCorpusKnowledge(unittest.TestCase):
    """v19 (what the skill says), v20 (what the source says), v21 (the shape of
    the needle) — all three, against v23."""

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

    def test_this_tripwire_states_no_corpus_number(self):
        """The test file itself is a channel. Its numbers are its own."""
        text = io.open(os.path.abspath(__file__), encoding="utf-8").read()
        for tok, ln, line in V19T.forbidden_hits(text):
            self.fail("the tripwire names a corpus: %s (line %d) %s"
                      % (tok, ln, line[:100]))


# ===========================================================================
# 8 — the frozen arms
# ===========================================================================
class FrozenArms(unittest.TestCase):

    EXPECTED_V22 = {
        "SKILL.md": "8bf27c2fc1e4e3e7bb101eb5945d429d",
        "tools/logmap.py": "ee301e89a4cda006415e36c7e3ad8624",
        "tools/citecheck.py": "82bece3c236a70de871da66c8e95758c",
        "tools/logjoin.py": "a0c1e11c9c52aaa814f1c26480ac37a4",
        "tools/triagecheck.py": "43b6420b708c4ec637e61a47fd52684f",
        "reference/report-format.md": "d19a98be30ab2b52fd30cceca3860169",
        "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
        "reference/tools.md": "55a3c3952e64fa54b41a236a7dc43b26",
    }

    def test_no_frozen_arm_moved(self):
        """v1..v22 are frozen as directories: a new file inside one ships to a
        measured agent just as surely as an edit to an old one."""
        for n in range(1, 23):
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

    def test_v22_hashes_are_recorded(self):
        """Pinned by content the moment v23 exists, so the next version has the
        same anchor v22 gave to v21."""
        got = {}
        for rel in sorted(self.EXPECTED_V22):
            path = os.path.join(V22, rel)
            got[rel] = hashlib.md5(open(path, "rb").read()).hexdigest()
        want = {k: v for k, v in self.EXPECTED_V22.items() if v}
        if not want:
            self.skipTest("hashes recorded on the first green run: %r" % got)
        self.assertEqual(want, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
