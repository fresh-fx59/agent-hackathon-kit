#!/usr/bin/env python3
"""Sherlock knowledge base — optional helper. Pure stdlib, py>=3.9, no pip.

THIS SCRIPT IS OPTIONAL BY CONTRACT (AGENTS.md R1). The skill must work
correctly when it cannot run at all — in Qwen Coder's non-interactive `-p` mode
`run_shell_command` is denied outright, so the skill's own reuse path is written
against `read_file` / `glob` / `grep_search`. Everything here is a convenience
for an engineer at a terminal, never a dependency.

    python3 patterns.py list                    # confirmed cards, one line each
    python3 patterns.py show <id>               # full card
    python3 patterns.py match <corpus-dir>      # which cards match this corpus
    python3 patterns.py new <id>                # print a filled template to stdout
    python3 patterns.py fingerprint <file|id>   # stable signature hash
    python3 patterns.py reject <id> --reason R --by WHO [--signature RE ...]
    python3 patterns.py rejected                # list rejections
    python3 patterns.py check                   # self-test over the whole base

Exit codes: 0 ok / 1 problem found / 2 usage error.
"""
import argparse
import gzip
import hashlib
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
PATTERNS_DIR = os.path.join(HERE, "patterns")
REJECTED = os.path.join(HERE, "REJECTED.md")

REQUIRED = ("id", "title", "kind", "severity", "status",
            "confirmed_by", "confirmed_at", "learned_from",
            "confirm_threshold", "expected_saving")

MAX_SCAN_BYTES = 64 * 1024 * 1024   # per file; cards are for shapes, not for haystacks


