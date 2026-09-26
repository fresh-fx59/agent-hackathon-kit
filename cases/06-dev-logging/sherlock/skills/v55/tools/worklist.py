#!/usr/bin/env python3
"""A CURSOR over the worklist, so the child never reads the ledger whole.

WHY THIS EXISTS, in numbers measured on the paid runs. `work/worklist.tsv` is 250
rows / 118,488 bytes; the mean row is 440.9 characters and column 6 `запись` — a
raw log excerpt — is 313 of them, **71 % of every row**. The gate never validates
it: `triagecheck.read_worklist` parses it into the row dict and it appears nowhere
else except inside `CONTENT_FIELDS`, the list of field names a bulk rule is
FORBIDDEN to use — deliberately, «so nobody can bulk-close rows by
pattern-matching text nobody read». The columns the gate does check average 124
characters.

So the old read path paid a 25,060-character truncated read (the stock 25,000-char
cap, hit dead on) for a file most of which no gate reads, and then asked for the
next page. Four such pages of the worklist and six of the map came to ~76,908
tokens, 23 % of a peak request.

A batch of 20 rows without the excerpt is about 2,480 bytes, and a full pass over
250 rows about 32,000 — against ~100,244 bytes for ONE partial pass the old way.

THREE RULES THIS TOOL MAY NOT BREAK, each one something the arm already depends on:

  1. IT NEVER LOSES A COLUMN. `citecheck` and `triagecheck` read the FULL
     `worklist.tsv` and their contract is untouched — only the child's READ path
     changes. A verdict write replaces column 2 of one named row and nothing else.
  2. IT HANDS OUT ROWS BY AXIS ON DEMAND. A bulk rule closes a CLASS
     (reference/bulk-closure.md), and a class cannot be recognised in rows that
     were never seen together; `--axis` mirrors the `view-<axis>-NN.tsv` slices the
     brief already mandates.
  3. IT REFUSES RATHER THAN GUESSING. An unknown id, a placeholder verdict, a cell
     containing a tab (which would forge a column) and a missing work directory are
     all non-zero exits with a diagnosis. A tool that silently does nothing is how
     a run believes it closed rows it did not.

The cursor needs no state file: "unresolved" is a property of the ledger itself — a
verdict cell that is empty or starts with `?`. One source of truth, so a lost state
file cannot rewind the investigation.

THE WITNESS (v42 fix 8). The three rules above were documented, tooled and NOT
ENFORCED. The independent review of the paid run 20260827T173511Z-v41 found that the
parent session edited ledger row `g228` directly, with a file-editing tool, after
`SKILL.md` step 2 said «read the worklist through the CURSOR, never as a file». A
verdict that never went through `apply_verdicts` skipped every refusal rule 3
promises — unknown id, placeholder verdict, a tab that forges a column — and no
downstream gate could tell the difference, because `triagecheck` and `citecheck
--ledger` read only the resulting file. Two files with the same bytes have the same
grade whoever wrote them.

So the write path gains a WITNESS, and only this tool can write it:

  * `work/<ledger>.provenance.jsonl` — append-only, one JSON entry per cursor call
    that changed the ledger. Entry 0 (`kind: genesis`) snapshots every row id and
    its verdict cell as the cursor first saw them; every later entry carries the
    exact `{id: cell}` it wrote and the sha256 of the WHOLE ledger as it stood
    after the write.
  * each entry commits to its predecessor: `prev` is the previous entry's `link`,
    and `link` is the sha256 of this entry's canonical JSON. One chain, so an entry
    cannot be dropped, reordered or inserted in the middle without breaking it.
  * `work/.worklist-witness.key` — 32 random bytes, mode 0600, created by this tool
    at the first write. Every entry also carries `mac = HMAC-SHA256(key, link)`.

WHY THIS IS NOT TRIVIALLY FAKEABLE. Recomputing the chain is arithmetic anyone can
do; the MAC is not, without the key. So a hand edit followed by a hand-written
journal entry — the obvious way to "also write the bookkeeping" — fails the MAC and
is refused. Forging requires reading a key file that no instruction, brief or
reference mentions and that no artefact of the run contains. And an ACCIDENTAL
bypass — the case that actually happened — cannot produce any entry at all, so it is
caught every time.

WHAT AN ADVERSARIAL AGENT CAN STILL DO, said plainly rather than hidden (same
honesty as `logmap.write_worklist_manifest`): this process can read every file it
can write. An agent that decides to forge can `cat` the key, recompute the chain and
mint a valid entry; or delete the journal and `reseal`. Neither is prevented and no
key in this sandbox would prevent it. What the witness buys is the shape the review
asked for: the cheap accidental bypass is impossible, and the expensive deliberate
one has to touch a hidden key or leave a `reseal` entry naming itself in the
delivered work directory. Tamper-EVIDENT, not tamper-proof.

RECOVERY, because a refusal that can only be satisfied by deleting the row is the
v37 defect this project already has a name for. A verdict that is right but was
written the wrong way is re-submitted through the cursor, verbatim — no triage work
is lost, and `verify` prints the exact `verdict --from-stdin` command with the cell
already in it. `reseal` exists for the other case, a witness genuinely lost in a
handoff, and it REFUSES while the journal is intact and valid: it can rebuild a
missing baseline, it can never launder a detected off-cursor write.
"""
import argparse
import datetime
import glob
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile

