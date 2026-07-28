"""Normalization v2 -- learned-format store + descriptor validation/apply.

Extraction order (see docs/specs/2026-07-28-design.md, "Normalization v2"):
1. Learned-format cache: `formats.d/learned/<fingerprint>.json` (FormatStore).
2. Heuristic extractors (logalyzer.ingest) -- unchanged path for known dialects.
3. Exit-4 inference handshake: unknown dialect -> CLI hands the driving agent
   a fingerprint + masked sample lines; the agent derives a format descriptor
   (named-group regex + timestamp format) and calls `register-format`, which
   `validate_descriptor` below checks before it is ever trusted.

This module has NO module-level dependency on logalyzer.ingest (which
imports FormatStore/fingerprint/apply_descriptor from here on the learned-
format cache lookup path) -- any use of ingest-side helpers (the shared
continuation-folding cap, correlation-id regex, generic domain-id discovery)
is a function-scope import, deferred until call time, so the two modules
never form an import cycle at module-load time.
"""
import hashlib, json, multiprocessing, os, re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logalyzer.records import NormalizedRecord, normalize_level

# ---------------------------------------------------------------------------
# Skeleton masking -- reduces a line to its "shape" for fingerprinting only.
# Order matters: hex-runs (mixed digits + a-f letters, >=8 chars) are found
# BEFORE plain digit-runs so a hash/commit-id token collapses to one 'H'
# instead of a string of '9's; remaining digit-runs collapse to one '9';
# remaining letter-runs collapse to one 'A'. Punctuation/whitespace/brackets
# are left untouched -- they carry the format's shape.
#
# Final pass: 2+ consecutive whitespace-separated bare 'A' tokens (free-text
# prose -- "reservation timed out", "using EPOLL") collapse into a single
# 'A'. Without this, two lines of the SAME dialect with differently-worded
# messages (a near-certainty for free text; word *count* varies line to
# line even when the surrounding structure is identical) would produce
# different skeletons and never accumulate into the same top-5 bucket,
# defeating the fingerprint. Structural single-word fields stay distinct
# because they are normally punctuation-adjacent, not space-adjacent
# ("svc=inventory", "[thread]", "a.b.Logger") -- only genuine multi-word
# runs get flattened.
# ---------------------------------------------------------------------------
_HEX_RUN = re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b")
_DIGIT_RUN = re.compile(r"\d+")
# IMPORTANT 4: any Unicode letter run, not just ASCII a-z/A-Z. `[^\W\d_]`
# means "a \w character that is neither a digit nor underscore" -- under
# Python 3's default (Unicode) `re` matching that is exactly "a letter in
# any script", so a Cyrillic (or Greek, CJK, etc.) message run collapses to
# 'A' the same way an English one does. Without this, two files of the
# SAME dialect with different Russian wording never converge on a shared
# skeleton and the learned-format cache never hits for RU logs.
_LETTER_RUN = re.compile(r"[^\W\d_]+", re.UNICODE)
_PROSE_RUN = re.compile(r"\bA(?:[ \t]+A\b)+")


def skeleton_line(raw):
    s = _HEX_RUN.sub("H", raw)
    s = _DIGIT_RUN.sub("9", s)
    s = _LETTER_RUN.sub("A", s)
    s = _PROSE_RUN.sub("A", s)
    return s


def top_skeletons(sample_lines, n=5):
    """The N most common line skeletons in sample_lines, most-frequent
    first (ties broken alphabetically for determinism)."""
    skels = [skeleton_line(ln) for ln in sample_lines if ln.strip()]
    if not skels:
        return []
    counts = Counter(skels)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [sk for sk, _ in ranked[:n]]


def fingerprint(sample_lines):
    """Stable 12-hex-char hash of a format's shape: the 5 most common
    masked line skeletons in the sample, sha256'd. Two samples of the same
    dialect (different literal values, same structure) hash identically;
    a structurally different dialect hashes differently."""
    skels = top_skeletons(sample_lines) or [""]
    material = "\n".join(skels)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# FormatStore -- learned/<fingerprint>.json persistence
