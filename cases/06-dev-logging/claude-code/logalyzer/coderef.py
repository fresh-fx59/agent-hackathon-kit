import re
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

_METHOD_SIG = re.compile(
    r"^\s*(?:public|private|protected)\s+[\w<>\[\],\s]+\s+(\w+)\s*\([^)]*\)\s*\{?\s*$")

def _strip_comments(text):
    """Strip Java comments while preserving line numbers.
    Replaces // and /* */ content with spaces, keeps newlines intact."""
    result = []
    in_block = False
    i = 0
    while i < len(text):
        if in_block:
            if i < len(text) - 1 and text[i:i+2] == '*/':
                result.append('  ')
                in_block = False
                i += 2
            else:
                result.append(' ' if text[i] != '\n' else '\n')
                i += 1
        else:
            if i < len(text) - 1 and text[i:i+2] == '/*':
                result.append('  ')
                in_block = True
                i += 2
            elif i < len(text) - 1 and text[i:i+2] == '//':
                result.append(' ')
                result.append(' ')
                i += 2
                while i < len(text) and text[i] != '\n':
                    result.append(' ')
                    i += 1
            else:
                result.append(text[i])
                i += 1
    return ''.join(result)

def extract_identifiers(bundle):
    exceptions, loggers = [], []
    for it in bundle.items:
        r = it["record"]
        exc = r.attrs.get("exception_type")
        if exc and exc not in exceptions: exceptions.append(exc)
        for m in re.finditer(r"\b([A-Z]\w+Exception)\b", r.body):
            if m.group(1) not in exceptions: exceptions.append(m.group(1))
        lg = r.attrs.get("logger")
        if lg and lg not in loggers: loggers.append(lg)
    return {"exceptions": exceptions, "loggers": loggers}

def _enclosing_method(lines, catch_idx):
    for i in range(catch_idx, -1, -1):
        m = _METHOD_SIG.match(lines[i])
        if m: return m.group(1)
    return ""

def locate(identifiers, repos):
    refs = []
    for repo in repos:
        repo = Path(repo)
        for java in sorted(repo.rglob("*.java")):
            text = java.read_text(encoding="utf-8", errors="replace")
            stripped = _strip_comments(text)
            lines = stripped.splitlines()
            for exc in identifiers["exceptions"]:
                for i, ln in enumerate(lines):
                    if re.search(r"catch\s*\(\s*%s\b" % re.escape(exc), ln):
                        method = _enclosing_method(lines, i)
                        confidence = "high" if method else "medium"
                        refs.append({
                            "file": str(java.relative_to(repo.parent)),
                            "method": method,
                            "line": i + 1,
                            "reason": "catch(%s)" % exc,
                            "confidence": confidence})
            for lg in identifiers["loggers"]:
                cls = lg.rsplit(".", 1)[-1]
                if java.stem == cls and not any(r2["file"] == str(java.relative_to(repo.parent))
                                                for r2 in refs):
                    refs.append({"file": str(java.relative_to(repo.parent)),
                                 "method": "", "line": 0,
                                 "reason": "logger %s" % lg, "confidence": "medium"})
    refs.sort(key=lambda r: 0 if r["confidence"] == "high" else 1)
    return refs

def gate(coderefs, repos):
    kept, rejected = [], 0
    for ref in coderefs:
        # Reject catch-derived refs with empty method (must have identified the method)
        if ref["reason"].startswith("catch(") and not ref["method"]:
            rejected += 1
            continue

        ok = False
        for repo in repos:
            p = Path(repo).parent / ref["file"]
            if p.is_file():
                if not ref["method"] or ref["method"] in p.read_text(
                        encoding="utf-8", errors="replace"):
                    ok = True
                break
        if ok: kept.append(ref)
        else: rejected += 1
    return kept, rejected
