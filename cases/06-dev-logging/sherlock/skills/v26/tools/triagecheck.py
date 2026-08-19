#!/usr/bin/env python3
"""triagecheck.py — из чего собран разбор рабочего списка, в числах.

    python3 triagecheck.py --worklist work/worklist-<хост>.tsv \
                           --rules work/rules.tsv --corpus <КАТАЛОГ_ЛОГОВ>

`citecheck --ledger` отвечает на вопрос «все ли строки закрыты». Этот отвечает
на другой: **чем** они закрыты. Каждая закрытая строка попадает ровно в одну из
трёх корзин:

    поимённо     — у вердикта есть своя ссылка `путь:N` с цитатой, либо это
                   дефект со ссылкой на блок находки (его улики проверяет отчёт);
    по правилу   — вердикт ссылается на правило `#R<k>` из `rules.tsv`;
    без опоры    — ни того, ни другого.

Третья корзина — весь смысл инструмента. Вердикт вида «N фон: n=1» проходит
леджер (буква есть, цифра есть) и не несёт ни одной проверяемой опоры. Если так
закрыто подавляющее большинство списка, разбор был не разбором, а одной
классификацией по признаку, который никто не записал.

ЧТО ТАКОЕ ПРАВИЛО
-----------------
Правило — это условие над колонками, которые **посчитал шаг 1**, и ничего
больше. Язык условий закрыт: `ось`, `хост`, `путь`, `файл`, `n`, `всплеск`,
`id`. Оператора над текстом записи в нём нет и не будет.

Так сделано намеренно. Массовое закрытие по списку слов, придуманному на ходу и
приложенному к колонке «запись» (то есть к проекции, а не к файлу), выглядит как
измерение и им не является: слово, которого в списке нет, получает ноль
совпадений и штамп «фон», а файл при этом никто не открывал. Такое условие в
`rules.tsv` просто не выражается — инструмент не умеет его вычислить, а правило,
которое инструмент не может вычислить, не правило.

КВИТАНЦИИ
---------
Правило, закрывшее N строк, обязано принести квитанции: k строк из его
собственного покрытия, у каждой — ссылка `путь:N` и дословная цитата, которую
проверяет `citecheck`. **Какие именно строки — выбирает инструмент**, иначе
проверялись бы ровно те, что и так были прочитаны.

    k = min(N, max(3, ceil(sqrt(N)), F))

где F — сколько РАЗНЫХ файлов правило закрывает по осям-экскурсиям
(`rare`, `new`, `peak`). Три свойства этой формулы:

  * она сублинейна: требовать по цитате на строку значит не починить провал, а
    перенести его — списка на две с половиной тысячи строк так не разобрать;
  * разбить широкое правило на m узких **никогда не дешевле**: m·√(N/m) = √(mN);
  * каждый файл, из которого правило выбрасывает экскурсию, получает хотя бы
    одну реально прочитанную и процитированную строку.

Оси `rare`/`new`/`peak` шаг 1 считает построчно — это и есть утверждение «эта
запись не похожа на соседей». Поэтому выборка берёт сначала их: иначе широкое
правило проверялось бы почти целиком на каталожных строках, которые фоном и
являются.

Откуда все три числа и что они дали на реальном разборе — `skills/DESIGN-EVIDENCE.md`,
раздел «v22». В самом навыке измерений нет намеренно: они называют корпуса.

ФОРМАТ `rules.tsv`
------------------
Строка правила   :  R1<TAB>ось=cat && n<=3<TAB>N<TAB>утверждение<TAB>основание словами
Строка квитанции :  +R1<TAB><id строки><TAB>путь:N<TAB>«дословная цитата»<TAB>правило|кандидат

`#` — комментарий. Вердикт правила — `N` или `X`; дефект массовым не бывает.
В рабочем списке строка, закрытая правилом, помечается `#R1` внутри вердикта:
`N #R1 фон`.

Пятый столбец квитанции — закрытое решение. `правило` значит: прочитанная
строка подтверждает именно массовое правило. `кандидат` значит: строка оказалась
самостоятельным подозрением; она не может оставаться закрытой правилом и должна
уйти в рабочем списке в отдельную находку. Проверяется каждая строка `+R…`, даже
если она не входила в требуемую выборку: неизвестное правило, неизвестная строка,
строка вне покрытия правила и повторная пара `правило+строка` — отдельные дефекты
сдачи.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import sys

# ---------------------------------------------------------------------------
# the one verifier
# ---------------------------------------------------------------------------
# Quotes are judged by `citecheck`, imported rather than re-implemented. Two
# verifiers of the same thing drift, and the day they disagree the one nobody
# runs is the one that was right.
def _load_citecheck():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "citecheck.py")
    spec = importlib.util.spec_from_file_location("citecheck_for_triage", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CC = _load_citecheck()

# ---------------------------------------------------------------------------
# the closed selector language
# ---------------------------------------------------------------------------
FIELDS = ("ось", "хост", "путь", "файл", "n", "всплеск", "id")

# Every way of saying "match the record text". Named explicitly so the refusal
# can explain itself instead of printing "unknown field" — a diagnostic that
# reads like a typo invites a second guess, and the second guess is another
# spelling of the same thing.
CONTENT_FIELDS = ("запись", "текст", "строка", "содержит", "содержимое",
                  "маркер", "маркеры", "слово", "слова", "excerpt", "record",
                  "text", "line", "regex", "grep", "match", "pattern",
                  "contains", "шаблон")

EXCURSION = ("rare", "new", "peak")

# The two axes Step 1 states as a claim about TIME rather than about rarity:
# `new` is "this participant was not here in the first half of the stream" and
# `peak` is "this measurement went up threefold and came back". Neither is a
# shape that repeats, so neither can honestly be swept into a bulk dismissal —
# a rule closes a class, and these two rows are by construction not a class.
# They are also rare enough that naming them costs a handful of rows on a
# corpus of thousands (measured; see `skills/DESIGN-EVIDENCE.md`, section v24).
STRONG = ("new", "peak")

# ---------------------------------------------------------------------------
# the claim language: what a rule ASSERTS, as opposed to which rows it picks
# ---------------------------------------------------------------------------
# The selector language above answers "which rows". It deliberately cannot
# speak about the record text, because the worklist's excerpt column is a
# projection and a predicate over a projection is not a measurement.
#
# That left the rule's reason as prose, and prose was the hole. A rule can
# close a class of rows for a stated reason, the tool can demand receipts from
# inside that class, the analyst can quote them accurately, every quote can
# verify — and the quotes can show the exact opposite of the reason, because
# nothing ever compared the two. Measured: one rule closed a large block of
# rows under a domain-specific hunch, the tool picked seven receipts, all seven
# landed on the very records that contradicted that hunch, all seven verified
# `ok`, and the report then disposed of the whole thing in an uncited line.
#
# So the fourth column stops being prose. It is a CLAIM: the same term grammar
# as the selector (`поле op значение`, `&&`, `|`), over a different closed set
# of fields — and every one of them is measured on the REAL LINE read out of
# the file, never on the excerpt column. Content over the file is a
# measurement; content over the projection is not. That is the whole of the
# distinction, and it is why this is not the refused predicate wearing a hat.
#
# The claim is evaluated on EVERY row the rule closes, because that costs
# seconds (measured: 2 546 lines out of 388 files in about three), and the
# receipt sample is left doing the job it was built for — making the analyst
# read.
CLAIM_FIELDS = ("токен", "код", "адрес")

# A "token" here is a run of characters between the separators that structure
# a log line. `-`, `_` and `*` are NOT separators: a random high-entropy label
# is exactly the thing that keeps its length across them, and splitting on them
# would hand it back as a handful of short pieces.
_TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z_*\-]+")
# A result code has one shape and no protocol: three digits, standing alone,
# inside the range codes live in. Guarded on both sides so an octet of a
# dotted quad and the tail of a longer number are not read as outcomes.
_CODE_RE = re.compile(r"(?<![0-9.])([1-5][0-9]{2})(?![0-9.])")
_IPV4_RE = re.compile(r"(?<![0-9.])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?![0-9.])")

RECEIPT_DECISIONS = ("правило", "кандидат")
_KV_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-я_.:-])([0-9A-Za-zА-Яа-я_.:-]{1,40})="
    r"([^\s,;|]{1,120})")


def extract_kv(text, limit=24):
    """Bounded format-agnostic `key=value` exposure for receipt lines."""
    out, seen = [], set()
    for m in _KV_RE.finditer(text or ""):
        key = m.group(1).strip()
        val = m.group(2).strip().strip("\"'«»“”`")
        if not key or not val:
            continue
        pair = (key, val)
        if pair in seen:
            continue
        seen.add(pair)
        out.append({"key": key, "value": val})
        if len(out) >= limit:
            break
    return out


def _unquote(text):
    t = (text or "").strip()
    if len(t) >= 2 and ((t[0], t[-1]) in (("«", "»"), ('"', '"'), ("“", "”"), ("`", "`"))):
        return t[1:-1]
    return t


def measure_line(line):
    """-> {токен: int, код: [int], адрес: [str]} for one real log line.

    Pure, and importable on its own: a measurement nobody can reproduce by
    hand is not a measurement."""
    text = line or ""
    toks = [t for t in _TOKEN_SPLIT_RE.split(text) if t]
    return {"токен": max([len(t) for t in toks] or [0]),
            "код": [int(m.group(1)) for m in _CODE_RE.finditer(text)],
            "адрес": [m.group(1) for m in _IPV4_RE.finditer(text)]}


# How "worst" is defined per field, so the tool can demand a receipt on the row
# that comes closest to breaking the rule's own claim. Raising a bound to make
# a rule pass therefore does not buy silence: it makes the line the bound was
# raised for the one line the analyst is required to read and quote.
def claim_scalar(field, m):
    if field == "токен":
        return float(m["токен"])
    if field == "код":
        return float(max(m["код"]) if m["код"] else -1)
    if field == "адрес":
        best = -1.0
        for a in m["адрес"]:
            parts = a.split(".")
            try:
                v = sum(int(x) << (8 * (3 - i)) for i, x in enumerate(parts))
            except ValueError:
                continue
            best = max(best, float(v))
        return best
    return 0.0


def parse_claim(text):
    """-> [(field, op, value)] or raise Unevaluable.

    Same grammar as `parse_condition`, different field table. The two refusals
    that matter are spelled out separately, because they are different
    mistakes: a predicate over the excerpt column is the old category error,
    and a SELECTOR field in this column is a rule that picked rows and then
    asserted nothing about them."""
    body = (text or "").strip()
    if not body:
        raise Unevaluable(
            "у правила нет утверждения. Четвёртая колонка — это то, что "
            "правило УТВЕРЖДАЕТ про каждую закрытую им строку, и его должно "
            "быть можно вычислить: %s. Словами основание пишется в пятой "
            "колонке." % ", ".join(CLAIM_FIELDS))
    terms = []
    for chunk in re.split(r"&&|\bи\b|,", body):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = TERM_RE.match(chunk)
        if not m:
            raise Unevaluable(
                "утверждение %r — это не утверждение, а слова. Нужен вид "
                "«поле оператор значение», поля: %s"
                % (chunk[:60], ", ".join(CLAIM_FIELDS)))
        field, op, value = m.group(1).strip(), m.group(2), m.group(3).strip()
        low = field.lower().strip("«»\"'` ")
        if low in CONTENT_FIELDS:
            raise Unevaluable(
                "утверждение не может опираться на колонку «запись» (%r): она "
                "проекция, а не файл. Поля утверждения меряются по настоящей "
                "строке: %s" % (field[:40], ", ".join(CLAIM_FIELDS)))
        if low in FIELDS:
            raise Unevaluable(
                "%r — поле ОТБОРА: им ты выбираешь строки, а не утверждаешь "
                "что-либо про их содержимое. Утверждение — про то, что в "
                "строке: %s" % (field[:40], ", ".join(CLAIM_FIELDS)))
        if low not in CLAIM_FIELDS:
            raise Unevaluable(
                "поле утверждения %r инструменту неизвестно — он не может его "
                "измерить. Поля: %s" % (field[:40], ", ".join(CLAIM_FIELDS)))
        if not value:
            raise Unevaluable("у поля %r нет значения" % low)
        if low == "токен" and op not in ("<=", ">=", "=", "!="):
            raise Unevaluable("«токен» — число: операторы <=, >=, =, !=")
        if low == "токен":
            try:
                int(value)
            except ValueError:
                raise Unevaluable("«токен%s%s» — справа должно быть число"
                                  % (op, value))
        terms.append((low, op, value))
    if not terms:
        raise Unevaluable("утверждение пустое")
    return terms


def claim_violations(terms, m):
    """-> [(field, op, value, measured)] — empty when the line honours the claim.

    `токен` is one number per line and compares. `код` and `адрес` are SETS
    read off the line, and the claim is about every member: «код=200» means
    every result code on this line is 200, not that one of them is."""
    bad = []
    for field, op, value in terms:
        if field == "токен":
            got, want = m["токен"], int(value)
            ok = {"<=": got <= want, ">=": got >= want,
                  "=": got == want, "!=": got != want}.get(op, False)
            if not ok:
                bad.append((field, op, value, got))
            continue
        alts = [v.strip() for v in value.split("|")]
        for got in m[field]:
            s = str(got)
            if op in ("~", "!~"):
                hit = any(_glob_to_re(a).match(s) for a in alts)
            else:
                hit = any(a.lower() == s.lower() for a in alts)
            if op in ("=", "~") and not hit:
                bad.append((field, op, value, got))
                break
            if op in ("!=", "!~") and hit:
                bad.append((field, op, value, got))
                break
    return bad

RULE_ID_RE = re.compile(r"^[Rr]\d+$")
TAG_RE = re.compile(r"#([Rr]\d+)")
TERM_RE = re.compile(r"^\s*([^=!<>~\s]+)\s*(!~|~|!=|<=|>=|=)\s*(.*?)\s*$")
REF_RE = re.compile(r"^(.+?):(\d+)(?:\s*[-–—]\s*(\d+))?$")
NUM_RE = re.compile(r"n\s*=\s*(\d+)")
ID_RANGE_RE = re.compile(r"^([A-Za-zА-Яа-я]*)(\d+)\s*[-–—]\s*([A-Za-zА-Яа-я]*)(\d+)$")


class Unevaluable(Exception):
    """A condition this tool cannot compute. Not an error — a verdict."""


def _glob_to_re(pat):
    out = []
    for ch in pat:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def parse_condition(cond):
    """-> [(field, op, value)] or raise Unevaluable."""
    text = (cond or "").strip()
    if not text:
        raise Unevaluable("условие пустое")
    terms = []
    for chunk in re.split(r"&&|\bи\b|,", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = TERM_RE.match(chunk)
        if not m:
            raise Unevaluable(
                "не разобрал условие %r — нужен вид «поле=значение», поля: %s"
                % (chunk[:60], ", ".join(FIELDS)))
        field, op, value = m.group(1).strip(), m.group(2), m.group(3).strip()
        low = field.lower().strip("«»\"'` ")
        if low in CONTENT_FIELDS:
            raise Unevaluable(
                "правило не может опираться на текст записи (%r). Колонка "
                "«запись» — это проекция, а не файл: слово, которого нет в "
                "твоём списке, получит ноль совпадений и штамп «фон», а файл "
                "останется неоткрытым. Выражай правило через то, что посчитал "
                "шаг 1: %s" % (field[:40], ", ".join(FIELDS)))
        if low not in FIELDS:
            raise Unevaluable(
                "поле %r инструменту неизвестно — он не может его вычислить, "
                "значит это не правило. Поля: %s" % (field[:40],
                                                     ", ".join(FIELDS)))
        if not value:
            raise Unevaluable("у поля %r нет значения" % low)
        if low in ("n",) and op in ("~", "!~"):
            raise Unevaluable("поле «n» — число, глоб к нему не применяется")
        terms.append((low, op, value))
    if not terms:
        raise Unevaluable("условие пустое")
    return terms


def _match_one(field, op, value, row):
    got = row.get(field)
    if field == "n":
        try:
            want = int(value)
        except ValueError:
            raise Unevaluable("«n%s%s» — справа должно быть число" % (op, value))
        if got is None:
            return False
        return {"=": got == want, "!=": got != want,
                "<=": got <= want, ">=": got >= want}.get(op, False)
    if field == "id":
        m = ID_RANGE_RE.match(value)
        if m:
            lo, hi = int(m.group(2)), int(m.group(4))
            n = row.get("id_num")
            pre = row.get("id_pre")
            inside = (n is not None and lo <= n <= hi
                      and pre == (m.group(1) or pre))
            return inside if op in ("=", "~") else not inside
    got = "" if got is None else str(got)
    alts = [v.strip() for v in value.split("|")]
    if op in ("~", "!~"):
        hit = any(_glob_to_re(a).match(got) for a in alts)
    else:
        hit = any(a.lower() == got.lower() for a in alts)
    return hit if op in ("=", "~") else not hit


def matches(terms, row):
    return all(_match_one(f, o, v, row) for f, o, v in terms)


# ---------------------------------------------------------------------------
# the worklist, as the columns the selector language is closed over
# ---------------------------------------------------------------------------
def host_of(worklist_path, ref):
    """The machine a row belongs to.

    Step 1 writes one worklist per machine and names the file after it, so the
    filename is the authority when it has one. Falling back to the first path
    component matches how the reference is written on a multi-machine bundle
    and is harmless on a single one."""
    base = os.path.basename(worklist_path)
    m = re.match(r"^worklist-(.+)\.tsv$", base)
    if m:
        return m.group(1)
    head = ref.split("/", 1)
    return head[0] if len(head) > 1 else ""


def read_worklist(path):
    """-> [row dicts]. Column order is Step 1's: id, вердикт, ось, ссылка,
    частота, запись."""
    rows = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if raw.startswith("#") or not raw.strip():
                continue
            c = raw.rstrip("\n").split("\t")
            if len(c) < 2:
                continue
            c += [""] * (6 - len(c))
            ref = c[3].strip()
            m = REF_RE.match(ref)
            n = NUM_RE.search(c[4] or "")
            idm = re.match(r"^([A-Za-zА-Яа-я]*)(\d+)$", c[0].strip())
            rows.append({
                "id": c[0].strip(),
                "verdict": c[1].strip(),
                "ось": c[2].strip(),
                "ref": ref,
                "путь": m.group(1) if m else ref,
                "line": int(m.group(2)) if m else None,
                "line_end": int(m.group(3)) if (m and m.group(3)) else None,
                "файл": os.path.basename(m.group(1)) if m else "",
                "хост": host_of(path, ref),
                "n": int(n.group(1)) if n else None,
                "всплеск": "да" if "ВСПЛЕСК" in (c[4] or "") else "нет",
                "запись": c[5],
                "id_pre": idm.group(1) if idm else "",
                "id_num": int(idm.group(2)) if idm else None,
            })
    return rows


# ---------------------------------------------------------------------------
# how many receipts, and which rows
# ---------------------------------------------------------------------------
def receipts_needed(n, files=0):
    """k = min(N, max(3, ceil(sqrt(N)), F)) — see the module docstring."""
    if n <= 0:
        return 0
    return min(n, max(3, int(math.ceil(math.sqrt(n))), int(files or 0)))


def _h(seed, s):
    return hashlib.sha256((seed + "\x00" + s).encode("utf-8")).hexdigest()


def _round_robin(pool, seed, want):
    """Take `want` rows from `pool`, one file at a time before any file gets a
    second. Order of files and of rows inside a file is a hash of the coverage,
    so it is reproducible and it is not the analyst's to choose."""
    if want <= 0 or not pool:
        return []
    by_file = {}
    for r in pool:
        by_file.setdefault(r["путь"], []).append(r)
    order = sorted(by_file, key=lambda f: _h(seed, f))
    for f in order:
        by_file[f].sort(key=lambda r: _h(seed, r["id"]))
    out, depth = [], 0
    while len(out) < want:
        moved = False
        for f in order:
            if depth < len(by_file[f]):
                out.append(by_file[f][depth])
                moved = True
                if len(out) >= want:
                    break
        if not moved:
            break
        depth += 1
    return out


