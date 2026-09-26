## Находки

### Н-1 · Перебор учётных записей (словарная атака) с внешних IP-адресов

Обнаружена массовая словарная атака на сервер IPSERVER через NTLM-вход по сети (LogonType=3). За 108 секунд с 12 различных внешних IP-адресов выполнено 20 попыток входа под 13 разными именами учётных записей.

- [!PROVEN] 13 разных имён учётных записей перебрано за 108 секунд — агрегат: Security.jsonl · distinct(Event.EventData.TargetUserName) = 13 · `jq -r '(try (.Event.EventData.TargetUserName) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!PROVEN] 12 внешних IP-адресов источника — агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, Event.EventData.IpAddress!=-) = 12 · `jq -r 'select((try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!PROVEN] 4 IP-адреса повторили попытку более 1 раза — агрегат: Security.jsonl · distinct_over(Event.EventData.IpAddress, 1, Event.EventData.IpAddress!=-) = 4 · `jq -r 'select((try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort | uniq -c | awk '$1 > 1' | wc -l`
- [!PROVEN] имя `Test` подставлено с рабочей станции Rdesktop — Security.jsonl:3#TargetUserName,IpAddress,WorkstationName,LogonType,SubStatus
- [!PROVEN] имя `KATE` — Security.jsonl:15#TargetUserName
- [!INFERENCE] Атака ведётся автоматизированным инструментом: интервал между попытками составляет от 1 до 30 секунд, все запросы идут через NTLM (NtLmSsp) с LogonType=3 (сетевой вход). Один из источников (112.25.201.244) указал рабочую станцию `Rdesktop` — имя RDP-клиента, что нетипично для чистого сетевого входа.

Корневая причина: атака идёт извне — ни один из 12 IP-адресов не является внутренним (частным). Служба NTLM-аутентификации доступна снаружи и принимает запросы на вход от любого сетевого клиента.

атрибуция: не установлена
исход: попытка

чем опровергал: проверено по SubStatus — 14 попыток пришлись на несуществующие учётные записи (SubStatus=0xc0000064 (нет такой учётной записи)); 6 попыток — на существующую учётную запись `АДМИНИСТРАТОР` с неверным паролем (SubStatus=0xc000006a (неверный пароль)). Доля несуществующих имён 14/20 = 70%, что подтверждает словарный характер атаки.

что делать сейчас:
- проверить, открыт ли порт 445 (SMB) или 3389 (RDP) в брандмауэре для внешних адресов;
- ограничить входящий NTLM-трафик до доверенных подсетей;
- включить блокировку учётной записи после N неудачных попыток;
- настроить Windows Firewall на отклонение трафика от перечисленных 12 IP-адресов.

### Н-2 · Целевой подбор пароля к учётной записи `АДМИНИСТРАТОР`

Учётная запись `АДМИНИСТРАТОР` является единственной, к которой атакующие подбирают пароль, а не перебирают словарные имена. Из 6 попыток под этой учётной записью все имеют SubStatus=0xc000006a (неверный пароль) — это означает, что учётная запись СУЩЕСТВУЕТ и включена. 4 из 6 попыток приходятся на один IP-адрес (91.220.163.170) за 39 секунд.

- [!PROVEN] 6 попыток с неверным паролем под именем `АДМИНИСТРАТОР` — агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc000006a, Event.EventData.SubStatus=0xc000006a) = 6 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false) and (try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false))' -- 'Security.jsonl' | wc -l`
- [!PROVEN] только одна учётная запись получает попытки с неверным паролем — агрегат: Security.jsonl · distinct(Event.EventData.TargetUserName, Event.EventData.SubStatus=0xc000006a) = 1 · `jq -r 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false)) | (try (.Event.EventData.TargetUserName) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!PROVEN] первая попытка под `АДМИНИСТРАТОР` — Security.jsonl:6#TargetUserName,SubStatus
- [!PROVEN] четыре попытки с 91.220.163.170 подряд — Security.jsonl:13#TargetUserName, Security.jsonl:16#TargetUserName, Security.jsonl:19#TargetUserName, Security.jsonl:20#TargetUserName
- [!INFERENCE] имя `АДМИНИСТРАТОР` — это русское написание локальной учётной записи администратора Windows. Атакующий использует знание локальной конфигурации сервера (русскоязычная версия Windows).

Status=0xc000006d (общий отказ входа) — внешний код, не уточняет причину; причину несёт SubStatus. SubStatus=0xc000006a (неверный пароль) — 6 записей, все под `АДМИНИСТРАТОР`. SubStatus=0xc0000064 (нет такой учётной записи) — 14 записей, под остальными 12 именами.

атрибуция: не установлена
исход: попытка

чем опровергал: проверено по SubStatus — 6 раз `неверный пароль` при существующей учётной записи против 14 раз `нет такой учётной записи` для несуществующих имён. Учётная запись `АДМИНИСТРАТОР` существует и пароль к ней не подобран.

что делать сейчас:
- немедленно сменить пароль учётной записи `АДМИНИСТРАТОР` на стойкий (не словарный);
- проверить, не используется ли эта учётная запись для повседневной работы (если да — создать отдельные учётки с минимальными правами);
- проверить журналы безопасности на более ранний период на наличие успешных входов под этим именем.

## Отклонённые кандидаты

### К-1 · Рабочая станция Rdesktop как признак RDP-атаки

В одной из записей поле WorkstationName содержит значение `Rdesktop` — это имя RDP-клиента (rdesktop/FreeRDP). Выглядело как указание на целевую атаку через RDP (LogonType=10 (удалённый интерактивный rdp)), однако тип входа в этой записи — LogonType=3 (сетевой, network), а SubStatus=0xc0000064 (нет такой учётной записи). Учётная запись `Test` не существует, попытка не могла увенчаться успехом.

- [!PROVEN] WorkstationName=Rdesktop при LogonType=3 — Security.jsonl:3#WorkstationName,LogonType
- [!INFERENCE] Атакующий мог указать имя рабочей станции произвольно — в NTLM-аутентификации WorkstationName — это самозаявляемое поле. Rdesktop не обязательно означает попытку RDP-подключения; это может быть автоматизированный скрипт, подставляющий произвольное имя.

исход: норма

чем опровергал: проверен LogonType — 3 (сетевой), а не 10 (удалённый интерактивный). В сочетании с SubStatus=0xc0000064 (нет такой учётной записи) попытка заведомо не могла быть успешным RDP-входом. Во всём корпусе только одна запись с Rdesktop:
> агрегат: Security.jsonl · count(line~=Rdesktop) = 1 · `grep -c -F -- 'Rdesktop' 'Security.jsonl'`
Единичное указание Rdesktop не образует паттерна RDP-атаки.

### К-2 · Аномалия ProcessID=0 в System.jsonl

Строка System.jsonl:1 имеет ProcessID=0 (запись 6009 о версии ОС). ProcessID=0 — нормальное значение для системных событий начальной загрузки (провайдер EventLog не привязан к конкретному процессу).

- [!PROVEN] ProcessID=0 для события 6009 — System.jsonl:1
- [!INFERENCE] ProcessID=0 типичен для событий загрузки ОС от провайдера EventLog.

исход: норма

чем опровергал: запись с 6009 (версия ОС) — одна:
> агрегат: System.jsonl · count(line~=6009) = 1 · `grep -c -F -- '6009' 'System.jsonl'`
ProcessID=0 встречается в 2 из 20 записей (обе — события провайдера EventLog при запуске системы). Это нормальный фон системного журнала Windows.

## Принадлежность учётных записей

| учётная запись | первое появление | path:line#Поле | как | вывод | раньше | метка |
|---|---|---|---|---|---|---|
| IPSERVER\ADMINI | 2021-06-01T18:36:04Z | Security.jsonl:1#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\МАКСИМУМ | 2021-06-01T18:36:09Z | Security.jsonl:2#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\Test | 2021-06-01T18:36:23Z | Security.jsonl:3#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\ADMIN | 2021-06-01T18:36:24Z | Security.jsonl:4#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\ADMINISTRATOR | 2021-06-01T18:36:53Z | Security.jsonl:5#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\АДМИНИСТРАТОР | 2021-06-01T18:37:01Z | Security.jsonl:6#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\BILLING | 2021-06-01T18:37:11Z | Security.jsonl:7#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\NOMINAS | 2021-06-01T18:37:23Z | Security.jsonl:9#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\ПОЛЬЗОВАТЕЛЬ2 | 2021-06-01T18:37:25Z | Security.jsonl:10#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\МАКСИМ | 2021-06-01T18:37:37Z | Security.jsonl:11#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\KATE | 2021-06-01T18:37:47Z | Security.jsonl:15#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\АДМИН | 2021-06-01T18:37:47Z | Security.jsonl:17#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |
| IPSERVER\СЌРҐСЃР°СЂРҐ | 2021-06-01T18:37:51Z | Security.jsonl:18#TargetUserName | неизвестно | не определяется | — | [!PROVEN] |

## Покрытие

| путь | статус | улики |
| --- | --- | --- |
| Security.jsonl | наблюдение | Security.jsonl:3 — «Security-Auditing","Guid":"54849625-5478-4994-A5BA-3E3B0328C30D"}},"EventID":4625,"Version» |
| System.jsonl | наблюдение | System.jsonl:14 — «soft-Windows-Ntfs","Guid":"3FF37A1C-A68D-4D6E-8C9B-F79E8B16C482"}},"EventID":98,"Version":» |

## Разбор рабочего списка

- g001 — D Н-1 (попытка входа с WorkstationName=Rdesktop при SubStatus=0xc0000064)
- [!PROVEN] g002 — N (System.jsonl:1#EventRecordID — нормальная запись о версии ОС при загрузке)
- [!PROVEN] g003 — N (System.jsonl:14#EventRecordID — нормальная проверка целостности диска)
- [!PROVEN] g004 — N (System.jsonl:20#EventRecordID — нормальное событие управления питанием процессора)

## Окно записей

итог: файлов=2 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 ошибок=0

## Чего не хватает в логах

- Корпус содержит 20 событий Security за 108 секунд и 20 событий System за 27 секунд (загрузка от 2021-05-08). Это узкое окно наблюдения — более ранние или более поздние атаки (успешные входы, создание учётных записей, установка ПО) могли находиться за пределами этого окна.
- Нет журналов брандмауэра Windows (Firewall) — невозможно определить, было ли входящее сетевое подключение к портам SMB (445) или RDP (3389) разрешено или заблокировано.
- Нет журнала событий терминальных служб (Microsoft-Windows-TerminalServices-LocalSessionManager/Operational) — нельзя независимо проверить, были ли попытки RDP-подключений.
- Нет журнала Sysmon или Process Creation (EventID 4688) — нельзя определить, какие процессы были запущены на машине.
- Нет журнала службы User Profile Service — невозможно определить, какие учётные записи имеют локальные профили. Системный журнал содержит только события загрузки ОС, датированные 2021-05-08T15:04:03 — за 24 дня до атаки (2021-06-01T18:36). Внутри этого окна события безопасности не генерировались, что может быть результатом фильтрованного экспорта.

## Перечень адресов и наблюдаемых величин

### Внешние IP-адреса атакующих (из Security.jsonl)

| адрес | количество попыток | примечание |
|---|---|---|
| 91.220.163.170 | 4 | все по учётной записи АДМИНИСТРАТОР |
| 31.131.253.132 | 3 | МАКСИМУМ, BILLING, МАКСИМ |
| 45.168.116.98 | 2 | ADMINI, ADMINISTRATOR |
| 77.37.206.173 | 2 | ADMIN, ADMIN (повтор) |
| 112.25.201.244 | 1 | Test (WorkstationName=Rdesktop) |
| 95.161.182.42 | 1 | ADMINISTRATOR |
| 223.31.121.20 | 1 | АДМИНИСТРАТОР |
| 62.122.102.155 | 1 | NOMINAS |
| 109.197.27.136 | 1 | ПОЛЬЗОВАТЕЛЬ2 |
| 193.142.146.135 | 1 | KATE |
| 95.59.85.48 | 1 | АДМИН |
| 93.170.134.237 | 1 | СЌРҐСЃР°СЂРҐ |

### Имена учётных записей (TargetUserName)

| имя | попыток | SubStatus |
|---|---|---|
| АДМИНИСТРАТОР | 6 | 0xc000006a (неверный пароль) |
| ADMIN | 2 | 0xc0000064 (нет такой учётной записи) |
| ADMINISTRATOR | 2 | 0xc0000064 (нет такой учётной записи) |
| ADMINI | 1 | 0xc0000064 (нет такой учётной записи) |
| МАКСИМУМ | 1 | 0xc0000064 (нет такой учётной записи) |
| Test | 1 | 0xc0000064 (нет такой учётной записи) |
| BILLING | 1 | 0xc0000064 (нет такой учётной записи) |
| NOMINAS | 1 | 0xc0000064 (нет такой учётной записи) |
| ПОЛЬЗОВАТЕЛЬ2 | 1 | 0xc0000064 (нет такой учётной записи) |
| МАКСИМ | 1 | 0xc0000064 (нет такой учётной записи) |
| KATE | 1 | 0xc0000064 (нет такой учётной записи) |
| АДМИН | 1 | 0xc0000064 (нет такой учётной записи) |
| СЌРҐСЃР°СЂРҐ | 1 | 0xc0000064 (нет такой учётной записи) |

## ВЕРДИКТ

[!INFERENCE] атаковали, но не доказано — Security.jsonl:1#IpAddress, Security.jsonl:6#SubStatus, Security.jsonl:3#WorkstationName

[!INFERENCE] Сервер IPSERVER подвергся словарной атаке с 12 внешних IP-адресов — агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, Event.EventData.IpAddress!=-) = 12 · `jq -r 'select((try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`

[!PROVEN] 6 из 20 попыток пришлись на существующую учётную запись `АДМИНИСТРАТОР` с неверным паролем — Security.jsonl:6#SubStatus, Security.jsonl:13#SubStatus, Security.jsonl:14#SubStatus, Security.jsonl:16#SubStatus, Security.jsonl:19#SubStatus, Security.jsonl:20#SubStatus

[!INFERENCE] Ни одна попытка не достигла цели — успешных входов (EventID 4624) в корпусе нет, все 20 записей Security.jsonl — EventID 4625 (Security.jsonl:1-20#EventID). Атрибуция не установлена — источники атаки внешние, но личность атакующего по корпусу не определяется.