# --------------------------------------------------------------------------
# frontmatter: a deliberately tiny subset of YAML so no library is needed.
# Supported: `key: scalar` and `key:` followed by `  - item` lines. Nothing else.
# The schema is flat on purpose (input-gate principle: constrain the input so
# the reader stays trivial and can never half-parse a card).
# --------------------------------------------------------------------------
def parse_card(text):
    if not text.startswith("---"):
        raise ValueError("no frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("unterminated frontmatter")
    head, body = text[3:end], text[end + 4:]
    meta, key = {}, None
    for raw in head.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            meta.setdefault(key, [])
            if not isinstance(meta[key], list):
                meta[key] = []
            meta[key].append(_unquote(line.lstrip()[2:].strip()))
            continue
        if ":" not in line:
            raise ValueError("bad frontmatter line: %r" % raw)
        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip()
        if not v or v == "[]":          # `key:` with items below, or an explicit empty list
            meta[key] = []
        else:
            meta[key] = _unquote(v)
    return meta, body.strip()


def _unquote(s):
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def load_cards(status=None):
    cards = []
    if not os.path.isdir(PATTERNS_DIR):
        return cards
    for name in sorted(os.listdir(PATTERNS_DIR)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        path = os.path.join(PATTERNS_DIR, name)
        try:
            meta, body = parse_card(open(path, encoding="utf-8").read())
        except (OSError, ValueError) as e:
            cards.append({"_path": path, "_error": str(e), "id": name[:-3]})
            continue
        meta["_path"] = path
        meta["_body"] = body
        cards.append(meta)
    if status:
        cards = [c for c in cards if c.get("status") == status]
    return cards


def signatures(card, keys=("signature_any", "signature_all")):
    out = []
    for k in keys:
        v = card.get(k) or []
        out.extend(v if isinstance(v, list) else [v])
    return [s for s in out if s]


def all_signatures(card):
    """Every signature of a card — content and filename — for fingerprinting."""
    return signatures(card, ("signature_any", "signature_all", "signature_filename"))


def fingerprint(sigs):
    """Stable identity of a pattern = its normalised signature set.

    A re-worded proposal of the same signature keeps the same fingerprint, so a
    rejection survives rephrasing — that is the whole point of storing it."""
    norm = sorted(re.sub(r"\s+", " ", s).strip().lower() for s in sigs)
    return hashlib.sha256("\n".join(norm).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
def iter_lines(path):
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rb") as fh:
            read = 0
            for i, raw in enumerate(fh, 1):
                read += len(raw)
                if read > MAX_SCAN_BYTES:
                    return
                yield i, raw.decode("utf-8", "replace")
    except OSError:
        return


def corpus_files(root):
    if os.path.isfile(root):
        return [root]
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            out.append(os.path.join(dirpath, f))
    return out


def cmd_match(args):
    cards = [c for c in load_cards("confirmed") if not c.get("_error")]
    if not cards:
        print("база знаний пуста — обычное расследование")
        return 0
    files = corpus_files(args.corpus)
    if not files:
        print("в корпусе нет файлов: %s" % args.corpus)
        return 1
    def compile_all(c, keys):
        pats = []
        for s in signatures(c, keys):
            try:
                pats.append((s, re.compile(s)))
            except re.error as e:
                print("  ! карточка %s: некорректное регулярное выражение %r (%s)"
                      % (c.get("id"), s, e), file=sys.stderr)
        return pats

    compiled = [(c, p) for c in cards
                if (p := compile_all(c, ("signature_any", "signature_all")))]
    by_name = [(c, p) for c in cards
               if (p := compile_all(c, ("signature_filename",)))]

    hits = {}          # id -> {sig: [(file, lineno, text), ...]}
    for path in files:
        rel = os.path.relpath(path, args.corpus) if os.path.isdir(args.corpus) else path
        for c, pats in by_name:
            for s, rx in pats:
                if rx.search(os.path.basename(path)):
                    hits.setdefault(c["id"], {}).setdefault(s, []).append(
                        (rel, 0, "<совпадение по имени файла>"))
        for lineno, line in iter_lines(path):
            for c, pats in compiled:
                for s, rx in pats:
                    if rx.search(line):
                        b = hits.setdefault(c["id"], {}).setdefault(s, [])
                        b.append((rel, lineno, line.strip()[:160])
                                 if len(b) < 3 else None)   # count all, store 3

    if not hits:
        print("ЗНАНИЯ: карточек в базе %d, совпадений с корпусом нет "
              "— обычное расследование" % len(cards))
        return 0

    for c in cards:
        h = hits.get(c["id"])
        if not h:
            continue
        total = sum(len(v) for v in h.values())
        print("\n=== %s — %s  (%d совпадений)" % (c["id"], c.get("title", ""), total))
        print("    порог подтверждения: %s" % c.get("confirm_threshold", "—"))
        print("    ожидаемая экономия:  %s" % c.get("expected_saving", "—"))
        for s, ex in h.items():
            real = [e for e in ex if e]
            print("    подпись %r — %d совпадений" % (s, len(ex)))
            for f, n, t in real[:2]:
                print("      %s:%d  %s" % (f, n, t))
    print("\nПорог — не арифметика, а суждение: сверь его сам, прежде чем применять карточку.")
    return 0


def cmd_list(args):
    cards = load_cards()
    if not cards:
        print("база знаний пуста")
        return 0
    for c in cards:
        if c.get("_error"):
            print("!! %-28s ПОВРЕЖДЕНА: %s" % (c.get("id"), c["_error"]))
            continue
        print("%-28s %-9s %-8s %s" % (c.get("id"), c.get("status"),
                                      c.get("severity"), c.get("title")))
    return 0


def cmd_show(args):
    for c in load_cards():
        if c.get("id") == args.id:
            print(open(c["_path"], encoding="utf-8").read())
            return 0
    print("нет такой карточки: %s" % args.id, file=sys.stderr)
    return 1


def cmd_fingerprint(args):
    if os.path.exists(args.target):
        meta, _ = parse_card(open(args.target, encoding="utf-8").read())
    else:
        meta = next((c for c in load_cards() if c.get("id") == args.target), None)
        if meta is None:
            print("нет такой карточки и нет такого файла: %s" % args.target,
                  file=sys.stderr)
            return 1
    print(fingerprint(all_signatures(meta)))
    return 0


TEMPLATE = """---
id: {id}
title: <короткое имя паттерна по-русски>
kind: incident
severity: medium
status: proposed
confirmed_by: <кто подтвердил — заполняется ЧЕЛОВЕКОМ>
confirmed_at: <ГГГГ-ММ-ДД — заполняется при подтверждении>
learned_from: "<инцидент/датасет + прогон, из которого выучено>"
signature_any:
  - "<конкретное grep-выражение по содержимому строк>"
signature_all: []
signature_filename: []
confirm_threshold: "<что отличает инцидент от фона>"
expected_saving: "<сколько шагов/времени экономит в следующий раз>"
---

## Что это значит

## Корневая причина

## Что делать

## Чем это НЕ является (ложные срабатывания)

## Улики (файл:строка → цитата, только проверенные)

## Что карточка НЕ отменяет

Карточка сокращает поиск, а не проверку.
"""


def cmd_new(args):
    print(TEMPLATE.format(id=args.id))
    print("# ↑ это ЧЕРНОВИК. Он не сохранён. Сохранять только после подтверждения "
          "человеком:\n#   python3 patterns.py new %s > patterns/%s.md"
          % (args.id, args.id), file=sys.stderr)
    return 0


def read_rejected():
    out = []
    if not os.path.exists(REJECTED):
        return out
    for line in open(REJECTED, encoding="utf-8"):
        if not line.startswith("| ") or line.startswith("| id ") or set(line.strip()) <= set("|- "):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) >= 5:
            out.append(dict(zip(("id", "fingerprint", "date", "by", "reason"), parts)))
    return out


def cmd_rejected(args):
    rows = read_rejected()
    if not rows:
        print("отклонённых карточек нет")
        return 0
    for r in rows:
        print("%-32s %-14s %-11s %-10s %s"
              % (r["id"], r["fingerprint"], r["date"], r["by"], r["reason"]))
    return 0


def cmd_reject(args):
    fp = fingerprint(args.signature) if args.signature else "-"
    if not os.path.exists(REJECTED):
        with open(REJECTED, "w", encoding="utf-8") as fh:
            fh.write("# Отклонённые карточки\n\n"
                     "| id | fingerprint | дата | кто | почему отклонено |\n"
                     "|---|---|---|---|---|\n")
    for r in read_rejected():
        if r["id"] == args.id or (fp != "-" and r["fingerprint"] == fp):
            print("уже отклонено ранее: %s (%s)" % (r["id"], r["reason"]))
            return 0
    with open(REJECTED, "a", encoding="utf-8") as fh:
        fh.write("| %s | %s | %s | %s | %s |\n"
                 % (args.id, fp, date.today().isoformat(), args.by, args.reason))
    print("отклонено и записано: %s" % args.id)
    return 0


def cmd_check(args):
    problems = []
    cards = load_cards()
    seen = {}
    for c in cards:
        cid = c.get("id")
        if c.get("_error"):
            problems.append("%s: не разбирается (%s)" % (c["_path"], c["_error"]))
            continue
        for f in REQUIRED:
            if not c.get(f):
                problems.append("%s: нет обязательного поля %s" % (cid, f))
        if c.get("status") not in ("proposed", "confirmed", "superseded"):
            problems.append("%s: недопустимый status=%r" % (cid, c.get("status")))
        if os.path.basename(c["_path"]) != "%s.md" % cid:
            problems.append("%s: имя файла не совпадает с id" % c["_path"])
        if cid in seen:
            problems.append("%s: дублирующийся id (%s)" % (cid, seen[cid]))
        seen[cid] = c["_path"]
        sigs = all_signatures(c)
        if not sigs:
            problems.append("%s: нет ни одной подписи" % cid)
        for s in sigs:
            try:
                re.compile(s)
            except re.error as e:
                problems.append("%s: подпись %r не компилируется (%s)" % (cid, s, e))
        if "Что карточка НЕ отменяет" not in (c.get("_body") or ""):
            problems.append("%s: нет раздела «Что карточка НЕ отменяет» "
                            "(нижняя граница качества)" % cid)

    rej = {r["fingerprint"] for r in read_rejected() if r["fingerprint"] != "-"}
    for c in cards:
        if c.get("_error"):
            continue
        fp = fingerprint(all_signatures(c))
        if fp in rej:
            problems.append("%s: подпись совпадает с ОТКЛОНЁННОЙ карточкой (%s)"
                            % (c.get("id"), fp))

    print("карточек: %d, отклонений: %d" % (len(cards), len(read_rejected())))
    if problems:
        for p in problems:
            print("  ✗ %s" % p)
        return 1
    print("  ✓ база знаний в порядке")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("id"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("match"); p.add_argument("corpus"); p.set_defaults(fn=cmd_match)
    p = sub.add_parser("new"); p.add_argument("id"); p.set_defaults(fn=cmd_new)
    p = sub.add_parser("fingerprint"); p.add_argument("target")
    p.set_defaults(fn=cmd_fingerprint)
    p = sub.add_parser("reject")
    p.add_argument("id"); p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--signature", action="append", default=[])
    p.set_defaults(fn=cmd_reject)
    sub.add_parser("rejected").set_defaults(fn=cmd_rejected)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
