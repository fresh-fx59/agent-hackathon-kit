#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает таблицы результатов для презентации из реестра прогонов.

    python3 make-table.py             # вписать таблицы в index.html (между маркерами)
    python3 make-table.py --stdout    # просто напечатать HTML, ничего не менять

Источник — ../eval/runs.jsonl (append-only реестр, который пишет eval/run.sh).
Побеждает ПОСЛЕДНЯЯ строка для каждой пары (датасет, арм), поэтому повторный
прогон ячейки заменяет её, а не удваивает. Новые армы (v2, v3, …) и новые
датасеты подхватываются сами — править скрипт для этого не нужно.

Прогоны классифицируются по исходу, потому что «средние по всем строкам» врут,
если часть прогонов вообще не состоялась:

    ok    — есть отчёт и есть ссылки файл:строка;
    слабо — отчёт есть, ссылок на строки нет;
    сбой  — прогон не состоялся: ошибка API, отказ инструмента, потерянный
            ответ. Такие строки видны в детальной таблице, но не входят в
            средние — и скрипт сам пишет об этом сноску под таблицей.

Только stdlib — те же правила, что и у самого навыка.
"""

import argparse
import json
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "..", "eval", "runs.jsonl")
INDEX = os.path.join(HERE, "index.html")

NBSP = " "  # узкий неразрывный пробел — разделитель разрядов

# Явные признаки того, что прогон не состоялся. Проверяются только в начале
# ответа либо как самостоятельные фразы — чтобы не подсчитать за сбой отчёт,
# который лишь ЦИТИРУЕТ такую строку из лога.
FAIL_MARKERS = [
    r"\[API Error",
    r"API Error:",
    r"Context is too large",
    r"tool is denied",
    r"is denied \(non-interactive",
    r"не удалось получить доступ",
    r"недостаточно разрешений",
    r"не хватает разрешений",
    r"Доступ к файлам за пределами",
    r"Доступ к файловой системе за пределами",
    r"упал с ошибкой провайдера",
    r"Я не могу распаковать",
]
FAIL_RE = re.compile("|".join(FAIL_MARKERS), re.IGNORECASE)

# Человеческие названия армов. Незнакомый арм показывается как есть.
ARM_TITLE = {
    "none": "без навыка (baseline)",
    "v1": "Sherlock v1",
    "v2": "Sherlock v2",
    "v3": "Sherlock v3",
}

STATUS_TITLE = {
    "ok": "отчёт со ссылками",
    "weak": "отчёт без ссылок файл:строка",
    "fail": "прогон не состоялся",
}


# ---------------------------------------------------------------- загрузка ---
def load(path):
    if not os.path.exists(path):
        sys.exit("нет реестра прогонов: %s" % path)
    latest = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("dataset") or not r.get("arm"):
            continue
        latest[(r["dataset"], r["arm"])] = r  # поздняя строка выигрывает
    if not latest:
        sys.exit("реестр пуст: %s" % path)
    return latest


def num(v):
    """Числовое поле реестра: ранние строки хранили duration_s строкой."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify(r):
    """ok | weak | fail — по тому, что прогон реально выдал."""
    text = r.get("answer") or ""
    chars = r.get("answer_chars") or len(text)
    refs = r.get("line_refs") or 0
    head = text[:600]
    if chars < 600 or FAIL_RE.search(head):
        return "fail"
    if refs == 0:
        return "weak"
    return "ok"


def arm_order(rows):
    seen = {a for (_, a) in rows}
    tail = sorted(a for a in seen if a != "none")
    return (["none"] if "none" in seen else []) + tail


# ------------------------------------------------------------ форматирование -
def gnum(v, digits=0):
    """1234567 -> 1 234 567 ; 189.4 -> 189,4"""
    if v is None:
        return "—"
    s = ("%%.%df" % digits) % v
    if "." in s:
        head, tail = s.split(".")
        tail = "," + tail
    else:
        head, tail = s, ""
    sign = "−" if head.startswith("-") else ""
    head = head.lstrip("-")
    out = ""
    while len(head) > 3:
        out = NBSP + head[-3:] + out
        head = head[:-3]
    return sign + head + out + tail


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def mean_of(rows, arm, field, only=("ok", "weak")):
    vals = []
    for (_, a), r in rows.items():
        if a != arm or classify(r) not in only:
            continue
        v = num(r.get(field))
        if v is not None:
            vals.append(v)
    return st.mean(vals) if vals else None


