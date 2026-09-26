СИНТЕЗ НЕ ЗАВЕРШЁН

## Находки

### Н-1 · Массовый перебор учётных записей через NTLM (сетевой вход)

- [!INFERENCE] За 108 секунд (18:36:04—18:37:52 UTC 2021-06-01) с 12 внешних IP-адресов выполнено 20 неудачных попыток входа на IPSERVER по протоколу NTLM (LogonType=3, сетевое).
- [!INFERENCE] 6 из них пришлись на существующую учётную запись АДМИНИСТРАТОР с неверным паролем (SubStatus=0xc000006a) — подбор имени, за которым стоит реальная цель.

улики:
- [!PROVEN] Security.jsonl:3 — «onPackageName":"NTLM","WorkstationName":"Rdesktop","TransmittedServices» — единственная запись с WorkstationName=Rdesktop, что нехарактерно для LogonType=3 (сетевое). IP отправителя: 112.25.201.244
- [!PROVEN] Security.jsonl:13 — «IpAddress":"91.220.163.170"» — адрес, выполнивший 5 из 6 попыток с SubStatus=0xc000006a против АДМИНИСТРАТОР
- [!PROVEN] Security.jsonl:6 — «TargetUserName":"АДМИНИСТРАТОР"» — цель с SubStatus=0xc000006a (неверный пароль)

> агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc0000064, Event.EventData.Status=0xc000006d) = 14 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc0000064") catch false) and (try (.Event.EventData.Status != null and (.Event.EventData.Status|tostring) == "0xc000006d") catch false))' -- 'Security.jsonl' | wc -l`

> агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc000006a, Event.EventData.Status=0xc000006d) = 6 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false) and (try (.Event.EventData.Status != null and (.Event.EventData.Status|tostring) == "0xc000006d") catch false))' -- 'Security.jsonl' | wc -l`

> агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, Event.EventData.IpAddress!=-) = 12 · `jq -r 'select((try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`

> агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, Event.EventData.WorkstationName=Rdesktop) = 1 · `jq -r 'select((try (.Event.EventData.WorkstationName != null and (.Event.EventData.WorkstationName|tostring) == "Rdesktop") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`

LogonType=3 (сетевое)
Status=0xc000006d (общий отказ входа)
SubStatus=0xc0000064 (нет такой учётной записи) — 14 записей
SubStatus=0xc000006a (неверный пароль) — 6 записей, все против АДМИНИСТРАТОР

атрибуция: не установлена
исход: попытка

чем опровергал: в корпусе нет событий 4624 (успешный вход) — ни для одной из перечисленных учётных записей. Нет подтверждения, что хотя бы одна попытка достигла цели. Нет событий изменения политик или создания учётных записей в этом окне.

что делать сейчас: проверить полный журнал Security за пределами этого 108-секундного среза — нет ли успешных входов (4624) из адресов 91.220.163.170, 112.25.201.244, 45.168.116.98 и других перечисленных выше. Включить блокировку адресов на межсетевом экране. Убедиться, что для АДМИНИСТРАТОР настроена блокировка после N неудачных попыток.

## Отклонённые кандидаты

### К-1 · Процесс EventLog с ProcessID=0 (System.jsonl:1)

что выглядело как причина: EventID 6009 (версия ОС, 10.0.19042) запущен процессом с ProcessID=0

- [!PROVEN] System.jsonl:1 — «EventID":{"#attributes":{"Qualifiers":32768},"#text":6009},"Version":0,"Level":4» — EventLog, ProcessID=0, ThreadID=0
- [!PROVEN] System.jsonl:2 — «EventID":{"#attributes":{"Qualifiers":32768},"#text":6005},"Version":0,"Level":4» — EventLog, ProcessID=0, ThreadID=0

- [!INFERENCE] исход: норма
чем опровергал: обе записи 6009 и 6005 имеют ProcessID=0 в стандартном поведении. EventLog пишет эти события до того, как его собственный процесс получает PID. Это норма начала работы журнала. Обе записи Timestamp=2021-05-08T15:04:30Z совпадают с загрузкой системы.

### К-2 · NTFS-событие 98 с ThreadID=124 (System.jsonl:14)

что выглядело как причина: EventID 98 (NTFS, проверка тома C:) выполнен с ThreadID=124

- [!PROVEN] System.jsonl:14 — «soft-Windows-Ntfs","Guid":"3FF37A1C-A68D-4D6E-8C9B-F79E8B16C482"}},"EventID":98,"Version":0», «ThreadID":124», «DriveName":"C:»
- [!PROVEN] System.jsonl:17 — «soft-Windows-Ntfs","Guid":"3FF37A1C-A68D-4D6E-8C9B-F79E8B16C482"}},"EventID":98,"Version":0», «ThreadID":308», «DriveName":"\\Device\\HarddiskVolume1»

