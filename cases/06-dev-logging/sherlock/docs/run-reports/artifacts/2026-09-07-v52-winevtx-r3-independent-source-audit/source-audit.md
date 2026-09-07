# Независимый source audit Winevtx r3

**Вердикт исходному отчёту: FAIL — существенные ошибки интерпретации и ложные отрицания.** Это не отказ только из-за путей ссылок. Основные июньские агрегаты воспроизводятся, но они не компенсируют неверные выводы о Credential Manager, майской идентичности/установке службы, хронологии и универсальной корреляции RDP.

**Исправленная редакторская копия:** [report-reviewed.md](report-reviewed.md). Она отделена от неизменённого вывода целевой модели. Оценка автономного r3 остаётся diagnostic FAIL; редакторская коррекция не доказывает, что целевая модель способна воспроизвести правильный отчёт.

## Объект, метод, происхождение

- Исходный `report.md`: [снимок r3](../2026-09-07-v52-winevtx-r3-live-report-snapshot/report.md), SHA256 `49828debdfcebd9579a454c58c60fb43caa948de1b2054e99fcb3b6248024dc5`.
- Источник: `contabo-prod:/home/claude-developer/hack/sherlock-winevtx-corpus-v30-normalized-20260821/`. Локальная read-only полученная rsync-копия: `/tmp/winevtx-r3-independent-source/`.
- Прочитаны все **143 JSONL**, **93 196 459 байт**, **90 267 событий**. Парсер различает числовой/объектный EventID, EventData и UserData; счёт не зависит от выводов `work/` целевой модели.
- [audit.py](audit.py), [source-manifest.json](source-manifest.json), [aggregate.json](aggregate.json), [rdp.json](rdp.json), [rdp-ip-correlation.json](rdp-ip-correlation.json), [reviewed-citation-evidence.json](reviewed-citation-evidence.json) содержат воспроизводимые числа и точные исходные записи.
- Отрицательные проверки выполнены по всему набору источников с учётом provider/channel. Никакие исторические answer keys и результаты r2 не использовались. Содержимое корпуса и live-run inputs не менялось.
- Счёт файлов/событий подтверждает охват агрегации, но не всеведение о семантике каждого из 90 267 событий. Неизвестные причинные связи в исправленной копии оставлены неизвестными.

## Матрица существенных утверждений

Обозначения путей только в этой таблице: **LSM** = `rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl`; **FW** = `rendered/Microsoft-Windows-Windows-Firewall-With-Advanced-Security-4Firewall.jsonl`; **RCM-O** = `rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Operational.jsonl`; **RCM-A** = `rendered/Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Admin.jsonl`; **Core** = `rendered/Microsoft-Windows-RemoteDesktopServices-RdpCoreTS-4Operational.jsonl`. Все полные пути сохранены в JSON.

