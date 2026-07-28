#!/usr/bin/env python3
"""Authoritative scoring, recomputed from the raw transcripts in measure/raw/.

Two things `record.py` gets wrong at write time, both discovered mid-experiment
and both fixed here rather than by editing the live scorer (which would make
early and late runs incomparable):

1. **The answer is not always the `result` field.** Sometimes the model writes
   the whole report in intermediate assistant turns and leaves `result` as a
   stub ("Отчёт готов. Расследование завершено." — 37 chars). Scoring that stub
   reports a perfect investigation as a total failure. The same artifact is
   visible in the pre-existing `eval/runs.jsonl` (nginx v1 = 181 chars,
   nginx v2 = 253 chars), so it is a harness bug, not a model failure.
   Here the answer is **every assistant text block concatenated**.
2. **One quality regex was loose**: `user test` also matches "Invalid user
   testing". Tightened with word boundaries.

    python3 measure/rescore.py            # per-run table
    python3 measure/rescore.py --summary  # medians per cell + the deltas
    python3 measure/rescore.py --json
"""
import json
import glob
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")

CHECKS = {
    "OpenSSH": {
        "bruteforce": r"(?i)brute[- ]?force|перебор|подбор пароля",
        "attacker":   r"187\.141\.143\.180|5\.188\.10\.180|103\.99\.0\.122|173\.234\.31\.186",
        "escalation": r"fztu|119\.137\.62\.142",
        "action":     r"(?i)PasswordAuthentication|PermitRootLogin|fail2ban|pam_faillock",
    },
    "Linux": {
        "bruteforce": r"(?i)brute[- ]?force|перебор|подбор пароля",
        "attacker":   r"150\.183\.249\.110|218\.188\.2\.4|207\.243\.167\.114|60\.30\.224\.116|195\.129\.24\.210",
        "escalation": r"(?i)uid=509|user test\b|user=test\b|пользовател\w+ test\b|для test\b",
        "action":     r"(?i)PasswordAuthentication|PermitRootLogin|fail2ban|pam_faillock",
        "coverage":   r"(?i)\bftpd?\b",
    },
}

CORPUS = {"OpenSSH": "OpenSSH_2k.log", "Linux": "Linux_2k.log"}
LOGS = os.path.expanduser("~/hack/logalyzer-real-world-testset/real-logs")


def full_text(records):
    """Everything the model said, not just the last thing it said."""
    out = []
    for r in records:
        if r.get("type") != "assistant":
            continue
        for c in ((r.get("message") or {}).get("content") or []):
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                out.append(c["text"])
    final = next((r for r in records if r.get("type") == "result"), None)
    if final and final.get("result"):
        out.append(final["result"])
    return "\n".join(out)


