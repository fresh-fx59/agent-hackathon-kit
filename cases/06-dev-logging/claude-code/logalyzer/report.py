from logalyzer import VERSION

_SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}

def build(matches, bundle, coderefs, mode, meta):
    matches = sorted(matches, key=lambda m: _SEV_ORDER.get(m["severity"], 9))
    top = matches[0] if matches else None
    primary_ref = coderefs[0] if (mode == "dev" and coderefs) else None
    limitations = []
    if mode == "ops":
        limitations.append("Режим без доступа к коду: указание файла/метода недоступно; "
                           "root cause дан на уровне сервиса. Для точного указания в коде "
                           "запустите с --repo <путь к исходникам>.")
    if not matches:
        limitations.append("Ни одно активное правило не сработало: отчёт содержит только "
                           "таймлайн и статистику. Возможен новый класс инцидента.")
    timeline = [{"ev": it["id"], "ts": it["record"].timestamp,
                 "service": it["record"].service, "event": it["record"].body[:160]}
                for it in bundle.items]
    cause_chain = [m["hypothesis"] for m in matches]
    root_service = ""
    if top:
        ev = bundle.by_id(top["evidence_ids"][-1])
        root_service = ev["record"].service if ev else ""
    return {
        "mode": mode,
        "classification": {
            "type": top["rule_id"] if top else "unclassified",
            "severity": top["severity"] if top else "unknown",
            "confidence": "high" if top else "low"},
        "timeline": timeline,
        "cause_chain": cause_chain,
        "root_cause": {
            "service": root_service,
            "description": top["hypothesis"] if top else "",
            "file": primary_ref["file"] if primary_ref else None,
            "method": primary_ref["method"] if primary_ref else None,
            "line": primary_ref["line"] if primary_ref else None},
        "invariant_violations": [m["invariant_ref"] for m in matches if m.get("invariant_ref")],
        "evidence": bundle.to_json(),
        "immediate_actions": _actions(matches, mode),
        "code_recommendations": ([{"file": c["file"], "method": c["method"], "line": c["line"],
                                   "reason": c["reason"], "confidence": c["confidence"]}
                                  for c in coderefs] if mode == "dev" else []),
        "limitations": limitations,
        "meta": dict(meta, rubric_sha=(matches[0]["rubric_sha"] if matches else ""),
                     generated_by="logalyzer %s (deterministic baseline, no LLM)" % VERSION),
    }

def _actions(matches, mode):
    out = []
    for m in matches:
        if m["rule_id"] == "R-ORD-001":
            out.append("Проверить зависшие авторизации платежей (reconciliation) и запустить компенсацию.")
        if m["rule_id"] == "R-ORD-002":
            out.append("Поднять клиентский таймаут вызова inventory или снизить латентность "
                       "(масштабирование/индексы), затем вернуть ретраи.")
        if m["rule_id"] == "R-INV-001":
            out.append("Найти и освободить orphaned-резервы склада за период инцидента.")
    if mode == "ops" and matches:
        out.append("Передать отчёт команде разработки для фикса на уровне кода.")
    return out

def render_ru(rep):
    L = []
    L.append("# Отчёт RCA — %s" % rep["meta"].get("correlation_id", ""))
    L.append("")
    L.append("Режим: %s. Классификация: %s / %s (уверенность: %s)." % (
        "с доступом к коду" if rep["mode"] == "dev" else "без доступа к коду (DevOps)",
        rep["classification"]["type"], rep["classification"]["severity"],
        rep["classification"]["confidence"]))
    if rep["invariant_violations"]:
        L.append("Нарушенные инварианты SDD: %s." % ", ".join(rep["invariant_violations"]))
    L.append("")
    L.append("## Причинная цепочка")
    for i, c in enumerate(rep["cause_chain"], 1):
        L.append("%d. %s" % (i, c))
    L.append("")
    L.append("## Root cause")
    rc = rep["root_cause"]
    L.append("- Сервис: `%s`" % rc["service"])
    L.append("- Описание: %s" % rc["description"])
    if rc["file"]:
        L.append("- Код: `%s`, метод `%s`, строка %s" % (rc["file"], rc["method"], rc["line"]))
    L.append("")
    L.append("## Таймлайн (доказательства)")
    for t in rep["timeline"]:
        L.append("- [%s] %s `%s` — %s" % (t["ev"], t["ts"], t["service"], t["event"]))
    L.append("")
    if rep["immediate_actions"]:
        L.append("## Немедленные действия")
        for a in rep["immediate_actions"]:
            L.append("- %s" % a)
        L.append("")
    if rep["code_recommendations"]:
        L.append("## Рекомендации по коду")
        for c in rep["code_recommendations"]:
            L.append("- `%s` → `%s` (строка %s): %s [уверенность: %s]" %
                     (c["file"], c["method"], c["line"], c["reason"], c["confidence"]))
        L.append("")
    if rep["limitations"]:
        L.append("## Ограничения")
        for l in rep["limitations"]:
            L.append("- %s" % l)
        L.append("")
    L.append("---")
    L.append("_Сгенерировано: %s; rubric_sha: %s._" %
             (rep["meta"]["generated_by"], rep["meta"].get("rubric_sha", "")))
    return "\n".join(L)