| Утверждение исходного отчёта | Независимый исходник/вычисление | Вердикт и обязательное исправление |
|---|---|---|
| Состав корпуса и размеры ключевых файлов | 143 файла, rendered138; Security34916/System1036/Application2942/Setup13/Hardware0; LSM105/RCM-A21/RCM-O1964/Core2082/Defender1106/Winlogon354/FW719 | PASS. Сводные количества сохранены. |
| Provider payload под Event.EventData.UserData.EventXML | LSM:40 и RCM-O:1 имеют Event.UserData.EventXML; FW/Core используют Event.EventData | FAIL. Исправить пути и не навязывать одну форму всем провайдерам. |
| EventID4625 в Application — просто текст | Application:11: EventID.#text=4625, Provider EventSystem, SuppressDuplicateDuration | FAIL точного описания; верно, что это не Security authentication. Учитывать provider, а не объявлять EID текстом. |
| IPSERVER, build19042 | System:1, Security:13497 и другие Computer | PASS для полей; серверная роль/доменная конфигурация этим не установлены. |
| WORKGROUP\ИМЯ находится в Target полях 4625 | Security:20089 SubjectDomainName=WORKGROUP; исходная формулировка о Target не подтверждается | FAIL примера. Использовать реальную ссылку и ограничить вывод о членстве. |
| root SID1001 — только предположение; группы в том же локальном SID-префиксе | Security:13511 напрямую содержит TargetUserName=root и TargetSid...1001; Security:10146/10147 встроенные S-1-5-32-544/551 | FAIL. Имя/SID root — прямой факт; группы имеют другой SID-префикс. |
| Security окно/ERID и циклический порядок | Security:19934 = earliest402275, 2021-06-01T00:09:15.930903Z; :19933 = latest437190, 2021-06-02T21:11:10.516024Z | PASS чисел. Циклический экспорт — интерпретация порядка; исходный EVTX отдельно не исследовался. |
| Физическая последняя строка34916 — конец корпуса; атака в середине попытки | Хронологический конец Security:19933; Core:1432 продолжается до21:11:34.029721Z | FAIL. Конец конкретного канала, последнее событие отказа; завершение атаки/продолжение за пределами данных неизвестны. |
| 33456 отказов;17798/15658 по дням;1976имён;93publicIP | aggregate.json, все34916Securityстрок; IpAddress='-' ровно1 | PASS. 93IP относятся к33455записям с адресами. |
| Все33456 — NTLM/NtLmSsp/svchost | 33455NTLM/NtLmSsp/ProcessName='-'; Security:13497 — Advapi/MICROSOFT_AUTHENTICATION_PACKAGE_V1_0/svchost/noIP | FAIL. Явно указать исключение и не использовать его как типовойNTLMпример. |
| 25355unknown user;8027АДМИНИСТРАТОР;66root;8ГОСТЬ | SubStatus0064=25355,006a=8098,0072=3; root65ROOT+1root; ГОСТЬ5wrongpassword+3disabled | PASS агрегатов. Это SubStatus; Status в основном006d, в3случаях006e. Состояние ГОСТЬ ограничить временами соответствующих событий. |
| Имена клиентов доказывают разные реальные программы | WorkstationName FreeRDP8/Rdesktop5/mstsc3/Remmina3 | PARTIAL. Поля подтверждены, реальная программа/автоматизация/оператор не доказаны. |
| Верхние8IP и количества | 5267/4207/3550/2087/1565/1261/1203/1076, независимо воспроизведены | PASS. |
| RCM-O1964, окно19:16:46.244193–21:11:15.742268; Core2082, окно21:03:11.737436–21:11:34.029721 | rdp.json, RCM-O:1837/1836; Core:1433/1432 | PASS. ВсеRCM-O — EID261/RDP-Tcp. |
| 1964RCM и1957Security ≈1:1 доказывают каждыйсеанс→одинотказ | Точные количества подтверждены, разница7; RCM261 не даёт IP. ПоCore141EID131:118 ближайших sameIPfail≤2s,137≤10s | FAIL универсальности. Оставить корреляцию с её пределами. Примеры Core3/18/40 ↔ Security19840/19841/19842. |
| 60подстрок3389 — все Security timestamps | 60 строк в13 файлах, Security26. Есть Storport206 MaxReadWriteLatency_100ns=33893 и другие числовые/ID-подстроки | FAIL описания поиска; вывод «порт не установлен» PASS. |
| TermService только косвенно;1056 значит service stop/start | System:192 Provider.EventSourceName=TermService; stop/start не содержится в payload | FAIL. Имя источника явно, значение/state службы не установлен. |
| 182SYSTEM4624type5 +3UMFD/DWMtype2; нетJuneLSM21/22/Winlogon1/2 | Полная Securityагрегация; LSM21 только40,LSM22только41; Winlogonпоследние185/18611мая | PASS отрицательного поиска. FAIL расширения до «успеха/компрометации не было». |
| Июньский эпизод — введённый на экране пароль и NLAoff | Security13497 localfail14:53:57.015856 раньшеUMFD14:53:59; RCM-A21IP128.14.134.134 через287мкс; LogonUI.exe вSecurity13511 | PARTIAL. Возможность интерфейса входа поддержана; ручнойввод/отключённыйNLA/причинноеотнесениеlocalfail этомуIP не доказаны. |
| UMFDправила в14:54:07 | FW716–719 фактически14:53:59.463043–14:53:59.471379; дваEID2006delete, дваEID2004block | FAIL времени/неточности типа. Исправлено. |
| root в майских LSM не видно;37/40обаEID21;25disconnect;23logon | LSM40/41 явноеUserIPSERVER\root;37EID40;47EID25reconnect;92EID23logoff | FAIL. Восстановить наблюдаемую последовательность одногоSessionID2, включая сменуIP176.59.42.91 в59/61. |
| 3proxy через40мин послеdisconnect22:11:55 | LSM47reconnect22:11:55.638239; System263install22:25:01.786082; LSM50disconnect23:34:00.597347 | FAIL. 13мин06.147843с послеreconnect, во временном интервале до следующегоdisconnect. |
| Установщик полностью не идентифицируется | System263 Security.UserID...1001, ImagePath3proxy.exe+cfg+--service, Auto, LocalSystem; Security13511 связываетSIDсroot | FAIL категорического отрицания. Учётныйконтекст указан; человек/installerprocess не установлены; configuredImagePath не process execution tree. |
| FW443–450 всеизмененыroot/dllhost;block→allow | 443/444созданиеblockслужебнымSID/svchost;445–450изменениеallowrootSID/dllhost, совпадающиеRuleId | PARTIAL. Block→allow PASS, исполнитель первыхдвухдругой. Настройка и майскийrootконтекст поддержаны; malicious intent и использование не доказаны. |
| Кроме3proxy/UMFD нетдругихincomingallowports | FW1/21/23mDNS5353 Direction1 Action3; всего74событияDirection1Action3 (не74уникальныхправила) | FAIL. Удалить широкое отрицание; существование другого правила само по себе не IOC. |
| Все1075Credentialreadsfailed/count0/SYSTEM | 966ReturnCode3221226021/count0;109ReturnCode0/count1;1068SID18+7SID20. SuccessfirstSecurity20089,last19298; targetvirtualapp/didlogical | **FAIL существенный.** Успешные чтения есть; кража/содержимое/передача атакующему не доказаны. Системность не доказывает безвредность. |
| JuneSystem40; no7045sinceMay31 | ПолныйSystemscan; последний7045System994May31MpKsl | PASS. Удалить путаницу7040/7045при BITS. |
| Defender1106,no1116/1117,June149–247 | Независимыйprovider-specificscan:99June; точныеграницы00:54:01Jun1–20:53:57Jun2 | PASS. Два1117другогопровайдераPushNotifications не Defender. Отсутствие обнаружения не доказывает отсутствиеexecution. |
| PowerShellJune6,путьPowerShell-4Operational,нетданныхокомандах | РеальныйMicrosoft-Windows-PowerShell-4Operational:6June; Windows-PowerShell:16June.191/199HostApplicationсWrite-Host;25всехEID400имеютэтотHostApplication | FAIL пути/полноты/широкогоотрицания. Нет4103/4104 — PASS, но некоторыеcmdlineимеются. |
| 4616рутинные±2s15:46 | 6Securityevents,LOCAL SERVICE/svchost; дваоколо−1.87s,4миллисекундных | PASS полей, routine/w32timeкак causal attribution — INFERENCE. |
| WMI/TaskScheduler/Shell-Core/Setup ничего примечательногоPROVEN | Каталог/полныйIDscanесть; blanketbenignverdictневытекаетизэтого | LIMIT. Нетпоказаннойсвязисдвумяэпизодами; не маркироватьканалыдоказаннобезвредными. |
| Нет4688/5156/5157/4103/4104/4720/4722/4724/4738/4726;нетSysmon;Hardwareempty | Полныйscan143файлов/90267событий, provideraware IDs и filenames | PASS узкихотрицаний. Отсутствие4688неозначаетотсутствиевсехprocessfields/PowerShellcommandlines. |
| Июньскиепаролинеобязательносчитатьскомпрометированнымипотомууспеханет | Нетуспешногопользовательского4624вокне, но майскийдоступесть; неиспытаныполнотааудита/внешниеданные | FAIL причинногоуспокоения. Не объявлятьcredentialssafe; отдельноразобратьмайскийдоступ/службу. |
| Точныессылкивезде | Исходныйкаталог3неразрешённыесокращённыессылки; ещёбесссылочныйPowerShellпутьневерен | FAIL оформлениедоказательств. В исправленнойкопии42явныхfile:lineссылкиразрешены и исходныеbytesсохранены. |

