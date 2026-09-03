# Deterministic contract probe report

## F-AUTH-EXTERNAL

- [!PROVEN] external authentication endpoints are -, 198.51.100.9 and 203.0.113.7: «"Message":"external authentication from 203.0.113.7"» — Security.jsonl:1.
- [!PROVEN] external authentication from 198.51.100.9 was recorded in source — Security.jsonl:3.
- unrelated assertion «"Message":"not in corpus"» — Security.jsonl:1.
- [!PROVEN] deliberately out of range «"Message":"external authentication from 203.0.113.7"» — Security.jsonl:99.

## F-INVENTORY-SERVICE

- [!PROVEN] RemoteAdmin service is present: «"Message":"RemoteAdmin service installed"» — System.jsonl:1.
- [!PROVEN] C:\Tools\remote-admin.exe is present: «"Message":"RemoteAdmin process inventory confirmed"» — System.jsonl:2.

## F-REPORTED-CONTEXT

- [!REPORTED] the source records «"Message":"reported by endpoint team"» — Security.jsonl:2.

## F-TIMELINE-LINK

- [!INFERENCE] authentication preceded the service: «"Message":"RemoteAdmin service installed"» — System.jsonl:1.

## Отклоненные кандидаты

К-1 · routine authentication does not establish attribution
- [!REPORTED] «"Message":"external authentication from 198.51.100.9"» — Security.jsonl:3.
исход: попытка

## Инвентарь наблюдаемых величин

- 203.0.113.7 — address — «"Message":"external authentication from 203.0.113.7"» — Security.jsonl:1
- RemoteAdmin — service — «"Message":"RemoteAdmin service installed"» — System.jsonl:1

## Чего не хватает в логах

- Нет сетевых журналов периметра для независимой атрибуции источника.

## Покрытие

| путь | статус | улики |
| --- | --- | --- |
| Security.jsonl | наблюдение | Security.jsonl:3 — «.9","TargetUserName":"svc-remote","Message":"external authentication from 198.51.100.9"}}}» |
| System.jsonl | наблюдение | System.jsonl:1 — «te-admin.exe","SubjectUserSid":"S-1-5-21-900","Message":"RemoteAdmin service installed"}}}» |

## Окно записей

итог: файлов=2 каналов=0 сплошных=0 с-пропусками=0 неприменимо=2 ошибок=0

## ВЕРДИКТ

атаковали, но не доказано — «"Message":"external authentication from 203.0.113.7"» — Security.jsonl:1; «"Message":"RemoteAdmin service installed"» — System.jsonl:1.
