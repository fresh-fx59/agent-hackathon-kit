#!/usr/bin/env python3
"""Create a durable resume receipt and an honest report skeleton."""
import argparse
import datetime
import hashlib
import json
import os
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
    if not report.exists() or not report.read_text(encoding="utf-8", errors="replace").strip():
        atomic_text(report,
                    "# Отчёт Sherlock\n\n"
                    "Состояние: частичный отчёт; синтез ещё не завершён.\n\n"
                    "Разобрано строк рабочего списка: %d из %d.\n" % (resolved, total))
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init",))
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    print(json.dumps(init(Path(args.work)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
