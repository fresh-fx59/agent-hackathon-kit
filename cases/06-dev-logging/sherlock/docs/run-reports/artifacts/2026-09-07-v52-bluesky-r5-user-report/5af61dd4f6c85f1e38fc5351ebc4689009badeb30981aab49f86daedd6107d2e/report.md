# Отчёт о расследовании инцидента

Корпус: `BlueSkyRansomware.jsonl` (469 записей, один файл; пути ниже указаны относительно `./corpus`). Наблюдаемый поток — журналы Windows-хоста `DESKTOP-7EQVM78` за 21.04.2024 00:32:42 – 23.04.2024 10:11:10 UTC: канал Application (336 записей, MSSQLSERVER и системные службы) и канал Windows PowerShell (133 записи). 22.04.2024 в корпусе событий нет.

## Находки

### Н-1 · Массовые отказы входа по учётной записи `sa` с клиента 87.96.21.84 (EventID 18456, 44 записи)

> [!INFERENCE]
> **что сломано:** в журнал приложения записаны 44 отказа входа в SQL Server под учётной записью `sa`, все — от клиентского адреса 87.96.21.84. Отказы образуют две плотные волны: 22 записи 21.04.2024 в 04:51 и 22 записи 23.04.2024 в 09:59–10:00.

корневая причина: текст каждой записи называет причину отказа — пароль для предъявленного имени входа не подошёл; сочетание одного имени учётной записи, одного адреса клиента и миллисекундной плотности событий — признак автоматизированного подбора пароля, а не разовых ошибок пользователей.

- [!PROVEN] В корпусе ровно 44 записи EventID 18456: `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18456) = 44` (см. улики).
- [!PROVEN] 22 из них старше 23.04.2024 (волна 1, строки 164–185): `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18456, Event.System.TimeCreated.#attributes.SystemTime<=2024-04-22T23:59:59) = 22` (см. улики).
- [!PROVEN] 22 из них приходятся на 23.04.2024 и позже (волна 2, строки 432–453): `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18456, Event.System.TimeCreated.#attributes.SystemTime>=2024-04-23T00:00:00) = 22` (см. улики).
- [!PROVEN] В процитированных записях обеих волн EventData несёт одинаковую тройку: имя учётной записи `sa`, текст `Reason: Password did not match that for the login provided.` и адрес клиента `[CLIENT: 87.96.21.84]` (см. улики: строки 164 и 432).
- [!INFERENCE] Это перебор пароля (подбор), а не отдельные ошибки людей: имя входа одинаковое, адрес клиента одинаковый, вся волна 1 укладывается в ~0,08 с (04:51:02.988886Z–04:51:03.066977Z), волна 2 — в ~0,14 с (09:59:54.644152Z–09:59:54.784581Z). Чего не хватает, чтобы считать доказанным: сведений о том, кто управлял клиентом 87.96.21.84, и журналов вне этого файла (ERRORLOG SQL Server, сетевые журналы).

улики:
- агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18456) = 44 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "18456") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
> [!PROVEN]
> агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18456, Event.System.TimeCreated.#attributes.SystemTime<=2024-04-22T23:59:59) = 22 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "18456") catch false) and (try (.Event.System.TimeCreated."#attributes".SystemTime != null and (.Event.System.TimeCreated."#attributes".SystemTime|tostring) <= "2024-04-22T23:59:59") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`

> [!PROVEN]
> агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18456, Event.System.TimeCreated.#attributes.SystemTime>=2024-04-23T00:00:00) = 22 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "18456") catch false) and (try (.Event.System.TimeCreated."#attributes".SystemTime != null and (.Event.System.TimeCreated."#attributes".SystemTime|tostring) >= "2024-04-23T00:00:00") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
- [!PROVEN] BlueSkyRansomware.jsonl:164 — «#attributes":{"Qualifiers":49152},"#text":18456},"Version":0,"Level":0,"Task":4,"Opcode":0»
- [!PROVEN] BlueSkyRansomware.jsonl:164 — «ata":{"Data":{"#text":["sa"," Reason: Password did not match that for the login provided."»
- [!PROVEN] BlueSkyRansomware.jsonl:432 — «#attributes":{"Qualifiers":49152},"#text":18456},"Version":0,"Level":0,"Task":4,"Opcode":0»
- [!PROVEN] BlueSkyRansomware.jsonl:432 — «at for the login provided."," [CLIENT: 87.96.21.84]"]},"Binary":"184800000E000000100000004»

