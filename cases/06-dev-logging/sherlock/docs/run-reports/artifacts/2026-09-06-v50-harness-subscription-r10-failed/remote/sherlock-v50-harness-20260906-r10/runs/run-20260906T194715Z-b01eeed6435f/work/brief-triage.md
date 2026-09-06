# Задача: разбор рабочего списка (фаза TRIAGE)

Ты работаешь в изолированном контексте. Всё, что ты прочитаешь, останется у тебя;
наружу уйдёт только твой финальный ответ. Поэтому читай столько, сколько нужно,
но ОТВЕТЬ КОРОТКО — не более 20 строк.

## Входные данные (абсолютные пути, не угадывай)

* корпус логов: `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/corpus`
* рабочий список: `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/worklist.tsv`
* правила массовых закрытий: `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/rules.tsv`
* карта корпуса: `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/map.txt`
* КУРСОР — единственный законный доступ к рабочему списку:

      python3 /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/skill-catalogue/log-rca/tools/worklist.py next --work /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work --batch 20
      python3 /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/skill-catalogue/log-rca/tools/worklist.py verdict --work /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work --from-stdin

  `next` выдаёт партию нерешённых строк БЕЗ колонки `запись`, которую не читает
  ни один гейт: полный проход по 250 строкам — 39 427 байт в 13 партиях против
  25 060 байт за ОДНО незаконченное чтение файла. `verdict --from-stdin` пишет
  ответы обратно (`id<TAB>ячейка` на строку) и отказывается forge-нуть колонку.
  Оглавление срезов `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/worklist-index.tsv` и срезы `view-<ось>-NN.tsv` — только
  чтобы выбрать ось для `--axis`. Сам `worklist.tsv` — леджер:
  **НЕ читай его целиком и НЕ правь его руками — обе ошибки уже стоили
  платного прогона.**
* инструменты навыка (ЗАПУСКАТЬ, НЕ ЧИТАТЬ): `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/skill-catalogue/log-rca/tools`

**НЕ ЧИТАЙ исходники инструментов и НЕ ЧИТАЙ SKILL.md.** Этот бриф — полный
контракт: всё, что нужно, ниже. На прогоне v36 чтение `citecheck.py` (25 раз),
`SKILL.md` и `reference/*.md` стоило ≈5,07 млн токенов — 27 % прогона — и не
дало ничего, чего нет здесь. Нужен формат вывода — он в разделе «Что вернуть».
Нужно поведение проверки — запусти её и прочитай её же вывод.

**ТВОЙ РЕЗУЛЬТАТ — ФАЙЛ, А НЕ ОТВЕТ.** Закрывай строки через
`worklist.py verdict` по мере работы, а не одним куском в конце. Если ходы кончаются — запиши то, что уже есть, и верни
короткий отчёт. Родитель смотрит на диск, а не на твой статус: на прогоне v36
ребёнок упал на лимите ходов, вернул пустую строку, и его работа была не видна.

## Что сделать

1. Бери партию: `worklist.py next --work /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work --batch 20` (или
   `--axis <ось>`). Каждая строка приходит со статусом `?`. Закрывай её на `D`
   (дефект), `N` (норма) или `X` (данных не хватает) и отдавай ответы обратно
   через `worklist.py verdict --work /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work --from-stdin`. Повторяй, пока `next`
   не вернёт пустую партию. Своих парсеров TSV не пиши — их уже написали.
2. Ни один вердикт не ставится одной буквой: он обязан принести ссылку
   `путь:строка` с дословной цитатой, либо номер правила `#R<n>` из `/home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/rules.tsv`.
   Правило обязано иметь утверждение и квитанции — иначе строка остаётся `?`.
3. Оси `new`, `peak`, `odd`, `minor`, `late` — сильные: такую строку нельзя
   закрыть массовым правилом, только поимённо.
4. Проверь себя и добейся нулевого кода возврата:

       python3 /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/skill-catalogue/log-rca/tools/triagecheck.py --worklist /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/worklist.tsv --rules /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/work/rules.tsv --corpus /home/claude-developer/hack/sherlock-v50-harness-20260906-r10/runs/run-20260906T194715Z-b01eeed6435f/workspace/corpus

## Что вернуть

Ровно эти строки, без пересказа работы:

    РАЗОБРАНО: <сколько строк> из <всего>
    ДЕФЕКТОВ: <сколько D>
    ПРАВИЛ: <сколько строк в rules.tsv>
    TRIAGECHECK: <код возврата и итоговая строка>
    ГЛАВНОЕ: <до 5 строк — что именно найдено, с адресами файл:строка>
