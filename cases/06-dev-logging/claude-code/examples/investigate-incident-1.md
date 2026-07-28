# Пример: разбор инцидента c-8f3a2b91 (petstore, developer-режим)

Это реальный прогон инструмента против `petstore_input_pack.zip` (лежит рядом
с `claude-code/`), а не выдуманный вывод. Логи распаковывались во временный
каталог, `--case-dir` тоже указывал на временный каталог, поэтому в репозиторий
`runs.jsonl` не попадает.

## Команды

```bash
cd cases/06-dev-logging/claude-code
TMP=$(mktemp -d)
unzip -q ../petstore_input_pack.zip -d "$TMP"
PACK="$TMP/petstore_input_pack"

python3 -m logalyzer stats --logs "$PACK/logs"

python3 -m logalyzer investigate \
  --logs "$PACK/logs" \
  --correlation-id c-8f3a2b91-4d7c-11ee-b962-0242ac120002 \
  --repo "$PACK/repo" \
  --out "$TMP/report.json" --md "$TMP/report.ru.md" \
  --case-dir "$TMP/case"
```

## Реальный вывод `stats`

```json
{
  "records_total": 77,
  "unparsed": 0,
  "by_service": {
    "inventory-service": 9,
    "k8s": 6,
    "kafka": 6,
    "metrics": 14,
    "notification-service": 5,
    "order-service": 20,
    "payment-service": 8,
    "second_incident_notification": 8,
    "api-gateway": 1
  }
}
```

77 записей из 9 источников, ни одной непарсированной строки — можно сразу
запускать `investigate`, каталоги с кодом (`--repo`) уже переданы, поэтому
уточнение (exit 3) в этом прогоне не потребовалось.

## Реальный вывод `investigate` (exit code 0)

```
report: $TMP/report.json (mode=dev, rules=R-ORD-001,R-INV-001,R-ORD-002)
```

Инструмент отработал в режиме `dev` (код найден по `--repo`) и сопоставил
сразу три правила: `R-ORD-001`, `R-INV-001`, `R-ORD-002`.

## Первые строки `report.ru.md` (как есть, без правок)

```markdown
# Отчёт RCA — c-8f3a2b91-4d7c-11ee-b962-0242ac120002

Режим: с доступом к коду. Классификация: R-ORD-001 / critical (уверенность: high).
Нарушенные инварианты SDD: И-1, И-2.

## Причинная цепочка
1. Таймаут резервирования склада обработан как терминальная ошибка: заказ помечен FAILED после успешной авторизации платежа, компенсация платежа не запущена.
2. inventory-service завершил резервирование после того, как клиент отключился по таймауту: резерв повисает без released/компенсации — утечка стока.
3. Таймаут вызова inventory (2000ms) ниже наблюдаемой латентности сервиса под нагрузкой; каждый вызов при деградации обречён на таймаут.

## Root cause
- Сервис: `order-service`
- Описание: Таймаут резервирования склада обработан как терминальная ошибка: заказ помечен FAILED после успешной авторизации платежа, компенсация платежа не запущена.
- Код: `repo/services/order-service/src/main/java/com/petstore/order/svc/OrderCheckoutService.java`, метод `checkout`, строка 80

## Таймлайн (доказательства)
- [EV-001] 2026-07-15T11:22:03.098Z `api-gateway` — span  ERROR
- [EV-002] 2026-07-15T11:22:03.104Z `order-service` — received checkout request
- [EV-003] 2026-07-15T11:22:03.104Z `order-service` — span  ERROR
- [EV-004] 2026-07-15T11:22:03.118Z `order-service` — order created in status PAYMENT_PENDING
- [EV-005] 2026-07-15T11:22:03.129Z `order-service` — POST /payments/authorize -> payment-service
- [EV-006] 2026-07-15T11:22:03.129Z `order-service` — span  OK
- [EV-007] 2026-07-15T11:22:03.135Z `payment-service` — POST /payments/authorize
- [EV-008] 2026-07-15T11:22:03.135Z `payment-service` — span  OK
- [EV-009] 2026-07-15T11:22:03.198Z `payment-service` — psp response OK
- [EV-010] 2026-07-15T11:22:03.402Z `payment-service` — payment AUTHORIZED
- [EV-011] 2026-07-15T11:22:03.405Z `payment-service` — produced PaymentAuthorized to payments.events.v1
- [EV-012] 2026-07-15T11:22:03.412Z `order-service` — payment authorized
- [EV-013] 2026-07-15T11:22:03.415Z `order-service` — order transitioned PAYMENT_PENDING -> INVENTORY_PENDING
- [EV-014] 2026-07-15T11:22:03.421Z `order-service` — POST /inventory/reserve -> inventory-service
```

## Что смотреть в этом отчёте

Классификация — `R-ORD-001`, критично, уверенность высокая: платёж по заказу
`c-8f3a2b91-…` уже списан (`payment AUTHORIZED`, EV-010), но резервирование
склада (`POST /inventory/reserve`, EV-014) не уложилось в таймаут, и
order-service пометил заказ `FAILED`, не запустив компенсацию платежа — это и
есть нарушенные инварианты И-1/И-2 из шапки отчёта. Причинная цепочка (3
пункта) объясняет это на трёх уровнях: непосредственная причина (таймаут
трактован как терминальная ошибка), системная причина (inventory-service
завершает резерв уже после отключения клиента — резерв повисает) и корневая
причина (таймаут вызова короче реальной латентности сервиса под нагрузкой).
Root cause указывает конкретное место в коде — `OrderCheckoutService.java`,
метод `checkout`, строка 80 — эта ссылка уже прошла проверку по дереву
репозитория (gate), поэтому её можно передавать разработчику как есть.
Дальше в полном отчёте (после первых 30 строк) идут разделы «Немедленные
действия», «Рекомендации по коду» и, если применимо, «Ограничения». В этом
прогоне `code_recommendations` — это 5 кандидатов (dev-режим): помимо
root cause (`OrderCheckoutService.checkout`, строка 80, уверенность high),
туда попали ещё четыре файла-логгера с medium-уверенностью
(`InventoryClient`, `PaymentClient`, `OrderEventProducer`,
`PaymentController`) — это места, откуда велось логирование по ходу
инцидента, а не обязательно точки правки; их стоит пересказывать
отдельно от root cause и с пометкой уверенности. Все эти разделы нужно
пересказывать дословно, ссылаясь на EV-идентификаторы из таймлайна, а не
добавлять факты от себя.
