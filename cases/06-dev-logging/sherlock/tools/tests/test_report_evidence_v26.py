#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
V26 = SHERLOCK / "skills" / "v26"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CITE = load("citecheck_v26_report_evidence", V26 / "tools" / "citecheck.py")
TRIAGE = load("triagecheck_v26_receipts", V26 / "tools" / "triagecheck.py")


NEEDLE = "2036-02-03T04:05:06Z type=SERVICE_START component=demo unit=put code=200"
CONTROL = "2036-02-03T04:06:00Z component=demo state=quiet code=200"


def corpus(root):
    host = Path(root) / "host"
    host.mkdir(parents=True, exist_ok=True)
    (host / "app.log").write_text(NEEDLE + "\n" + CONTROL + "\n", encoding="utf-8")
    (host / "empty.log").write_text("", encoding="utf-8")
    (host / "nonempty.log").write_text("not empty\n", encoding="utf-8")
    (host / "bin.dat").write_bytes(b"binary\x00payload")
    (host / "bad.gz").write_bytes(b"not a gzip stream")
    (host / "old.log").write_text("old but present\n", encoding="utf-8")
    for branch in ("a", "b"):
        p = Path(root) / branch / "same.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("same name\n", encoding="utf-8")


def report(outcome="успех", attribution="не установлена", rejected=True,
           coverage=True):
    parts = [
        "# Отчёт",
        "## Находки",
        "### Н-1 · проверяемое наблюдение",
        "что сломано: проверка держит адрес.",
        "улики: host/app.log:1 «%s»" % NEEDLE,
        "чем опровергал: host/app.log:2 «%s»" % CONTROL,
        "атрибуция: %s" % attribution,
        "исход: %s" % outcome,
    ]
    if rejected:
        parts += [
            "## Отклонённые кандидаты",
            "### К-1 · штатный фон",
            "что выглядело как причина: похожий запуск.",
            "улики: host/app.log:2 «%s»" % CONTROL,
            "исход: норма",
        ]
    if coverage:
        parts += [
            "## Покрытие",
            "| path | status | detail |",
            "| --- | --- | --- |",
            "| host/app.log | наблюдение | host/app.log:1 «%s» |" % NEEDLE,
            "| host/empty.log | пусто | байт=0 |",
        ]
    return "\n".join(parts) + "\n"


def evidence(text, root):
    checked = CITE.check(text, root, require_quote=True)
    return CITE.report_evidence(text, checked)