def demand(covered, boundary=()):
    """-> ([row ids the rule must receipt], k, F).

    `boundary` is the row that sits closest to breaking the rule's own claim,
    one per claim field. It is ADDED to the v22 sample rather than taken out of
    it, so the excursion-first guarantee that sample was built for is untouched
    and the extra cost is at most one row per field."""
    if not covered:
        return [], 0, 0
    seed = hashlib.sha256(
        "\n".join(sorted(r["id"] for r in covered)).encode("utf-8")).hexdigest()
    exc = [r for r in covered if r["ось"] in EXCURSION]
    rest = [r for r in covered if r["ось"] not in EXCURSION]
    files = len({r["путь"] for r in exc})
    k = receipts_needed(len(covered), files)
    picked = _round_robin(exc, seed, k)
    if len(picked) < k:
        picked += _round_robin(rest, seed, k - len(picked))
    ids = [r["id"] for r in picked]
    return ids + [b for b in boundary if b not in ids], k, files


def read_rows(rows, corpus):
    """-> {row id: the real line the row addresses}.

    One pass per file, stopping at the highest line asked for — the same reader
    `citecheck` uses, for the same reason it exists: a claim about what is in
    the logs has to be evaluated against the logs. Rows whose reference means
    more than one file are left out rather than guessed at; `citecheck` refuses
    those references in the report for the same reason."""
    by_rel, by_base = CC.index_corpus(corpus)
    want, owner = {}, {}
    for r in rows:
        if r.get("line") is None:
            continue
        cands, _how = CC.resolve(r["путь"], by_rel, by_base)
        if len(cands) != 1:
            continue
        rel = cands[0]
        want.setdefault(rel, set()).add(r["line"])
        owner.setdefault((rel, r["line"]), []).append(r["id"])
    out = {}
    for rel, lines in sorted(want.items()):
        ap = by_rel[rel]
        try:
            if CC.looks_binary(ap):
                continue
            got, _total = CC.read_lines(ap, lines)
        except (OSError, ValueError, UnicodeError):
            continue
        for n, text in got.items():
            for rid in owner.get((rel, n), []):
                out[rid] = text
    return out


