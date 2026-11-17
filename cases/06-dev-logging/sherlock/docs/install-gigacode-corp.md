# Sherlock v70 — установка в корпоративный GigaCode 26 (DeepSeek V4.1 Flash)

Инструкция для инженера. Заменяет раздел «Установить» из `skills/v70/README.md`:
там устаревшие имена (v60, `skills/v64`, папка `.qwen`). Сам README внутри пакета
не меняем: правка изменила бы хэш пакета v70.

Пометки:

- **ПРОВЕРЕНО** — так работало в наших прогонах v68–v70 (Qwen Code + DeepSeek, 2026-11-16).
- **ПРОВЕРЕНО на GigaCode** — оператор проверил на корпоративном GigaCode.
- **ПРЕДПОЛОЖЕНИЕ** — перенесено из Qwen Code; GigaCode — форк Qwen Code, но на нём
  это не проверяли. Для каждого пункта есть команда проверки.

## 0. Что нужно

- GigaCode 26 (`gigacode --version`), уже настроенный на корпоративную модель
  DeepSeek V4.1 Flash. Ключи и адрес модели — корпоративные, Sherlock своих не требует.
- `python3` 3.9+ в `PATH`: инструменты пакета — это Python-скрипты.
- Копия репозитория `agent-hackathon-kit`, ветка `main` (коммит `f886289` или новее).

## 1. Скопировать пакет — всю папку, не только SKILL.md

```bash
SHERLOCK_KIT="/absolute/path/to/agent-hackathon-kit"
WS="/absolute/path/to/investigation"        # новая пустая папка на одно расследование
(
  set -eu
  mkdir -p "$WS/.gigacode/skills" "$WS/.gigacode/agents"
  test ! -e "$WS/.gigacode/skills/log-rca"
  cp -R "$SHERLOCK_KIT/cases/06-dev-logging/sherlock/skills/v70" "$WS/.gigacode/skills/log-rca"
  find "$WS/.gigacode/skills/log-rca" -name '._*' -delete      # мусор macOS, ломает хэш
  cp "$WS/.gigacode/skills/log-rca/agents/sherlock-triage.md" "$WS/.gigacode/agents/"
)
cd "$WS"
```

- Папка `.gigacode` и навыки в виде `<имя>/SKILL.md` — **ПРОВЕРЕНО на GigaCode** (2026-09-23).
- Папка агентов `.gigacode/agents` — **ПРЕДПОЛОЖЕНИЕ** (так в Qwen Code).
- Имя навыка — `sherlock` (из `SKILL.md`), хотя папка называется `log-rca`.
- Для нового расследования — новая папка. `test` остановит копирование поверх старой.

## 2. Настройки: `.gigacode/settings.json` в папке расследования

Это фрагмент для **объединения** с уже существующими корпоративными настройками,
а не замена файла. Не трогайте адрес модели, вход и корпоративные политики.

```json
{
  "model": {
    "generationConfig": {
      "extra_body": { "thinking": { "type": "disabled" } },
      "reasoning": false,
      "samplingParams": { "max_tokens": 20000 },
      "contextWindowSize": 262000,
      "timeout": 660000
    },
    "sessionTokenLimit": 230000,
    "maxToolCallsPerTurn": 1000
  },
  "context": { "autoCompactThreshold": 0.7 },
  "memory": { "enableManagedAutoMemory": false, "enableDreams": false },
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 .gigacode/skills/log-rca/tools/stopcheck.py"
      }]
    }]
  }
}
```

Зачем каждое значение:

| Ключ | Зачем | Статус |
|---|---|---|
| `extra_body.thinking.type: disabled`, `reasoning: false` | DeepSeek V4.1 Flash по умолчанию **думает**. С думанием v67 тратил 77 % вывода на рассуждения и не успевал написать отчёт за 3600 с. Без думания черновик пишется за 230–880 с. | ПРОВЕРЕНО на прямом API DeepSeek. Пройдёт ли поле через корпоративный шлюз — ПРЕДПОЛОЖЕНИЕ, проверка в §4 |
| `max_tokens: 20000` | Предел одного ответа модели | ПРОВЕРЕНО |
| `contextWindowSize: 262000`, `sessionTokenLimit: 230000`, `autoCompactThreshold: 0.7` | Размер контекста, с которым проверяли v70; сжатие истории при 70 % | ПРОВЕРЕНО на Qwen Code; ключи в GigaCode — ПРЕДПОЛОЖЕНИЕ |
| `timeout: 660000` | Ждать ответа модели до 11 минут | ПРОВЕРЕНО |
| `memory.enableManagedAutoMemory: false`, `enableDreams: false` | Без этого после каждого ответа идёт лишний скрытый запрос к модели (извлечение «памяти») | ПРОВЕРЕНО на Qwen Code 0.24.5 |
| хук `Stop` → `stopcheck.py` | Не даёт модели закончить этап, пока проверки не пройдены, и подсказывает следующий шаг | ПРОВЕРЕНО на Qwen Code; хуки в GigaCode есть (их отключает `--safe-mode`), формат — ПРЕДПОЛОЖЕНИЕ |

Хук `PreToolUse` → `boundarycheck.py` из старого README в наших прогонах v68–v70
**не включали**. Не добавляйте его без отдельной проверки.