class ReportEvidenceV26(unittest.TestCase):
    def test_full_v26_report_passes_outcome_evidence_and_cli(self):
        text = report()
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            rp = Path(td) / "report.md"
            rp.write_text(text, encoding="utf-8")
            self.assertEqual(CITE.outcomes_of(text)["blocking"], 0)
            self.assertEqual(evidence(text, td)["blocking"], 0)
            old_argv = sys.argv[:]
            try:
                sys.argv = ["citecheck.py", str(rp), "--corpus", td,
                            "--require-quote"]
                with contextlib.redirect_stdout(io.StringIO()):
                    code = CITE.main()
                self.assertEqual(code, 0)
            finally:
                sys.argv = old_argv

    def test_unattributed_observation_stays_a_finding(self):
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(report(attribution="не установлена"), td)
            self.assertEqual(ev["attribution"]["missing"], [])
            self.assertEqual(ev["attribution"]["invalid"], [])
            self.assertEqual(ev["blocking"], 0)

    def test_rejected_candidate_needs_one_outcome_and_quoted_citation(self):
        bad = report().replace("исход: норма\n## Покрытие", "## Покрытие")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(bad, td)
            self.assertEqual(ev["rejected"]["missing_outcome"], ["К-1"])
            self.assertGreater(ev["blocking"], 0)

        uncited = report().replace("улики: host/app.log:2 «%s»" % CONTROL,
                                   "улики: просто слова")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(uncited, td)
            self.assertEqual(ev["rejected"]["missing_citation"], ["К-1"])
            self.assertGreater(ev["blocking"], 0)

    def test_finding_and_candidate_outcomes_are_exactly_one_closed_line(self):
        two = report().replace("исход: успех", "исход: успех\nисход: норма", 1)
        outcomes = CITE.outcomes_of(two)
        self.assertEqual(outcomes["invalid"][0]["finding"], "1")
        self.assertGreater(outcomes["blocking"], 0)

    def test_coverage_observation_requires_verified_quote(self):
        bad = report().replace("| host/app.log | наблюдение | host/app.log:1 «%s» |" % NEEDLE,
                               "| host/app.log | наблюдение | host/app.log:1 «not this line» |")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(bad, td)
            self.assertIn(17, ev["coverage"]["invalid_citation"])
            self.assertGreater(ev["blocking"], 0)

    def test_no_address_access_row_cannot_smuggle_content_claim(self):
        ok = report()
        smuggled = ok.replace("байт=0",
                              "содержимое было обычным, ничего тревожного")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            self.assertEqual(evidence(ok, td)["blocking"], 0)
            ev = evidence(smuggled, td)
            self.assertEqual(ev["coverage"]["invalid_no_address_detail"], [18])
            self.assertGreater(ev["blocking"], 0)

    def test_no_address_detail_rejects_every_known_substring_laundering_shape(self):
        cases = (
            ("host/empty.log", "пусто",
             "содержимое было обычным; байт=0; ничего тревожного"),
            ("host/bin.dat", "двоичный", "внутри всё чисто; формат=двоичный"),
            ("host/bad.gz", "нечитабельно", "нет атак; ошибка=EACCES"),
            ("host/old.log", "не смотрел", "чистый файл; причина=лимит"),
        )
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            for path, status, detail in cases:
                text = report().replace(
                    "| host/empty.log | пусто | байт=0 |",
                    "| %s | %s | %s |" % (path, status, detail))
                ev = evidence(text, td)
                self.assertEqual(ev["coverage"]["invalid_no_address_detail"], [18],
                                 detail)
                self.assertGreater(ev["blocking"], 0, detail)

    def test_no_address_access_details_accept_closed_grammar_for_each_status(self):
        text = report().replace(
            "| host/empty.log | пусто | байт=0 |",
            "| host/empty.log | пусто | байт=0 |\n"
            "| host/bin.dat | двоичный | формат=двоичный |\n"
            "| host/bad.gz | нечитабельно | ошибка=truncated-gzip |\n"
            "| host/old.log | не смотрел | причина=лимит |")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(text, td)
            self.assertEqual(ev["coverage"]["invalid_no_address_detail"], [])
            self.assertEqual(ev["blocking"], 0)

    def test_coverage_observation_must_cite_its_own_resolved_path(self):
        wrong_row = report().replace(
            "| host/empty.log | пусто | байт=0 |",
            "| host/empty.log | наблюдение | host/app.log:1 «%s» |" % NEEDLE)
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(wrong_row, td)
            self.assertEqual(ev["coverage"]["mismatched_citation"], [18])
            self.assertIn("указывает не на файл", CITE.render_report_evidence(ev))
            self.assertGreater(ev["blocking"], 0)

    def test_unique_basename_observation_accepts_canonical_citation(self):
        row = report().replace(
            "| host/app.log | наблюдение | host/app.log:1 «%s» |" % NEEDLE,
            "| app.log | наблюдение | host/app.log:1 «%s» |" % NEEDLE)
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(row, td)
            self.assertEqual(ev["coverage"]["mismatched_citation"], [])
            self.assertEqual(ev["blocking"], 0)

    def test_coverage_paths_are_real_unique_and_safe(self):
        cases = (
            ("../host/empty.log", "traversal_path"),
            ("same.log", "ambiguous_path"),
            ("host/missing.log", "missing_path"),
        )
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            for path, key in cases:
                text = report().replace("host/empty.log", path)
                ev = evidence(text, td)
                got = ev["coverage"][key]
                self.assertTrue(got, "%s did not reject %s: %r" % (key, path, ev))
                self.assertGreater(ev["blocking"], 0)

    def test_no_address_claims_match_the_file_mechanics(self):
        cases = (
            ("host/nonempty.log", "пусто", "байт=0", "false_empty"),
            ("host/app.log", "двоичный", "формат=двоичный", "false_binary"),
        )
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            for path, status, detail, key in cases:
                text = report().replace(
                    "| host/empty.log | пусто | байт=0 |",
                    "| %s | %s | %s |" % (path, status, detail))
                ev = evidence(text, td)
                self.assertEqual(ev["coverage"][key], [18])
                self.assertGreater(ev["blocking"], 0)

    def test_duplicate_coverage_paths_fail_closed(self):
        duplicate = report().replace(
            "| host/empty.log | пусто | байт=0 |",
            "| host/empty.log | пусто | байт=0 |\n"
            "| ./host/empty.log | пусто | байт=0 |")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(duplicate, td)
            self.assertEqual(ev["coverage"]["duplicate_paths"],
                             [{"path": "host/empty.log", "lines": [18, 19]}])
            self.assertGreater(ev["blocking"], 0)

    def test_unsupported_coverage_status_fails_closed(self):
        bad = report().replace("| host/empty.log | пусто |",
                               "| host/empty.log | штатный фон |")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(bad, td)
            self.assertEqual(ev["coverage"]["unsupported_no_address"], [18])
            self.assertGreater(ev["blocking"], 0)

    def test_rejected_candidates_section_is_mandatory_and_nonempty(self):
        absent = report(rejected=False)
        empty = absent.replace("## Покрытие", "## Отклонённые кандидаты\n\n## Покрытие")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(absent, td)
            self.assertTrue(ev["rejected"]["missing_section"])
            self.assertGreater(ev["blocking"], 0)
            ev = evidence(empty, td)
            self.assertTrue(ev["rejected"]["empty_section"])
            self.assertGreater(ev["blocking"], 0)

    def test_coverage_section_is_mandatory_and_nonempty(self):
        absent = report(coverage=False)
        empty = absent + "## Покрытие\n| path | status | detail |\n| --- | --- | --- |\n"
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(absent, td)
            self.assertTrue(ev["coverage"]["missing_section"])
            self.assertGreater(ev["blocking"], 0)
            ev = evidence(empty, td)
            self.assertTrue(ev["coverage"]["empty_section"])
            self.assertGreater(ev["blocking"], 0)

    def test_duplicate_candidate_ids_fail_closed(self):
        dup = report().replace(
            "## Покрытие",
            "### К-1 · второй такой же номер\n"
            "улики: host/app.log:2 «%s»\n"
            "исход: норма\n"
            "## Покрытие" % CONTROL)
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(dup, td)
            self.assertEqual(ev["rejected"]["duplicate_ids"], ["К-1"])
            self.assertGreater(ev["blocking"], 0)

    def test_duplicate_finding_ids_fail_closed(self):
        duplicate = report().replace(
            "## Отклонённые кандидаты",
            "### Н-1 · повторный номер\n"
            "улики: host/app.log:2 «%s»\n"
            "атрибуция: установлена\n"
            "исход: норма\n"
            "## Отклонённые кандидаты" % CONTROL)
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            ev = evidence(duplicate, td)
            self.assertEqual(ev["attribution"]["duplicate_ids"], ["Н-1"])
            self.assertIn("номера находок", CITE.render_report_evidence(ev))
            self.assertGreater(ev["blocking"], 0)

    def test_fenced_examples_do_not_satisfy_or_create_report_structure(self):
        fenced_rejected = report(rejected=False).replace(
            "## Покрытие",
            "```\n## Отклонённые кандидаты\n### К-1 · пример\n"
            "исход: норма\n```\n## Покрытие")
        fenced_coverage = report(coverage=False) + (
            "```\n## Покрытие\n| path | status | detail |\n"
            "| host/empty.log | пусто | байт=0 |\n```\n")
        fenced_blocks = report().replace(
            "## Покрытие",
            "```\n### Н-1 · пример находки\nатрибуция: установлена\n"
            "исход: норма\n### К-1 · пример кандидата\n"
            "исход: норма\n```\n## Покрытие")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            self.assertTrue(evidence(fenced_rejected, td)["rejected"]["missing_section"])
            self.assertTrue(evidence(fenced_coverage, td)["coverage"]["missing_section"])
            ev = evidence(fenced_blocks, td)
            self.assertEqual(ev["attribution"]["duplicate_ids"], [])
            self.assertEqual(ev["rejected"]["duplicate_ids"], [])
            self.assertEqual(CITE.finding_blocks("```\n### Н-9 · sample\n```\n"), [])
            self.assertEqual(ev["blocking"], 0)

    def test_cli_text_and_json_expose_report_evidence_and_fail(self):
        bad = report().replace("атрибуция: не установлена\n", "")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            rp = Path(td) / "report.md"
            rp.write_text(bad, encoding="utf-8")
            old_argv = sys.argv[:]
            try:
                sys.argv = ["citecheck.py", str(rp), "--corpus", td,
                            "--require-quote"]
                text_out = io.StringIO()
                with contextlib.redirect_stdout(text_out):
                    code = CITE.main()
                self.assertEqual(code, 1)
                self.assertIn("ОТЧЁТНЫЕ НАБЛЮДЕНИЯ v26", text_out.getvalue())

                sys.argv = ["citecheck.py", str(rp), "--corpus", td,
                            "--require-quote", "--json"]
                json_out = io.StringIO()
                with contextlib.redirect_stdout(json_out):
                    code = CITE.main()
                self.assertEqual(code, 1)
                payload = json.loads(json_out.getvalue())
                self.assertGreater(payload["report_evidence"]["blocking"], 0)
                self.assertEqual(payload["report_evidence"]["attribution"]["missing"], ["1"])
            finally:
                sys.argv = old_argv

    def test_delivered_report_evidence_blocks_delivery(self):
        draft = report()
        handed = draft.replace("атрибуция: не установлена\n", "")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            checked = CITE.check(draft, td, require_quote=True)
            delivered = CITE.check(handed, td, require_quote=True)
            delivered["path"] = "handover.md"
            delivered["outcomes"] = CITE.outcomes_of(handed)
            delivered["report_evidence"] = CITE.report_evidence(handed, delivered)
            delivered["not_in_checked"] = CITE.not_in_checked(checked, delivered)
            self.assertTrue(CITE.delivery_failed(delivered))
            self.assertIn("ОТЧЁТНЫЕ НАБЛЮДЕНИЯ v26",
                          CITE.render_delivery(delivered))