def check_claim(terms, cov, lines, seed):
    """-> (violations, measured extremes, boundary row ids, rows read).

    Evaluated on EVERY row the rule closes, not on the receipts: the receipts
    are what makes the analyst read, the claim is what makes the rule true, and
    those are two different jobs."""
    viol, measured, read = [], {}, 0
    best = {}
    for row in cov:
        text = lines.get(row["id"])
        if text is None:
            continue
        read += 1
        m = measure_line(text)
        for field, op, value in terms:
            s = claim_scalar(field, m)
            key = (-s, _h(seed, row["id"]))
            if field not in best or key < best[field][0]:
                best[field] = (key, row["id"])
            if field not in measured or s > measured[field]:
                measured[field] = s
        for field, op, value, got in claim_violations(terms, m):
            viol.append({"id": row["id"], "ссылка": row["ref"], "поле": field,
                         "оператор": op, "граница": value, "измерено": got,
                         "строка": (text or "")[:160]})
    out_measured = {}
    for field, s in measured.items():
        out_measured[field] = int(s) if field == "токен" else s
    boundary = [best[f][1] for f in sorted(best)]
    seen, uniq = set(), []
    for b in boundary:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return viol, out_measured, uniq, read


# ---------------------------------------------------------------------------
# rules.tsv
# ---------------------------------------------------------------------------
def read_rules(path):
    """-> ([rule dicts], [receipt dicts], [(line number, complaint)])."""
    rules, receipts, junk = [], [], []
    if not path:
        return rules, receipts, junk
    if not os.path.exists(path):
        junk.append((0, "нет такого файла: %s" % path))
        return rules, receipts, junk
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for ln, raw in enumerate(fh, 1):
            if raw.startswith("#") or not raw.strip():
                continue
            c = [x.strip() for x in raw.rstrip("\n").split("\t")]
            if c[0].startswith("+"):
                if len(c) != 5:
                    junk.append((ln, "квитанция должна иметь ровно 5 столбцов: "
                                     "+R<k> <id> путь:N «цитата» правило|кандидат"))
                    continue
                decision = c[4].lower().strip()
                if decision not in RECEIPT_DECISIONS:
                    junk.append((ln, "пятый столбец квитанции должен быть "
                                     "ровно: %s" % "|".join(RECEIPT_DECISIONS)))
                    continue
                receipts.append({"rule": c[0][1:].upper(), "row": c[1],
                                 "ref": c[2], "quote": c[3],
                                 "decision": decision,
                                 "fields": extract_kv(c[3]), "line": ln})
                continue
            if len(c) < 2:
                junk.append((ln, "строка правила без условия"))
                continue
            rid = c[0].upper()
            if not RULE_ID_RE.match(c[0]):
                junk.append((ln, "имя правила %r не вида R1/R2 — это не строка "
                                 "правила и не квитанция" % c[0][:30]))
                continue
            rules.append({"id": rid, "cond": c[1],
                          "verdict": (c[2] if len(c) > 2 else "").strip(),
                          "claim": (c[3] if len(c) > 3 else "").strip(),
                          "why": (c[4] if len(c) > 4 else "").strip(),
                          "line": ln})
    return rules, receipts, junk