## Причинность и пределы

- Credential Manager109success — факт чтения записи, не доказательство кражи. Неизвестны содержимое возвращённого объекта, получивший его процесс по имени и дальнейшее использование.
- Майский `root` и его SID — непосредственно наблюдаемые поля. Запись установки7045 имеет тот жеSID; связывать это с неизвестным физическим лицом или июньскими атакующими нельзя.
- Настроенный ImagePath/автозапуск/LocalSystem и разрешающие firewallправила — проверенные свойства службы. Они не доказывают сетевое использование прокси, злой умысел или технически успешно исполненный процесс установки конкретного имени.
- Наблюдаемые отказы и пересекающиеся RDPсобытия поддерживают гипотезу перебора поRDP. Полное индивидуальное отображение каждогоотказа наRDPconnection, password-sprayстратегия, порт и NLAсостояние не установлены.
- Отсутствиепользовательского4624вполномудержанномSecurity — фактпоиска; полнота исходного аудита относительно реального мира отдельно не доказана.

## Общие уроки для следующего изменения skill (без реализации сейчас)

1. Извлекать реальные поля и значения из всех вариантов контейнера, включая `System.Security`, `UserData.EventXML.User`, `CallerProcessName`, `ImagePath`, `CountOfCredentialsReturned` и `ReturnCode`; отсутствие любимого EventID не означает отсутствие информации этого класса.
2. Проверять каждое широкое отрицание полным запросом к источнику и сохранять контрпримеры. Реальные провалы: успешные5379, другиеfirewallallow, известныеPowerShellHostApplication.
3. Проверять семантику event/provider и арифметику времени. Реальныйпровал: reconnectназванdisconnect, logoffлогином,13мин40минутами.
4. Корреляция агрегатов не создаёт индивидуальную причинную связь.1964≠1957; близостьcount/IP/time полезна при явной границе вывода.
5. Разделять факт, локальный учётныйконтекст, физическогооператора, intent и дальнейшийэффект. Не терять источникSID, одновременно не выдавать его за идентификациючеловека.
6. Не превращать «не найдено в удержанныхданных» в «этого не было» и не повышать оценку модели после редакторскойправки.

## Семантические справки

Для действий firewall2=block/3=allow сверена первичная спецификация Microsoft [FW_RULE_ACTION](https://learn.microsoft.com/mt-mt/openspecs/windows_protocols/ms-fasp/702e3c23-c9d8-43db-8380-b4c670dd7f7d) (дата проверки2026-09-07). Для общего поведения RDP использована [документация Microsoft о подключении Terminal Server](https://learn.microsoft.com/en-us/troubleshoot/windows-server/remote/terminal-server-startup-connection-application). Внешние источники не применялись как сведения о данном частном инциденте.

## Timeline

Текущие результаты записывались по мере обнаружения в [timeline.md](timeline.md) и передавались root. Оригинальный modelreport, исходныйcorpus и liveinputs не редактировались. Финальные хеши и exactdiff фиксируются в [provenance.json](provenance.json).
