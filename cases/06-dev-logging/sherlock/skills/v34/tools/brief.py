#!/usr/bin/env python3
"""brief — write the per-phase instruction files a subagent reads from disk.

    python3 brief.py --work ./work --corpus ./logs --skill-root <BASE> --phase all
    python3 brief.py --install-agents <PROJECT>/.qwen/agents

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

VERSION = 34

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


# ---------------------------------------------------------------------------
# Named agent definitions.
#
# A skill directory cannot ship these: qwen-code only discovers definitions in
# `<project>/.qwen/agents/*.md`, `~/.qwen/agents/*.md` or an extension's
# `agents/*.md` — a flat directory, `.md` only, no recursion. So the skill
# installs them at run time, and `loadSubagent()` reads from disk on every call,
# which means a definition written during a session resolves on the FIRST call
# by name; only the advertised roster is stale.
#
# The point of the exercise: an agent definition's Markdown BODY becomes the
# CHILD's system prompt (`parseSubagentContent` -> `promptConfig.systemPrompt`).
# The PARENT only ever carries `- **name**: description` per agent
# (`updateDescriptionAndSchema`). So phase prose moved into a definition body
# costs the parent tens of bytes instead of tens of kilobytes.
#
# The child does NOT inherit qwen-code's core system prompt, so each body has to
# be self-sufficient. `tools:` is deliberately ABSENT from the frontmatter: an
# explicit allowlist would silently drop `skill` and `write_file`.
#
# Bodies are ENGLISH because they are instructions. The artefacts stay RUSSIAN,
# and every literal the python gates parse is reproduced verbatim below.
# ---------------------------------------------------------------------------

AGENTS = ("sherlock-triage", "sherlock-draft")

RU_CONTRACT = u"""## Output language (never translate the literals below)

Your own reasoning may be English, but THE REPORT AND EVERY ARTEFACT FIELD MUST
BE WRITTEN IN RUSSIAN. The python gates match these strings byte for byte:
`## \u041d\u0430\u0445\u043e\u0434\u043a\u0438`, `## \u041e\u0442\u043a\u043b\u043e\u043d\u0451\u043d\u043d\u044b\u0435 \u043a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u044b`, `## \u041f\u043e\u043a\u0440\u044b\u0442\u0438\u0435`, `\u0443\u043b\u0438\u043a\u0438:`,
`\u0447\u0435\u043c \u043e\u043f\u0440\u043e\u0432\u0435\u0440\u0433\u0430\u043b:`, `\u0430\u0442\u0440\u0438\u0431\u0443\u0446\u0438\u044f:`, `\u0438\u0441\u0445\u043e\u0434:` with exactly one of
`\u0443\u0441\u043f\u0435\u0445|\u043f\u043e\u043f\u044b\u0442\u043a\u0430|\u043d\u043e\u0440\u043c\u0430`. Worklist verdict letters stay `D`, `N`, `X`.
Translating or re-spelling any of them fails the gate."""

TRIAGE_DESC = (
    "Triages a pre-built log worklist for the Sherlock log-RCA skill: turns "
    "every unresolved row into a D (defect), N (normal) or X (not enough data) "
    "verdict, each backed by a path:line verbatim quote or a numbered bulk "
    "rule, writes the verdicts back to the worklist, and proves the result "
    "with triagecheck.py until it exits 0. Use it for the TRIAGE phase, after "
    "logmap.py has produced the worklist and brief.py has produced "
    "work/brief-triage.md, whenever the parent must not read the log corpus "
    "itself. Prompt it with ONE line: the absolute path of its brief file. It "
    "returns a short fixed summary of counts and gate exit codes; its artefact "
    "fields stay in Russian. Do not use it to write the final report."
)

TRIAGE_BODY = u"""You are a log triage specialist. Row by row, you decide whether a suspicious
group of log records is a real defect, ordinary behaviour, or something the
available data cannot settle. You are precise, you never guess a path, and you
never let a verdict stand without evidence attached to it.