Режим подтверждений. В GigaCode нет `yolo`: есть только `plan`, `default` и
`auto-edit` (**ПРОВЕРЕНО на GigaCode**, `--help`, 2026-11-16). Sherlock много раз
запускает `python3 …/tools/*.py`. В режиме `default` каждый запуск придётся
подтверждать. Чтобы не подтверждать каждый раз, разрешите только эти команды, если
корпоративная политика позволяет (ключ **ПРОВЕРЕН на Qwen Code 0.24.5**, на GigaCode — ПРЕДПОЛОЖЕНИЕ):

```json
{ "permissions": { "allow": ["run_shell_command(python3)"] } }
```

Правило без `*` разрешает команды, которые начинаются с `python3`. Составная команда
вроде `cd work && python3 …` будет запрошена: `cd` не разрешён. Старый ключ
`tools.allowed` ещё работает, но устарел.

## 3. Положить логи и запустить

```bash
mkdir -p "$WS/input"     # сюда исходные логи: .evtx, JSONL, текст, архив или каталог
cd "$WS" && gigacode
```

Работа идёт в три этапа. Каждый этап — **новая сессия** (так проверяли v70):

1. **Разбор (triage).** Первое сообщение:
   `/sherlock` и путь к логам. Пусть закроет рабочий список и выполнит
   `checkpoint.py handoff --work ./work --done triage`. Отчёт на этом этапе не пишется.
2. **Черновик.** Новая сессия: `/sherlock ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ ./work — СТУПЕНЬ draft`
3. **Исправление.** Новая сессия: `/sherlock ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ ./work — СТУПЕНЬ repair`.
   Повторяйте, пока проверка не пройдёт. В нашем полном прогоне хватило 6 сессий.

Не передавайте вывод `check.py` и `checkpoint.py` через `head`/`tail`/`grep`:
полный вывод проверки лежит в `work/validation/last-check.txt`.

## 4. Проверить установку до настоящих логов

Выполните один раз на машине. Каждая проверка — одна команда.

1. Версия: `gigacode --version` → должна начинаться с `26`.
2. Навык виден: в интерактивном `gigacode` введите `/skills` — в списке должен быть
   `sherlock`. Без интерактива: в выводе `-o stream-json` первое событие `init` содержит
   `slash_commands`, и там должно быть `sherlock`.
3. Хуки включены. Сам `stopcheck.py` вне расследования молча разрешает остановку
   (`{"decision":"allow","suppressOutput":true}`), поэтому по нему ничего не видно.
   Проверьте хуки временной меткой. Добавьте в `hooks.Stop[0].hooks` второй элемент:
   ```json
   { "type": "command", "command": "python3 -c \"open('hook-fired.txt','w').write('ok')\"" }
   ```
   Затем выполните `gigacode -p "Ответь одним словом: OK"` и `cat hook-fired.txt`.
   Должно быть `ok`. После проверки удалите этот элемент и файл `hook-fired.txt`.
   Если файла нет — хуки отключены политикой: соблюдайте этапы вручную и сами
   запускайте `python3 .gigacode/skills/log-rca/tools/check.py --work ./work --corpus <папка корпуса>`.
4. Думание выключено (самая важная проверка):
   ```bash
   gigacode -p "Сколько будет 17*23? Ответь числом." -o stream-json > think-check.jsonl
   grep -c '"type":"thinking"' think-check.jsonl
   ```
   `0` — думание выключено. Число больше нуля — корпоративный шлюз не передаёт
   `extra_body`, и модель думает. Тогда попросите владельцев шлюза выключить думание для
   DeepSeek V4.1 Flash (параметр DeepSeek API `thinking: {"type": "disabled"}` или
   `reasoning_effort: "none"`). Второй признак: поле `thoughtsTokens` в файле учёта
   токенов (в Qwen Code — `~/.qwen/usage/token-usage-ГГГГ-ММ.jsonl`, в GigaCode —
   предположительно `~/.gigacode/usage/…`) должно быть `0`.
   Строка `grep reasoning` в выводе ничего не доказывает: Qwen Code не печатает такие поля.

## 5. Чем это проверено и чем нет

- **Сама инструкция** прогнана дословно на Qwen Code 0.24.5 (`.gigacode` → `.qwen`),
  2026-11-17: копирование, все ключи настроек, обнаружение навыка, срабатывание хука
  по относительному пути, разрешение `python3`, проверка думания.

- **Qwen Code + DeepSeek V4.1 Flash (прямой API), v70, 2 файла:** все детерминированные
  проверки пройдены (0 блокирующих). Судья-модель нашла утверждения «ровно три»,
  «больше ничего нет» без подсчёта по логам — это ещё предстоит исправить.
- **Claude Haiku через Claude Code, v70, 143 файла:** все этапы и финальная проверка
  пройдены. Это проверка механики, а не модели DeepSeek.
- **Полный прогон DeepSeek на 143 файлах для v70 не выполнен** (нет бюджета).
- **На GigaCode 26 пакет v70 не запускали.** Всё, что помечено ПРЕДПОЛОЖЕНИЕ, проверьте по §4.

Подробности и история: vault `personal-os`, `projects/active/sherlock-winevtx-2026-08-21.md`.
