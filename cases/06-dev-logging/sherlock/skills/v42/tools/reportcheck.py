#!/usr/bin/env python3
"""reportcheck — the OPERATOR'S report contract, enforced as a gate.

    python3 reportcheck.py work/report.md
    python3 reportcheck.py work/report.md --contract <SKILL_BASE_DIR>/reference/report-contract.corporate.json
    python3 reportcheck.py work/report.md --json

Why this exists. On 2026-08-27 the paid corporate run `20260827T173511Z-v41`
finished green: its stored `citecheck`, `statecheck` and `triagecheck` all
exited zero, and the project called it accepted. The independent review on
2026-08-28 found the acceptance was a FALSE POSITIVE. Every gate in the kit
grades the SKILL'S OWN internal format — `### Н-n` blocks, `улики:` lines,
`атрибуция:`, the coverage table, the record window. Not one of them had ever
read the CUSTOMER'S requirements, which do not live in the skill at all: they
arrive in the prompt.

Measured against that prompt, the delivered report:

  * carried NO `PROVEN` / `REPORTED` / `INFERENCE` label on any assertion,
    though the operator demanded one on every single one;
  * had NO inventory of the addresses, names, paths and hashes it met, with the
    origin of each — the section simply did not exist;
  * had NO separate section for what the logs LACK, though «не хватает данных»
    was named as a full answer;
  * put `ВЕРДИКТ` FIRST, where the operator asked for it as the last section;
  * stated `компрометация` while admitting elsewhere that owner-versus-attacker
    was undetermined.

Five explicit, written, checkable requirements. Zero of them checked. The gates
said «сдано». That gap is what this tool closes: it takes the report and the
operator's contract, and blocks on each structural violation separately, under
its own name, with a count — the same idiom as `statecheck`'s «не отвечено».

WHY THE CONTRACT IS DATA AND NOT PROSE IN THIS FILE. The skill's own format is
a constant: `Н-n` blocks are the skill, and hardcoding them is right. The
operator's requirements are NOT a constant — they are one customer's order,
written in that customer's prompt, and the next customer will ask for different
labels, a different verdict vocabulary, different mandatory sections. If those
requirements lived as literals inside this checker, serving a second customer
would mean editing the gate, and every edit would put the first customer's paid,
sealed run at risk of silently changing meaning. So the requirements are a
declarative PROFILE on disk (`reference/report-contract.corporate.json`), and
this file is only the engine that reads one. A new customer gets a new profile,
never a new gate. `--contract` selects it; the corporate profile is the default
because that is the contract this kit is sold against today.

SCOPE — STRUCTURE ONLY. This gate answers «имеет ли отчёт форму, которую
заказчик заказал?». It deliberately does NOT answer «следует ли выбранный
вердикт из находок?» — that is a semantic question about the body of the report,
it needs the findings and their outcomes, and it lands as a separate gate step.
The seam is prepared here on purpose: `check()` builds the parsed section model
and dispatches over a registry of per-defect functions, so the verdict-support
check plugs in as one more entry beside `verdict_not_one_of_three`, reading the
same `sections` model and the same `contract["sections"]` verdict entry (add a
`"support"` key there). Nothing else has to move.

FAIL-CLOSED. A report that cannot be read, and a contract that cannot be parsed
or does not carry the keys the engine needs, are REFUSALS (exit 2), never
passes. A gate that cannot grade has not graded; the whole point of this file is
that «exit 0» stopped meaning «checked» once, and must never mean it again.

No LLM, no network, stdlib only. Exit 0 when no blocking defect is found, 1 when
any is, 2 on usage error or a fail-closed refusal.
"""
import argparse
import json
import os
import re
import sys

VERSION = 42

