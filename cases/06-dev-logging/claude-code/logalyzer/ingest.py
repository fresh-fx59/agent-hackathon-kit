import json, re, tempfile, zipfile, stat, gzip
from datetime import datetime, timezone
from pathlib import Path
from logalyzer.records import NormalizedRecord

_CORR = re.compile(r"correlation_id[=:\"\s]+([A-Za-z0-9-]+)")
_DOMAIN_KEYS = ("order_id", "payment_id", "auth_id", "reservation_id", "sku", "user_id", "trace_id")
_PLAIN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2}[.,]\d{3})"
    r"\s+\[(?P<thread>[^\]]+)\]\s+(?P<level>[A-Z]+)\s+(?P<logger>\S+)\s+[-—]\s+(?P<msg>.*)$")
# Kept exactly as-is for the logback `_PLAIN` structured-reader path (byte-
# identical on the pack). The newer generic-parser fallback below uses the
# broader, key-name-agnostic `discover_domain_ids` instead (Normalization v2).
_INLINE_ID = re.compile(r"\b(auth_id|order_id|reservation_id|sku|user_id)[=:]\s?([A-Za-z0-9._-]+)")

# ---------------------------------------------------------------------------
# Generic ID discovery (Normalization v2) -- replaces a hardcoded key list
# for the generic-parser fallback: UUIDs, `<key>_id=value`/`<key>Id:value`
# style pairs (key name kept as found), and bare hex runs >= 8 chars (must
# contain an actual a-f letter, or a plain numeric id would get mislabeled
# "hex"). Alternation order matters for finditer's per-position first-match:
# UUID (most specific shape) before the generic key=value form, before the
# bare-hex fallback.
#
# IMPORTANT 5 fix: the key must have genuine id MORPHOLOGY -- exactly "id",
# a snake_case `*_id` suffix, or a camelCase `*Id` suffix -- not "any word
# ending in the letters i-d", which swept up ordinary English words like
# "valid"/"paid"/"invalid" ("valid: true", "paid: confirming") as if they
# were id fields. `\bid\b` and `\b\w+_id\b` are case-insensitive on the
# "id" itself; `[a-zA-Z]\w*Id\b` specifically requires a capital I (real
# camelCase), which is what excludes "valid"/"paid" (they end in lowercase
# "id", never "Id") without a hardcoded word blocklist. Captured values are
# further filtered by `_looks_like_real_id_value`: trivial tokens
# (true/false/null/none/yes/no) and anything under 4 characters are
# dropped, and a value with no digit at all must still be hex/uuid-shaped
# to count -- guards against sentences that happen to parse
# `key: shortword` (e.g. "ok: yes") as if it were an id assignment.
# ---------------------------------------------------------------------------
_ID_UUID = r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
_ID_KEY_MORPH = r"(?:[Ii]d|\w+_[Ii]d|[a-zA-Z]\w*Id)"
_ID_KEYVAL = r"\b(?P<idkey>%s)\b[=:]\s?(?P<idval>[\w-]+)" % _ID_KEY_MORPH
_ID_HEX = r"\b(?=[0-9a-fA-F]*[a-fA-F])(?P<hexid>[0-9a-fA-F]{8,})\b"
_ID_DISCOVERY = re.compile("|".join([_ID_UUID, _ID_KEYVAL, _ID_HEX]))

_TRIVIAL_ID_VALUES = {"true", "false", "null", "none", "yes", "no"}
_MIN_ID_VALUE_LEN = 4
_HEXY_VALUE = re.compile(r"^[0-9a-fA-F]+$")


def _looks_like_real_id_value(val):
    """IMPORTANT 5: a captured `key=value`/`key:value` pair is only kept as
    a domain id if the value itself looks id-shaped -- not a boolean-ish
    word or a too-short token that a normal sentence could easily produce
    ("valid: true", "state: ok")."""
    if not val or val.lower() in _TRIVIAL_ID_VALUES:
        return False
    if len(val) < _MIN_ID_VALUE_LEN:
        return False
    if any(c.isdigit() for c in val):
        return True
    return bool(_HEXY_VALUE.match(val))


def discover_domain_ids(raw):
    """Scan `raw` for UUIDs, `<key>_id=value`-style pairs, and bare hex runs
    (>=8 chars, real hex -- at least one a-f letter). Key = the key name
    found as-is (no hardcoded allow-list), or "uuid"/"hex" for the other two
    shapes. First occurrence per key wins (deterministic, left-to-right)."""
    out = {}
    for m in _ID_DISCOVERY.finditer(raw):
        if m.group("uuid"):
            out.setdefault("uuid", m.group("uuid"))
        elif m.group("idkey"):
            val = m.group("idval")
            if _looks_like_real_id_value(val):
                out[m.group("idkey")] = val
        elif m.group("hexid"):
            out.setdefault("hex", m.group("hexid"))
    return out

