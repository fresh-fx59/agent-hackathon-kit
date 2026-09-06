Файл `work/report.md` создать не удалось — запись в файлы запрещена политикой (deny rule: `edit`; как и shell). Отчёт ниже — итог расследования, все цитаты сверены с прочитанными строками корпуса вручную.

---

# Отчёт по корпусу sherlock-winevtx-corpus-v30-normalized-20260821

**ВЕРДИКТ: компрометация не подтверждена и не исключена — зафиксированы успешные RDP-входы локальной учётки `root` (09–11.05.2021) без возможности атрибуции по корпусу; в окне массированного RDP-брутфорса 01–02.06.2021 все зафиксированные попытки входа неуспешны.**

Метод и ограничение: shell и python-инструменты скилла (`logmap.py`, `citecheck.py`, `triagecheck.py`, `logjoin.py`) в этой сессии запрещены политикой (`run_shell_command` отклонён), запись файлов тоже запрещена (`edit`), поэтому `work/report.md` не создан и отчёт сдаётся текстом. Карта собрана вручную штатными инструментами: `glob` по всему дереву (143 файла: 5 корневых + 138 в `rendered/`), выборочное чтение окон `read_file`, поиск с подсчётом совпадений `grep_search` (узкие литералы). Числа совпадений — из заголовков `Found N matches`; там, где поиск прерван по объёму, это сказано явно. Корпус только читался, не изменялся.

---

## 1. Находки

### Н-1 · Массированный удалённый подбор паролей RDP (NTLM, LogonType 3) с внешних адресов, 01–02.06.2021

Что сломано: хост IPSERVER (WORKGROUP, ru-локализация, сборка 10.0.19042) открыт для RDP-подбора: `Security.jsonl` — экспорт журнала аудита из 34 917 строк, доминирующая часть которого — события 4625 «сбой входа». Поток попыток покрывает как минимум 01.06.2021 00:13Z – 02.06.2021 15:25Z (границы экспорта; событий 03.06+ и мая в экспорте нет — проверено поисками `"SystemTime":"2021-06-0[3-9]` и `"SystemTime":"2021-05-`). Подбираемые имена — словарь локальных учёток и типовых имён: ADMIN, ADMINI, ADMINISTRATOR, АДМИНИСТРАТОР, АДМИН, ROOT, USER, TEST, KATE, VIKTOR, OKSANA, МАКСИМ, МАНСУР, ЭЛИЗАБЕТ, ПОЛЬЗОВАТЕЛЬ2, GLAVBUH, BUH1, BILLING, BILLING1, FINANCIAL2, NOMINAS, SALES, OWNER, PASSWORD и др. Провайдер — NTLM через `NtLmSsp`, тип входа 3 (сетевой), порт источника 0.

Корневая причина: RDP-слушатель доступен из интернета без ограничения источников; учётные записи с паролями, допускающими перебор (существование учёток АДМИНИСТРАТОР и ROOT подтверждается подстатусом 0xc000006a «неверный пароль»).

Улики:
- Security.jsonl:1 — `"SystemTime":"2021-06-01T18:36:04.949933Z"`
- Security.jsonl:1 — `"TargetUserName":"ADMINI","TargetDomainName":"","Status":"0xc000006d","FailureReason":"%%2313","SubStatus":"0xc0000064","LogonType":3,"LogonProcessName":"NtLmSsp "`
- Security.jsonl:20001 — `"SystemTime":"2021-06-01T00:13:38.546499Z"`
- Security.jsonl:20001 — `"TargetUserName":"USER","TargetDomainName":"","Status":"0xc000006d","FailureReason":"%%2313","SubStatus":"0xc0000064","LogonType":3,"LogonProcessName":"NtLmSsp "`
- Security.jsonl:30001 — `"TargetUserName":"АДМИНИСТРАТОР","TargetDomainName":"","Status":"0xc000006d","FailureReason":"%%2313","SubStatus":"0xc000006a","LogonType":3,"LogonProcessName":"NtLmSsp "`
- Security.jsonl:2841 — `"TargetUserName":"ROOT","TargetDomainName":"","Status":"0xc000006d","FailureReason":"%%2313","SubStatus":"0xc000006a","LogonType":3,"LogonProcessName":"NtLmSsp "`

