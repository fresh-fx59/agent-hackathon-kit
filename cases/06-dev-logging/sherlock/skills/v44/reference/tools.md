<!-- Loaded on demand from SKILL.md §11. -->

## Contents

- **`covermap.py`** — таблица покрытия целиком; руками её не пишут.
- **`rollover.py`** — окно записей канала: не пропали ли записи внутри окна.
- **`cite.py`** — готовая цитата по адресу; руками цитаты не пишут.
- **Четыре инструмента навыка** — `logmap.py`, `logjoin.py`, `citecheck.py`,
  `triagecheck.py`: пути, флаги, что печатают.
  - Двоичные файлы: инструменты их пропускают.
  - `logmap.py` — карта, рабочий список, таблица темпа.
  - `logjoin.py` — один идентификатор по всем файлам.
  - `citecheck.py` — проверка отчёта и условие остановки.
  - Закрытые строки отчёта — и как их читает проверяющий.
  - `triagecheck.py` — чем закрыты строки рабочего списка.
- **Если shell запрещён** — чем заменить каждую команду.
- **Логи не в файлах** — journald, docker, удалённый хост.
- **Step 1: the map, the worklist and the axes** — what every axis of
  `work/worklist.tsv` claims, `род` / `кадрирование` / `время: НЕТ`, the
  machine-qualified reference, and the `logmap.py` split flags.
- **Перепись изменений состояния** — что `statecheck.py` считает изменением
  состояния и как закрывается группа.
- **`reportcheck.py` — контракт заказчика** — метки, инвентарь, раздел о
  нехватке данных и ВЕРДИКТ последним; профиль требований — это данные.
- **Бюджет: почему одна широкая команда убивает прогон** — измеренные числа.
- **Почему фазы идут в субагентах** — размер тела навыка в каждом запросе.

## Четыре инструмента навыка

Все четыре лежат внутри каталога навыка, поэтому путь подставляется через `ls`:
короткий `tools/…` относительно твоего рабочего каталога **не существует**. Это
не стилистика — арм, который поставлял инструменты и звал их коротким путём,
не запустил их **ни разу** ни в одном прогоне.

### Двоичные файлы: инструменты их пропускают

`logmap.py`, `logjoin.py` и `citecheck.py` считают файл двоичным по одному и тому
же признаку — нулевой байт в первых 8 КБ. Такой файл:

* в карте помечен «двоичный файл — читать нечем»;
* в `logjoin` не ищется вовсе и попадает в поле `not_searched_binary`. Это важно
  отдельно: `logjoin` печатает `absent_in`, а отсутствие — это улика. Файл,
  который никто не открывал, не имеет права выглядеть доказательством того, что
  идентификатора там нет;
* в `citecheck` даёт вердикт `binary-file` — ссылка не проверяется.

Вывод один: двоичную улику сначала рендерят в текст, потом ссылаются на рендер.

### `logmap.py` — карта, рабочий список, таблица темпа

    python3 "$(ls .qwen/skills/log-rca/tools/logmap.py ~/.qwen/skills/log-rca/tools/logmap.py 2>/dev/null | head -1)" <КАТАЛОГ_ЛОГОВ> --out ./work

`--worklist-cap 250` (сколько строк в рабочем списке), `--per-file-cap 40`
(сколько строк максимум от одного файла), `--jobs 1` (строго последовательно).
`--single-host` (весь корпус — одна машина) и `--host-depth N` (столько
компонентов пути образуют хост) — на случай, если разбиение угадано неверно.
`--map-cap N` — потолок карты в БАЙТАХ на хост (по умолчанию 150 000; `0`
выключает бюджет карты целиком и пишет одну общую `map.txt`, как до v17).
Пишет только в `--out`, корпус не трогает.

Что лежит в `work/`:

| файл | что это |
|---|---|
| `map.txt` | если хост один — по файлу: объём, число записей, кадрирование, форма времени, выведенная из данных ось серьёзности с полной гистограммой, доля уникальных форм; и **дословное содержимое каждого файла меньше 4 КБ**. Если хостов больше одного — это **указатель**, а сами карты лежат в `map-<хост>.txt` |
| `map-<хост>.txt` | **только если хостов больше одного**: карта ОДНОЙ машины. Потолок — 150 КБ на хост; что не влезло, остаётся в карте одной строкой (имя, размер, род, число форм), и карта пишет, сколько файлов свернула и сколько байт не показала. Измерено: на большой многохостовой выгрузке неразбитая карта весила около двух миллионов токенов |
| `worklist.tsv` | `id ⇥ вердикт ⇥ ось ⇥ ссылка ⇥ частота ⇥ запись`. Вердикт начинается с `?`. `ось`: `rare` — редкая форма записи, `cat` — редкое значение оси серьёзности, `new` — **новый участник**: адрес, которого не было в первой половине потока (утверждение про ВРЕМЯ, а не про редкость), `peak` — **выброс измерения**: час, в котором медиана числового поля ушла вверх минимум втрое от обычной по файлу и вернулась, `rate`/`bg` — ось темпа, `code`/`level`/`burst`/`edge` — **опора**: у файла редких форм нет вообще, и строку выбрала запасная ось (код ответа не как у всех, самое редкое значение шкалы, самый полный час, первая/последняя запись). Опора — адрес «открой и посмотри», а не утверждение; без неё такие файлы не получали ни одной строки |
| `axis3.tsv` | по (файл, форма): доля часа и p50/p90/p99 каждого числового слота, первый сравнимый час против последнего. Строки `bg` — то, что **не** сдвинулось |
| `hosts.tsv` | **только если хостов больше одного**: хост ⇥ файлов ⇥ строк ⇥ из них темп ⇥ не вошло ⇥ файл ⇥ карта ⇥ свёрнуто файлов. Здесь перечислены ВСЕ найденные хосты — ни один не выброшен |
| `worklist-<хост>.tsv` | **только если хостов больше одного**: тот же формат, что и `worklist.tsv`, но по одному хосту. Читать и заполнять надо ИХ; общий `worklist.tsv` — это леджер, в нём строки всех хостов сразу |

Три вещи, которые важно понимать про её вывод:

1. **Никакого словаря уровней внутри нет.** Ось серьёзности выведена из формы
   данных: инструмент сам находит то поле, которое в ЭТОМ файле ведёт себя как
   шкала — будь оно словом в позиции разделителя, ключом `КЛЮЧ=ЗНАЧЕНИЕ`, числом
   в JSON или кодом статуса, на любом языке. Он сообщает, какое поле выбрал и
   какие значения в нём встречаются. Смотри в гистограмму, а не в своё
   представление о том, как называется ошибка.
