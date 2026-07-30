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


def tool_use(name, inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def tool_result(text):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "content": text}]}}


class ReadEvents(unittest.TestCase):
    def test_read_file_offset_and_limit_become_a_line_range(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        ev = measure.read_events(p)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["range_known"])
        self.assertEqual(ev[0]["line_start"], 178971)
        self.assertEqual(ev[0]["line_end"], 179010)

    def test_tool_result_text_is_preferred_over_the_input(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 1, "limit": 2}),
                   tool_result("Read lines 2-3 of 4 from /c/apps/api.log"))
        ev = measure.read_events(p)
        self.assertEqual((ev[0]["line_start"], ev[0]["line_end"]), (2, 3))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