# ---------------------------------------------------------------------------
# verification: one citecheck pass for every quote in the artefact
# ---------------------------------------------------------------------------
def receipt_line(ref, quote):
    q = (quote or "").strip()
    if not (q.startswith("«") or q.startswith('"') or q.startswith("“")):
        q = "«%s»" % q
    return "%s %s" % ((ref or "").strip(), q)


def verify(claims, corpus):
    """claims: [(key, one line of text carrying `путь:N` and a quote)]
    -> {key: verdict}.

    A pseudo-report of one claim per line, so `citecheck.check` does the whole
    job — resolution, ambiguity, ranges, binaries, quote overlap. The WORST
    verdict on a line wins: a line carrying two citations, one of which does
    not hold, is a line that does not hold."""
    if not claims:
        return {}
    lines, keys = [], {}
    for i, (key, text) in enumerate(claims):
        lines.append(" ".join((text or "").split()))
        keys[i + 1] = key
    d = CC.check("\n".join(lines), corpus, require_quote=True)
    out = {}
    for r in d["citations"]:
        key = keys.get(r["report_line"])
        if key is None:
            continue
        if key not in out or CC.RANK[r["verdict"]] > CC.RANK[out[key]]:
            out[key] = r["verdict"]
    for r in d["non_references"]:
        key = keys.get(r["report_line"])
        if key is not None:
            out[key] = "не-ссылка"
    for key, _text in claims:
        out.setdefault(key, "не-ссылка")
    return out