# ---------------------------------------------------------------------------
def default_store_dir():
    """The shipped store dir is <case package root>/formats.d/learned/ --
    computed independently of logalyzer.ingest/cli_impl (no cross-import)
    since this file's own location IS inside that same package root.
    LOGALYZER_FORMATS_DIR is a test-only escape hatch (undocumented in the
    CLI usage text on purpose): it lets tests exercise the real CLI
    register-format/investigate round trip against an isolated temp
    directory instead of writing committable artifacts into the repo
    itself during a test run."""
    override = os.environ.get("LOGALYZER_FORMATS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "formats.d" / "learned"


class FormatStore:
    """Learned formats as JSON files under dir_path, one per fingerprint."""

    def __init__(self, dir_path=None):
        self.dir_path = Path(dir_path) if dir_path is not None else default_store_dir()

    def _path(self, fp):
        return self.dir_path / ("%s.json" % fp)

    def get(self, fp):
        p = self._path(fp)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, fp, descriptor, hit_rates, skeleton, validate_seconds=None):
        self.dir_path.mkdir(parents=True, exist_ok=True)
        doc = {
            "fingerprint": fp,
            "descriptor": descriptor,
            "hit_rates": hit_rates,
            "sample_skeleton": skeleton,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # CRITICAL 3: the measured validate_descriptor wall time is stored
        # for operator visibility -- a descriptor that took suspiciously
        # long to validate (even if it stayed under the timeout budget) is
        # worth a second look before it starts getting applied to every
        # matching file forever. Optional/omitted for save() calls that
        # don't have a timing to report (e.g. direct test usage).
        if validate_seconds is not None:
            doc["validate_seconds"] = round(validate_seconds, 4)
        self._path(fp).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        return doc


# ---------------------------------------------------------------------------
# Timestamp normalization for descriptor-driven parsing
# ---------------------------------------------------------------------------
_ISO_RX = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:[.,](?P<frac>\d+))?\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?$")


def _parse_value_to_dt(value, ts_format):
    value = (value or "").strip()
    if not value:
        raise ValueError("empty timestamp value")
    if ts_format == "iso":
        m = _ISO_RX.match(value)
        if not m:
            raise ValueError("not a recognized ISO timestamp: %r" % value)
        y, mo, d = (int(x) for x in m.group("date").split("-"))
        h, mi, s = (int(x) for x in m.group("time").split(":"))
        frac = m.group("frac") or "0"
        micro = int((frac + "000000")[:6])
        tz = m.group("tz")
        if tz and tz != "Z":
            sign = 1 if tz[0] == "+" else -1
            body = tz[1:].replace(":", "")
            th, tm = int(body[:2]), int(body[2:4] or "0")
            tzinfo = timezone(sign * timedelta(hours=th, minutes=tm))
        else:
            tzinfo = timezone.utc
        return datetime(y, mo, d, h, mi, s, micro, tzinfo=tzinfo)
    if ts_format == "epoch_s":
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if ts_format == "epoch_ms":
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    # Anything else: an strptime format string. Naive results are assumed
    # already-UTC (logs rarely self-describe a local offset outside the
    # timestamp text itself, and guessing one would silently corrupt
    # correlation windows -- same call made for BSD syslog in ingest.py).
    dt = datetime.strptime(value, ts_format)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_utc_iso(value, ts_format):
    """Parse `value` per ts_format ("iso" | "epoch_s" | "epoch_ms" | an
    strptime format string) and render as ISO-8601 UTC with millisecond
    precision, e.g. "2026-07-28T15:08:38.903Z". Raises ValueError (or a
    stdlib parsing exception) on anything unparseable -- callers decide
    whether that means "reject the line" or "reject the whole descriptor"."""
    dt = _parse_value_to_dt(value, ts_format)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


# ---------------------------------------------------------------------------
# ReDoS containment (CRITICAL 3) -- ordinary in-process timeouts (signal
# alarms, threading) CANNOT interrupt a catastrophically-backtracking C-level
# regex match: CPython's `re` engine runs a tight native loop and does not
# poll for pending signals or check a cooperative deadline mid-match, so a
# pattern like `(?P<ts>(\d+)+X)` against a long non-matching digit run hangs
# the calling thread/process indefinitely no matter what timer you arm.
# Empirically confirmed (see the Task-A-follow-up report): a plain
# `signal.alarm`-guarded call to that pattern still blocked for 5+ seconds
# with zero interruption. The only reliable hard-kill mechanism in the
# stdlib is a separate OS process, which the parent can `.terminate()`
# unconditionally regardless of what the child is doing internally.
# ---------------------------------------------------------------------------