Подтверждающие измерения (grep по файлу; файл — 34 917 строк): `"EventID":4625,` — **Found 12889 matches, поиск прерван по объёму вывода** → фактическое число ≥12 889; прочие распознанные типы в файле — 4624 = 185, 4672 = 184, 4634 = 3, 4648 = 2, 4798 = 1 (сумма ≈ 375), т.е. подавляющая часть файла — 4625. `"TargetUserName":"root"` (варианты ROOT/root) — 67 совпадений.

Чем опровергал: гипотезу «разовый инцидент/единичный сканер» опровергает охват: попытки есть и в 00:13, и в 18:36, и в 20:08–20:54, и в 22:51 01.06, и 02.06 в 11:08 (строка 30001) и 14:53–15:24 (строки 13494–13517, 14001–14002) — минимум ~15 часов внутри экспорта, с десятков разных публичных адресов (в просмотренных строках: 45.168.116.98, 31.131.253.132, 77.37.206.173, 91.220.163.170, 91.204.137.106, 94.159.82.34, 95.161.182.42, 112.25.201.244, 168.232.20.77, 193.142.146.135, 195.88.127.82, 202.165.22.28, 80.211.136.242, 95.31.233.42, 45.167.30.124, 208.180.194.241 и др.). Гипотезу «успешный вход в окне» опровергает отсутствие 4624 с LogonType 10/3/2 для пользовательских учёток: поиски `"EventID":4624,.*"LogonType":10`, `"EventID":4624,.*"LogonType":3,` и `"EventID":4624,.*"LogonType":(2|4|7|11|12|13),` дали только виртуальные UMFD-2/DWM-2 (строки 13500, 13504, 13505) и системные тип-5 входы.

Что делать сейчас: ограничить RDP (VPN/firewall по источникам, NLA), включить блокировку учёток после N неудач, сменить пароли учёток АДМИНИСТРАТОР и ROOT, проверить остальные локальные учётки (в т.ч. «1С-словарь»: GLAVBUH, BILLING, NOMINAS и т.п.), выяснить у владельца происхождение учётки root.

атрибуция: не установлена
исход: попытка
чем опровергал: см. выше — проверены альтернативы «разовый случай» и «успех в окне»; ни одна не подтвердилась.
что делать сейчас: описано выше.

---

### Н-2 · Успешные интерактивные RDP-сессии локальной учётки IPSERVER\root (SID …-1001) 09–11.05.2021 с публичных адресов

Что сломано/наблюдение: до волны брутфорса, 09.05.2021 21:43, локальная учётка `root` (домен IPSERVER, RID 1001) выполнила вход в RDP-сессию 2 с адреса 192.99.186.31: LocalSessionManager зафиксировал «вход в сеанс» (событие 21) и «старт оболочки» (событие 22), далее многократные disconnect/reconnect сессий 2 и 3 09–11.05 с адресов 192.99.186.31 и 176.59.42.91; логаут сессии 2 — 11.05.2021 21:43. Та же учётка позже фигурирует в брутфорсе (Н-1) и вводится на экране входа 02.06 (Н-4). Событий создания учётки (4720) в экспорте нет (экспорт начинается 01.06), происхождение учётки корпус не покрывает; сам факт входа означает, что пароль root был известен подключавшемуся.

Корневая причина: не выводится из корпуса — нужен владелец.

Улики:
- rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl:40 — `"SystemTime":"2021-05-09T21:43:09.396401Z"`; в той же строке: `"User":"IPSERVER\\root","SessionID":2,"Address":"192.99.186.31"` (событие 21)
- rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl:41 — `"User":"IPSERVER\\root","SessionID":2,"Address":"192.99.186.31"` (событие 22, старт оболочки)
- rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl:92 — `"SystemTime":"2021-05-11T21:43:02.253804Z"`; в той же строке: `"User":"IPSERVER\\root","SessionID":2` (событие 23, логаут)
- Security.jsonl:13511 — `"TargetUserName":"root","TargetDomainName":"IPSERVER","TargetSid":"S-1-5-21-2929202171-1942120112-2054978817-1001"` (4798, 02.06 — та же учётка вводилась на экране входа)
- Security.jsonl:2841 — `"TargetUserName":"ROOT","TargetDomainName":"","Status":"0xc000006d","FailureReason":"%%2313","SubStatus":"0xc000006a"` (брутфорс против существующей учётки)

