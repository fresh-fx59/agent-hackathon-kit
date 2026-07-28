# Кросс-ревью: план Codex × спек claude-code — план синергии

Дата: 2026-07-28, состояние репо: `c8d7bd5`.
Метод: 13 агентов — 4 линзы сравнения (контракты, сильные стороны каждой
из сторон, матрица покрытия приёмки), 8 adversarial-проверок critical/major
утверждений по реальным файлам, синтез.

Входные документы:
- **Codex (Артём):** `cases/6-log-analisys-codex/research_codex.md` (912 строк),
  `cases/6-log-analisys-codex/case.md`,
  `openspec/changes/log-analysis-mvp-increment/` (proposal, design, tasks,
  3 capability-спеки: offline-log-rca, confirmed-case-reuse, replay-early-warning).
- **claude-code:** `cases/06-dev-logging/claude-code/docs/specs/2026-07-28-design.md`.
- Ground truth: наш defect-ledger (~40 дефектов, из них 20 диагностируемых по
  логам) + контракты кита (benchmark `--self-test`/`--score-only`, `verify.sh`).

## 1. Вердикт

OpenSpec MVP Артёма — правильный критический путь (маленький офлайн
vertical slice, воспроизводимый без LLM и сети), но он на 100% пересекается с
нашим детерминированным «хребтом» и целится в **ту же папку**
`cases/06-dev-logging/claude-code/` с **тем же именем пакета** `logalyzer`.
Наш спек выигрывает по охвату (typed ingest, генератор, benchmark-gate,
stand/fix/CI, MCP), их — по процессной дисциплине (SHALL-сценарии, порядок
сборки, governance знаний, production-safe фикс). Единые контракты:

| Контракт | Берём из Codex | Берём из claude-code | Единое решение |
|---|---|---|---|
| Порядок сборки | Vertical slice как Инкремент 1 (офлайн, без LLM, cut-lines, e2e demo-команда как smoke) | Полный охват (генератор, stand, fix, CI, MCP, API) как Инкременты 2–3 | Их 6-фазный tasks.md = Инкремент 1 общего плана; наши подсистемы поверх, по модулям |
| Схема события | Их record: `timestamp`/`observed_timestamp`, `service`, `level`, `body`, trace/correlation/domain IDs, `source.ref`/`source.line`, `parse_quality`, `redaction_applied` | (схема не была зафиксирована) | Их схема канонична; pack-поля `{ts,msg}` — проекция на границе ingest; отдельный doc `docs/contracts/normalized-record.md` |
| Правила | Канонический versioned JSON: `id/version/status/owner/scope/condition/hypothesis/invariant_refs/tests/created_from_case`; предикаты `sequence/within/count/rate/absence/metric` | Subset-YAML-парсер как адаптер pack-диалекта; `rubric_sha`; матчинг по (service, level, drain-template) | Один движок, JSON каноничен, YAML — импорт-адаптер. Карантин R-NOTIF-001 через `status`, не папкой |
| MCP | `submit_feedback` | Обязательная шестёрка (имена совпали — free win) + extras (`get_log_stats`, `get_log_patterns`, `list_services`, `list_log_sources`, пагинация) | Наша поверхность + их `submit_feedback`; только `minimcp` (stdlib-lock кита); MCP владеет claude-code |
| Knowledge | Governance: `proposed/confirmed/rejected`, immutable `feedback_events`, CLI confirm/reject, reuse ТОЛЬКО confirmed, пометка reuse в warm-отчёте | Retrieval: exact-fingerprint ⇒ recurrence, иначе TF-IDF cosine + boosts; dedup-on-save | Их governance вокруг нашего ретривера, одна SQLite-схема. Закрывает критерий «working HUMAN feedback loop», который auto-save→auto-reuse проваливал |
| RCA-отчёт | JSON-контракт research §4.7 (classification/timeline/cause_chain/root_cause/…/limitations); Markdown = представление JSON + тест «не добавляет утверждений» | `gates.py` («model proposes, code disposes»), `EV-*` (совпали) | Один producer: детерминированное ядро выдаёт ПОЛНЫЙ baseline-отчёт без LLM; LLM — enrichment-флаг; gates в обоих режимах |
| Маскирование | Default: отсутствие raw-значений в хранилище И отчётах + тесты | Обратимые псевдонимы как механизм | Их default побеждает; де-маск — только локальный флаг, НЕ в сдаваемых артефактах |
| IDs требований | Capability-имена | TC-01..05 / A1–A5 (язык пака) | Канон = TC/A; в capability-спеки добавить crosswalk (offline-log-rca≈TC-01+A1.1/A1.2; confirmed-case-reuse≈TC-03+A1.5; replay-early-warning≈TC-02/05+A1.4/A1.6; TC-04 — за claude-code) |
| Верификация | Snapshot-тесты, per-scenario unittest | `benchmark.py` findings-mode + `expected-findings.json` (замороженный ledger, gate ≥50% из 20) | Единственный источник истины = benchmark.py + ledger; snapshots → fixtures под benchmark; SHALL-сценарии → unittest |
| Синтетический стрим | Требования replay: event-time JSONL, помеченная точка отказа, healthy control | `streamgen/` (NHPP+MMPP, Zipf, метастабильный каскад, dirty-data, `--audit`) | Fixture = выхлоп streamgen (+ `ground_truth.jsonl`, manifest). Ручная fixture запрещена: файл сам оценивается LLM-as-judge |

## 2. Разделение труда

**Директория:** одна поставка — `cases/06-dev-logging/claude-code/`,
владелец claude-code. Codex работает PR-ами в неё по своим MVP-инкрементам
(proposal/design Артёма ошибочно считают папку пустой — там уже наш спек).
`cases/6-log-analisys-codex/` — planning-docs-only (и никогда не получает
`benchmark.py`/`test_*.py`: `verify.sh` глобит их по всему репо в общий гейт).

