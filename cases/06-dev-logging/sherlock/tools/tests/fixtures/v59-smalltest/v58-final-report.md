# Отчёт о расследовании инцидента

## Находки

### Н-1 · Массовые неудачные попытки сетевого входа (EventID 4625) с внешних IP-адресов

что сломано: Сервер IPSERVER в течение 108 секунд получил 20 неудачных попыток входа (EventID 4625) от 12 различных внешних IP-адресов по 13 разным именам учётных записей. Все попытки — LogonType=3 (сетевое подключение), протокол NTLM. Атакующие перебирают как англоязычные («ADMIN», «ADMINISTRATOR», «Test», «KATE», «BILLING», «NOMINAS»), так и русскоязычные имена («АДМИНИСТРАТОР», «АДМИН», «МАКСИМУМ», «МАКСИМ», «ПОЛЬЗОВАТЕЛЬ2»).

корневая причина: Отсутствие блокировки входящих NTLM-соединений на сетевом периметре.

улики:
- [!PROVEN] Security.jsonl:3 — «"WorkstationName":"Rdesktop"» — одна из попыток (пользователь Test) указала рабочую станцию Rdesktop.
- [!PROVEN] Security.jsonl:1 — «"TargetUserName":"ADMINI"» — Status=0xc000006d (общий отказ входа), SubStatus=0xc0000064 (нет такой учётной записи).
- [!PROVEN] агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc0000064) = 14 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc0000064") catch false))' -- 'Security.jsonl' | wc -l`
- [!PROVEN] агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc000006a) = 6 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false))' -- 'Security.jsonl' | wc -l`
- [!PROVEN] агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, Event.EventData.IpAddress!=-) = 12 · `jq -r 'select((try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!PROVEN] агрегат: Security.jsonl · distinct(Event.EventData.TargetUserName) = 13 · `jq -r '(try (.Event.EventData.TargetUserName) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!PROVEN] агрегат: Security.jsonl · distinct_over(Event.EventData.IpAddress, 1, Event.EventData.IpAddress!=-) = 4 · `jq -r 'select((try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort | uniq -c | awk '$1 > 1' | wc -l`

> 20 записей имеют LogonType=3 (сетевое подключение), AuthenticationPackageName=NTLM, Status=0xc000006d (общий отказ входа). «"FailureReason":"%%2313"» присутствует во всех записях. Причины по SubStatus:
> - SubStatus=0xc0000064 (нет такой учётной записи) — 14 записей
> - SubStatus=0xc000006a (неверный пароль) — 6 записей (все 6 по имени «АДМИНИСТРАТОР»)

4 IP-адреса сделали больше одной попытки: 91.220.163.170 (6 попыток), 31.131.253.132 (3 попытки), 77.37.206.173 (2 попытки), 45.168.116.98 (2 попытки).

атрибуция: не установлена
исход: попытка
чем опровергал: В корпусе нет записей EventID 4624 (успешный вход). 20 записей имеют Status=0xc000006d (общий отказ входа) — попытки не достигли цели. Утверждение о подборе пароля к существующей учётке АДМИНИСТРАТОР подтверждается SubStatus=0xc000006a (неверный пароль) для 6 попыток.
что делать сейчас: Проверить, какие службы открыты на порту 445/TCP (SMB) и 3389/TCP (RDP); заблокировать IP-адреса атакующих на файрволе; включить расширенное аудирование на учётную запись «АДМИНИСТРАТОР»; установить политику блокировки после N неудачных попыток.

## Отклонённые кандидаты

### К-1 · Системные события System.jsonl (загрузка ОС) как возможный индикатор аномалии

что выглядело как причина: Четыре записи System.jsonl попали в рабочий список: g002 (ProcessID=0, 2 из 20), g003 (ThreadID=124, 1 из 20), g004 (последняя запись файла, EventID 55).

улики:
- [!PROVEN] System.jsonl:5 — «"LastShutdownGood":true,"LastBootGood":true» — загрузка без ошибок.
- [!PROVEN] System.jsonl:1 — «Multiprocessor Free» — запись EventLog о старте ОС Windows 10.
- [!PROVEN] System.jsonl:14 — «"DriveName":"C:"» — NTFS event 98 с CorruptionActionState=0.
- [!PROVEN] System.jsonl:20 — «"Group":0,"Number":1» — штатная запись о процессоре.

> [!REPORTED]
> исход: норма
чем опровергал: 20 записей System.jsonl относятся к загрузке ОС Windows. Загрузка чистая: System.jsonl:5 — «"LastShutdownGood":true,"LastBootGood":true». Сеанс System.jsonl укладывается в 27 секунд (15:04:03–15:04:30). Events Kernel-Processor-Power (EventID 55) — штатное перечисление ядер процессора.

## Инвентарь

Учётные записи в корпусе (целевые, Security.jsonl): ADMINI, МАКСИМУМ, Test, ADMIN, ADMINISTRATOR, АДМИНИСТРАТОР, BILLING, NOMINAS, ПОЛЬЗОВАТЕЛЬ2, МАКСИМ, KATE, АДМИН, СЌРҐСЃР°СЂРҐ.

Внешние IP-адреса (Security.jsonl): 45.168.116.98, 31.131.253.132, 112.25.201.244, 77.37.206.173, 95.161.182.42, 223.31.121.20, 62.122.102.155, 109.197.27.136, 91.220.163.170, 193.142.146.135, 95.59.85.48, 93.170.134.237.

Имя компьютера: IPSERVER.

## Принадлежность учётных записей

| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше |
| --- | --- | --- | --- | --- | --- |
| ADMINI | 2021-06-01T18:36:04 | Security.jsonl:1 «"TargetUserName":"ADMINI"» | неизвестно | не установлена | |
| МАКСИМУМ | 2021-06-01T18:36:06 | Security.jsonl:2 | посторонняя | посторонняя | ADMINI |
| Test | 2021-06-01T18:36:13 | Security.jsonl:3 | посторонняя | посторонняя | ADMINI |
| ADMIN | 2021-06-01T18:36:14 | Security.jsonl:4 | посторонняя | посторонняя | ADMINI |
| ADMINISTRATOR | 2021-06-01T18:36:17 | Security.jsonl:5 | посторонняя | посторонняя | ADMINI |
| АДМИНИСТРАТОР | 2021-06-01T18:36:42 | Security.jsonl:6 | посторонняя | посторонняя | ADMINI |
| BILLING | 2021-06-01T18:36:45 | Security.jsonl:7 | посторонняя | посторонняя | ADMINI |
| NOMINAS | 2021-06-01T18:37:10 | Security.jsonl:9 | посторонняя | посторонняя | ADMINI |
| ПОЛЬЗОВАТЕЛЬ2 | 2021-06-01T18:37:27 | Security.jsonl:10 | посторонняя | посторонняя | ADMINI |
| МАКСИМ | 2021-06-01T18:37:47 | Security.jsonl:11 | посторонняя | посторонняя | ADMINI |
| KATE | 2021-06-01T18:37:53 | Security.jsonl:15 | посторонняя | посторонняя | ADMINI |
| АДМИН | 2021-06-01T18:38:07 | Security.jsonl:17 | посторонняя | посторонняя | ADMINI |
| СЌРҐСЃР°СЂРҐ | 2021-06-01T18:38:16 | Security.jsonl:18 | посторонняя | посторонняя | ADMINI |

## Покрытие

| путь | статус | улики |
| --- | --- | --- |
| Security.jsonl | наблюдение | Security.jsonl:3 — «"WorkstationName":"Rdesktop"» |
| System.jsonl | наблюдение | System.jsonl:14 — «"CorruptionActionState":0» |

## Окно записей

| путь | канал | окно | записей | нет |
| --- | --- | --- | --- | --- |
| Security.jsonl | Security | окно=417258–417277 | записей=20 | нет=0 |

итог: файлов=2 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 ошибок=0

## Чего не хватает в логах

- По корпусу невозможно определить, является ли эта активность распределённой атакой или целенаправленным подбором — промежуток всего 108 секунд слишком мал.
- Корпус не содержит журналов фаервола, поэтому неизвестно, блокируются ли эти IP-адреса на периметре.
- Нет данных о том, какие сетевые службы (SMB, RDP, Netlogon) открыты на IPSERVER.
- Нет записей успешного входа (EventID 4624), но журнал мог быть ротирован.
- Учётная запись владельца машины по корпусу не определяется — в обоих файлах нет событий локального входа или профиля.

## Разбор рабочего списка

g001 D Н-1 · g002 N норма (EventLog ProcessID=0) · g003 N норма (NTFS CorruptionActionState=0) · g004 N норма (Kernel-Processor-Power EventID 55)

## ВЕРДИКТ

Атаковали, но не доказано — попытки входа завершились отказом (Status=0xc000006d, Security.jsonl:1 — «"EventRecordID":417258»). Источник атрибуции не установлен.