атрибуция: не установлена
исход: попытка

чем опровергал: проверка «это разовые ошибки легитимных пользователей» убивается плотностью волн (22 события за ~0,08 с и ~0,14 с) и единственным адресом клиента во всех просмотренных записях; проверка «подбиралась другая учётная запись» — во всех записях имя входа именно `sa`. Исход «попытка», а не «успех»: сами записи 18456 результата не показывают; что произошло после волн, разобрано в Н-2.

что делать сейчас: сменить пароль учётной записи `sa`, отключить SQL-аутентификацию либо закрыть порт 1433 для ненадёжных сетей; включить аудит входов (канал Security, ERRORLOG); по сетевым журналам установить владельца адреса 87.96.21.84 и проверить, с каких ещё адресов шли подключения к 1433.

### Н-2 · Успешный вход `sa` с клиента 87.96.21.84 и записи изменения конфигурации `xp_cmdshell` 0→1 (23.04.2024)

> [!INFERENCE]
> **что сломано:** сразу после второй волны отказов (Н-1), 23.04.2024 в 09:59:54–10:00:28, с того же клиента 87.96.21.84 записаны три события EventID 18454 под учётной записью `sa`; в 10:00:12 — две записи EventID 15457, несущие значения `show advanced options` и `xp_cmdshell`, изменённые с 0 на 1; далее, в 10:01:17–18, записан старт аномальной PowerShell-сессии (см. Н-3).

корневая причина: записи 18454 фиксируют успешный вход по SQL-аутентификации (семантика номера — внешнее знание о SQL Server, см. ниже); записи 15457 фиксируют изменение настроенного значения 0→1, но не применение изменения (`RECONFIGURE`), не исполнение `xp_cmdshell` и не привязку к конкретной сессии. Связка «успешный вход → изменение конфигурации → старт PowerShell» — временная причинная гипотеза: других записанных сессий в этом окне нет, но незаписанные или ранее открытые сессии корпус не исключает.

- [!PROVEN] В корпусе 4 записи EventID 18454: `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18454) = 4` (см. улики).
- [!PROVEN] Три из четырёх записей 18454 приходятся на 23.04.2024 (строки 454, 455, 458): `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18454, Event.System.TimeCreated.#attributes.SystemTime>=2024-04-23T00:00:00) = 3` (см. улики); четвёртая — 21.04.2024, строка 186.
- [!PROVEN] Записи 18454 от 23.04.2024 называют учётную запись `sa` и клиента `[CLIENT: 87.96.21.84]` (см. улики: строки 454, 455, 458).
- [!PROVEN] 23.04.2024 в 10:00:12 зафиксированы две записи EventID 15457 с парами значений `["show advanced options","0","1"]` (строка 456) и `["xp_cmdshell","0","1"]` (строка 457) (см. улики).
- [!INFERENCE] Семантика номеров: 18454 — успешный вход по SQL-аутентификации, 15457 — изменение параметра конфигурации (sp_configure), требующее для применения отдельного `RECONFIGURE`. В корпусе текстов шаблонов этих событий нет, поэтому расшифровка — внешнее знание о SQL Server. Запись 15457 показывает только изменение настроенного значения 0→1: ни применения (`RECONFIGURE`), ни исполнения `xp_cmdshell`, ни привязки к конкретной сессии она не доказывает; поле данных события имени субъекта не несёт.
- [!INFERENCE] Связь «успешный вход → изменение конфигурации» — временная гипотеза, а не доказанная последовательность: записи 15457 (10:00:12.238113Z и 10:00:12.284576Z) следуют через ~0,4 с после второй записи 18454 (10:00:11.878462Z), других записанных сессий в этом окне нет, но незаписанные или ранее открытые сессии корпус не исключает.

