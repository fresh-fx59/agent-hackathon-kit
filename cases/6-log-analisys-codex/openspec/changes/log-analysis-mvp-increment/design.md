## Context

Кейс требует анализа разнородных логов, RCA, рекомендаций и feedback loop.
Исследование предлагает deterministic spine, однако утверждённая дизайн-спека
объединяет MVP и production/C-phase. Целевая папка поставки пока пуста. MVP
должен работать на локальном petstore input pack в закрытой среде и быть
воспроизводимым без модели, сети или кластера.

## Goals / Non-Goals

**Goals:**

- Дать один проверяемый offline путь от файла/ZIP до evidence-linked RCA.
- Доказать human-approved knowledge reuse и rule-based early warning на
  фиксированных fixtures.
- Сохранить границы, по которым позже добавятся MCP и live adapters.

**Non-Goals:**

- Production remote transport, аутентификация, HTTP/SSE/WebSocket и реальный
  Kubernetes.
- Универсальный LLM-агент, embeddings/vector DB и автоматическая активация
  правил.
- Синтетический генератор, Java patch/CI и predictive ML.
- Гарантия диагностики всех дефектов из полного ~40-item ledger в MVP.

## Decisions

### Python stdlib CLI как единая точка входа

Поставка использует Python 3.9+ и только stdlib; CLI вызывает application core,
а не содержит бизнес-логику. Это соответствует offline-ограничению и исключает
неопределённость установки зависимостей. MCP/HTTP будут тонкими адаптерами над
тем же core в следующем инкременте.

### Ограниченный безопасный ingest и единый record

Поддерживаются TXT, JSONL и ZIP с этими файлами. ZIP walk отклоняет absolute
path, traversal, symlink entries и превышение лимитов числа файлов/распакованного
размера. Каждый record содержит минимум `timestamp`, `service`, `level`,
`body`, correlation/domain IDs и `source.ref`/`source.line`; неисправимые строки
учитываются отдельно. Чувствительные значения маскируются до хранения и отчёта.
Полный OTel-контракт отложен: сейчас он избыточен для demo.

### Evidence-first deterministic RCA

Коррелятор доверяет `trace_id`, затем `correlation_id`, затем `order_id`;
результат — отсортированная timeline. Стартовые versioned JSON rules выявляют
известную checkout-цепочку: inventory latency/lock → client timeout →
`OrderFailed` после `PaymentAuthorized`. Каждому выводу присваиваются stable
`EV-*` ссылки на source records. Structured JSON report — источник истины;
Markdown является его представлением. LLM может позднее обогащать гипотезы, но
не определяет finding в MVP.

### Подтверждённая память отделена от автоматических правил

SQLite хранит cases и immutable feedback events со статусами `proposed`,
`confirmed`, `rejected`. `similar_cases` выдаёт для reuse только confirmed
cases, используя fingerprints и фильтры service/type. Сохранение знания и
подтверждение — отдельные CLI действия; правило не активируется автоматически.
Так demo доказывает feedback loop, а не скрытую подсказку в rule catalog.

### Replay как минимальный AIOps proof

Replay читает фиксированный JSONL fixture с event time и проверяет оконные
пороги warnings/latency. Alert создаётся до fixture user-failure и включает
evidence IDs. Те же правила обязаны не сработать на healthy fixture. Это
покрывает early warning, не выдавая deterministic threshold за prediction.

## Risks / Trade-offs

- [Узкие форматы и правила] → Явно документировать supported inputs и
  тестировать corrupted records; расширения добавлять адаптерами.
- [Ложная уверенность RCA] → Отчёт содержит evidence, confidence и
  limitations; не подтверждённый вывод не становится knowledge.
- [ZIP resource exhaustion] → Лимиты проверяются до и во время extraction;
  архив не извлекается на диск без необходимости.
- [Знание преждевременно решает второй кейс] → Notification-specific rules
  отсутствуют в active catalog; demo разделяет cold и warm runs.
- [Псевдо-AIOps] → Назвать результат early warning и проверять healthy control.

## Migration Plan

1. Создать изолированную поставку в `cases/06-dev-logging/claude-code/` и
   fixtures без изменения исходного input pack.
2. Выполнить unit tests и e2e demo; сохранить expected report snapshots.
3. Поставлять как локальную команду `python -m logalyzer ...`; rollback —
   удалить новую изолированную папку, так как общие интерфейсы не меняются.

## Open Questions

Нет блокирующих. Лимиты ZIP и точные CLI-флаги фиксируются в первой задаче
реализации и документации вместе с fixtures.
