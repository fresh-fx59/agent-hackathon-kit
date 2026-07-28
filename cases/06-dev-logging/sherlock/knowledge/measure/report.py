#!/usr/bin/env python3
"""Сводка по measure/runs.jsonl: цена расследования и качество ответа.

    python3 measure/report.py           # таблица в markdown
    python3 measure/report.py --raw     # все прогоны построчно

Медиана, а не среднее: прогонов мало, а один зависший вызов API сдвигает
среднее сильнее, чем весь эффект, который мы измеряем.
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "runs.jsonl")

FIELDS = [("turns", "шагов"), ("tool_calls", "вызовов инстр."),
          ("duration_s", "время, с"), ("input_tokens", "вход. токенов"),
          ("output_tokens", "вых. токенов"), ("answer_chars", "ответ, симв."),
          ("line_refs", "ссылок файл:строка")]


def load():
    if not os.path.exists(LEDGER):
        sys.exit("нет измерений: %s" % LEDGER)
    rows = []
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def med(rows, field):
    vals = [r[field] for r in rows if isinstance(r.get(field), (int, float))]
    return st.median(vals) if vals else None


def fmt(v):
    if v is None:
        return "—"
    return "%d" % round(v) if abs(v) >= 10 else "%.1f" % v


def main():
    rows = load()
    if "--raw" in sys.argv:
        for r in rows:
            print("%-9s %-5s rep%-2s шагов=%-3s %4ss  карточек=%s  "
                  "kb_reads=%-2s качество=%s/%s  ЗНАНИЯ=%s"
                  % (r["dataset"], r["arm"], r["rep"], r["turns"], r["duration_s"],
                     r["cards_in_kb"], r.get("kb_reads"), r.get("quality_score"),
                     r.get("quality_max"), (r.get("knowledge_line") or "—")[:60]))
        return

    cells = {}
    for r in rows:
        cells.setdefault((r["dataset"], r["arm"]), []).append(r)
    order = [k for k in [("OpenSSH", "cold"), ("Linux", "cold"), ("Linux", "warm")]
             if k in cells] + [k for k in sorted(cells)
                               if k not in [("OpenSSH", "cold"), ("Linux", "cold"),
                                            ("Linux", "warm")]]

    print("# Цикл самообучения — измерения\n")
    print("Модель: %s. Медиана по повторам.\n" % (rows[0].get("model") or "?"))

    print("| корпус | база | n | " + " | ".join(t for _, t in FIELDS) +
          " | качество | карточки прочитаны |")
    print("|" + "---|" * (len(FIELDS) + 5))
    for k in order:
        rs = cells[k]
        q = [r.get("quality_score") for r in rs if r.get("quality_score") is not None]
        qmax = rs[0].get("quality_max")
        print("| %s | **%s** | %d | %s | %s/%s | %d |" % (
            k[0], k[1], len(rs),
            " | ".join(fmt(med(rs, f)) for f, _ in FIELDS),
            fmt(st.median(q)) if q else "—", qmax,
            sum(1 for r in rs if (r.get("kb_reads") or 0) > 0)))

    def delta(a, b, field, label):
        ra, rb = cells.get(a), cells.get(b)
        if not ra or not rb:
            return
        va, vb = med(ra, field), med(rb, field)
        if not va or vb is None:
            return
        print("| %s | %s | %s → %s | **%+.0f %%** |"
              % (label, dict(FIELDS)[field], fmt(va), fmt(vb), (vb - va) / va * 100))

    print("\n## Изменение относительно холодного прогона\n")
    print("| сравнение | метрика | было → стало | изменение |")
    print("|---|---|---|---|")
    for f in ("turns", "duration_s", "tool_calls", "input_tokens"):
        delta(("Linux", "cold"), ("Linux", "warm"), f,
              "тот же корпус, база пустая → база с карточкой (честное сравнение)")
    print("|  |  |  |  |")
    for f in ("turns", "duration_s"):
        delta(("OpenSSH", "cold"), ("Linux", "warm"), f,
              "инцидент №1 (без знаний) → инцидент №2 (со знанием)")

    print("\nОтрицательное значение = стало меньше/быстрее. "
          "Критерий кейса: −30 % и лучше по времени до RCA.")


if __name__ == "__main__":
    main()