**Codex строит** (под едиными контрактами): rules_engine (JSON-диалект) +
каталог правил checkout-цепочки; knowledge governance (схема, feedback_events,
CLI confirm/reject, confirmed-only retrieval); replay early-warning
(event-time, bounded lateness, processing_latency в алерте); RU-рендерер
отчёта + тест «no new claims»; e2e demo-команда.

**claude-code строит:** ingest со всеми typed-парсерами (trace JSON, kafka
jsonl, k8s events, metrics text, kubectl) + ZIP-safety; masking; drain;
correlate+evidence (с их 6-ступенчатой лестницей доверия + absence-детекцией);
MCP; streamgen; stand/ + fix/ (Java diff + JUnit) + 3 CI-lane; benchmark.py +
defect-ledger; detect.py (статистический ярус поверх их rule-ярусов);
SKILL.md/README/workflows/examples; api.py; observability.

**Общие артефакты (один экземпляр):** defect-ledger + expected-findings.json;
synthetic_stream.jsonl + healthy control + ground_truth.jsonl; benchmark.py;
stand/; fix diff; контрактные доки.

**Независимо и потом сравниваем:** LLM-стратегия обогащения RCA, ранжирование
гипотез, формулировки RU-отчёта — дёшево, без файловых конфликтов.

## 3. Критические предупреждения (все CONFIRMED проверкой по файлам)

**Codex не видит:**
1. **Папка не пуста, имя занято.** proposal Impact + design Migration Plan
   целятся в `cases/06-dev-logging/claude-code/` + `python -m logalyzer` —
   коллизия на каждом модуле хребта. Решить до первого коммита кода.
2. **TXT/JSONL/ZIP-only ingest** не читает trace JSON, kafka_events.jsonl,
   k8s_events.log, metrics_snapshot.txt — там ~треть из 20 gold-дефектов
   (в т.ч. кросс-файловая корреляция lock-wait 2384ms). MVP сам по себе не
   достигает ≥50%-gate; отложенные MCP/generator/fix/stand обязательны по
   приёмке (A3, A1.3, TC-02 precision≥0.7, A4, TC-04).
3. **Ручная replay-fixture** почти гарантированно «too clean» для
   LLM-as-judge — стрим сам является оцениваемым артефактом.
4. **Snapshot-тесты как источник истины** расходятся с CI-гейтом кита
   (benchmark findings-mode) — два разных ответа на «работает ли».

**claude-code не видел (research Артёма прав):**
5. **Java-фикс без idempotency key создаёт двойную reservation** — в repo
   нулевая идемпотентность, а reservation завершается ПОСЛЕ отключения
   клиента; фикс в исходном виде воспроизводит дефект, который чинит.
   Плюс `CheckoutResult` не выражает pending.
6. **Auto-save→auto-reuse проваливает критерий «working human feedback
   loop»** (агент подтверждает сам себя); при их confirmed-only-политике наш
   warm-run вернул бы ноль кейсов.
7. **detect.py на 5s-бакетах по arrival order будет флапать на нашем же
   генераторе** (1–5% out-of-order + clock skew) — нужны event-time-окна;
   «predictive»-ярус противоречит их честному «early warning, а не
   prediction» — жюри-видимое противоречие.
8. **TC-01 держится на LLM, а доступность LLM/сети на площадке не
   подтверждена** — без детерминированного baseline-отчёта демо хрупкое.
9. **Де-маскированный финальный отчёт** красит их тесты raw-value-absence.

## 4. Дыры, которых не видит НИКТО (из матрицы покрытия)

- **A5.1 InSourceHub review checklist** — не адресован ни одной стороной;
  нужно раздобыть чек-лист или задокументировать соответствие «по духу».
- **Metric-предикаты поверх metrics_snapshot.txt** — Prometheus-text ingest
  есть только у claude-code, `metric`-предикат есть только в DSL Codex;
  работает только вместе.
- **TC-05: контракт severity-ярусов** — «no false positives» требует различать
  «инцидент» и «data-quality note» в формате отчёта, иначе dirty-data-находки
  засчитают как ложные алерты.
- **Протокол p95** (A2.2 RCA ≤60s, A2.3 detection ≤30s, TC-01 «p95 ≤60s»):
  ни одна сторона не определила, из скольких прогонов и на чём меряем.
- **TC-01 «точность гипотез ≥80% (эксперт)»** — протокола нет ни у кого.

## 5. Топ правок

**В спек claude-code (внесены разделом Cross-review amendments):**
build order (их slice = Инкремент 1); record-контракт; knowledge lifecycle +
`submit_feedback`; idempotency в fix/; детерминированный baseline-отчёт (LLM
= enrichment); event-time-окна в detect + переименование predictive; ZIP-safety;
редактированный отчёт по умолчанию; report-JSON = research §4.7; открытые
вопросы вместо «none blocking»; закрытие дыр §4.

**Предложения Артёму (codex/openspec):**
1. Поправить proposal.md Impact + design.md: `claude-code/` занят — инкремент
   выполняется PR-ами в общий `logalyzer/` по единым контрактам;
   `6-log-analisys-codex/` → planning-docs-only; убрать «целевая папка пуста».
2. Расширить инкремент 2 typed-парсерами trace/kafka/k8s/metrics (или взять
   наш `ingest.py` как есть); снять Non-Goal с defect-ledger — иначе
   ≥50%-gate недостижим кодом MVP.
3. Snapshots → fixtures под общий benchmark.py; replay-fixture = выхлоп
   streamgen; masking-default ваш (raw-value-absence) — принят; добавить
   TC/A-crosswalk в каждый capability-spec; TC-04 остаётся за claude-code.
