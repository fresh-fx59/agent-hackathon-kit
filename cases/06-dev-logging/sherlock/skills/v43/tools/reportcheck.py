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

SCOPE — STRUCTURE, AND SINCE FIX 3 THE VERDICT'S SUPPORT. The original gate
answered only «имеет ли отчёт форму, которую заказчик заказал?». That left the
worst half of the delivered defect standing: «компрометация» was not merely the
wrong WORD, it was a word the report's own body refuted two sentences later. So
`check_verdict_support` now also answers «следует ли выбранный вердикт из
находок?», reading the same `sections` model and the `support` key of the
contract's verdict section: the outcome→verdict binding, the attribution
vocabulary and the admission vocabularies are all data in the profile, and this
file stays free of any one customer's Russian prose.

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
    if not isinstance(labels, dict) or not labels.get("allowed"):
        raise ContractError("labels должен нести allowed: %s" % path)
    if labels.get("candidate") or labels.get("ignore"):
        raise ContractError(
            "labels.candidate/labels.ignore удалены в v43 — метка опознаётся "
            "по ПОЗИЦИИ, а не поиском по тексту: %s" % path)
    if not labels.get("table_column"):
        raise ContractError("labels должен нести table_column: %s" % path)
    if not isinstance(data["sections"], list) or not data["sections"]:
        raise ContractError("sections должен быть непустым списком: %s" % path)
    try:
        data["_cite_re"] = re.compile(data["citation"])
        for sec in data["sections"]:
            if not isinstance(sec, dict) or "role" not in sec or "title" not in sec:
                raise ContractError("раздел без role/title: %s" % path)
            sec["_title_re"] = re.compile(sec["title"], re.IGNORECASE)
    except re.error as exc:
        raise ContractError("плохое регулярное выражение в контракте %s: %s"
                            % (path, exc))
    for sec in data["sections"]:
        if sec.get("one_of"):
            compile_support(sec, path)
    return data


# --------------------------------------------------------------------------
# contract: the verdict-support binding (fix 3)

SUPPORT_REQUIRED = ("outcome_line", "outcome_head", "outcome_rank", "implies",
                    "attribution_line", "attribution_head",
                    "attribution_established", "stranger_verdict",
                    "admission", "defects")
SUPPORT_DEFECT_KEYS = ("unsupported", "contradicts", "stranger", "unreadable")
ADMISSION_GROUPS = ("owner", "stranger", "undetermined")
NO_OUTCOMES_POLICIES = ("skip", "refuse")


