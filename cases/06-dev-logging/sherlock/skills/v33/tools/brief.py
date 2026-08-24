#!/usr/bin/env python3
"""brief — write the per-phase instruction files a subagent reads from disk.

    python3 brief.py --work ./work --corpus ./logs --skill-root <BASE> --phase all

Why this exists. Measured 2026-08-24 with a logging proxy in front of the
provider, on qwen-code 0.21.1: a trivial request is 83,705 bytes; the same
request with this skill loaded is 152,245 bytes. The skill body is ~68 KB of
EVERY request for the rest of the session, and a session that runs 55 turns
resends it 55 times. That is how a run reaches a 1.1 MB body, at which point the
provider stops answering: measured twice, an HTTP 200 carrying a single SSE
event and no finish reason (Qwen renders it «Model stream ended without a finish
reason»), first at 828,403 bytes, then at 1,143,191 bytes.

The fix is not to shorten the instructions — they are the product. It is to stop
carrying them in the SAME conversation that accumulates the evidence. A named
subagent has its own history; measured on the same box, a parent that launched
one child grew by 1,570 bytes for the child's ENTIRE run, while the child's own
requests ran at 104-109 KB and never touched the parent.

So each phase runs in its own subagent, and each subagent gets a BRIEF FILE
instead of a skill body: a short prompt that names one file to read. That is the
shape Qwen Code's own `review` skill uses for its 14 workers, and the reason is
the same — a prompt is retried and resent, a file is read once.

The briefs are ASSEMBLED HERE, deterministically, rather than written by the
model, for the same reason `logmap.py` builds the worklist: an instruction the
model paraphrases is an instruction that can drift. Every brief carries the
absolute paths of its inputs and outputs, so a worker never has to guess where
the corpus is.

No LLM, no network, stdlib only. Exit 0 when every requested brief is written,
2 on usage error.
"""
import argparse
import os
import sys

VERSION = 33

PHASES = ("triage", "draft")

TRIAGE = """# Задача: разбор рабочего списка (фаза TRIAGE)

Ты работаешь в изолированном контексте. Всё, что ты прочитаешь, останется у тебя;
наружу уйдёт только твой финальный ответ. Поэтому читай столько, сколько нужно,
но ОТВЕТЬ КОРОТКО — не более 20 строк.

## Входные данные (абсолютные пути, не угадывай)

* корпус логов: `{corpus}`
* рабочий список: `{worklist}`
* правила массовых закрытий: `{rules}`
* карта корпуса: `{map}`
* инструменты навыка: `{tools}`
* полная инструкция навыка: `{skill_md}` — прочитай раздел «Шаг 2. Разбор
  рабочего списка» целиком, прежде чем ставить первый вердикт.

## Что сделать

1. Каждая строка `{worklist}` начинается со статуса `?`. Замени его на `D`
   (дефект), `N` (норма) или `X` (данных не хватает) И ЗАПИШИ ФАЙЛ ОБРАТНО.
2. Ни один вердикт не ставится одной буквой: он обязан принести ссылку
   `путь:строка` с дословной цитатой, либо номер правила `#R<n>` из `{rules}`.
   Правило обязано иметь утверждение и квитанции — иначе строка остаётся `?`.
3. Оси `new`, `peak`, `odd`, `minor`, `late` — сильные: такую строку нельзя
   закрыть массовым правилом, только поимённо.
4. Проверь себя и добейся нулевого кода возврата:

       python3 {tools}/triagecheck.py --worklist {worklist} --rules {rules} --corpus {corpus}

## Что вернуть

Ровно эти строки, без пересказа работы:

    РАЗОБРАНО: <сколько строк> из <всего>
    ДЕФЕКТОВ: <сколько D>
    ПРАВИЛ: <сколько строк в rules.tsv>
    TRIAGECHECK: <код возврата и итоговая строка>
    ГЛАВНОЕ: <до 5 строк — что именно найдено, с адресами файл:строка>
"""

