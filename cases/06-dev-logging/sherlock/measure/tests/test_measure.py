#!/usr/bin/env python3
"""Tests for measure.py — deterministic verdicts from a captured run.

The load-bearing case is `test_right_file_wrong_lines_is_not_reached`: the whole
diagnosis rests on telling "never opened the evidence" apart from "opened it and
failed to connect it". If that distinction breaks, every verdict downstream is noise.

The three-valued verdict matters too. A shell-based read (`sed -n`, `grep`) often
cannot be resolved to a line range. Calling that "not reached" would manufacture
coverage failures that never happened, so it is reported as `unknown`.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import measure  # noqa: E402

PROOFS = [
    {"file": "apps/api.log", "line_start": 178977, "line_end": 178996, "note": "the NPE"},
]


def stream(*records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


FIXTURE = os.path.join(HERE, "fixtures", "real-stream-excerpt.jsonl")


def tool_use(name, inp, uid=None):
    """Shaped like the real stream: the correlation id lives in `id` on the
    tool_use block (see tests/fixtures/real-stream-excerpt.jsonl)."""
    block = {"type": "tool_use", "name": name, "input": inp}
    if uid:
        block["id"] = uid
    return {"type": "assistant", "message": {"content": [block]}}


def tool_result(text, uid=None, is_error=False):
    """Shaped like the real stream: `tool_use_id` + `is_error` + a `content` string."""
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": is_error,
         "content": text}]}}


class ReadEvents(unittest.TestCase):
    def test_read_file_offset_and_limit_become_a_line_range(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        ev = measure.read_events(p)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["range_known"])
        self.assertEqual(ev[0]["line_start"], 178971)
        self.assertEqual(ev[0]["line_end"], 179010)

    def test_no_offset_no_limit_is_the_whole_file_not_an_unknown_range(self):
        """CRITICAL-2a. The natural call on a small file passes neither offset nor
        limit — and reading a whole file reads every line in it, so a proof inside it
        is definitively REACHED. The old code required both to be ints and demoted
        the commonest successful read to `unknown`."""
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log"}))
        ev = measure.read_events(p)
        self.assertTrue(ev[0]["range_known"])
        self.assertEqual(ev[0]["line_start"], 1)
        self.assertEqual(ev[0]["line_end"], measure.OPEN_END)

    def test_a_whole_file_result_body_pins_the_real_line_count(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log"}, uid="call_x"),
                   tool_result("l1\nl2\nl3\n", uid="call_x"))
        ev = measure.read_events(p)
        self.assertEqual((ev[0]["line_start"], ev[0]["line_end"]), (1, 3))

    def test_a_failed_read_delivered_no_bytes_so_its_range_is_unknown(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log"}, uid="call_x"),
                   tool_result("File not found", uid="call_x", is_error=True))
        ev = measure.read_events(p)
        self.assertFalse(ev[0]["range_known"],
                         "an errored read must not count as having read the file")

    def test_shell_read_records_the_file_but_leaves_the_range_unknown(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "grep -n 'NullPointer' /c/apps/api.log"}))
        ev = measure.read_events(p)
        self.assertEqual(ev[0]["file"], "/c/apps/api.log")
        self.assertFalse(ev[0]["range_known"])


class ProofReach(unittest.TestCase):
    def test_reading_the_proof_lines_counts_as_reached(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "reached")

    def test_right_file_wrong_lines_is_not_reached(self):
        # THE load-bearing case: it opened the file, but nowhere near the evidence.
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 0, "limit": 200}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")
        self.assertIn("apps/api.log", r["files_opened"])

    def test_never_opening_the_file_is_not_reached(self):
        p = stream(tool_use("read_file", {"file_path": "/c/other.log",
                                          "offset": 0, "limit": 10}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")
        self.assertEqual(r["files_opened"], ["other.log"])

    def test_unresolvable_shell_read_of_the_proof_file_is_unknown_not_a_failure(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "sed -n '178977,178996p' /c/apps/api.log"}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "unknown",
                         "an unresolvable range must not be reported as a coverage failure")

    def test_empty_stream_is_not_reached(self):
        p = stream()
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")

    def test_files_opened_matches_files_with_proofs_on_a_deep_absolute_path(self):
        # Regression: a real captured run nests the proof file many directories
        # deep under a sandbox root, unlike the fixture's single-segment "/c/"
        # mount. files_opened must still land on the exact corpus-relative name
        # so it is directly comparable to files_with_proofs.
        proofs = [{"file": "apps/checkout-api/checkout-api.log",
                   "line_start": 10, "line_end": 20, "note": "the NPE"}]
        p = stream(tool_use("read_file", {
            "file_path": "/home/claude-developer/hack/agent-hackathon-kit/cases/06-dev-logging"
                         "/sherlock/measure/cases/D01/apps/checkout-api/checkout-api.log",
            "offset": 5, "limit": 20}))
        r = measure.proof_reach(measure.read_events(p), proofs)
        self.assertEqual(r["verdict"], "reached")
        self.assertEqual(r["files_opened"], r["files_with_proofs"])

    def test_same_basename_in_different_directories_is_not_the_same_file(self):
        # syslog/node-a/syslog and syslog/node-b/syslog are two different hosts'
        # logs that happen to share a basename. A basename-only fallback would
        # let reading node-a satisfy node-b's (red-herring) proof.
        proofs = [{"file": "syslog/node-b/syslog", "line_start": 5, "line_end": 10,
                   "note": "RED HERRING"}]
        p = stream(tool_use("read_file", {"file_path": "/c/syslog/node-a/syslog",
                                          "offset": 0, "limit": 20}))
        r = measure.proof_reach(measure.read_events(p), proofs)
        self.assertEqual(r["verdict"], "not_reached",
                         "node-a/syslog must not satisfy node-b/syslog's proof by basename alone")


class AgainstTheRealCapturedStream(unittest.TestCase):
    """CRITICAL-2, built on tests/fixtures/real-stream-excerpt.jsonl — records 8,9,
    11,12,13,15,16,25 copied VERBATIM out of the only real capture we have
    (runs/20260730T195412Z-cap-multiline-stitching-v6/stream.jsonl). Every defect
    fixed here existed because the original code was written against an assumed
    stream format. Observed there and nowhere else:

      * tool_use  -> {"type":"tool_use","id":"call_eb40…","name":…,"input":…}
      * tool_result -> {"type":"tool_result","tool_use_id":"call_eb40…",
                        "is_error":false,"content":"<the file's bytes>"}
      * record 11 emits TWO tool_use blocks in ONE assistant message
        (read_file + run_shell_command), answered by records 12 and 13.
      * `grep -c "Read lines" stream.jsonl` == 0 — the range message the old code
        parsed is not something this CLI emits.
    """

    PROOF = [{"file": "checkout-api.log", "line_start": 3, "line_end": 6,
              "note": "the interleaved NPE trace"}]

    def setUp(self):
        self.events = measure.read_events(FIXTURE)

    def test_parallel_tool_uses_pair_to_their_own_results_by_id(self):
        by_id = {e["tool_use_id"]: e for e in self.events if e["tool_use_id"]}
        # record 11's read_file was answered by record 12 (case.json, ~750 chars);
        # its sibling run_shell_command by record 13 (`wc -l` output, ~130 chars).
        # A "last pending" slot hands BOTH results to the second call.
        self.assertEqual(by_id["call_eb402eb6d98b498d9ad60a31"]["tool"], "read_file")
        self.assertEqual(by_id["call_1d52d317bf0c466db20246e0"]["tool"], "run_shell_command")
        self.assertIn("case.json", by_id["call_eb402eb6d98b498d9ad60a31"]["file"])
        self.assertGreater(by_id["call_eb402eb6d98b498d9ad60a31"]["result_chars"],
                           by_id["call_1d52d317bf0c466db20246e0"]["result_chars"])

    def test_the_whole_file_read_reaches_the_proof(self):
        """Record 15 reads checkout-api.log with no offset/limit; record 16 returns
        all 9 lines. The live row scored proofs_reached=0 on this exact run."""
        r = measure.proof_reach(self.events, self.PROOF)
        self.assertEqual(r["verdict"], "reached", r)
        self.assertEqual(r["not_reached"], [])

    def test_list_directory_is_a_dir_scan_not_a_file_read(self):
        """CRITICAL-2c. Record 8 is list_directory on the case dir. The old code
        pulled its `path` into `file`, _same_file never matched, and the proof came
        back not_reached -> diagnosis `coverage`."""
        dirs = [e for e in self.events if e["kind"] == "dir"]
        self.assertEqual([e["tool"] for e in dirs], ["list_directory"])
        self.assertTrue(dirs[0]["dir"].endswith("cap-multiline-stitching"))
        self.assertIsNone(dirs[0]["file"])

    def test_a_dir_scan_alone_yields_unknown_never_not_reached(self):
        only_dir = [e for e in self.events if e["kind"] == "dir"]
        r = measure.proof_reach(only_dir, self.PROOF)
        self.assertEqual(r["verdict"], "unknown",
                         "a directory-scoped search must never manufacture a coverage failure")
        self.assertEqual(r["not_reached"], [])

    def test_a_dir_scan_of_an_unrelated_subtree_still_leaves_the_proof_missed(self):
        """The fixture's list_directory is on the CASE dir, so with the corpus root
        supplied it does cover this proof. Point a scan somewhere else and the
        coverage failure must survive — see DirScanContainment for the full matrix."""
        elsewhere = [dict(e, dir="/somewhere/else") for e in self.events
                     if e["kind"] == "dir"]
        r = measure.proof_reach(elsewhere, self.PROOF, corpus_root="/c/corpus")
        self.assertEqual(r["verdict"], "not_reached", r)

    def test_files_opened_counts_corpus_files_only(self):
        """Important-8. The live row recorded files_opened=3 for a corpus holding ONE
        log file: the directory, case.json, and the log."""
        r = measure.proof_reach(self.events, self.PROOF)
        self.assertEqual(r["files_opened"], ["checkout-api.log"], r["files_opened"])

    def test_the_old_range_message_never_appears_in_a_real_stream(self):
        body = open(FIXTURE, encoding="utf-8").read()
        self.assertNotIn("Read lines", body,
                         "if the CLI ever does emit this, the derivation can be revisited")


CORPUS = "/sandbox/cases/c1/corpus"
NESTED_PROOF = [{"file": "web/nginx/access.log", "line_start": 10, "line_end": 20,
                 "note": "the 502 burst"}]


class DirScanContainment(unittest.TestCase):
    """A directory scan may excuse a miss ONLY for proofs inside the scanned subtree.

    The defect this class exists to prevent: `_dir_scan_covers` was
    `bool(dir) and bool(proof)` — no correlation between the directory scanned and
    the proof at all. Since `list_directory` / `glob` / `grep_search` are how every
    agent opens an investigation, ONE such call anywhere made `coverage` unreachable
    for the whole run — and coverage-vs-reasoning is the only thing this module
    exists to tell apart. The fix for "manufactured coverage failures" had inverted
    into "coverage never fires".

    The proof locations are corpus-RELATIVE and the scanned dirs are ABSOLUTE, so
    the containment question is only answerable against the corpus root. That root
    is not guessed: the rig builds `cases/<id>/corpus` and hands it in (AGENTS.md
    input-gate principle — constrain at the boundary instead of disambiguating
    unconstrained input downstream).
    """

    def scan(self, *dirs):
        return measure.read_events(stream(
            *[tool_use("list_directory", {"path": d}) for d in dirs]))

    def test_scanning_an_unrelated_subtree_does_not_excuse_the_miss(self):
        """THE regression. `db/` cannot have surfaced `web/nginx/access.log`."""
        r = measure.proof_reach(self.scan(CORPUS + "/db"), NESTED_PROOF,
                                corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "not_reached", r)
        self.assertEqual(r["not_reached"], ["web/nginx/access.log:10-20"])

    def test_scanning_the_proofs_own_directory_softens_the_miss_to_unknown(self):
        r = measure.proof_reach(self.scan(CORPUS + "/web/nginx"), NESTED_PROOF,
                                corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_scanning_an_ancestor_of_the_proof_softens_the_miss_to_unknown(self):
        """A recursive glob/grep at `web/` reports the whole subtree beneath it."""
        r = measure.proof_reach(self.scan(CORPUS + "/web"), NESTED_PROOF,
                                corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_scanning_the_corpus_root_covers_every_proof(self):
        r = measure.proof_reach(self.scan(CORPUS), NESTED_PROOF, corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_scanning_outside_the_corpus_entirely_covers_nothing(self):
        r = measure.proof_reach(self.scan("/etc"), NESTED_PROOF, corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "not_reached", r)

    def test_a_near_miss_sibling_directory_does_not_cover(self):
        """`web/nginx-old` shares a string prefix with `web/nginx` but is a different
        directory. Prefix matching on raw strings would wrongly excuse this."""
        r = measure.proof_reach(self.scan(CORPUS + "/web/nginx-old"), NESTED_PROOF,
                                corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "not_reached", r)

    def test_one_covering_scan_among_several_unrelated_ones_is_enough(self):
        r = measure.proof_reach(
            self.scan(CORPUS + "/db", "/etc", CORPUS + "/web/nginx"),
            NESTED_PROOF, corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_a_trailing_slash_on_the_scanned_dir_changes_nothing(self):
        r = measure.proof_reach(self.scan(CORPUS + "/web/nginx/"), NESTED_PROOF,
                                corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_scanning_a_directory_that_contains_the_whole_corpus_covers(self):
        """A recursive glob/grep at the CASE dir (the corpus's parent) reaches every
        file in the corpus. The real capture's list_directory is exactly this — it
        scanned `cases/cap-multiline-stitching`, one level above `corpus/`."""
        r = measure.proof_reach(self.scan("/sandbox/cases/c1"), NESTED_PROOF,
                                corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_without_a_corpus_root_the_conservative_answer_is_kept(self):
        """Degraded mode: no anchor means containment is genuinely unanswerable, so
        `unknown` ("cannot exclude") stays the honest verdict. Production never takes
        this path — test_report_case asserts report-case.py supplies the root."""
        r = measure.proof_reach(self.scan(CORPUS + "/db"), NESTED_PROOF)
        self.assertEqual(r["verdict"], "unknown", r)

    def test_a_real_read_of_the_proof_still_wins_over_containment(self):
        """Containment can only ever soften not_reached to unknown. It must never
        downgrade a genuine read, and never manufacture `reached`."""
        events = measure.read_events(stream(
            tool_use("list_directory", {"path": CORPUS + "/db"}),
            tool_use("read_file", {"file_path": CORPUS + "/web/nginx/access.log",
                                   "offset": 5, "limit": 40})))
        r = measure.proof_reach(events, NESTED_PROOF, corpus_root=CORPUS)
        self.assertEqual(r["verdict"], "reached", r)


REPORT_OK = """
## 1. Что произошло
Каскад отказов в сервисе checkout начался в 03:14 UTC: очередь исходящих запросов к
платёжному шлюзу начала расти, тайм-ауты участились, а спустя семь минут упал API
сервер, обслуживающий чек-аут. Первые пользовательские жалобы поступили в 03:19,
алерт на латентность сработал в 03:21, автоматический рестарт не помог — под падал
повторно каждые 90 секунд в течение почти часа, пока дежурный не остановил
автоскейлер и не выкатил хотфикс вручную.

## 2. Корневая причина
Не хватало индекса на таблице `orders` по колонке `customer_id`: миграция,
добавляющая внешний ключ, была применена без сопутствующего индекса, потому что
чек-лист миграции не требует явного подтверждения индексации для колонок,
участвующих в JOIN на горячем пути. В результате каждый запрос чек-аута выполнял
последовательное сканирование таблицы, которая за три недели выросла с 40 тысяч
строк до 6 миллионов.

## 3. Цепочка причин
1. Миграция `0042_add_customer_fk` добавила внешний ключ без индекса.
2. Нагрузочное тестирование перед релизом гоняли на копии БД с 40 тысячами строк —
   регрессия по времени отклика была ниже порога алертинга.
3. За три недели таблица `orders` выросла в 150 раз за счёт промо-кампании.
4. Последовательное сканирование стало доминировать во времени ответа чек-аута.
5. Пул соединений к БД исчерпался, потому что долгие запросы держали соединения
   дольше обычного.
6. API сервер начал отклонять новые запросы с ошибкой `NullPointerException` в
   обработчике тайм-аута пула соединений — именно эта ошибка попала в алерт.
7. Автоскейлер интерпретировал рост latency как нехватку CPU и добавил инстансы,
   которые лишь усилили конкуренцию за соединения с БД.

## 4. Улики
apps/api.log:178977 — `NullPointerException` при получении соединения из пула.
apps/api.log:178981 — тайм-аут ожидания соединения, 30000ms превышены.
apps/api.log:179004 — повторный краш через 90 секунд после рестарта.
db/slow-query.log:2211 — план запроса показывает `Seq Scan on orders`, без индекса.
infra/autoscaler.log:551 — автоскейлер добавил три инстанса за 4 минуты.
apps/checkout-api.log:9021 — первая пользовательская ошибка 500 в 03:19 UTC.

## 5. Немедленные действия
Поднять индекс `CREATE INDEX CONCURRENTLY idx_orders_customer_id ON
orders(customer_id);` на проде без блокировки таблицы, затем вручную перезапустить
API сервер и отключить автоскейлер до подтверждения стабилизации латентности.

## 6. Исправление в коде
Добавить обязательный шаг чек-листа миграции: любая колонка, участвующая в JOIN
или WHERE на горячем пути, требует явного подтверждения индекса перед мёржем.
Также добавить нагрузочное тестирование на копии продовой БД (а не урезанной),
чтобы такие регрессии ловились до релиза, а не после.

## 7. Чего я не знаю
Не установил точно, почему нагрузочный тест использовал урезанную копию БД —
это могло быть осознанным решением ради скорости CI, а не упущением; нужно
уточнить у команды платформы. Также не проверил, была ли похожая проблема на
других таблицах с внешними ключами без индексов.

## 8. ЗНАНИЯ
ЗНАНИЯ: база пуста — обычное расследование
"""

REPORT_COLLAPSED = "Все агенты завершили работу. Отчёт выше уже содержит все находки."


class ReportChecks(unittest.TestCase):
    def test_all_eight_sections_detected(self):
        r = measure.report_checks(REPORT_OK)
        self.assertEqual(r["sections_missing"], [])
        self.assertTrue(r["has_knowledge_line"])
        self.assertFalse(r["collapsed"])

    def test_missing_root_cause_and_unknowns_are_named(self):
        text = REPORT_OK.replace("## 2. Корневая причина", "").replace("## 7. Чего я не знаю", "")
        r = measure.report_checks(text)
        self.assertIn("Корневая причина", r["sections_missing"])
        self.assertIn("Чего я не знаю", r["sections_missing"])

    def test_collapse_detected_by_banned_phrase(self):
        r = measure.report_checks(REPORT_COLLAPSED)
        self.assertTrue(r["collapsed"])
        self.assertIn("отчёт выше", r["collapse_reason"].lower())

    def test_collapse_detected_by_length(self):
        r = measure.report_checks("слишком коротко")
        self.assertTrue(r["collapsed"])


class BudgetProfile(unittest.TestCase):
    def test_counts_calls_by_tool(self):
        p = stream(tool_use("read_file", {"file_path": "/c/a.log", "offset": 0, "limit": 5}),
                   tool_use("read_file", {"file_path": "/c/b.log", "offset": 0, "limit": 5}),
                   tool_use("run_shell_command", {"command": "ls /c"}))
        b = measure.budget_profile(measure.read_events(p))
        self.assertEqual(b["tool_calls"], 3)
        self.assertEqual(b["by_tool"]["read_file"], 2)


class CombinedVerdict(unittest.TestCase):
    CASE = {"case_id": "D01", "proof_locations": PROOFS}

    def test_missed_and_never_read_is_coverage(self):
        p = stream(tool_use("read_file", {"file_path": "/c/other.log", "offset": 0, "limit": 5}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=False)
        self.assertEqual(v["diagnosis"], "coverage")

    def test_missed_but_did_read_is_reasoning(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=False)
        self.assertEqual(v["diagnosis"], "reasoning")

    def test_collapse_outranks_everything(self):
        p = stream()
        v = measure.verdict(self.CASE, p, REPORT_COLLAPSED, judge_found=False)
        self.assertEqual(v["diagnosis"], "collapse")

    def test_found_is_ok(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=True)
        self.assertEqual(v["diagnosis"], "ok")

    def test_unknown_reach_is_inconclusive_not_coverage(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "sed -n '178977,178996p' /c/apps/api.log"}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=False)
        self.assertEqual(v["diagnosis"], "inconclusive")


class CollapseThresholdScalesWithCaseKind(unittest.TestCase):
    """Important-7. MIN_REPORT_CHARS=2000 was calibrated on full-corpus reports and
    then applied to 4-line micro-corpora, so a SHORT CORRECT report on a micro case
    was labelled `collapse`. That hit the `none` baseline arm hardest — its short
    reports were scored as collapses rather than counted as misses, which flatters
    the skill it exists to be compared against."""

    SHORT_BUT_REAL = ("## Что произошло\nВ логе checkout-api видно исключение NPE. " * 12)

    def test_a_short_correct_report_on_a_micro_corpus_is_not_a_collapse(self):
        self.assertGreater(len(self.SHORT_BUT_REAL), 600)
        self.assertLess(len(self.SHORT_BUT_REAL), 2000)
        r = measure.report_checks(self.SHORT_BUT_REAL, "capability_micro")
        self.assertFalse(r["collapsed"], r["collapse_reason"])
        self.assertEqual(r["min_chars"], 600)

    def test_the_same_length_on_a_full_defect_slice_is_still_a_collapse(self):
        r = measure.report_checks(self.SHORT_BUT_REAL, "defect_slice")
        self.assertTrue(r["collapsed"])
        self.assertEqual(r["min_chars"], 2000)

    def test_a_genuinely_collapsed_micro_report_is_still_caught(self):
        r = measure.report_checks("Отчёт выше." * 3, "capability_micro")
        self.assertTrue(r["collapsed"])

    def test_the_verdict_uses_the_cases_own_kind(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        micro_case = {"case_id": "cap-x", "kind": "capability_micro",
                      "proof_locations": PROOFS}
        v = measure.verdict(micro_case, p, self.SHORT_BUT_REAL, judge_found=False)
        self.assertEqual(v["diagnosis"], "reasoning",
                         "a short micro report that read the proof is a reasoning miss, "
                         "not a collapse")
        slice_case = dict(micro_case, kind="defect_slice")
        self.assertEqual(measure.verdict(slice_case, p, self.SHORT_BUT_REAL,
                                         judge_found=False)["diagnosis"], "collapse")

    def test_judge_found_outranks_collapse(self):
        """If the judge read the whole report and says the defect was identified,
        a report WAS delivered — `collapse` is a false label whatever its length."""
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        v = measure.verdict(self.__class__.CASE_MICRO, p, "коротко", judge_found=True)
        self.assertEqual(v["diagnosis"], "ok")

    CASE_MICRO = {"case_id": "cap-x", "kind": "capability_micro", "proof_locations": PROOFS}


if __name__ == "__main__":
    unittest.main(verbosity=2)