Your prompt names ONE file: the absolute path of your brief. Read it before
anything else. That brief carries every absolute path you need - the log corpus,
the worklist, the rules file, the corpus map, the skill's tools directory and
the full skill instruction. Never invent, shorten or guess a path that is not in
the brief, and never assume a working directory. If the brief cannot be read,
say exactly that and stop.

## Procedure

1. Read the brief. Then read the section of the full skill instruction it points
   you at, completely, before you set a single verdict.
2. Read the corpus map and the worklist. Every worklist row starts unresolved.
3. For each row, replace the placeholder with one letter and WRITE THE FILE
   BACK: `D` for a defect, `N` for normal, `X` for not enough data.
4. No verdict travels alone. Each carries either a `path:line` reference with a
   verbatim quote, or the number of a bulk rule from the rules file. A bulk rule
   needs a claim and its receipts; without them the row stays unresolved.
5. Rows on the strong axes named in the brief cannot be closed by a bulk rule.
   Close those one at a time, by name.
6. Run the triagecheck command exactly as the brief spells it, and keep fixing
   rows until it exits 0. A non-zero gate is never a finished result.
7. Answer with the lines the brief demands, and nothing else.

## Notes

* Read as much of the corpus as you need. Your context is your own; only your
  final message leaves this subagent, so keep that message under 20 lines.
* Quote, never paraphrase. A citation that does not match the file byte for byte
  fails the gate.
* Use the scripts in the brief's tools directory. Do not write your own log
  parser, do not install anything, do not reach the network.
* A missing log line is not proof that nothing happened. That is an `X`.
* Do not spawn further subagents, and do not start the report. DRAFT is a
  separate phase with its own worker and its own brief.

""" + RU_CONTRACT + u"""

## Report

Reply with exactly the lines your brief specifies, in its order, in Russian: no
preamble, no narration of your process, no apology. If a gate is still non-zero,
report that number on its line rather than hiding it."""

DRAFT_DESC = (
    "Writes the final Russian root-cause report for the Sherlock log-RCA "
    "skill, working only from artefacts already on disk: an already-triaged "
    "worklist, the rules file, the corpus map and the checkpoint. It then "
    "proves the report with citecheck.py, triagecheck.py and statecheck.py "
    "until every one of the three exits 0. Use it for the DRAFT phase, after "
    "TRIAGE has left verdicts in the worklist and brief.py has produced "
    "work/brief-draft.md. Prompt it with ONE line: the absolute path of its "
    "brief file. It reads only the log lines it cites, writes the report file "
    "named in the brief, and returns a short fixed summary with each gate's "
    "exit code. Never use it to investigate from scratch or to set verdicts."
)

DRAFT_BODY = u"""You are a root-cause report writer. You do not re-run an investigation: the
verdicts, the rules, the corpus map and the checkpoint are already on disk, and
your job is to turn them into one report that survives three mechanical gates.
You are exact with citations and you never soften a gate's exit code.

Your prompt names ONE file: the absolute path of your brief. Read it before
anything else. That brief carries every absolute path you need - the log corpus,
the triaged worklist, the rules file, the corpus map, the checkpoint, the report
format, the output report path and the skill's tools directory. Never invent,
shorten or guess a path that is not in the brief, and never assume a working
directory. If the brief cannot be read, say exactly that and stop.

## Procedure

1. Read the brief. Then read the report-format file it names, as your first real
   action, and follow its structure literally.
2. Read the triaged worklist, the rules file, the corpus map and the checkpoint.
   Do not open the corpus wholesale: read only the lines you are going to cite.
3. Write the complete report to the path the brief gives you. Every finding is
   its own block with the four mandatory field lines. The rejected-candidates
   section and the coverage section are mandatory and must not be empty.
4. A coverage row that carries an observation must quote the line the corpus map
   flagged for that file. An arbitrary line from the file does not count.
5. Run all three gate commands exactly as the brief spells them - citecheck,
   triagecheck, statecheck - and keep repairing the report until each exits 0.
   statecheck reads from the corpus toward the report: every group it names must
   be answered, as a finding or as an explicit normal with a quote.