DEFAULT_CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reference", "report-contract.corporate.json")

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
# A markdown table row that is only dashes/colons/pipes — a separator, not data.
TABLE_RULE_RE = re.compile(r"^\s*\|[\s:|-]+\|?\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


class ContractError(Exception):
    """The profile cannot be used to grade. Fail closed, never pass."""


# --------------------------------------------------------------------------
# contract


def load_contract(path):
    if not os.path.isfile(path):
        raise ContractError("нет файла контракта: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ContractError("контракт не разбирается: %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ContractError("контракт не объект: %s" % path)
    for key in ("citation", "labels", "sections"):
        if key not in data:
            raise ContractError("в контракте нет ключа %r: %s" % (key, path))
    labels = data["labels"]
    if not isinstance(labels, dict) or not labels.get("allowed") \
            or not labels.get("candidate"):
        raise ContractError("labels должен нести allowed и candidate: %s" % path)
    if not isinstance(data["sections"], list) or not data["sections"]:
        raise ContractError("sections должен быть непустым списком: %s" % path)
    try:
        data["_cite_re"] = re.compile(data["citation"])
        data["_label_re"] = re.compile(labels["candidate"])
        for sec in data["sections"]:
            if not isinstance(sec, dict) or "role" not in sec or "title" not in sec:
                raise ContractError("раздел без role/title: %s" % path)
            sec["_title_re"] = re.compile(sec["title"], re.IGNORECASE)
    except re.error as exc:
        raise ContractError("плохое регулярное выражение в контракте %s: %s"
                            % (path, exc))
    return data


# --------------------------------------------------------------------------
# parsing


def citations(text, cite_re):
    """-> list of (path, line) that are real `файл:строка`, not clock times."""
    out = []
    for path, num in cite_re.findall(text):
        base = os.path.basename(path)
        if not base or base.isdigit():
            continue
        out.append((path, int(num)))
    return out


def split_sections(text, contract):
    """-> ordered list of sections: {title, level, lines, role, index}.

    Everything before the first heading is the preamble; it is a section with an
    empty title so an assertion written there is still graded.
    """
    sections = []
    cur = {"title": "", "level": 0, "lines": []}
    for raw in text.splitlines():
        m = HEADING_RE.match(raw)
        if m:
            sections.append(cur)
            cur = {"title": m.group(2), "level": len(m.group(1)), "lines": []}
        else:
            cur["lines"].append(raw)
    sections.append(cur)
    # Drop a leading preamble that holds nothing — a report that opens with its
    # title heading must not be told its verdict section is "not last".
    if sections and not sections[0]["title"] \
            and not any(l.strip() for l in sections[0]["lines"]):
        sections = sections[1:]
    for i, sec in enumerate(sections):
        sec["index"] = i
        sec["role"] = None
        sec["body"] = "\n".join(sec["lines"])
    for spec in contract["sections"]:
        for sec in sections:
            if sec["role"] is None and spec["_title_re"].search(sec["title"]):
                sec["role"] = spec["role"]
                break
    return sections


def blocks(lines):
    """Split a section body into ASSERTION UNITS.

    A unit is a contiguous run of non-blank lines, except that a table row and a
    list item each start their own unit. That is the unit a human labels: one
    bullet, one row, one paragraph. Grading a whole table as one unit would let a
    single `PROVEN` at the top discharge twenty unlabelled rows.
    """
    out, cur = [], []
    for raw in lines:
        s = raw.strip()
        if not s:
            if cur:
                out.append(cur)
                cur = []
            continue
        if (s.startswith("|") or BULLET_RE.match(raw)) and cur:
            out.append(cur)
            cur = []
        cur.append(raw)
        if s.startswith("|"):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def labels_in(text, contract):
    ignore = set(contract["labels"].get("ignore") or [])
    found = []
    for m in contract["_label_re"].finditer(text):
        tok = m.group(1)
        if tok in ignore:
            continue
        found.append(tok)
    return found


# --------------------------------------------------------------------------
# per-defect checks. Each returns a list of {defect, where, detail}.


def check_labels(sections, contract):
    """assertion_unlabelled + label_unknown, over every cited assertion."""
    allowed = set(contract["labels"]["allowed"])
    exempt = set(contract["labels"].get("exempt_roles") or [])
    bad = []
    for sec in sections:
        if sec["role"] in exempt:
            continue
        for unit in blocks(sec["lines"]):
            text = "\n".join(unit)
            if TABLE_RULE_RE.match(text.strip()):
                continue
            if not citations(text, contract["_cite_re"]):
                continue
            found = labels_in(text, contract)
            known = [t for t in found if t in allowed]
            unknown = [t for t in found if t not in allowed]
            where = sec["title"] or "(преамбула)"
            snippet = text.strip().splitlines()[0][:110]
            for tok in unknown:
                bad.append({"defect": "label_unknown", "where": where,
                            "detail": "%s — метка %s вне %s"
                                      % (snippet, tok, "/".join(sorted(allowed)))})
            if not known:
                bad.append({"defect": "assertion_unlabelled", "where": where,
                            "detail": snippet})
    return bad


def check_section_presence(sections, contract):
    """Every non-optional role in the contract must exist."""
    have = {s["role"] for s in sections if s["role"]}
    bad = []
    for spec in contract["sections"]:
        if spec.get("optional") or spec["role"] in have:
            continue
        name = spec.get("absent_defect")
        if not name:
            continue
        bad.append({"defect": name, "where": "(отчёт)",
                    "detail": "нет раздела по образцу %r" % spec["title"]})
    return bad


def check_section_order(sections, contract):
    """must_be_last: the section has to be the report's final one."""
    bad = []
    if not sections:
        return bad
    last = sections[-1]["index"]
    for spec in contract["sections"]:
        if not spec.get("must_be_last"):
            continue
        for sec in sections:
            if sec["role"] != spec["role"]:
                continue
            if sec["index"] != last:
                bad.append({"defect": spec.get("not_last_defect", "section_not_last"),
                            "where": sec["title"],
                            "detail": "раздел %d из %d, последний — «%s»"
                                      % (sec["index"] + 1, last + 1,
                                         sections[-1]["title"] or "(без заголовка)")})
    return bad


def check_section_citations(sections, contract):
    """require_citation: the section body must carry at least one файл:строка."""
    bad = []
    for spec in contract["sections"]:
        if not spec.get("require_citation"):
            continue
        for sec in sections:
            if sec["role"] != spec["role"]:
                continue
            if not citations(sec["body"], contract["_cite_re"]):
                bad.append({"defect": spec.get("uncited_defect", "section_uncited"),
                            "where": sec["title"],
                            "detail": "в разделе нет ни одной ссылки файл:строка"})
    return bad


def check_section_entries(sections, contract):
    """entries_require_citation: every entry line needs its origin."""
    bad = []
    for spec in contract["sections"]:
        if not spec.get("entries_require_citation"):
            continue
        for sec in sections:
            if sec["role"] != spec["role"]:
                continue
            for raw in sec["lines"]:
                s = raw.strip()
                if not s or TABLE_RULE_RE.match(s):
                    continue
                is_entry = s.startswith("|") or bool(BULLET_RE.match(raw))
                if not is_entry:
                    continue
                if s.startswith("|") and _is_table_header(sec["lines"], raw):
                    continue
                if citations(s, contract["_cite_re"]):
                    continue
                bad.append({"defect": spec.get("unsourced_defect",
                                               "section_entry_unsourced"),
                            "where": sec["title"], "detail": s[:110]})
    return bad


def _is_table_header(lines, row):
    """A `|`-row immediately followed by a `|---|` rule is the header row."""
    try:
        i = lines.index(row)
    except ValueError:
        return False
    return i + 1 < len(lines) and bool(TABLE_RULE_RE.match(lines[i + 1].strip()))


def check_one_of(sections, contract):
    """one_of: the section states EXACTLY one of the allowed verdicts."""
    bad = []
    for spec in contract["sections"]:
        allowed = spec.get("one_of")
        if not allowed:
            continue
        for sec in sections:
            if sec["role"] != spec["role"]:
                continue
            low = sec["body"].lower()
            hit = [v for v in allowed if v.lower() in low]
            if len(hit) == 1:
                continue
            bad.append({"defect": spec.get("one_of_defect", "section_not_one_of"),
                        "where": sec["title"],
                        "detail": ("не найдено ни одного из: %s"
                                   % " | ".join(allowed)) if not hit
                                  else ("найдено сразу несколько: %s"
                                        % " | ".join(hit))})
    return bad


# The registry fix 3 plugs into: one more entry, same signature, same model.
CHECKS = (
    check_section_presence,
    check_section_order,
    check_section_citations,
    check_section_entries,
    check_one_of,
    check_labels,
)


def check(text, contract):
    """-> list of defect dicts, in contract order then report order."""
    sections = split_sections(text, contract)
    bad = []
    for fn in CHECKS:
        bad.extend(fn(sections, contract))
    return bad


# --------------------------------------------------------------------------


def counts(bad):
    out = {}
    for d in bad:
        out[d["defect"]] = out.get(d["defect"], 0) + 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="проверка отчёта на контракт заказчика (структура)")
    ap.add_argument("report", help="файл отчёта; «-» — stdin")
    ap.add_argument("--contract", default=DEFAULT_CONTRACT,
                    help="профиль требований заказчика (JSON); "
                         "по умолчанию reference/report-contract.corporate.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        contract = load_contract(a.contract)
    except ContractError as exc:
        sys.stderr.write("reportcheck: %s\n" % exc)
        sys.stderr.write("reportcheck: без контракта проверять нечем — это отказ, "
                         "а не сдача гейта.\n")
        return 2

    if a.report == "-":
        text = sys.stdin.read()
    else:
        if not os.path.isfile(a.report):
            sys.stderr.write("reportcheck: нет отчёта: %s\n" % a.report)
            return 2
        try:
            with open(a.report, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("reportcheck: отчёт не читается: %s: %s\n"
                             % (a.report, exc))
            return 2
    if not text.strip():
        sys.stderr.write("reportcheck: отчёт пуст: %s. Пустой файл — это отказ.\n"
                         % a.report)
        return 2

    bad = check(text, contract)
    names = contract.get("defects") or {}
    tally = counts(bad)

    if a.json:
        json.dump({"version": VERSION, "contract": contract.get("name"),
                   "blocking": len(bad), "counts": tally, "defects": bad},
                  sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    else:
        sys.stdout.write("контракт: %s\n" % contract.get("name", a.contract))
        for d in bad:
            sys.stdout.write("%s: %s — %s\n"
                             % (d["defect"].upper(), d["where"], d["detail"]))
        for name in sorted(tally):
            sys.stdout.write("%s: %d  (%s)\n"
                             % (name, tally[name], names.get(name, "")))
        sys.stdout.write("блокирующих дефектов: %d\n" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
