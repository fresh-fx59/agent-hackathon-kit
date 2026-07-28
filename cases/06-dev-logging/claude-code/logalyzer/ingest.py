import json, re, tempfile, zipfile, stat
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

def _read_plaintext(lines, service_hint, ref, masker):
    out = []
    for i, raw in enumerate(lines, 1):
        raw = raw.rstrip("\n")
        if not raw.strip(): continue
        m = _PLAIN.match(raw)
        body_src = m.group("msg") if m else raw
        body, applied = masker.mask_with_flag(body_src)
        corr = _CORR.search(raw)
        domain = {k: v for k, v in _INLINE_ID.findall(raw)}
        ts = ""
        if m:
            ts = "%sT%sZ" % (m.group("date"), m.group("time").replace(",", "."))
        out.append(NormalizedRecord(
            timestamp=ts, service=service_hint,
            level=m.group("level") if m else "UNKNOWN", body=body,
            correlation_id=corr.group(1) if corr else "",
            domain_ids=domain, source_ref=ref, source_line=i,
            parse_quality="ok" if m else "partial",
            redaction_applied=applied,
            attrs={"logger": m.group("logger")} if m else {}))
    return out

def read_source(path, masker, service_hint=""):
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hint = service_hint or _service_from_name(path)
    fmt = sniff_format(lines[:5], path.name)
    ref = path.name
    if fmt == "jsonl":
        return _read_jsonl(lines, hint, ref, masker)
    if fmt == "plaintext":
        return _read_plaintext(lines, hint, ref, masker)
    # kafka/k8s/trace/metrics readers arrive in Task 4
    from logalyzer.ingest_structured import read_structured
    return read_structured(fmt, lines, hint, ref, masker)

_ZIP_MAX_ENTRIES = 500
_ZIP_MAX_UNCOMPRESSED = 100 * 1024 * 1024

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

def read_all(root, masker):
    root = Path(root)
    if root.suffix == ".zip":
        tmp = tempfile.TemporaryDirectory()
        _safe_extract(root, tmp.name)
        recs = read_all(Path(tmp.name), masker)
        tmp.cleanup()
        return recs
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in (".log", ".jsonl", ".txt", ".json"):
            try:
                out.extend(read_source(p, masker))
            except Exception as e:
                # A file that can't be read must not vanish silently: surface
                # one visible unparsed record naming the failure (exception
                # class only, never the raw message, which could leak paths
                # or content). Other files in the directory still get processed.
                out.append(NormalizedRecord(
                    timestamp="", service=_service_from_name(p), level="UNKNOWN",
                    body="ingest error: %s" % type(e).__name__,
                    source_ref=p.name, source_line=0, parse_quality="unparsed"))
    return out
