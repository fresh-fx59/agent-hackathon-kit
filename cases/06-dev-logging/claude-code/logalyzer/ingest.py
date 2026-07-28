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
# for the generic-parser fallback: UUIDs, any `*_id[=:]value`/`Id:value`
# style pair (key name kept as found), and bare hex runs >= 8 chars (must
# contain an actual a-f letter, or a plain numeric id would get mislabeled
# "hex"). Alternation order matters for finditer's per-position first-match:
# UUID (most specific shape) before the generic key=value form, before the
# bare-hex fallback.
# ---------------------------------------------------------------------------
_ID_UUID = r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
_ID_KEYVAL = r"\b(?P<idkey>\w*_?[Ii]d)[=:]\s?(?P<idval>[\w-]+)"
_ID_HEX = r"\b(?=[0-9a-fA-F]*[a-fA-F])(?P<hexid>[0-9a-fA-F]{8,})\b"
_ID_DISCOVERY = re.compile("|".join([_ID_UUID, _ID_KEYVAL, _ID_HEX]))


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
            out[m.group("idkey")] = m.group("idval")
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

def sniff_format(first_lines, filename):
    name = filename.lower()
    if "kafka" in name: return "kafka"
    if "k8s" in name or "kube" in name: return "k8s"
    if "trace" in name: return "trace"
    if "metrics" in name: return "metrics"
    for ln in first_lines:
        ln = ln.strip()
        if not ln: continue
        if ln.startswith("{"): return "jsonl"
        return "plaintext"
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

    Returns a dict {timestamp, level, logger, msg, quality} on a recognized
    timestamp, or None when the line has none -- the caller then treats it
    as a continuation of the previous record (multi-line folding) instead
    of a new one.
    """
    m = _TS_ISO.search(raw)
    if m:
        ms = m.group("ms")
        tz = m.group("tz") or "Z"
        ts = "%sT%s.%s%s" % (m.group("date"), m.group("time"), ms, tz)
        end, ts_kind = m.end(), "iso"
    else:
        m = _TS_EPOCH_MS.search(raw)
        if m:
            epoch_ms = int(m.group("epoch"))
            seconds, millis = divmod(epoch_ms, 1000)
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % millis
            end, ts_kind = m.end(), "epoch"
        else:
            m = _TS_EPOCH_S.search(raw)
            if m:
                epoch_s = int(m.group("epoch"))
                dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
                ts = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                end, ts_kind = m.end(), "epoch"
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
                end, ts_kind = m.end(), "syslog"

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
    return {"timestamp": ts, "level": level, "logger": logger, "msg": msg, "quality": quality}

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

def _read_plaintext(lines, service_hint, ref, masker):
    """Returns (records, plain_hits, generic_hits) -- the hit counts drive
    the "logback" vs "heuristic" dialect label one level up (Normalization
    v2's `_parse_plaintext_dialected`); the per-line parsing behavior below
    is otherwise unchanged from the pre-v2 universal-ingest implementation."""
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
        if g is not None:
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

        # No recognized timestamp anywhere in the line (neither _PLAIN nor
        # the generic bank): a continuation line -- Java stack traces,
        # wrapped messages -- folds into the previous record's body instead
        # of becoming a standalone UNKNOWN singleton (see
        # `_fold_continuation` for the cap/truncation behavior).
        newrec, fold_count = _fold_continuation(raw, prev, fold_count, masker,
                                                service_hint, ref, i)
        if newrec is not None:
            out.append(newrec); prev = newrec
    return out, plain_hits, generic_hits

def _parse_lines(lines, hint, ref, masker):
    fmt = sniff_format(lines[:5], ref)
    if fmt == "jsonl":
        return fmt, _read_jsonl(lines, hint, ref, masker)
    if fmt == "plaintext":
        return _parse_plaintext_dialected(lines, hint, ref, masker)
    from logalyzer.ingest_structured import read_structured
    return fmt, read_structured(fmt, lines, hint, ref, masker)

def _parse_plaintext_dialected(lines, hint, ref, masker):
    """Normalization v2 ingest order for a plaintext-sniffed file:
    (a) learned-format cache hit (FormatStore, keyed by a fingerprint of
        the first <=50 lines) -> apply_descriptor, deterministic, zero-LLM;
    (b)+(c) else the existing logback `_PLAIN` / generic-parser waterfall
        (`_read_plaintext`, unchanged -- byte-identical on lines the pack
        already exercises).
    Returns (dialect, records) where dialect is "learned:<fp>", "logback"
    (any line matched the logback `_PLAIN` regex), or "heuristic" (only the
    generic parser/folding fired) -- consumed by stats["files"][name]
    ["format"], and by the (d) needs_inference check one level up in
    `_ingest_one_file`.
    """
    # Local import: formats.py imports ingest-side helpers (the fold cap,
    # _CORR, discover_domain_ids) at call time from inside apply_descriptor,
    # so this edge must stay deferred too, or the two modules would form a
    # circular import at load time.
    from logalyzer import formats as _formats
    fp = _formats.fingerprint(lines[:50])
    learned = _formats.FormatStore().get(fp)
    if learned is not None:
        recs = _formats.apply_descriptor(learned["descriptor"], lines, hint, ref, masker)
        return "learned:%s" % fp, recs
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
        fmt, recs = _parse_lines(lines, hint, name, masker)
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
        if total and (counts["ok"] / total) < 0.30 and len(lines) >= 20:
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