Чем опровергал: проверял, не является ли событие 21 «соединением без аутентификации» — нет: событие 21 (session logon) сопровождается событием 22 (shell start notification) для той же учётки и сессии; это штатная пара «вход выполнен, оболочка запущена». Проверял, есть ли в LSM-канале другие пользовательские входы в мае–июне — все 18 событий 21/22/23/24/25 (строки 40–92) относятся к root (сессии 2/3); других имён нет.

Что делать сейчас: установить у владельца, кто подключался 09–11.05 с 192.99.186.31 и 176.59.42.91; сменить пароль root; решить судьбу учётки с Unix-именем на Windows-хосте.

атрибуция: не установлена
исход: успех
чем опровергал: описано выше; альтернатива «это были только попытки соединения» опровергнута парой 21+22.
что делать сейчас: описано выше.

---

### Н-3 · Единичная локальная попытка входа (4625) под случайным именем «0xivfrh1» от NETWORK SERVICE через svchost.exe — за ~2,4 с до RDP-сессии 02.06.2021 14:53:57

Что сломано/наблюдение: в файле ровно одно такое событие (поиск `0xivfrh1` — 1 совпадение). В отличие от всех остальных 4625 (субъект S-1-0-0, удалённый NTLM), здесь субъект — S-1-5-20 (NETWORK SERVICE), учётка IPSERVER$, пакет MICROSOFT_AUTHENTICATION_PACKAGE_V1_0, процесс `C:\Windows\System32\svchost.exe` (PID 0x358), WorkstationName = IPSERVER (локальный), IP-адреса нет; цель — несуществующая учётка со случайным именем (0xc0000064). Время — 02.06.2021 14:53:57.015Z; создание RDP-сессии 2 (UMFD-2/DWM-2, Н-4) началось в 14:53:59. Связь между событиями корпусом не подтверждена (общего идентификатора нет); штатного объяснения вызову LogonUser из svchost под NETWORK SERVICE в корпусе не найдено.

Корневая причина: не установлена.

Улики:
- Security.jsonl:13497 — `"SubjectUserSid":"S-1-5-20","SubjectUserName":"IPSERVER$","SubjectDomainName":"WORKGROUP","SubjectLogonId":"0x3e4","TargetUserSid":"S-1-0-0","TargetUserName":"0xivfrh1","TargetDomainName":"","Status":"0xc000006d","FailureReason":"%%2313","SubStatus":"0xc0000064","LogonType":3,"LogonProcessName":"Advapi  ","AuthenticationPackageName":"MICROSOFT_AUTHENTICATION_PACKAGE_V1_0","WorkstationName":"IPSERVER"`
- Security.jsonl:13497 — `"ProcessId":"0x358","ProcessName":"C:\\Windows\\System32\\svchost.exe","IpAddress":"-","IpPort":"-"`

Чем опровергал: проверил, что событие не дубликат/артефакт экспорта — совпадение единственное; проверил повторяемость имени/субъекта — нет; гипотеза «это сетевой брутфорс» опровергнута субъектом (локальный NETWORK SERVICE, без IP) и именем (не из словаря). Объяснение в рамках корпуса отсутствует.

Что делать сейчас: при доступе к хосту — выяснить, какой svchost-сервис (PID 0x358) выполнял LogonUser (по времени — TermService/планировщик); проверить службы и задачи от имени NETWORK SERVICE; сопоставить с RDP-соединением 14:53:57.

атрибуция: не установлена
исход: попытка
чем опровергал: описано выше.
что делать сейчас: описано выше.

---

### Н-4 · RDP-соединение 02.06.2021 14:53:57–14:54:08 дошло до экрана входа, на котором введена учётка «root»; сессия закрыта без событий аутентификации пользователя

Что сломано/наблюдение: 02.06.2021 14:53:57.016 RCM-Admin фиксирует соединение со слушателем RDP-Tcp с адреса 128.14.134.134 (событие 1158). Через ~2,4 с winlogon создаёт в сессии 2 виртуальные учётки UMFD-2 (Font Driver Host) и DWM-2 (Window Manager) — сессия дошла до интерфейса входа; в 14:53:59.906 LogonUI.exe перечисляет группы локальной учётки root (событие 4798, единственное в файле) — на экране ввода был введён пользователь root. Затем, без каких-либо 4624/4625 для root, в 14:54:07–14:54:08 сессия разорвана: LSM-событие 40 (Session 2, Reason 2147500036 = 0x80004004 E_ABORT) и разлогин UMFD-2/DWM-2 (4634). Соединение оборвано до завершения аутентификации пользователя.

