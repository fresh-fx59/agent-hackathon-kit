# Контракт черновика v69

Заголовки блоков пишутся строго как `Н-n · название` и `К-n · название`.
В каждом блоке находки ровно одна строка `исход:` и одна `атрибуция:`.
Агрегатная улика содержит literal command text; escaped shell commands are rejected.
Строки ownership use `домен\\учётная_запись` and must name a witnessed account.
Only required rollover rows belong in `Окно записей`; excess rows are defects.

v69: черновик собирается только из закрытых строк рабочего списка; отчёт пишется
только в `work/report.md` по одному блоку/разделу через `tools/reportappend.py`;
«Покрытие» и «Окно записей» вставляются командами `reportappend.py coverage|rollover`.
