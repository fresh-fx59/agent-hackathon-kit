# Кейс 06 — Sherlock: разбор логов и поиск причины

Навык **Sherlock** для **Qwen Coder CLI**: по логам находит корневую причину, место в коде и предлагает исправление.
Принцип — **модель разбирает, код обеспечивает покрытие, проверяемость и память** (парсеров форматов нет).

Постановка организаторов — [`06-dev-logging.md`](06-dev-logging.md); требования в проверяемом виде — [`REQUIREMENTS.md`](REQUIREMENTS.md).

## Два имени, и оба обязательны

| что | значение | почему именно так |
|---|---|---|
| имя навыка (`name:` в `SKILL.md`) | **`sherlock`** | одинаково во всех 13 версиях; это имя видит модель |
| каталог установки | **`log-rca`** | команды запуска инструментов внутри `SKILL.md` записаны как `.qwen/skills/log-rca/tools/…` |

**Каталог переименовывать нельзя.** Под любым другим именем обе ветки `ls` в
команде промахиваются, `ls` печатает пустую строку, команда становится
`python3 ""` — и `SKILL.md` уводит этот сбой в свою же ветку «если инструмента
нет». То есть навык молча работает без инструментов, а прогон выглядит как
обычный прогон без навыка. Проверяется механически:
`tools/tests/test_documented_commands_run.py::EveryRunnerInstallsWhereTheSkillLooksForItsTools`.

## Установка

Поставляется **`v11`** — какая версия и почему, см. `sherlock/skills/README.md`.

```bash
mkdir -p ~/.qwen/skills && rm -rf ~/.qwen/skills/log-rca
cp -r sherlock/skills/v11 ~/.qwen/skills/log-rca
```

Больше ничего: ни переменных окружения, ни ключей, ни конфигов, ни MCP-серверов, ни pip.
Проверка: `qwen` → «логи в /путь/к/логам, разберись что случилось».

## Запуск на паке организаторов

Распаковать их же пак туда, где его ждёт раннер (`petstore_input_pack.zip` лежит в этой папке и разворачивается в каталог `petstore_input_pack/`):

```bash
mkdir -p ~/hack/petstore-pack && unzip -q petstore_input_pack.zip -d ~/hack/petstore-pack
```

Интерфейс: `sherlock/eval/petstore/run-tc.sh <tc01|tc03|tc05> <плечо> [label]`. Плечо —
имя любой папки из `sherlock/skills/` с `SKILL.md` (`v11`, `v10`, …, `v1`; в
usage-строке скрипта перечислены не все) либо `none` — та же модель **без**
навыка, это baseline.
Обязательна `SHERLOCK_API_KEY`; остальное с умолчаниями: `SHERLOCK_BASE_URL=https://linkapi.ai/v1`,
`SHERLOCK_MODEL=[SP]deepseek-v4-flash`, `SHERLOCK_TIMEOUT=900`,
`SHERLOCK_PACK=~/hack/petstore-pack/petstore_input_pack`, `QWEN_BIN=~/.local/bin/qwen`.
`--approval-mode yolo` раннер выставляет сам — без него Qwen отказывает и навыку, и
`run_shell_command`, и меряется не то.

```bash
export SHERLOCK_API_KEY=…                    # ключ только через env, не в argv
sherlock/eval/petstore/run-tc.sh tc01 v11    # цепочка по correlation_id → файл:метод
sherlock/eval/petstore/run-tc.sh tc03 v11    # второй инцидент, notification-service
sherlock/eval/petstore/run-tc.sh tc05 v11    # НЕГАТИВНЫЙ: верный ответ — инцидента нет
sherlock/eval/petstore/run-tc.sh tc01 none   # то же плечо baseline, для сравнения
```

## Измерения

**Главная цифра, корпус 649 МБ, 11 заложенных дефектов, один судья (`gpt-5.5`),
одна модель (`[SP]deepseek-v4-flash`):** та же модель **без навыка находит 0 из
11**, с навыком — **7–9 из 11** (четыре прогона: 7, 8, 8, 9). Цена — примерно
в 3–5 раз больше входных токенов и в 9 раз больше времени.

Две оговорки, обе измеренные: разброс судьи ±1 дефект, поэтому одно число
называть нельзя; и **приманки навык не отсеивает лучше базовой линии** —
базовая выдаёт за причину 1 из 2, навык 1–2 из 2. Это открытый дефект.

Каждая строка получена `sherlock/eval/bench/score-bench.py` по ответному ключу;
ни одна не посчитана глазами.

Сырые реестры — одна строка на прогон, вместе с полным текстом ответа:

- `sherlock/eval/petstore/runs-petstore.jsonl` — пак организаторов, TC-01/03/05;
- `sherlock/eval/runs.jsonl` — A/B по датасетам (`sherlock/eval/run.sh`);
- `sherlock/eval/bench/runs-bench.jsonl` — корпус 649 МБ, 26 форматов, answer-key.

Сам корпус — скачать (67 МБ zst, с answer-key):
https://github.com/fresh-fx59/agent-hackathon-kit/releases/tag/case06-corpus-seed20260728
— либо сгенерировать бит-в-бит: `sherlock/eval/bench/gen_corpus.py` (SEED=20260728,
`CORPUS_OUT`/`CORPUS_KEY` в env). sha256 архива — на странице релиза.

Разбор: `sherlock/eval/GAP-ANALYSIS-2026-07-29.md` — сверка с критериями организаторов;
`sherlock/eval/V5-ASSESSMENT-2026-07-29.md` — почему v5 не в поставке. **Правило: любая
цифра прослеживается до строки реестра**, а не до чьего-то резюме. Пересчитать объём:
`wc -l sherlock/eval/runs.jsonl sherlock/eval/*/runs-*.jsonl`

Измерительный стенд (per-defect слайсы, coverage/reasoning вердикт, three-tier gate) —
`sherlock/measure/README.md`; тесты — `sherlock/measure/tests/run.sh`.

## Инструменты

**Поставляемый набор — `sherlock/skills/v11/tools/`**: три файла на python3,
только stdlib, без сети и без конфига. Они лежат внутри папки навыка, поэтому
установка навыка ставит и их; при этом навык обязан выдать тот же отчёт, когда
ни один из них запустить нельзя — медленнее, но не хуже.

- `logmap.py` — шаг 1: карта корпуса, **рабочий список аномалий** (≤250 строк) и таблица темпа. Ни одной строки лога в контекст не тянет.
- `logjoin.py` — шаг 3: один id по всем файлам сразу, включая `absent_in` (где его нет) и отказ подтверждать связь двух id без совместного вхождения.
- `citecheck.py` — шаг 4: проверка улик **по содержанию** (`ok`/`wrong-content`/…) плюс `--ledger`: сколько строк рабочего списка ещё не разобрано.

`sherlock/tools/` — **прежняя линия `v8`–`v10`** (`logstat.py`, `fetch-logs.sh`),
оставлена ради воспроизводимости уже снятых замеров; `v11` держит собственную
копию инструментов, иначе правка здесь переписала бы то, что уже измерено для
`v8`–`v10` (`test_bundle_copy.py::TheForkedArmOwnsItsTools`). Подробно и с
замерами — `sherlock/tools/README.md`, тесты — `sherlock/tools/tests/run.sh`.

## Презентация

`sherlock/presentation/index.html`, живой адрес — https://fresh-fx59.github.io/agent-hackathon-kit/