def compile_support(spec, path):
    """Validate and compile the `support` block of a verdict section spec.

    A section that declares a closed verdict vocabulary (`one_of`) MUST also
    declare how that vocabulary is earned. Without the binding the gate could
    only grade the word and never the reasoning — which is precisely how
    20260827T173511Z-v41 passed. So a missing or thin `support` is a
    ContractError, i.e. exit 2, i.e. a refusal: a gate that cannot grade the
    semantics must not report that it did.
    """
    sup = spec.get("support")
    if not isinstance(sup, dict):
        raise ContractError(
            "раздел %r несёт one_of, но не несёт support: %s — сопоставить "
            "вердикт с исходами нечем" % (spec.get("role"), path))
    for key in SUPPORT_REQUIRED:
        if not sup.get(key):
            raise ContractError("в support нет ключа %r: %s" % (key, path))
    for key in SUPPORT_DEFECT_KEYS:
        if not sup["defects"].get(key):
            raise ContractError("в support.defects нет ключа %r: %s"
                                % (key, path))
    rank = sup["outcome_rank"]
    if not isinstance(rank, list) or len(set(rank)) != len(rank):
        raise ContractError("support.outcome_rank должен быть списком без "
                            "повторов: %s" % path)
    implies = sup["implies"]
    if not isinstance(implies, dict) or set(implies) != set(rank):
        raise ContractError("support.implies должен покрывать ровно исходы из "
                            "outcome_rank: %s" % path)
    unknown = [v for v in implies.values() if v not in spec["one_of"]]
    if unknown:
        raise ContractError("support.implies указывает на вердикт вне one_of: "
                            "%s: %s" % (", ".join(unknown), path))
    if sup["stranger_verdict"] not in spec["one_of"]:
        raise ContractError("support.stranger_verdict вне one_of: %s" % path)
    down = sup.get("unattributed_strongest_counts_as")
    if down is not None:
        if down not in rank or rank.index(down) >= len(rank) - 1:
            raise ContractError(
                "support.unattributed_strongest_counts_as должен быть исходом "
                "слабее сильнейшего: %s" % path)
    policy = sup.get("when_no_outcomes", "skip")
    if policy not in NO_OUTCOMES_POLICIES:
        raise ContractError("support.when_no_outcomes должен быть одним из %s: %s"
                            % ("/".join(NO_OUTCOMES_POLICIES), path))
    sup["_no_outcomes"] = policy
    adm = sup["admission"]
    if not isinstance(adm, dict):
        raise ContractError("support.admission не объект: %s" % path)
    for grp in ADMISSION_GROUPS:
        if not adm.get(grp) or not isinstance(adm[grp], list):
            raise ContractError("в support.admission нет непустого списка %r: %s"
                                % (grp, path))
    try:
        sup["_outcome_line_re"] = re.compile(sup["outcome_line"], re.IGNORECASE)
        sup["_outcome_head_re"] = re.compile(sup["outcome_head"], re.IGNORECASE)
        sup["_attr_line_re"] = re.compile(sup["attribution_line"], re.IGNORECASE)
        sup["_attr_head_re"] = re.compile(sup["attribution_head"], re.IGNORECASE)
        sup["_sentence_re"] = re.compile(sup.get("sentence_split") or r"[.!?;\n]+")
        sup["_admission_res"] = {
            grp: [re.compile(p, re.IGNORECASE) for p in adm[grp]]
            for grp in ADMISSION_GROUPS}
    except re.error as exc:
        raise ContractError("плохое регулярное выражение в support контракта "
                            "%s: %s" % (path, exc))
    return sup


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


#: A label is a MARKER at a POSITION. Never a token found in text.
#: On 20260830T190815Z-v42 the free-text scan produced 32 false blockers from
#: corpus usernames and SQL keywords, and the model's answer was to append 15
#: tokens to an ignore list. There is no list any more, so there is nothing to
#: append to: `ADMINI` is not a label because it is not in a label position.
MARKER_RE = re.compile(r"\[!([A-Z][A-Z0-9_]*)\]")
PARA_MARKER_RE = re.compile(r"^\s*>?\s*\[!([A-Z][A-Z0-9_]*)\]\s*$")
LIST_MARKER_RE = re.compile(r"^\s*[-*+]\s+\[!([A-Z][A-Z0-9_]*)\](\s|$)")


def _table_cells(line):
    """The cells of a markdown table row, outer pipes dropped."""
    s = line.strip()
    if not s.startswith("|"):
        return None
    parts = s.split("|")
    return [c.strip() for c in parts[1:-1]] if len(parts) >= 3 else []


def marker_at(line):
    """The label word if `line` is a paragraph marker line, else None."""
    m = PARA_MARKER_RE.match(line)
    return m.group(1) if m else None