улики:
- агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18454) = 4 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "18454") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
> [!PROVEN]
> агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=18454, Event.System.TimeCreated.#attributes.SystemTime>=2024-04-23T00:00:00) = 3 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "18454") catch false) and (try (.Event.System.TimeCreated."#attributes".SystemTime != null and (.Event.System.TimeCreated."#attributes".SystemTime|tostring) >= "2024-04-23T00:00:00") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
- агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=15457) = 2 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "15457") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
- [!PROVEN] BlueSkyRansomware.jsonl:454 — «#attributes":{"Qualifiers":16384},"#text":18454},"Version":0,"Level":0,"Task":4,"Opcode":0»
- [!PROVEN] BlueSkyRansomware.jsonl:454 — «ata":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"164800000A000000100000004»
- [!PROVEN] BlueSkyRansomware.jsonl:455 — «ata":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"164800000A000000100000004»
- [!PROVEN] BlueSkyRansomware.jsonl:458 — «ata":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"164800000A000000100000004»
- [!PROVEN] BlueSkyRansomware.jsonl:456 — «l},"EventData":{"Data":{"#text":["show advanced options","0","1"]},"Binary":"613C00000A000»
- [!PROVEN] BlueSkyRansomware.jsonl:457 — «#attributes":{"Qualifiers":16384},"#text":15457},"Version":0,"Level":4,"Task":2,"Opcode":0»
- [!PROVEN] BlueSkyRansomware.jsonl:457 — «":null},"EventData":{"Data":{"#text":["xp_cmdshell","0","1"]},"Binary":"613C00000A00000010»
- [!PROVEN] BlueSkyRansomware.jsonl:186 — «urity":null},"EventData":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"16480»

атрибуция: не установлена
исход: успех

чем опровергал: версия «входы не удались, а конфигурацию менял кто-то другой» не подтверждается содержимым окна: между волной отказов и записями 15457 в корпусе нет ни одного другого события аутентификации или администрирования. Но отсутствие другого записанного входа не исключает ранее открытых или незаписанных сессий; записи 15457 не называют субъекта, поэтому прямая атрибуция изменения конфигурации невозможна — связь с сессиями `sa` с 87.96.21.84 остаётся временной. Интерпретация 18454 как успешного SQL-входа держится на семантике номера (внешнее знание) и не зависит от канала Security: сами записи созданы провайдером MSSQLSERVER (строки 186, 454, 455, 458).

что делать сейчас: вернуть `show advanced options` и `xp_cmdshell` в 0 через `sp_configure` и применить изменение командой `RECONFIGURE`, затем проверить фактические значения в `sys.configurations` (записи 15457 фиксируют изменение настроенного значения 0→1, а не факт применения); сменить пароль `sa` и пароли всех SQL-логинов; проверить SQL-агент/задания и содержимое баз на признаки исполнения кода; собрать ERRORLOG и канал Security с этого хоста; ограничить доступ к 1433.

### Н-3 · Аномальная PowerShell-сессия `HostName=MSFConsole`, `HostApplication=winlogon.exe` (23.04.2024, 10:01)

> [!INFERENCE]
> **что сломано:** в 10:01:17–10:01:18 в канале Windows PowerShell зафиксирована серия событий EventID 600 (строки 459–466) и EventID 400 (строка 467) с `HostName=MSFConsole`, `HostVersion=0.1` и `HostApplication=winlogon.exe`. Это единственная такая сессия во всём корпусе: по полю `HostName` записи канала делятся на ConsoleHost (70 записей), Default Host (46), Visual Studio Code Host (8) и MSFConsole (9, строки 459–467). Старт сессии записан через ~65,6 секунды после записи изменения конфигурации (строка 457, 10:00:12.284576Z → строка 459, 10:01:17.878505Z); после неё в корпусе остаются только фоновые записи (Windows Error Reporting, строка 468, и вход телеметрии, строка 469, 10:11:10).

корневая причина: сочетание имени хоста MSFConsole, версии хоста 0.1 и `HostApplication=winlogon.exe` не соответствует ни одному легитимному способу запуска PowerShell, наблюдаемому в корпусе; это согласуется с запуском сессии механизмом, не оставляющим обычной командной строки, но сами записи не показывают ни реального winlogon.exe, ни внедрения, ни исполняемого файла, ни кода, ни оператора. По времени старт сессии следует за записью изменения конфигурации (Н-2).

- [!PROVEN] События сессии несут `HostName=MSFConsole`, `HostVersion=0.1` (строка 459) и `HostApplication=winlogon.exe`, `EngineVersion=5.1.19041.4291` (строка 467); строка 467 — событие EventID 400 (см. улики).
- [!PROVEN] Во всём корпусе ровно 107 событий EventID 600 и 16 событий EventID 400 канала Windows PowerShell: `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=600) = 107` и `агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=400) = 16` (см. улики); из них серии 459–467 относятся к MSFConsole-сессии.
- [!INFERENCE] Хост MSFConsole с `HostApplication=winlogon.exe` не соответствует ни одной обычной сессии канала: во всех 124 записях с хостами ConsoleHost/Default Host/Visual Studio Code Host поле HostApplication содержит командную строку powershell.exe, а в 9 записях MSFConsole (строки 459–467) — нет. Одна строка `HostApplication` не доказывает, что PowerShell был реально запущен как winlogon.exe: ни внедрение, ни исполняемый файл, ни код, ни оператор в записях не видны. Чего не хватает, чтобы считать доказанным: событий создания процесса (4688/Sysmon), полной командной строки и родительского процесса, имени учётной записи (в записях 459–467 Security пуст).
- [!INFERENCE] Связь сессии с Н-2 (записи изменения конфигурации 0→1) — временная: интервал от строки 457 (10:00:12.284576Z) до строки 459 (10:01:17.878505Z) составляет ~65,6 секунды; других записанных способов появления этой сессии в корпусе не видно, но это не доказывает причинную связь с изменением конфигурации.

улики:
- агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=600) = 107 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "600") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
- агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=400) = 16 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "400") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
- [!PROVEN] BlueSkyRansomware.jsonl:459 — «r\n\tSequenceNumber=1\r\n\r\n\tHostName=MSFConsole\r\n\tHostVersion=0.1\r\n\tHostId=1693e6»
- [!PROVEN] BlueSkyRansomware.jsonl:467 — «D":{"#attributes":{"Qualifiers":0},"#text":400},"Version":0,"Level":4,"Task":4,"Opcode":0,»
- [!PROVEN] BlueSkyRansomware.jsonl:467 — «8356-4245271c31e8\r\n\tHostApplication=winlogon.exe\r\n\tEngineVersion=5.1.19041.4291\r\n\»
- [!PROVEN] BlueSkyRansomware.jsonl:24 — «bject { $_.ExecutablePath -eq 'C:\\Users\\Win 10\\AppData\\Local\\Programs\\Microsoft VS C»

атрибуция: не установлена
исход: попытка

чем опровергал: версия «это обычный запуск PowerShell» опровергнута сравнением HostName и HostApplication со всеми остальными записями канала: хосты ConsoleHost (70), Default Host (46) и Visual Studio Code Host (8) несут в HostApplication командную строку powershell.exe, а 9 записей MSFConsole (строки 459–467) — нет; версия «единичный артефакт» — серия из девяти событий одной сессии с общим HostId. Исход «попытка»: старт сессии зафиксирован (события 600/400 записаны, строка 467 показывает состояние движка Available), но ни исполняемого файла, ни кода, ни оператора внутри неё корпус не показывает, поэтому результат и атрибуция остаются неподтверждёнными.

что делать сейчас: собрать с хоста события создания процессов (4688/Sysmon), полный ERRORLOG SQL Server и память/образ хоста; проверить, какой код исполняла сессия MSFConsole; изолировать хост и обращаться со всеми учётными данными на нём как с потенциально скомпрометированными.

## Отклонённые кандидаты

### К-1 · PowerShell-серия 21.04.2024 в 23:39 с единственным EventID 800 — не посторонняя скриптовая активность

что выглядело как причина: вечером 21.04.2024 (23:39) в канале Windows PowerShell прошла серия событий 600/400, включая единственный в корпусе EventID 800; в рабочем списке (S001) эта серия была помечена как «признак скриптовой активности злоумышленника» (фрагмент `Add-Type -Path "$PSScriptRoot/Newtonsoft.Json.dll"`).

улики:
- агрегат: BlueSkyRansomware.jsonl · count(Event.System.EventID.#text=800) = 1 · `jq -c 'select((try (.Event.System.EventID."#text" != null and (.Event.System.EventID."#text"|tostring) == "800") catch false))' -- 'BlueSkyRansomware.jsonl' | wc -l`
- [!PROVEN] BlueSkyRansomware.jsonl:365 — «vscode.powershell-2024.2.1\\modules\\PSScriptAnalyzer\\1.22.0\\PSScriptAnalyzer.psm1\r\n\t»
- [!PROVEN] BlueSkyRansomware.jsonl:360 — «Start-EditorServices -HostName 'Visual Studio Code Host' -HostProfileId 'Microsoft.VSCode»

исход: норма

чем опровергал: единственное событие EventID 800 в корпусе (агрегат = 1) относится к строке 365, где поле ScriptName указывает на модуль PSScriptAnalyzer расширения Visual Studio Code `ms-vscode.powershell-2024.2.1`, а HostApplication серии (строка 360) содержит импорт модуля PowerShellEditorServices и `Start-EditorServices -HostName 'Visual Studio Code Host'`. Поле `HostName` строки 360 при этом равно `Default Host`: `Visual Studio Code Host` фигурирует в записи только как аргумент `-HostName` внутри HostApplication, а не как отдельное значение поля HostName. Запись 800 подтверждает контекст IDE-расширения VS Code (анализ и редакторские службы), а не посторонний код; фрагмент `Add-Type …Newtonsoft.Json.dll` — загрузка зависимостей самого расширения. Снятие кандидата относится к этому событию и его контексту; доверенность любого другого кода на хосте оно не устанавливает. Кандидат снят.

## Принадлежность учётных записей

| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше | метка |
|---|---|---|---|---|---|---|
| DESKTOP-7EQVM78\Win 10 | 2024-04-21T00:34:18.429107Z | BlueSkyRansomware.jsonl:24 «bject { $_.ExecutablePath -eq 'C:\\Users\\Win 10\\AppData\\Local\\Programs\\Microsoft VS C» | профиль | владелец | — | [!PROVEN] |
| sa | 2024-04-21T04:38:07.272160Z | BlueSkyRansomware.jsonl:109 «y":null},"EventData":{"Data":{"#text":["1","sa"]},"Binary":"564A00000A00000010000000440045» | неизвестно | не определяется | — | [!INFERENCE] |
| NT SERVICE\SQLTELEMETRY | 2024-04-21T04:43:08.693292Z | BlueSkyRansomware.jsonl:155 «ntData":{"Data":{"#text":["NT SERVICE\\SQLTELEMETRY"," [CLIENT: <local machine>]"]},"Binar» | служба | владелец | — | [!PROVEN] |

- [!PROVEN] `DESKTOP-7EQVM78\Win 10` — локальная учётная запись Windows; «владелец» в таблице означает сторону самого хоста, а не доказанного человека-оператора: первое появление — профиль `C:\Users\Win 10` внутри командной строки PowerShell (строка 24, 00:34:18); далее локальные входы по Windows-аутентификации с link-local клиента `fe80::6742:7b8e:1350:5571%9` (строка 143). Идентификатор учётной записи и её локальный характер записи показывают; какой человек и с какими полномочиями за ней стоит, они не отражают.
- [!INFERENCE] `sa` — учётная запись SQL Server; вывод «не определяется» означает, что по корпусу нельзя установить, чья это учётка — сторона владельца хоста или иная сторона: профиля `C:\Users\sa` и локальных входов по Windows-аутентификации нет, во всех записях входа и подключений `sa` фигурирует только удалённый клиентский адрес 87.96.21.84 (волны 18456, строки 164–185 и 432–453; успешные входы 18454, строки 186, 454, 455, 458). Первое появление — в записи запуска SQL Trace внутри потока запуска службы MSSQLSERVER (строка 109, 04:38:07); вторая такая же запись — строка 295 (21.04 23:31:02); обе — события MSSQLSERVER EventID 19030 (SQL Trace startup), а не удалённая аутентификация. Удалённый адрес показывает, что входы шли по сети, но кто стоял за 87.96.21.84 — владелец сервера или иное лицо — записи не отражают, и принадлежность оператора по корпусу не определяется. Чего не хватает: ERRORLOG/аудита создания учётной записи и канала Security, чтобы определить, как и когда `sa` появилась на сервере и кто выполнял входы.
- [!PROVEN] `NT SERVICE\SQLTELEMETRY` — встроенная учётная запись службы SQL Server («владелец» в таблице — сторона самого хоста, его служба): первое появление — вход телеметрии с клиентом `<local machine>` (строка 155, 04:43:08); далее те же служебные входы (строки 207, 210, 211, 317, 426, 469). Равномерного ~10-минутного ряда эти входы не образуют (интервалы ≈10,5 мин, 15 мин и разрывы в часы), и уликами с удалёнными сессиями `sa` они не связаны.

## Покрытие

| путь | статус | улики |
| --- | --- | --- |
| BlueSkyRansomware.jsonl | наблюдение | BlueSkyRansomware.jsonl:53 — «":{"#attributes":{"xmlns":"http://schemas.microsoft.com/win/2004/08/events/event"},"System» |

covermap: 1 файлов — наблюдение 1

## Разбор рабочего списка

- g001 N (норма) — MSSQLSERVER, EventID 1 (запуск SqlCeip): фон, нормальная работа службы.
- g002 N (норма) — Windows-Search, EventID 1003: фон.
- g003 N (норма) — VMTools, EventID 108: фон.
- g004 N (норма) — edgeupdate, EventID 0 (остановка службы): фон.
- g005 N (норма) — VSS, EventID 8224: фон (в т.ч. строка 424, 23.04 09:53).
- g006 N (норма) — EventSystem, EventID 4625 (COM+): фон; это событие провайдера EventSystem, а не отказ входа Windows.
- g007 N (норма) — Complus, EventID 781: фон.
- g008 N (норма) — MSDTC 2, EventID 4202: фон.
- g009 N (норма) — User Profiles Service, EventID 1532: фон.
- g010 N (норма) — WMI, EventID 5615: фон.
- g011 N (норма) — Winlogon, EventID 6000 (WSearch): фон.
- g012 N (норма) — VMUpgradeHelper, EventID 260: фон.
- g013 N (норма) — RestartManager, EventID 10000: фон.
- g014 N (норма) — ESENT, EventID 102 (SearchIndexer): фон.
- S001 D — канал Windows PowerShell (серии 600/400, единственный EventID 800): фоновая часть и событие 800 отклонены (К-1); аномальная сессия MSFConsole/winlogon.exe от 23.04 10:01 осталась находкой Н-3.
- S002 D — MSSQLSERVER (волны 18456, записи 18454, изменение конфигурации 0→1): находки Н-1 и Н-2.

## Чего я не знаю (чего не хватает)

- Канала Security (4624/4625/4672/4688) в корпусе нет — Windows-уровневая корреляция недоступна: какие процессы и с какими правами стартовали и кто выполнял входы на уровне ОС, по этому корпусу не подтверждается.
- Успешность SQL-входов `sa` фиксируют сами записи MSSQLSERVER EventID 18454 (строки 186, 454, 455, 458): это события уровня SQL Server, и они не зависят от наличия канала Security. Что именно исполняла PowerShell-сессия (строки 459–467), эти записи не показывают.
- ERRORLOG SQL Server и тексты шаблонов событий 18453/18454/18456/15457 в корпусе отсутствуют — расшифровка номеров событий остаётся внешним знанием (см. метки INFERENCE в Н-1, Н-2).
- События 15457 (строки 456–457) не содержат имени субъекта — кто именно выполнил `sp_configure`, по корпусу не видно.
- Имя учётной записи, под которой стартовала MSFConsole-сессия (строки 459–467), в записях отсутствует (поле Security пусто).
- Кто и что именно стояло за клиентским адресом 87.96.21.84, в корпусе не отражено — нужны сетевые журналы.
- Переход Windows Defender в `SECURITY_PRODUCT_STATE_SNOOZED` 23.04.2024 записан (строки 425, 427–429; перед ним — `SECURITY_PRODUCT_STATE_ON`, строка 423, 09:52:59), но причина и субъект перехода в записях не отражены: события SecurityCenter EventID 2 и EventID 1 (строки 430–431) пусты, а связующих записей между 09:54:57 и волной 18456 (09:59:54) нет. Отнести этот переход к подготовке атаки или снять его как норму улики не позволяют — данных не хватает.
- Ряд входов по Windows-аутентификации (EventID 18453, агрегат по корпусу = 15) состоит только из локальных принципалов — локальной учётной записи `Win 10` с клиентом `fe80::6742:7b8e:1350:5571%9` (серия от 04:40:28, строки 143–154) и службы `NT SERVICE\SQLTELEMETRY` с клиентом `<local machine>` (строки 155, 207, 210, 211, 317, 426, 469); `sa` и адрес 87.96.21.84 в записях 18453 не встречаются. Отсутствие связи этого ряда с удалёнными сессиями `sa` нормой его не делает: кто и с какой целью выполнял эти локальные входы, по корпусу не определяется — канала Security и сведений о процессах не хватает.
- Как и когда была создана/настроена учётная запись `sa`, в корпусе не видно (первое появление — строка 109, внутри запуска службы).
- За 22.04.2024 событий нет; после последней записи 23.04 10:11:10 (строка 469) наблюдение обрывается.

# Окно записей

итог: файлов=1 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 ошибок=0

| путь | канал | окно | записей | нет |
| --- | --- | --- | --- | --- |
| BlueSkyRansomware.jsonl | Application | окно=2713–3048 | записей=336 | нет=0 |
| BlueSkyRansomware.jsonl | Windows PowerShell | окно=1–133 | записей=133 | нет=0 |

## Инвентарь

- Имя хоста: DESKTOP-7EQVM78 — источник: BlueSkyRansomware.jsonl:71 — «"Computer":"DESKTOP-7EQVM78","Security":null}»
- ОС: Windows 10 Pro 10.0, сборка 19045, виртуальная машина (Hypervisor) — источник: BlueSkyRansomware.jsonl:71 — «Developer Edition (64-bit) on Windows 10 Pro 10.0 <X64> (Build 19045: ) (Hypervisor)»
- SQL Server: Microsoft SQL Server 2022 (RTM) 16.0.1000.6 (X64), Developer Edition — источник: BlueSkyRansomware.jsonl:71 — «"Data":{"#text":"Microsoft SQL Server 2022 (RTM) - 16.0.1000.6 (X64)»
- Режим аутентификации SQL Server: MIXED — источник: BlueSkyRansomware.jsonl:77 — «"EventData":{"Data":{"#text":"MIXED"»
- Служба: NT Service\MSSQLSERVER — источник: BlueSkyRansomware.jsonl:79 — «NT Service\\MSSQLSERVER"},"Binary":"F0C200000A00000010»
- Пути данных и журнала: C:\Program Files\Microsoft SQL Server\MSSQL16.MSSQLSERVER\MSSQL\DATA\master.mdf, ...\MSSQL\Log\ERRORLOG, ...\DATA\mastlog.ldf — источник: BlueSkyRansomware.jsonl:80 — «-d C:\\Program Files\\Microsoft SQL Server\\MSSQL16.MSSQLSERVER\\MSSQL\\DATA\\master.mdf»
- Учётная запись SQL: sa — источник: BlueSkyRansomware.jsonl:109 — «"#text":["1","sa"]},"Binary":"564A00000A00000010»
- Локальная учётная запись Windows: DESKTOP-7EQVM78\Win 10 — источник: BlueSkyRansomware.jsonl:24 — «bject { $_.ExecutablePath -eq 'C:\\Users\\Win 10\\AppData\\Local\\Programs\\Microsoft VS C», BlueSkyRansomware.jsonl:143 — «Win 10"," [CLIENT: fe80::6742:7b8e:1350:5571%9]"]},"Binary":"154800000A000000100»
- Учётная запись службы: NT SERVICE\SQLTELEMETRY — источник: BlueSkyRansomware.jsonl:155 — «ntData":{"Data":{"#text":["NT SERVICE\\SQLTELEMETRY"," [CLIENT: <local machine>]"]},"Binar»
- Слушатель SQL Server: TCP 1433, привязка «'any'», IPv6 и IPv4 — источник: BlueSkyRansomware.jsonl:121 — «"#text":["'any'","ipv6","1433","1"]},"Binary":"A66500000A00000010», BlueSkyRansomware.jsonl:122 — «"#text":["'any'","ipv4","1433","1"]},"Binary":"A66500000A00000010»
- Клиентский адрес (волны 18456/18454): 87.96.21.84 — источник: BlueSkyRansomware.jsonl:164 — «"#text":["sa"," Reason: Password did not match that for the login provided."," [CLIENT: 87.96.21.84]"]},"Binary":"184800000E0000», BlueSkyRansomware.jsonl:432 — «"#text":["sa"," Reason: Password did not match that for the login provided."," [CLIENT: 87.96.21.84]"]},"Binary":"184800000E0000»
- Клиентский адрес (локальные входы Win 10): fe80::6742:7b8e:1350:5571%9 — источник: BlueSkyRansomware.jsonl:143 — «Win 10"," [CLIENT: fe80::6742:7b8e:1350:5571%9]"]},"Binary":"154800000A000000100»
- Расширение VS Code: ms-vscode.powershell-2024.2.1; модули PSScriptAnalyzer 1.22.0 и PowerShellEditorServices — источник: BlueSkyRansomware.jsonl:360 — «Start-EditorServices -HostName 'Visual Studio Code Host' -HostProfileId 'Microsoft.VSCode», BlueSkyRansomware.jsonl:365 — «vscode.powershell-2024.2.1\\modules\\PSScriptAnalyzer\\1.22.0\\PSScriptAnalyzer.psm1\r\n\t»
- PowerShell-хост MSFConsole: HostName=MSFConsole, HostVersion=0.1, HostId=1693e66c-ce22-41d0-8356-4245271c31e8, HostApplication=winlogon.exe — источник: BlueSkyRansomware.jsonl:459 — «r\n\tSequenceNumber=1\r\n\r\n\tHostName=MSFConsole\r\n\tHostVersion=0.1\r\n\tHostId=1693e6», BlueSkyRansomware.jsonl:467 — «8356-4245271c31e8\r\n\tHostApplication=winlogon.exe\r\n\tEngineVersion=5.1.19041.4291\r\n\»
- Изменённые параметры конфигурации: show advanced options 0→1, xp_cmdshell 0→1 — источник: BlueSkyRansomware.jsonl:456 — «l},"EventData":{"Data":{"#text":["show advanced options","0","1"]},"Binary":"613C00000A000», BlueSkyRansomware.jsonl:457 — «":null},"EventData":{"Data":{"#text":["xp_cmdshell","0","1"]},"Binary":"613C00000A00000010»
- Состояния Windows Defender: SECURITY_PRODUCT_STATE_ON, SECURITY_PRODUCT_STATE_SNOOZED — источник: BlueSkyRansomware.jsonl:423 — «":{"#text":["Windows Defender","SECURITY_PRODUCT_STATE_ON"]},"Binary":null}}}», BlueSkyRansomware.jsonl:425 — «indows Defender","SECURITY_PRODUCT_STATE_SNOOZED"]},"Binary":null}}}»

## ВЕРДИКТ

Наиболее весомая связка находок — Н-2: сразу после второй волны отказов входа с одного и того же клиентского адреса в корпусе записаны успешные входы под учётной записью `sa` (EventID 18454), затем — записи изменения серверных параметров `show advanced options`/`xp_cmdshell` 0→1 (EventID 15457), а через ~65,6 секунды после них — старт единственной в корпусе PowerShell-сессии `HostName=MSFConsole`, `HostApplication=winlogon.exe` (Н-3). Доказанная граница доступа: с адреса 87.96.21.84, с которого шли обе волны подбора пароля (Н-1), записан успешный вход по SQL-аутентификации под `sa` — записи 18454 созданы провайдером MSSQLSERVER и не зависят от канала Security. Кто стоял за этим адресом, кто выполнил изменение конфигурации (субъект в записях 15457 не назван) и кто запустил сессию (в строках 459–467 нет ни учётной записи, ни полезной нагрузки), корпус не отражает. Временная связка «вход → изменение конфигурации → старт сессии» опирается на совпадение адреса и времени и на отсутствие других записанных сессий в окне; отсутствие записанных сессий не доказывает отсутствия других субъектов: те же действия могли исходить от иной стороны или от ранее открытой сессии (семантика номеров событий — внешнее знание, границы разобраны в находках Н-1, Н-2 и Н-3). Успех входа доказан, атрибуция инициатора не установлена.

- BlueSkyRansomware.jsonl:164 — «ata":{"Data":{"#text":["sa"," Reason: Password did not match that for the login provided."»
- BlueSkyRansomware.jsonl:432 — «at for the login provided."," [CLIENT: 87.96.21.84]"]},"Binary":"184800000E000000100000004»
- BlueSkyRansomware.jsonl:454 — «ata":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"164800000A000000100000004»
- BlueSkyRansomware.jsonl:455 — «ata":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"164800000A000000100000004»
- BlueSkyRansomware.jsonl:458 — «ata":{"Data":{"#text":["sa"," [CLIENT: 87.96.21.84]"]},"Binary":"164800000A000000100000004»
- BlueSkyRansomware.jsonl:456 — «l},"EventData":{"Data":{"#text":["show advanced options","0","1"]},"Binary":"613C00000A000»
- BlueSkyRansomware.jsonl:457 — «":null},"EventData":{"Data":{"#text":["xp_cmdshell","0","1"]},"Binary":"613C00000A00000010»
- BlueSkyRansomware.jsonl:459 — «r\n\tSequenceNumber=1\r\n\r\n\tHostName=MSFConsole\r\n\tHostVersion=0.1\r\n\tHostId=1693e6»
- BlueSkyRansomware.jsonl:467 — «8356-4245271c31e8\r\n\tHostApplication=winlogon.exe\r\n\tEngineVersion=5.1.19041.4291\r\n\»

атаковали, но не доказано