def same_address(row, ref):
    """A receipt for row g0007 has to cite the line g0007 points at."""
    m = REF_RE.match((ref or "").strip())
    if not m or row.get("line") is None:
        return False
    if m.group(1).strip() != row["путь"]:
        return False
    lo, hi = row["line"], row["line_end"] or row["line"]
    n = int(m.group(2))
    return lo <= n <= hi


def enrich_receipt_fields(receipt, real_line=None):
    """Attach generic key=value fields from the real cited line when available."""
    fields = extract_kv(real_line)
    if not fields:
        fields = extract_kv(_unquote(receipt.get("quote")))
    receipt["fields"] = fields
    return fields


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def analyse(worklist_path, rules_path, corpus):
    rows = read_worklist(worklist_path)
    rules, receipts, junk = read_rules(rules_path)
    by_id = {r["id"]: r for r in rows}

    parsed, problems, claims, no_claim = {}, {}, {}, 0
    for rule in rules:
        try:
            parsed[rule["id"]] = parse_condition(rule["cond"])
            problems[rule["id"]] = []
        except Unevaluable as e:
            parsed[rule["id"]] = None
            problems[rule["id"]] = [str(e)]
        if not (rule["claim"] or "").strip():
            no_claim += 1
        try:
            claims[rule["id"]] = parse_claim(rule["claim"])
        except Unevaluable as e:
            claims[rule["id"]] = None
            problems[rule["id"]].append(str(e))
        if rule["verdict"] and rule["verdict"][:1].upper() not in (
                "N", "Н", "X", "Х"):
            problems[rule["id"]].append(
                "вердикт правила %r — массовым бывает только «N» или «X»"
                % rule["verdict"][:20])

    buckets = {"поимённо": 0, "по правилу": 0, "без опоры": 0,
               "не разобрано": 0}
    covered = {r["id"]: [] for r in rules}
    outside, inline_claims, strong = [], [], []
    for row in rows:
        state, _why = CC.classify_verdict(row["verdict"])
        if state == "open":
            buckets["не разобрано"] += 1
            continue
        tags = [t.upper() for t in TAG_RE.findall(row["verdict"])]
        live = [t for t in tags if t in parsed and parsed[t] is not None]
        if live:
            ok = False
            for t in live:
                if matches(parsed[t], row):
                    covered[t].append(row)
                    ok = True
                    break
            if ok:
                buckets["по правилу"] += 1
                if row["ось"] in STRONG:
                    strong.append({"id": row["id"], "ось": row["ось"],
                                   "ссылка": row["ref"], "правило": ",".join(live)})
            else:
                outside.append((row["id"], ",".join(live)))
                buckets["без опоры"] += 1
            continue
        if CC.FINDING_REF_RE.match(row["verdict"]) or (
                CC.DEFECT_RE.match(row["verdict"])
                and CC.DIGIT_RE.search(row["verdict"])):
            buckets["поимённо"] += 1
            continue
        cites = CC.extract(row["verdict"])
        if cites:
            buckets["поимённо"] += 1
            inline_claims.append(("row:%s" % row["id"], row["verdict"]))
            continue
        buckets["без опоры"] += 1

    # what each rule owes, and what it brought
    got = {}
    for rc in receipts:
        got.setdefault(rc["rule"], {}).setdefault(rc["row"], []).append(rc)
    pool = []
    seen_ids = set()
    for rid in covered:
        for row in covered[rid]:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                pool.append(row)
    receipt_rows = []
    for rc in receipts:
        row = by_id.get(rc["row"])
        if row is not None and row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            receipt_rows.append(row)
    read_pool = pool + receipt_rows
    real_lines = read_rows(read_pool, corpus) if read_pool else {}

    receipt_claims, out_rules = list(inline_claims), []
    misaddressed, unpaid, bad_claim, candidate_receipts = 0, 0, 0, 0
    receipt_problems, duplicate_receipts = [], []
    receipt_details_by_rule, demanded_by_rule = {}, {}
    for rule in rules:
        rid = rule["id"]
        cov = covered.get(rid, [])
        terms = claims.get(rid)
        seed = hashlib.sha256(
            "\n".join(sorted(r["id"] for r in cov)).encode("utf-8")).hexdigest()
        if terms and cov:
            viol, measured, boundary, nread = check_claim(
                terms, cov, real_lines, seed)
        else:
            viol, measured, boundary, nread = [], {}, [], 0
        bad_claim += len(viol)
        want, k, files = demand(cov, boundary)
        demanded_by_rule[rid] = set(want)
        have_all = got.get(rid, {})
        have_rule = {i: [rc for rc in rcs if rc["decision"] == "правило"]
                     for i, rcs in have_all.items()}
        have_rule = {i: rcs for i, rcs in have_rule.items() if rcs}
        missing = [i for i in want if i not in have_rule]
        unpaid += len(missing)
        receipt_details = receipt_details_by_rule.setdefault(rid, [])
        out_rules.append({
            "id": rid, "условие": rule["cond"], "вердикт": rule["verdict"],
            "утверждение": rule["claim"], "основание": rule["why"],
            "покрытие": len(cov),
            "нужно квитанций": k, "файлов с экскурсиями": files,
            "квитанций": len([i for i in want if i in have_rule]),
            "квитанции-поля": receipt_details,
            "кандидат-квитанций": len([d for d in receipt_details if d["decision"] == "кандидат"]),
            "нужны": want, "не хватает": missing,
            "граничные": boundary, "строк проверено": nread,
            "измерено": measured, "нарушения": viol,
            "нарушений": len(viol),
            "проблемы": problems.get(rid, []),
        })

    rule_ids = {r["id"] for r in rules}
    covered_ids = dict((rid, {r["id"] for r in rows}) for rid, rows in covered.items())
    by_receipt_key = {}
    for rc in receipts:
        by_receipt_key.setdefault((rc["rule"], rc["row"]), []).append(rc)
    duplicate_keys = set()
    for (rid, row_id), rcs in sorted(by_receipt_key.items()):
        if len(rcs) <= 1:
            continue
        duplicate_keys.add((rid, row_id))
        payloads = {(r["ref"], r["quote"], r["decision"]) for r in rcs}
        dup = {"rule": rid, "row": row_id,
               "lines": [r["line"] for r in rcs],
               "kind": "identical" if len(payloads) == 1 else "conflicting"}
        duplicate_receipts.append(dup)
        receipt_problems.append({"kind": "duplicate", "rule": rid,
                                 "row": row_id, "lines": dup["lines"],
                                 "detail": dup["kind"]})

    for rc in receipts:
        rid, row_id = rc["rule"], rc["row"]
        row = by_id.get(row_id)
        fields = enrich_receipt_fields(rc, real_lines.get(row_id))
        detail = {"rule": rid, "row": row_id, "decision": rc["decision"],
                  "ref": rc["ref"], "line": rc["line"], "fields": fields,
                  "demanded": row_id in demanded_by_rule.get(rid, set()),
                  "problems": []}
        receipt_details_by_rule.setdefault(rid, []).append(detail)
        if rc["decision"] == "кандидат":
            candidate_receipts += 1
            detail["problems"].append("candidate")
            receipt_problems.append({"kind": "candidate", "rule": rid,
                                     "row": row_id, "line": rc["line"],
                                     "detail": "перенеси строку в находку"})
        if rid not in rule_ids:
            detail["problems"].append("unknown-rule")
            receipt_problems.append({"kind": "unknown-rule", "rule": rid,
                                     "row": row_id, "line": rc["line"],
                                     "detail": "нет такого правила"})
        elif row is None:
            detail["problems"].append("unknown-row")
            receipt_problems.append({"kind": "unknown-row", "rule": rid,
                                     "row": row_id, "line": rc["line"],
                                     "detail": "нет такой строки рабочего списка"})
        elif row_id not in covered_ids.get(rid, set()):
            detail["problems"].append("row-not-covered")
            receipt_problems.append({"kind": "row-not-covered", "rule": rid,
                                     "row": row_id, "line": rc["line"],
                                     "detail": "строка не закрыта этим правилом"})
        if row is not None:
            if not same_address(row, rc["ref"]):
                misaddressed += 1
                detail["problems"].append("wrong-address")
            receipt_claims.append(("rcpt:%s:%s:%s" % (rid, row_id, rc["line"]),
                                   receipt_line(rc["ref"], rc["quote"])))

    for r in out_rules:
        r["кандидат-квитанций"] = len(
            [d for d in r["квитанции-поля"] if d["decision"] == "кандидат"])

    verdicts = verify(receipt_claims, corpus)
    bad_receipts = sorted(k for k, v in verdicts.items()
                          if k.startswith("rcpt:") and v != "ok")
    bad_inline = sorted(k for k, v in verdicts.items()
                        if k.startswith("row:") and v != "ok")
    for r in out_rules:
        r["квитанций не подтвердилось"] = len(
            [k for k in bad_receipts if k.startswith("rcpt:%s:" % r["id"])])

    unevaluable = len([r for r in out_rules if r["проблемы"]])
    invalid_receipts = len([p for p in receipt_problems
                            if p["kind"] in ("unknown-rule", "unknown-row",
                                              "row-not-covered")])
    totals = {
        "без опоры": buckets["без опоры"],
        "непроверяемых правил": unevaluable,
        "правил без утверждения": no_claim,
        "нарушений утверждения": bad_claim,
        "сильных экскурсий под правилом": len(strong),
        "нехватка квитанций": unpaid,
        "квитанций не подтвердилось": len(bad_receipts),
        "квитанций не по адресу": misaddressed,
        "квитанций-кандидатов": candidate_receipts,
        "ошибок лишних квитанций": invalid_receipts,
        "дубликатов квитанций": len(duplicate_receipts),
        "строк вне своего правила": len(outside),
        "ссылок в вердиктах не подтвердилось": len(bad_inline),
    }
    closed = buckets["поимённо"] + buckets["по правилу"] + buckets["без опоры"]
    widest = max(out_rules, key=lambda r: r["покрытие"]) if out_rules else None
    return {
        "worklist": os.path.abspath(worklist_path),
        "corpus": os.path.abspath(corpus),
        "rows": len(rows), "closed": closed,
        "buckets": buckets, "rules": out_rules, "totals": totals,
        "junk": [{"строка": ln, "что": what} for ln, what in junk],
        "outside": [{"id": i, "правило": t} for i, t in outside],
        "strong": strong,
        "bad_receipts": bad_receipts, "bad_inline": bad_inline,
        "receipt_problems": receipt_problems,
        "duplicate_receipts": duplicate_receipts,
        "widest": None if not widest else {
            "id": widest["id"], "покрытие": widest["покрытие"],
            "доля": (round(100.0 * widest["покрытие"] / closed, 1)
                     if closed else 0.0)},
        "blocking": sum(totals[k] for k in (
            "без опоры", "непроверяемых правил", "правил без утверждения",
            "нарушений утверждения", "сильных экскурсий под правилом",
            "нехватка квитанций",
            "квитанций не подтвердилось", "квитанций не по адресу",
            "квитанций-кандидатов", "ошибок лишних квитанций",
            "дубликатов квитанций", "строк вне своего правила",
            "ссылок в вердиктах не подтвердилось")),
    }