def _service_from_name(path):
    stem = Path(path).name
    if stem.endswith(".gz"):
        stem = stem[:-3]
    for suffix in (".log", ".jsonl", ".txt", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem

# ---------------------------------------------------------------------------
# Content-based container detection (Normalization v2 CRITICAL 2). Filename
# substrings ("kafka"/"k8s"/"trace"/"metrics") used to be authoritative --
# a logback file merely NAMED kafka-server.log would be forced through the
# kafka JSON reader and come back 100% unparsed, which also made it
# INELIGIBLE for the needs_inference recovery path (that check is scoped to
# plaintext dialects, since a line_regex descriptor can't fix "wrong
# reader"). Filename is now only a tie-breaker for genuinely ambiguous
# content (an unparseable-but-JSON-shaped blob); a confident, disagreeing
# content read always wins.
# ---------------------------------------------------------------------------
_METRICS_VALUE_LINE = re.compile(
    r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})?\s+[-+]?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
_CONTENT_SAMPLE_SIZE = 20
_CONTENT_MIN_RATE = 0.6


def _looks_like_trace_doc(lines, filename):
    """Whole-file check: a trace file is a single (possibly pretty-printed)
    JSON document keyed "spans" -- per-line sampling below can never see
    this (each individual line of a pretty-printed doc isn't valid JSON on
    its own). A cheap first/last-char guard skips the join+parse for an
    ordinary plaintext file (whose first non-blank character is essentially
    never '{'/'['), so this doesn't cost a large log file anything.

    An unambiguous {"spans": [...]} document is "trace" regardless of
    filename. A JSON-shaped blob that fails to parse, or parses to
    something else (a bare list, a dict with no "spans"), is genuinely
    ambiguous content -- not confidently "not trace" the way ordinary log
    text is -- so a "trace"-named file wins that specific tie (matches
    read_structured's own deliberate single-unparsed-record handling of
    malformed/list trace docs).
    """
    stripped = [ln.strip() for ln in lines if ln.strip()]
    if not stripped or stripped[0][:1] not in ("{", "["):
        return False
    is_trace_named = "trace" in filename.lower()
    try:
        doc = json.loads("\n".join(lines))
    except (ValueError, TypeError):
        return is_trace_named
    if isinstance(doc, dict) and "spans" in doc:
        return True
    return is_trace_named


def _content_classify(sample_lines):
    """Scores a line sample by what it actually contains. Returns one of
    "jsonl", "kafka", "k8s", "metrics", or None (inconclusive -> caller
    falls back to "plaintext"). Trace is handled separately by
    `_looks_like_trace_doc` (whole-file, not a line sample) since it isn't
    a line-per-record format."""
    lines = [ln.strip() for ln in sample_lines if ln.strip()][:_CONTENT_SAMPLE_SIZE]
    if not lines:
        return None

    # `json_syntax_ok` counts ANY successfully-parsed JSON value (object,
    # array, number, string, null) -- that's the real "is this a JSON-lines
    # file" signal. A healthy jsonl file legitimately mixes real objects
    # with the occasional bare scalar/array/null garbage line (see
    # tests/test_ingest_lines.py's JSON_SCALAR_ARRAY fixture): those lines
    # aren't objects (dict_ok excludes them) but they ARE valid JSON syntax,
    # unlike an ordinary log line, which essentially never parses as JSON
    # at all. Requiring `dict_ok` specifically to clear the content-rate
    # bar would misclassify that legitimate mixed file as plaintext.
    json_syntax_ok = 0
    dict_ok = 0
    kafka_like = 0
    for ln in lines:
        try:
            obj = json.loads(ln)
        except (ValueError, TypeError):
            continue
        json_syntax_ok += 1
        if not isinstance(obj, dict):
            continue
        dict_ok += 1
        if (("topic" in obj and "partition" in obj) or
                ("payload" in obj and "type" in obj)):
            kafka_like += 1
    if json_syntax_ok / len(lines) >= _CONTENT_MIN_RATE:
        if dict_ok and kafka_like / dict_ok >= 0.5:
            return "kafka"
        return "jsonl"

    from logalyzer.ingest_structured import _K8S
    k8s_hits = sum(1 for ln in lines if _K8S.match(ln))
    if k8s_hits / len(lines) >= _CONTENT_MIN_RATE:
        return "k8s"

    metrics_hits = sum(1 for ln in lines if ln.startswith("#") or _METRICS_VALUE_LINE.match(ln))
    if metrics_hits / len(lines) >= _CONTENT_MIN_RATE:
        return "metrics"

    return None