_VALIDATE_TIMEOUT_S = 2.0
_APPLY_TIMEOUT_S = 10.0


def _match_sample_worker(pattern_str, ts_format, declared, lines, q):
    """Runs in a child process. Computes exactly the plain (picklable) data
    validate_descriptor needs from matching `pattern_str` against `lines` --
    re.Match objects never cross the process boundary (not picklable, and
    not needed once the per-group values are pulled out here)."""
    try:
        compiled = re.compile(pattern_str)
        matched_count = 0
        ts_ok = 0
        iso_values = []
        level_values = []
        group_present = {g: 0 for g in declared}
        for ln in lines:
            m = compiled.search(ln)
            if not m:
                continue
            matched_count += 1
            for g in declared:
                if m.group(g):
                    group_present[g] += 1
            ts_val = m.group("ts") if "ts" in declared else None
            if ts_val:
                try:
                    iso_values.append(to_utc_iso(ts_val, ts_format))
                    ts_ok += 1
                except Exception:
                    pass
            if "level" in declared:
                lv = m.group("level")
                if lv:
                    level_values.append(lv)
        q.put(("ok", {"matched_count": matched_count, "ts_ok": ts_ok,
                      "iso_values": iso_values, "level_values": level_values,
                      "group_present": group_present}))
    except Exception as e:
        q.put(("error", "%s: %s" % (type(e).__name__, e)))


def _run_with_budget(target, args, timeout):
    """Run `target(*args, q)` in a child process; hard-kill it if it is
    still alive past `timeout` seconds. Returns ("ok", payload),
    ("error", message), or ("timeout", message)."""
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=target, args=tuple(args) + (q,))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        p.join()
        return ("timeout", "exceeded the %.1fs time budget (possible catastrophic "
                           "backtracking)" % timeout)
    try:
        status, payload = q.get_nowait()
    except Exception:
        return ("error", "worker process exited without a result "
                         "(code %s)" % p.exitcode)
    return (status, payload)


# ---------------------------------------------------------------------------
# validate_descriptor
# ---------------------------------------------------------------------------
_MAX_REGEX_LEN = 2000
_OPTIONAL_GROUPS = ("level", "service", "logger", "msg", "thread")
_PLAUSIBLE_YEAR_MIN = 2000
_PLAUSIBLE_YEAR_MAX = 2100
_PLAUSIBLE_SPAN_DAYS = 366


def _implausible_timestamps_reason(iso_values):
    """IMPORTANT 7: a descriptor can pass the 90% ts hit-rate bar on
    garbage -- e.g. `(?P<ts>\\d+)` + ts_format "epoch_s" matches a bare "28"
    and parses "successfully" to 1970-01-01T00:00:28Z. Hit-rate alone
    can't catch that; plausibility bounds on the PARSED values can. Sample
    years must fall in [2000, 2100] and the sample's own min/max span must
    be under a year (one real dialect's sample shouldn't straddle more)."""
    if not iso_values:
        return None  # the ts hit-rate check already explains an empty sample
    years = [int(v[:4]) for v in iso_values]
    lo, hi = min(years), max(years)
    if lo < _PLAUSIBLE_YEAR_MIN or hi > _PLAUSIBLE_YEAR_MAX:
        return ("parsed sample timestamps fall outside the plausible range "
                "[%d, %d] (saw year %d..%d) -- likely a garbage ts_format/regex "
                "combination, not a real dialect" % (_PLAUSIBLE_YEAR_MIN,
                _PLAUSIBLE_YEAR_MAX, lo, hi))
    from datetime import date as _date
    ordinals = [_date(int(v[0:4]), int(v[5:7]), int(v[8:10])).toordinal() for v in iso_values]
    span = max(ordinals) - min(ordinals)
    if span >= _PLAUSIBLE_SPAN_DAYS:
        return ("parsed sample timestamps span %d days (>= %d) -- implausible "
                "for a single sample of one dialect" % (span, _PLAUSIBLE_SPAN_DAYS))
    return None