PLACEHOLDER = "?"
COLUMNS = 6
#: id, вердикт, ось, ссылка, частота — everything the gate reads. `запись`
#: (column 6) is deliberately withheld: see the module docstring.
BATCH_COLUMNS = 5


def read_rows(path):
    """-> (header_lines, [row_cells]). Byte-faithful: nothing is normalised."""
    header, rows = [], []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("#"):
                header.append(line)
                continue
            if not line.strip():
                continue
            cells = line.split("\t")
            cells += [""] * (COLUMNS - len(cells))
            rows.append(cells)
    return header, rows


def worklist_path(work, name=None):
    work = os.path.abspath(work)
    if not os.path.isdir(work):
        raise SystemExit("✗ no work directory at %s" % work)
    path = os.path.join(work, name or "worklist.tsv")
    if not os.path.exists(path):
        raise SystemExit("✗ no %s in %s" % (name or "worklist.tsv", work))
    return path


def unresolved(cells):
    cell = (cells[1] or "").strip()
    return (not cell) or cell.startswith(PLACEHOLDER)


def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                              dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# the witness
# ---------------------------------------------------------------------------
#: append-only, one entry per ledger-changing cursor call
JOURNAL_SUFFIX = ".provenance.jsonl"
KEY_NAME = ".worklist-witness.key"
SCHEMA = 1
#: the fields the link hash commits to — the whole entry minus link/mac
LINKED = ("schema", "seq", "at", "kind", "writes", "rows", "ledger", "prev",
          "note")


def journal_path(ledger):
    base = os.path.basename(ledger)
    if base.endswith(".tsv"):
        base = base[:-4]
    return os.path.join(os.path.dirname(os.path.abspath(ledger)),
                        base + JOURNAL_SUFFIX)


def key_path(ledger):
    return os.path.join(os.path.dirname(os.path.abspath(ledger)), KEY_NAME)


def load_key(ledger, create=False):
    path = key_path(ledger)
    if not os.path.exists(path):
        if not create:
            return None
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(os.urandom(32).hex() + "\n")
    try:
        raw = io.open(path, encoding="utf-8").read().strip()
        return bytes.fromhex(raw)
    except (ValueError, OSError):
        return None