def sniff_format(lines, filename):
    if _looks_like_trace_doc(lines, filename):
        return "trace"
    content = _content_classify(lines)
    if content:
        return content
    return "plaintext"

def _from_json_obj(obj, service_hint, ref, lineno, masker):
    body, applied = masker.mask_with_flag(str(obj.get("msg") or obj.get("message") or obj.get("body") or ""))
    domain = {k: obj[k] for k in _DOMAIN_KEYS if k in obj and k != "trace_id"}
    attrs = {k: v for k, v in obj.items()
             if k not in ("ts", "timestamp", "service", "level", "msg", "message",
                          "body", "correlation_id", "trace_id") and k not in domain}
    return NormalizedRecord(
        timestamp=str(obj.get("ts") or obj.get("timestamp") or ""),
        service=str(obj.get("service") or service_hint),
        level=str(obj.get("level") or "UNKNOWN"),
        body=body,
        trace_id=str(obj.get("trace_id") or ""),
        correlation_id=str(obj.get("correlation_id") or ""),
        domain_ids=domain, source_ref=ref, source_line=lineno,
        redaction_applied=applied, attrs=attrs)

def _read_jsonl(lines, service_hint, ref, masker):
    out = []
    for i, raw in enumerate(lines, 1):
        raw = raw.rstrip("\n")
        if not raw.strip(): continue
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("JSON must be an object, not a scalar or array")
            out.append(_from_json_obj(obj, service_hint, ref, i, masker))
        except (ValueError, TypeError, AttributeError):
            body, applied = masker.mask_with_flag(raw)
            m = _CORR.search(raw)
            out.append(NormalizedRecord(
                timestamp="", service=service_hint, level="UNKNOWN", body=body,
                correlation_id=m.group(1) if m else "", source_ref=ref,
                source_line=i, parse_quality="unparsed", redaction_applied=applied))
    return out

# ---------------------------------------------------------------------------
# Generic text parser (universal-ingest fallback) — Task: read ANY plaintext
# log, not just the pack's logback dialect. Timestamp bank tried in order:
# ISO 8601, epoch-millis (13 digits), epoch-seconds (10 digits), syslog
# month-name (BSD syslog has no year on the wire).
#
# Normalization v2: no `^` anchor -- `_generic_parse` now `.search()`es
# these anywhere in the line (position remembered via `m.end()`), not just
# at line start. Digit-run patterns keep a `(?<!\d)`/`(?!\d)` boundary on
# both sides so a 13-digit epoch match can't be a substring of a longer
# number; the syslog month name keeps a `\b` for the same reason.
# ---------------------------------------------------------------------------
_TS_ISO = re.compile(
    r"(?<!\d)(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})[.,](?P<ms>\d{3})"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?")
_TS_EPOCH_MS = re.compile(r"(?<!\d)(?P<epoch>\d{13})(?!\d)")
_TS_EPOCH_S = re.compile(r"(?<!\d)(?P<epoch>\d{10})(?!\d)")
_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_TS_SYSLOG = re.compile(
    r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})")
_GEN_THREAD = re.compile(r"^\s*\[(?P<thread>[^\]]+)\]")
_GEN_LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|ERR|FATAL|SEVERE|CRITICAL)\b")
_GEN_SEP = re.compile(r"\s[-—]\s|:\s")