def share(part, whole):
    return "" if not whole else "  (%.1f %%)" % (100.0 * part / whole)


def render(d):
    closed = d["closed"]
    out = ["ТРИАЖ — из чего собран разбор рабочего списка",
           "  строк всего: %d" % d["rows"],
           "  закрыто поимённо (своя ссылка или блок находки): %d%s"
           % (d["buckets"]["поимённо"], share(d["buckets"]["поимённо"], closed)),
           "  закрыто правилом: %d%s"
           % (d["buckets"]["по правилу"], share(d["buckets"]["по правилу"], closed)),
           "  закрыто без опоры (ни ссылки, ни правила): %d%s"
           % (d["buckets"]["без опоры"], share(d["buckets"]["без опоры"], closed)),
           "  ещё не разобрано: %d" % d["buckets"]["не разобрано"], ""]
    w = d["widest"]
    out.append("ПРАВИЛ: %d · непроверяемых условий/утверждений: %d · "
               "без всех квитанций: %d · нарушенных утверждений: %d"
               % (len(d["rules"]), d["totals"]["непроверяемых правил"],
                  len([r for r in d["rules"] if r["не хватает"]]),
                  len([r for r in d["rules"] if r.get("нарушений")])))
    out.append("  самое широкое правило: %s"
               % ("—" if not w else "%s — %d строк (%.1f %% закрытых)"
                  % (w["id"], w["покрытие"], w["доля"])))
    for r in d["rules"]:
        out.append("")
        out.append("  %s  %s  →  %s" % (r["id"], r["условие"] or "—",
                                        r["вердикт"] or "—"))
        for p in r["проблемы"]:
            out.append("     ✗ %s" % p)
        if r["проблемы"]:
            continue
        out.append("     утверждение: %s" % (r["утверждение"] or "—"))
        if r["покрытие"]:
            out.append("     измерено на %d прочитанных строках из %d: %s"
                       % (r["строк проверено"], r["покрытие"],
                          " · ".join("%s макс %s" % (f, v)
                                     for f, v in sorted(r["измерено"].items()))
                          or "—"))
        if r["нарушения"]:
            out.append("     ✗ УТВЕРЖДЕНИЕ НЕ ДЕРЖИТСЯ на %d строках из %d:"
                       % (r["нарушений"], r["покрытие"]))
            for v in r["нарушения"][:5]:
                out.append("        %-10s %s — %s%s%s, а в строке %s"
                           % (v["id"], v["ссылка"], v["поле"], v["оператор"],
                              v["граница"], v["измерено"]))
                out.append("           %s" % v["строка"][:120])
            if r["нарушений"] > 5:
                out.append("        … и ещё %d" % (r["нарушений"] - 5))
            out.append("        правило утверждает не то, что в этих строках "
                       "написано. Либо сузь правило, либо это находка.")
        if r["граничные"]:
            out.append("     граничные строки (ближе всех к нарушению — их "
                       "квитируй обязательно): %s" % ", ".join(r["граничные"]))
        out.append("     закрывает %d строк · квитанций нужно %d, есть %d"
                   % (r["покрытие"], r["нужно квитанций"], r["квитанций"]))
        if r.get("квитанции-поля"):
            for rc in r["квитанции-поля"][:12]:
                fields = " ".join("%s=%s" % (f["key"], f["value"])
                                  for f in rc.get("fields", [])[:12]) or "—"
                marker = "✗ " if rc.get("problems") or rc["decision"] == "кандидат" else ""
                probs = "" if not rc.get("problems") else " · " + ",".join(rc["problems"])
                out.append("     %sквитанция %s: %s · %s%s"
                           % (marker, rc["row"], rc["decision"], fields, probs))
        if r.get("кандидат-квитанций"):
            out.append("     ✗ %d квитанц. помечены «кандидат»: переоткрой эти "
                       "строки в рабочем списке как находки; правилом они не "
                       "закрываются." % r["кандидат-квитанций"])
        if r["не хватает"]:
            out.append("     впиши в rules.tsv по строке на каждую "
                       "(+%s<TAB>id<TAB>путь:N<TAB>«дословная цитата»<TAB>правило):"
                       % r["id"])
            for i in r["не хватает"][:20]:
                out.append("       +%s\t%s\t?" % (r["id"], i))
            if len(r["не хватает"]) > 20:
                out.append("       … и ещё %d" % (len(r["не хватает"]) - 20))
    if d.get("strong"):
        out.append("")
        out.append("  сильные экскурсии, закрытые правилом: %d — так нельзя"
                   % len(d["strong"]))
        out.append("     оси %s — это утверждение про ВРЕМЯ («такого участника "
                   "в первой половине потока не было», «мера ушла втрое и "
                   "вернулась»), а не повторяющаяся форма. Класса тут нет, "
                   "значит и правила нет: закрывай такую строку поимённо — "
                   "своей ссылкой `путь:N` с цитатой или блоком находки."
                   % "/".join(STRONG))
        for s in d["strong"][:10]:
            out.append("     %-12s %-5s %s  (#%s)"
                       % (s["id"], s["ось"], s["ссылка"], s["правило"]))
        if len(d["strong"]) > 10:
            out.append("     … и ещё %d" % (len(d["strong"]) - 10))
    if d["outside"]:
        out.append("")
        out.append("  строки, помеченные правилом, которому они не "
                   "удовлетворяют: %d" % len(d["outside"]))
        for o in d["outside"][:10]:
            out.append("     %-12s %s" % (o["id"], o["правило"]))
    if d.get("duplicate_receipts"):
        out.append("")
        out.append("  дубликаты квитанций: %d" % len(d["duplicate_receipts"]))
        for dup in d["duplicate_receipts"][:10]:
            out.append("     +%s %s — %s, строки rules.tsv: %s"
                       % (dup["rule"], dup["row"], dup["kind"],
                          ", ".join(str(x) for x in dup["lines"])))
    if d.get("receipt_problems"):
        out.append("")
        out.append("  ошибки квитанций: %d" % len(d["receipt_problems"]))
        for p in d["receipt_problems"][:12]:
            out.append("     rules.tsv:%s +%s %s — %s: %s"
                       % (p.get("line", "?"), p.get("rule", "?"),
                          p.get("row", "?"), p.get("kind"),
                          p.get("detail", "")))
    if d["bad_receipts"]:
        out.append("")
        out.append("  квитанции, которые не подтвердились: %d"
                   % len(d["bad_receipts"]))
        for k in d["bad_receipts"][:10]:
            out.append("     %s" % k)
    if d["bad_inline"]:
        out.append("")
        out.append("  ссылки внутри вердиктов, которые не подтвердились: %d"
                   % len(d["bad_inline"]))
        for k in d["bad_inline"][:10]:
            out.append("     %s" % k)
    for j in d["junk"]:
        out.append("  ✗ rules.tsv:%s — %s" % (j["строка"], j["что"]))
    out.append("")
    if d["blocking"]:
        out.append("ИТОГ: НЕ ЗАКОНЧЕНО — %d" % d["blocking"])
        if d["totals"].get("квитанций-кандидатов"):
            out.append("  %d квитанций сказали «кандидат»: это не правило, "
                       "перенеси строки в находки."
                       % d["totals"]["квитанций-кандидатов"])
        if d["buckets"]["без опоры"]:
            out.append("  %d строк закрыто без единой опоры. Либо у строки своя "
                       "ссылка `путь:N` с цитатой, либо она под правилом "
                       "`#R<k>` из rules.tsv."
                       % d["buckets"]["без опоры"])
    else:
        out.append("ИТОГ: разбор опирается на проверенные строки.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="чем закрыты строки рабочего списка — поимённо, правилом "
                    "или ничем")
    ap.add_argument("--worklist", required=True, metavar="worklist.tsv")
    ap.add_argument("--rules", metavar="rules.tsv", default=None,
                    help="правила массового закрытия и квитанции к ним")
    ap.add_argument("--corpus", required=True, metavar="КАТАЛОГ",
                    help="корень корпуса — по нему проверяются цитаты")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.exists(args.worklist):
        sys.exit("нет такого файла: %s" % args.worklist)
    if not os.path.isdir(args.corpus):
        sys.exit("нет такого каталога: %s" % args.corpus)
    d = analyse(args.worklist, args.rules, args.corpus)
    if args.json:
        json.dump(d, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(d) + "\n")
    return 1 if d["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
