def related(records, correlation_id):
    seed = [r for r in records if r.correlation_id == correlation_id
            or r.trace_id == correlation_id]
    ids = set()
    for r in seed:
        ids.update(v for v in r.domain_ids.values() if v)
        if r.trace_id: ids.add(r.trace_id)
    out = []
    for r in records:
        if r in seed:
            out.append(r); continue
        if r.trace_id and r.trace_id in ids:
            out.append(r); continue
        if any(v in ids for v in r.domain_ids.values()):
            out.append(r)
    return sorted(out, key=lambda r: (r.timestamp == "", r.timestamp, r.source_ref, r.source_line))