Корневая причина: не установлена (обрыв на этапе CredSSP/ввода учётных данных; по времени совпадает с волной брутфорса).

Улики:
- Security.jsonl:13511 — `"EventID":4798,"Version":0,"Level":0,"Task":13824` … `"TargetUserName":"root","TargetDomainName":"IPSERVER"` … в той же строке: `"CallerProcessId":"0x294","CallerProcessName":"C:\\Windows\\System32\\LogonUI.exe"`; `"SystemTime":"2021-06-02T14:53:59.906256Z"`
- Security.jsonl:13500 — `"TargetUserSid":"S-1-5-96-0-2","TargetUserName":"UMFD-2","TargetDomainName":"Font Driver Host","TargetLogonId":"0x38c5a12f","LogonType":2` (4624, 14:53:59.447)
- Security.jsonl:13514 — `"TargetUserSid":"S-1-5-96-0-2","TargetUserName":"UMFD-2","TargetDomainName":"Font Driver Host","TargetLogonId":"0x38c5a12f","LogonType":2` (4634, 14:54:07.687)
- rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl:105 — `"SystemTime":"2021-06-02T14:54:07.484842Z"`; в той же строке: `"Session":2,"Reason":2147500036`
- rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Admin.jsonl:21 — `"SystemTime":"2021-06-02T14:53:57.016143Z"`; в той же строке: `"Param1":"128.14.134.134"`

Чем опровергал: гипотезу «успешный вход root 02.06» опровергает отсутствие 4624 для root или любого пользователя в эти секунды (полный охват файла поисками по 4624/LogonType, см. Н-1); за 4798 не следует ничего, кроме разрыва; LSM фиксирует событие 40 (разрыв сессии), а не 21/22 (вход+оболочка). Гипотезу «соединение не создавало сессию» опровергает цепочка UMFD-2/DWM-2 (сессия была создана). Кто ввёл root и почему соединение оборвано, корпус не показывает.

Что делать сейчас: при доступе к хосту — логи TermService за 14:53:57–14:54:08 для 128.14.134.134; проверить, не был ли это ручной тест владельца; ужесточить политику входа.

атрибуция: не установлена
исход: попытка
чем опровергал: описано выше.
что делать сейчас: описано выше.

---

## 2. Отклонённые кандидаты

### К-1 · Учётка root создана злоумышленником в окне атаки (июнь)
Что выглядело как причина: Unix-имя учётки на Windows-хосте + ввод root на экране входа 02.06 (4798) + подбор ROOT в брутфорсе.
Улики (опровержение): rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl:40 — `"User":"IPSERVER\\root","SessionID":2,"Address":"192.99.186.31"` — учётка уже входила 09.05, до начала экспорта Security; событий создания учёток (4720) в экспорте Security нет вовсе (поиск `"EventID":4720,` — 0 совпадений).
Чем опровергал: вход root 09.05 доказывает существование учётки до июньской волны; дата создания не покрыта корпусом, поэтому версия «создана в июне» несостоятельна.
исход: норма

### К-2 · Ошибки WMI (5858) — признак вредоносной WMI-активности/поддержания
Что выглядело как причина: 169 совпадений событий 5858/5860 в WMI-Activity (Level 2, ошибки запросов).
Улики (опровержение): rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl:14 — `"Operation":"Start IWbemServices::ExecQuery - ROOT\\CIMV2 : SELECT ID FROM Win32_ServerFeature","ResultCode":"0x80041010"`; путь:76 — `"Operation":"Start IWbemServices::ExecQuery - ROOT\\WMI : SELECT * FROM BatteryStaticData","ResultCode":"0x80041010"`; путь:20 — `"Operation":"Start IWbemServices::ExecQuery - ROOT\\CIMV2 : SELECT State FROM Win32_Service","ResultCode":"0x80041032"`.
Чем опровергал: все просмотренные 5858 — локальные запросы NT AUTHORITY\СИСТЕМА (ClientMachine=IPSERVER) к штатным классам (Win32_Service, Win32_LogicalDisk, Win32_ServerFeature, BatteryStaticData) — мониторинг «здоровья», а не инъекция; ошибки 0x80041010/0x80041032 («класс не найден»/«вызов отменён») типовы для опроса отсутствующих классов. Встречаются и в мае (09.05, строки 73–78), и в июне (02.06, строки 14–20) — фон. Признак устойчивости — событие 5861 (постоянная подписка WMI) отсутствует: поиск `"EventID":5861,` — 0 совпадений.
исход: норма