2. **`n=` — это базовая частота, бесплатно.** `n=1` значит «во всём файле такое
   один раз». `n=14042` значит, что это фон, даже если строка выглядит страшно.
3. **`форм больше, чем попало в список`** (`TRUNC=`) — честный остаток. Список
   обрезан по потолку, а не потому, что остальное неинтересно.

### `logjoin.py` — один идентификатор по всем файлам

    python3 "$(ls .qwen/skills/log-rca/tools/logjoin.py ~/.qwen/skills/log-rca/tools/logjoin.py 2>/dev/null | head -1)" ORD-77421 --corpus <КАТАЛОГ_ЛОГОВ>

`--substring` (совпадение внутри слова), `--no-canon` (искать буквально),
`--max-hits N`, `--json`. Ссылки выдаются записями: многострочная запись — это
`файл:N-M`, а не её первая строка.

### `citecheck.py` — проверка отчёта и условие остановки

    python3 "$(ls .qwen/skills/log-rca/tools/citecheck.py ~/.qwen/skills/log-rca/tools/citecheck.py 2>/dev/null | head -1)" report.md --corpus <КАТАЛОГ_ЛОГОВ> --require-quote --ledger ./work/worklist.tsv

Вердикты: `ok`, `wrong-content` (строка есть и говорит не то), `out-of-range`,
`missing-file`, `binary-file` (ссылка в двоичный файл — проверять нечем),
`ambiguous` (путь означает больше одного файла корпуса — проверка называет
кандидатов и **не выбирает** между ними), `no-quote` (нет дословной цитаты;
только при `--require-quote`), `не-ссылка` (похоже на файл, но в корпусе такого
нет). `ambiguous` лечится в отчёте, а не в инструменте: путь пиши от корня
корпуса, вместе с именем машины — ровно так, как он записан в рабочем списке. Ссылкой считается только
то, что **разрешилось в корпусе**, — поэтому файл без расширения (например,
`kernring` или `batchjob-stage-2019-03-11`) цитируется так же, как `.log`.
Имена здесь выдуманы: примеры в навыке не должны называть файлы разбираемого
корпуса.

`--ledger` печатает числа условия остановки: открытые строки, непроверенные
ссылки, плохие цитаты, не-ссылки, дефекты исходов и дефекты отчётных наблюдений.
Возвращает 0 только когда все они нули. Диапазон `g041-g068` в первой колонке
закрывает все строки между.

**`--delivered <файл>` — что сдаёшь, то и проверял.** `report.md` — черновик;
пользователь видит только последнее сообщение. Положи текст, который собираешься
отдать, в файл и передай его этим ключом:

    python3 "$(ls .qwen/skills/log-rca/tools/citecheck.py ~/.qwen/skills/log-rca/tools/citecheck.py 2>/dev/null | head -1)" report.md --corpus <КАТАЛОГ_ЛОГОВ> --delivered handover.md

Поставка проходит **ту же** проверку по тому же корпусу, и сверх того печатается
блок `ПОСТАВКА` со строкой «НЕ БЫЛО В ПРОВЕРЕННОМ НАБОРЕ» — это ссылки поставки,
которых не было среди подтверждённых. Возврат ненулевой, если у поставки есть
плохие вердикты, непроверенные ссылки, дефекты `исход:` или дефекты
`report_evidence` (например, пропавшая `атрибуция:`). Сдал черновик дословно —
блок пуст и возврат 0.

Почему одного подмножества мало: измерено на сохранённом прогоне, где черновик
дал 100 %, а поставка — 77,9 %. Из провалившихся ссылок поставки **20 из 21 уже
были** в подтверждённом наборе: тот же файл, та же строка, перепечатанная под
другой фразой. Проверять надо не только адрес, но и утверждение вокруг него —
поэтому поставка перепроверяется целиком, а подмножество ловит вторую половину:
адрес, который в черновике не проверялся вовсе.

### Закрытые строки отчёта — и как их читает проверяющий

Каждый блок `Н-n` несёт две отдельные строки. Грамматика закрытая, чтобы её можно
было разобрать машинально, без чтения смысла:

    атрибуция: установлена|не установлена
    исход: успех|попытка|норма

Ровно одно слово из словаря, и в строке больше ничего. Допускаются только
оформление (`**жирный**`, маркер списка, точка в конце) и регистр; **любая
приписка после слова строку не проходит** — «успех, но не доказан» это уже
четвёртый исход, а их три. Две строки `исход:` или `атрибуция:` в одном блоке —
тоже ошибка: проверка не выбирает за тебя.

`атрибуция: не установлена` не переносит наблюдение в отклонённые кандидаты:
событие остаётся находкой, просто виновник не назван.

`citecheck` печатает исходы, называет находки без исхода (это дефект сдачи,
как и отсутствующий раздел) и складывает их в ответ всего отчёта: самый сильный
исход и есть вердикт — `успех` → компрометация, `попытка` → атаковали, но успех
не подтверждается, `норма` → чисто. Если в отчёте есть раздел «ВЕРДИКТ» и он
говорит другое, проверка называет противоречие и не даёт зелёного.

Раздел «Отклонённые кандидаты» обязателен и непуст: он читается блоками `К-n ·`,
номера `К-n` не должны повторяться, в каждом блоке ровно один `исход:` и хотя бы
одна `файл:строка` с дословной цитатой. Раздел «Покрытие» тоже обязателен и
непуст, читается таблицей `| путь | статус | улики |`. Статусы
`наблюдение`/`факт` обязаны нести дословную цитату из **того же пути**, что указан
в первой колонке. Статусы `пусто`, `двоичный`, `нечитабельно`, `не смотрел` могут
быть без адреса содержимого, но сам путь обязан однозначно существовать в корпусе,
а третья колонка — это не свободная фраза, а **ровно один** закрытый токен доступа:
`байт=0`, `формат=двоичный`, `ошибка=<код>` или
`причина=лимит`/`дубликат`/`область`/`пропуск`/`выборка`. Проверка сверяет `пусто`
с нулевым размером файла и `двоичный` с двоичным признаком; у `нечитабельно` и
`не смотрел` достаточно существующего пути и закрытой причины. Переход `..`,
отсутствующий/неоднозначный путь, чужая цитата или повтор пути покрытия не проходят.

Машинно всё это лежит в JSON под ключами `outcomes` (`findings`, `missing`,
`invalid`, `implied`, `stated`, `contradiction`) и `report_evidence`
(`attribution`, `rejected`, `coverage`, `blocking`).

### `triagecheck.py` — чем закрыты строки рабочего списка

    python3 "$(ls .qwen/skills/log-rca/tools/triagecheck.py ~/.qwen/skills/log-rca/tools/triagecheck.py 2>/dev/null | head -1)" --worklist ./work/worklist.tsv --rules ./work/rules.tsv --corpus <КАТАЛОГ_ЛОГОВ>

