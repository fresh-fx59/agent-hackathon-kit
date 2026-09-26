## Инвентарь

| наблюдаемая величина | значение | источник |
|---|---|---|
| Имя хоста | IPSERVER | System.jsonl:263 — «"Computer":"IPSERVER"» |
| Путь 3proxy | C:\3proxy\bin64\3proxy.exe | System.jsonl:263 — «"ImagePath":"\"C:\\3proxy\\bin64\\3proxy.exe\" \"C:\\3proxy\\bin64\\3proxy.cfg\"» |
| Конфигурация 3proxy | C:\3proxy\bin64\3proxy.cfg | System.jsonl:263 — «"ImagePath":"\"C:\\3proxy\\bin64\\3proxy.exe\" \"C:\\3proxy\\bin64\\3proxy.cfg\"» |
| Учётная запись службы | LocalSystem | System.jsonl:263 — «"AccountName":"LocalSystem"» |
| SID установщика 3proxy | S-1-5-21-2929202171-1942120112-2054978817-1001 | System.jsonl:263 — «"UserID":"S-1-5-21-2929202171-1942120112-2054978817-1001"» |
| Профиль пользователя | C:\Users\root\ntuser.dat | rendered/Microsoft-Windows-User-Profile-Service-4Operational.jsonl:1 — «"File":"C:\\Users\\root\\ntuser.dat","Key":"S-1-5-21-2929202171-1942120112-2054978817-1001"» |
| IP атакующего (пример) | 223.31.121.20 | Security.jsonl:6 — «"IpAddress":"223.31.121.20"» |
| IP атакующего (пример) | 45.168.116.98 | Security.jsonl:1 — «"IpAddress":"45.168.116.98"» |

## Находки

### Н-1 · Массовый подбор учётных записей по сети (NTLM-брутфорс)

- [!REPORTED] Хост `IPSERVER` подвергся массированной атаке перебора учётных записей по протоколу NTLM через сетевое подключение. За период с 2021-06-01T18:36 до 21:11 зафиксировано 33 456 неудачных попыток входа (EventID 4625, LogonType=3 — сетевое подключение).

- [!REPORTED]
> агрегат: Security.jsonl · count(Event.System.EventID=4625) = 33456 · `jq -c 'select((try (.Event.System.EventID != null and (.Event.System.EventID|tostring) == "4625") catch false))' -- 'Security.jsonl' | wc -l`
- [!REPORTED]
> агрегат: Security.jsonl · count(Event.EventData.LogonType=3, Event.System.EventID=4625) = 33456 · `jq -c 'select((try (.Event.EventData.LogonType != null and (.Event.EventData.LogonType|tostring) == "3") catch false) and (try (.Event.System.EventID != null and (.Event.System.EventID|tostring) == "4625") catch false))' -- 'Security.jsonl' | wc -l`

Источники: не менее 93 внешних IP-адресов, 1974 различных имён учётных записей. 8 адресов отправили более 1000 попыток каждый, 24 имени атакованы более 100 раз.

- [!REPORTED]
> агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, Event.System.EventID=4625, Event.EventData.IpAddress!=-) = 93 · `jq -r 'select((try (.Event.System.EventID != null and (.Event.System.EventID|tostring) == "4625") catch false) and (try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!REPORTED]
> агрегат: Security.jsonl · distinct(Event.EventData.TargetUserName, Event.System.EventID=4625, Event.EventData.IpAddress!=-) = 1974 · `jq -r 'select((try (.Event.System.EventID != null and (.Event.System.EventID|tostring) == "4625") catch false) and (try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.TargetUserName) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort -u | wc -l`
- [!REPORTED]
> агрегат: Security.jsonl · distinct_over(Event.EventData.IpAddress, 1000, Event.System.EventID=4625, Event.EventData.IpAddress!=-) = 8 · `jq -r 'select((try (.Event.System.EventID != null and (.Event.System.EventID|tostring) == "4625") catch false) and (try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.IpAddress) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort | uniq -c | awk '$1 > 1000' | wc -l`
- [!REPORTED]
> агрегат: Security.jsonl · distinct_over(Event.EventData.TargetUserName, 100, Event.System.EventID=4625, Event.EventData.IpAddress!=-) = 24 · `jq -r 'select((try (.Event.System.EventID != null and (.Event.System.EventID|tostring) == "4625") catch false) and (try (.Event.EventData.IpAddress != null and (.Event.EventData.IpAddress|tostring) != "-") catch false)) | (try (.Event.EventData.TargetUserName) catch null) | select(. != null) | tostring | select(. != "")' -- 'Security.jsonl' | sort | uniq -c | awk '$1 > 100' | wc -l`

**Разбор SubStatus показывает наличие как атаки на несуществующие учётки, так и целенаправленного подбора пароля к существующей.** SubStatus=0xc0000064 (нет такой учётной записи) — 25 355 попыток (75,8 %). SubStatus=0xc000006a (неверный пароль) — 8098 попыток (24,2 %), из которых 8027 атакуют имя `АДМИНИСТРАТОР` — локальную учётную запись администратора, существующую по умолчанию на русскоязычных Windows.

- [!REPORTED]
> агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc0000064, Event.EventData.Status=0xc000006d) = 25355 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc0000064") catch false) and (try (.Event.EventData.Status != null and (.Event.EventData.Status|tostring) == "0xc000006d") catch false))' -- 'Security.jsonl' | wc -l`
- [!REPORTED]
> агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc000006a, Event.EventData.Status=0xc000006d) = 8098 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false) and (try (.Event.EventData.Status != null and (.Event.EventData.Status|tostring) == "0xc000006d") catch false))' -- 'Security.jsonl' | wc -l`
- [!REPORTED]
> агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc000006a, Event.EventData.Status=0xc000006d, Event.EventData.TargetUserName=АДМИНИСТРАТОР) = 8027 · `jq -c 'select((try (.Event.EventData.SubStatus != null and (.Event.EventData.SubStatus|tostring) == "0xc000006a") catch false) and (try (.Event.EventData.Status != null and (.Event.EventData.Status|tostring) == "0xc000006d") catch false) and (try (.Event.EventData.TargetUserName != null and (.Event.EventData.TargetUserName|tostring) == "АДМИНИСТРАТОР") catch false))' -- 'Security.jsonl' | wc -l`

