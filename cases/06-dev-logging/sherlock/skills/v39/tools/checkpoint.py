#!/usr/bin/env python3
"""Create a durable resume receipt and an honest report skeleton."""
import argparse
import datetime
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inspect_worklists(work):
    paths = sorted(work.glob("worklist*.tsv"))
    if not paths:
        raise ValueError("no worklist*.tsv in checkpoint")
    total = resolved = 0
    seals = {}
    for path in paths:
        raw = path.read_bytes()
        seals[path.name] = hashlib.sha256(raw).hexdigest()
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                raise ValueError("malformed worklist row in %s" % path.name)
            total += 1
            if cols[1].strip() and not cols[1].lstrip().startswith("?"):
                resolved += 1
    return total, resolved, seals


PLACEHOLDER_MARKER = ("СИНТЕЗ НЕ ЗАВЕРШЁН — удали эту строку последним "
                      "действием синтеза.")

#: the shape a v38 `init` produced. Kept because a run that starts under v38 and
#: finishes under v39 must not keep the frozen count either.
LEGACY_PLACEHOLDER_RE = re.compile(
    "\\A# Отчёт Sherlock\\n\\nСостояние: частичный отчёт; синтез ещё не завершён\\.\\n\\n"
    "Разобрано строк рабочего списка: \\d+ из \\d+\\.\\n\\Z")


def render_placeholder(row):
    """The report skeleton, ALWAYS agreeing with the checkpoint beside it.

    fix 5b. MEASURED, v38 paid run 20260826T132832Z-v38: the old `init` wrote the
    stub only `if not report.exists() or not report.read_text().strip()`, so the
    FIRST call — at 13 of 262 rows resolved — froze «Разобрано строк рабочего
    списка: 13 из 262» and every later call left it alone. End state on disk:
    `checkpoint.json` = {"state": "ready_for_synthesis", "resolved": 262,
    "total": 262} at 15:35:05Z beside a 192-byte `report.md` still claiming 13.
    The run's own progress signal read 5 % complete while it was 100 %.

    An artifact that contradicts the state file next to it is worse than no
    artifact, so the placeholder states the machine state VERBATIM and names the
    next action.
    """
    out = ["# Отчёт Sherlock", "", PLACEHOLDER_MARKER, ""]
    if row["state"] == "ready_for_synthesis":
        out += [
            "Состояние: ready_for_synthesis — рабочий список разобран ПОЛНОСТЬЮ "
            "(%d из %d); синтез не начат." % (row["resolved"], row["total"]),
            "",
            "СЛЕДУЮЩЕЕ ДЕЙСТВИЕ: писать этот файл ПО РАЗДЕЛАМ, начиная сейчас, а "
            "не в конце. Порядок: «## Находки» (по одному блоку `### Н-n` за раз), "
            "«## Отклонённые кандидаты», «## Принадлежность учётных записей», "
            "«## Покрытие», «## Окно записей». Каждый готовый блок дописывай в файл "
            "сразу.",
        ]
    else:
        out += [
            "Состояние: %s — частичный отчёт; синтез ещё не завершён." % row["state"],
            "",
            "Разобрано строк рабочего списка: %d из %d."
            % (row["resolved"], row["total"]),
            "",
            "СЛЕДУЮЩЕЕ ДЕЙСТВИЕ: закрыть вердиктом D / N / X остальные %d строк "
            "рабочего списка, затем перезапустить checkpoint init."
            % row["unresolved"],
        ]
    return "\n".join(out) + "\n"


_SHAPE_SENTINEL = 987654321


def _shape_re(state):
    """The generated text itself, with only its numbers loosened.

    EXACT, never fuzzy: the pattern IS `re.escape(render_placeholder(...))`, so a
    single character the arm typed into the file stops it matching and the file is
    left alone. This tool must not be able to destroy the report it protects.
    """
    text = render_placeholder({"state": state, "resolved": _SHAPE_SENTINEL,
                               "total": _SHAPE_SENTINEL,
                               "unresolved": _SHAPE_SENTINEL})
    return re.compile("\\A" + re.escape(text).replace(
        re.escape(str(_SHAPE_SENTINEL)), "\\d+") + "\\Z")


PLACEHOLDER_SHAPES = (_shape_re("ready_for_synthesis"),
                      _shape_re("resume_triage"),
                      LEGACY_PLACEHOLDER_RE)


def is_placeholder(text):
    """True only for text byte-identical to a placeholder this tool generates."""
    return any(rx.match(text) for rx in PLACEHOLDER_SHAPES)


def init(work):
    work = work.resolve(strict=True)
    total, resolved, seals = inspect_worklists(work)
    unresolved = total - resolved
    row = {
        "schema": 1,
        "state": "ready_for_synthesis" if unresolved == 0 else "resume_triage",
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "worklists": seals,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    atomic_text(work / "checkpoint.json", json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report = work / "report.md"
    try:
        existing = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        existing = None
    if existing is None or not existing.strip():
        action = "created"
    elif is_placeholder(existing):
        action = "regenerated"          # fix 5b: never freeze a stale count
    else:
        action = "preserved"            # real content, or a partial report
    if action != "preserved":
        atomic_text(report, render_placeholder(row))
    row["report"] = action
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init",))
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    print(json.dumps(init(Path(args.work)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
