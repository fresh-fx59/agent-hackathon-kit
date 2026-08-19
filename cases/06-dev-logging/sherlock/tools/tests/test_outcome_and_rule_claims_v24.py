#!/usr/bin/env python3
"""v24 — a finding says whether the thing happened, and a bulk rule says what it claims.

    python3 tools/tests/test_outcome_and_rule_claims_v24.py
    SHERLOCK_SKILL=$PWD/skills/v24 python3 tools/tests/test_outcome_and_rule_claims_v24.py

TWO CEILINGS, MEASURED ON THE v22 CONVERGENCE ARMS
--------------------------------------------------
1.  **A finding could not state its own outcome.**  Across the eight arms, 12
    planted non-defects were written up as findings and 0 were written up as
    refuted.  Every one of them was cited under «улики» — the evidence-FOR
    field — because that is the only field the report format has.  A heading
    could say «успеха нет» in prose, but the format mandated no per-finding
    field that records it, and «чем опровергал» is a METHOD field present in
    every finding, so it cannot discriminate.  The consequence is that the
    report cannot tell «I found an intrusion» from «I checked this and it was
    nothing» — and that distinction is the headline metric of the field.

    The fix is a mandatory, closed, machine-parsable outcome on every finding:

        исход: успех     — действие достигло цели
        исход: попытка   — действие видно, и видно, что цели оно НЕ достигло
        исход: норма     — проверено и объяснено штатным поведением

    Three states, no fourth.  A vocabulary with an escape hatch for
    «подозрительно» measures nothing, so the grammar is a whole line and a
    qualifier on the same line is refused — that is where the fourth state
    always comes back.  The three compose upward onto the three verdicts, so a
    report whose findings are all `норма` cannot end in «скомпрометирована».

2.  **A receipt forced reading and did not force believing.**  v22 made bulk
    triage checkable: rules over a closed selector language, receipts chosen by
    the tool from each rule's own coverage, every quote verified.  On one arm it
    worked exactly as designed — 0 rows closed without support, 192 receipts all
    `ok` — and the score fell, because nothing in the pipeline compared a
    receipt's CONTENT against the rule's own claim.  A rule whose stated reason
    is prose («штатный резолвинг») closes rows whose receipts show the opposite,
    and the receipts verify, because they are quoted accurately.

    The fix: a rule's fourth column stops being prose and becomes a CLAIM in a
    closed language over measurements of the real line — read from the file, not
    from the worklist's excerpt column.  The tool evaluates it on every row the
    rule closes, and demands a receipt on the row that comes closest to breaking
    it, so raising the bound to make the rule pass forces the analyst to read
    and quote the very line the bound was raised for.

Synthetic throughout — every corpus, worklist and rules file here is built by
this file.  Nothing depends on a dataset.
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
V23 = os.path.join(SKILLS, "v23")
V24 = os.path.join(SKILLS, "v24")

UNDER_TEST = os.environ.get("SHERLOCK_SKILL", V24)


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
V22T = load_module(os.path.join(HERE, "test_triage_receipts_v22.py"),
                   "triage_receipts_v22")

TRIAGE = os.path.join(UNDER_TEST, "tools", "triagecheck.py")
CITE = os.path.join(UNDER_TEST, "tools", "citecheck.py")

CC = load_module(CITE, "citecheck_v24_under_test")
TC = load_module(TRIAGE, "triagecheck_v24_under_test")


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


# ===========================================================================
# PART 1 — the outcome of a finding
# ===========================================================================
NEEDLE_FILE = "edge-7/logs/warden.log"
NEEDLE_LINE = 12
NEEDLE = ("2031-04-09T11:22:33+00:00 warden[58]: harness=quill-spare rota "
          "handover accepted after four refusals")


def outcome_corpus(root):
    body = ["2031-04-09T11:%02d:00+00:00 warden[58]: sweep %d clean\n"
            % (i, i) for i in range(30)]
    body[NEEDLE_LINE - 1] = NEEDLE + "\n"
    V19T._write(os.path.join(root, NEEDLE_FILE), "".join(body))
    return root


def report_with(outcomes, verdict_text=None, n=None):
    """A report of len(outcomes) finding blocks, each with a verified citation.

    `outcomes[i]` is the text put on the outcome line, or None to omit it."""
    out = ["# отчёт", ""]
    for i, oc in enumerate(outcomes, 1):
        out.append("Н-%d · блок %d" % (i, i))
        out.append("что сломано: одна строка, которая ведёт себя не как соседи")
        out.append("улики: %s:%d «%s»" % (NEEDLE_FILE, NEEDLE_LINE, NEEDLE[-60:]))
        if oc is not None:
            out.append(oc)
        out.append("чем опровергал: пересчитал частоту вне окна")
        out.append("")
    if verdict_text:
        out.append("## ВЕРДИКТ")
        out.append(verdict_text)
    return "\n".join(out) + "\n"


class TheVocabularyIsClosedAndOrdered(unittest.TestCase):
    """Three states, named in the source, ordered so they can compose."""

    def test_exactly_three_outcomes_exist(self):
        self.assertEqual(("норма", "попытка", "успех"),
                         tuple(CC.OUTCOME_ORDER),
                         "the outcome vocabulary is not the three states")

    def test_each_outcome_maps_onto_exactly_one_verdict(self):
        self.assertEqual({"норма": "clean",
                          "попытка": "attacked-not-proven",
                          "успех": "compromised"},
                         dict(CC.OUTCOME_VERDICT))

    def test_no_two_outcome_words_contain_one_another(self):
        """A scorer regex must not be able to read one token inside another —
        that is how «не удался» would be scored as «удался»."""
        for a in CC.OUTCOME_ORDER:
            for b in CC.OUTCOME_ORDER:
                if a != b:
                    self.assertNotIn(a, b, "%r is a substring of %r" % (a, b))


class TheOutcomeLineHasOneGrammar(unittest.TestCase):

    def parse(self, line):
        d = CC.finding_outcomes(report_with([line]))
        return d[0]

    def test_the_plain_line_parses(self):
        for tok in CC.OUTCOME_ORDER:
            got = self.parse("исход: %s" % tok)
            self.assertEqual(tok, got["outcome"], got)
            self.assertIsNone(got["bad"])

    def test_formatting_around_the_token_is_tolerated(self):
        """Bold, a bullet, a trailing period — presentation, not qualification."""
        for line in ("исход: **успех**", "- исход: успех", "**исход:** успех",
                     "исход:успех", "  исход :  успех .", "ИСХОД: Успех"):
            self.assertEqual("успех", self.parse(line)["outcome"],
                             "%r did not parse" % line)

    def test_a_qualifier_on_the_same_line_is_refused(self):
        """The escape hatch always comes back as a trailing clause, so the line
        is the token and nothing else."""
        for line in ("исход: успех — но не доказан",
                     "исход: успех, вероятно",
                     "исход: попытка или успех",
                     "исход: скорее норма"):
            got = self.parse(line)
            self.assertIsNone(got["outcome"], "%r was accepted" % line)
            self.assertTrue(got["bad"], "%r was not named as invalid" % line)

    def test_a_fourth_state_is_refused_and_named(self):
        for word in ("подозрительно", "неясно", "требует проверки", "unknown"):
            got = self.parse("исход: %s" % word)
            self.assertIsNone(got["outcome"])
            self.assertIn(word, got["bad"], got)

    def test_a_missing_line_is_missing_not_invalid(self):
        got = CC.finding_outcomes(report_with([None]))[0]
        self.assertIsNone(got["outcome"])
        self.assertIsNone(got["bad"], "a missing outcome was reported as bad")

    def test_the_grammar_is_published_as_a_regex_a_scorer_can_import(self):
        self.assertTrue(hasattr(CC, "OUTCOME_LINE_RE"))
        m = CC.OUTCOME_LINE_RE.match("исход: попытка")
        self.assertTrue(m)
        self.assertEqual("попытка", m.group(1),
                         "group 1 of the published regex is not the token")

    def test_the_outcome_belongs_to_the_block_it_sits_in(self):
        d = CC.finding_outcomes(report_with(["исход: норма", "исход: успех"]))
        self.assertEqual([("1", "норма"), ("2", "успех")],
                         [(x["finding"], x["outcome"]) for x in d])


class AMissingOutcomeIsADeliveryDefect(unittest.TestCase):
    """The same class of failure as a missing verdict section: the ledger
    refuses to go green and says which finding."""

    def ledger(self, report, corpus, extra=()):
        return V19T.run_citecheck(UNDER_TEST, report, corpus,
                                  ("--require-quote",) + tuple(extra))

    def _bundle(self, tmp):
        corpus = os.path.join(tmp, "corpus")
        outcome_corpus(corpus)
        wl = os.path.join(tmp, "worklist.tsv")
        io.open(wl, "w", encoding="utf-8").write(
            "g0001\tD Н-1\trare\t%s:%d\tn=1\tзапись\n"
            % (NEEDLE_FILE, NEEDLE_LINE))
        return corpus, wl

    def test_a_finding_without_an_outcome_keeps_the_ledger_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, wl = self._bundle(tmp)
            rc, d, err = self.ledger(report_with([None]), corpus,
                                     ("--ledger", wl))
            self.assertNotEqual(0, rc, json.dumps(d, ensure_ascii=False)[:900])
            self.assertEqual(["1"], d["outcomes"]["missing"], d["outcomes"])

    def test_the_same_report_with_an_outcome_goes_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, wl = self._bundle(tmp)
            rc, d, err = self.ledger(report_with(["исход: успех"],
                                                 "скомпрометирована"),
                                     corpus, ("--ledger", wl))
            self.assertEqual(0, rc, json.dumps(d, ensure_ascii=False)[:900])
            self.assertEqual([], d["outcomes"]["missing"])

    def test_an_invalid_outcome_is_reported_separately_from_a_missing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, wl = self._bundle(tmp)
            rc, d, err = self.ledger(report_with(["исход: подозрительно"]),
                                     corpus, ("--ledger", wl))
            self.assertNotEqual(0, rc)
            self.assertEqual([], d["outcomes"]["missing"])
            self.assertEqual(1, len(d["outcomes"]["invalid"]), d["outcomes"])

    def test_the_ledger_prints_the_count_and_the_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, wl = self._bundle(tmp)
            path = os.path.join(tmp, "report.md")
            io.open(path, "w", encoding="utf-8").write(report_with([None]))
            p = subprocess.run(
                [sys.executable, CITE, path, "--corpus", corpus,
                 "--require-quote", "--ledger", wl],
                capture_output=True, text=True)
            self.assertIn("исход", p.stdout)
            for tok in CC.OUTCOME_ORDER:
                self.assertIn(tok, p.stdout, p.stdout)
            self.assertNotEqual(0, p.returncode)

    def test_a_report_with_no_findings_at_all_is_not_penalised_here(self):
        """The finding-block rule is already someone else's check; this one
        only speaks about blocks that exist."""
        d = CC.finding_outcomes("# отчёт\nничего не найдено\n")
        self.assertEqual([], d)


class TheOutcomesComposeOntoTheVerdict(unittest.TestCase):

    def implied(self, outcomes):
        return CC.implied_verdict(
            report_with(["исход: %s" % o for o in outcomes]))

    def test_one_success_makes_the_report_a_compromise(self):
        self.assertEqual("compromised", self.implied(["норма", "успех", "норма"]))

    def test_attempts_without_a_success_stop_at_the_middle_verdict(self):
        self.assertEqual("attacked-not-proven",
                         self.implied(["норма", "попытка", "норма"]))

    def test_all_benign_is_clean(self):
        self.assertEqual("clean", self.implied(["норма", "норма"]))

    def test_no_outcomes_implies_nothing(self):
        self.assertIsNone(CC.implied_verdict("# отчёт\nпусто\n"))

    def test_a_benign_report_that_claims_a_compromise_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            outcome_corpus(corpus)
            rc, d, err = V19T.run_citecheck(
                UNDER_TEST,
                report_with(["исход: норма", "исход: норма"],
                            "**скомпрометирована** — доступ получен"),
                corpus, ("--require-quote",))
            self.assertNotEqual(0, rc)
            self.assertTrue(d["outcomes"]["contradiction"], d["outcomes"])
            self.assertEqual("clean", d["outcomes"]["implied"])
            self.assertEqual("compromised", d["outcomes"]["stated"])

    def test_an_agreeing_report_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            outcome_corpus(corpus)
            rc, d, err = V19T.run_citecheck(
                UNDER_TEST,
                report_with(["исход: норма", "исход: попытка"],
                            "атаковали, но не доказано"),
                corpus, ("--require-quote",))
            self.assertFalse(d["outcomes"]["contradiction"], d["outcomes"])
            self.assertEqual(0, rc, json.dumps(d, ensure_ascii=False)[:600])

    def test_no_verdict_section_means_no_contradiction(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            outcome_corpus(corpus)
            rc, d, err = V19T.run_citecheck(
                UNDER_TEST, report_with(["исход: норма"]), corpus,
                ("--require-quote",))
            self.assertIsNone(d["outcomes"]["stated"])
            self.assertFalse(d["outcomes"]["contradiction"])
            self.assertEqual(0, rc)


class TheHandOverCarriesTheOutcomesToo(unittest.TestCase):
    """v23's lesson: what you deliver is what you checked. A condensed
    hand-over that drops the outcome lines has dropped the answer."""

    def test_a_delivery_without_outcomes_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            outcome_corpus(corpus)
            report = os.path.join(tmp, "report.md")
            handover = os.path.join(tmp, "handover.md")
            io.open(report, "w", encoding="utf-8").write(
                report_with(["исход: успех"], "скомпрометирована"))
            io.open(handover, "w", encoding="utf-8").write(
                report_with([None], "скомпрометирована"))
            p = subprocess.run(
                [sys.executable, CITE, report, "--corpus", corpus,
                 "--require-quote", "--delivered", handover, "--json"],
                capture_output=True, text=True)
            self.assertNotEqual(0, p.returncode, p.stdout[:600])
            d = json.loads(p.stdout)
            self.assertEqual(["1"], d["delivered"]["outcomes"]["missing"])

    def test_the_verbatim_delivery_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            outcome_corpus(corpus)
            report = os.path.join(tmp, "report.md")
            io.open(report, "w", encoding="utf-8").write(
                report_with(["исход: успех"], "скомпрометирована"))
            p = subprocess.run(
                [sys.executable, CITE, report, "--corpus", corpus,
                 "--require-quote", "--delivered", report],
                capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stdout[-900:])


class TheSkillStatesTheRule(unittest.TestCase):
    """v13's lesson: a rule the skill text does not state is a rule the model
    cannot follow. It has to be in SKILL.md AND in the report format."""

    def _read(self, rel):
        with io.open(os.path.join(UNDER_TEST, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_the_report_format_mandates_the_field(self):
        body = self._read(os.path.join("reference", "report-format.md"))
        self.assertIn("исход:", body)
        for tok in CC.OUTCOME_ORDER:
            self.assertIn(tok, body, "the format never names %r" % tok)

    def test_skill_md_states_it_too(self):
        body = self._read("SKILL.md")
        self.assertIn("исход:", body)
        for tok in CC.OUTCOME_ORDER:
            self.assertIn(tok, body, "SKILL.md never names %r" % tok)

    def test_the_stopping_condition_includes_it(self):
        body = self._read("SKILL.md")
        stop = body[body.index("## 8."):body.index("## 9.")]
        self.assertIn("исход", stop,
                      "the stopping condition does not mention the outcome")

    def test_the_grammar_is_written_where_a_scorer_author_will_find_it(self):
        body = self._read(os.path.join("reference", "tools.md"))
        self.assertIn("исход:", body)
        self.assertIn("успех|попытка|норма", body.replace(" ", ""),
                      "the closed alternation is not written down")


# ===========================================================================
# PART 2 — a rule states a claim, and the claim is checked
# ===========================================================================
HOST = "node-11"
QUIET = "spool-%d.log"
LOUD = "resolver.log"

# A high-entropy label of the kind a bulk rule must not be able to call
# background. Invented here; it exists in no corpus.
BLOB = "Zq7mHRt4vKpLdWx9BnCyE2sUaJfTgQ6hMv3"
NOISY = ("2031-05-02T03:04:05+00:00 resolver[7]: query[A] %s.example.invalid "
         "from 10.0.0.9" % BLOB)
CALM = "2031-05-02T03:04:%02d+00:00 resolver[7]: query[A] mail.example.invalid from 10.0.0.9"


def claim_corpus(root, quiet_files=4, lines=40, noisy_at=(3, 9, 11)):
    for f in range(quiet_files):
        V19T._write(
            os.path.join(root, HOST, "logs", QUIET % f),
            "".join("2031-05-01T00:%02d:%02d+00:00 spool[%d]: slice %d ok 200\n"
                    % (i % 60, i % 60, 30 + f, i) for i in range(lines)))
    body = [CALM % (i % 60) + "\n" for i in range(lines)]
    for i in noisy_at:
        body[i] = NOISY + "\n"
    V19T._write(os.path.join(root, HOST, "logs", LOUD), "".join(body))
    return root


def claim_rows(quiet_files=4, per_file=6, noisy_at=(3, 9, 11)):
    rows, n = [], 1
    for f in range(quiet_files):
        for i in range(per_file):
            rows.append(["g%04d" % n, "?", "cat",
                         "%s/logs/%s:%d" % (HOST, QUIET % f, i + 1), "n=1",
                         "slice %d ok 200" % i])
            n += 1
    for i in range(12):
        rows.append(["g%04d" % n, "?", "rare",
                     "%s/logs/%s:%d" % (HOST, LOUD, i + 1), "n=1",
                     "query[A] … from 10.0.0.9"])
        n += 1
    return rows


class ClaimBundle(object):
    def __init__(self, tmp, rows=None):
        self.tmp = tmp
        self.corpus = os.path.join(tmp, "corpus")
        claim_corpus(self.corpus)
        self.rows = claim_rows() if rows is None else rows
        self.worklist = os.path.join(tmp, "worklist.tsv")
        self.rules = os.path.join(tmp, "rules.tsv")
        self.save()

    def save(self):
        V22T.write_worklist(self.worklist, self.rows)

    def write_rules(self, text):
        with io.open(self.rules, "w", encoding="utf-8") as fh:
            fh.write(text)

    def run(self, with_rules=True, extra=()):
        self.save()
        return run_triage(self.worklist, self.rules if with_rules else None,
                          self.corpus, extra)

    def line_of(self, ref):
        path, n = ref.rsplit(":", 1)
        with io.open(os.path.join(self.corpus, path), encoding="utf-8") as fh:
            return fh.read().splitlines()[int(n) - 1]

    def close_all(self, verdict="N #R1 фон"):
        for r in self.rows:
            r[1] = verdict
        return self


class AProseReasonIsNoLongerARule(unittest.TestCase):
    """The A10 shape, rebuilt from nothing: a rule whose fourth column is a
    sentence. It closed 46 rows and the receipts it produced showed the
    opposite of what it said."""

    def test_a_rule_without_a_claim_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertEqual(1, d["totals"]["правил без утверждения"], out)

    def test_a_sentence_in_the_claim_column_is_refused_and_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tштатный резолвинг\tтак принято\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            problems = " ".join(p for r in d["rules"] for p in r["проблемы"])
            self.assertTrue(problems, out)
            self.assertEqual(1, d["totals"]["непроверяемых правил"], out)

    def test_the_claim_may_not_be_a_predicate_over_the_excerpt_column(self):
        """The v22 refusal still holds inside the new column: a claim about
        the projection is not a claim about the file."""
        for cond in ("запись~*ok*", "текст!~*query*", "grep=slice"):
            with tempfile.TemporaryDirectory() as tmp:
                b = ClaimBundle(tmp).close_all()
                b.write_rules("R1\tось=cat|rare\tN\t%s\tфон\n" % cond)
                rc, d, out, _e = b.run()
                self.assertNotEqual(0, rc, "%r passed as a claim" % cond)

    def test_a_selector_field_is_not_a_claim(self):
        """`файл~*.log` is how you CHOOSE the rows; it asserts nothing about
        what is in them, so it is refused with its own message."""
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tфайл~*.log\tфон\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            problems = " ".join(p for r in d["rules"] for p in r["проблемы"])
            self.assertIn("утвержд", problems, problems)


class TheClaimIsCheckedAgainstTheRealLines(unittest.TestCase):

    def test_a_claim_that_holds_everywhere_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp)
            for r in b.rows:
                r[1] = "N #R1 фон" if r[2] == "cat" else "D Н-1"
            b.write_rules("R1\tось=cat\tN\tтокен<=24\tодна форма записи\n")
            rc, d, out, _e = b.run()
            self.assertEqual([], d["rules"][0]["проблемы"], out)
            self.assertEqual(0, d["totals"]["нарушений утверждения"], out)

    def test_the_claim_is_evaluated_on_every_covered_row_not_only_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tтокен<=24\tвсё это фон\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertEqual(3, d["totals"]["нарушений утверждения"], out)
            self.assertEqual(len(b.rows), d["rules"][0]["строк проверено"], out)

    def test_the_violation_names_the_row_the_measurement_and_the_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tтокен<=24\tвсё это фон\n")
            rc, d, out, _e = b.run()
            v = d["rules"][0]["нарушения"][0]
            self.assertIn("id", v)
            self.assertEqual("токен", v["поле"])
            self.assertEqual(len(BLOB), v["измерено"], v)
            self.assertEqual("24", v["граница"], v)

    def test_a_bound_that_admits_the_outlier_passes_and_prints_the_measurement(self):
        """Raising the bound is legal. It is also a written-down, falsifiable
        claim, and the measured extreme is printed next to it."""
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tтокен<=%d\tвсё это фон\n"
                          % len(BLOB))
            rc, d, out, _e = b.run()
            self.assertEqual(0, d["totals"]["нарушений утверждения"], out)
            self.assertEqual(len(BLOB), d["rules"][0]["измерено"]["токен"], out)

    def test_a_code_claim_reads_the_codes_off_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp)
            for r in b.rows:
                r[1] = "N #R1 фон" if r[2] == "cat" else "D Н-1"
            b.write_rules("R1\tось=cat\tN\tкод=200\tвсегда успешный ответ\n")
            rc, d, out, _e = b.run()
            self.assertEqual(0, d["totals"]["нарушений утверждения"], out)
            b.write_rules("R1\tось=cat\tN\tкод=404\tвсегда 404\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertTrue(d["totals"]["нарушений утверждения"] > 0, out)

    def test_an_address_claim_reads_the_addresses_off_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp)
            for r in b.rows:
                r[1] = "N #R1 фон" if r[2] == "rare" else "D Н-1"
            b.write_rules("R1\tось=rare\tN\tадрес~10.*\tтолько свои адреса\n")
            rc, d, out, _e = b.run()
            self.assertEqual(0, d["totals"]["нарушений утверждения"], out)
            b.write_rules("R1\tось=rare\tN\tадрес~192.168.*\tтолько свои\n")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)

    def test_terms_combine_with_the_same_operator_as_the_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tтокен<=%d && адрес~10.*\tфон\n"
                          % len(BLOB))
            rc, d, out, _e = b.run()
            self.assertEqual([], d["rules"][0]["проблемы"], out)
            self.assertEqual(0, d["totals"]["нарушений утверждения"], out)

    def test_the_measurement_functions_are_pure_and_importable(self):
        m = TC.measure_line(NOISY)
        self.assertEqual(len(BLOB), m["токен"])
        self.assertEqual(["10.0.0.9"], m["адрес"])
        self.assertEqual([], m["код"])
        m2 = TC.measure_line("GET /x 200 6202 from 10.143.2.91")
        self.assertEqual([200], m2["код"],
                         "a dotted-quad octet was read as a result code")


class TheBoundaryRowMustBeReceipted(unittest.TestCase):
    """Raising the bound to make a rule pass forces the analyst to read and
    quote the line the bound was raised for."""

    def _wide(self, tmp):
        b = ClaimBundle(tmp).close_all()
        b.write_rules("R1\tось=cat|rare\tN\tтокен<=%d\tвсё это фон\n"
                      % len(BLOB))
        return b

    def test_the_row_holding_the_extreme_is_demanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            worst = d["rules"][0]["граничные"]
            self.assertTrue(worst, d["rules"][0])
            self.assertTrue(set(worst) <= set(d["rules"][0]["нужны"]),
                            "a boundary row is not in the demanded set")
            byid = {r[0]: r for r in b.rows}
            self.assertEqual(len(BLOB),
                             TC.measure_line(b.line_of(byid[worst[0]][3]))["токен"])

    def test_the_boundary_row_is_demanded_on_top_of_the_v22_sample(self):
        """Excursion-first stays exactly as v22 measured it; the boundary is
        additional, so nothing v22 guaranteed is displaced."""
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            r = d["rules"][0]
            self.assertGreaterEqual(len(r["нужны"]), r["нужно квитанций"])

    def test_the_rule_is_not_closed_until_the_boundary_row_is_quoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            worst = set(d["rules"][0]["граничные"])
            byid = {r[0]: r for r in b.rows}
            lines = ["R1\tось=cat|rare\tN\tтокен<=%d\tвсё это фон" % len(BLOB)]
            for rid in d["rules"][0]["нужны"]:
                if rid in worst:
                    continue
                row = byid[rid]
                lines.append("+R1\t%s\t%s\t«%s»"
                             % (rid, row[3], b.line_of(row[3])[-60:]))
            b.write_rules("\n".join(lines) + "\n")
            rc, d2, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertEqual(len(worst), d2["totals"]["нехватка квитанций"], out)

    def test_all_demanded_receipts_close_the_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._wide(tmp)
            d = b.run()[1]
            byid = {r[0]: r for r in b.rows}
            lines = ["R1\tось=cat|rare\tN\tтокен<=%d\tвсё это фон" % len(BLOB)]
            for rid in d["rules"][0]["нужны"]:
                row = byid[rid]
                lines.append("+R1\t%s\t%s\t«%s»"
                             % (rid, row[3], b.line_of(row[3])[-60:]))
            b.write_rules("\n".join(lines) + "\n")
            rc, d2, out, _e = b.run()
            self.assertEqual(0, rc, out)


class TheStrongestExcursionsCannotBeClosedByRule(unittest.TestCase):
    """`new` («a participant that was not here in the first half») and `peak`
    («a measurement that went up threefold and came back») are the two axes
    Step 1 states as a claim about time, not about rarity. They are rare
    enough on a real corpus that requiring a name costs a handful of rows."""

    def _with_strong(self, tmp, axis):
        rows = claim_rows()
        rows[0][2] = axis
        b = ClaimBundle(tmp, rows)
        b.close_all()
        b.write_rules("R1\tось=cat|rare|%s\tN\tтокен<=%d\tфон\n"
                      % (axis, len(BLOB)))
        return b

    def test_a_new_row_under_a_rule_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._with_strong(tmp, "new")
            rc, d, out, _e = b.run()
            self.assertNotEqual(0, rc, out)
            self.assertEqual(1, d["totals"]["сильных экскурсий под правилом"], out)
            self.assertEqual(["g0001"], [x["id"] for x in d["strong"]], out)

    def test_a_peak_row_under_a_rule_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._with_strong(tmp, "peak")
            rc, d, out, _e = b.run()
            self.assertEqual(1, d["totals"]["сильных экскурсий под правилом"], out)

    def test_a_rare_row_under_a_rule_is_still_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._with_strong(tmp, "rare")
            rc, d, out, _e = b.run()
            self.assertEqual(0, d["totals"]["сильных экскурсий под правилом"], out)

    def test_the_same_row_closed_by_name_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._with_strong(tmp, "new")
            b.rows[0][1] = "N %s «%s»" % (b.rows[0][3],
                                          b.line_of(b.rows[0][3])[-50:])
            rc, d, out, _e = b.run()
            self.assertEqual(0, d["totals"]["сильных экскурсий под правилом"], out)

    def test_a_defect_row_is_also_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._with_strong(tmp, "peak")
            b.rows[0][1] = "D Н-3"
            rc, d, out, _e = b.run()
            self.assertEqual(0, d["totals"]["сильных экскурсий под правилом"], out)

    def test_the_two_axes_are_named_in_the_source_and_are_a_subset_of_excursions(self):
        self.assertEqual(("new", "peak"), tuple(TC.STRONG))
        self.assertTrue(set(TC.STRONG) < set(TC.EXCURSION))


class TheSummaryShowsTheClaim(unittest.TestCase):

    def test_the_rendered_output_prints_the_claim_and_the_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ClaimBundle(tmp).close_all()
            b.write_rules("R1\tось=cat|rare\tN\tтокен<=24\tвсё это фон\n")
            b.save()
            p = subprocess.run(
                [sys.executable, TRIAGE, "--worklist", b.worklist,
                 "--rules", b.rules, "--corpus", b.corpus],
                capture_output=True, text=True)
            self.assertIn("утверждение", p.stdout)
            self.assertIn("токен<=24", p.stdout)
            self.assertIn(str(len(BLOB)), p.stdout, p.stdout)
            self.assertNotEqual(0, p.returncode)


class TheSkillStatesTheClaimRule(unittest.TestCase):

    def _read(self, rel):
        with io.open(os.path.join(UNDER_TEST, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_the_claim_fields_are_documented(self):
        docs = self._read("SKILL.md") + self._read(
            os.path.join("reference", "tools.md"))
        for field in TC.CLAIM_FIELDS:
            self.assertIn(field, docs, "claim field %r is undocumented" % field)

    def test_the_rules_format_shows_five_columns(self):
        body = self._read(os.path.join("reference", "tools.md"))
        self.assertIn("утверждение", body)

    def test_skill_md_says_a_prose_reason_is_not_a_rule(self):
        body = self._read("SKILL.md")
        self.assertIn("утверждение", body)

    def test_skill_md_states_the_strong_excursion_restriction(self):
        body = self._read("SKILL.md")
        section = body[body.index("### Массовое закрытие"):body.index("## 6.")]
        for axis in TC.STRONG:
            self.assertIn(axis, section,
                          "the restriction never names %r" % axis)


# ===========================================================================
# PART 3 — v24 is v23 plus these two changes, and nothing else
# ===========================================================================
class V24IsV23PlusTwoChanges(unittest.TestCase):

    def setUp(self):
        if os.path.abspath(UNDER_TEST) != os.path.abspath(V24):
            self.skipTest("only meaningful for v24")

    def test_step_one_is_untouched(self):
        for name in ("logmap.py", "logjoin.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(V23, "tools", name),
                            os.path.join(V24, "tools", name), shallow=False),
                "%s moved — v24 changes the report and the rules, not Step 1"
                % name)

    def test_the_ranked_artefacts_are_byte_identical_on_a_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "corpus")
            V21T.make_bundle(corpus, 4)
            outs = {}
            for tag, skill in (("v23", V23), ("v24", V24)):
                outs[tag] = os.path.join(tmp, tag)
                p = V19T.run_logmap(skill, corpus, outs[tag])
                self.assertEqual(0, p.returncode, p.stderr)
            names = sorted(os.listdir(outs["v23"]))
            self.assertEqual(names, sorted(os.listdir(outs["v24"])))
            moved = [fn for fn in names
                     if not filecmp.cmp(os.path.join(outs["v23"], fn),
                                        os.path.join(outs["v24"], fn),
                                        shallow=False)]
            self.assertEqual([], moved, "Step 1 output moved: %s" % moved)

    def test_no_file_is_added_or_removed(self):
        a = {os.path.relpath(os.path.join(dp, fn), V23)
             for dp, dn, fns in os.walk(V23) for fn in fns
             if "__pycache__" not in dp}
        bset = {os.path.relpath(os.path.join(dp, fn), V24)
                for dp, dn, fns in os.walk(V24) for fn in fns
                if "__pycache__" not in dp}
        self.assertEqual(set(), bset - a)
        self.assertEqual(set(), a - bset)


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

    EXPECTED_V23 = {
        "SKILL.md": "b11a0c09f15adccc0a9809897e4cd4bc",
        "tools/logmap.py": "b7f292211177bfe2975c01fb74ff8495",
        "tools/citecheck.py": "90e11b5914b7be1608ebdfad236cd661",
        "tools/logjoin.py": "a0c1e11c9c52aaa814f1c26480ac37a4",
        "tools/triagecheck.py": "43b6420b708c4ec637e61a47fd52684f",
        "reference/report-format.md": "d19a98be30ab2b52fd30cceca3860169",
        "reference/code-and-spec.md": "70425eda47ac75b7c526ec8ca34340f5",
        "reference/tools.md": "11c62da8d36d2bc0a13beeafda3554d6",
    }

    def test_no_frozen_arm_moved(self):
        for n in range(1, 24):
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

    def test_v23_hashes_are_recorded(self):
        got = {}
        for rel in sorted(self.EXPECTED_V23):
            path = os.path.join(V23, rel)
            got[rel] = hashlib.md5(open(path, "rb").read()).hexdigest()
        want = {k: v for k, v in self.EXPECTED_V23.items() if v}
        if not want:
            self.skipTest("hashes recorded on the first green run: %r" % got)
        self.assertEqual(want, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
