import json, re
from logalyzer.records import NormalizedRecord

_K8S = re.compile(r"^(?P<ts>\S+)\s+(?P<kind>Normal|Warning)\s+(?P<reason>\S+)\s+(?P<obj>\S+)\s+(?P<msg>.*)$")

def read_structured(fmt, lines, service_hint, ref, masker):
    if fmt == "kafka":
        out = []
        for i, raw in enumerate(lines, 1):
            if not raw.strip(): continue
            try:
                obj = json.loads(raw)
            except ValueError:
                out.append(NormalizedRecord(timestamp="", service="kafka", level="UNKNOWN",
                                            body=masker.mask(raw), source_ref=ref, source_line=i,
                                            parse_quality="unparsed"))
                continue
            payload = obj.get("payload") or {}
            out.append(NormalizedRecord(
                timestamp=str(obj.get("ts") or ""), service="kafka", level="INFO",
                body=masker.mask("%s %s" % (obj.get("type", ""), json.dumps(payload, ensure_ascii=False))),
                domain_ids={k: v for k, v in payload.items() if k.endswith("_id") or k == "sku"},
                source_ref=ref, source_line=i,
                attrs={"event_type": obj.get("type", ""), "topic": obj.get("topic", ""),
                       "partition": obj.get("partition"), "offset": obj.get("offset")}))
        return out
    if fmt == "k8s":
        out = []
        for i, raw in enumerate(lines, 1):
            if not raw.strip(): continue
            m = _K8S.match(raw)
            out.append(NormalizedRecord(
                timestamp=m.group("ts") if m else "", service="k8s",
                level="WARN" if (m and m.group("kind") == "Warning") else "INFO",
                body=masker.mask(m.group("msg") if m else raw),
                source_ref=ref, source_line=i,
                parse_quality="ok" if m else "partial",
                attrs={"reason": m.group("reason"), "object": m.group("obj")} if m else {}))
        return out
    if fmt == "trace":
        doc = json.loads("\n".join(lines))
        out = []
        for i, span in enumerate(doc.get("spans", []), 1):
            attrs = dict(span.get("attrs") or {})
            attrs.update({"span_id": span.get("span_id", ""), "span_name": span.get("name", ""),
                          "duration_ms": span.get("duration_ms"), "span_status": span.get("status", "")})
            out.append(NormalizedRecord(
                timestamp=str(span.get("start") or ""), service=str(span.get("service") or ""),
                level="INFO", body=masker.mask("span %s %s" % (span.get("name", ""), span.get("status", ""))),
                trace_id=str(doc.get("trace_id") or ""), source_ref=ref, source_line=i, attrs=attrs))
        return out
    if fmt == "metrics":
        return [NormalizedRecord(timestamp="", service="metrics", level="INFO",
                                 body=masker.mask(ln), source_ref=ref, source_line=i)
                for i, ln in enumerate(lines, 1) if ln.strip() and not ln.startswith("#")]
    return []