def validate_descriptor(descriptor, sample_lines, timeout=_VALIDATE_TIMEOUT_S):
    """Validate a driving-agent-proposed format descriptor against a raw
    sample of lines from the file it was derived from.

    descriptor schema: {"line_regex": <python regex, named group "ts"
    required, level/service/logger/msg/thread optional>, "ts_format":
    <strptime format string, or "iso"/"epoch_s"/"epoch_ms">, "notes": str}.

    Returns (ok, hit_rates, reason). hit_rates always has a "ts" key; other
    keys appear only for groups the regex actually declares.

    "Non-continuation lines" (the design doc's denominator for the 90% ts
    threshold) is operationalized here as every non-blank sample line: the
    sample handed to register-format is expected to be representative
    primary-format lines (that's what the exit-4 handshake collects), so a
    handful of true continuation lines mixed in only mildly depresses the
    rate -- exactly the slack the 90% (not 100%) threshold exists for.

    The actual per-line matching runs in a child process with a hard
    wall-clock budget (`timeout`, default 2s) -- see the ReDoS containment
    note above `_match_sample_worker`. `timeout` is overridable so tests
    can exercise the real kill path without waiting on the production
    budget.
    """
    if not isinstance(descriptor, dict):
        return False, {}, "descriptor must be a JSON object"
    pattern_str = descriptor.get("line_regex")
    ts_format = descriptor.get("ts_format")
    if not isinstance(pattern_str, str) or not pattern_str:
        return False, {}, "descriptor missing 'line_regex'"
    if not isinstance(ts_format, str) or not ts_format:
        return False, {}, "descriptor missing 'ts_format'"
    if len(pattern_str) > _MAX_REGEX_LEN:
        return False, {}, ("line_regex too long (%d > %d chars) -- refused as a "
                           "catastrophic-backtracking guard" % (len(pattern_str), _MAX_REGEX_LEN))
    try:
        compiled = re.compile(pattern_str)
    except re.error as e:
        return False, {}, "line_regex does not compile: %s" % e
    declared = set(compiled.groupindex)
    if "ts" not in declared:
        return False, {}, "line_regex must define a named group 'ts'"

    lines = [ln for ln in sample_lines if ln.strip()]
    if not lines:
        return False, {}, "sample_lines is empty"

    status, payload = _run_with_budget(
        _match_sample_worker, (pattern_str, ts_format, declared, lines), timeout)
    if status == "timeout":
        return False, {}, "line_regex %s -- rejected" % payload
    if status == "error":
        return False, {}, "line_regex raised while matching the sample: %s" % payload

    ts_ok = payload["ts_ok"]
    matched_count = payload["matched_count"]
    hit_rates = {"ts": round(ts_ok / len(lines), 4)}
    reasons = []
    if hit_rates["ts"] < 0.90:
        reasons.append("ts hit-rate %.0f%% below 90%% threshold (%d/%d lines)" %
                       (hit_rates["ts"] * 100, ts_ok, len(lines)))

    implausible = _implausible_timestamps_reason(payload["iso_values"])
    if implausible:
        reasons.append(implausible)

    if "level" in declared:
        level_values = payload["level_values"]
        rate = (sum(1 for lv in level_values if normalize_level(lv) != "UNKNOWN")
                / len(level_values)) if level_values else 0.0
        hit_rates["level"] = round(rate, 4)
        if rate < 0.50:
            reasons.append("level hit-rate %.0f%% below 50%% threshold" % (rate * 100))

    for g in _OPTIONAL_GROUPS[1:]:  # "service", "logger", "msg", "thread" (level handled above)
        if g in declared:
            hit_rates[g] = (round(payload["group_present"][g] / matched_count, 4)
                            if matched_count else 0.0)

    if reasons:
        return False, hit_rates, "; ".join(reasons)
    return True, hit_rates, ""