def _generic_parse(raw):
    """Generic timestamp-anywhere-in-the-line parser -- the universal-ingest
    fallback for text formats with no dedicated reader. Tried only after
    the logback `_PLAIN` regex has already failed on the line (see
    `_read_plaintext`), so the pack's existing byte-identical behavior is
    untouched: this function only ever fires on lines `_PLAIN` rejects.

    Normalization v2 upgrade: the timestamp bank is searched ANYWHERE in the
    line, not just at line start (`.search` instead of `.match`), so a
    leading `[thread]` prefix or other framing before the timestamp no
    longer forces a line into continuation-folding. The match end position
    is what downstream level/logger/msg extraction anchors on, same as
    before; a "no timestamp found anywhere" line is now the only kind that
    is a continuation.

    Returns a dict {timestamp, level, logger, msg, quality, _ts_kind,
    _ts_start} on a recognized timestamp, or None when the line has none --
    the caller then treats it as a continuation of the previous record
    (multi-line folding) instead of a new one. The leading-underscore keys
    are internal (IMPORTANT 6's dominant-pattern conformance check in
    `_read_plaintext`, not part of the public per-line contract).
    """
    m = _TS_ISO.search(raw)
    if m:
        ms = m.group("ms")
        tz = m.group("tz") or "Z"
        ts = "%sT%s.%s%s" % (m.group("date"), m.group("time"), ms, tz)
        start, end, ts_kind = m.start(), m.end(), "iso"
    else:
        m = _TS_EPOCH_MS.search(raw)
        if m:
            epoch_ms = int(m.group("epoch"))
            seconds, millis = divmod(epoch_ms, 1000)
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % millis
            start, end, ts_kind = m.start(), m.end(), "epoch"
        else:
            m = _TS_EPOCH_S.search(raw)
            if m:
                epoch_s = int(m.group("epoch"))
                dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
                ts = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                start, end, ts_kind = m.start(), m.end(), "epoch"
            else:
                m = _TS_SYSLOG.search(raw)
                if not m:
                    return None
                mon = _MONTHS[m.group("mon")]
                day = int(m.group("day"))
                # BSD syslog carries no year. Guessing one ("this year") would
                # silently corrupt correlation windows whenever a rotated file
                # spans a year boundary, so we stamp a fixed 1900 placeholder
                # instead and force parse_quality="partial" below even when a
                # level token is found right after it. That is a deliberate
                # signal: any windowed rule (elapsed_ms math in
                # rules_engine._eval_sequence) sees a multi-decade delta
                # against real timestamps and naturally stops treating the
                # record as part of a timed sequence, without us having to
                # special-case syslog inside rules_engine itself. Windowless
                # (all_of) rule matching is unaffected -- it never looks at
                # the timestamp.
                ts = "1900-%02d-%02dT%s.000Z" % (mon, day, m.group("time"))
                start, end, ts_kind = m.start(), m.end(), "syslog"

    rest = raw[end:]
    thread_m = _GEN_THREAD.match(rest)
    if thread_m:
        rest = rest[thread_m.end():]

    window = rest[:96]
    level_m = _GEN_LEVEL.search(window)
    level, logger, msg = "", "", rest.strip()
    if level_m:
        level = level_m.group(1)
        remainder = rest[level_m.end():]
        sep_m = _GEN_SEP.search(remainder)
        if sep_m:
            logger = remainder[:sep_m.start()].strip()
            msg = remainder[sep_m.end():].strip()
        else:
            msg = remainder.strip()

    quality = "ok" if (level and ts_kind != "syslog") else "partial"
    return {"timestamp": ts, "level": level, "logger": logger, "msg": msg, "quality": quality,
            "_ts_kind": ts_kind, "_ts_start": start}

_FOLD_CAP = 20
_FOLD_TRUNC_MARKER = "… [truncated]"

def _fold_continuation(raw, prev, fold_count, masker, service_hint, ref, lineno):
    """Shared "unmatched line" handling, used by both the plaintext
    heuristic parser (`_read_plaintext`) and `formats.apply_descriptor`
    (Normalization v2) so a learned/descriptor-driven dialect folds
    continuation lines with the exact same cap/behavior as the built-in
    heuristic path. A line with no recognized structure folds into the
    previous record's body (capped at _FOLD_CAP appended lines, with a
    truncation marker emitted once); with no previous record yet, it
    becomes its own standalone partial record instead.

    Returns (new_record_or_None, new_fold_count). new_record_or_None is the
    record the caller should append to its output list (and make the new
    `prev`); None means the line was folded into the existing `prev` in
    place (already mutated)."""
    if prev is not None and fold_count < _FOLD_CAP:
        masked, applied = masker.mask_with_flag(raw)
        fold_count += 1
        if fold_count == _FOLD_CAP:
            prev.body += "\n" + masked + "\n" + _FOLD_TRUNC_MARKER
        else:
            prev.body += "\n" + masked
        if applied:
            prev.redaction_applied = True
        return None, fold_count
    if prev is not None:
        # past the fold cap: still a continuation, silently absorbed
        # (the truncation marker was already emitted once, above)
        return None, fold_count

    body, applied = masker.mask_with_flag(raw)
    corr = _CORR.search(raw)
    rec = NormalizedRecord(
        timestamp="", service=service_hint, level="UNKNOWN", body=body,
        correlation_id=corr.group(1) if corr else "",
        source_ref=ref, source_line=lineno, parse_quality="partial",
        redaction_applied=applied)
    return rec, 0