class ReceiptDecisionV26(unittest.TestCase):
    def write_work(self, td, decision="правило", include_decision=True,
                   quote=NEEDLE, ref="host/app.log:1"):
        corpus(td)
        work = Path(td) / "worklist-host.tsv"
        work.write_text("g0001\tN #R1 фон\trare\thost/app.log:1\tn=1\t%s\n" % NEEDLE,
                        encoding="utf-8")
        cols = ["+R1", "g0001", ref, "«%s»" % quote]
        if include_decision:
            cols.append(decision)
        rules = Path(td) / "rules.tsv"
        rules.write_text(
            "R1\tid=g0001\tN\tтокен>=1\tдоменная причина без парсинга\n"
            + "\t".join(cols) + "\n",
            encoding="utf-8")
        return str(work), str(rules)

    def write_many_work(self, td, rule="n>=1", receipt_lines=()):
        lines = [
            "2036-02-03T04:05:%02dZ type=SERVICE_START component=demo unit=put code=200 seq=%d"
            % (i, i) for i in range(1, 7)
        ]
        p = Path(td) / "host" / "app.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        work = Path(td) / "worklist-host.tsv"
        work.write_text("".join(
            "g%04d\tN #R1 фон\tcat\thost/app.log:%d\tn=1\t%s\n" % (i, i, lines[i - 1])
            for i in range(1, 7)), encoding="utf-8")
        rules = Path(td) / "rules.tsv"
        rules.write_text(
            "R1\t%s\tN\tтокен>=1\tдоменная причина без парсинга\n" % rule
            + "".join(receipt_lines), encoding="utf-8")
        return str(work), str(rules), lines

    def test_rule_receipt_exposes_boundary_key_value_fields(self):
        with tempfile.TemporaryDirectory() as td:
            work, rules = self.write_work(td)
            d = TRIAGE.analyse(work, rules, td)
            fields = d["rules"][0]["квитанции-поля"][0]["fields"]
            self.assertIn({"key": "type", "value": "SERVICE_START"}, fields)
            self.assertIn({"key": "unit", "value": "put"}, fields)
            self.assertEqual(d["blocking"], 0)

    def test_receipt_missing_decision_fails(self):
        with tempfile.TemporaryDirectory() as td:
            work, rules = self.write_work(td, include_decision=False)
            d = TRIAGE.analyse(work, rules, td)
            self.assertEqual(d["totals"]["нехватка квитанций"], 1)
            self.assertTrue(d["junk"])
            self.assertGreater(d["blocking"], 0)

    def test_receipt_with_a_sixth_column_is_junk_but_rule_rows_stay_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            work, rules = self.write_work(td)
            body = Path(rules).read_text(encoding="utf-8").replace(
                "\tправило\n", "\tправило\tлишнее\n")
            Path(rules).write_text(body, encoding="utf-8")
            d = TRIAGE.analyse(work, rules, td)
            self.assertTrue(d["junk"])
            self.assertIn("ровно 5 столбцов", d["junk"][0]["что"])
            self.assertEqual(d["totals"]["нехватка квитанций"], 1)

            Path(rules).write_text(
                "R1\tid=g0001\tN\tтокен>=1\tоснование\tпояснение\n",
                encoding="utf-8")
            parsed, receipts, junk = TRIAGE.read_rules(rules)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(receipts, [])
            self.assertEqual(junk, [])

    def test_candidate_decision_fails_until_row_is_reassigned(self):
        with tempfile.TemporaryDirectory() as td:
            work, rules = self.write_work(td, decision="кандидат")
            d = TRIAGE.analyse(work, rules, td)
            self.assertEqual(d["totals"]["квитанций-кандидатов"], 1)
            self.assertEqual(d["totals"]["нехватка квитанций"], 1)
            self.assertGreater(d["blocking"], 0)

    def test_rule_decision_still_requires_existing_gates(self):
        with tempfile.TemporaryDirectory() as td:
            work, rules = self.write_work(td, quote="wrong quote")
            d = TRIAGE.analyse(work, rules, td)
            self.assertEqual(d["totals"]["квитанций не подтвердилось"], 1)
            self.assertGreater(d["blocking"], 0)

    def test_off_demand_candidate_receipt_still_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            work, rules, lines = self.write_many_work(td)
            first = TRIAGE.analyse(work, rules, td)
            want = set(first["rules"][0]["нужны"])
            extra = next("g%04d" % i for i in range(1, 7) if "g%04d" % i not in want)
            line_no = int(extra[1:])
            receipt = "+R1\t%s\thost/app.log:%d\t«%s»\tкандидат\n" % (
                extra, line_no, lines[line_no - 1])
            work, rules, _lines = self.write_many_work(td, receipt_lines=[receipt])
            d = TRIAGE.analyse(work, rules, td)
            self.assertEqual(d["totals"]["квитанций-кандидатов"], 1)
            self.assertTrue(d["receipt_problems"])
            self.assertGreater(d["blocking"], 0)

    def test_unknown_rule_unknown_row_and_noncovered_receipts_fail_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            receipts = [
                "+R9\tg0001\thost/app.log:1\t«placeholder»\tправило\n",
                "+R1\tg9999\thost/app.log:1\t«placeholder»\tправило\n",
                "+R1\tg0002\thost/app.log:2\t«placeholder»\tправило\n",
            ]
            work, rules, lines = self.write_many_work(td, rule="id=g0001")
            receipts = [r.replace("placeholder", lines[0] if "app.log:1" in r else lines[1])
                        for r in receipts]
            work, rules, _lines = self.write_many_work(td, rule="id=g0001",
                                                       receipt_lines=receipts)
            d = TRIAGE.analyse(work, rules, td)
            kinds = {p["kind"] for p in d["receipt_problems"]}
            self.assertIn("unknown-rule", kinds)
            self.assertIn("unknown-row", kinds)
            self.assertIn("row-not-covered", kinds)
            self.assertGreater(d["blocking"], 0)

    def test_duplicate_receipt_rows_fail_identical_and_conflicting(self):
        for second, kind in (("правило", "identical"), ("кандидат", "conflicting")):
            with tempfile.TemporaryDirectory() as td:
                one = "+R1\tg0001\thost/app.log:1\t«%s»\tправило\n" % NEEDLE
                two = "+R1\tg0001\thost/app.log:1\t«%s»\t%s\n" % (NEEDLE, second)
                work, rules, _lines = self.write_many_work(td, rule="id=g0001",
                                                           receipt_lines=[one, two])
                d = TRIAGE.analyse(work, rules, td)
                self.assertEqual(d["duplicate_receipts"][0]["kind"], kind)
                self.assertGreater(d["blocking"], 0)
                self.assertIn("дублик", TRIAGE.render(d).lower())