DRAFT = """# Задача: написать отчёт (фаза DRAFT)

Ты работаешь в изолированном контексте и НЕ повторяешь расследование. Всё, что
нужно, уже лежит на диске. Не открывай корпус целиком: читай только те строки,
на которые ссылаешься.

## Входные данные (абсолютные пути, не угадывай)

* корпус логов: `{corpus}`
* разобранный рабочий список: `{worklist}`
* правила и квитанции: `{rules}`
* карта корпуса: `{map}`
* чекпойнт: `{checkpoint}`
* формат отчёта: `{report_format}` — прочитай его ПЕРВЫМ действием.
* полная инструкция навыка: `{skill_md}` — разделы «Что ты производишь» и
  «Проверка и сдача».
* инструменты навыка: `{tools}`

## Что сделать

1. Напиши полный отчёт в `{report}`. Каждая находка — блок `### Н-n · заголовок`
   со строками «улики», «чем опровергал», «атрибуция», «исход». Обязательны
   раздел «Отклонённые кандидаты» и раздел «Покрытие».
2. Строка покрытия с наблюдением обязана цитировать строку, которую отметил
   `logmap` для этого файла, — произвольная строка не считается.
3. Прогони три проверки и добейся нулевого кода у каждой:

       python3 {tools}/citecheck.py {report} --corpus {corpus} --require-quote --ledger {worklist}
       python3 {tools}/triagecheck.py --worklist {worklist} --rules {rules} --corpus {corpus}
       python3 {tools}/statecheck.py --corpus {corpus} --report {report}

   `statecheck` идёт от корпуса к отчёту: КАЖДАЯ её группа должна быть названа в
   отчёте — находкой или явным «норма» с цитатой.
4. Связи по времени не выдумывай, а возьми таблицей:

       python3 {tools}/logjoin.py --window --corpus {corpus}

## Что вернуть

Ровно эти строки, без самого отчёта:

    ОТЧЁТ: {report} (<байт>)
    НАХОДОК: <сколько блоков Н-n>
    CITECHECK: <код возврата>
    TRIAGECHECK: <код возврата>
    STATECHECK: <код возврата и сколько групп не отвечено>
    ВЕРДИКТ: <одна строка>
"""


def render(phase, work, corpus, skill_root):
    def w(name):
        return os.path.join(work, name)
    fields = {
        "corpus": os.path.abspath(corpus),
        "work": os.path.abspath(work),
        "worklist": os.path.abspath(w("worklist.tsv")),
        "rules": os.path.abspath(w("rules.tsv")),
        "map": os.path.abspath(w("map.txt")),
        "checkpoint": os.path.abspath(w("checkpoint.json")),
        "report": os.path.abspath(w("report.md")),
        "tools": os.path.abspath(os.path.join(skill_root, "tools")),
        "skill_md": os.path.abspath(os.path.join(skill_root, "SKILL.md")),
        "report_format": os.path.abspath(
            os.path.join(skill_root, "reference", "report-format.md")),
    }
    body = {"triage": TRIAGE, "draft": DRAFT}[phase]
    return body.format(**fields)


def main():
    ap = argparse.ArgumentParser(
        description="написать файлы-задания для фазовых субагентов")
    ap.add_argument("--work", required=True, help="каталог состояния (./work)")
    ap.add_argument("--corpus", required=True, help="корень корпуса логов")
    ap.add_argument("--skill-root", required=True,
                    help="базовый каталог навыка (его выдают первой строкой)")
    ap.add_argument("--phase", default="all", choices=("all",) + PHASES)
    a = ap.parse_args()

    if not os.path.isdir(a.corpus):
        sys.stderr.write("brief: нет корпуса: %s\n" % a.corpus)
        return 2
    if not os.path.isdir(a.skill_root):
        sys.stderr.write("brief: нет каталога навыка: %s\n" % a.skill_root)
        return 2
    os.makedirs(a.work, exist_ok=True)

    phases = PHASES if a.phase == "all" else (a.phase,)
    for phase in phases:
        path = os.path.join(a.work, "brief-%s.md" % phase)
        text = render(phase, a.work, a.corpus, a.skill_root)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stdout.write("%s (%d байт)\n" % (os.path.abspath(path), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