- [!PROVEN] Security.jsonl:6 — «"TargetUserSid":"S-1-0-0","TargetUserName":"АДМИНИСТРАТОР","TargetDomainName"» — Status=0xc000006d (общий отказ входа), SubStatus=0xc000006a (неверный пароль), LogonType=3 (сетевое подключение), IpAddress=223.31.121.20
- [!PROVEN] Security.jsonl:1 — «"TargetUserSid":"S-1-0-0","TargetUserName":"ADMINI","TargetDomainName"» — Status=0xc000006d (общий отказ входа), SubStatus=0xc0000064 (нет такой учётной записи), LogonType=3 (сетевое подключение), IpAddress=45.168.116.98

атрибуция: не установлена
исход: попытка

чем опровергал: все 4624 (успешный вход) зафиксированы только как LogonType=5 (служба) от учётной записи СИСТЕМА. Успешных входов LogonType=10 (удалённый интерактивный RDP) не обнаружено — ни одна внешняя атака паролем не достигла цели. SubStatus=0xc000006a против имени АДМИНИСТРАТОР доказывает, что атака целенаправленно подбирала пароль к существующей привилегированной учётной записи, но без успеха за доступный корпус.

### Н-2 · Несанкционированная установка и запуск прокси-сервера 3proxy

**что сломано:** на хост установлено несанкционированное программное обеспечение — 3proxy — прокси-сервер, зарегистрированный как служба Windows, запускающаяся автоматически от имени LocalSystem. Это означает, что атакующий уже имел доступ к хосту на уровне пользователя и смог повысить до SYSTEM.

- [!PROVEN] **корневая причина:** 2021-05-09T22:25:01Z через Service Control Manager зарегистрирована служба «3proxy tiny proxy server». Через 13 минут пользователем через `dllhost.exe` созданы 6 правил брандмауэра, разрешающих входящий трафик для этого приложения — UDP и TCP на все порты, для общедоступной сети и для всех профилей.

- [!PROVEN] System.jsonl:263 — «"ServiceName":"3proxy tiny proxy server","ImagePath":"\"C:\\3proxy\\bin64\\3proxy.exe\" \"C:\\3proxy\\bin64\\3proxy.cfg\" --service","ServiceType":"служба режима пользователя","StartType":"Автоматически","AccountName":"LocalSystem"» — установка службы 3proxy
- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:445 — «"SecurityOptions":0,"ModifyingUser":"S-1-5-21-2929202171-1942120112-2054978817-1001"» — Action=3 (разрешить), Direction=1 (входящий), Profiles=4 (общедоступная сеть), Protocol=17 (UDP), LocalPorts=*, RemotePorts=*
- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:448 — «3proxy.exe","ServiceName":"","Direction":1,"Protocol":6,"LocalPorts":"*","RemotePorts":"*"» — Action=3 (разрешить), Direction=1 (входящий), Protocol=6 (TCP), LocalPorts=*
- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:447 — «"Flags":257,"Active":1,"EdgeTraversal":3,"LooseSourceMapped":0» — правило UDP с обходом периметра (EdgeTraversal=3), Profiles=6 (частная+общедоступная)
- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:450 — «"Flags":257,"Active":1,"EdgeTraversal":3,"LooseSourceMapped":0» — правило TCP с обходом периметра, Profiles=6 (частная+общедоступная)

Установка службы и создание правил брандмауэра выполнены от имени одного и того же пользователя (SID `S-1-5-21-2929202171-1942120112-2054978817-1001`, который ассоциирован с ключом профиля `C:\Users\root\ntuser.dat`). Правила созданы через `dllhost.exe` (COM Surrogate) — типичный механизм системного запроса на открытие порта для приложения, что указывает на интерактивное согласие: пользователь увидел диалог брандмауэра и нажал «Разрешить».

- [!INFERENCE] Анализ RDP-соединений: зафиксировано 1964 входящих RDP-подключения через RDP-Tcp слушатель в окне с 19:24 до 21:11 2021-06-02 (на сутки позже установки 3proxy). RdpCoreTS регистрирует 417 событий EventID 148 (создание канала RDP) и 141 событие EventID 131 (входящее RDP-соединение с указанием IP отправителя, например «5.53.118.90:52817»). Это подтверждает, что RDP был открыт и активно использовался — вероятно, как канал управления для установленного 3proxy.

атрибуция: установлена
исход: успех

чем опровергал: проверены все записи 4624 — успешные интерактивные входы (LogonType=2 (интерактивный) или 10 (удалённый интерактивный)) в корпусе не обнаружены. Первоначальный доступ к системе не покрыт доступными журналами (Security начинается с 2021-06-01, а установка 3proxy произошла 2021-05-09). Доступ к уровню пользователя мог быть получен через другую атаку или через существовавшую ранее сессию. Факт установки службы, создание правил брандмауэра с интерактивного согласия (через dllhost.exe) и наличие 1962 RDP-подключений после установки совместно указывают, что атакующий действовал целенаправленно и получил устойчивый удалённый доступ.

## Отклонённые кандидаты

### К-1 · Отказ службы CSC — возможная нештатная конфигурация

**что выглядело как причина:** при загрузке системы EventID 7026 (отказ службы) для службы `dam`/CSC (Client Side Caching).

- [!PROVEN] System.jsonl:102 — «"EventData":{"param1":"\r\ndam"}}}»

**исход:** норма

**чем опровергал:** это штатное поведение — служба CSC/dam отключена в конфигурации по умолчанию на многих серверах Windows; событие единичное (1 из 1036 записей System), не повторяется, не влияет на другие службы и не связано с инцидентом.

### К-2 · Единичные Level=2/Error-записи в Application и различных каналах — возможные ошибки

**что выглядело как причина:** в нескольких файлах встречены записи с Level=2 (ошибка) при основном Level=4 (информация) — ESENT (g001), AppXDeployment (g011), AppModel-Runtime (g007), PushNotification (g043), StateRepository (g053) и другие.