### К-3 · Защитник Windows отключён / срабатывания (1116–1119) — след атаки
Что выглядело как причина: изменения конфигурации Defender (события 5007) и обновления платформы.
Улики (опровержение): rendered/Microsoft-Windows-Windows-Defender-4Operational.jsonl:318 — `"Old Value":"Default\\IsServiceRunning = 0x0","New Value":"HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\IsServiceRunning = 0x1"` (служба остановлена/запущена в рамках обновления платформы 4.18.2104.10→.14, 14.05.2021 21:54); путь:255 — `"Old Value":"HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\MpEngine\\MpCampGradualRelease = 0x1","New Value":""` (смена канала обновления движка, 13.05).
Чем опровергал: события 5007 относятся к обновлению платформы (13–14.05); после этого состояние 1151 (строка 1143, 29.05) показывает `"RTP state":"Включен"`, OA/IOAV/BM «Включен»; событий обнаружения 1116–1119/2001/2002/2003/5008/1006 в канале нет (поиск по этим ID — только 7 совпадений 5007, все 13–14.05). Признаков отключения защиты или срабатываний нет.
исход: норма

### К-4 · Ошибки служб и Schannel в System.log — следствия атаки/деградация
Что выглядело как причина: 41 событие уровня 1–3 в System.jsonl (из них 34 уровня 1–2), включая ошибки служб и Schannel.
Улики (опровержение): System.jsonl:45 — `"param1":"Служба списка сетей","param2":"%%21"` (7023, 08.05.2021 15:04:31, единичный сбой при старте); System.jsonl:534 — `"EventID":36874,"Version":0,"Level":2,"Task":0` … `"Protocol":"TLS 1.1"` (Schannel, 13.05.2021 01:47, серия по TLS 1.0/1.1).
Чем опровергал: ошибка службы списка сетей — однократная, в момент загрузки 08.05; падений/перезапусков (41, 6008, 1074, 7045, 7040) в System.jsonl нет (поиски — 0 совпадений). События Schannel 36874 — единичный эпизод 13.05 01:47 (TLS 1.0/1.1) — согласуется с приёмом входящих TLS-предложений устаревших протоколов (фоновое сканирование), а не с дефектом сервисов. Корреляции с окном брутфорса (01–02.06) в этих ошибках нет.
исход: норма

### К-5 · Признаки пост-эксплуатации: PowerShell, планировщик, WinRM, WMI-подписки
Что выглядело как причина: классические каналы пост-эксплуатации могли содержать запуски скриптов/задач.
Улики (опровержение): rendered/Windows-PowerShell.jsonl:1 — `HostApplication=powershell.exe -ExecutionPolicy Restricted -Command Write-Host 'Final result: 1';` (видимый запуск PS в классическом журнале — установочная команда 09.05 01:42); rendered/Microsoft-Windows-PowerShell-4Operational.jsonl:1 — `"EventID":40961,"Version":1,"Level":4,"Task":4` (старт консоли PowerShell; событий 4103/4104/400/600 в канале нет — поиск 0 совпадений); поиск по Windows-PowerShell.jsonl шаблонов `DownloadString|Invoke-WebRequest|IEX |net user|schtasks|FromBase64|-enc |Bypass|mimikatz` — 0 совпадений; постоянных WMI-подписок (5861) нет (К-2); в Security нет 4698 (создание задачи), 7045 (установка службы), 1102 (очистка журнала) — поиски дали 0.
Чем опровергал: перечисленными поисками по узким литералам; минус-результаты указаны явно. Ограничение: PowerShell ScriptBlock-логирование выключено, поэтому «нет следов PS» — отсутствие улик, а не доказательство чистоты.
исход: норма

---

## 3. Покрытие