# ------------------------------------------------------------------ таблицы --
def main_table(rows):
    """Сводка по армам — это таблица главного слайда."""
    arms = arm_order(rows)
    counted = {}
    for a in arms:
        runs = [r for (_, aa), r in rows.items() if aa == a]
        counted[a] = {
            "total": len(runs),
            "done": sum(1 for r in runs if classify(r) != "fail"),
            "turns": mean_of(rows, a, "turns"),
            "dur": mean_of(rows, a, "duration_s"),
            "chars": mean_of(rows, a, "answer_chars"),
            "refs": mean_of(rows, a, "line_refs"),
        }
    best = max(arms, key=lambda a: (counted[a]["refs"] or -1)) if arms else None

    out = ['<table class="tbl tbl-main">']
    out.append(
        "<thead><tr>"
        "<th scope=\"col\">арм</th>"
        "<th scope=\"col\" class=\"n\">ссылок <span class=\"mono\">файл:строка</span></th>"
        "<th scope=\"col\" class=\"n\">объём отчёта, симв.</th>"
        "<th scope=\"col\" class=\"n\">шагов агента</th>"
        "<th scope=\"col\" class=\"n\">время, с</th>"
        "<th scope=\"col\" class=\"n\">прогонов</th>"
        "</tr></thead><tbody>"
    )
    for a in arms:
        c = counted[a]
        cls = ' class="best"' if a == best and len(arms) > 1 else ""
        mark = '<span class="tick" aria-hidden="true"></span>' if a == best and len(arms) > 1 else ""
        out.append(
            "<tr%s><th scope=\"row\">%s%s<span class=\"arm-code mono\">%s</span></th>"
            "<td class=\"n hero-cell\">%s</td><td class=\"n\">%s</td>"
            "<td class=\"n\">%s</td><td class=\"n\">%s</td>"
            "<td class=\"n dim\">%s</td></tr>"
            % (cls, mark, esc(ARM_TITLE.get(a, a)), esc(a),
               gnum(c["refs"], 0), gnum(c["chars"], 0),
               gnum(c["turns"], 1), gnum(c["dur"], 0),
               "%d / %d" % (c["done"], c["total"]))
        )
    out.append("</tbody></table>")

    fails = sum(1 for r in rows.values() if classify(r) == "fail")
    model = next((r.get("model") for r in rows.values() if r.get("model")), "?")
    note = (
        '<p class="tbl-note">Модель <span class="mono">%s</span>, датасетов: %d. '
        'Средние — по состоявшимся прогонам%s. '
        'Промпт во всех армах побайтно одинаковый, единственная переменная — навык.</p>'
        % (esc(model), len({d for (d, _) in rows}),
           ("; %d не состоявшихся исключены и перечислены в приложении" % fails) if fails else "")
    )
    return "\n".join(out) + "\n" + note


CHIP_SHORT = {"ok": "ок", "weak": "без ссылок", "fail": "не состоялся"}


def detail_table(rows):
    """Матрица «датасет × арм» — слайд приложения. Одна строка на датасет,
    чтобы таблица оставалась читаемой с задних рядов при любом числе армов."""
    arms = arm_order(rows)
    datasets = sorted({d for (d, _) in rows})
    out = ['<table class="tbl tbl-detail">']
    out.append('<thead><tr><th scope="col">датасет</th>')
    for a in arms:
        out.append('<th scope="col" class="n">%s</th>' % esc(ARM_TITLE.get(a, a)))
    out.append("</tr></thead><tbody>")
    for d in datasets:
        out.append('<tr><th scope="row">%s</th>' % esc(d))
        for a in arms:
            r = rows.get((d, a))
            if not r:
                out.append('<td class="n dim">—</td>')
                continue
            s = classify(r)
            out.append(
                '<td class="n cell st-%s"><span class="cn">%s</span>'
                '<span class="chip chip-%s">%s</span></td>'
                % (s, gnum(r.get("line_refs")), s, CHIP_SHORT[s])
            )
        out.append("</tr>")
    out.append("</tbody></table>")
    out.append(
        '<p class="tbl-note">Крупное число — сколько ссылок <span class="mono">файл:строка</span> '
        'оказалось в отчёте. Прогоны, помеченные «не состоялся», остаются в реестре и видны здесь, '
        'но не попадают в средние на слайде 7.</p>'
    )
    return "\n".join(out)


# -------------------------------------------------------------------- вывод --
BLOCKS = [("MAIN-TABLE", main_table), ("DETAIL-TABLE", detail_table)]


def patch(html, name, body):
    begin, end = "<!-- %s:BEGIN -->" % name, "<!-- %s:END -->" % name
    i, j = html.find(begin), html.find(end)
    if i < 0 or j < 0:
        sys.exit("в index.html нет маркеров %s / %s" % (begin, end))
    return html[: i + len(begin)] + "\n" + body + "\n" + html[j:]


def run():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--index", default=INDEX)
    ap.add_argument("--stdout", action="store_true", help="напечатать, не менять index.html")
    args = ap.parse_args()

    rows = load(args.ledger)
    parts = [(name, fn(rows)) for name, fn in BLOCKS]

    if args.stdout:
        for name, body in parts:
            print("<!-- %s -->" % name)
            print(body)
            print()
        return

    html = open(args.index, encoding="utf-8").read()
    for name, body in parts:
        html = patch(html, name, body)
    open(args.index, "w", encoding="utf-8").write(html)

    kinds = {}
    for r in rows.values():
        kinds[classify(r)] = kinds.get(classify(r), 0) + 1
    print("✓ index.html обновлён: %d ячеек (%s)"
          % (len(rows), ", ".join("%s=%d" % kv for kv in sorted(kinds.items()))))


if __name__ == "__main__":
    run()
