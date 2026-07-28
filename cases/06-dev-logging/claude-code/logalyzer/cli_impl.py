import json, time
from pathlib import Path
from logalyzer.masking import Masker
from logalyzer.ingest import read_all, read_all_with_stats
from logalyzer.correlate import related
from logalyzer.evidence import EvidenceBundle
from logalyzer.rules_engine import load_rules, evaluate
from logalyzer.coderef import (is_code_dir, suggest_repos, resolve_mode,
                               extract_identifiers, locate, gate)
from logalyzer.report import build, render_ru
from logalyzer.runlog import RunLog

_CASE_DIR = Path(__file__).resolve().parents[1]

# A bare `.git` directory alone is enough for coderef.is_code_dir (used for
# explicit --repo validation, whose semantics stay unchanged), but it is NOT
# enough to silently auto-adopt cwd as the repo: that would bypass the
# clarification handshake for any ordinary git clone with no code in it yet.
# Auto-adopt requires an actual code marker.
_AUTO_ADOPT_MARKER_FILES = ("pom.xml", "build.gradle", "pyproject.toml", "requirements.txt")
_AUTO_ADOPT_MARKER_DIRS = ("src", "services")

def _has_non_git_marker(p):
    p = Path(p)
    try:
        return (any((p / f).is_file() for f in _AUTO_ADOPT_MARKER_FILES)
                or any((p / d).is_dir() for d in _AUTO_ADOPT_MARKER_DIRS))
    except OSError:
        return False

def _arg(argv, name, default=None):
    if name in argv:
        return argv[argv.index(name) + 1]
    return default

def _args_multi(argv, name):
    return [argv[i + 1] for i, a in enumerate(argv) if a == name]

def cmd_suggest(argv):
    start = Path(_arg(argv, "--from", "."))
    for p in suggest_repos(start):
        print(p)
    return 0

def cmd_stats(argv):
    logs = _arg(argv, "--logs")
    if not logs: print("--logs required"); return 2
    recs, stats = read_all_with_stats(Path(logs), Masker())
    by_service, unparsed = {}, 0
    for r in recs:
        by_service[r.service] = by_service.get(r.service, 0) + 1
        if r.parse_quality == "unparsed": unparsed += 1
    print(json.dumps({"records_total": len(recs), "unparsed": unparsed,
                      "by_service": by_service,
                      "files": stats["files"], "skipped": stats["skipped"]},
                     ensure_ascii=False, indent=2))
    return 0

def cmd_investigate(argv):
    logs, corr = _arg(argv, "--logs"), _arg(argv, "--correlation-id")
    if not logs or not corr:
        print("--logs and --correlation-id required"); return 2
    mode_arg = _arg(argv, "--mode", "auto")
    out = Path(_arg(argv, "--out", "report.json"))
    md_path = _arg(argv, "--md")
    case_dir = Path(_arg(argv, "--case-dir", str(_CASE_DIR)))
    suggest_from = Path(_arg(argv, "--suggest-from", "."))
    repos = [Path(p) for p in _args_multi(argv, "--repo") if is_code_dir(Path(p))]
    if not repos and mode_arg == "auto" and is_code_dir(Path.cwd()) and _has_non_git_marker(Path.cwd()):
        repos = [Path.cwd()]
        print("auto-adopted repo: %s" % Path.cwd())
    mode, clar = resolve_mode(mode_arg, repos, suggest_repos(suggest_from))
    if mode == "ask":
        print(json.dumps(clar, ensure_ascii=False, indent=2))
        return 3

    rl = RunLog(case_dir, argv)
    stages = {}
    t = time.monotonic(); masker = Masker()
    recs = read_all(Path(logs), masker); stages["ingest_ms"] = int((time.monotonic() - t) * 1000)
    t = time.monotonic()
    bundle = EvidenceBundle.build(related(recs, corr))
    stages["correlate_ms"] = int((time.monotonic() - t) * 1000)
    t = time.monotonic()
    catalog = load_rules(_CASE_DIR / "rules" / "rules.json")
    matches = evaluate(catalog, bundle)
    stages["rules_ms"] = int((time.monotonic() - t) * 1000)
    for m in matches:
        rl.event("rule_match", rule_id=m["rule_id"], evidence_ids=m["evidence_ids"])
    kept, rejected = [], 0
    if mode == "dev":
        t = time.monotonic()
        refs = locate(extract_identifiers(bundle), repos)
        kept, rejected = gate(refs, repos)
        stages["coderef_ms"] = int((time.monotonic() - t) * 1000)
        rl.event("coderef", kept=len(kept), rejected=rejected)
    rep = build(matches, bundle, kept, mode, {"correlation_id": corr})
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path:
        Path(md_path).write_text(render_ru(rep), encoding="utf-8")
    unparsed = sum(1 for r in recs if r.parse_quality == "unparsed")
    rl.summary(cmd="investigate", correlation_id=corr, mode=mode,
               rules_matched=[m["rule_id"] for m in matches],
               coderefs_kept=len(kept), coderefs_rejected=rejected,
               records_total=len(recs), records_unparsed=unparsed,
               stage_ms=stages, rubric_sha=catalog["rubric_sha"])
    print("report: %s (mode=%s, rules=%s)" % (out, mode,
          ",".join(m["rule_id"] for m in matches) or "none"))
    return 0