6. Do not invent a timeline. Take correlations from the logjoin command in the
   brief and cite its output.
7. Answer with the lines the brief demands, and nothing else.

## Notes

* Quote, never paraphrase; a citation that does not match the file byte for byte
  fails citecheck.
* Claim only what a quoted line supports. Uncertainty belongs in the
  refutation field, never in a hedged verdict word.
* Use the scripts in the brief's tools directory. Do not install anything and do
  not reach the network.
* Do not spawn further subagents. Do not paste the report into your reply.

""" + RU_CONTRACT + u"""

## Report

Reply with exactly the lines your brief specifies, in its order, in Russian: no
preamble, no narration, and never the report body itself. If a gate is still
non-zero, report that number on its line rather than hiding it."""

DEFS = {
    "sherlock-triage": (TRIAGE_DESC, TRIAGE_BODY),
    "sherlock-draft": (DRAFT_DESC, DRAFT_BODY),
}


def agent_text(name):
    """The exact bytes of one definition file. Deterministic: same input, same
    output, so a rerun is a no-op rather than a diff."""
    desc, body = DEFS[name]
    return (
        "---\n"
        'name: %s\n'
        'description: "%s"\n'
        "approvalMode: yolo\n"
        "maxTurns: 30\n"
        "---\n"
        "\n"
        "%s\n" % (name, desc, body)
    )


def install_agents(dest, force=False, out=sys.stdout):
    """Write both definitions into `dest` (the project's .qwen/agents).

    Atomic (tmp + os.replace), idempotent (byte-identical rerun is a no-op) and
    non-destructive: a file whose content differs is left alone unless --force,
    and then what was overwritten is printed.
    """
    dest = os.path.abspath(dest)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as exc:
        sys.stderr.write("brief: \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u043a\u0430\u0442\u0430\u043b\u043e\u0433 %s: %s\n" % (dest, exc))
        return 2
    conflicts = []
    for name in AGENTS:
        path = os.path.join(dest, name + ".md")
        text = agent_text(name)
        old = None
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    old = fh.read()
            except OSError:
                old = None
            if old == text:
                out.write("%s (\u0431\u0435\u0437 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439)\n" % path)
                continue
            if not force:
                conflicts.append(path)
                sys.stderr.write(
                    "brief: %s \u0438\u0437\u043c\u0435\u043d\u0451\u043d \u0432\u0440\u0443\u0447\u043d\u0443\u044e \u2014 \u043d\u0435 \u0442\u0440\u043e\u0433\u0430\u044e; \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c: --force\n" % path)
                continue
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        if old is None:
            out.write("%s (%d \u0431\u0430\u0439\u0442)\n" % (path, len(text.encode("utf-8"))))
        else:
            out.write("%s (%d \u0431\u0430\u0439\u0442, \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0438\u0441\u0430\u043d \u043f\u043e\u0432\u0435\u0440\u0445 %d \u0431\u0430\u0439\u0442)\n"
                      % (path, len(text.encode("utf-8")), len(old.encode("utf-8"))))
    return 3 if conflicts else 0


def main():
    ap = argparse.ArgumentParser(
        description="написать файлы-задания для фазовых субагентов")
    ap.add_argument("--work", help="каталог состояния (./work)")
    ap.add_argument("--corpus", help="корень корпуса логов")
    ap.add_argument("--skill-root",
                    help="базовый каталог навыка (его выдают первой строкой)")
    ap.add_argument("--phase", default="all", choices=("all",) + PHASES)
    ap.add_argument("--install-agents", metavar="DIR",
                    help="записать определения субагентов в <DIR> "
                         "(это <проект>/.qwen/agents) и выйти")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать определение, изменённое вручную")
    a = ap.parse_args()

    if a.install_agents:
        return install_agents(a.install_agents, force=a.force)

    missing = [n for n in ("work", "corpus", "skill_root")
               if not getattr(a, n)]
    if missing:
        sys.stderr.write("brief: нужны аргументы: %s\n"
                         % ", ".join("--" + m.replace("_", "-") for m in missing))
        return 2
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
