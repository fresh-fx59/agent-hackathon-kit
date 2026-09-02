#!/usr/bin/env python3
"""Create a durable resume receipt and an honest report skeleton.

v40 adds the STAGE MACHINE the interactive lane needs. The corporate harness runs
`qwen` interactively, so a stage cannot be a separate process: it is a `/clear`
plus a re-invoked skill inside the same process, and the only thing that survives
that boundary is this directory. So the stage lives HERE, on disk, beside the
counts — and `handoff` prints the literal block the human copies.

Read out of the installed qwen-code 0.22.0 bundle (clearCommand.ts), and both
facts are load-bearing below: `/clear` calls `skillTool.clearLoadedSkills()`, so
the skill body is dropped and `/sherlock` must be typed again; and it REFUSES
while blocking background work is alive, so a stage must not end with a
background task running.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
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


#: the bounded stages, in order. A stage is one `/clear`-to-`/clear` span.
#: `triage` covers MAP + TRIAGE (they share the worklist and must not be split);
#: `draft` writes the report; `repair` exists because a gate can fail after the
#: report is written and repairing it in the draft session is what re-grew the
#: context on r6.
STAGES = ("triage", "draft", "repair", "done")

#: THE BOUNDARY COUNTER'S NAME, and why the stage alone was not enough.
#:
#: CAUGHT IN REVIEW before the v41 launch: `handoff --partial` deliberately does
#: NOT advance the stage, while the driver reacted only to a stage ADVANCE. So the
#: first batch boundary was invisible: the driver waited, nudged the model to carry
#: on in the SAME session — the opposite of what a batch boundary is for — and
#: exited STAGE_STALLED without ever typing `/clear`. Both halves had tests; no
#: test ran them together, which is exactly how an integration failure hides.
#:
#: «A boundary happened» and «the stage changed» are two different facts. They
#: only looked like one while every boundary ended a stage. This counter is the
#: first fact, on its own: it rises on EVERY handoff, partial or full, and never
#: falls — not on an `init` inside a stage, and not when
#: `triagecheck --refresh-checkpoint` rewrites the same file.
BOUNDARY_SEQ = "boundary_seq"

#: THE schema number. It lives here because `checkpoint.py` is not the only
#: writer of checkpoint.json — `triagecheck.py --refresh-checkpoint` rewrites it
#: too, and on v39 that second writer hard-coded `"schema": 1` while this one
#: wrote 1 as well. The moment this file moved to 2, the two writers would have
#: disagreed depending on which ran last, and a reader cannot tell a v1 row from
#: a v2 row that lost its stage. One constant, imported by the other writer.
SCHEMA = 2


def next_stage(stage):
    return STAGES[STAGES.index(stage) + 1]


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


def read_row(work):
    """The checkpoint as it stands, or {} — never an invented one."""
    try:
        text = (Path(work) / "checkpoint.json").read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        row = json.loads(text)
    except ValueError:
        return {}
    return row if isinstance(row, dict) else {}


def render_handoff(work, done, row, partial=False):
    """The literal block a human copies. Russian: a human reads it.

    Every line is load-bearing. The absolute path is here because a cleared
    session has no memory of the working directory. The background-work warning
    is here because `/clear` REFUSES while a background task is alive, and a
    refused clear looks exactly like a clear that worked.
    """
    stage = row["stage"]
    if partial:
        # A BATCH BOUNDARY, NOT A STAGE BOUNDARY. Measured on the paid run
        # 20260827T104334Z-v40: 13 of 262 worklist rows closed in 35 minutes
        # while the session grew to a 227,030-token prompt. 262 rows do not close
        # in one context, so a long stage has to be many bounded sessions - and
        # the coverage rule («triage EVERY line») is untouched: the stage does not
        # advance until every row is closed.
        if done in ("draft", "repair"):
            state_line = ("СОСТОЯНИЕ: %s/checkpoint.json (stage=%s, разделов "
                          "написано %d из %d, отчёт %d байт)"
                          % (work, stage, row.get("report_sections_written", 0),
                             row.get("report_sections_required", 0),
                             row.get("report_bytes", 0)))
        else:
            state_line = ("СОСТОЯНИЕ: %s/checkpoint.json (stage=%s, разобрано "
                          "%d из %d, осталось %d)"
                          % (work, stage, row["resolved"], row["total"],
                             row["unresolved"]))
        return "\n".join([
            "ЧАСТЬ СТУПЕНИ %s ЗАВЕРШЕНА — СТУПЕНЬ ПРОДОЛЖАЕТСЯ." % done,
            state_line,
            "",
            "ДАЛЬШЕ — ВЫПОЛНИ ТРИ ДЕЙСТВИЯ ПО ПОРЯДКУ, НЕ ПРОДОЛЖАЙ В ЭТОЙ "
            "СЕССИИ:",
            "  1) /clear",
            "  2) /sherlock",
            "  3) вставь одной строкой:",
            "     ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ %s — СТУПЕНЬ %s" % (work, stage),
            "",
            "ПОЧЕМУ: контекст этой сессии уже израсходован на разобранную часть. "
            "Следующая партия строк должна начаться с чистого контекста — на "
            "оплаченном прогоне одна сессия дошла до 327 639 токенов при потолке "
            "262 000.",
            "/clear стирает загружённый навык, поэтому шаг 2 обязателен.",
            "/clear ОТКАЖЕТСЯ, пока живёт фоновая задача — фоновых задач тут "
            "быть не должно.",
        ]) + "\n"
    lines = [
        "СТУПЕНЬ ЗАВЕРШЕНА: %s" % done,
        "СОСТОЯНИЕ: %s/checkpoint.json (stage=%s, разобрано %d из %d)"
        % (work, stage, row["resolved"], row["total"]),
    ]
    if stage == "done":
        lines += ["", "РАССЛЕДОВАНИЕ ЗАВЕРШЕНО. Отчёт: %s/report.md" % work,
                  "Больше ступеней нет — /clear не нужен."]
        return "\n".join(lines) + "\n"
    lines += [
        "",
        "ДАЛЬШЕ — ВЫПОЛНИ ТРИ ДЕЙСТВИЯ ПО ПОРЯДКУ, НЕ ПРОДОЛЖАЙ В ЭТОЙ СЕССИИ:",
        "  1) /clear",
        "  2) /sherlock",
        "  3) вставь одной строкой:",
        "     ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ %s — СТУПЕНЬ %s" % (work, stage),
        "",
        "ПОЧЕМУ: следующая ступень должна начаться с чистого контекста — на "
        "оплаченном прогоне r6 одна сессия дошла до 327 639 токенов в запросе "
        "при потолке 262 000.",
        "/clear стирает загружённый навык, поэтому шаг 2 обязателен.",
        "/clear ОТКАЖЕТСЯ, пока живёт фоновая задача — фоновых задач тут быть "
        "не должно.",
    ]
    return "\n".join(lines) + "\n"


#: Stages where a BATCH boundary means something. v42 allowed only `triage`,
#: on the grounds that `draft` and `repair` are one piece of work with nothing
#: to count. On 20260830T190815Z-v42 that made the one mechanism that bounds a
#: session refuse to run in the 55-minute draft stage where BOTH clipped state
#: snapshots happened. `draft` and `repair` DO have a countable unit: the
#: report's own sections, which reportcheck and citecheck already enumerate.
BATCHED_STAGES = ("triage", "draft", "repair")

#: Section headings the report must carry, by the contract's own roles. Used
#: only to report progress at a partial boundary — the STAGE still does not
#: advance until the stage is actually finished.
REPORT_REQUIRED_ROLES = ("inventory", "missing_data", "coverage", "window",
                         "verdict")


def report_sections(work):
    """{'written', 'required', 'roles'} counted from work/report.md on disk.

    An in-progress section counts as ABSENT: the count comes from headings that
    are on disk with a non-empty body, so a half-written section cannot be
    mistaken for a finished one by the next session.
    """
    path = os.path.join(str(work), "report.md")
    roles, current, body = [], None, []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {"written": 0, "required": len(REPORT_REQUIRED_ROLES), "roles": []}
    for raw in lines + ["## "]:
        if raw.startswith("## "):
            if current and any(s.strip() for s in body):
                roles.append(current)
            current, body = raw[3:].strip(), []
        else:
            body.append(raw)
    return {"written": len(roles), "required": len(REPORT_REQUIRED_ROLES),
            "roles": roles}


HISTORY = "checkpoint.jsonl"

#: THE CANONICAL LIST — the one place these four names live. Both `--gate-tool`
#: below and measure/interactive-drive.py's progress gate must agree on which
#: boundaries the "the model was doing real preparatory work" escape covers;
#: keeping one copy here (the arm ships with this file, self-contained) means
#: there is nothing left in measure/ to drift out of sync with it. Approval
#: mode `yolo` lets the model itself choose `handoff`'s arguments, so this is
#: not documentation, it is validated below — see `--gate-tool`'s `choices`.
GATE_TOOLS = ("reportcheck", "citecheck", "statecheck", "triagecheck")


def append_boundary(work, row, gate_tools_run=()):
    """One append-only row per boundary — the only place a DELTA can be read.

    checkpoint.json is overwrite-in-place by design: it is the current state.
    That makes it useless for the question the driver has to answer, which is
    whether anything changed between boundary N and N+1. Paid run
    20260901T002401Z-v43 took 12 boundaries with report_sections_written stuck
    at 0; after the fact only the final row survived and the delta was gone.

    Seals, not counts: inspect_worklists() already computes a sha256 per
    worklist, and a hash distinguishes real change from a rewrite that lands on
    the same row count. fsync before returning, because the driver clears the
    session immediately after this and a truncated history is worse than none.
    """
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "boundary_seq": row.get(BOUNDARY_SEQ),
        "stage": row.get("stage"),
        "stage_partial": row.get("stage_partial"),
        "resolved": row.get("resolved"),
        "total": row.get("total"),
        "report_bytes": row.get("report_bytes", 0),
        "report_sections_written": row.get("report_sections_written", 0),
        "report_sections_required": row.get("report_sections_required", 0),
        "worklist_seals": dict(row.get("worklists") or {}),
        "gate_tools_run": list(gate_tools_run),
    }
    path = os.path.join(str(work), HISTORY)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def handoff(work, done, partial=False, gate_tools_run=()):
    """Close stage `done` (or one BATCH of it), and print the block."""
    work = Path(work).resolve(strict=True)
    if done not in STAGES or done == "done":
        raise ValueError("unknown stage %r — stages are %s"
                         % (done, ", ".join(STAGES[:-1])))
    row = init(work)                      # refresh the counts before judging
    if row["stage"] != done:
        raise ValueError("checkpoint says stage=%s, not %s — finish that stage "
                         "first" % (row["stage"], done))
    if partial:
        if done not in BATCHED_STAGES:
            raise ValueError("--partial only means something in %s: %s is one "
                             "piece of work, not a countable list"
                             % (", ".join(BATCHED_STAGES), done))
        row["stage_partial"] = True
        row[BOUNDARY_SEQ] = int(row.get(BOUNDARY_SEQ, 0) or 0) + 1
        if done in ("draft", "repair"):
            secs = report_sections(work)
            row["report_sections_written"] = secs["written"]
            row["report_sections_required"] = secs["required"]
            try:
                row["report_bytes"] = os.path.getsize(
                    os.path.join(str(work), "report.md"))
            except OSError:
                row["report_bytes"] = 0
        row["updated_at"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        atomic_text(work / "checkpoint.json",
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        append_boundary(work, row, gate_tools_run=gate_tools_run)
        block = render_handoff(str(work), done, row, partial=True)
        atomic_text(work / "handoff.txt", block)
        return row, block
    if done == "triage" and row["unresolved"]:
        raise ValueError("triage is not finished: %d of %d worklist rows still "
                         "open — close them, then run handoff again"
                         % (row["unresolved"], row["total"]))
    if done in ("draft", "repair"):
        report = work / "report.md"
        try:
            text = report.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if not text.strip() or is_placeholder(text):
            raise ValueError("work/report.md is still the placeholder — the "
                             "draft stage is not finished")
        if PLACEHOLDER_MARKER in text:
            raise ValueError("work/report.md still carries the "
                             "«СИНТЕЗ НЕ ЗАВЕРШЁН» line — synthesis is not done")
    row["stage"] = next_stage(done)
    row["stage_closed"] = done
    row[BOUNDARY_SEQ] = int(row.get(BOUNDARY_SEQ, 0) or 0) + 1
    # The stage really ended, so the batch marker is spent. Left explicitly False
    # rather than deleted: a reader must be able to tell "no partial boundary was
    # ever taken" from "this key predates the feature".
    row["stage_partial"] = False
    row["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    atomic_text(work / "checkpoint.json",
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    append_boundary(work, row, gate_tools_run=gate_tools_run)
    block = render_handoff(str(work), done, row)
    atomic_text(work / "handoff.txt", block)
    return row, block


def reseed_line(work):
    """ONE LINE that says where the work stands, not where it lives.

    The v43 reseed was «ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ <dir> — СТУПЕНЬ <stage>» and
    was byte-identical at every boundary, so a reseeded session learned
    nothing from it and re-derived everything. Measured on
    20260901T002401Z-v43: reference/report-format.md was re-read in 11 of 11
    cycles, about 153,000 tokens on one document, and report.md was never
    written at all.

    `report_sections()` returns a DICT — {'written', 'required', 'roles'} —
    not a list of names, so the done-list here is built from `roles`
    (already the section headings actually on disk), never from the dict
    itself: `", ".join(dict)` would join its KEYS and print nonsense.

    THE ANTI-REDO CLAUSE IS CONDITIONAL ON boundary_seq > 0. CAUGHT IN REVIEW:
    a first version appended it unconditionally, so a BRAND-NEW investigation
    at `границ пройдено 0` — one that has read nothing at all — was told «НЕ
    ПЕРЕЧИТЫВАЙ справочники» before it had ever read them once. That is
    exactly the failure mode the spec's §C amendment warns about (§8/C6: a
    ceiling on RE-reading must never become a floor that discourages a first,
    genuine read). At boundary 0 the state line still prints (stage,
    resolved/total, sections, bytes) — that part is true and harmless at any
    boundary — but the slot that would carry an anti-redo instruction is
    replaced with an affirmative first-session instruction instead of being
    silently dropped, so the model is never left without SOME next-action
    text in that position.

    The anti-redo clause itself is copied in spirit from qwen's own
    post-compaction trailer — «Continue from the last in-flight step; do not
    acknowledge the summary, do not re-introduce» — because the one thing a
    model that HAS already read the material must not do is start over
    politely.
    """
    row = read_row(work) or {}
    sections = report_sections(work)
    done = ", ".join(sections["roles"]) if sections["roles"] else "нет"
    boundary_seq = row.get(BOUNDARY_SEQ, 0) or 0
    if boundary_seq > 0:
        action = ("НЕ ПЕРЕЧИТЫВАЙ справочники и НЕ ПОВТОРЯЙ уже сделанное — "
                  "продолжи со следующего незаконченного шага и пиши в "
                  "report.md сейчас.")
    else:
        # First session, nothing read yet — say so plainly rather than say
        # nothing: a first-session run still needs a next action.
        action = ("ЭТО ПЕРВАЯ СЕССИЯ РАССЛЕДОВАНИЯ — прочти нужные "
                  "справочники и начни с STEP 0.")
    return (
        "ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ %s — СТУПЕНЬ %s. "
        "Разобрано %s из %s. Разделов отчёта написано %s из %s (%s), "
        "отчёт %s байт, границ пройдено %s. "
        "%s"
        % (os.path.abspath(str(work)), row.get("stage"),
           row.get("resolved"), row.get("total"),
           sections["written"], sections["required"], done,
           row.get("report_bytes", 0), boundary_seq, action))


def resume(work):
    """What a freshly cleared, freshly re-invoked skill must do next."""
    work = Path(work).resolve(strict=True)
    row = read_row(work)
    if not row:
        raise ValueError("no readable checkpoint.json in %s — this is a new "
                         "investigation: start at stage triage" % work)
    stage = row.get("stage", "triage")
    return row, ("СТУПЕНЬ СЕЙЧАС: %s\nСОСТОЯНИЕ: %s (разобрано %s из %s, "
                 "границ пройдено %s)\n%s\n"
                 % (stage, row.get("state", "?"), row.get("resolved", "?"),
                    row.get("total", "?"), row.get(BOUNDARY_SEQ, 0),
                    reseed_line(work)))


def init(work):
    work = work.resolve(strict=True)
    total, resolved, seals = inspect_worklists(work)
    unresolved = total - resolved
    previous = read_row(work)
    row = {
        "schema": SCHEMA,
        # The stage is SEPARATE from the state on purpose. `state` answers "is
        # the worklist closed"; `stage` answers "which bounded session am I".
        # init must never rewind a stage: it is re-run inside every stage.
        "stage": previous.get("stage", "triage") if previous else "triage",
        # NEVER LOWERED. `init` runs inside every stage, so resetting this would
        # erase a boundary the driver has not seen yet.
        BOUNDARY_SEQ: int(previous.get(BOUNDARY_SEQ, 0) or 0) if previous else 0,
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
    parser.add_argument("command",
                        choices=("init", "handoff", "resume", "reseed-line"))
    parser.add_argument("--work", required=True)
    parser.add_argument("--done", help="the stage being closed (handoff only)")
    parser.add_argument("--partial", action="store_true",
                        help="close one BATCH of the stage: print the block and "
                             "reset the session, but do NOT advance the stage "
                             "(triage only)")
    parser.add_argument("--json", action="store_true",
                        help="print the machine receipt instead of the block")
    parser.add_argument("--gate-tool", action="append", default=[],
                        dest="gate_tools_run", metavar="NAME",
                        help="a gate tool (%s) that was run against a real "
                             "candidate before this boundary was taken — "
                             "repeatable. Recorded on the checkpoint.jsonl row "
                             "so the driver's progress gate can tell "
                             "preparatory gate-tool work from a boundary that "
                             "changed nothing. `handoff` is typed by the model "
                             "itself under approval-mode yolo, so a name "
                             "outside GATE_TOOLS is filtered out before it is "
                             "recorded (a warning is printed, the boundary "
                             "still completes) rather than being trusted: the "
                             "model must not be able to grant itself the "
                             "escape by typing any word it likes."
                             % ", ".join(GATE_TOOLS))
    args = parser.parse_args()
    if args.command == "init":
        print(json.dumps(init(Path(args.work)), ensure_ascii=False,
                         sort_keys=True))
        return
    if args.command == "handoff":
        if not args.done:
            raise SystemExit("handoff needs --done <stage>")
        # ANTI-GAMING FILTER. `handoff` is typed by the model itself under
        # approval-mode yolo, so `--gate-tool` cannot be trusted as given: an
        # unrecognized name (a typo, or a bare word chosen to fake the escape)
        # is DROPPED here, loudly, rather than recorded — a boundary whose
        # only "progress" is a name outside GATE_TOOLS is still barren. The
        # boundary itself still completes (it may be genuinely resolving
        # rows or writing a report even when a gate-tool name is wrong), so a
        # typo costs the escape credit, never the whole checkpoint.
        verified_gate_tools = [g for g in args.gate_tools_run
                              if g in GATE_TOOLS]
        for bogus in args.gate_tools_run:
            if bogus not in GATE_TOOLS:
                sys.stderr.write(
                    "⚠ --gate-tool %r is not one of %s — ignored, this "
                    "boundary does not count as gate-tool progress on that "
                    "name alone\n" % (bogus, ", ".join(GATE_TOOLS)))
        row, block = handoff(args.work, args.done, partial=args.partial,
                             gate_tools_run=verified_gate_tools)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True) if args.json
              else block, end="" if not args.json else "\n")
        return
    if args.command == "reseed-line":
        work = Path(args.work).resolve(strict=True)
        print(reseed_line(work))
        return
    row, text = resume(args.work)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True) if args.json
          else text, end="" if not args.json else "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        # A gate that cannot advance must SAY SO and exit non-zero. The v36
        # lesson: a check that prints failure and exits 0 is not a check.
        print("✗ %s" % exc)
        raise SystemExit(1)
