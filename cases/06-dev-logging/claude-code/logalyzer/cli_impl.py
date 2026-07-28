import json, re, time
from datetime import timedelta
from pathlib import Path
from logalyzer.masking import Masker
from logalyzer.ingest import read_all_with_stats
from logalyzer.correlate import related, related_window
from logalyzer.evidence import EvidenceBundle
from logalyzer.rules_engine import load_rules, evaluate
from logalyzer.coderef import (is_code_dir, suggest_repos, resolve_mode,
                               extract_identifiers, locate, gate)
from logalyzer.report import build, render_ru
from logalyzer.runlog import RunLog
from logalyzer.formats import (FormatStore, validate_descriptor, top_skeletons,
                               parse_iso_dt, format_utc_iso)

_INFERENCE_INSTRUCTIONS = (
    "For each entry in 'files': derive a format descriptor JSON "
    "{line_regex (a Python regex with named groups: 'ts' required, optional "
    "level/service/logger/msg/thread), ts_format (an strptime format string, "
    "or \"iso\"/\"epoch_s\"/\"epoch_ms\")}, save it to a file (e.g. "
    "descriptor.json), then write that entry's own sample_lines to a second "
    "file, one line per line, exactly as given (e.g. sample.txt), and run: "
    "python3 -m logalyzer register-format descriptor.json "
    "--fingerprint <fp from this entry> --sample sample.txt -- then re-run "
    "the same investigate/stats command. Acceptance thresholds enforced by "
    "register-format: the ts group must match and parse on >=90% of the "
    "sample's non-blank lines; if a 'level' group is present, it must "
    "normalize to a known level (DEBUG/INFO/WARN/ERROR) on >=50% of its own "
    "matches; parsed sample timestamps must fall within calendar years "
    "2000-2100 and span under 366 days (guards against a ts_format/regex "
    "combination that 'matches' but parses to garbage, e.g. epoch math on "
    "the wrong unit); line_regex is capped at 2000 characters and any "
    "match is abandoned past a 2-second time budget (both guard against "
    "catastrophic regex backtracking). Exit codes: 0 = saved, hit_rates "
    "printed; 1 = validation failed, the JSON 'reason' explains which "
    "threshold was missed -- fix the regex/ts_format and retry; "
    "2 = malformed command-line arguments.")

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

_DURATION_RX = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)$")
_DURATION_MULT = {"s": 1.0, "m": 60.0, "h": 3600.0}
_DEFAULT_WINDOW = "5m"

def _parse_duration_seconds(value):
    """Parse a duration like '5m', '90s', '1h' (Normalization v2's
    --around/--window) into seconds. Returns None on anything else --
    callers turn that into a usage error, not a crash."""
    m = _DURATION_RX.match((value or "").strip())
    if not m:
        return None
    return float(m.group(1)) * _DURATION_MULT[m.group(2)]

def _unresolved_needs_inference(ingest_stats):
    """CRITICAL 1 safety net: a fingerprint already present in FormatStore
    must never be re-offered by investigate's exit-4 payload -- once
    registered, that dialect is solved; re-requesting it would deadloop
    the handshake (an agent register-formatting the exact same descriptor
    forever) if any edge case slips a "learned:" file past the ts-vs-ok
    rate gate fix in ingest.py. Filters the raw ingest-stats list down to
    fingerprints the store genuinely has no entry for."""
    store = FormatStore()
    return [f for f in ingest_stats.get("needs_inference", [])
            if store.get(f["fingerprint"]) is None]

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
    # Normalization v2: needs_inference always prints inline here (stats
    # never exits non-zero over it -- only `investigate` can exit 4, and
    # only when the affected files would leave the timeline empty).
    print(json.dumps({"records_total": len(recs), "unparsed": unparsed,
                      "by_service": by_service,
                      "files": stats["files"], "skipped": stats["skipped"],
                      "needs_inference": stats.get("needs_inference", [])},
                     ensure_ascii=False, indent=2))
    return 0