def marker_in_unit(unit, contract, header=None):
    """(word, defect) for one assertion unit.

    `defect` is None, "label_position" (a marker sits somewhere that is not a
    label position) or "label_conflict" (more than one marker in the unit).
    `header` is the table's header cells when the unit is a table row.
    """
    found = []
    misplaced = False
    for raw in unit:
        cells = _table_cells(raw)
        if cells is not None:
            col = contract["labels"]["table_column"]
            idx = header.index(col) if header and col in header else None
            for i, cell in enumerate(cells):
                m = MARKER_RE.search(cell)
                if not m:
                    continue
                if i == idx and MARKER_RE.fullmatch(cell):
                    found.append(m.group(1))
                else:
                    misplaced = True
            continue
        word = marker_at(raw)
        if word is None:
            m = LIST_MARKER_RE.match(raw)
            word = m.group(1) if m else None
        if word:
            # The line IS a label position: every marker on it counts as a
            # found label — a second one here is a conflict, not a misplacement.
            found.extend(MARKER_RE.findall(raw))
        elif MARKER_RE.search(raw):
            misplaced = True
    if len(found) > 1:
        return None, "label_conflict"
    if found:
        return found[0], None
    return None, ("label_position" if misplaced else None)


# --------------------------------------------------------------------------
# per-defect checks. Each returns a list of {defect, where, detail}.


def check_labels(sections, contract):
    """assertion_unlabelled / label_unknown / label_position / label_conflict.

    One defect per cause. A unit with a WRONG marker is label_unknown and is NOT
    also assertion_unlabelled — the label is present, it is just not permitted.
    """
    allowed = set(contract["labels"]["allowed"])
    exempt = set(contract["labels"].get("exempt_roles") or [])
    bad = []
    for sec in sections:
        if sec["role"] in exempt:
            continue
        header = None
        for raw in sec["lines"]:
            cells = _table_cells(raw)
            if cells and contract["labels"]["table_column"] in cells \
                    and _is_table_header(sec["lines"], raw):
                header = cells
                break
        for unit in blocks(sec["lines"]):
            text = "\n".join(unit)
            if TABLE_RULE_RE.match(text.strip()):
                continue
            if not citations(text, contract["_cite_re"]):
                continue
            where = sec["title"] or "(преамбула)"
            snippet = text.strip().splitlines()[0][:110]
            word, defect = marker_in_unit(unit, contract, header)
            if defect == "label_conflict":
                bad.append({"defect": "label_conflict", "where": where,
                            "detail": "%s — две метки в одном утверждении" % snippet})
                continue
            if defect == "label_position":
                bad.append({"defect": "label_position", "where": where,
                            "detail": "%s — метка не на своей позиции" % snippet})
            if word is None:
                bad.append({"defect": "assertion_unlabelled", "where": where,
                            "detail": snippet})
            elif word not in allowed:
                bad.append({"defect": "label_unknown", "where": where,
                            "detail": "%s — метка %s вне %s"
                                      % (snippet, word, "/".join(sorted(allowed)))})
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


# --------------------------------------------------------------------------
# verdict support: does the chosen word FOLLOW from the report? (fix 3)

FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _verdict_spec(contract):
    for spec in contract["sections"]:
        if spec.get("one_of") and spec.get("support"):
            return spec
    return None


def _stated_verdict(sections, spec):
    """-> the single verdict word the section states, or None.

    None means «check_one_of already blocks this report»: zero or several words.
    The support checks stay silent there instead of piling a second, derived
    complaint on top of an unreadable verdict.
    """
    for sec in sections:
        if sec["role"] != spec["role"]:
            continue
        low = sec["body"].lower()
        hit = [v for v in spec["one_of"] if v.lower() in low]
        if len(hit) == 1:
            return hit[0], sec
        return None, sec
    return None, None