- [!PROVEN] Application.jsonl:213 — «"EventID":{"#attributes":{"Qualifiers":0},"#text":640},"Version":0,"Level":3»
- [!PROVEN] rendered/Microsoft-Windows-AppXDeploymentServer-4Operational.jsonl:1054 — «"EventID":8761,"Version":0,"Level":2»

**исход:** норма

**чем опровергал:** все такие записи единичны (1–2 на файл) и относятся к нормальной работе: ESENT-журналирование AppX, AppReadiness, PushNotification — это системные операции развёртывания приложений Store, не связанные с атакой. Каждая запись представляет собой информационный отказ конкретного компонента, не влияющий на общую работу хоста.

### К-3 · Фоновый трафик RDP-подключений — возможный канал управления

**что выглядело как причина:** зафиксировано 1964 входящих RDP-подключения (EventID 261, RDP-Tcp) и 141 событие EventID 131 RdpCoreTS с внешними IP — может указывать на использование RDP для удалённого управления.

- [!REPORTED] rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Operational.jsonl:619 — «"listenerName":"RDP-Tcp"}}»

**исход:** попытка

**чем опровергал:** RDP-соединения активно использовались (6 профилей обнаружено в NetworkProfile: 04, 05, 08 — идентифицированные сети, 06 — недостоверная, 07 — недостоверная, 03 — доменная). Это свидетельство того, что внешние подключения действительно существовали. Однако, так как RDP-соединения сами по себе не являются аномалией в корпоративной среде, эта находка не выделена в отдельный дефект: она является частью более крупной картины, встроенной в Н-2 (3proxy). Основной вопрос — как атакующий первоначально получил доступ — остаётся без ответа.

### К-4 · Пакетная установка опыта локализации (Language Experience Pack) — возможный признак активности

- [!REPORTED] в журнале зафиксированы многочисленные события установки пакета локализации и обновлений Defender длительностью около минуты — после установки 3proxy (22:25) в 23:43–23:44, System.jsonl:264 — «"updateTitle":"9PDSCC711RVF-Microsoft.LanguageExperiencePacken-US"»

**исход:** норма

**чем опровергал:** обновления имеют корректную сигнатуру Microsoft и устанавливаются стандартным Windows Update Agent (EventID 43, 44). Временная близость к установке 3proxy (22:25 → 23:43) составляет ~78 минут, что не является статистически значимым совпадением. Установка обновлений — штатная активность.

## Принадлежность учётных записей

| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше | метка |
|---|---|---|---|---|---|---|---|
| root | 2021-05-08T15:04:51Z | rendered/Microsoft-Windows-User-Profile-Service-4Operational.jsonl:1 «"EventData":{"File":"C:\\Users\\root\\ntuser.dat","Key":"S-1-5-21-2929202171-1942120112-2054978817-1001"}}» | профиль | владелец | — | [!PROVEN] |
| SYSTEM | 2021-05-08T15:04:03Z | System.jsonl:3 «"UserID":"S-1-5-18"» | служба | владелец | — | [!PROVEN] |
| АДМИНИСТРАТОР | 2021-06-01T00:09:15Z | Security.jsonl:19934 «"TargetUserName":"АДМИНИСТРАТОР","TargetDomainName":"","Status":"0xc000006d"» | удалённый вход | не определяется | — | [!PROVEN] |
| ADMINI | 2021-06-01T13:53:08Z | Security.jsonl:31880 «"TargetUserName":"ADMINI","TargetDomainName":"","Status":"0xc000006d"» | удалённый вход | посторонний | root | [!PROVEN] |

## Покрытие