def canonical(entry):
    return json.dumps({k: entry.get(k) for k in LINKED}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def link_of(entry):
    return hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest()


def mac_of(key, link):
    return hmac.new(key, link.encode("ascii"), hashlib.sha256).hexdigest()


def ledger_digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_journal(ledger):
    """-> (entries, [complaints]). A line that will not parse is a complaint."""
    path = journal_path(ledger)
    if not os.path.exists(path):
        return [], []
    entries, bad = [], []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                bad.append("%s:%d — запись журнала не разбирается как JSON"
                           % (os.path.basename(path), n))
                continue
            if not isinstance(row, dict):
                bad.append("%s:%d — запись журнала не объект" % (os.path.basename(path), n))
                continue
            entries.append(row)
    return entries, bad


def append_entry(ledger, kind, writes=None, rows=None, note=""):
    """Append one witnessed entry. Only this function ever writes the journal."""
    key = load_key(ledger, create=True)
    if key is None:
        raise SystemExit("✗ ключ свидетеля %s не читается — курсор не может "
                         "записать вердикт без свидетеля" % key_path(ledger))
    entries, bad = read_journal(ledger)
    prev = "" if not entries else (entries[-1].get("link") or "")
    entry = {
        "schema": SCHEMA,
        "seq": len(entries),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kind": kind,
        "writes": writes or {},
        "rows": rows,
        "ledger": ledger_digest(ledger),
        "prev": prev,
        "note": note or "",
    }
    entry["link"] = link_of(entry)
    entry["mac"] = mac_of(key, entry["link"])
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with io.open(journal_path(ledger), "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return entry


def verdict_snapshot(ledger):
    _, rows = read_rows(ledger)
    out = {}
    for cells in rows:
        rid = (cells[0] or "").strip()
        if rid:
            out.setdefault(rid, cells[1])
    return out


def ensure_genesis(ledger):
    """Seal the baseline the first time the cursor touches this ledger.

    The baseline is «the ledger as the cursor first saw it», not «as logmap wrote
    it»: the cursor cannot testify about a state that predates it. In the arm that
    is the same thing, because step 2 reaches for `next` before it writes anything.
    """
    entries, _bad = read_journal(ledger)
    if entries:
        return None
    return append_entry(ledger, "genesis", rows=verdict_snapshot(ledger),
                        note="базовое состояние на первый вызов курсора")


def fix_command(work, updates):
    """The invocation that WOULD have been legitimate, ready to paste."""
    lines = ["python3 <SKILL_BASE_DIR>/tools/worklist.py verdict --work %s "
             "--from-stdin <<'EOF'" % work]
    for rid, cell in updates:
        lines.append("%s\t%s" % (rid, cell))
    lines.append("EOF")
    return lines


def audit(ledger):
    """Grade the write path of one ledger. Never raises; every failure is data.

    state:
      ok           — the chain is intact and the ledger is exactly what it says
      unwitnessed  — no journal and no logmap manifest: graded, not blocked
                     (same rule as citecheck.worklist_removed — a gate that fails
                     on fixtures gets switched off)
      broken       — ledger_witness_broken: missing, truncated, unparseable,
                     unchained, wrong MAC, or bytes that no entry accounts for
      off_cursor   — ledger_write_off_cursor: a named row carries a verdict the
                     cursor never wrote
    """
    d = {"ledger": os.path.abspath(ledger), "journal": journal_path(ledger),
         "state": "ok", "entries": 0, "broken": [], "off_cursor": [],
         "resealed": [], "fix": []}
    if not os.path.exists(ledger):
        d["state"] = "broken"
        d["broken"].append("нет леджера %s" % ledger)
        return d
    work = os.path.dirname(os.path.abspath(ledger))
    entries, bad = read_journal(ledger)
    d["entries"] = len(entries)
    d["broken"].extend(bad)
    have_journal = os.path.exists(journal_path(ledger))
    manifest = os.path.join(work, "worklist.manifest.json")
    current = verdict_snapshot(ledger)
    closed = [rid for rid, cell in current.items() if not unresolved([rid, cell])]

    if not have_journal:
        if os.path.exists(manifest) and closed:
            # logmap built this directory, so step 2 was told to use the cursor.
            # A journal that is not here was either never created (every verdict
            # is off-cursor) or removed (the same, plus a removal).
            d["state"] = "broken"
            d["broken"].append(
                "журнала %s нет, а manifest от logmap есть и %d строк закрыто: "
                "вердикты написаны не курсором"
                % (os.path.basename(journal_path(ledger)), len(closed)))
            return d
        d["state"] = "unwitnessed"
        return d
    if not entries:
        d["state"] = "broken"
        d["broken"].append("журнал %s пуст" % os.path.basename(journal_path(ledger)))
        return d

    key = load_key(ledger)
    if key is None:
        d["state"] = "broken"
        d["broken"].append("ключ свидетеля %s отсутствует или не читается"
                           % KEY_NAME)
        return d

    # the chain, entry by entry
    prev = ""
    expected, genesis_seen = {}, False
    for i, e in enumerate(entries):
        seq = e.get("seq")
        if e.get("schema") != SCHEMA:
            d["broken"].append("запись %d: schema=%r, ожидалась %d"
                               % (i, e.get("schema"), SCHEMA))
        if seq != i:
            d["broken"].append("запись %d: seq=%r — цепочка переставлена"
                               % (i, seq))
        if (e.get("prev") or "") != prev:
            d["broken"].append("запись %d: prev не совпадает с предыдущей "
                               "записью — звено вырвано" % i)
        want_link = link_of(e)
        if e.get("link") != want_link:
            d["broken"].append("запись %d: link не совпадает с содержимым "
                               "записи" % i)
        elif e.get("mac") != mac_of(key, want_link):
            d["broken"].append(
                "запись %d: MAC не совпадает — эту запись сделал не курсор" % i)
        prev = e.get("link") or ""
        kind = e.get("kind")
        if kind in ("genesis", "reseal"):
            rows = e.get("rows")
            if not isinstance(rows, dict):
                d["broken"].append("запись %d: %s без снимка строк"
                                   % (i, kind))
            else:
                expected = dict(rows)
                genesis_seen = True
            if kind == "reseal":
                d["resealed"].append({"seq": i, "at": e.get("at"),
                                      "note": e.get("note") or "",
                                      "rows": len(e.get("rows") or {})})
        elif kind == "verdict":
            writes = e.get("writes")
            if not isinstance(writes, dict):
                d["broken"].append("запись %d: verdict без writes" % i)
            else:
                expected.update(writes)
        else:
            d["broken"].append("запись %d: неизвестный kind=%r" % (i, kind))
    if not genesis_seen:
        d["broken"].append("в журнале нет базового снимка (genesis/reseal)")
    if d["broken"]:
        d["state"] = "broken"
        return d

    # replay vs. reality
    off = []
    for rid in sorted(current):
        cell = current[rid]
        if rid not in expected:
            off.append({"id": rid, "в леджере": cell, "по журналу": None,
                        "что": "строки не было в снимке"})
        elif expected[rid] != cell:
            off.append({"id": rid, "в леджере": cell,
                        "по журналу": expected[rid],
                        "что": "вердикт написан не курсором"})
    vanished = sorted(set(expected) - set(current))
    if vanished:
        d["broken"].append("строк(и) исчезли из леджера: %s"
                           % ", ".join(vanished[:8]))
    if off:
        d["state"] = "off_cursor"
        d["off_cursor"] = off
        d["fix"] = fix_command(work, [(o["id"], o["в леджере"]) for o in off])
        return d
    if vanished:
        d["state"] = "broken"
        return d
    # nothing attributable to a row: the bytes still have to match
    last = entries[-1].get("ledger")
    if last != ledger_digest(ledger):
        d["state"] = "broken"
        d["broken"].append(
            "байты леджера не совпадают с последней записью журнала, а колонка "
            "вердикта цела: изменено что-то вне вердиктов (строка, ось, ссылка, "
            "запись) или файл переписан целиком")
    return d


def render_audit(d):
    out = ["СВИДЕТЕЛЬ КУРСОРА — кто писал в леджер",
           "  леджер: %s" % d["ledger"],
           "  журнал: %s (записей: %d)"
           % (os.path.basename(d["journal"]), d["entries"])]
    if d["state"] == "unwitnessed":
        out.append("  свидетеля нет и manifest от logmap отсутствует — "
                   "рабочий каталог собран не logmap, проверка не делается")
        return "\n".join(out)
    for r in d["resealed"]:
        out.append("  ⚠ ПЕЧАТЬ СВИДЕТЕЛЯ ПЕРЕСТАВЛЕНА (запись %d, %s): %s — "
                   "принято строк: %d"
                   % (r["seq"], r["at"], r["note"] or "без причины", r["rows"]))
    if d["state"] == "ok":
        out.append("  ✓ каждый вердикт в леджере написан курсором")
        return "\n".join(out)
    if d["state"] == "broken":
        out.append("  ✗ ledger_witness_broken — свидетель не читается, значит "
                   "проверить происхождение вердиктов нечем:")
        for b in d["broken"]:
            out.append("     %s" % b)
        out.append("  Свидетель сломан — это отказ, а не пропуск. Если журнал "
                   "потерян при передаче, восстанови базу и скажи, почему:")
        out.append("     python3 <SKILL_BASE_DIR>/tools/worklist.py reseal "
                   "--work %s --reason '<что случилось>'"
                   % os.path.dirname(d["ledger"]))
        out.append("  reseal откажет, пока журнал цел: переставить печать, "
                   "чтобы скрыть правку в обход курсора, нельзя.")
        return "\n".join(out)
    out.append("  ✗ ledger_write_off_cursor — %d строк(а) закрыты в обход "
               "курсора:" % len(d["off_cursor"]))
    for o in d["off_cursor"][:12]:
        out.append("     %-10s %s" % (o["id"], o["что"]))
        out.append("        в леджере: %s" % (o["в леджере"] or "")[:120])
        out.append("        по журналу: %s"
                   % ("—" if o["по журналу"] is None
                      else (o["по журналу"] or "")[:120]))
    if len(d["off_cursor"]) > 12:
        out.append("     … и ещё %d" % (len(d["off_cursor"]) - 12))
    out.append("  СТРОКУ НЕ УДАЛЯЙ И ВЕРДИКТ НЕ СТИРАЙ — разбор уже сделан. "
               "Пропусти тот же текст через курсор, дословно:")
    for line in d["fix"]:
        out.append("     %s" % line)
    return "\n".join(out)



def cmd_next(args):
    path = worklist_path(args.work, getattr(args, "ledger", None))
    ensure_genesis(path)
    _, rows = read_rows(path)
    picked = []
    for cells in rows:
        if not unresolved(cells):
            continue
        if args.axis and (cells[2] or "").strip() != args.axis:
            continue
        picked.append(cells)
        if len(picked) >= args.batch:
            break
    out = [
        "# batch of %d unresolved row(s)%s — columns: id, вердикт, ось, ссылка, "
        "частота" % (len(picked), (" on axis %s" % args.axis) if args.axis else ""),
        "# the record excerpt is NOT here on purpose: no gate reads it. Need the "
        "raw record? open the corpus line named in `ссылка`, or grep "
        "worklist.tsv for that id.",
        "# write verdicts back with: worklist.py verdict --work %s --from-stdin "
        "(id<TAB>cell per line)" % os.path.abspath(args.work),
    ]
    for cells in picked:
        out.append("\t".join(cells[:BATCH_COLUMNS]))
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def apply_verdicts(path, updates):
    """updates: {id: cell}. Refuses before writing anything."""
    header, rows = read_rows(path)
    index = {}
    for i, cells in enumerate(rows):
        index.setdefault((cells[0] or "").strip(), i)
    missing = [rid for rid in updates if rid not in index]
    if missing:
        raise SystemExit("✗ no such row id: %s" % ", ".join(sorted(missing)[:8]))
    for rid, cell in updates.items():
        if "\t" in cell or "\n" in cell:
            raise SystemExit("✗ verdict for %s contains a tab or newline — that "
                             "would forge a column" % rid)
        if not cell.strip() or cell.strip().startswith(PLACEHOLDER):
            raise SystemExit("✗ verdict for %s is still a placeholder (%r) — a "
                             "row is closed by a letter with evidence, not by an "
                             "empty cell" % (rid, cell))
    for rid, cell in updates.items():
        rows[index[rid]][1] = cell
    text = "".join(line + "\n" for line in header)
    text += "".join("\t".join(cells) + "\n" for cells in rows)
    atomic_write(path, text)
    return len(updates)


def cmd_verdict(args):
    path = worklist_path(args.work, getattr(args, "ledger", None))
    ensure_genesis(path)
    updates = {}
    if args.from_stdin:
        for raw in sys.stdin.read().splitlines():
            if not raw.strip():
                continue
            parts = raw.split("\t", 1)
            if len(parts) != 2:
                raise SystemExit("✗ stdin line is not `id<TAB>cell`: %r"
                                 % raw[:80])
            updates[parts[0].strip()] = parts[1]
    else:
        if not args.id or args.cell is None:
            raise SystemExit("✗ verdict needs --id and --cell, or --from-stdin")
        updates[args.id.strip()] = args.cell
    n = apply_verdicts(path, updates)
    # The witness is written AFTER the ledger and only on success: an entry for a
    # write that was refused would testify to a state that never existed.
    entry = append_entry(path, "verdict", writes=updates)
    print(json.dumps({"written": n, "witness": entry["seq"]},
                     ensure_ascii=False))
    return 0


def cmd_status(args):
    path = worklist_path(args.work, getattr(args, "ledger", None))
    ensure_genesis(path)
    _, rows = read_rows(path)
    by_axis = {}
    open_rows = 0
    for cells in rows:
        axis = (cells[2] or "").strip() or "?"
        entry = by_axis.setdefault(axis, {"total": 0, "unresolved": 0})
        entry["total"] += 1
        if unresolved(cells):
            entry["unresolved"] += 1
            open_rows += 1
    print(json.dumps({"total": len(rows), "unresolved": open_rows,
                      "resolved": len(rows) - open_rows, "axes": by_axis},
                     ensure_ascii=False, sort_keys=True))
    return 0


def cmd_verify(args):
    path = worklist_path(args.work, getattr(args, "ledger", None))
    d = audit(path)
    if args.json:
        json.dump(d, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_audit(d) + "\n")
    return 0 if d["state"] in ("ok", "unwitnessed") else 1


def cmd_reseal(args):
    """Rebuild a LOST baseline. Refuses while the journal is intact.

    The escape hatch a broken witness needs, and deliberately not the escape hatch
    an off-cursor write wants: if the chain still verifies, the only way back to a
    clean state is to put the verdict through the cursor.
    """
    path = worklist_path(args.work, getattr(args, "ledger", None))
    d = audit(path)
    if d["state"] in ("ok", "off_cursor"):
        raise SystemExit(
            "✗ журнал свидетеля цел — печать не переставляют, чтобы закрыть "
            "правку в обход курсора.\n"
            "  Пропусти тот же вердикт через курсор:\n    "
            + "\n    ".join(d["fix"] or [
                "python3 <SKILL_BASE_DIR>/tools/worklist.py verdict --work %s "
                "--from-stdin" % os.path.abspath(args.work)]))
    if not (args.reason or "").strip():
        raise SystemExit("✗ reseal требует --reason: переставленная печать "
                         "остаётся в сданном каталоге навсегда, и читатель "
                         "должен знать, почему")
    old = journal_path(path)
    if os.path.exists(old):
        n = len(glob.glob(old + ".superseded-*"))
        os.replace(old, "%s.superseded-%d" % (old, n))
        prior = "прежний журнал сохранён как %s.superseded-%d" % (
            os.path.basename(old), n)
    else:
        prior = "прежнего журнала не было"
    entry = append_entry(path, "reseal", rows=verdict_snapshot(path),
                         note="%s · %s" % (args.reason.strip(), prior))
    print(json.dumps({"resealed": True, "rows": len(entry["rows"]),
                      "note": entry["note"]}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("next", help="hand out the next unresolved rows")
    p.add_argument("--work", required=True)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--axis", default="")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("verdict", help="write verdicts back into the ledger")
    p.add_argument("--work", required=True)
    p.add_argument("--id")
    p.add_argument("--cell")
    p.add_argument("--from-stdin", action="store_true")
    p.set_defaults(func=cmd_verdict)

    p = sub.add_parser("status", help="counts, overall and per axis")
    p.add_argument("--work", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("verify", help="who wrote the verdicts in the ledger")
    p.add_argument("--work", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("reseal", help="rebuild a LOST witness baseline")
    p.add_argument("--work", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_reseal)

    for sp in sub.choices.values():
        sp.add_argument("--ledger", default=None,
                        help="имя леджера в --work (по умолчанию worklist.tsv); "
                             "у каждого свой журнал <леджер>.provenance.jsonl")

    args = ap.parse_args()
    if args.command == "next" and args.batch <= 0:
        raise SystemExit("✗ --batch must be positive")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