def read_outcomes(sections, spec):
    """-> (blocks, unreadable) over every section that is not the verdict.

    blocks: [{where, outcome, attribution, attribution_bad}] — one per section
    that carries a well-formed `исход:` line.
    unreadable: [(where, detail)] — an `исход:` head that is not a legal line,
    or two legal lines in one block. Both mean the report is not machine
    readable at the field the verdict is graded against, and that is a defect,
    never a pass: see the class comment in citecheck's OUTCOME_ORDER.
    """
    sup = spec["support"]
    out, unreadable = [], []
    for sec in sections:
        if sec["role"] == spec["role"]:
            continue
        where = sec["title"] or "(преамбула)"
        valid, invalid, attrs, attr_bad, inside = [], [], [], [], False
        for raw in sec["lines"]:
            if FENCE_RE.match(raw):
                inside = not inside
                continue
            if inside:
                continue
            m = sup["_outcome_line_re"].match(raw)
            if m:
                valid.append(_norm(m.group(1)))
            elif sup["_outcome_head_re"].match(raw):
                invalid.append(raw.strip())
            a = sup["_attr_line_re"].match(raw)
            if a:
                attrs.append(_norm(a.group(1)))
            elif sup["_attr_head_re"].match(raw):
                attr_bad.append(raw.strip())
        if invalid:
            unreadable.append((where, "строка исхода не по образцу: %s"
                                      % invalid[0][:110]))
            continue
        if len(valid) > 1:
            unreadable.append((where, "несколько строк исхода: %s"
                                      % ", ".join(valid)))
            continue
        if not valid:
            continue
        if valid[0] not in sup["implies"]:
            unreadable.append((where, "исход вне словаря: %s" % valid[0]))
            continue
        out.append({
            "where": where,
            "outcome": valid[0],
            "attribution": attrs[0] if len(attrs) == 1 else None,
            "attribution_bad": (attr_bad[0] if attr_bad
                                else ("несколько строк атрибуции"
                                      if len(attrs) > 1 else None)),
        })
    return out, unreadable


def admissions(text, spec):
    """-> list of sentences in which the report itself says «actor unknown».

    HOW THIS AVOIDS BEING A ONE-SENTENCE GATE. The naive version of this check
    greps for the sentence that burned us — «кто именно действовал под учёткой
    root (владелец или атакующий) — по корпусу не определяется» — and passes
    every rewording of the same admission. That is theatre: the gate would be
    green on the second report and the operator would find the same defect by
    hand again.

    So the detector is a CONJUNCTION OF THREE VOCABULARIES inside one sentence,
    all three declared in the profile: a word for the legitimate owner, a word
    for an outsider, and a word for «not determined». An admission that the
    actor behind the access is undetermined has to carry all three meanings —
    it names the two candidates and says they cannot be separated — no matter
    which words it picks. «по журналам нельзя различить, работал ли хозяин или
    посторонний» trips it exactly like the delivered sentence does, and neither
    wording appears anywhere in this file or in the profile.
    """
    sup = spec["support"]
    res = sup["_admission_res"]
    found = []
    for sentence in sup["_sentence_re"].split(text):
        s = sentence.strip()
        if not s:
            continue
        if all(any(r.search(s) for r in res[grp]) for grp in ADMISSION_GROUPS):
            found.append(_norm(s))
    return found


def _effective(block, sup, rank):
    """The outcome a block CONTRIBUTES to the verdict, which is not always the
    outcome it states.

    The strongest outcome earns the strongest verdict only when the report can
    say WHO. «Скомпрометирована» is a claim about a stranger, so an
    `исход: успех` the report cannot attribute («атрибуция: не установлена»)
    supports one step less — `support.unattributed_strongest_counts_as`. That is
    exactly the delivered report's case: two successes, both unattributed, and
    the honest answer «атаковали, но не доказано». Profiles that do not declare
    the key keep the plain mapping.
    """
    down = sup.get("unattributed_strongest_counts_as")
    if not down or block["outcome"] != rank[-1]:
        return block["outcome"]
    if block["attribution"] == _norm(sup["attribution_established"]):
        return block["outcome"]
    return down