| путь | статус | улики |
| --- | --- | --- |
| Security.jsonl | наблюдение | Security.jsonl:1 `"EventID":4625,`; Security.jsonl:13497 `"TargetUserName":"0xivfrh1"`; Security.jsonl:13511 `"EventID":4798,`; Security.jsonl:13500 `"TargetUserName":"UMFD-2"`; Security.jsonl:2841 `"TargetUserName":"ROOT"` |
| System.jsonl | наблюдение | System.jsonl:45 `"param1":"Служба списка сетей","param2":"%%21"`; System.jsonl:534 `"EventID":36874,` |
| Application.jsonl | наблюдение | Application.jsonl:149 `hr=0x8007139F` |
| Setup.jsonl | наблюдение | Setup.jsonl:1 `"PackageIdentifier":"KB4580325","InitialPackageState":5000` |
| HardwareEvents.jsonl | пусто | строк=0 |
| rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl | наблюдение | путь:40 `"User":"IPSERVER\\root","SessionID":2,"Address":"192.99.186.31"`; путь:105 `"Session":2,"Reason":2147500036` |
| rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Admin.jsonl | пусто | строк=0 |
| rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Operational.jsonl | наблюдение | путь:1 `"EventID":261,"Version":0,"Level":4` … `"listenerName":"RDP-Tcp"` (совпадений 261 — 1964) |
| rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Admin.jsonl | наблюдение | путь:21 `"Param1":"128.14.134.134"`; путь:1 `"Param1":"5.63.117.101"` |
| rendered/Microsoft-Windows-RemoteDesktopServices-RdpCoreTS-4Operational.jsonl | наблюдение | путь:2075 `"EventID":140,"Version":0,"Level":3,"Task":4,"Opcode":14` … `"IPString":"45.167.30.124"`; путь:2077 `"EventName":"Event_Disconnect","ErrorCode":"0x80070040"` |
| rendered/Microsoft-Windows-RemoteDesktopServices-RdpCoreTS-4Admin.jsonl | пусто | строк=0 |
| rendered/Microsoft-Windows-Windows-Defender-4Operational.jsonl | наблюдение | путь:1143 `"RTP state":"Включен"`; путь:318 `"New Value":"HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\IsServiceRunning = 0x1"` |
| rendered/Microsoft-Windows-Windows-Defender-4WHC.jsonl | пусто | строк=0 |
| rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl | наблюдение | путь:14 `"Operation":"Start IWbemServices::ExecQuery - ROOT\\CIMV2 : SELECT ID FROM Win32_ServerFeature"` |
| rendered/Microsoft-Windows-Security-Mitigations-4KernelMode.jsonl | наблюдение | путь:1 `"ChildImagePathName":"C:\\WINDOWS\\system32\\regsvr32.exe"` |
| rendered/Microsoft-Windows-Security-Mitigations-4UserMode.jsonl | пусто | строк=0 |
| rendered/Microsoft-Windows-PowerShell-4Operational.jsonl | наблюдение | путь:1 `"EventID":40961,"Version":1,"Level":4,"Task":4` |
| rendered/Microsoft-Windows-PowerShell-4Admin.jsonl | пусто | строк=0 |
| rendered/Windows-PowerShell.jsonl | наблюдение | путь:1 `HostApplication=powershell.exe -ExecutionPolicy Restricted -Command Write-Host 'Final result: 1';` |
| rendered/Microsoft-Windows-WinRM-4Operational.jsonl | наблюдение | путь:1 `"operationName":"Enumeration","resourceUri":"http://schemas.microsoft.com/wbem/wsman/1/config/listener"` |
| rendered/Microsoft-Windows-Winlogon-4Operational.jsonl | наблюдение | путь:1 `"SubscriberName":"TrustedInstaller"` |
| rendered/Microsoft-Windows-TaskScheduler-4Maintenance.jsonl | наблюдение | путь:1 `"Task":"NT TASK\\Microsoft\\Windows\\.NET Framework\\.NET Framework NGEN v4.0.30319 64 Critical"` |
| rendered/Internet-Explorer.jsonl | пусто | строк=0 |
| rendered/Key-Management-Service.jsonl | пусто | строк=0 |
| rendered/Microsoft-Windows-GroupPolicy-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-HelloForBusiness-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-BitLocker-4BitLocker-Management.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-CodeIntegrity-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-Bits-Client-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-LiveId-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-AAD-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-Biometrics-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SmartCard-DeviceEnum-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-Time-Service-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-WebAuthN-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-User-Device-Registration-4Admin.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SmbClient-4Security.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SMBServer-4Audit.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SmbClient-4Audit.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SMBServer-4Security.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SmbClient-4Connectivity.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SMBClient-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SMBServer-4Operational.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-SMBServer-4Connectivity.jsonl | не смотрел | причина=выборка |
| rendered/Microsoft-Windows-Application-Experience-4Program-Compatibility-Assistant.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Application-Experience-4Program-Compatibility-Troubleshooter.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Application-Experience-4Program-Inventory.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Application-Experience-4Program-Telemetry.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Application-Experience-4Steps-Recorder.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppModel-Runtime-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppReadiness-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppReadiness-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppXDeployment-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppXDeploymentServer-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppXDeploymentServer-4Restricted.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-AppxPackaging-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-CloudStore-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Containers-BindFlt-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Containers-Wcifs-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-CoreSystem-SmsRouter-Events-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Crypto-DPAPI-4BackUpKeySvc.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Crypto-DPAPI-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Crypto-NCrypt-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-DeviceSetupManager-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-DeviceSetupManager-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Dhcp-Client-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Dhcpv6-Client-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Diagnosis-DPS-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Diagnosis-Scheduled-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-DiskDiagnosticDataCollector-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-FileHistory-Core-4WHC.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-HotspotAuth-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-Boot-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-EventTracing-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-PnP-4Configuration.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-PnP-4Driver-Watchdog.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-Power-4Thermal-Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-ShimEngine-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-StoreMgr-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-WHEA-4Errors.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Kernel-WHEA-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Known-Folders-API-Service.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-LanguagePackSetup-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-MUI-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-MUI-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-NCSI-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-NetworkProfile-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Ntfs-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Ntfs-4WHC.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Partition-4Diagnostic.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-PrintService-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Privacy-Auditing-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Program-Compatibility-Assistant-4CompatAfterUpgrade.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4AutoPilot.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4ManagementService.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-PushNotification-Platform-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-PushNotification-Platform-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-ReadyBoost-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Resource-Exhaustion-Detector-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-RestartManager-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Security-SPP-UX-Notifications-4ActionCenter.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-SettingSync-4Debug.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-SettingSync-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Shell-ConnectedAccountState-4ActionCenter.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Shell-Core-4ActionCenter.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Shell-Core-4AppDefaults.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Shell-Core-4LogonTasksChannel.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Shell-Core-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-ShellCommon-StartLayoutPopulation-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-StateRepository-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-StateRepository-4Restricted.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Storage-Storport-4Health.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Storage-Storport-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-StorageSpaces-Driver-4Diagnostic.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-StorageSpaces-Driver-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-StorageSpaces-ManagementAgent-4WHC.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Store-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Storsvc-4Diagnostic.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-TerminalServices-PnPDevices-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-TerminalServices-PnPDevices-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-TerminalServices-ServerUSBDevices-4Admin.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-TerminalServices-ServerUSBDevices-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-TWinUI-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-TZSync-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-UniversalTelemetryClient-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-User-Profile-Service-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-UserPnp-4ActionCenter.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-UserPnp-4DeviceInstall.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-VolumeSnapshot-Driver-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Wcmsvc-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-WER-PayloadHealth-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4ConnectionSecurity.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4FirewallDiagnostics.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-WindowsBackup-4ActionCenter.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-WindowsSystemAssessmentTool-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-WindowsUpdateClient-4Operational.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-WinINet-Config-4ProxyConfigChanged.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Windows-WorkFolders-4WHC.jsonl | не смотрел | причина=область |
| rendered/Microsoft-Client-Licensing-Platform-4Admin.jsonl | не смотрел | причина=область |

