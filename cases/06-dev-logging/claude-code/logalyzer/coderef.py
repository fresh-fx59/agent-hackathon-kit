from pathlib import Path

_MARKERS_FILES = ("pom.xml", "build.gradle", "pyproject.toml", "requirements.txt")
_MARKERS_DIRS = (".git", "src", "services")

def is_code_dir(p):
    p = Path(p)
    try:
        if not p.is_dir(): return False
    except OSError:
        return False
    try:
        return (any((p / f).is_file() for f in _MARKERS_FILES)
                or any((p / d).is_dir() for d in _MARKERS_DIRS))
    except OSError:
        return False

def _children(p, max_depth):
    out, frontier = [], [(p, 0)]
    while frontier:
        cur, depth = frontier.pop(0)
        if depth >= max_depth: continue
        try:
            subs = [c for c in sorted(cur.iterdir())
                    if c.is_dir() and not c.name.startswith(".") and c.name != "node_modules"]
        except OSError:
            continue
        for c in subs:
            out.append(c); frontier.append((c, depth + 1))
    return out

def suggest_repos(start, max_depth=3):
    start = Path(start).resolve()
    candidates = [start]
    parent = start
    for _ in range(3):
        parent = parent.parent
        candidates.append(parent)
        try:
            candidates.extend(c for c in sorted(parent.iterdir()) if c.is_dir())
        except OSError:
            pass
        if parent == parent.parent: break
    candidates.extend(_children(start, max_depth))
    seen, out = set(), []
    for c in candidates:
        c = c.resolve()
        if c in seen: continue
        seen.add(c)
        if is_code_dir(c): out.append(c)
    return out

def resolve_mode(explicit_mode, repos, suggestions):
    if explicit_mode == "ops":
        return "ops", None
    if repos:
        return "dev", None
    if explicit_mode == "dev":
        return "ask", _clarification(suggestions)
    return "ask", _clarification(suggestions)

def _clarification(suggestions):
    return {
        "question": ("Я не вижу исходного кода рядом с текущей директорией. "
                     "Укажите путь к коду сервисов (--repo <путь>) — или скажите "
                     "«без кода», тогда отчёт будет на уровне сервисов (режим DevOps)."),
        "suggestions": [str(s) for s in suggestions],
        "how_to_answer": "повторите команду с --repo <путь> или с --mode ops",
    }
