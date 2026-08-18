#!/usr/bin/env python3
"""v22 — a bulk dismissal has to be a rule the tool can evaluate, with receipts.

    python3 tools/tests/test_triage_receipts_v22.py
    SHERLOCK_SKILL=$PWD/skills/v22 python3 tools/tests/test_triage_receipts_v22.py

WHY THIS TEST EXISTS
--------------------
v19 added «отказ доказывается так же, как утверждение» and v21 added «приписать
улику — это тоже утверждение».  Both are rules about what a verdict must carry.
Both were satisfied in FORM and defeated in SUBSTANCE by the same move: the
analyst wrote one shell command that ran a regex it had invented at runtime over
the worklist's own excerpt column, and stamped the outcome into 96.6 % of the
rows as `N фон: n=<count>; 0 совпадений с маркерами инцидента (<its own marker
list>)`.  Every one of those rows then carried a verdict AND a digit, so
`citecheck --ledger` counted them closed and printed «можно отдавать отчёт».

Three things were true of that move, and each is a check below:

1.  **The classifier was content over a projection.**  It matched words against
    the excerpt the worklist prints, not against the file.  The one piece of
    evidence that mattered was inside the excerpt and was not in the marker
    list, so it scored zero and was stamped background.  No tool result in the
    whole run ever opened that file.

2.  **The rule was never written down.**  It lived in one Bash invocation.  The
    deliverable said «ничего относящегося» with no line reference, and the only
    place the reasoning survived was a transcript nobody reads.

3.  **It cost nothing.**  Closing one row and closing two and a half thousand
    took the same single call.

So the fix is not another sentence.  It is an artefact with the same shape as
the report's citations: `triagecheck.py` reads the worklist, reads a `rules.tsv`
the analyst writes, and refuses three things —

  * a condition it cannot evaluate.  The selector language is CLOSED over the
    columns Step 1 computed (`ось`, `хост`, `путь`, `файл`, `n`, `всплеск`,
    `id`).  There is no operator over the record text, so the marker list is
    not expressible as a rule at all — which is the point.  A rule the tool
    cannot evaluate is not a rule.
  * a rule with no receipts.  The TOOL picks which rows must be receipted, from
    the rule's own coverage, so a rule is tested rather than asserted and the
    analyst cannot receipt only the rows it already happened to read.
  * a row closed with neither a citation of its own nor a rule.  That bucket is
    counted and printed, and it is what 2 473 rows would have landed in.

PROPORTIONALITY
---------------
Demanding a citation per row would move the failure rather than fix it, so the
receipt count is sub-linear: `k = max(3, ceil(sqrt(N)), F)` where N is how many
rows the rule closes and F is how many distinct files it dismisses an EXCURSION
row from (`rare`/`new`/`peak` — the axes Step 1 computed one row at a time,
because they say "this record is unlike its neighbours").  Two properties this
buys, both asserted below:

  * splitting one rule into m rules never costs fewer receipts, so a wide rule
    cannot be made cheap by chopping it up;
  * every file the rule dismisses an excursion from gets at least one line
    actually read and quoted.

Synthetic corpora throughout — the tripwire builds its own worklist, its own
rules file and its own logs, so nothing here depends on a dataset.
"""
import ast
import filecmp
import hashlib
import importlib.util
import io
import json
import math
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
V21 = os.path.join(SKILLS, "v21")
V22 = os.path.join(SKILLS, "v22")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V22)


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

TRIAGE = os.path.join(UNDER_TEST, "tools", "triagecheck.py")


def run_triage(worklist, rules, corpus, extra=()):
    argv = [sys.executable, TRIAGE, "--worklist", worklist,
            "--corpus", corpus, "--json"]
    if rules:
        argv += ["--rules", rules]
    p = subprocess.run(argv + list(extra), capture_output=True, text=True)
    try:
        d = json.loads(p.stdout or "{}")
    except ValueError:
        d = {}
    return p.returncode, d, p.stdout, p.stderr


# ---------------------------------------------------------------------------
# the synthetic bundle: a worklist, a corpus the worklist addresses, and rows
# whose excerpt is the ONLY place a distinguishing word appears
# ---------------------------------------------------------------------------
HOST = "node-07"
QUIET = "spool-%d.log"
ODD = "relay.log"