## 4. Разбор рабочего списка

Формальный `worklist.tsv` не создан: генератор карты (`logmap.py`) недоступен (shell запрещён). Ниже — вердикты по группам источников из ручной триажной карты (опоры — в разделах 1–3):

- Security.jsonl (4625-поток) — дефект, Н-1 (попытка; опора — цитаты строк 1/20001/30001/2841 и подсчёты).
- Security.jsonl (4624/4672 тип-5, службы) — фон: 4624=185, 4672=184, пары «запуск службы»; пользовательских входов нет (норма).
- Security.jsonl (4634/4648/4798, UMFD-2/DWM-2 02.06 14:53–14:54) — Н-4 (попытка).
- Security.jsonl (0xivfrh1, строка 13497) — Н-3 (попытка), единичное событие.
- Security.jsonl (минус-результаты: 4720/4740/1102/4776/4698/4697/7045, 4624-тип10/3 для пользователей) — 0 совпадений, норма-фон.
- System.jsonl — норма: ошибки при старте 08.05 (7023/7030), Schannel 36874 13.05 (К-4); 7045/7040/41/6008/1074 нет.
- Application.jsonl — норма: SPP-активация 8198 (hr=0x8007139F, ежедневно), CAPI2 257, ESENT — фон; WER/Application Error/.NET Runtime (1000–1002) нет.
- Setup.jsonl — норма: установка KB4580325/KB4601050/KB4601554/KB5003242/KB5003173 (09–11.05, WindowsUpdateAgent).
- TerminalServices-LocalSessionManager-Op — Н-2 (root-сессии 09–11.05), Н-4 (разрыв 02.06).
- RemoteConnectionManager-Admin — Н-1/Н-4 (1158: входящие соединения, включая 128.14.134.134 02.06 14:53:57).
- RemoteConnectionManager-Op — фон-аномалия: 1964 события 261 (listener RDP-Tcp) в срезе ~19:23–19:24 02.06 — высокая плотность входящих подключений; согласуется с Н-1 (волна продолжалась вечером 02.06, за пределами экспорта Security).
- RdpCoreTS-Op — фон-аномалия в пользу Н-1: срез 21:05–21:09 02.06, ошибки 140/143/226 (разрывы соединений, включая IP 45.167.30.124, встречающийся и в 4625).
- Windows-Defender-Op/WHC — норма (К-3): обновление платформы 13–14.05, защита включена, детектов нет.
- WMI-Activity-Op — норма (К-2): локальные SYSTEM-запросы 5858 (май–июнь), 5861=0.
- PowerShell (все три канала) — норма (К-5): ScriptBlock-логирование выключено; подозрительных команд нет.
- WinRM/GroupPolicy/Winlogon/TaskScheduler-Maintenance/Security-Mitigations — норма по просмотренным окнам; детального разбора нет (причина=выборка).
- Остальные каналы rendered/ — не разобраны (причина=область/выборка), перечислены в разделах 3 и 5.