# ---------------------------------------------------------------------------
# IMPORTANT 6: timestamp-anywhere-in-the-line can shred multi-line content.
# Once the generic bank searches the WHOLE line (not just its start), an
# epoch-shaped number or a bare "Mon DD HH:MM:SS" phrase embedded deep
# inside an unrelated continuation line (a Java stack trace frame, a
# wrapped message) technically matches the bank and would otherwise become
# its own bogus record, shredding what should have folded into one.
#
# Fix: a first pass over the file finds every generic-bank hit's
# (timestamp-kind, position-band) outside `_PLAIN`-matched lines and picks
# the file's DOMINANT one; the main pass only trusts a generic match that
# conforms to it. "Position band" buckets the match's start column so real
# timestamps sharing a near-identical prefix length still count as the
# same pattern despite small width differences (e.g. day-of-month "9" vs
# "12"). If the file already has an established `_PLAIN` anchor (its real
# record-starting dialect is the fixed logback format, not the generic
# bank), a generic-bank pattern is only trusted once it genuinely RECURS
# (>=2 occurrences) -- a single coincidental hit inside continuation lines
# must not promote itself to a record start. Without a `_PLAIN` anchor, the
# generic bank IS the file's only source of truth, so even one hit is
# trusted (the common case for small/single-record generic-format files).
# ---------------------------------------------------------------------------
_TS_POSITION_BAND = 10
_NO_CONFORMING_PATTERN = object()

