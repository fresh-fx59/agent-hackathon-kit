<!-- Loaded on demand from SKILL.md — progressive disclosure. Kept out of SKILL.md
     so the procedure stays close to the moment it matters: smaller models
     tolerate far less distance between an instruction and its use. -->

## Contents

- **Формат отчёта** — блоки `Н-n`, обязательные поля, ссылки `файл:строка`.
  - Строка «исход» — что из этого вышло (`успех|попытка|норма`).
  - Строка «атрибуция» — отдели событие от виновника.
  - Отклонённые кандидаты (`К-n`).
  - Покрытие — таблица `| файл | статус | деталь |`.
  - Чего не делать.
  - Улики.
- **Вердикты `ambiguous` и `binary-file`** — почему проверка отказывается
  подтверждать ссылку и что с ней делать.
- **Измерено: доставка не равна черновику** — два прогона, которые прошли
  проверку и не доехали до пользователя.
- **Исход: третьего не бывает** — почему «успех, но не доказан» отклоняется и
  как исходы находок складываются в ответ отчёта.

## Формат отчёта

Отвечай по-русски, **целиком в финальном сообщении** (правило 1). Пять разделов,
ровно в этом порядке. Разделы 2, 3 и 4 обязательны и **непусты**; 3 и 4 уже
заготовлены на диске (`work/map.txt`, `work/worklist.tsv`), так что заполнить их —
это переписать, а не вспомнить.

```
1. Находки — ПОВТОРЯЮЩИЙСЯ блок, по одному на каждый независимый дефект:
     Н-n · заголовок
     что сломано (человеческим языком, 1–2 предложения)
     корневая причина ЭТОГО дефекта
     улики: файл:строка → дословная цитата (≥1; многострочная запись — файл:N-M)
     атрибуция: установлена | не установлена   ← ОБЯЗАТЕЛЬНАЯ отдельная строка
     исход: успех | попытка | норма            ← ОБЯЗАТЕЛЬНАЯ отдельная строка
     чем опровергал: какая проверка убила бы эту версию — и что она вернула
     что делать сейчас (если есть что)
     правка: ТОЛЬКО если рядом лежат исходники и ты их прочитал —
       файл:строка в коде → что меняется и на что (минимально, без переписывания
       архитектуры) + каким тестом это проверяется. Тесты не запускай.
       Исходников рядом нет — строки «правка» в блоке просто НЕТ; писать
       «исходники недоступны» не нужно.
2. Отклонённые кандидаты — блок на каждого, ОБЯЗАТЕЛЕН И НЕПУСТ:
     К-n · заголовок
     что выглядело как причина
     улики: файл:строка → дословная цитата, которая это опровергла
     исход: норма | попытка | успех
     чем опровергал: что именно измерено и почему кандидат снят
3. Покрытие — строка на КАЖДЫЙ относительный путь из карты:
     | путь | статус | улики |
     | --- | --- | --- |
     | файл | наблюдение | файл:строка «дословная цитата» |
     | файл | пусто | только ограничение доступа/адреса: размер, двоичный, не читалось |
4. Разбор рабочего списка — одной строкой на группу:
     g001-g004 N · g005 Н-2 · g011 Н-1 · S003 отклонён · B002 фон …
5. Чего я не знаю — данных не хватило, что бы их дало; и что осталось `?`.
```

### Строка «исход» — что из этого вышло

Каждый блок `Н-n` и каждый блок `К-n` обязан нести отдельную строку, целиком и без
ничего лишнего:

    исход: успех
    исход: попытка
    исход: норма

Ровно одно слово из трёх, и в этой строке больше ничего. Две строки `исход:` в
одном блоке — такая же ошибка, как четвёртое слово: проверка не выбирает за тебя.

| слово | когда | чем доказывается |
|---|---|---|
| `успех` | действие достигло цели: доступ получен, данные ушли, дефект дошёл до пользователя | ссылка на запись, показывающую **результат**, а не только само действие |
| `попытка` | действие видно — и видно, что цели оно **не** достигло | две ссылки: на действие и на его отказ |
| `норма` | проверено и объяснено нормальным поведением: смотрелось как дефект и им не оказалось | цитата или измерение, которое можно проверить по адресу |

**Четвёртого исхода нет, и «успех, но не доказан» — это он и есть.** Не пиши
рядом со словом никаких оговорок: сомнение живёт в «чем опровергал» и в «чего я
не знаю», а не в этой строке. Проверка отклоняет строку, в которой после слова
стоит что-то ещё, и печатает, как надо.

**Зачем.** Без этой строки блок «я проверил, и это оказалось ничем» написан теми
же полями, что и блок «я нашёл вторжение»: «улики» — это поле ЗА, а «чем
опровергал» есть у каждого блока, каким бы ни был ответ. Отчёт, который не
различает эти два случая, не отвечает на главный вопрос.

**Исходы складываются в вердикт.** Ответ всего отчёта — это самый сильный исход
среди находок: хоть один `успех` — компрометация; ни одного `успех`, но есть
`попытка` — атаковали, успех не подтверждается; все `норма` — чисто. Поэтому
реестр из одних `норма` не может закончиться словом «скомпрометирована»: проверка
сравнивает одно с другим и отказывается, если они не сходятся.