## 5. Чего я не знаю

- Точное число событий 4625: подсчёт grep оборван на 12 889 совпадениях по объёму вывода (`[Search did not complete]`); в файле 34 917 строк, сумма распознанных не-4625 типов ≈ 375, поэтому фактическое число — порядка 34,5 тыс., но это оценка, не измерение.
- Что происходило после 02.06.2021 ~15:25 (конец экспорта Security.jsonl): LSM-канал обрывается на 02.06 14:54; срезы RCM-Op (19:23–19:24) и RdpCoreTS-Op (21:05–21:09+) показывают продолжение плотных RDP-подключений вечером 02.06, но событий аутентификации за это время в корпусе нет — успешность/неуспешность этих соединений неизвестна.
- Кто такой root и кто входил 09–11.05 с 192.99.186.31/176.59.42.91: владелец или злоумышленник — корпус не различает (предыстории до 01.06 в экспорте Security нет; событий создания учётки нет).
- Причина события 0xivfrh1 (svchost.exe/NETWORK SERVICE, LogonUser случайного имени, 02.06 14:53:57) не установлена; связь с параллельным RDP-соединением не подтверждена (общий идентификатор отсутствует).
- Почему RDP-сессия 02.06 14:53–14:54 (128.14.134.134, ввод root) оборвалась без событий 4624/4625 — неизвестно (нет данных TermService/CredSSP ниже уровня журналов).
- Полные списки атакующих адресов и подбираемых имён не составлены: нужен полный проход по 34 917 строкам (logmap/logjoin), недоступный без shell. Приведённые адреса — из просмотренных строк.
- PowerShell ScriptBlock-логирование (4104) выключено — невидимость скриптов PS есть отсутствие улик, а не чистота.
- Не разобраны (причина=область/выборка): большинство телеметрических/прикладных каналов rendered/ (полный перечень в разделе «Покрытие»). Формальные проверки `citecheck`/`triagecheck` не запускались (shell запрещён); цитаты перепроверены вручную по адресам. Файл `work/report.md` не создан (запись запрещена политикой `edit`), отчёт сдан в сообщении.
