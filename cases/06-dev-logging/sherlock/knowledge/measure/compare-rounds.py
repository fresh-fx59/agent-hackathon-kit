#!/usr/bin/env python3
"""Both measurement rounds side by side — the whole numeric story in one table.

    python3 measure/compare-rounds.py

Round 1: first card format (additive checks) on skill v1.
Round 2: redesigned format with a mandatory "what you may SKIP" section, on v2.
"""
import json, os, subprocess, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
ROUNDS = [("раунд 1 — карточка только ДОБАВЛЯЛА проверки (навык v1)",
           os.path.join(HERE, "round1-v1-additive/raw"),
           os.path.join(HERE, "round1-v1-additive/runs.jsonl")),
          ("раунд 2 — карточка РАЗРЕШАЕТ пропускать (навык v2)",
           os.path.join(HERE, "raw"), os.path.join(HERE, "runs.jsonl"))]


def load(raw, ledger):
    env = dict(os.environ, SHERLOCK_RAW=raw, SHERLOCK_LEDGER=ledger)
    out = subprocess.run(["python3", os.path.join(HERE, "rescore.py"), "--json"],
                         capture_output=True, text=True, env=env).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def med(rs, f):
    v = [r[f] for r in rs if isinstance(r.get(f), (int, float))]
    return st.median(v) if v else None


print("# Два раунда — что дала переработка формата карточки\n")
for title, raw, ledger in ROUNDS:
    rows = load(raw, ledger)
    if not rows:
        print("## %s\n\n(нет данных)\n" % title)
        continue
    print("## %s\n" % title)
    print("| ячейка | n | шагов | вызовов инстр. | время, с | качество |")
    print("|---|---|---|---|---|---|")
    cells = {}
    for ds, arm in [("Linux", "cold"), ("Linux", "warm")]:
        rs = [r for r in rows if r["dataset"] == ds and r["arm"] == arm]
        if not rs:
            continue
        cells[arm] = rs
        print("| %s %s | %d | %s | %s | %s | %s/%s |"
              % (ds, arm, len(rs), med(rs, "turns"), med(rs, "tool_calls"),
                 med(rs, "duration_s"), med(rs, "quality"), rs[0]["quality_max"]))
    if "cold" in cells and "warm" in cells:
        print("\n| метрика | холодный → с карточкой | изменение |")
        print("|---|---|---|")
        for f, fl in (("turns", "шагов"), ("tool_calls", "вызовов инстр."),
                      ("duration_s", "время")):
            a, b = med(cells["cold"], f), med(cells["warm"], f)
            if a and b is not None:
                print("| %s | %s → %s | **%+.0f %%** |" % (fl, round(a), round(b),
                                                          (b - a) / a * 100))
    print()
print("Критерий кейса: −30 % по времени до RCA. "
      "Положительное значение = стало дороже.")