### Строка «атрибуция» — отдели событие от виновника

Каждый блок `Н-n` обязан нести отдельную строку:

    атрибуция: установлена
    атрибуция: не установлена

Это закрытый словарь. `атрибуция: не установлена` означает только одно: событие
наблюдалось и осталось находкой, но по прочитанным строкам нельзя честно назвать
инициатора. Такая строка **не переносит** событие в отклонённые кандидаты и не
делает его нормой. Наблюдение остаётся в `Н-n`, потому что оно проверяемо; меняетcя
только степень уверенности в виновнике.

### Отклонённые кандидаты

Раздел 2 теперь состоит из машинно-читаемых блоков `К-n ·`, а не из свободных
пунктов. У каждого блока ровно один `исход:` и хотя бы одна проверяемая ссылка
`файл:строка` с дословной цитатой. Обычно истинное опровержение получает
`исход: норма`: кандидат выглядел как причина, но измерение показало нормальное
поведение или другой порядок событий. Если кандидат оказался попыткой или
успехом, это уже не «мусорная корзина» — перепроверь, не должна ли строка стать
находкой.

Ссылка обязательна даже для чисел: «доля не изменилась» должна указывать на
строку, таблицу или карту, где эта доля записана. Неадресная фраза «ничего
относящегося» больше не является доказательством.

### Покрытие

Раздел 3 — таблица со схемой:

    | путь | статус | улики |

Статусы закрыты:

- `наблюдение` или `факт` — строка утверждает что-то о содержимом файла и обязана
  иметь `файл:строка` с дословной цитатой **того же пути**, что в первой колонке;
- `пусто`, `двоичный`, `нечитабельно`, `не смотрел` — адреса содержимого нет, но
  путь в первой колонке всё равно должен однозначно существовать в корпусе. Третья
  колонка принимает только закрытую грамматику ограничения доступа:
  - `пусто`: `байт=0`, `bytes=0`, `размер=0`, `size=0`, `строк=0`, `lines=0`;
  - `двоичный`: `формат=двоичный`, `format=binary`, `тип=двоичный`, `type=binary`,
    `nul=1`, `binary=true`;
  - `нечитабельно`: `ошибка=<код>`, `error=<code>`, `errno=<code>`,
    `кодировка=<code>`, `encoding=<code>`, `gzip=<code>`, `доступ=<code>`,
    `permission=<code>`;
  - `не смотрел`: `причина=лимит`, `reason=limit`, `причина=дубликат`,
    `reason=duplicate`, `причина=область`, `reason=scope`, `причина=пропуск`,
    `reason=skip`, `reason=sampling`.

Вся третья колонка no-address строки должна быть **ровно одним** закрытым токеном
выше: нельзя дописывать через `;` или предложением вывод о содержимом. «Содержимое
было обычным, ничего тревожного», «нет ошибок», «ничего относящегося», «норма» —
это наблюдения, а не ограничения доступа. Если это правда, найди строку или
измерение и запиши её как `наблюдение`; если адреса нет, пиши только закрытую
деталь выше. Проверка дополнительно сверяет `пусто` с размером файла (`0` байт) и
`двоичный` с двоичным признаком; для `нечитабельно` и `не смотрел` достаточно
существующего однозначного пути и закрытой причины.

Путь всегда пишется от корня корпуса: переход `..`, отсутствующий или
неоднозначный путь, повтор одной и той же строки покрытия и цитата из другого
файла — ошибки сдачи.

### Чего не делать

- **Не отклоняй кандидата без адреса.** «Выглядит нормально» — не опровержение.
  В разделе 2 у каждого блока обязаны быть измерение, `исход:` и ссылка с цитатой.
- **Не превращай отчёт в таймлайн.** Таймлайн без причинной связи — не
  расследование.
- **Не называй причиной то, что просто коррелирует.** «Случилось раньше» и
  «вызвало» — разные утверждения.
- **Не выдумывай инцидент на здоровом корпусе.** Если улик нет — так и напиши, и
  приведи в разделе 1 строки, показывающие **норму**. Выдуманная причина
  отправляет инженера чинить работающее.
- **Форма не зависит от того, как задан вопрос.** «Слушай, глянешь, что там с
  сервером?» — тот же запрос, что и формальный тикет. Измерено: на разговорной
  формулировке первыми пропадают ровно два раздела — «чем опровергал» и «чего я не
  знаю», — и это самые дорогие потери. Та же модель без навыка посмотрела на
  реально взломанный хост и выдала аккуратную сводку: 520 неудачных попыток, 85
  `POSSIBLE BREAK-IN ATTEMPT`, топ атакующих IP — **не упомянув единственный
  успешный вход**. Всё посчитано верно, главное пропущено.

### Улики

Вместе с уликами называй **выполненные команды** (или их точные шаблоны, если
прогонов было много) — по ним проверяющий воспроизводит твой путь.