def cmd_register_format(argv):
    if not argv or argv[0].startswith("--"):
        print("usage: register-format <descriptor.json> --fingerprint <fp> "
             "(--sample <file> | --sample-from-stats <stats.json>)")
        return 2
    descriptor_path = Path(argv[0])
    rest = argv[1:]
    fp = _arg(rest, "--fingerprint")
    sample_path = _arg(rest, "--sample")
    sample_from_stats = _arg(rest, "--sample-from-stats")
    if not fp:
        print("--fingerprint required"); return 2
    if not sample_path and not sample_from_stats:
        print("--sample or --sample-from-stats required"); return 2
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("cannot read descriptor %s: %s" % (descriptor_path, e)); return 1
    if sample_path:
        try:
            sample_lines = Path(sample_path).read_text(encoding="utf-8").splitlines()
        except OSError as e:
            print("cannot read sample %s: %s" % (sample_path, e)); return 1
    else:
        try:
            stats_doc = json.loads(Path(sample_from_stats).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print("cannot read stats %s: %s" % (sample_from_stats, e)); return 1
        entry = next((e for e in stats_doc.get("needs_inference", [])
                     if e.get("fingerprint") == fp), None)
        if not entry:
            print("no needs_inference entry for fingerprint %s in %s" %
                 (fp, sample_from_stats))
            return 1
        sample_lines = entry.get("sample_lines", [])
    t0 = time.monotonic()
    ok, hit_rates, reason = validate_descriptor(descriptor, sample_lines)
    validate_seconds = time.monotonic() - t0
    if not ok:
        print(json.dumps({"ok": False, "fingerprint": fp, "reason": reason,
                          "hit_rates": hit_rates}, ensure_ascii=False, indent=2))
        return 1
    FormatStore().save(fp, descriptor, hit_rates, top_skeletons(sample_lines),
                       validate_seconds=validate_seconds)
    print(json.dumps({"ok": True, "fingerprint": fp, "hit_rates": hit_rates},
                     ensure_ascii=False, indent=2))
    return 0

def _resolve_correlation_basis(argv):
    """Normalization v2 -- time-frame correlation (IDs optional): validate
    and resolve exactly one correlation basis from argv. Returns
    (error_message_or_None, corr, since, until, service). error_message is
    a ready-to-print usage-error string (caller returns exit 2 on it); on
    success it is None and `corr` xor (`since` and `until`) is set --
    `since`/`until` are already canonicalized UTC ISO strings when the
    basis is a time window."""
    corr = _arg(argv, "--correlation-id")
    since_arg = _arg(argv, "--since")
    until_arg = _arg(argv, "--until")
    around_arg = _arg(argv, "--around")
    window_arg = _arg(argv, "--window")
    service = _arg(argv, "--service")

    if bool(since_arg) != bool(until_arg):
        return ("--since and --until must be given together", corr, None, None, service)
    since_until_given = bool(since_arg) and bool(until_arg)

    if window_arg and not around_arg:
        return ("--window requires --around", corr, None, None, service)

    if around_arg and since_until_given:
        return ("specify either --since/--until or --around/--window, not both",
                corr, None, None, service)

    window_basis_given = since_until_given or bool(around_arg)
    basis_count = (1 if corr else 0) + (1 if window_basis_given else 0)
    if basis_count == 0:
        return ("exactly one correlation basis required: --correlation-id, "
                "or --since/--until, or --around/--window", corr, None, None, service)
    if basis_count == 2:
        return ("exactly one correlation basis allowed: got both --correlation-id "
                "and a time window (--since/--until or --around/--window)",
                corr, None, None, service)

    if not window_basis_given:
        return (None, corr, None, None, service)  # correlation_id basis

    if since_until_given:
        since_raw, until_raw = since_arg, until_arg
    else:
        dur_seconds = _parse_duration_seconds(window_arg or _DEFAULT_WINDOW)
        if dur_seconds is None:
            return ("invalid --window duration %r (expected e.g. 5m, 90s, 1h)" %
                    (window_arg,), corr, None, None, service)
        try:
            center = parse_iso_dt(around_arg)
        except (ValueError, TypeError) as e:
            return ("invalid --around timestamp %r: %s" % (around_arg, e),
                    corr, None, None, service)
        half = timedelta(seconds=dur_seconds / 2.0)
        since_raw = format_utc_iso(center - half)
        until_raw = format_utc_iso(center + half)

    try:
        since_dt = parse_iso_dt(since_raw)
        until_dt = parse_iso_dt(until_raw)
    except (ValueError, TypeError) as e:
        return ("invalid --since/--until timestamp: %s" % e, corr, None, None, service)
    if since_dt > until_dt:
        return ("--since must not be after --until", corr, None, None, service)
    return (None, corr, format_utc_iso(since_dt), format_utc_iso(until_dt), service)

def cmd_investigate(argv):
    logs = _arg(argv, "--logs")
    if not logs:
        print("--logs required"); return 2
    err, corr, since, until, service = _resolve_correlation_basis(argv)
    if err:
        print(err); return 2
    window_basis = since is not None  # equivalently: until is not None

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

    masker = Masker()
    t = time.monotonic()
    recs, ingest_stats = read_all_with_stats(Path(logs), masker)
    stages = {"ingest_ms": int((time.monotonic() - t) * 1000)}
    t = time.monotonic()
    if window_basis:
        windowed, excluded_no_ts = related_window(recs, since, until, service)
        bundle = EvidenceBundle.build(windowed)
    else:
        excluded_no_ts = 0
        bundle = EvidenceBundle.build(related(recs, corr))
    stages["correlate_ms"] = int((time.monotonic() - t) * 1000)

    # Normalization v2: exit-4 inference handshake. Only when the files that
    # need inference are the REASON the timeline is empty -- if other files
    # already parsed cleanly and produced evidence for this correlation id,
    # proceed normally (the needs_inference files just get a limitations
    # note below) rather than blocking a perfectly good investigation.
    needs_inf = _unresolved_needs_inference(ingest_stats)
    if needs_inf and not bundle.items:
        payload = {
            "action": "format_inference_needed",
            "files": [{"file": f["file"], "fingerprint": f["fingerprint"],
                      "sample_lines": f["sample_lines"]} for f in needs_inf],
            "instructions": _INFERENCE_INSTRUCTIONS,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 4

    rl = RunLog(case_dir, argv)
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

    # Normalization v2: correlation_basis discloses HOW the timeline was
    # selected -- an id-based join (related()) or a time window
    # (related_window()) -- so the report never silently reads as if it
    # always came from a correlation_id. See report.render_ru's header.
    if window_basis:
        basis = {"kind": "time_window", "since": since, "until": until}
        if service:
            basis["service"] = service
        basis["excluded_no_ts"] = excluded_no_ts
    else:
        basis = {"kind": "correlation_id", "correlation_id": corr, "excluded_no_ts": 0}
    rep = build(matches, bundle, kept, mode,
               {"correlation_id": corr or "", "correlation_basis": basis})
    if window_basis:
        rep["limitations"].append(
            "Отбор записей для таймлайна выполнен по временному окну %s .. %s%s "
            "(без correlation_id); записей без разбираемой метки времени "
            "исключено из окна: %d." % (
                since, until, (", сервис `%s`" % service) if service else "",
                excluded_no_ts))
    if needs_inf:
        rep["limitations"].append(
            "Часть файлов не удалось разобрать штатными эвристиками (%s) -- "
            "доказательная база по ним может быть неполной; обучите формат "
            "через `register-format` (fingerprint: %s)." % (
                ", ".join(f["file"] for f in needs_inf),
                ", ".join(f["fingerprint"] for f in needs_inf)))
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path:
        Path(md_path).write_text(render_ru(rep), encoding="utf-8")
    unparsed = sum(1 for r in recs if r.parse_quality == "unparsed")
    rl.summary(cmd="investigate", correlation_id=corr or "", mode=mode,
               correlation_basis=basis["kind"],
               rules_matched=[m["rule_id"] for m in matches],
               coderefs_kept=len(kept), coderefs_rejected=rejected,
               records_total=len(recs), records_unparsed=unparsed,
               stage_ms=stages, rubric_sha=catalog["rubric_sha"])
    print("report: %s (mode=%s, rules=%s)" % (out, mode,
          ",".join(m["rule_id"] for m in matches) or "none"))
    return 0
