import json, re, tempfile, zipfile, stat, gzip
from datetime import datetime, timezone
from pathlib import Path
from logalyzer.records import NormalizedRecord

_CORR = re.compile(r"correlation_id[=:\"\s]+([A-Za-z0-9-]+)")
_DOMAIN_KEYS = ("order_id", "payment_id", "auth_id", "reservation_id", "sku", "user_id", "trace_id")
_PLAIN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2}[.,]\d{3})"
    r"\s+\[(?P<thread>[^\]]+)\]\s+(?P<level>[A-Z]+)\s+(?P<logger>\S+)\s+[-—]\s+(?P<msg>.*)$")
_INLINE_ID = re.compile(r"\b(auth_id|order_id|reservation_id|sku|user_id)[=:]\s?([A-Za-z0-9._-]+)")

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
# ---------------------------------------------------------------------------
_TS_ISO = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})[.,](?P<ms>\d{3})"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?")
_TS_EPOCH_MS = re.compile(r"^(?P<epoch>\d{13})(?!\d)")
_TS_EPOCH_S = re.compile(r"^(?P<epoch>\d{10})(?!\d)")
_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_TS_SYSLOG = re.compile(
    r"^(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})")
_GEN_THREAD = re.compile(r"^\s*\[(?P<thread>[^\]]+)\]")
_GEN_LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|ERR|FATAL|SEVERE|CRITICAL)\b")
_GEN_SEP = re.compile(r"\s[-—]\s|:\s")

def _generic_parse(raw):
    """Generic timestamp-anchored line parser -- the universal-ingest
    fallback for text formats with no dedicated reader. Tried only after
    the logback `_PLAIN` regex has already failed on the line (see
    `_read_plaintext`), so the pack's existing byte-identical behavior is
    untouched: this function only ever fires on lines `_PLAIN` rejects.

    Returns a dict {timestamp, level, logger, msg, quality} on a recognized
    leading timestamp, or None when the line has none -- the caller then
    treats it as a continuation of the previous record (multi-line folding)
    instead of a new one.
    """
    m = _TS_ISO.match(raw)
    if m:
        ms = m.group("ms")
        tz = m.group("tz") or "Z"
        ts = "%sT%s.%s%s" % (m.group("date"), m.group("time"), ms, tz)
        end, ts_kind = m.end(), "iso"
    else:
        m = _TS_EPOCH_MS.match(raw)
        if m:
            epoch_ms = int(m.group("epoch"))
            seconds, millis = divmod(epoch_ms, 1000)
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % millis
            end, ts_kind = m.end(), "epoch"
        else:
            m = _TS_EPOCH_S.match(raw)
            if m:
                epoch_s = int(m.group("epoch"))
                dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
                ts = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                end, ts_kind = m.end(), "epoch"
            else:
                m = _TS_SYSLOG.match(raw)
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

def _read_plaintext(lines, service_hint, ref, masker):
    out = []
    prev = None
    fold_count = 0
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
            continue

        # Only reached once the existing logback regex has rejected the
        # line, so pack behavior (which only ever exercises _PLAIN) is
        # byte-identical -- this generic path is purely additive.
        g = _generic_parse(raw)
        if g is not None:
            body, applied = masker.mask_with_flag(g["msg"] if g["msg"] else raw)
            corr = _CORR.search(raw)
            domain = {k: v for k, v in _INLINE_ID.findall(raw)}
            rec = NormalizedRecord(
                timestamp=g["timestamp"], service=service_hint,
                level=g["level"] or "UNKNOWN", body=body,
                correlation_id=corr.group(1) if corr else "",
                domain_ids=domain, source_ref=ref, source_line=i,
                parse_quality=g["quality"], redaction_applied=applied,
                attrs={"logger": g["logger"]} if g["logger"] else {})
            out.append(rec); prev = rec; fold_count = 0
            continue

        # No recognized leading timestamp at all (neither _PLAIN nor the
        # generic bank): a continuation line -- Java stack traces, wrapped
        # messages -- folds into the previous record's body instead of
        # becoming a standalone UNKNOWN singleton. Capped at _FOLD_CAP
        # appended lines per record to bound memory on runaway traces; a
        # truncation marker is appended once when the cap is hit. Only
        # when there is no previous record (start of file) does the line
        # become its own standalone partial record -- same as the
        # pre-folding fallback behavior.
        if prev is not None and fold_count < _FOLD_CAP:
            masked, applied = masker.mask_with_flag(raw)
            fold_count += 1
            if fold_count == _FOLD_CAP:
                prev.body += "\n" + masked + "\n" + _FOLD_TRUNC_MARKER
            else:
                prev.body += "\n" + masked
            if applied:
                prev.redaction_applied = True
            continue
        if prev is not None:
            # past the fold cap: still a continuation, silently absorbed
            # (the truncation marker was already emitted once, above)
            continue

        body, applied = masker.mask_with_flag(raw)
        corr = _CORR.search(raw)
        rec = NormalizedRecord(
            timestamp="", service=service_hint, level="UNKNOWN", body=body,
            correlation_id=corr.group(1) if corr else "",
            source_ref=ref, source_line=i, parse_quality="partial",
            redaction_applied=applied)
        out.append(rec); prev = rec; fold_count = 0
    return out

def _parse_lines(lines, hint, ref, masker):
    fmt = sniff_format(lines[:5], ref)
    if fmt == "jsonl":
        return fmt, _read_jsonl(lines, hint, ref, masker)
    if fmt == "plaintext":
        return fmt, _read_plaintext(lines, hint, ref, masker)
    from logalyzer.ingest_structured import read_structured
    return fmt, read_structured(fmt, lines, hint, ref, masker)

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

def _sniff_file_format(p):
    """Cheap format sniff for stats display: reads only the first few lines
    (not the whole file) so the 200MB size cap doesn't force a second full
    read of a huge file just to label it."""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            first_lines = [next(f, "") for _ in range(5)]
    except OSError:
        return "plaintext"
    return sniff_format(first_lines, p.name)

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
    try:
        if is_gz:
            lines = _read_gz_lines(p)
            fmt, recs = _parse_lines(lines, hint, name, masker)
        else:
            fmt = _sniff_file_format(p)
            recs = read_source(p, masker)
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
    stats["files"][name] = {"format": fmt, "ok": counts["ok"],
                            "partial": counts["partial"], "unparsed": counts["unparsed"]}
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