def _dominant_ts_pattern(lines):
    from collections import Counter
    plain_hits = 0
    generic_hits = []
    for raw in lines:
        raw = raw.rstrip("\n")
        if not raw.strip():
            continue
        if _PLAIN.match(raw):
            plain_hits += 1
            continue
        g = _generic_parse(raw)
        if g is not None:
            generic_hits.append((g["_ts_kind"], g["_ts_start"] // _TS_POSITION_BAND))
    if not generic_hits:
        return None
    top_pattern, top_count = Counter(generic_hits).most_common(1)[0]
    if plain_hits > 0 and top_count < 2:
        return _NO_CONFORMING_PATTERN
    return top_pattern

def _conforms(g, dominant):
    if dominant is None or dominant is _NO_CONFORMING_PATTERN:
        return dominant is None
    return (g["_ts_kind"], g["_ts_start"] // _TS_POSITION_BAND) == dominant

def _read_plaintext(lines, service_hint, ref, masker):
    """Returns (records, plain_hits, generic_hits) -- the hit counts drive
    the "logback" vs "heuristic" dialect label one level up (Normalization
    v2's `_parse_plaintext_dialected`); the per-line parsing behavior below
    is otherwise unchanged from the pre-v2 universal-ingest implementation,
    except that a generic-bank match must now conform to the file's
    dominant (timestamp-kind, position) pattern -- see
    `_dominant_ts_pattern` above (IMPORTANT 6)."""
    dominant = _dominant_ts_pattern(lines)
    out = []
    prev = None
    fold_count = 0
    plain_hits = 0
    generic_hits = 0
    for i, raw in enumerate(lines, 1):
        raw = raw.rstrip("\n")
        if not raw.strip(): continue

        m = _PLAIN.match(raw)
        if m:
            body, applied = masker.mask_with_flag(m.group("msg"))
            corr = _CORR.search(raw)
            domain = {k: v for k, v in _INLINE_ID.findall(raw)}
            ts = "%sT%sZ" % (m.group("date"), m.group("time").replace(",", "."))
            rec = NormalizedRecord(
                timestamp=ts, service=service_hint, level=m.group("level"), body=body,
                correlation_id=corr.group(1) if corr else "",
                domain_ids=domain, source_ref=ref, source_line=i,
                parse_quality="ok", redaction_applied=applied,
                attrs={"logger": m.group("logger")})
            out.append(rec); prev = rec; fold_count = 0
            plain_hits += 1
            continue

        # Only reached once the existing logback regex has rejected the
        # line, so pack behavior (which only ever exercises _PLAIN) is
        # byte-identical -- this generic path is purely additive.
        g = _generic_parse(raw)
        if g is not None and _conforms(g, dominant):
            body, applied = masker.mask_with_flag(g["msg"] if g["msg"] else raw)
            corr = _CORR.search(raw)
            domain = discover_domain_ids(raw)
            rec = NormalizedRecord(
                timestamp=g["timestamp"], service=service_hint,
                level=g["level"] or "UNKNOWN", body=body,
                correlation_id=corr.group(1) if corr else "",
                domain_ids=domain, source_ref=ref, source_line=i,
                parse_quality=g["quality"], redaction_applied=applied,
                attrs={"logger": g["logger"]} if g["logger"] else {})
            out.append(rec); prev = rec; fold_count = 0
            generic_hits += 1
            continue

        # No CONFORMING timestamp anywhere in the line (neither _PLAIN nor
        # a dominant-pattern generic-bank hit): a continuation line -- Java
        # stack traces, wrapped messages -- folds into the previous
        # record's body instead of becoming a standalone UNKNOWN singleton
        # (see `_fold_continuation` for the cap/truncation behavior).
        newrec, fold_count = _fold_continuation(raw, prev, fold_count, masker,
                                                service_hint, ref, i)
        if newrec is not None:
            out.append(newrec); prev = newrec
    return out, plain_hits, generic_hits

_STRUCTURED_FALLBACK_MIN_RECORDS = 5
_STRUCTURED_FALLBACK_UNPARSED_RATE = 0.90

def _mostly_unparsed(recs):
    """CRITICAL 2 fallback guard: a structured-labeled file that comes back
    almost entirely unparsed was probably misclassified (content sniffing
    is a heuristic, not a guarantee) -- worth re-trying through the
    plaintext waterfall, where needs_inference can pick it up if it's
    genuinely unrecognizable. Gated on a minimum record count so a
    DELIBERATE single "malformed trace/kafka" unparsed record (an
    intentional, well-defined outcome from read_structured, not a
    misdetection symptom) never gets reinterpreted."""
    total = len(recs)
    if total < _STRUCTURED_FALLBACK_MIN_RECORDS:
        return False
    unparsed = sum(1 for r in recs if r.parse_quality == "unparsed")
    return (unparsed / total) >= _STRUCTURED_FALLBACK_UNPARSED_RATE

def _parse_lines(lines, hint, ref, masker, warnings=None):
    fmt = sniff_format(lines, ref)
    if fmt == "plaintext":
        return _parse_plaintext_dialected(lines, hint, ref, masker, warnings)
    if fmt == "jsonl":
        recs = _read_jsonl(lines, hint, ref, masker)
    else:
        from logalyzer.ingest_structured import read_structured
        recs = read_structured(fmt, lines, hint, ref, masker)
    if _mostly_unparsed(recs):
        return _parse_plaintext_dialected(lines, hint, ref, masker, warnings)
    return fmt, recs

def _parse_plaintext_dialected(lines, hint, ref, masker, warnings=None):
    """Normalization v2 ingest order for a plaintext-sniffed file:
    (a) learned-format cache hit (FormatStore, keyed by a fingerprint of
        the first <=50 lines) -> apply_descriptor (under CRITICAL 3's
        secondary apply-time budget -- see formats.apply_descriptor_with_
        budget), deterministic, zero-LLM;
    (b)+(c) else (cache miss, OR the budget was breached) the existing
        logback `_PLAIN` / generic-parser waterfall (`_read_plaintext`,
        unchanged -- byte-identical on lines the pack already exercises).
    Returns (dialect, records) where dialect is "learned:<fp>", "logback"
    (any line matched the logback `_PLAIN` regex), or "heuristic" (only the
    generic parser/folding fired) -- consumed by stats["files"][name]
    ["format"], and by the (d) needs_inference check one level up in
    `_ingest_one_file`. `warnings`, when given a list, gets a visible
    {"file", "reason"} entry appended if a learned descriptor had to be
    abandoned mid-file (timeout/crash) and the file fell back to the
    heuristic waterfall instead.
    """
    # Local import: formats.py imports ingest-side helpers (the fold cap,
    # _CORR, discover_domain_ids) at call time from inside apply_descriptor,
    # so this edge must stay deferred too, or the two modules would form a
    # circular import at load time.
    from logalyzer import formats as _formats
    fp = _formats.fingerprint(lines[:50])
    learned = _formats.FormatStore().get(fp)
    if learned is not None:
        recs, reason = _formats.apply_descriptor_with_budget(
            learned["descriptor"], lines, hint, ref)
        if recs is not None:
            return "learned:%s" % fp, recs
        # CRITICAL 3 secondary budget breach (or a worker crash): fall
        # through to the heuristic waterfall instead of hanging the whole
        # ingest run -- visible in stats["warnings"], never silent.
        if warnings is not None:
            warnings.append({"file": ref, "reason": reason})
    recs, plain_hits, _generic_hits = _read_plaintext(lines, hint, ref, masker)
    dialect = "logback" if plain_hits > 0 else "heuristic"
    return dialect, recs

def read_source(path, masker, service_hint=""):
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hint = service_hint or _service_from_name(path)
    ref = path.name
    _, recs = _parse_lines(lines, hint, ref, masker)
    return recs

_ZIP_MAX_ENTRIES = 500
_ZIP_MAX_UNCOMPRESSED = 100 * 1024 * 1024
_MAX_FILE_BYTES = 200 * 1024 * 1024
_BINARY_SNIFF_BYTES = 8192

def _safe_extract(zpath, dest):
    with zipfile.ZipFile(zpath) as z:
        infos = z.infolist()
        if len(infos) > _ZIP_MAX_ENTRIES:
            raise ValueError("zip refused: %d entries > %d" % (len(infos), _ZIP_MAX_ENTRIES))
        total = sum(i.file_size for i in infos)
        if total > _ZIP_MAX_UNCOMPRESSED:
            raise ValueError("zip refused: %d uncompressed bytes" % total)
        for info in infos:
            name = info.filename
            p = (Path(dest) / name).resolve()
            if not str(p).startswith(str(Path(dest).resolve())):
                continue  # traversal/absolute entry: skip silently, count in caller stats
            if stat.S_ISLNK(info.external_attr >> 16):
                continue  # symlink entry: skip silently
            z.extract(info, dest)

def _looks_binary(p):
    try:
        with open(p, "rb") as f:
            chunk = f.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return False
    return b"\x00" in chunk

class _DecompressedTooLarge(Exception):
    """Internal signal only: a .gz file's DEcompressed content exceeds the
    size cap. Caught by _ingest_one_file and turned into a visible
    "skipped" entry (same mechanism as the on-disk size cap), never an
    unbounded read -- a small compressed file can otherwise expand to far
    more than _MAX_FILE_BYTES (decompression-bomb risk), which checking
    only the on-disk (compressed) size never catches."""

_GZ_CHUNK_BYTES = 65536

def _read_gz_lines(p):
    """Stream-decompress in bounded chunks with a running decompressed-byte
    counter, aborting past _MAX_FILE_BYTES instead of reading unboundedly
    (mirrors the rigor of _ZIP_MAX_UNCOMPRESSED for regular zips)."""
    total = 0
    chunks = []
    with gzip.open(p, mode="rb") as f:
        while True:
            chunk = f.read(_GZ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_FILE_BYTES:
                raise _DecompressedTooLarge(
                    "decompressed size exceeds %d byte cap" % _MAX_FILE_BYTES)
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace").splitlines()

def _is_plaintext_dialect(fmt):
    """True for any dialect label `_parse_plaintext_dialected` can produce
    (Normalization v2) -- gates the (d) needs_inference check to plaintext
    files only, never jsonl/kafka/k8s/trace/metrics/error."""
    return fmt in ("logback", "heuristic") or fmt.startswith("learned:")

def _ingest_one_file(p, masker, stats):
    """Classify + ingest ONE regular file found while walking --logs (a
    directory, or a single file passed directly). Every file becomes either
    parsed records (tallied into stats["files"][name]) or an explicit,
    visible entry in stats["skipped"] with a reason -- never a silent drop.
    """
    name = p.name
    hint = _service_from_name(p)
    try:
        size = p.stat().st_size
    except OSError as e:
        stats["skipped"].append({"file": name, "reason": "cannot stat file: %s" % type(e).__name__})
        return []
    if p.suffix == ".zip":
        stats["skipped"].append({"file": name,
            "reason": "nested zip archive skipped (only the top-level --logs path may be a .zip)"})
        return []
    if size > _MAX_FILE_BYTES:
        stats["skipped"].append({"file": name,
            "reason": "file too large: %d bytes exceeds %d byte cap" % (size, _MAX_FILE_BYTES)})
        return []
    is_gz = p.suffix == ".gz"
    if not is_gz and _looks_binary(p):
        stats["skipped"].append({"file": name, "reason": "binary file (null byte in first 8KB)"})
        return []
    lines = []
    try:
        if is_gz:
            lines = _read_gz_lines(p)
        else:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        fmt, recs = _parse_lines(lines, hint, name, masker,
                                 warnings=stats.setdefault("warnings", []))
    except _DecompressedTooLarge as e:
        stats["skipped"].append({"file": name, "reason": str(e)})
        return []
    except Exception as e:
        # A file that can't be read/parsed must not vanish silently:
        # surface one visible unparsed record naming the failure
        # (exception class only, never the raw message, which could leak
        # paths or content). Other files still get processed.
        recs = [NormalizedRecord(
            timestamp="", service=hint, level="UNKNOWN",
            body="ingest error: %s" % type(e).__name__,
            source_ref=name, source_line=0, parse_quality="unparsed")]
        fmt = "error"
    counts = {"ok": 0, "partial": 0, "unparsed": 0}
    for r in recs:
        if r.parse_quality in counts:
            counts[r.parse_quality] += 1
    entry = {"format": fmt, "ok": counts["ok"],
             "partial": counts["partial"], "unparsed": counts["unparsed"]}
    # Normalization v2 (d): a plaintext-dialect file that mostly failed to
    # parse cleanly needs an LLM-derived format descriptor, not silent
    # UNKNOWN/partial records -- flag it (with a fingerprint + a masked
    # sample) so the CLI can hand the driving agent the exit-4 handshake.
    # Scoped to plaintext dialects only: a malformed jsonl/kafka/k8s file is
    # a different problem (bad data, not an unknown line format) and the
    # line_regex-based descriptor mechanism doesn't apply to it anyway.
    if _is_plaintext_dialect(fmt):
        total = counts["ok"] + counts["partial"] + counts["unparsed"]
        # MINOR 9: per-file extractor hit rates, always surfaced for
        # plaintext/learned files (stats diagnosability), independent of
        # whether they end up gating needs_inference below.
        ts_rate = (sum(1 for r in recs if r.timestamp) / total) if total else 0.0
        level_rate = (sum(1 for r in recs if r.level != "UNKNOWN") / total) if total else 0.0
        entry["ts_hit_rate"] = round(ts_rate, 4)
        entry["level_hit_rate"] = round(level_rate, 4)
        # CRITICAL 1 fix: apply_descriptor's "ok" quality requires BOTH ts
        # AND level -- a valid, fully-matching but level-less descriptor
        # (nginx-style access logs, etc.) would then have an ok-rate stuck
        # at 0% forever, deadlocking exit-4 even after the correct
        # descriptor is registered. For a learned: dialect, gate on the
        # descriptor's actual MATCH rate (ts_rate: did the regex find and
        # parse ts at all -- apply_descriptor never sets a non-empty
        # timestamp on a folded/standalone continuation record) instead of
        # the ok-rate, which conflates "unmatched" with "matched but no
        # level field in this dialect".
        quality_rate = ts_rate if fmt.startswith("learned:") else (
            (counts["ok"] / total) if total else 0.0)
        if total and quality_rate < 0.30 and len(lines) >= 20:
            from logalyzer import formats as _formats
            fp = _formats.fingerprint(lines[:50])
            sample = [masker.mask(ln) for ln in lines if ln.strip()][:20]
            entry["needs_inference"] = True
            entry["fingerprint"] = fp
            stats.setdefault("needs_inference", []).append(
                {"file": name, "fingerprint": fp, "sample_lines": sample})
    stats["files"][name] = entry
    return recs

def read_all_with_stats(root, masker):
    """Like read_all, but also returns a stats dict:
    {"files": {name: {"format", "ok", "partial", "unparsed"}},
     "skipped": [{"file", "reason"}]} -- so "didn't know the format" is
    diagnosable in one command (see cli_impl.cmd_stats).

    root may be a directory, a .zip, or a single file of any name.
    """
    root = Path(root)
    if root.is_file() and root.suffix == ".zip":
        tmp = tempfile.TemporaryDirectory()
        try:
            _safe_extract(root, tmp.name)
            return read_all_with_stats(Path(tmp.name), masker)
        finally:
            tmp.cleanup()
    stats = {"files": {}, "skipped": []}
    if root.is_file():
        return _ingest_one_file(root, masker, stats), stats
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # Hidden path components (.git, .DS_Store, dotfiles) are never
        # ingested and never even reach the visible skip-with-reason
        # bookkeeping -- they are not "logs of an unknown format", they are
        # not logs at all, same as `.gitignore`/`find` conventions treat them.
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        out.extend(_ingest_one_file(p, masker, stats))
    return out, stats

def read_all(root, masker):
    recs, _ = read_all_with_stats(root, masker)
    return recs