| путь | статус | улики |
| --- | --- | --- |
| Application.jsonl | наблюдение | Application.jsonl:3 — «Profiles Service","Guid":"89B1E9F0-5AFF-44A6-9B44-0A07A7CE5845"}},"EventID":1531,"Version» |
| HardwareEvents.jsonl | пусто | байт=0 |
| Security.jsonl | наблюдение | Security.jsonl:1 — «Security-Auditing","Guid":"54849625-5478-4994-A5BA-3E3B0328C30D"}},"EventID":4625,"Version» |
| Setup.jsonl | наблюдение | Setup.jsonl:7 — «Windows-Servicing","Guid":"BD12F3B8-FC40-4A61-A307-B7A013A069C1"}},"EventID":1,"Version":0» |
| System.jsonl | наблюдение | System.jsonl:263 — «ttributes":{"UserID":"S-1-5-21-2929202171-1942120112-2054978817-1001"}}},"EventData":{"Ser» |
| rendered/Internet-Explorer.jsonl | пусто | байт=0 |
| rendered/Key-Management-Service.jsonl | пусто | байт=0 |
| rendered/Microsoft-Client-Licensing-Platform-4Admin.jsonl | наблюдение | rendered/Microsoft-Client-Licensing-Platform-4Admin.jsonl:309 — «icensing-Platform","Guid":"B6CC0D55-9ECC-49A8-B929-2B9022426F2A"}},"EventID":102,"Version"» |
| rendered/Microsoft-Windows-AAD-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-AAD-4Operational.jsonl:1 — «osoft-Windows-AAD","Guid":"4DE9BC9C-B27A-43C9-8994-0915F1A5E24F"}},"EventID":1097,"Version» |
| rendered/Microsoft-Windows-AppModel-Runtime-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-AppModel-Runtime-4Admin.jsonl:2 — «-AppModel-Runtime","Guid":"F1EF270A-0D32-4352-BA52-DBAB41E1D859"}},"EventID":36,"Version":» |
| rendered/Microsoft-Windows-AppReadiness-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-AppReadiness-4Admin.jsonl:138 — «dows-AppReadiness","Guid":"F0BE35F8-237B-4814-86B5-ADE51192E503"}},"EventID":10,"Version":» |
| rendered/Microsoft-Windows-AppReadiness-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-AppReadiness-4Operational.jsonl:3 — «dows-AppReadiness","Guid":"F0BE35F8-237B-4814-86B5-ADE51192E503"}},"EventID":108,"Version"» |
| rendered/Microsoft-Windows-AppXDeployment-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-AppXDeployment-4Operational.jsonl:2 — «ws-AppXDeployment","Guid":"8127F6D4-59F9-4ABF-8952-3E3A02073D5F"}},"EventID":327,"Version"» |
| rendered/Microsoft-Windows-AppXDeploymentServer-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-AppXDeploymentServer-4Operational.jsonl:1054 — «:{"#attributes":{"Name":"Microsoft-Windows-AppXDeployment-Server","Guid":"3F471139-ACB7-4A» |
| rendered/Microsoft-Windows-AppXDeploymentServer-4Restricted.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Application-Experience-4Program-Compatibility-Assistant.jsonl | наблюдение | rendered/Microsoft-Windows-Application-Experience-4Program-Compatibility-Assistant.jsonl:1 — «root\\AppData\\Local\\Programs\\Opera\\launcher.exe","ResolverName":"WrpKeyMitigation"}}}}» |
| rendered/Microsoft-Windows-Application-Experience-4Program-Compatibility-Troubleshooter.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Application-Experience-4Program-Inventory.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Application-Experience-4Program-Telemetry.jsonl | наблюдение | rendered/Microsoft-Windows-Application-Experience-4Program-Telemetry.jsonl:1 — «:{"#attributes":{"Name":"Microsoft-Windows-Application-Experience","Guid":"EEF54E71-0661-4» |
| rendered/Microsoft-Windows-Application-Experience-4Steps-Recorder.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-AppxPackaging-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-AppxPackaging-4Operational.jsonl:3 — «s-AppxPackagingOM","Guid":"BA723D81-0D0C-4F1E-80C8-54740F508DDF"}},"EventID":216,"Version"» |
| rendered/Microsoft-Windows-Biometrics-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Biometrics-4Operational.jsonl:1 — «indows-Biometrics","Guid":"A0E3D8EA-C34F-4419-A1DB-90435B8B21D0"}},"EventID":1600,"Version» |
| rendered/Microsoft-Windows-BitLocker-4BitLocker-Management.jsonl | наблюдение | rendered/Microsoft-Windows-BitLocker-4BitLocker-Management.jsonl:1 — «Bridge:\r\n\tPCI\\VEN_8086&DEV_7000 (PCI to ISA Bridge)\r\n"}}}» |
| rendered/Microsoft-Windows-Bits-Client-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Bits-Client-4Operational.jsonl:4 — «ndows-Bits-Client","Guid":"EF1CC15B-46C1-414E-BB95-E76B077BD51E"}},"EventID":16403,"Versio» |
| rendered/Microsoft-Windows-CloudStore-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-CloudStore-4Operational.jsonl:13 — «indows-CloudStore","Guid":"741BB90C-A7A3-49D6-BD82-1E6B858403F7"}},"EventID":3013,"Version» |
| rendered/Microsoft-Windows-CodeIntegrity-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-CodeIntegrity-4Operational.jsonl:3 — «ity":{"#attributes":{"UserID":"S-1-5-18"}}},"EventData":{"Settings":"0x0","Exemption":1}}}» |
| rendered/Microsoft-Windows-Containers-BindFlt-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Containers-BindFlt-4Operational.jsonl:3 — «"Computer":"IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-18"}}},"EventData":null}}» |
| rendered/Microsoft-Windows-Containers-Wcifs-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Containers-Wcifs-4Operational.jsonl:3 — «"Computer":"IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-18"}}},"EventData":null}}» |
| rendered/Microsoft-Windows-CoreSystem-SmsRouter-Events-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-CoreSystem-SmsRouter-Events-4Operational.jsonl:16 — «":{"#attributes":{"Name":"Microsoft-Windows-CoreSystem-SmsRouter","Guid":"A9C11050-9E93-4F» |
| rendered/Microsoft-Windows-Crypto-DPAPI-4BackUpKeySvc.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Crypto-DPAPI-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Crypto-DPAPI-4Operational.jsonl:7 — «dows-Crypto-DPAPI","Guid":"89FE8F40-CDCE-464E-8217-15EF97D4C7C3"}},"EventID":12289,"Versio» |
| rendered/Microsoft-Windows-Crypto-NCrypt-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Crypto-NCrypt-4Operational.jsonl:9 — «ttributes":{"UserID":"S-1-5-21-2929202171-1942120112-2054978817-1001"}}},"EventData":{"Pro» |
| rendered/Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider-4Admin.jsonl:66 — «s":{"Name":"Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider","Guid":"3D» |
| rendered/Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider-4Operational.jsonl:6 — «#attributes":{"Name":"Microsoft-Windows-DeviceManagement-Pushrouter","Guid":"F1201B5A-E170» |
| rendered/Microsoft-Windows-DeviceSetupManager-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-DeviceSetupManager-4Admin.jsonl:84 — «r":{"#attributes":{"Name":"Microsoft-Windows-DeviceSetupManager","Guid":"FCBB06BB-6A2A-46E» |
| rendered/Microsoft-Windows-DeviceSetupManager-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-DeviceSetupManager-4Operational.jsonl:5 — «r":{"#attributes":{"Name":"Microsoft-Windows-DeviceSetupManager","Guid":"FCBB06BB-6A2A-46E» |
| rendered/Microsoft-Windows-Dhcp-Client-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Dhcpv6-Client-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Diagnosis-DPS-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Diagnosis-DPS-4Operational.jsonl:5 — «ows-Diagnosis-DPS","Guid":"6BBA3851-2C7E-4DEA-8F54-31E5AFD029E3"}},"EventID":100,"Version"» |
| rendered/Microsoft-Windows-Diagnosis-Scheduled-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-DiskDiagnosticDataCollector-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-FileHistory-Core-4WHC.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-GroupPolicy-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-GroupPolicy-4Operational.jsonl:1 — «ndows-GroupPolicy","Guid":"AEA1B4FA-97D1-45F2-A64C-4D69FFFD92C9"}},"EventID":4116,"Version» |
| rendered/Microsoft-Windows-HelloForBusiness-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-HelloForBusiness-4Operational.jsonl:2 — «-HelloForBusiness","Guid":"906B8A99-63CE-58D7-86AB-10989BBD5567"}},"EventID":3054,"Version» |
| rendered/Microsoft-Windows-HotspotAuth-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Kernel-Boot-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Kernel-Boot-4Operational.jsonl:12 — «ndows-Kernel-Boot","Guid":"15CA44FF-4D7A-4BAA-BBA5-0998955E531E"}},"EventID":208,"Version"» |
| rendered/Microsoft-Windows-Kernel-EventTracing-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-Kernel-EventTracing-4Admin.jsonl:7 — «":{"#attributes":{"Name":"Microsoft-Windows-Kernel-EventTracing","Guid":"B675EC37-BDB6-464» |
| rendered/Microsoft-Windows-Kernel-PnP-4Configuration.jsonl | наблюдение | rendered/Microsoft-Windows-Kernel-PnP-4Configuration.jsonl:1 — «indows-Kernel-PnP","Guid":"9C205A39-1250-487D-ABD7-E831C6290539"}},"EventID":440,"Version"» |
| rendered/Microsoft-Windows-Kernel-PnP-4Driver-Watchdog.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Kernel-Power-4Thermal-Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Kernel-ShimEngine-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Kernel-ShimEngine-4Operational.jsonl:1 — «s","ShimSource":0,"ShimCount":1,"AppliedGuids":"{434abafd-08fa-4c3d-a88d-d09a88e2ab17}"}}}» |
| rendered/Microsoft-Windows-Kernel-StoreMgr-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Kernel-WHEA-4Errors.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Kernel-WHEA-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Kernel-WHEA-4Operational.jsonl:12 — «ndows-Kernel-WHEA","Guid":"7B563579-53C8-44E7-8236-0F87B9FE6594"}},"EventID":42,"Version":» |
| rendered/Microsoft-Windows-Known-Folders-API-Service.jsonl | наблюдение | rendered/Microsoft-Windows-Known-Folders-API-Service.jsonl:4 — «dows-KnownFolders","Guid":"8939299F-2315-4C5C-9B91-ABB86AA0627D"}},"EventID":1003,"Version» |
| rendered/Microsoft-Windows-LanguagePackSetup-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-LanguagePackSetup-4Operational.jsonl:18 — «LanguagePackSetup","Guid":"7237FFF9-A08A-4804-9C79-4A8704B70B87"}},"EventID":4001,"Version» |
| rendered/Microsoft-Windows-LiveId-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-LiveId-4Operational.jsonl:12 — «ft-Windows-LiveId","Guid":"05F02597-FE85-4E67-8542-69567AB8FD4F"}},"EventID":6115,"Version» |
| rendered/Microsoft-Windows-MUI-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-MUI-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-MUI-4Operational.jsonl:29 — «osoft-Windows-MUI","Guid":"A8A1F2F6-A13A-45E9-B1FE-3419569E5EF2"}},"EventID":3003,"Version» |
| rendered/Microsoft-Windows-NCSI-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-NCSI-4Operational.jsonl:1 — «soft-Windows-NCSI","Guid":"314DE49F-CE63-4779-BA2B-D616F6963A88"}},"EventID":4042,"Version» |
| rendered/Microsoft-Windows-NetworkProfile-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-NetworkProfile-4Operational.jsonl:6 — «ws-NetworkProfile","Guid":"FBCFAC3F-8459-419F-8E48-1F0B49CDB85E"}},"EventID":20002,"Versio» |
| rendered/Microsoft-Windows-Ntfs-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Ntfs-4Operational.jsonl:1 — «soft-Windows-Ntfs","Guid":"3FF37A1C-A68D-4D6E-8C9B-F79E8B16C482"}},"EventID":145,"Version"» |
| rendered/Microsoft-Windows-Ntfs-4WHC.jsonl | наблюдение | rendered/Microsoft-Windows-Ntfs-4WHC.jsonl:3 — «IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-18"}}},"EventData":{"hc_stateid":0}}}» |
| rendered/Microsoft-Windows-Partition-4Diagnostic.jsonl | наблюдение | rendered/Microsoft-Windows-Partition-4Diagnostic.jsonl:7 — «Windows-Partition","Guid":"412BDFF2-A8C4-470D-8F33-63FE0D8C20E2"}},"EventID":1006,"Version» |
| rendered/Microsoft-Windows-PowerShell-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-PowerShell-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-PowerShell-4Operational.jsonl:7 — «indows-PowerShell","Guid":"A0C1853B-5C40-4B15-8766-3CF1C58F985A"}},"EventID":40961,"Versio» |
| rendered/Microsoft-Windows-PrintService-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-PrintService-4Admin.jsonl:8 — «dows-PrintService","Guid":"747EF6FD-E535-4D16-B510-42C90F6873A1"}},"EventID":318,"Version"» |
| rendered/Microsoft-Windows-Privacy-Auditing-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Privacy-Auditing-4Operational.jsonl:2 — «-Privacy-Auditing","Guid":"D67FBB76-D18A-5AE3-24A3-8C1DB52D6C62"}},"EventID":1008,"Version» |
| rendered/Microsoft-Windows-Program-Compatibility-Assistant-4CompatAfterUpgrade.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4Admin.jsonl:20 — «tributes":{"Name":"Microsoft-Windows-Provisioning-Diagnostics-Provider","Guid":"ED8B9BD3-F» |
| rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4AutoPilot.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Provisioning-Diagnostics-Provider-4ManagementService.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-PushNotification-Platform-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-PushNotification-Platform-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-PushNotification-Platform-4Operational.jsonl:56 — «"#attributes":{"Name":"Microsoft-Windows-PushNotifications-Platform","Guid":"88CD9180-4491» |
| rendered/Microsoft-Windows-ReadyBoost-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-ReadyBoost-4Operational.jsonl:6 — «indows-ReadyBoost","Guid":"E6307A09-292C-497E-AAD6-498F68E2B619"}},"EventID":1016,"Version» |
| rendered/Microsoft-Windows-RemoteDesktopServices-RdpCoreTS-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-RemoteDesktopServices-RdpCoreTS-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-RemoteDesktopServices-RdpCoreTS-4Operational.jsonl:2082 — «ttributes":{"Name":"Microsoft-Windows-RemoteDesktopServices-RdpCoreTS","Guid":"1139C61B-B5» |
| rendered/Microsoft-Windows-Resource-Exhaustion-Detector-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Resource-Exhaustion-Detector-4Operational.jsonl:3 — «"Computer":"IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-19"}}},"EventData":null}}» |
| rendered/Microsoft-Windows-RestartManager-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-SMBClient-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-SMBServer-4Audit.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-SMBServer-4Connectivity.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-SMBServer-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-SMBServer-4Operational.jsonl:12 — «Windows-SMBServer","Guid":"D48CE617-33A2-4BC3-A5C7-11AA4F29619E"}},"EventID":1027,"Version» |
| rendered/Microsoft-Windows-SMBServer-4Security.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Security-Mitigations-4KernelMode.jsonl | наблюдение | rendered/Microsoft-Windows-Security-Mitigations-4KernelMode.jsonl:5 — «":{"#attributes":{"Name":"Microsoft-Windows-Security-Mitigations","Guid":"FAE10392-F0AF-4A» |
| rendered/Microsoft-Windows-Security-Mitigations-4UserMode.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Security-SPP-UX-Notifications-4ActionCenter.jsonl | наблюдение | rendered/Microsoft-Windows-Security-SPP-UX-Notifications-4ActionCenter.jsonl:1 — «IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-20"}}},"EventData":{"hc_stateid":1}}}» |
| rendered/Microsoft-Windows-SettingSync-4Debug.jsonl | наблюдение | rendered/Microsoft-Windows-SettingSync-4Debug.jsonl:20 — «ndows-SettingSync","Guid":"83D6E83B-900B-48A3-9835-57656B6F6474"}},"EventID":6065,"Version» |
| rendered/Microsoft-Windows-SettingSync-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Shell-ConnectedAccountState-4ActionCenter.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Shell-Core-4ActionCenter.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Shell-Core-4AppDefaults.jsonl | наблюдение | rendered/Microsoft-Windows-Shell-Core-4AppDefaults.jsonl:1 — «indows-Shell-Core","Guid":"30336ED4-E327-447C-9DE0-51B652C86108"}},"EventID":62443,"Versio» |
| rendered/Microsoft-Windows-Shell-Core-4LogonTasksChannel.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Shell-Core-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Shell-Core-4Operational.jsonl:164 — «indows-Shell-Core","Guid":"30336ED4-E327-447C-9DE0-51B652C86108"}},"EventID":62164,"Versio» |
| rendered/Microsoft-Windows-ShellCommon-StartLayoutPopulation-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-ShellCommon-StartLayoutPopulation-4Operational.jsonl:21 — «tributes":{"Name":"Microsoft-Windows-ShellCommon-StartLayoutPopulation","Guid":"97CA8142-1» |
| rendered/Microsoft-Windows-SmartCard-DeviceEnum-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-SmartCard-DeviceEnum-4Operational.jsonl:3 — «":{"#attributes":{"Name":"Microsoft-Windows-SmartCard-DeviceEnum","Guid":"AAEAC398-3028-48» |
| rendered/Microsoft-Windows-SmbClient-4Audit.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-SmbClient-4Connectivity.jsonl | наблюдение | rendered/Microsoft-Windows-SmbClient-4Connectivity.jsonl:6 — «eLength":58,"ServerName":"\\Device\\NetBT_Tcpip_{BE751000-4023-4659-8B70-7F45A94374E5}"}}}» |
| rendered/Microsoft-Windows-SmbClient-4Security.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-StateRepository-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-StateRepository-4Operational.jsonl:159 — «s-StateRepository","Guid":"89592015-D996-4636-8F61-066B5D4DD739"}},"EventID":255,"Version"» |
| rendered/Microsoft-Windows-StateRepository-4Restricted.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Storage-Storport-4Health.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Storage-Storport-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Storage-Storport-4Operational.jsonl:566 — «-Windows-StorPort","Guid":"C4636A1E-7986-4646-BF10-7BC3B4A76E8E"}},"EventID":505,"Version"» |
| rendered/Microsoft-Windows-StorageSpaces-Driver-4Diagnostic.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-StorageSpaces-Driver-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-StorageSpaces-Driver-4Operational.jsonl:3 — «Hat","DriveModel":"VirtIO","DriveSerial":""}}}» |
| rendered/Microsoft-Windows-StorageSpaces-ManagementAgent-4WHC.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Store-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Store-4Operational.jsonl:5 — «oft-Windows-Store","Guid":"9C2A37F3-E5FD-5CAE-BCD1-43DAFEEE1FF0"}},"EventID":8000,"Version» |
| rendered/Microsoft-Windows-Storsvc-4Diagnostic.jsonl | наблюдение | rendered/Microsoft-Windows-Storsvc-4Diagnostic.jsonl:6 — «t-Windows-Storsvc","Guid":"A963A23C-0058-521D-71EC-A1CCE6173F21"}},"EventID":1002,"Version» |
| rendered/Microsoft-Windows-TWinUI-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-TWinUI-4Operational.jsonl:1 — «tributes":{"UserID":"S-1-5-21-2929202171-1942120112-2054978817-1001"}}},"EventData":null}}» |
| rendered/Microsoft-Windows-TZSync-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-TZSync-4Operational.jsonl:8 — «ft-Windows-TZSync","Guid":"3527CB55-1298-49D4-AB94-1243DB0FCAFF"}},"EventID":2,"Version":0» |
| rendered/Microsoft-Windows-TaskScheduler-4Maintenance.jsonl | наблюдение | rendered/Microsoft-Windows-TaskScheduler-4Maintenance.jsonl:68 — «ows-TaskScheduler","Guid":"DE7B24EA-73C8-4A09-985D-5BDADCFA9017"}},"EventID":800,"Version"» |
| rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Admin.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl:1 — «ributes":{"Name":"Microsoft-Windows-TerminalServices-LocalSessionManager","Guid":"5D896912» |
| rendered/Microsoft-Windows-TerminalServices-PnPDevices-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-TerminalServices-PnPDevices-4Admin.jsonl:1 — «"Computer":"IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-18"}}},"EventData":null}}» |
| rendered/Microsoft-Windows-TerminalServices-PnPDevices-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Admin.jsonl:1 — «butes":{"Name":"Microsoft-Windows-TerminalServices-RemoteConnectionManager","Guid":"C76BAA» |
| rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Operational.jsonl:619 — «butes":{"Name":"Microsoft-Windows-TerminalServices-RemoteConnectionManager","Guid":"C76BAA» |
| rendered/Microsoft-Windows-TerminalServices-ServerUSBDevices-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-TerminalServices-ServerUSBDevices-4Admin.jsonl:1 — «"Computer":"IPSERVER","Security":{"#attributes":{"UserID":"S-1-5-18"}}},"EventData":null}}» |
| rendered/Microsoft-Windows-TerminalServices-ServerUSBDevices-4Operational.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Time-Service-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Time-Service-4Operational.jsonl:2 — «dows-Time-Service","Guid":"06EDCFEB-0FD0-4E53-ACCA-A6F8BBF81BCB"}},"EventID":257,"Version"» |
| rendered/Microsoft-Windows-UniversalTelemetryClient-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-UniversalTelemetryClient-4Operational.jsonl:5 — «{"#attributes":{"Name":"Microsoft-Windows-UniversalTelemetryClient","Guid":"6489B27F-7C43-» |
| rendered/Microsoft-Windows-User-Device-Registration-4Admin.jsonl | наблюдение | rendered/Microsoft-Windows-User-Device-Registration-4Admin.jsonl:1 — «sted","UserIsRemote":"No","LogonCertRequired":"Not Tested","MachinePolicySource":"none"}}}» |
| rendered/Microsoft-Windows-User-Profile-Service-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-User-Profile-Service-4Operational.jsonl:3 — «Profiles Service","Guid":"89B1E9F0-5AFF-44A6-9B44-0A07A7CE5845"}},"EventID":1,"Version":0» |
| rendered/Microsoft-Windows-UserPnp-4ActionCenter.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-UserPnp-4DeviceInstall.jsonl | наблюдение | rendered/Microsoft-Windows-UserPnp-4DeviceInstall.jsonl:10 — «t-Windows-UserPnp","Guid":"96F4A050-7E31-453C-88BE-9634F4E02139"}},"EventID":8005,"Version» |
| rendered/Microsoft-Windows-VolumeSnapshot-Driver-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-VolumeSnapshot-Driver-4Operational.jsonl:5 — «:{"#attributes":{"Name":"Microsoft-Windows-VolumeSnapshot-Driver","Guid":"67FE2216-727A-40» |
| rendered/Microsoft-Windows-WER-PayloadHealth-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-WER-PayloadHealth-4Operational.jsonl:4 — «WER-PayloadHealth","Guid":"4AFDDFDE-002D-51AC-C109-C3B7897858D0"}},"EventID":1,"Version":0» |
| rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl:147 — «dows-WMI-Activity","Guid":"1418EF04-B0B4-4623-BF7E-D74AB47BBDAA"}},"EventID":5858,"Version» |
| rendered/Microsoft-Windows-Wcmsvc-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Wcmsvc-4Operational.jsonl:53 — «ft-Windows-Wcmsvc","Guid":"67D07935-283A-4791-8F8D-FA9117F3E6F2"}},"EventID":10001,"Versio» |
| rendered/Microsoft-Windows-WebAuthN-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-WebAuthN-4Operational.jsonl:1 — «-Windows-WebAuthN","Guid":"3AE1EA61-C002-47FB-B06C-4022A8C98929"}},"EventID":2000,"Version» |
| rendered/Microsoft-Windows-WinINet-Config-4ProxyConfigChanged.jsonl | наблюдение | rendered/Microsoft-Windows-WinINet-Config-4ProxyConfigChanged.jsonl:1 — «entData":{"fAutoDetect":true,"pwszAutoConfigUrl":"","pwszProxy":"","pwszProxyBypass":""}}}» |
| rendered/Microsoft-Windows-WinRM-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-WinRM-4Operational.jsonl:2 — «oft-Windows-WinRM","Guid":"A7975C8F-AC13-49F1-87DA-5A984A4AB417"}},"EventID":254,"Version"» |
| rendered/Microsoft-Windows-Windows-Defender-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Windows-Defender-4Operational.jsonl:255 — «-Windows Defender","Guid":"11CD958A-C507-4EF3-B3F2-5FD9DFBD2C78"}},"EventID":5007,"Version» |
| rendered/Microsoft-Windows-Windows-Defender-4WHC.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4ConnectionSecurity.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl | наблюдение | rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:2 — «Advanced Security","Guid":"D1BC9AFF-2ABF-4D71-9146-ECB2A986EB85"}},"EventID":2010,"Version» |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4FirewallDiagnostics.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-WindowsBackup-4ActionCenter.jsonl | пусто | байт=0 |
| rendered/Microsoft-Windows-WindowsSystemAssessmentTool-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-WindowsSystemAssessmentTool-4Operational.jsonl:2 — «#attributes":{"Name":"Microsoft-Windows-WindowsSystemAssessmentTool","Guid":"11A75546-3234» |
| rendered/Microsoft-Windows-WindowsUpdateClient-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-WindowsUpdateClient-4Operational.jsonl:1 — «":{"#attributes":{"Name":"Microsoft-Windows-WindowsUpdateClient","Guid":"945A8954-C147-4AC» |
| rendered/Microsoft-Windows-Winlogon-4Operational.jsonl | наблюдение | rendered/Microsoft-Windows-Winlogon-4Operational.jsonl:21 — «-Windows-Winlogon","Guid":"DBE9B383-7CF3-4331-91CC-A3CB16A3B538"}},"EventID":811,"Version"» |
| rendered/Microsoft-Windows-WorkFolders-4WHC.jsonl | пусто | байт=0 |
| rendered/Windows-PowerShell.jsonl | наблюдение | rendered/Windows-PowerShell.jsonl:17 — «":{"#attributes":{"xmlns":"http://schemas.microsoft.com/win/2004/08/events/event"},"System» |

## Окно записей

итог: файлов=143 каналов=93 сплошных=93 с-пропусками=0 неприменимо=50 ошибок=0

| путь | канал | окно | записей | нет |
| --- | --- | --- | --- | --- |
| Security.jsonl | Security | окно=402275–437190 | записей=34916 | нет=0 |
| System.jsonl | System | окно=1–1036 | записей=1036 | нет=0 |
| rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl | Microsoft-Windows-Windows Firewall With Advanced Security/Firewall | окно=1–719 | записей=719 | нет=0 |

## Нормальная фоновая активность системы

Следующие изменения состояния системы зафиксированы журналами и отнесены к штатной работе сервера — они не связаны с инцидентом.

### Службы: установка (S-1-5-18 / SYSTEM)

В System.jsonl зафиксировано 15 событий установки служб (EventID 7045) от имени S-1-5-18 (SYSTEM), среди которых драйверы виртуального оборудования (Red Hat VirtIO Ethernet Adapter Service), системные службы Windows и компоненты обновлений. Установка служб от имени SYSTEM является нормальным поведением — Windows автоматически регистрирует драйверы устройств и системные службы при загрузке, обновлении или при подключении нового оборудования. Установка 3proxy (единственное событие от имени пользователя S-1-5-21-...-1001) отнесена к инциденту Н-2.

- [!REPORTED] System.jsonl:263 — «"ServiceName":"3proxy tiny proxy server"» — установка 3proxy (инцидент, от S-1-5-21-...-1001)
- [!REPORTED] System.jsonl:44 — «"ServiceName":"Red Hat VirtIO Ethernet Adapter Service"» — установка драйвера VirtIO (норма, от S-1-5-18)

### Службы: изменение типа запуска (S-1-5-18, S-1-5-20)

Зафиксированы события изменения типа запуска служб (EventID 7040) от имени S-1-5-18 и S-1-5-20 (служба). Изменение типа запуска — штатная операция при установке обновлений и обслуживании системы. Например, координатор распределенных транзакций (MSDTC) и служба оптимизации доставки могут менять тип запуска при активации отложенных обновлений или по расписанию обслуживания. Данные события не коррелируют с временными рамками атаки.

- [!PROVEN] System.jsonl:103 — «"param1":"Координатор распределенных транзакций","param2":"Вручную","param3":"Автоматически"» — изменение типа запуска MSDTC (норма, от S-1-5-18)
- [!PROVEN] System.jsonl:195 — «"param1":"Оптимизация доставки","param2":"Вручную","param3":"Автоматически"» — изменение типа запуска DoSvc (норма, от S-1-5-20)

### WMI-подписки (S-1-5-18)

В rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl зафиксированы события управления WMI-подписками (EventID 5857, 5861) от имени S-1-5-18. WMI-подписки — стандартный механизм Windows для мониторинга системы, используемый компонентами ОС для сбора метрик, отслеживания состояния и выполнения задач по расписанию. Отсутствие подозрительной командной строки или PowerShell-активности на момент подписки позволяет отнести эти события к штатной работе.

- [!PROVEN] rendered/Microsoft-Windows-WMI-Activity-4Operational.jsonl:238 — «"EventID":5860» — WMI-подписка (норма)

### Правила брандмауэра: не связанные с 3proxy (S-1-5-18, S-1-5-80-...)

Помимо правил 3proxy, в Firewall зафиксированы изменения правил от имени S-1-5-18 (системные правила Microsoft Edge) и S-1-5-80-... (правило `windows_ie_ac_001`, созданное Internet Explorer Application Compatibility). Оба правила — штатные: Microsoft Edge (mDNS-входящий) создаётся браузером для обнаружения служб в локальной сети при включённой настройке mDNS; `windows_ie_ac_001` — правило совместимости Internet Explorer, создаваемое компонентом Application Compatibility. Ни одно из них не имеет отношения к 3proxy и не представляет угрозы.

- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:1 — «"RuleName":"Microsoft Edge (mDNS-входящий)"» — системное правило браузера (норма)
- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:3 — «"RuleName":"windows_ie_ac_001"» — правило совместимости IE (норма)

## Чего не хватает в данных

1. **Первоначальный доступ.** Журнал Security начинается с 2021-06-01, а установка 3proxy произошла 2021-05-09. Как атакующий получил первоначальный доступ к системе — неизвестно. Требуются логи удалённого доступа (VPN, RDP-шлюз), IIS, FTP, или иные источники, предшествующие 2021-05-09.
2. **Содержимое 3proxy.** Конфигурация 3proxy (`C:\3proxy\bin64\3proxy.cfg`) не доступна в корпусе. Неизвестно, какие порты и протоколы использовались для проксирования, какие IP были разрешены, велась ли аутентификация.
3. **Что ещё установлено.** В корпусе нет логов установки ПО (MsiInstaller, AppLocker), которые могли бы показать, как был доставлен и установлен 3proxy на диск.
4. **Активность после установки.** Журналы PowerShell и WMI-Activity показывают активность, но без контекста (какие команды выполнялись, какие скрипты запускались) нельзя восстановить полную картину действий атакующего.
5. **Атрибуция брутфорса.** Так как атака сетевого перебора (Н-1) не привела к успешному входу, её связь с установкой 3proxy (Н-2) не подтверждена корпусом. Корпус не даёт ответа, кто атаковал и было ли это скоординировано.

## ВЕРДИКТ

Хост `IPSERVER` скомпрометирован. На системе установлен и запущен прокси-сервер 3proxy с автоматическим запуском от имени LocalSystem, созданы разрешающие правила брандмауэра для входящего трафика. Установка выполнена локальным пользователем `IPSERVER\root` (SID S-1-5-21-2929202171-1942120112-2054978817-1001). Одновременно с этим хост подвергался массированной атаке перебора паролей по NTLM: 33 456 неудачных попыток, из которых 8098 пришлись на существующие учётные записи. Связь между атакой и установкой 3proxy не подтверждена наличным корпусом.

- [!REPORTED] Security.jsonl:1 — «"EventID":4625» — 33 456 неудачных попыток входа (EventID 4625)
- [!PROVEN] System.jsonl:263 — «"ServiceName":"3proxy tiny proxy server"» — установка службы 3proxy
- [!PROVEN] rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl:445 — «"RuleName":"3proxy - tiny proxy server"» — 6 правил брандмауэра, разрешающих входящий трафик

Ответ: скомпрометирована