## Why

Текущая дизайн-спека охватывает полноценный production/AIOps-контур: remote API,
live Kubernetes, генератор, JVM-верификацию и predictive detection. Такой объём
не позволяет надёжно продемонстрировать основную ценность кейса в хакатонном MVP:
по локальному пакету логов доказуемо найти инцидент, объяснить его причины и
использовать подтверждённое знание повторно.

Нужен небольшой воспроизводимый vertical slice, который работает офлайн без
внешних сервисов и даёт проверяемый результат на предоставленном petstore pack.

## What Changes

- Добавить CLI-ориентированный offline-анализ файлов и ZIP-архивов с
  нормализацией, маскированием чувствительных данных и ссылками на исходное
  доказательство.
- Добавить детерминированную корреляцию по `trace_id`, `correlation_id` и
  `order_id`, стартовые правила для checkout-инцидента и структурированный
  evidence-linked RCA report.
- Добавить локальный SQLite knowledge store и явный human feedback: только
  подтверждённый кейс доступен для повторного использования.
- Добавить детерминированный stream replay с rule-based early warning и
  healthy-control проверкой; LLM не участвует в hot path.
- Добавить sample data, unit/integration tests и один end-to-end demo script,
  фиксирующий ожидаемые findings и cold/warm reuse.
- Отложить MCP transport, HTTP/SSE/WebSocket API, live `kubectl`, генератор
  синтетических логов, Java-фиксы/Jenkins и predictive ML до следующих
  инкрементов.

## Capabilities

### New Capabilities

- `offline-log-rca`: Безопасно анализировать локальные логи/ZIP и выдавать
  доказательный отчёт RCA по известному checkout-инциденту.
- `confirmed-case-reuse`: Сохранять подтверждённые человеком кейсы и применять
  их как явно помеченный контекст при повторном расследовании.
- `replay-early-warning`: Воспроизводимо проигрывать поток событий и выдавать
  ранний alert при деградации, не алертя на healthy control.

### Modified Capabilities

Нет существующих capability specs.

## Impact

Новая поставка размещается в `cases/06-dev-logging/claude-code/`: Python 3.9+
stdlib CLI и модули анализатора, rules, SQLite-файл, demo fixtures и tests.
Внешние API, облачные сервисы и дополнительные Python-зависимости не требуются.