def line_count(ds):
    p = os.path.join(LOGS, ds, CORPUS.get(ds, ""))
    try:
        with open(p, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def load():
    rows = []
    for path in sorted(glob.glob(os.path.join(RAW, "*.json"))):
        name = os.path.basename(path)[:-5]          # <ds>-<arm>-rep<N>
        ds, arm, rep = name.rsplit("-", 2)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        final = next((r for r in d if r.get("type") == "result"), None)
        if final is None:
            continue
        text = full_text(d)
        n = line_count(ds)
        refs = re.findall(r"([A-Za-z0-9_.\-]+\.log):(\d+)", text)
        checks = {k: bool(re.search(v, text)) for k, v in CHECKS.get(ds, {}).items()}
        tool_calls = sum(1 for r in d
                         for c in ((r.get("message") or {}).get("content") or [])
                         if isinstance(c, dict) and c.get("type") == "tool_use")
        kb = sum(1 for r in d
                 for c in ((r.get("message") or {}).get("content") or [])
                 if isinstance(c, dict) and c.get("type") == "tool_use"
                 and "knowledge" in json.dumps(c.get("input") or {}, ensure_ascii=False))
        u = final.get("usage") or {}
        know = re.search(r"ЗНАНИЯ\s*:\s*(.{0,110})", text)
        rows.append({
            "dataset": ds, "arm": arm, "rep": int(rep.replace("rep", "")),
            "turns": final.get("num_turns"), "tool_calls": tool_calls,
            "api_s": round((final.get("duration_api_ms") or 0) / 1000, 1),
            "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
            "chars": len(text), "refs": len(refs),
            "refs_ok": sum(1 for _f, x in refs if n and 1 <= int(x) <= n),
            "kb_reads": kb,
            "quality": sum(checks.values()), "quality_max": len(checks),
            "checks": checks,
            "knowledge_line": know.group(1).strip() if know else None,
        })
    return rows


def wall(rows_ledger, ds, arm, rep):
    for r in rows_ledger:
        if r["dataset"] == ds and r["arm"] == arm and r["rep"] == rep:
            return r.get("duration_s")
    return None


def main():
    rows = load()
    if not rows:
        sys.exit("нет транскриптов в %s" % RAW)
    ledger = []
    lp = os.path.join(HERE, "runs.jsonl")
    if os.path.exists(lp):
        ledger = [json.loads(l) for l in open(lp, encoding="utf-8") if l.strip()]
    for r in rows:
        r["duration_s"] = wall(ledger, r["dataset"], r["arm"], r["rep"])

    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    if "--summary" not in sys.argv:
        print("%-9s %-5s %-4s %-6s %-6s %-7s %-7s %-8s %-6s %s"
              % ("корпус", "база", "rep", "шагов", "вызов", "время", "симв.",
                 "ссылок", "kb", "качество"))
        for r in sorted(rows, key=lambda x: (x["dataset"], x["arm"], x["rep"])):
            print("%-9s %-5s %-4d %-6s %-6s %-7s %-7d %-8s %-6d %d/%d %s"
                  % (r["dataset"], r["arm"], r["rep"], r["turns"], r["tool_calls"],
                     r["duration_s"], r["chars"], "%d(%d)" % (r["refs"], r["refs_ok"]),
                     r["kb_reads"], r["quality"], r["quality_max"],
                     "".join("✓" if v else "✗" for v in r["checks"].values())))
        print()

    def cell(ds, arm, reps=None):
        return [r for r in rows if r["dataset"] == ds and r["arm"] == arm
                and (reps is None or r["rep"] in reps)]

    def med(rs, f):
        v = [r[f] for r in rs if isinstance(r.get(f), (int, float))]
        return st.median(v) if v else None

    def block(title, reps_cold, reps_warm):
        print("## %s\n" % title)
        print("| ячейка | n | шагов | вызовов | время, с | качество |")
        print("|---|---|---|---|---|---|")
        cells = [("OpenSSH", "cold", reps_cold), ("Linux", "cold", reps_cold),
                 ("Linux", "warm", reps_warm)]
        got = {}
        for ds, arm, reps in cells:
            rs = cell(ds, arm, reps)
            if not rs:
                continue
            got[(ds, arm)] = rs
            print("| %s %s | %d | %s | %s | %s | %s/%s |"
                  % (ds, arm, len(rs), med(rs, "turns"), med(rs, "tool_calls"),
                     med(rs, "duration_s"), med(rs, "quality"), rs[0]["quality_max"]))
        print()
        print("| сравнение | метрика | было → стало | изменение |")
        print("|---|---|---|---|")
        for a, b, label in [(("Linux", "cold"), ("Linux", "warm"),
                             "Linux холодный → Linux с карточкой"),
                            (("OpenSSH", "cold"), ("Linux", "warm"),
                             "инцидент №1 → инцидент №2")]:
            if a not in got or b not in got:
                continue
            for f, fl in (("turns", "шагов"), ("duration_s", "время"),
                          ("tool_calls", "вызовов инстр."),
                          ("input_tokens", "вход. токенов")):
                va, vb = med(got[a], f), med(got[b], f)
                if not va or vb is None:
                    continue
                print("| %s | %s | %s → %s | **%+.0f %%** |"
                      % (label, fl, round(va), round(vb), (vb - va) / va * 100))
        print()

    reps = sorted({r["rep"] for r in rows})
    clean_cold = [x for x in reps if x >= 2]
    if clean_cold and len(clean_cold) >= 2:
        block("Основной расчёт — байт-идентичный текст навыка "
              "(cold rep %s, warm все)" % ",".join(map(str, clean_cold)),
              clean_cold, None)
    block("Все прогоны", None, None)
    print("Отрицательное значение = меньше/быстрее. Критерий кейса: −30 % по времени до RCA.")


if __name__ == "__main__":
    main()