- [!INFERENCE] исход: норма
чем опровергал: разные тома обрабатываются разными потоками NTFS. Обнаружение томов C: и системного раздела — штатное поведение при загрузке. Разные ThreadID объясняются разными устройствами, не являются аномалией.

### К-3 · Завершающая запись Kernel-Processor-Power (System.jsonl:20)

что выглядело как причина: последняя запись в System.jsonl (EventID 55, Processor-Power, CPU #1)

- [!PROVEN] System.jsonl:20 — «soft-Windows-Kernel-Processor-Power","Guid":"0F67E49F-FE51-4E9F-B490-6F2948CC6027"}},"EventID":55,» — последняя запись, Number=1, NominalFrequency=3600
- [!PROVEN] System.jsonl:19 — «soft-Windows-Kernel-Processor-Power","Guid":"0F67E49F-FE51-4E9F-B490-6F2948CC6027"}},"EventID":55,» — Number=0

- [!INFERENCE] исход: норма
чем опровергал: это штатная запись инициализации питания процессора на втором ядре при загрузке. Перед ней — запись для ядра 0. Журнал не обрывается, а завершается естественным образом по исчерпанию событий загрузки в данном файле. Rollover подтверждает сплошной канал без пропусков.

## Принадлежность учётных записей

В корпусе нет событий создания профилей пользователей, успешных входов (4624) или загрузки профилей (EventID 642). Все записи TargetUserName в Security.jsonl относятся к неудавшимся попыткам входа (EventID 4625) — это словарные имена, вводимые атакующим, а не учётные записи на хосте. Установить владельца или постороннего по корпусу невозможно.

| учётная запись | первое появление | path:line «цитата» | как | вывод | метка | раньше |
|---|---|---|---|---|---|---|---|
| IPSERVER\АДМИНИСТРАТОР | 2021-06-01T18:37:01.691708Z | Security.jsonl:6 «TargetUserName":"АДМИНИСТРАТОР"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\ADMINI | 2021-06-01T18:36:04.949933Z | Security.jsonl:1 «TargetUserName":"ADMINI"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\ADMIN | 2021-06-01T18:36:24.407795Z | Security.jsonl:4 «TargetUserName":"ADMIN"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\ADMINISTRATOR | 2021-06-01T18:36:53.346990Z | Security.jsonl:5 «TargetUserName":"ADMINISTRATOR"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\Test | 2021-06-01T18:36:23.065178Z | Security.jsonl:3 «TargetUserName":"Test"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\BILLING | 2021-06-01T18:37:11.075047Z | Security.jsonl:7 «TargetUserName":"BILLING"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\NOMINAS | 2021-06-01T18:37:23.254393Z | Security.jsonl:9 «TargetUserName":"NOMINAS"» | неизвестно | не определяется | [!PROVEN] | — |
| IPSERVER\KATE | 2021-06-01T18:37:47.396452Z | Security.jsonl:15 «TargetUserName":"KATE"» | неизвестно | не определяется | [!PROVEN] | — |

Примечание: перечисленные учётные записи — это значения TargetUserName в записях 4625 (отказ входа). Наличие SubStatus=0xc000006a для АДМИНИСТРАТОР означает, что эта учётная запись существует, но не доказывает её владельца. Подтвердить, что она принадлежит владельцу или постороннему, корпус не позволяет.

## Покрытие

| путь | статус | улики |
| --- | --- | --- |
| Security.jsonl | наблюдение | Security.jsonl:3 — «onPackageName":"NTLM","WorkstationName":"Rdesktop","TransmittedServices» |
| System.jsonl | наблюдение | System.jsonl:14 — «soft-Windows-Ntfs","Guid":"3FF37A1C-A68D-4D6E-8C9B-F79E8B16C482"}},"EventID":98,"Version":0 |

## Окно записей

итог: файлов=2 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 ошибок=0

## Разбор рабочего списка

g001 D Н-1 · g002 N К-1 · g003 N К-2 · g004 N К-3

## Чего не хватает в данных

- Корпус содержит ровно один 108-секундный срез Security-журнала (20 записей, все EventID 4625) и 27-секундный срез System-журнала (загрузка, 20 записей). Нет данных до или после этих окон.
- Нет событий 4624 (успешный вход). Неизвестно, были ли успешные входы из атакующих адресов за пределами окна.
- Нет событий 4647/4634 (выход из системы), 4720 (создание пользователя), 4722 (включение учётной записи), 4732 (добавление в группу). Невозможно оценить, была ли учётная запись АДМИНИСТРАТОР скомпрометирована до этого окна.
- Нет журналов брандмауэра, сетевого трафика или RDP-подключений. WorkstationName=Rdesktop может указывать на RDP-инструмент, но LogonType=3 (сетевое) не соответствует RDP (тип 10).
- Корпус — экспорт, а не полный журнал. Ротация (rollover) показывает сплошные каналы, но это гарантирует только непрерывность в пределах имеющихся данных, а не их полноту.

## Перечень адресов, имён и путей

| объект | тип | появление | источник |
| --- | --- | --- | --- |
| IPSERVER | имя хоста | System.jsonl:1 | [!PROVEN] System.jsonl:1 — «Computer":"IPSERVER» |
| 112.25.201.244 | внешний IP | Security.jsonl:3 | [!PROVEN] Security.jsonl:3 — «IpAddress":"112.25.201.244"» |
| 45.168.116.98 | внешний IP | Security.jsonl:1 | [!PROVEN] Security.jsonl:1 — «IpAddress":"45.168.116.98"» |
| 31.131.253.132 | внешний IP | Security.jsonl:2 | [!PROVEN] Security.jsonl:2 — «IpAddress":"31.131.253.132"» |
| 77.37.206.173 | внешний IP | Security.jsonl:4 | [!PROVEN] Security.jsonl:4 — «IpAddress":"77.37.206.173"» |
| 95.161.182.42 | внешний IP | Security.jsonl:5 | [!PROVEN] Security.jsonl:5 — «IpAddress":"95.161.182.42"» |
| 223.31.121.20 | внешний IP | Security.jsonl:6 | [!PROVEN] Security.jsonl:6 — «IpAddress":"223.31.121.20"» |
| 62.122.102.155 | внешний IP | Security.jsonl:9 | [!PROVEN] Security.jsonl:9 — «IpAddress":"62.122.102.155"» |
| 109.197.27.136 | внешний IP | Security.jsonl:10 | [!PROVEN] Security.jsonl:10 — «IpAddress":"109.197.27.136"» |
| 91.220.163.170 | внешний IP | Security.jsonl:13 | [!PROVEN] Security.jsonl:13 — «IpAddress":"91.220.163.170"» |
| 193.142.146.135 | внешний IP | Security.jsonl:15 | [!PROVEN] Security.jsonl:15 — «IpAddress":"193.142.146.135"» |
| 95.59.85.48 | внешний IP | Security.jsonl:17 | [!PROVEN] Security.jsonl:17 — «IpAddress":"95.59.85.48"» |
| 93.170.134.237 | внешний IP | Security.jsonl:18 | [!PROVEN] Security.jsonl:18 — «IpAddress":"93.170.134.237"» |
| АДМИНИСТРАТОР | TargetUserName | Security.jsonl:6 | [!PROVEN] Security.jsonl:6 — «TargetUserName":"АДМИНИСТРАТОР"» |
| ADMINI | TargetUserName | Security.jsonl:1 | [!PROVEN] Security.jsonl:1 — «TargetUserName":"ADMINI"» |
| ADMIN | TargetUserName | Security.jsonl:4 | [!PROVEN] Security.jsonl:4 — «TargetUserName":"ADMIN"» |
| ADMINISTRATOR | TargetUserName | Security.jsonl:5 | [!PROVEN] Security.jsonl:5 — «TargetUserName":"ADMINISTRATOR"» |
| Test | TargetUserName | Security.jsonl:3 | [!PROVEN] Security.jsonl:3 — «TargetUserName":"Test"» |
| BILLING | TargetUserName | Security.jsonl:7 | [!PROVEN] Security.jsonl:7 — «TargetUserName":"BILLING"» |
| NOMINAS | TargetUserName | Security.jsonl:9 | [!PROVEN] Security.jsonl:9 — «TargetUserName":"NOMINAS"» |
| KATE | TargetUserName | Security.jsonl:15 | [!PROVEN] Security.jsonl:15 — «TargetUserName":"KATE"» |
| Rdesktop | WorkstationName | Security.jsonl:3 | [!PROVEN] Security.jsonl:3 — «WorkstationName":"Rdesktop"» |

## ВЕРДИКТ

атаковали, но не доказано

Security.jsonl:3, Security.jsonl:6, Security.jsonl:13 — установлен массовый перебор учётных записей с внешних адресов (12 IP) по протоколу NTLM. 6 попыток пришлись на существующую учётную запись АДМИНИСТРАТОР с неверным паролем. Атрибуция атакующего не установлена; событий 4624 (успешный вход) в корпусе нет (предикат `count(Event.System.EventID=4624)` не совпал ни с чем).