def check_verdict_support(sections, contract):
    """The chosen verdict must FOLLOW from the findings, not merely be legal.

    Three defects, each with its own name, all bound by the profile:

      * `verdict_unsupported_by_outcomes` — the strongest `исход:` among the
        findings maps, under `support.implies`, to a different verdict than the
        one stated. Strongest is decided by `support.outcome_rank`.
      * `verdict_contradicts_report` — the stranger verdict is stated while the
        report itself admits the actor is undetermined (see `admissions`).
      * `verdict_success_not_attributed_to_stranger` — the stranger verdict
        rests on an `исход: успех` block whose `атрибуция:` is not
        «установлена». «Скомпрометирована» means proof that an OUTSIDER got
        access; a successful login the report cannot pin on anyone is not that
        proof, and the account's own owner logging in is not a compromise.

    Fail-closed: an `исход:` head that is not a legal line, two of them in one
    block, or a value outside the vocabulary is `verdict_outcomes_unreadable`,
    not a pass. A report with NO outcome lines at all is handled by
    `support.when_no_outcomes`; the corporate profile sets `skip`, because
    findings with no `исход:` line are citecheck's ledger condition
    («находок без строки «исход»») and stopcheck runs that gate on the same
    report — two gates blocking the same defect would only double the noise.
    """
    spec = _verdict_spec(contract)
    if spec is None:
        return []
    sup = spec["support"]
    names = sup["defects"]
    stated, vsec = _stated_verdict(sections, spec)
    if vsec is None:
        return []  # verdict_section_absent already fires
    where = vsec["title"] or "ВЕРДИКТ"
    bad = []
    blocks_, unreadable = read_outcomes(sections, spec)
    for w, detail in unreadable:
        bad.append({"defect": names["unreadable"], "where": w, "detail": detail})
    if not blocks_ and not unreadable and sup["_no_outcomes"] == "refuse":
        bad.append({"defect": names["unreadable"], "where": where,
                    "detail": "в отчёте нет ни одной строки «исход:» — "
                              "сопоставлять вердикт не с чем"})
    if stated is None:
        return bad
    rank = sup["outcome_rank"]

    if blocks_ and not unreadable:
        top = max(blocks_, key=lambda b: rank.index(_effective(b, sup, rank)))
        eff = _effective(top, sup, rank)
        implied = sup["implies"][eff]
        if _norm(implied) != _norm(stated):
            note = ("" if eff == top["outcome"]
                    else " (исход «%s» без установленной атрибуции считается "
                         "как «%s»)" % (top["outcome"], eff))
            bad.append({"defect": names["unsupported"], "where": where,
                        "detail": "сильнейший исход «%s» (%s) требует «%s», "
                                  "а сказано «%s»%s"
                                  % (eff, top["where"], implied, stated, note)})

    if _norm(stated) == _norm(sup["stranger_verdict"]):
        text = "\n".join(s["body"] for s in sections)
        for sentence in admissions(text, spec)[:3]:
            bad.append({"defect": names["contradicts"], "where": where,
                        "detail": "отчёт сам признаёт: «%s»" % sentence[:160]})
        established = _norm(sup["attribution_established"])
        strongest = rank[-1]
        for b in blocks_:
            if b["outcome"] != strongest:
                continue
            if b["attribution_bad"]:
                bad.append({"defect": names["stranger"], "where": b["where"],
                            "detail": "исход «%s», атрибуция не по образцу: %s"
                                      % (strongest, b["attribution_bad"][:110])})
            elif b["attribution"] is None:
                bad.append({"defect": names["stranger"], "where": b["where"],
                            "detail": "исход «%s» без строки «атрибуция:» — "
                                      "постороннего никто не назвал" % strongest})
            elif b["attribution"] != established:
                bad.append({"defect": names["stranger"], "where": b["where"],
                            "detail": "исход «%s», атрибуция «%s» — «%s» "
                                      "требует доказанного постороннего"
                                      % (strongest, b["attribution"],
                                         sup["stranger_verdict"])})
    return bad


# The registry fix 3 plugs into: one more entry, same signature, same model.
CHECKS = (
    check_section_presence,
    check_section_order,
    check_section_citations,
    check_section_entries,
    check_one_of,
    check_verdict_support,
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
