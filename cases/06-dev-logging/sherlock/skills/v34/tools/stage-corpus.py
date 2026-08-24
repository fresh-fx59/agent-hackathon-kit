#!/usr/bin/env python3
"""Normalize unsafe staged corpus names before Qwen sees them."""
import argparse
import hashlib
import os
import re
import tempfile
import unicodedata
from pathlib import Path


def safe_name(name):
    normalized = unicodedata.normalize("NFC", name)
    normalized = normalized.replace("%", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = "".join(
        ch if ch.isalnum() or ch in "._-" else "-" for ch in normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-.")
    return normalized or "log"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


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


def stage(corpus, map_path):
    corpus = corpus.resolve(strict=True)
    if not corpus.is_dir() or corpus.is_symlink():
        raise ValueError("corpus must be a real directory")
    files = sorted(
        path for path in corpus.rglob("*")
        if path.is_file() and not path.is_symlink()
        and "rendered" not in path.relative_to(corpus).parts)
    rendered = corpus / "rendered"
    used = {path.name for path in rendered.iterdir()} if rendered.is_dir() else set()
    rows = []
    moved = 0
    for source in files:
        rel = source.relative_to(corpus).as_posix()
        name = safe_name(source.name)
        unsafe = "%" in rel or any(ch.isspace() for ch in rel)
        if unsafe:
            rendered.mkdir(exist_ok=True)
            candidate = name
            if candidate in used:
                stem, suffix = os.path.splitext(name)
                candidate = "%s-%s%s" % (
                    stem, hashlib.sha256(rel.encode("utf-8")).hexdigest()[:8], suffix)
            used.add(candidate)
            target = rendered / candidate
            os.replace(source, target)
            try:
                source.parent.rmdir()
            except OSError:
                pass
            safe_rel = target.relative_to(corpus).as_posix()
            moved += 1
        else:
            target = source
            safe_rel = rel
        rows.append((rel, safe_rel, digest(target)))
    body = "source_relpath\tsafe_relpath\tsha256\n" + "".join(
        "%s\t%s\t%s\n" % row for row in rows)
    atomic_text(map_path, body)
    return {"files": len(files), "moved": moved, "path_map": str(map_path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus")
    parser.add_argument("--map", required=True)
    args = parser.parse_args()
    result = stage(Path(args.corpus), Path(args.map))
    print(__import__("json").dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
