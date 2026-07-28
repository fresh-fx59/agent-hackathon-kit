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
                if not isinstance(obj, dict):
                    raise ValueError("JSON must be an object, not a scalar or array")
            except (ValueError, TypeError):
                body, applied = masker.mask_with_flag(raw)
                out.append(NormalizedRecord(timestamp="", service="kafka", level="UNKNOWN",
                                            body=body, source_ref=ref, source_line=i,
                                            parse_quality="unparsed", redaction_applied=applied))
                continue
            payload = obj.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            body, applied = masker.mask_with_flag(
                "%s %s" % (obj.get("type", ""), json.dumps(payload, ensure_ascii=False)))
            out.append(NormalizedRecord(
                timestamp=str(obj.get("ts") or ""), service="kafka", level="INFO",
                body=body,
                domain_ids={k: v for k, v in payload.items() if k.endswith("_id") or k == "sku"},
                source_ref=ref, source_line=i, redaction_applied=applied,
                attrs={"event_type": obj.get("type", ""), "topic": obj.get("topic", ""),
                       "partition": obj.get("partition"), "offset": obj.get("offset")}))
        return out
    if fmt == "k8s":
        out = []
        for i, raw in enumerate(lines, 1):
            if not raw.strip(): continue
            m = _K8S.match(raw)
            body, applied = masker.mask_with_flag(m.group("msg") if m else raw)
            out.append(NormalizedRecord(
                timestamp=m.group("ts") if m else "", service="k8s",
                level="WARN" if (m and m.group("kind") == "Warning") else "INFO",
                body=body,
                source_ref=ref, source_line=i,
                parse_quality="ok" if m else "partial", redaction_applied=applied,
                attrs={"reason": m.group("reason"), "object": m.group("obj")} if m else {}))
        return out
    if fmt == "trace":
        try:
            doc = json.loads("\n".join(lines))
            if not isinstance(doc, dict):
                raise ValueError("Trace JSON must be an object, not a scalar or array")
        except (ValueError, TypeError, json.JSONDecodeError):
            # Malformed trace file: return one unparsed record
            masked_body, applied = masker.mask_with_flag(
                "\n".join(lines[:100]) if len(lines) > 100 else "\n".join(lines))
            return [NormalizedRecord(timestamp="", service="trace", level="UNKNOWN",
                                     body=masked_body, source_ref=ref, source_line=1,
                                     parse_quality="unparsed", redaction_applied=applied)]
        out = []
        for i, span in enumerate(doc.get("spans", []), 1):
            attrs = dict(span.get("attrs") or {})
            # Real trace packs key the span label "operation"; some producers
            # use "name" instead. Prefer whichever is present so span_name is
            # never silently dropped.
            span_name = span.get("name") or span.get("operation") or ""
            attrs.update({"span_id": span.get("span_id", ""), "span_name": span_name,
                          "duration_ms": span.get("duration_ms"), "span_status": span.get("status", "")})
            body, applied = masker.mask_with_flag("span %s %s" % (span_name, span.get("status", "")))
            out.append(NormalizedRecord(
                timestamp=str(span.get("start") or ""), service=str(span.get("service") or ""),
                level="INFO", body=body,
                trace_id=str(doc.get("trace_id") or ""), source_ref=ref, source_line=i, attrs=attrs,
                redaction_applied=applied))
        return out
    if fmt == "metrics":
        out = []
        for i, ln in enumerate(lines, 1):
            if not ln.strip() or ln.startswith("#"): continue
            body, applied = masker.mask_with_flag(ln)
            out.append(NormalizedRecord(timestamp="", service="metrics", level="INFO",
                                        body=body, source_ref=ref, source_line=i,
                                        redaction_applied=applied))
        return out
    return []