class ScoreReportOutcomeV26(unittest.TestCase):
    def score_report(self, text, root):
        bench = SHERLOCK / "eval" / "bench"
        sys.path.insert(0, str(bench))
        try:
            scorer = load("score_report_v26_test", bench / "score-report.py")
        finally:
            try:
                sys.path.remove(str(bench))
            except ValueError:
                pass
        key = {"D01": {"title": "RED HERRING: service start",
                       "anchor": "host/app.log:1"}}
        return scorer.findings_of(key, text, root)

    def test_decoy_success_is_false_positive_but_normal_is_refutation(self):
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            false_positive = self.score_report(report(outcome="успех", rejected=False,
                                                      coverage=False), td)
            refutation = self.score_report(report(outcome="норма", rejected=False,
                                                  coverage=False), td)
            self.assertEqual(false_positive["outcomes"]["decoys_false_positive"], 1)
            self.assertEqual(false_positive["outcomes"]["decoys_refutation"], 0)
            self.assertEqual(refutation["outcomes"]["decoys_false_positive"], 0)
            self.assertEqual(refutation["outcomes"]["decoys_refutation"], 1)

    def test_score_report_exports_new_v26_counts_additively(self):
        bad = report().replace(
            "| host/empty.log | пусто | байт=0 |",
            "| host/nonempty.log | пусто | байт=0 |")
        with tempfile.TemporaryDirectory() as td:
            corpus(td)
            structure = self.score_report(bad, td)["structure"]
            self.assertEqual(structure["v26_coverage_false_empty"], 1)
            self.assertEqual(structure["v26_coverage_mismatched_citation"], 0)
            self.assertEqual(structure["v26_attribution_duplicate_ids"], 0)


if __name__ == "__main__":
    unittest.main()