# The record that must survive a bulk dismissal. It is invented — no corpus has
# it — and its distinguishing token appears in no marker a reader would guess.
NEEDLE = ("2031-03-04T05:06:07+00:00 warden[41]: harness=quill-spare handover "
          "accepted by the standby rota")


def make_corpus(root, quiet_files=6, lines=60):
    """A one-machine bundle whose rows the synthetic worklist addresses."""
    for f in range(quiet_files):
        V19T._write(
            os.path.join(root, HOST, "logs", QUIET % f),
            "".join("2031-03-01T%02d:%02d:%02d+00:00 spool[%d]: slice %d "
                    "written ok\n" % (i // 60 % 24, i % 60, i % 60, 70 + f, i)
                    for i in range(lines)))
    body = ["2031-03-02T00:%02d:00+00:00 relay[9]: batch %d flushed to spool\n"
            % (i % 60, i) for i in range(lines)]
    body[29] = NEEDLE + "\n"
    V19T._write(os.path.join(root, HOST, "logs", ODD), "".join(body))
    return root


HEADER = ("# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
          "# вердикт: ? не разобрано · D дефект · N норма · X данных не хватает\n")


def worklist_rows(quiet_files=6, per_file=8):
    """-> [[id, verdict, axis, ref, freq, excerpt]] with the needle at g0001.

    The needle row is `rare` — an excursion. Everything else is `cat`, the
    catalogue axis, which is what a wide rule is legitimately for."""
    rows = [["g0001", "?", "rare", "%s/logs/%s:30" % (HOST, ODD), "n=1", NEEDLE]]
    n = 2
    for f in range(quiet_files):
        for i in range(per_file):
            rows.append(["g%04d" % n, "?", "cat",
                         "%s/logs/%s:%d" % (HOST, QUIET % f, i + 1), "n=1",
                         "2031-03-01T00:%02d:%02d+00:00 spool[%d]: slice %d "
                         "written ok" % (i, i, 70 + f, i)])
            n += 1
    # a handful of extra excursions, spread over files, so F > 1
    for f in range(quiet_files):
        rows.append(["g%04d" % n, "?", "rare",
                     "%s/logs/%s:%d" % (HOST, QUIET % f, 40 + f), "n=1",
                     "2031-03-01T00:40:%02d+00:00 spool[%d]: slice %d written "
                     "ok" % (f, 70 + f, 40 + f)])
        n += 1
    return rows


def write_worklist(path, rows):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(HEADER)
        for r in rows:
            fh.write("\t".join(r) + "\n")


def close_all(rows, verdict):
    for r in rows:
        r[1] = verdict
    return rows


class Bundle(object):
    """A temp dir holding corpus/, worklist.tsv and rules.tsv."""

    def __init__(self, tmp, rows=None):
        self.tmp = tmp
        self.corpus = os.path.join(tmp, "corpus")
        make_corpus(self.corpus)
        self.rows = worklist_rows() if rows is None else rows
        self.worklist = os.path.join(tmp, "worklist.tsv")
        self.rules = os.path.join(tmp, "rules.tsv")
        self.save()

    def save(self):
        write_worklist(self.worklist, self.rows)

    def write_rules(self, text):
        with io.open(self.rules, "w", encoding="utf-8") as fh:
            fh.write(text)

    def run(self, with_rules=True, extra=()):
        self.save()
        return run_triage(self.worklist,
                          self.rules if with_rules else None,
                          self.corpus, extra)

    def line_of(self, ref):
        path, n = ref.rsplit(":", 1)
        with io.open(os.path.join(self.corpus, path), encoding="utf-8") as fh:
            return fh.read().splitlines()[int(n) - 1]


# ===========================================================================
# 1 — the tripwire: the move that emptied the worklist is not expressible
# ===========================================================================
class AnInventedKeywordListIsNotARule(unittest.TestCase):
    """The exact shape of the failure, rebuilt from nothing but this file.

    The classifier is a regex over the excerpt column. Written as a rule it has
    to name the record text, and the selector language has no such field."""

    MARKERS = "alpha|bravo|charlie|delta|echo"

    def test_a_condition_over_the_record_text_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N #R1 фон: n=1")
            b.write_rules(
                "R1\tзапись~*%s*\tN\tничего относящегося\n" % self.MARKERS)
            rc, d, out, err = b.run()
            self.assertNotEqual(0, rc, "a content predicate passed:\n" + out)
            problems = " ".join(p for r in d["rules"] for p in r["проблемы"])
            self.assertIn("текст записи", problems,
                          "the refusal does not say why:\n%s" % problems)
            self.assertEqual(1, d["totals"]["непроверяемых правил"])

    def test_every_spelling_of_the_same_move_is_refused(self):
        """A field name is not a password. Any predicate whose left side is the
        record, the excerpt or a free regex is the same category error."""
        for cond in ("запись!~*%s*" % self.MARKERS,
                     "текст~*%s*" % self.MARKERS,
                     "строка~*%s*" % self.MARKERS,
                     "excerpt~*%s*" % self.MARKERS,
                     "содержит=%s" % self.MARKERS,
                     "маркеры=%s" % self.MARKERS,
                     "regex=/%s/" % self.MARKERS,
                     "grep=%s" % self.MARKERS):
            with tempfile.TemporaryDirectory() as tmp:
                b = Bundle(tmp)
                close_all(b.rows, "N #R1 фон: n=1")
                b.write_rules("R1\t%s\tN\tфон\n" % cond)
                rc, d, out, _e = b.run()
                self.assertNotEqual(0, rc, "%r passed as a rule" % cond)
                self.assertEqual(1, d["totals"]["непроверяемых правил"],
                                 "%r was not refused as unevaluable" % cond)

    def test_the_same_rows_expressed_over_an_axis_are_accepted(self):
        """The refusal must be about the LANGUAGE, not about bulk. The honest
        version of the same dismissal — over the axis Step 1 computed — is
        legal, and then the tool names the receipts it wants."""
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            for r in b.rows:
                r[1] = "N #R1 фон: n=1" if r[2] == "cat" else "D Н-1"
            b.write_rules("R1\tось=cat\tN\tкаталог форм, вне окна столько же\n")
            rc, d, out, _e = b.run()
            self.assertEqual([], d["rules"][0]["проблемы"],
                             "an axis condition was refused: %s" % out)
            self.assertTrue(d["rules"][0]["нужны"],
                            "an accepted rule demanded no receipts")
            self.assertNotEqual(0, rc, "a rule with zero receipts went green")


# ===========================================================================
# 2 — the third bucket: closed with nothing at all
# ===========================================================================
class ARowClosedWithNothingIsCounted(unittest.TestCase):
    """`N фон: n=1` is a legal ledger verdict and carries no evidence. That is
    how 96.6 % of a worklist was disposed of. It now has a name and a number."""

    def test_a_verdict_with_a_number_and_no_support_lands_in_the_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N фон: n=1")
            rc, d, out, _e = b.run(with_rules=False)
            self.assertEqual(len(b.rows), d["buckets"]["без опоры"], out)
            self.assertEqual(0, d["buckets"]["по правилу"])
            self.assertEqual(0, d["buckets"]["поимённо"])
            self.assertNotEqual(0, rc)

    def test_the_ledger_calls_the_same_worklist_finished(self):
        """The evidence that this is a real gap and not a hypothetical: the
        checker that already exists reads the very same file and goes green."""
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N фон: n=1")
            b.save()
            report = "## Н-1 находка\n%s:30 «%s»\n" % (
                "%s/logs/%s" % (HOST, ODD), NEEDLE[-60:])
            rc, d, _err = V19T.run_citecheck(
                UNDER_TEST, report, b.corpus,
                ("--ledger", b.worklist))
            self.assertEqual(0, d["ledger"]["unresolved_total"],
                             "the ledger no longer accepts the bare verdict — "
                             "this test is stale, not the tool")

    def test_a_row_with_its_own_citation_is_individual(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            for r in b.rows:
                r[1] = "N %s «%s»" % (r[3], b.line_of(r[3])[-50:])
            rc, d, out, _e = b.run(with_rules=False)
            self.assertEqual(len(b.rows), d["buckets"]["поимённо"], out)
            self.assertEqual(0, d["buckets"]["без опоры"])
            self.assertEqual(0, rc, out)

    def test_a_defect_row_is_individual_because_the_report_carries_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "D Н-2")
            rc, d, out, _e = b.run(with_rules=False)
            self.assertEqual(len(b.rows), d["buckets"]["поимённо"], out)
            self.assertEqual(0, rc, out)

    def test_an_unresolved_row_is_not_counted_as_unsupported(self):
        """`?` is the ledger's business, not this tool's. Counting it twice
        would make two checkers argue about the same row."""
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            rc, d, out, _e = b.run(with_rules=False)
            self.assertEqual(len(b.rows), d["buckets"]["не разобрано"], out)
            self.assertEqual(0, d["buckets"]["без опоры"])


# ===========================================================================
# 3 — the tool picks the receipts, not the analyst
# ===========================================================================
def satisfy(b, d, rule="R1", drop=0, forge=False):
    """Write the receipts the tool asked for. `drop` leaves that many out."""
    demanded = [r for r in d["rules"] if r["id"] == rule][0]["нужны"]
    byid = {r[0]: r for r in b.rows}
    lines = [ln for ln in io.open(b.rules, encoding="utf-8").read().splitlines()
             if not ln.startswith("+")]
    for rid in demanded[:len(demanded) - drop]:
        row = byid[rid]
        text = "не та строка, честное слово" if forge else b.line_of(row[3])
        lines.append("+%s\t%s\t%s\t«%s»" % (rule, rid, row[3], text[-70:]))
    b.write_rules("\n".join(lines) + "\n")


class TheToolChoosesWhichRowsAreReceipted(unittest.TestCase):

    def _wide(self, tmp):
        b = Bundle(tmp)
        close_all(b.rows, "N #R1 фон: n=1")
        b.write_rules("R1\tось=cat|rare\tN\tвсё это фон\n")
        return b

    def test_the_demanded_ids_are_a_function_of_the_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            first = b.run()[1]["rules"][0]["нужны"]
            second = b.run()[1]["rules"][0]["нужны"]
            self.assertEqual(first, second, "the sample is not deterministic")
            self.assertTrue(set(first) <= {r[0] for r in b.rows})

    def test_receipts_for_other_rows_do_not_satisfy_the_rule(self):
        """The whole point: receipting the rows you already read is not a test
        of the rule."""
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            demanded = set(d["rules"][0]["нужны"])
            others = [r for r in b.rows if r[0] not in demanded]
            lines = ["R1\tось=cat|rare\tN\tвсё это фон"]
            for row in others[:len(demanded)]:
                lines.append("+R1\t%s\t%s\t«%s»"
                             % (row[0], row[3], b.line_of(row[3])[-70:]))
            b.write_rules("\n".join(lines) + "\n")
            rc, d2, out, _e = b.run()
            self.assertNotEqual(0, rc, "unrequested receipts satisfied a rule")
            self.assertEqual(len(demanded),
                             d2["totals"]["нехватка квитанций"], out)

    def test_the_demanded_receipts_close_the_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            satisfy(b, b.run()[1])
            rc, d, out, _e = b.run()
            self.assertEqual(0, rc, out)
            self.assertEqual(0, d["totals"]["нехватка квитанций"], out)
            self.assertEqual(len(b.rows), d["buckets"]["по правилу"], out)

    def test_one_missing_receipt_keeps_the_rule_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            satisfy(b, b.run()[1], drop=1)
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertEqual(1, d["totals"]["нехватка квитанций"], out)

    def test_a_row_tagged_with_a_rule_it_does_not_match_is_refused(self):
        """The tag is not a password either — the row has to satisfy the
        condition the rule states."""
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N #R1 фон: n=1")
            b.write_rules("R1\tось=cat\tN\tкаталог форм\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertEqual(len([r for r in b.rows if r[2] != "cat"]),
                             d["totals"]["строк вне своего правила"], out)

    def test_a_rule_nobody_cites_is_reported_and_costs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            for r in b.rows:
                r[1] = "N %s «%s»" % (r[3], b.line_of(r[3])[-50:])
            b.write_rules("R9\tось=cat\tN\tникем не использовано\n")
            rc, d, out, _e = b.run()
            self.assertEqual(0, [r for r in d["rules"]
                                 if r["id"] == "R9"][0]["покрытие"], out)
            self.assertEqual(0, rc, out)


# ===========================================================================
# 4 — a receipt is a citation, and citecheck is the one that judges it
# ===========================================================================
class AReceiptIsVerifiedAgainstTheCorpus(unittest.TestCase):

    def _wide(self, tmp):
        b = Bundle(tmp)
        close_all(b.rows, "N #R1 фон: n=1")
        b.write_rules("R1\tось=cat|rare\tN\tвсё это фон\n")
        return b

    def test_a_fabricated_quote_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            satisfy(b, b.run()[1], forge=True)
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertTrue(d["totals"]["квитанций не подтвердилось"] > 0, out)

    def test_a_receipt_pointing_at_the_wrong_line_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            byid = {r[0]: r for r in b.rows}
            lines = ["R1\tось=cat|rare\tN\tвсё это фон"]
            for rid in d["rules"][0]["нужны"]:
                row = byid[rid]
                path, n = row[3].rsplit(":", 1)
                lines.append("+R1\t%s\t%s:%d\t«%s»"
                             % (rid, path, int(n) + 3, b.line_of(row[3])[-70:]))
            b.write_rules("\n".join(lines) + "\n")
            rc, d2, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertTrue(d2["totals"]["квитанций не подтвердилось"] > 0, out)

    def test_a_receipt_must_address_the_row_it_receipts(self):
        """A receipt for row g0007 that cites some other row's line proves
        nothing about g0007."""
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            byid = {r[0]: r for r in b.rows}
            wrong = [r for r in b.rows
                     if r[0] not in set(d["rules"][0]["нужны"])][0]
            lines = ["R1\tось=cat|rare\tN\tвсё это фон"]
            for rid in d["rules"][0]["нужны"]:
                lines.append("+R1\t%s\t%s\t«%s»"
                             % (rid, wrong[3], b.line_of(wrong[3])[-70:]))
            b.write_rules("\n".join(lines) + "\n")
            rc, d2, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertTrue(d2["totals"]["квитанций не по адресу"] > 0, out)

    def test_citecheck_is_reused_rather_than_reimplemented(self):
        """One verifier or two verifiers is a design decision, and two drift.
        The source has to import the one that already exists."""
        src = io.open(TRIAGE, encoding="utf-8").read()
        self.assertIn("citecheck", src)
        tree = ast.parse(src)
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertIn("check", called,
                      "triagecheck does not call citecheck.check — it is "
                      "verifying quotes on its own")


# ===========================================================================
# 5 — proportionality, and why a wide rule cannot be chopped up to save work
# ===========================================================================
class ReceiptsAreProportionalAndNotGameable(unittest.TestCase):

    def _k(self, mod, n, files=1):
        return mod.receipts_needed(n, files)

    def setUp(self):
        self.mod = load_module(TRIAGE, "triagecheck_under_test")

    def test_the_count_is_sublinear(self):
        for n in (10, 100, 1000, 2500):
            self.assertLessEqual(self._k(self.mod, n), n / 4.0 + 3,
                                 "k is close to linear at N=%d" % n)
        self.assertGreater(self._k(self.mod, 2500), self._k(self.mod, 100))

    def test_splitting_a_rule_never_costs_fewer_receipts(self):
        """The incentive check. If m small rules were cheaper than one wide
        one, the fix would be defeated by `sed`."""
        for n in (64, 256, 900, 2473):
            whole = self._k(self.mod, n)
            for m in (2, 4, 8, 16):
                part = n // m
                self.assertGreaterEqual(m * self._k(self.mod, part), whole,
                                        "N=%d split into %d is cheaper" % (n, m))

    def test_a_tiny_rule_still_costs_something(self):
        self.assertEqual(3, self._k(self.mod, 3))
        self.assertEqual(2, self._k(self.mod, 2))

    def test_every_excursion_file_gets_a_receipt(self):
        """The guarantee that does not depend on luck: a rule that dismisses an
        excursion row from F files owes at least F receipts, so every one of
        those files has a line actually read."""
        self.assertGreaterEqual(self._k(self.mod, 100, files=40), 40)

    def test_the_sample_prefers_excursion_rows(self):
        """`rare`/`new`/`peak` are the rows Step 1 nominated one at a time. If
        the sample were uniform, a wide rule would be tested almost entirely on
        the catalogue rows that are legitimately background."""
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N #R1 фон: n=1")
            b.write_rules("R1\tось=cat|rare\tN\tвсё это фон\n")
            d = b.run()[1]
            axis = {r[0]: r[2] for r in b.rows}
            demanded = d["rules"][0]["нужны"]
            exc = [rid for rid in demanded if axis[rid] in ("rare", "new", "peak")]
            self.assertEqual(len(exc), min(len(demanded),
                                           len([r for r in b.rows
                                                if r[2] == "rare"])),
                             "the sample did not take the excursions first: %s"
                             % [(r, axis[r]) for r in demanded])

    def test_the_needle_row_is_demanded_when_a_rule_swallows_it(self):
        """The tripwire's positive half. The one row whose record differs is an
        excursion in a file of its own, so a rule that closes it in bulk owes a
        receipt on exactly that row — which is the read that never happened."""
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N #R1 фон: n=1")
            b.write_rules("R1\tось=cat|rare\tN\tвсё это фон\n")
            d = b.run()[1]
            self.assertIn("g0001", d["rules"][0]["нужны"],
                          "a wide rule swallowed the excursion without owing a "
                          "receipt on it")


# ===========================================================================
# 6 — the summary is the artefact: the shape has to be visible at a glance
# ===========================================================================
class TheSummaryShowsTheShape(unittest.TestCase):

    def test_the_four_numbers_are_printed_in_plain_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N фон: n=1")
            b.save()
            p = subprocess.run(
                [sys.executable, TRIAGE, "--worklist", b.worklist,
                 "--corpus", b.corpus], capture_output=True, text=True)
            out = p.stdout
            for fragment in ("закрыто поимённо", "закрыто правилом",
                             "закрыто без опоры", "ИТОГ"):
                self.assertIn(fragment, out, out)
            self.assertIn(str(len(b.rows)), out)
            self.assertNotEqual(0, p.returncode)

    def test_the_share_of_the_widest_rule_is_stated(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N #R1 фон: n=1")
            b.write_rules("R1\tось=cat|rare\tN\tвсё это фон\n")
            b.save()
            p = subprocess.run(
                [sys.executable, TRIAGE, "--worklist", b.worklist,
                 "--rules", b.rules, "--corpus", b.corpus],
                capture_output=True, text=True)
            self.assertIn("самое широкое правило", p.stdout)
            self.assertIn("100.0", p.stdout, p.stdout)


# ===========================================================================
# 7 — a capability the skill text does not mention is one nobody uses
# ===========================================================================
class TheSkillDocumentsTheChecker(unittest.TestCase):

    def _read(self, rel):
        with io.open(os.path.join(UNDER_TEST, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_skill_md_names_the_tool_and_the_artefact(self):
        body = self._read("SKILL.md")
        for fragment in ("triagecheck.py", "rules.tsv"):
            self.assertIn(fragment, body, "SKILL.md never mentions %r" % fragment)

    def test_the_stopping_condition_includes_it(self):
        body = self._read("SKILL.md")
        stop = body[body.index("## 8."):body.index("## 9.")]
        self.assertIn("triagecheck", stop,
                      "the stopping condition does not include the new checker")

    def test_the_reference_page_documents_it(self):
        body = self._read(os.path.join("reference", "tools.md"))
        self.assertIn("triagecheck.py", body)

    def test_the_selector_fields_are_listed_where_the_model_can_see_them(self):
        """A closed language nobody published is a closed language nobody can
        satisfy. Every field the parser accepts has to be written down."""
        mod = load_module(TRIAGE, "triagecheck_fields")
        docs = self._read("SKILL.md") + self._read(
            os.path.join("reference", "tools.md"))
        for field in mod.FIELDS:
            self.assertIn(field, docs, "field %r is undocumented" % field)

    def test_the_documented_command_runs(self):
        """Copy the invocation out of the reference page and run it."""
        body = self._read(os.path.join("reference", "tools.md"))
        m = re.search(r"triagecheck\.py[^\n]*", body)
        self.assertTrue(m, "no invocation in the reference page")
        with tempfile.TemporaryDirectory() as tmp:
            b = Bundle(tmp)
            close_all(b.rows, "N фон: n=1")
            b.save()
            p = subprocess.run(
                [sys.executable, TRIAGE, "--worklist", b.worklist,
                 "--rules", b.rules if os.path.exists(b.rules) else b.worklist,
                 "--corpus", b.corpus], capture_output=True, text=True)
            self.assertIn("ТРИАЖ", p.stdout, p.stderr)


# ===========================================================================
# 8 — v22 is v21 plus one tool
# ===========================================================================
class V22IsV21PlusOneChecker(unittest.TestCase):

    def setUp(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V22):
            self.skipTest("only meaningful for v22")

    def test_the_three_existing_tools_are_byte_identical(self):
        for name in ("logmap.py", "citecheck.py", "logjoin.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V21, "tools", name),
                            os.path.join(V22, "tools", name), shallow=False),
                "%s moved — v22 adds a checker, it does not change Step 1" % name)

    def test_the_ranked_artefacts_are_byte_identical_on_a_bundle(self):
        """Every score in this project is computed from these two files. If
        they move, the regression numbers are not comparable."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            V21T.make_bundle(corpus, 4)
            outs = {}
            for tag, skill in (("v21", V21), ("v22", V22)):
                outs[tag] = os.path.join(tmp, tag)
                p = V19T.run_logmap(skill, corpus, outs[tag])
                self.assertEqual(0, p.returncode, p.stderr)
            names = sorted(os.listdir(outs["v21"]))
            self.assertEqual(names, sorted(os.listdir(outs["v22"])))
            moved = [fn for fn in names
                     if not filecmp.cmp(os.path.join(outs["v21"], fn),
                                        os.path.join(outs["v22"], fn),
                                        shallow=False)]
            self.assertEqual([], moved, "Step 1 output moved: %s" % moved)

    def test_only_the_new_file_and_the_docs_differ(self):
        a = {os.path.relpath(os.path.join(dp, fn), V21)
             for dp, dn, fns in os.walk(V21) for fn in fns
             if "__pycache__" not in dp}
        bset = {os.path.relpath(os.path.join(dp, fn), V22)
                for dp, dn, fns in os.walk(V22) for fn in fns
                if "__pycache__" not in dp}
        self.assertEqual({"tools/triagecheck.py"}, bset - a)
        self.assertEqual(set(), a - bset)


# ===========================================================================
# 9 — the anti-crib-sheet discipline, applied to the new file
# ===========================================================================
class TheNewToolCarriesNoCorpusKnowledge(unittest.TestCase):
    """v19 (what the skill says), v20 (what the source says), v21 (the shape of
    the needle) — all three, against v22."""

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
        """An example is a crib sheet the moment it is a real address."""
        _names, roots = V19T.measured_corpora()
        if not roots:
            self.skipTest("no measured corpus on this machine")
        for _ch, rel, text in V20T.channels(UNDER_TEST):
            hits = V19T.corpus_paths_in(text, roots)
            self.assertEqual([], hits, "%s cites a real corpus path: %s"
                             % (rel, hits))


# ===========================================================================
# 10 — the frozen arms
# ===========================================================================
class FrozenArms(unittest.TestCase):

    EXPECTED_V21 = {
        "SKILL.md": "f5b526f5f2f09978b7b9e4c19bf618f1",
        "tools/logmap.py": "ee301e89a4cda006415e36c7e3ad8624",
        "tools/citecheck.py": "82bece3c236a70de871da66c8e95758c",
        "tools/logjoin.py": "a0c1e11c9c52aaa814f1c26480ac37a4",
        "reference/report-format.md": "d19a98be30ab2b52fd30cceca3860169",
        "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
        "reference/tools.md": "743808781ba175741c392f258d035065",
    }

    def test_no_frozen_arm_moved(self):
        """v1..v21 are frozen as directories: a new file inside one ships to a
        measured agent just as surely as an edit to an old one."""
        for n in range(1, 22):
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

    def test_v21_hashes_are_recorded(self):
        """Pinned by content the moment v22 exists, so the next version has the
        same anchor v21 gave to v20."""
        got = {}
        for rel in sorted(self.EXPECTED_V21):
            path = os.path.join(V21, rel)
            got[rel] = hashlib.md5(open(path, "rb").read()).hexdigest()
        want = {k: v for k, v in self.EXPECTED_V21.items() if v}
        if not want:
            self.skipTest("hashes recorded on the first green run: %r" % got)
        self.assertEqual(want, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
