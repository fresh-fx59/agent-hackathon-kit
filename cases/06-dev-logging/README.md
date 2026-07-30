# Кейс 06 — Sherlock: разбор логов и поиск причины

Навык log-RCA для **Qwen Coder CLI**: по логам находит корневую причину, место в коде и предлагает исправление.
Принцип — **модель разбирает, код обеспечивает покрытие, проверяемость и память** (парсеров форматов нет).

Постановка организаторов — [`06-dev-logging.md`](06-dev-logging.md); требования в проверяемом виде — [`REQUIREMENTS.md`](REQUIREMENTS.md).

## Установка

Поставляется **`v4.1`** — какая версия и почему, см. `sherlock/skills/README.md`
(`v5` — размеченный черновик self-learning + анализа кода, **не измерен**;
`v6` — это `v5` плюс выгрузка логов со стенда по SSH, тоже **не измерен**).

```bash
mkdir -p ~/.qwen/skills && rm -rf ~/.qwen/skills/log-rca
cp -r sherlock/skills/v4.1 ~/.qwen/skills/log-rca
```

Больше ничего: ни переменных окружения, ни ключей, ни конфигов, ни MCP-серверов, ни pip.
Проверка: `qwen` → «логи в ./logs, разберись что случилось».

## Запуск на паке организаторов

Распаковать их же пак туда, где его ждёт раннер (`petstore_input_pack.zip` лежит в этой папке и разворачивается в каталог `petstore_input_pack/`):

```bash
mkdir -p ~/hack/petstore-pack && unzip -q petstore_input_pack.zip -d ~/hack/petstore-pack
```

Интерфейс: `sherlock/eval/petstore/run-tc.sh <tc01|tc03|tc05> <плечо> [label]`. Плечо —
имя любой папки из `sherlock/skills/` с `SKILL.md` (`v4.1`, `v4`, …, `v6`; в
usage-строке скрипта перечислены не все) либо `none` — та же модель **без**
навыка, это baseline.
Обязательна `SHERLOCK_API_KEY`; остальное с умолчаниями: `SHERLOCK_BASE_URL=https://linkapi.ai/v1`,
`SHERLOCK_MODEL=[SP]deepseek-v4-flash`, `SHERLOCK_TIMEOUT=900`,
`SHERLOCK_PACK=~/hack/petstore-pack/petstore_input_pack`, `QWEN_BIN=~/.local/bin/qwen`.
`--approval-mode yolo` раннер выставляет сам — без него Qwen отказывает и навыку, и
`run_shell_command`, и меряется не то.

```bash
export SHERLOCK_API_KEY=…                    # ключ только через env, не в argv
sherlock/eval/petstore/run-tc.sh tc01 v4.1   # цепочка по correlation_id → файл:метод
sherlock/eval/petstore/run-tc.sh tc03 v4.1   # второй инцидент, notification-service
sherlock/eval/petstore/run-tc.sh tc05 v4.1   # НЕГАТИВНЫЙ: верный ответ — инцидента нет
sherlock/eval/petstore/run-tc.sh tc01 none   # то же плечо baseline, для сравнения
```

## Измерения

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

## Инструменты

Три — по одному файлу на python3, четвёртый — на bash; только stdlib/coreutils,
без сети; подробно и с замерами — `sherlock/tools/README.md`, тесты —
`sherlock/tools/tests/run.sh`.

- `sherlock/tools/citecheck.py` — проверка улик **по содержанию**: говорит ли цитируемая строка то, что утверждает отчёт (`ok`/`wrong-content`/…).
- `sherlock/tools/logstat.py` — карта корпуса без чтения корпуса: размер, строки, границы времени, уровни и формы строк, чтобы выбрать, что открывать.
- `sherlock/tools/logjoin.py` — один id по всем файлам сразу, включая `absent_in` (где его нет) и совместную встречаемость двух id.
- `sherlock/tools/fetch-logs.sh` — принести логи со стенда по SSH (или из локального каталога) инкрементально, по курсорам, с манифестом; содержимое не читает — разбор остаётся за моделью. Используется только в `v6`, **опционален**.

## Презентация

`sherlock/presentation/index.html`, живой адрес — https://fresh-fx59.github.io/agent-hackathon-kit/