# ---------------------------------------------------------------------------
# apply_descriptor -- deterministic, zero-LLM application of a validated
# descriptor to a file's lines.
# ---------------------------------------------------------------------------
def apply_descriptor(descriptor, lines, service_hint, ref, masker):
    # Local import: ingest.py imports FormatStore/fingerprint/apply_descriptor
    # from this module on its cache-lookup path, so the reverse edge must be
    # deferred to call time to avoid a module-load-time circular import.
    from logalyzer.ingest import _CORR, _fold_continuation, discover_domain_ids

    compiled = re.compile(descriptor["line_regex"])
    ts_format = descriptor["ts_format"]
    declared = set(compiled.groupindex)
    out = []
    prev = None
    fold_count = 0
    for i, raw in enumerate(lines, 1):
        raw = raw.rstrip("\n")
        if not raw.strip():
            continue

        m = compiled.search(raw)
        if m is None:
            newrec, fold_count = _fold_continuation(raw, prev, fold_count, masker,
                                                     service_hint, ref, i)
            if newrec is not None:
                out.append(newrec)
                prev = newrec
            continue

        ts_val = m.group("ts") if "ts" in declared else ""
        try:
            ts_iso = to_utc_iso(ts_val, ts_format) if ts_val else ""
        except Exception:
            ts_iso = ""
        level_raw = (m.group("level") or "") if "level" in declared else ""
        service = ((m.group("service") or "") if "service" in declared else "") or service_hint
        logger = (m.group("logger") or "") if "logger" in declared else ""
        thread = (m.group("thread") or "") if "thread" in declared else ""
        msg_raw = (m.group("msg") or "") if "msg" in declared else ""
        body, applied = masker.mask_with_flag(msg_raw if msg_raw else raw)
        corr = _CORR.search(raw)
        domain = discover_domain_ids(raw)
        attrs = {}
        if logger:
            attrs["logger"] = logger
        if thread:
            attrs["thread"] = thread
        quality = "ok" if (ts_iso and normalize_level(level_raw) != "UNKNOWN") else "partial"
        rec = NormalizedRecord(
            timestamp=ts_iso, service=service, level=level_raw or "UNKNOWN", body=body,
            correlation_id=corr.group(1) if corr else "", domain_ids=domain,
            source_ref=ref, source_line=i, parse_quality=quality,
            redaction_applied=applied, attrs=attrs)
        out.append(rec)
        prev = rec
        fold_count = 0
    return out


def _apply_worker(descriptor, lines, service_hint, ref, q):
    """Runs in a child process (see the ReDoS containment note above
    `_match_sample_worker`): a descriptor that validated cleanly on a small
    sample can still behave catastrophically on a real file's full content,
    so the whole per-file application gets the same hard-kill treatment.
    Uses its own fresh Masker (see apply_descriptor_with_budget's
    docstring for the pseudonym-numbering trade-off that implies)."""
    try:
        from logalyzer.masking import Masker
        recs = apply_descriptor(descriptor, lines, service_hint, ref, Masker())
        q.put(("ok", recs))
    except Exception as e:
        q.put(("error", "%s: %s" % (type(e).__name__, e)))


def apply_descriptor_with_budget(descriptor, lines, service_hint, ref, timeout=_APPLY_TIMEOUT_S):
    """Same contract as apply_descriptor, but the whole per-file
    application runs in a child process with a hard wall-clock budget
    (CRITICAL 3's secondary/apply-time safety net -- default 10s, generous
    since a real file is much bigger than a validation sample). On timeout
    or a worker crash, returns (None, reason) so the caller can fall back
    to the heuristic parser instead of hanging the whole ingest run.

    Trade-off: the child uses a FRESH Masker rather than sharing the
    caller's running instance across the process boundary -- redaction is
    still fully applied to this file's content, but the pseudonym
    *sequence number* (<EMAIL:e-01> etc.) is no longer guaranteed globally
    continuous with pseudonyms assigned to other files in the same run.
    Necessary because (a) Python cannot hard-interrupt a hanging regex any
    other way, and (b) a stateful Masker isn't meaningfully shareable
    across a process boundary regardless.

    Returns (records_or_None, reason_or_None).
    """
    status, payload = _run_with_budget(
        _apply_worker, (descriptor, lines, service_hint, ref), timeout)
    if status == "timeout":
        return None, "descriptor application %s -- falling back to the heuristic parser" % payload
    if status == "error":
        return None, "descriptor application failed (%s) -- falling back to the heuristic parser" % payload
    return payload, None
