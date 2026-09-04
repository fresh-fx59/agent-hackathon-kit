# Deterministic contract probe report

## F-AUTH-EXTERNAL

- [!PROVEN] external_ips=["198.51.100.9","203.0.113.7"]
citation_files=["Security.jsonl"]
«"Message":"external authentication from 203.0.113.7"» — Security.jsonl:1; «"Message":"external authentication from 198.51.100.9"» — Security.jsonl:3.

## F-INVENTORY-SERVICE

- [!PROVEN] services=["RemoteAdmin"]
processes=["C:\\Tools\\remote-admin.exe"]
citation_files=["System.jsonl"]
«"Message":"RemoteAdmin service installed"» — System.jsonl:1; «"Message":"RemoteAdmin process inventory confirmed"» — System.jsonl:2.

## F-REPORTED-CONTEXT

- [!REPORTED] reported_context="svc-remote"
citation_files=["Security.jsonl"]
«"TargetUserName":"svc-remote"» — Security.jsonl:2.

## F-TIMELINE-LINK

- [!INFERENCE] timeline={"authentication_first":"2026-08-30T10:00:00Z","inventory_first":"2026-08-30T10:03:00Z","relation":"authentication_before_inventory"}
citation_files=["Security.jsonl","System.jsonl"]
«"Message":"external authentication from 203.0.113.7"» — Security.jsonl:1; «"Message":"RemoteAdmin service installed"» — System.jsonl:1.

## Отклоненные кандидаты

К-1 · routine authentication does not establish attribution
- [!REPORTED] «"Message":"external authentication from 198.51.100.9"» — Security.jsonl:3.
исход: попытка

## Инвентарь наблюдаемых величин

- 203.0.113.7 — address — «"Message":"external authentication from 203.0.113.7"» — Security.jsonl:1
- RemoteAdmin — service — «"Message":"RemoteAdmin service installed"» — System.jsonl:1
- C:\Tools\remote-admin.exe — process path — «"Message":"RemoteAdmin process inventory confirmed"» — System.jsonl:2

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