`--ledger` отвечает «все ли строки закрыты». Этот — «**чем** они закрыты», и
раскладывает закрытые строки по трём корзинам:

| корзина | когда |
|---|---|
| **поимённо** | в вердикте есть своя ссылка `путь:N` с дословной цитатой, либо это `D` со ссылкой на блок находки (его улики проверяет отчёт) |
| **по правилу** | вердикт помечен номером правила — `N #R1 фон` |
| **без опоры** | ни того, ни другого. Вердикт «N фон: n=1» проходит леджер и не несёт ни одной проверяемой опоры |

Третья корзина — то, ради чего инструмент существует, и она печатается долей от
всех закрытых строк. Рядом печатается **самое широкое правило** и его доля.

**`work/rules.tsv` — правила массового закрытия.** Формат — TSV, `#` комментарий:

    R1	ось=cat && n<=3	N	токен<=24	каталог форм: доля до окна и внутри совпала
    R2	путь~*/rules/*	N	адрес~10.*	список наблюдения, а не наблюдение
    +R1	g0041	edge-1/logs/relayd.log:145	«дословный кусок строки»	правило

Первая колонка правила — его номер (`R1`, `R2`…), дальше условие отбора, вердикт
(`N` или `X` — дефект массовым не бывает), **утверждение** и основание словами.
Строка, начинающаяся с `+`, — **квитанция**: ровно пять TSV-столбцов — номер
правила, `id` строки рабочего списка, ссылка, дословная цитата и закрытое решение
`правило` или `кандидат`. Шестой столбец — ошибка, а не комментарий.
`кандидат` означает, что процитированная строка стала самостоятельным подозрением:
проверка не засчитает её правилу и попросит перенести строку в находку, даже если
эта квитанция не была среди обязательной выборки. Квитанция для неизвестного
правила, неизвестной строки, строки вне покрытия правила или повторной пары
`правило+строка` — дефект сдачи; адрес и цитата проверяются там, где строка
рабочего списка известна. Имена в примерах выдуманы.

**Язык условий закрыт.** Слева от оператора стоит только то, что посчитал шаг 1:

| поле | что это |
|---|---|
| `ось` | колонка «ось» рабочего списка: `rare`, `cat`, `new`, `peak`, `rate`, `bg`, `code`, `level`, `burst`, `edge` |
| `хост` | машина — из имени `worklist-<хост>.tsv`, иначе первый компонент пути |
| `путь` | путь ссылки целиком (с `~` — глоб) |
| `файл` | только имя файла (с `~` — глоб) |
| `n` | частота формы из колонки «частота»; операторы `=`, `!=`, `<=`, `>=` |
| `всплеск` | `да`/`нет` — уместились ли все вхождения в узкое окно |
| `id` | одна строка или диапазон: `id=g0041-g0068` |

Операторы: `=`, `!=`, `<=`, `>=`, `~` и `!~` (глоб). Термы соединяются `&&`,
альтернативы — через `|`: `ось=cat|level`. **Оператора над текстом записи в этом
языке нет.** Регулярка по колонке «запись» — это условие над проекцией, а не над
файлом: слово, которого нет в твоём списке, даст ноль совпадений при любом
содержимом строки. Инструмент такое условие отклоняет как невычислимое.

**Утверждение — четвёртая колонка, и без него правила нет.** Условие отбора
говорит, КАКИЕ строки правило закрывает; утверждение говорит, ЧТО оно про них
заявляет. Язык тот же (`поле оператор значение`, `&&`, альтернативы через `|`),
поля другие — и все они меряются по **настоящей строке, прочитанной из файла**:

| поле | что меряет | операторы |
|---|---|---|
| `токен` | длина самого длинного куска строки между разделителями (`-`, `_`, `*` разделителями не считаются: случайная метка именно на них и держится) | `<=`, `>=`, `=`, `!=` |
| `код` | каждый трёхзначный код результата (100–599), стоящий отдельным числом | `=`, `!=`, `~`, `!~` |
| `адрес` | каждый IPv4 в строке | `~`, `!~`, `=`, `!=` |

Для `код` и `адрес` утверждение — про **каждое** найденное значение: `код=200`
значит «все коды в строке равны 200», а не «хотя бы один».

Словами утверждение не пишется, и поле отбора в этой колонке тоже не принимается:
`файл~*.log` — это то, как ты выбрал строки, а не то, что ты про них заявил.
Разница не формальная. Условие над колонкой «запись» инструмент отклоняет потому,
что колонка — проекция; утверждение читает **файл**, поэтому оно измерение и
может не подтвердиться.

Инструмент считает утверждение **на всех строках, которые правило закрыло** (одно
чтение на файл), печатает измеренный максимум рядом с утверждением и называет
каждую строку, где оно не держится, вместе с самой строкой. Дополнительно он
требует квитанцию на **граничную строку** — ту, что ближе всех к нарушению. Это
и есть цена поднятой границы: подняв её, ты обязан прочитать и процитировать ту
самую строку, ради которой поднимал.

**Оси `new` и `peak` правилом не закрываются.** Обе — утверждение про время
(«участника не было в первой половине потока», «мера ушла втрое и вернулась»), а
не повторяющаяся форма. Класса нет — значит нет и правила; такие строки идут
поимённо. Инструмент их называет и не даёт зелёного.

**Цена правила — квитанции.** Правило, закрывшее N строк, обязано принести
`k = min(N, max(3, ⌈√N⌉, F))` квитанций, где `F` — сколько разных файлов оно
закрывает по осям `rare`/`new`/`peak`. **Какие именно строки — называет
инструмент**, из покрытия самого правила: иначе проверялись бы ровно те строки,
которые и так были прочитаны. Экскурсии идут в выборку первыми, и каждый файл,
откуда правило такую строку выбрасывает, получает хотя бы одну. Цитату проверяет
тот же `citecheck` — вердикт не `ok` квитанцию не закрывает. Из каждой
подтверждённой реальной строки инструмент дополнительно вытаскивает ограниченный
набор пар `key=value` (`type=...`, `unit=...`, `code=...`) и печатает их рядом с
квитанцией в обычном выводе и JSON: это не парсер формата, а короткий чек-лист,
что именно читалось.

Возвращает 0, только когда пусты все корзины «без опоры», у каждого правила есть
вычислимое условие И вычислимое утверждение, утверждение держится на всех
закрытых строках, ни одна `new`/`peak` строка не закрыта правилом, а все
квитанции на месте, подтверждены, стоят по адресу своей строки и помечены
`правило`, а не `кандидат`.

## Если shell запрещён

`run_shell_command` бывает запрещён политикой. Инструмент отказал — **не повторяй
и не проси разрешения**, сразу бери замену; сессия неинтерактивная.

| вместо | делай |
|---|---|
| `logmap.py` | `list_directory`/`glob` по всему дереву; затем по каждому файлу 4–6 окон по ~150 строк (начало, середина, конец) — так ты получишь формат, охват и «нормальный фон». Список файлов веди сам и закрывай каждый явно |
| `grep -n` | поиск по содержимому **узким, редким литералом**. Общий шаблон вернёт десятки тысяч строк и убьёт прогон |
| `sed -n 'A,Bp'` | `read_file` с `offset`=A−1 и `limit`=B−A+1 |
| `zcat` | многие реализации `read_file` не умеют `.gz`. Не умеет — так и напиши в разделе «Чего я не знаю», назвав файл. Молча пропустить сжатый файл нельзя: в нём регулярно лежит половина улик |
| `citecheck.py` | перечитай каждую цитату по адресу вручную; не подтвердилась — удали утверждение |

## Логи не в файлах

- **journald**: `journalctl -o export`/`-o json` даёт записи, разделённые пустой
  строкой; одна запись — это `файл:N-M`, а не одна строка.
- **Kubernetes**: `kubectl logs --previous` — то, что писал упавший контейнер;
  `kubectl get events --sort-by=.lastTimestamp` часто единственный источник того,
  кто и когда менял число реплик.
- **Loki/Elasticsearch**: тяни узкое окно по времени и одному лейблу, сохраняй в
  файл и дальше работай файлом — иначе ответ поиска съест контекст.
- **Удалённый хост**: забери логи на диск (`scp`, `ssh 'cat …' > file`) и работай
  локально. Читать по `ssh` в контекст построчно — самый быстрый способ убить
  прогон.

Ничего из этого не получилось — отчёт всё равно обязателен (правило 2).

## Step 1: the map, the worklist and the axes

**Your first action is this command, before any `ls`, `grep` or `read_file`:**

    python3 <SKILL_BASE_DIR>/tools/logmap.py <LOG_DIR> --out ./work

`<LOG_DIR>` — substitute the path from the task in full, exactly as written
there (usually it is absolute). A `./logs` directory in your working directory
most likely does **not** exist. If the command answered `нет такого каталога`,
that is a path error and **not** a missing tool: fix the path and retry. Falling
back to the workaround (section "If the tool is missing") is allowed only after
the correct path has failed too.

It pulls not a single log line into context: the raw material goes to `./work/`,
the answer holds only the map. Copy the path in full: a short
`tools/logmap.py` **will not work** — the `tools/` directory lives inside the
skill, and your working directory does not.

It writes three files, and all three must be read: `work/map.txt` — the map;
`work/worklist.tsv` — the worklist, ≤250 lines, read in one call;
`work/axis3.tsv` — what changed in rate and what did **not**.

**If the bundle was collected from several machines, that is N corpora, not
one.** The tool determines the split from the path structure by itself and, on
finding more than one host, additionally writes `work/hosts.tsv` (the host list)
and a pair of files per host — `work/worklist-<хост>.tsv` and
`work/map-<хост>.txt`. Then `work/map.txt` is **only an index**: a header, the
list of machines and where to go. **Work one host at a time** and read ITS pair
of files, not the shared `work/worklist.tsv`: the shared one is the ledger for
`citecheck --ledger`, it holds the lines of all hosts at once, and on a bundle of
N machines it has N ceilings, not one. The map is split for the same reason, and
that reason is measured: on a large multi-host dump an unsplit `map.txt` weighed
**about two million tokens**, and two thirds of that was configs under 4 KB
quoted verbatim. If one host's map still did not fit the ceiling, the surplus
files stay in it as **one line each** (name, size, genus, number of shapes) —
and the map states how many files it folded and how many bytes it did not show.
Nothing disappears silently. The ceiling is lifted with `--map-cap 0`.
Why it works this way: a 250-line ceiling divided by a couple of dozen hosts is
about ten lines per machine. Measured on a real multi-host dump: the very same
code, aimed at the whole bundle, reached **three times fewer** interesting files
than the same code aimed at a single machine — and on a file where the sought
lines number a handful it produced **none at all**. The tool is not blind — its
budget is diluted. If the split was guessed wrong, you have the last word:
`--single-host` (the whole corpus is one machine) or `--host-depth N` (that many
path components form the host).

**Always write the path in a reference from the corpus root — including the
machine name.** Not `logs/relayd.log:145` but `edge-1/logs/relayd.log:145` (the
names here are invented on purpose: this file ships with the skill, and an
example in it must not be a path from a real corpus). That is how the worklist
writes it too: copy it from there, do not shorten it. The same file name lives on
many machines — on a real dump **almost half** of all file names occurred on more
than one, and the most ordinary ones (`auth.log`, `facts.json`) on most machines
at once — and a shortened path does not say which of them you wrote about. The
check does not confirm such a reference: verdict `ambiguous`.

**Do not search for the word `ERROR`.** You do not know in advance what the
severity level is called in this corpus: every file has its own scale — its own
word, its own number, its own status code, and sometimes severity is not
expressed as a level at all. There is no ready list of values here and there
cannot be: substitute somebody else's list and you will search for what is not in
these files and miss what is.

The severity axis is **derived from the data itself** for every file and lives in
`work/map.txt` — as a histogram: which values occur and how many times. Read the
histogram and reason from it. **The rarest value of a scale is not necessarily a
defect, and the most frequent is not necessarily background;** check, do not
assume.

**A line with axis `code`, `level`, `burst` or `edge` is a "handhold", not an
anomaly.** Such lines go to files that have NO rare shapes: either almost every
record has its own shape (a typical website access log under a scanner), or there
are only two shapes in total. They cannot be ranked, so the line was chosen by a
fallback axis — a response code unlike the rest, the rarest value of the scale,
the fullest hour, the first and last record. This is the address "open it and
look around", not a claim.
**Open them just as seriously as `rare`:** before this axis a file in which
almost every record has its own shape got NOT A SINGLE worklist line — and that
is exactly what a journal looks like when somebody else's tool worked through it
long and monotonously: it has no "rare" shape because nothing there is rare.
Measured: **the more completely a machine is captured, the less step 1 looked at
it** — not a paradox but a direct consequence of ranking by rarity.

**A line with axis `new` is a NEW PARTICIPANT, not a rarity.** An address that
was not present in the first half of the stream. Rarity and novelty are different
claims, and confusing them is expensive: measured on a real VPN concentrator
journal. Its rarest addresses are internal pool addresses, 4–16 records each; the
address for whose sake the file was worth opening at all occurs 51 times and
stands out in no way by rarity. But it first appears at 78 % into the file, while
all the others are there from the first lines. Such a line points at **session
establishment**: open it and read the block whole, from the first record with that
address to the end of its session.

**A line with axis `peak` is a MEASUREMENT OUTLIER.** An hour in which the median
of a numeric field went up at least threefold from the usual for that file **and
came back** — the neighbouring hours are normal. The rate axis (`S…`) compares
the first hour with the last and does not see such a spike at all: to it, that is
"nothing changed". Measured on a real metrics sampler journal: the CPU load
fraction holds at 1.0 for one whole hour against the usual 0.065 for the file
(×15.5), while the hours left and right of it are normal — and before this axis
the tool wrote about such a file `фон, не сдвинулось`. The line gives you an
HOUR; read it whole, not one record.

**Every file under 4 KB has been read in full and already lies in `work/map.txt`
verbatim — with line numbers** (when split by hosts, in
`work/map-<хост>.txt`). The number to the left of `|` is the **physical line of
the file**, the very one the check reads: `путь:N` from that block is a legal
reference, and you may quote from it without opening anything again. One
exception: if a line is long, the map truncated it and marked it `обрезано` —
re-read such a line in full, a truncated fragment will not pass the check. If a
host's map hit the ceiling, some of those files are folded to a single line —
they are named there, with size and number of shapes, and then **open the ones
you need yourself**; the map states how many files it folded. These are usually
configs and notes — they say what the state **is supposed** to be. Their value is
that a log line by itself is not evidence: it becomes evidence only paired with a
recorded expectation it deviates from. So first read what has been declared
normal, and only then decide what in the log deviated from it.

**A compressed file is an ordinary file.** `zcat F | grep -n …`, `zcat F | sed -n
'A,Bp'`. A line number inside a `.gz` is the number in the **decompressed**
stream; that is how `citecheck` counts them too.

**A range `файл:N-M` is a legal reference** (up to 40 lines). For a multi-line
record — a stack trace, a journald block, docker-json — it is **mandatory**.

A file marked in the map as `время: НЕТ` does not enter the rate table at all.
Its silence proves nothing — evidence from there has to be taken by eye.

**`род` in the map: `поток` or `состояние`.** A stream has a time axis: records
go forward in time, and time does not run backwards. A state has no time axis —
it is a config, a rule set, a dump, key material. The axes "rare value",
"appeared late" and "spike" are not computed on a state: without time they mean
nothing.

**`состояние` does not mean "unimportant".** About such a file exactly one thing
is known in advance: it has no clock. The heaviest piece of evidence in the dump
may well lie precisely there — a planted key in `authorized_keys`, a modified
`sshd_config`, an uploaded web shell, a script forgotten on disk. These are
pieces of evidence that simply have no clock, and `состояние` **never** means
"discard". They get a separate, small share of the worklist so that they do not
drown: in a real dump there can be 20 times more files from `/etc` than logs
(measured on a real machine), and with an equal split they took almost half the
list. Read them yourself — there are few.

And conversely: **a rule set is not evidence.** A file like
`suricata/rules/*.rules` or a blocklist consists of addresses the sensor was
told to **look for**. A rare address from there is not something the host did. Do
not confuse a watch list with an observation.

**`кадрирование` tells you what counts as one record.** `line` — a line;
`block` — a paragraph between blank lines; `anchor` — from one timestamp to the
next; `key:<поле>` — consecutive lines with the same value of that field. The
last one is about auditd: one event there is several lines sharing
`msg=audit(<время>:<номер>)`, and the command arguments lie on a different line
than the call itself. The tool has already glued them; the reference stays a range
of physical lines, so the quote is still verifiable.

## Перепись изменений состояния

The two previous tools judge **your text**: `citecheck` — your references,
`triagecheck` — your verdicts. Neither sees what you kept quiet about. The third
comes from the other side — from the corpus to the report:

    python3 <SKILL_BASE_DIR>/tools/statecheck.py --corpus <LOG_DIR> --report work/report.md

It walks the corpus once and writes out **every** record of a state change: a
service installation, a scheduler task, an autostart entry, a WMI subscription,
an account creation and a group edit, a firewall rule, an antivirus exclusion, an
audit policy change, a log clear. The records are folded into groups by the pair
"file + the subject that made the change". One reference closes a whole group —
but somebody else's subject does not land in your group, so fifteen routine
service installations cannot be closed together with a sixteenth made by someone
else.

This is exactly the check that was missing: a report can fail it over a finding
it **did not make**.

## `worklist.py` — курсор по леджеру и его свидетель

    python3 <SKILL_BASE_DIR>/tools/worklist.py next    --work ./work --batch 20 [--axis rare]
    python3 <SKILL_BASE_DIR>/tools/worklist.py verdict --work ./work --from-stdin
    python3 <SKILL_BASE_DIR>/tools/worklist.py status  --work ./work
    python3 <SKILL_BASE_DIR>/tools/worklist.py verify  --work ./work
    python3 <SKILL_BASE_DIR>/tools/worklist.py reseal  --work ./work --reason '<что случилось>'

`next` выдаёт неразобранные строки пятью колонками, которые читает гейт, БЕЗ
колонки `запись`: измерено — это 313 символов из 440,9 средней строки, 71 %
файла, и ни один гейт её не читает. `verdict --from-stdin` принимает строки
`id<TAB>ячейка` и заменяет колонку 2 названной строки, и больше ничего.
`--ledger <имя>` переключает на `worklist-<хост>.tsv`: у каждого леджера свой
журнал.

**ЕДИНСТВЕННЫЙ ПУТЬ ЗАПИСИ, И ТЕПЕРЬ ЭТО ПРОВЕРЯЕТСЯ.** До v42 граница была
описана словами и не была защищена: в платном прогоне `20260827T173511Z-v41`
строку `g228` родительская сессия поправила файловым редактором после указания
«читай леджер через КУРСОР, никогда как файл», и ни один гейт этого не увидел —
`triagecheck` и `citecheck --ledger` читают получившийся файл, а два файла с
одинаковыми байтами получают одинаковую оценку, кто бы их ни написал. Вердикт,
не прошедший через курсор, миновал и все его отказы: неизвестный id,
вердикт-заглушка, таб, который подделывает колонку.

Поэтому курсор пишет рядом свидетеля:

| файл | что в нём |
|------|-----------|
| `work/worklist.provenance.jsonl` | только на добавление. Запись 0 (`genesis`) — снимок всех id и их вердиктов, каким курсор увидел леджер впервые; каждая следующая несёт ровно то `{id: ячейка}`, что записала, и sha256 всего леджера после записи. `prev` каждой записи — `link` предыдущей, `link` — sha256 канонического JSON самой записи: звено нельзя выкинуть, переставить или вставить в середину |
| `work/.worklist-witness.key` | 32 случайных байта, права 0600, создаёт курсор при первой записи. Каждая запись несёт `mac = HMAC-SHA256(key, link)` |

`triagecheck` проигрывает цепочку и сравнивает с файлом. Два отдельных
блокирующих дефекта:

* `ledger_write_off_cursor` — названная строка несёт вердикт, которого курсор не
  писал. **Строку не удаляй и вердикт не стирай**: разбор уже сделан, удаление
  строки — это регрессия v37, а не починка. Пропусти тот же текст через курсор
  дословно; `verify` печатает готовую команду с ячейкой внутри.
* `ledger_witness_broken` — журнала нет, он пуст, обрезан, не разбирается, без
  базового снимка, с порванным звеном или с неверным MAC; либо изменены байты вне
  колонки вердикта; либо строка исчезла из леджера. Свидетеля, которого нельзя
  прочитать, гейт считает отказом, а не пропуском.

**Почему подделать не так просто.** Пересчитать цепочку — арифметика, доступная
любому; MAC без ключа — нет. Поэтому правка руками плюс дописанная от руки
запись журнала — очевидный способ «заодно написать бухгалтерию» — не проходит.
СЛУЧАЙНЫЙ обход, тот самый, что и случился, не порождает вообще ни одной записи,
поэтому ловится всегда.

**Что всё ещё может сделать враждебный агент — прямо, а не спрятанно** (та же
честность, что у `logmap.write_worklist_manifest`): процесс может прочитать любой
файл, который может записать. Агент, решивший подделывать, способен прочитать
ключ, пересчитать цепочку и выпустить действительную запись; либо удалить журнал
и сделать `reseal`. Ни то, ни другое не предотвращено, и никакой ключ в этой
песочнице этого не предотвратит. Свидетель покупает другое: дешёвого случайного
обхода больше нет, а дорогой обязан либо тронуть ключ, который не назван ни в
одном брифе, ни в SKILL.md, ни в этом файле-справке, либо оставить запись
`reseal`, называющую себя в сданном рабочем каталоге. Свидетельство, а не
неподделываемость.

**Восстановление.** `reseal` — выход для потерянного свидетеля, а не для
пойманной правки: пока журнал цел и проверяется, он ОТКАЗЫВАЕТ и показывает
команду курсора. Он требует `--reason`, сохраняет прежний журнал как
`*.superseded-N`, и `verify`/`triagecheck` печатают «ПЕЧАТЬ СВИДЕТЕЛЯ
ПЕРЕСТАВЛЕНА» с причиной даже при итоге «всё в порядке».

**НЕТ MANIFEST — НЕТ ПРОВЕРКИ.** Рабочий каталог без журнала И без
`worklist.manifest.json` считается «без свидетеля» и оценивается как раньше: это
то же правило, что у `citecheck.worklist_removed`, и по той же причине — гейт,
который валится на самодельных фикстурах, выключают. Прогон, который платят,
всегда начинается с `logmap`, поэтому manifest там есть всегда, а значит
отсутствие журнала при закрытых строках — это отказ.

## `reportcheck.py` — контракт заказчика

    python3 <SKILL_BASE_DIR>/tools/reportcheck.py work/report.md
    python3 <SKILL_BASE_DIR>/tools/reportcheck.py work/report.md --contract <профиль.json>
    python3 <SKILL_BASE_DIR>/tools/reportcheck.py work/report.md --json

The three gates above all grade the SKILL'S own format — `Н-n` blocks, `улики:`
lines, the coverage table, the census of state changes. **None of them has ever
read the CUSTOMER'S requirements**, and those are not in the skill: they arrive
in the prompt. Measured, and this is why the tool exists: the paid run
`20260827T173511Z-v41` exited 0 on `citecheck`, `triagecheck` and `statecheck`
and was called accepted. The independent review then found the delivered report
broke **five** written requirements from the operator's own prompt — no
`PROVEN`/`REPORTED`/`INFERENCE` labels, no inventory of addresses/names/paths/
hashes with the source of each, no section for what the logs LACK, `ВЕРДИКТ`
placed FIRST instead of last, and a verdict of «компрометация» beside an
admission that owner-versus-attacker was undetermined. Three green gates, zero of
the five checked.

`reportcheck` blocks on each violation **separately**, by name, with a count:

| дефект | что значит |
| --- | --- |
| `assertion_unlabelled` | утверждение со ссылкой `файл:строка` без метки `PROVEN`/`REPORTED`/`INFERENCE` |
| `label_unknown` | метка вне этих трёх |
| `inventory_missing` | нет раздела-инвентаря адресов, имён, путей и хешей |
| `inventory_unsourced` | запись инвентаря без источника `файл:строка` |
| `missing_data_section_absent` | нет отдельного раздела о том, чего в логах не хватает |
| `verdict_section_absent` | нет раздела `ВЕРДИКТ` |
| `verdict_not_last` | раздел `ВЕРДИКТ` не последний в отчёте |
| `verdict_not_one_of_three` | вердикт не равен ровно одному из «скомпрометирована», «атаковали, но не доказано», «чисто» |
| `verdict_uncited` | в разделе `ВЕРДИКТ` нет ни одной ссылки `файл:строка` |

An **assertion** is the unit a human labels: one bullet, one table row, one
paragraph — and only one that carries a `файл:строка` reference. Prose that
claims nothing checkable is not dragged into the gate. The inventory, the
missing-data section, the coverage table, the record window and the verdict
itself are exempt from the label rule (`labels.exempt_roles` in the profile):
their entries are sourced, not labelled.

### Почему требования — это данные, а не текст внутри проверяльщика

The skill's format is a constant, so `citecheck` hardcodes it, and that is right.
The operator's requirements are **not** a constant: they are one customer's
order, in that customer's prompt. The next customer asks for other labels, other
mandatory sections, another verdict vocabulary. If those lived as literals inside
the gate, serving a second customer would mean editing the gate — and every edit
puts the first customer's sealed, paid run at risk of quietly changing meaning.

So the requirements are a declarative **profile** on disk and the tool is only
the engine that reads one:

    <SKILL_BASE_DIR>/reference/report-contract.corporate.json

The same reasoning gave the population-scope guard its own profile:

    <SKILL_BASE_DIR>/reference/population-scope.json

It holds the words that NARROW a population in the report's language
(«внешн», «публичн», «извне», «удал[её]нн») and the spellings this corpus uses
for «no address» (`-`, `127.0.0.1`, `::1`, `localhost`, `fe80:`). `citecheck
--scope-profile` selects it. Neither list is a property of the engine: the
language belongs to the customer, the spellings to the corpus.

`--contract` selects it; the corporate profile is the default. Copy it, edit it,
point the flag at the copy — a new customer gets a **new profile, never a new
gate**.

### Отказ, а не пропуск

Fail-closed by construction: a report that cannot be read, an empty report, a
profile that will not parse or that lacks the keys the engine needs — all exit
**2**. Exit 0 stopped meaning «проверено» exactly once, in the v41 run, and must
never mean it again. `stopcheck` runs this gate beside `triagecheck` and
`citecheck`, so delivery is blocked on it whether or not the model remembered.

**Scope: structure only.** It answers «имеет ли отчёт заказанную форму?», not
«следует ли вердикт из находок?». The latter is a separate, semantic gate step,
and the seam is already cut for it: `CHECKS` in `reportcheck.py` is a registry of
per-defect functions over one parsed section model, and a verdict-support check
plugs in as one more entry beside `verdict_not_one_of_three`.

## Бюджет: почему одна широкая команда убивает прогон

**Step 1 has already saved you the whole corpus — do not spend it again.**
Measured on a 649 MB / 4.26 million line corpus: the three residue files take up
**≈29 thousand tokens**, i.e. about five thousand times less than the logs
themselves. Everything you need to choose "where to look" is already there.
Re-reading the whole corpus "to be sure" is the single most reliable way not to
finish the investigation: the context will run out before you reach the second
defect.

Hence the rule: **read from the corpus only the addresses the residue named for
you**, and only with a narrow window. Every trip to the logs is a test of a
specific hypothesis, not reconnaissance.

Budget is **not the number of calls but the size of one call**. There can be any
number of calls; each must be narrow. Runs die not from forty narrow calls but
from one wide one. `read_file` always with a `limit` (≤300 lines). A content
search returns every matched line, not their count: a general pattern over an
800,000-line file kills the whole run.

**Keep state on disk, not in context.** Closed a line — write the verdict into
`worklist.tsv` right away. Context can be lost; a file cannot.

## Почему фазы идут в субагентах

**THE PHASES RUN IN SUBAGENTS IF THE `agent` TOOL EXISTS.** Measured
2026-08-24: a request without the skill — 83,705 bytes, with the skill loaded —
152,245, i.e. the skill body is ≈68 KB IN EVERY request; over 55 turns it goes
up the wire 55 times, the body reaches 1.1 MB, and the provider answers HTTP 200
with a single empty event. A subagent has its own history: a parent that
launched one grew by 1,570 bytes for ALL of its work. Therefore:


## `cite.py` — цитата, которую citecheck примет

    python3 <SKILL_BASE_DIR>/tools/cite.py --corpus <КАТАЛОГ_ЛОГОВ> System.jsonl:263 --contains 3proxy
    System.jsonl:263 — «8817-1001"}}},"EventData":{"ServiceName":"3proxy tiny proxy server","ImagePath":"\"C:\\3pr»

Печатает одну готовую строку на каждый адрес. Цитату строит `quote_example` из самого
`citecheck.py`, поэтому разойтись они не могут: что напечатал `cite.py`, то
`citecheck` зачтёт как `ok`.

Коды возврата: `0` — цитата выдана на каждый адрес; `1` — хотя бы один
отказ (нет файла, строка за пределами, `--contains` не найден, строка слишком
короткая). Отказ печатается в stderr и НИКОГДА не сопровождается выдуманной
цитатой.

**`--contains` — не удобство, а проверка.** Без него вы получите хвост строки —
формально верную цитату, которая ничего не доказывает. А отказ `на строке нет
«X»` — это ровно тот случай, когда ручная цитата тихо утвердила бы ложь.

### Почему это инструмент, а не ещё одно правило в SKILL.md

ИЗМЕРЕНО на полном прогоне winevtx на арме v36
(`sherlock-winevtx-runs-v36-full-r1/20260825T061049Z-v36`, exit 0,
`gates.json verdict=blocking`). Модель НАШЛА атаку: `3proxy` назван 16 раз,
`System.jsonl:263` — 6, вердикт «скомпрометирована» — верный. Из 41 ссылки
зачтено 18: **17 без цитаты вообще, 6 с цитатой собственной прозы вместо
лога**. На `System.jsonl:263` она предложила «входящего доступа, установленная
от имени пользователя root. улики:» — совпало 1 слово из 7 (14 %).

То есть криминалистика была верной, а грамматика — нет. Прочитать правило
внимательнее — именно тот ремонт, который уже провалился: в документации
`quote_example` записаны два прогона, потратившие 40 ходов / 11,15 M токенов и
123 хода на реверс-инжиниринг этой же проверки вместо того, чтобы поставить
пару кавычек. Принцип входного шлюза: ограничь вход там, где он
порождается, и ниже по течению ошибиться будет нечем.

## Resident bytes — почему шаг 0 безусловен

ИЗМЕРЕНО: тело навыка — ≈68 KB В КАЖДОМ запросе; за 55 ходов оно уходит в
провод 55 раз, набирает 1,1 MB, и провайдер отвечает HTTP 200 с одним пустым
событием — прогон умирает. Родитель, который делегирует, вырос на 1 570 байт
за ВСЮ свою работу. Делегация — не оптимизация, а условие того, что прогон
вообще дойдёт до конца.


## `covermap.py` — таблица покрытия, которую не надо писать

    python3 <SKILL_BASE_DIR>/tools/covermap.py --corpus <КАТАЛОГ_ЛОГОВ> --worklist ./work/worklist.tsv --header

Одна строка на каждый файл корпуса. Статус читается с самого файла: `пусто` +
`байт=0` при нулевом размере, `двоичный` по той же проверке, что у `citecheck`,
иначе `наблюдение` с дословной цитатой. Цитату строит `quote_example` из `citecheck.py`,
поэтому что напечатал `covermap`, то `citecheck` зачтёт.

Цитируется СТРОКА, ОТМЕЧЕННАЯ `logmap`, а из нескольких — та, которую триаж
назвал дефектом (`D`). Это не косметика: `citecheck` считает цитату, не попавшую ни
в одну отмеченную строку, блокирующим дефектом — произвольная строка доказывает
только то, что файл открывали. И окно цитаты центрируется на токене, из-за
которого строка попала в рабочий список.

**СТРОКА 1 НЕ ГОДИТСЯ В ОТВЕТ.** `logmap` называет ссылкой группы её ПЕРВЫЙ
элемент, поэтому `путь:1` лежит в рабочем списке почти для каждого файла:
измерено, 81 из 93 строк «наблюдение» последнего чистого прогона цитировали
строку 1, и все прошли. `citecheck` считает это `cov_inadmissible_line` и
блокирует. Множество допустимых строк ЗАМКНУТО — отмеченные строки выше первой,
или строка 1, если она единственная отмеченная, или любая строка файла из двух
строк и меньше, или последняя цитируемая строка, если `logmap` не отметил
ничего. Строка 2 не дешевле строки 1. `covermap.py` выбирает из этого же
множества (`citecheck.coverage_admissible_lines` — одна реализация на
производителя и на проверяющего), поэтому запусти инструмент, а не угадывай.

**Читается тот же поток, что видит читатель.** `.gz` открывается через
`gzip.open` и в `covermap`, и в `citecheck`: цитата — расшифрованный текст, а
не байты сжатого потока.

**«нечитабельно» проверяется.** Если файл читается и даёт цитируемую строку, то
`cov_false_unreadable` блокирует. `covermap` задаёт этот же вопрос тем же кодом
перед тем, как написать «нечитабельно», поэтому его собственный вывод никогда
не блокирует прогон.

**Рабочий список — не более дешёвый ответ.** `logmap` пишет рядом
`work/worklist.manifest.json` со списком id, которые он выдал; `citecheck
--ledger` блокирует на каждом id, которого в сданном списке нет. Строку
закрывают вердиктом, её не удаляют.

**«не смотрел» не выдаётся никогда.** Этот статус не закрывает файл — его нечем
проверить, — так что генерировать его значило бы писать ложь.

### Откуда этот инструмент

Его написала САМА МОДЕЛЬ во время первого прогона, прошедшего все три
гейта (`20260825T173021Z-v37`, файл `work/gen_cov.py`, 73 строки). Получив гейт,
который блокирует любой файл без строки покрытия, она не стала искать дешёвый
путь — она написала генератор. 143 строки на 143 файла: 93 «наблюдение» с
цитатами, 50 «пусто», ноль «не смотрел».

Оговорка честно: сгенерированная таблица делает покрытие механическим.
Это допустимо, потому что задача строки покрытия — «этот файл отвечен, вот
проверяемый адрес», а не «этот файл понят»: думание живёт в находках и
отклонённых кандидатах. Тот же прогон с генератором в руках дал 5 находок и
9 отклонённых кандидатов. **НАХОДКУ ГЕНЕРИРОВАТЬ НЕЛЬЗЯ НИКОГДА.**

## `rollover.py` — окно записей канала

    python3 <SKILL_BASE_DIR>/tools/rollover.py --corpus <КАТАЛОГ_ЛОГОВ> --report --required-only --cite Security.jsonl

Журнал Windows — кольцевой буфер. `EventRecordID` монотонен внутри канала,
поэтому `(max − min + 1)` против числа записей прямо говорит, пропали ли записи
ВНУТРИ окна. Единица счёта — пара (файл, `Event.System.Channel`), а не файл:
в сборных выгрузках Sysmon и Security лежат в одном файле, и общий пролёт по
файлу бессмысленен.

Что означает `нет=N`: файл не содержит всех записей канала внутри собственного
окна идентификаторов. Это НЕ диагноз «вытеснение»: кольцо срезает голову, а не
дырявит середину, так что внутренняя дыра почти всегда — фильтрованная выгрузка.
Замер: в `evtx-attack-samples-jsonl` дыры у 85 файлов из 278, и все они —
подборки событий (15 записей на пролёт 289 607). Вывод для отчёта одинаков в
обоих случаях: внутри окна с дырами отрицательный вывод («такого события не
было») недействителен.

Статусы: `сплошной`, `с-пропусками`, `неприменимо` (пусто, двоичный, не JSONL,
нет поля), `ошибка` (нечитаемо, битый JSONL, идентификатор не число). `ошибка`
блокирует — проверка, которая не отработала, это не «чисто».

## `cite.py --aggregate` — улика на популяцию

`cite.py <путь:строка>` печатает цитату одной строки. `cite.py --file <путь>
--aggregate '<предикат>'` печатает улику на **популяцию** — `агрегат: файл ·
предикат = число · команда`, где число считает сам инструмент.

Зачем отдельная форма, измерено: под проверкой, принимавшей только цитату
строки, утверждение «93 разных источника, 8 из них больше 1000 отказов» было
**нецитируемо**, и модель его удаляла. v36 не прошёл ворота и назвал 12
атакующих IP; v37 прошёл все трое ворот и назвал 4 из 93. Ворота сделали отчёт
хуже — это и есть тот случай, когда дешёвый путь к зелёному оказался неверным.

Почему инструмент, а не правило в SKILL.md: та же причина, что и у цитаты
строки. Число в отчёте и число, которое пересчитает `citecheck`, обязаны
получаться из одного кода — `agg_parse_predicate`, `agg_evaluate` и
`agg_render_citation` живут в `citecheck.py`, а `cite.py` их только вызывает.
Разойтись производителю и проверяющему негде.

Команда в хвосте улики — **рендер** предиката, а не строка, которую кто-то
выполняет: проверка сравнивает её с собственным рендером и не запускает ни её,
ни какую-либо другую. Отчёт — недоверенный ввод; в `citecheck.py` нет ни
`subprocess`, ни `eval`, и тест это проверяет.

Грамматика, предикаты и все причины отказа: `reference/report-format.md`.

## ingest.py — archives and `.evtx` into a corpus

    python3 <SKILL_BASE_DIR>/tools/ingest.py <INPUT>... --out ./corpus

Takes files, directories and archives in any mix. Unpacks `.zip`, `.tar`,
`.tar.gz`, `.tar.bz2`, `.tar.xz`, `.7z` with the entry-count, uncompressed-size,
path-traversal and symlink guards recovered from the retired logalyzer ingest;
converts `.evtx` to JSONL; copies text and `.gz` through; ignores `__MACOSX`
sidecars. Nested archives up to three deep.

MEASURED on a real 6 MB `winevt.zip`: 296 entries → **143 channels in 3.4 s**,
50 of them empty, one NTFS `$I30` artefact correctly refused.

Every input is accounted for in `corpus/_ingest-manifest.tsv`
(`источник · результат · как · заметка`). Skips are printed AND fail the run;
`--keep-going` only after a human has read the list.

`.evtx` needs a converter — the `evtx_dump` binary, or
`pip install python-evtx xmltodict`. Candidates are tried in order and proven by
their OUTPUT, never by `which`: a broken shim on PATH can shadow a working
binary. When none works, the skip names both cures; it never reports the channel
as empty.