Цитата — **дословный кусок строки**, скопированный из неё, а не пересказ.
`citecheck --require-quote` проверяет именно это. Многострочную запись цитируй
диапазоном `файл:N-M` (до 40 строк) и приводи ту строку диапазона, которая несёт
смысл.

## Вердикты `ambiguous` и `binary-file`

**`ambiguous` — the reference means several files at once.** On a bundle of
several machines `logs/relayd.log:145` is ten different files on ten machines.
The check **does not choose** any of them for you: it prints the list of
candidates and does not confirm the reference. It used to choose — it took the
file that best confirmed the claim, i.e. ambiguity was always resolved in favour
of the quote, and a claim that was false on the named machine got `ok` because of
a file on the neighbouring one.

What to do: take the path **from the corpus root** out of the candidate list (or
out of `work/worklist-<хост>.tsv`, where it already looks like that) and
substitute it in full. This is not cosmetics: without the machine name your
evidence does not say where the event happened.

**`binary-file` — the reference leads into a binary file.** `.evtx`, `.pcap`, a
memory dump, an archive, an executable: there are no lines and no line numbers
there. Opened "as text", such a file turns into garbage inside which readable
chunks turn up by accident — and a quote from such a chunk looks genuine. The
check neither confirms nor rejects such a reference: it **refuses** to check it,
because there is nothing to check it with.

What to do: render the evidence into text (`evtx_dump -o jsonl`, `tshark -T
fields`, `strings` — whatever you have) **into a separate directory**, reference a
line of the render, and say in one line of the report what you rendered it with.
The render must be reproducible: the same file, the same tool, the same result —
otherwise your reference will not survive re-checking. The binary file itself must
never be quoted, even if the word you need is visible inside.

## Измерено: доставка не равна черновику

Measured (2026-08-18): a run where `work/report.md` scored **110 out of 110**
while the text actually handed over scored **74 out of 95**, because the summary
section was written anew instead of copied. One document was checked, another was
delivered.

Measured (D04, 2026-07-31): 146 steps, 16.7 million tokens, 36 `citecheck` runs
down to zero errors, 38 verified references, 5 findings — and a final message
"Отчёт в финальном состоянии… Работа завершена", 161 characters. The
investigation was done completely and **delivered to nobody**. That is the most
expensive way to fail the task of all: paid for everything, received zero.

## Исход: третьего не бывает

There is no fourth outcome. "Success, but unproven" is exactly that fourth one,
and the check rejects such a line. Doubt lives in `чем опровергал:` and in
"what I do not know".

Without that line, the block "I checked it and it turned out to be nothing" is
written with the same fields as the block "I found an intrusion": `улики:` is
the FOR field, and `чем опровергал:` is present in every block whatever the
answer. The answer of the whole report is the **strongest outcome among the
findings**: at least one `успех` — compromise; none, but there is a `попытка` —
attacked, success not confirmed; all `норма` — clean. A registry made only of
`норма` cannot end with the word "compromised", and the check verifies that.

## `## Окно записей` — окно записей канала

Ring buffers evict. Without this section a report says «в журнале нет X» as if
it meant «X не было», and the project has already shipped one false claim of
exactly that shape («~402 000 записей вытеснено из Security.jsonl» — in fact
402275…437190 over 34 916 records, i.e. nothing lost).

Generate it, never type it:

    python3 <SKILL_BASE_DIR>/tools/rollover.py --corpus <LOG_DIR> --report --required-only --cite <файл-улики>

    # Окно записей

    итог: файлов=143 каналов=93 сплошных=93 с-пропусками=0 неприменимо=50 ошибок=0

    | путь | канал | окно | записей | нет |
    | --- | --- | --- | --- | --- |
    | Security.jsonl | Security | окно=402275–437190 | записей=34916 | нет=0 |

* PLACEMENT IS PART OF THE FORMAT: the heading must be TOP-LEVEL — `# Окно
  записей` (h1), or `## Окно записей` (h2) placed AFTER the whole «Покрытие»
  section. NEVER nest it inside «Покрытие». A deeper heading does not end where
  its author thinks: the span runs to the next heading of its OWN level, so
  either the coverage rows get read as rollover rows, or — the usual case, the
  table placed at the end of «Покрытие» — the rollover rows get read as COVERAGE
  rows. Measured on the recorded v37 report, `## Окно записей` nested inside
  `# Покрытие` is **12** blocking defects (6 «повторные пути покрытия» + 6
  «без адреса»), not one of which says the word rollover. It is NOT «+2». The
  same report at `# Окно записей`, or with the h2 section moved after all of
  «Покрытие», is exit 0;
* the `итог:` line is mandatory, exactly once, and all six counts are re-derived
  from the corpus by `citecheck` — a wrong count blocks;
* one row per channel WITH A GAP, and one row per channel of a file your
  FINDINGS cite. Not one row per corpus file: the «Покрытие» table already pays
  that price;
* a row the corpus does not support blocks just as hard as a missing one, so
  declaring everything «с пропусками» to be safe costs more, not less;
* a file whose window cannot be read (corrupt JSONL, an id that is not a number,
  an unreadable path) is a blocking defect — it is never «чисто».
