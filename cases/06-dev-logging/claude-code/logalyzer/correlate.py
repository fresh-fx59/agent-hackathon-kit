from logalyzer.formats import parse_iso_dt

_SORT_KEY = lambda r: (r.timestamp == "", r.timestamp, r.source_ref, r.source_line)


def related(records, correlation_id):
    if not correlation_id:
        return []
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
    return sorted(out, key=_SORT_KEY)


def related_window(records, since_iso, until_iso, service=None):
    """Normalization v2 -- time-frame correlation (design doc "Time-frame
    correlation", IDs optional). Records whose normalized timestamp falls
    in [since_iso, until_iso] (inclusive), optionally restricted to
    `service`. `since_iso`/`until_iso` are ISO-8601 strings (any offset or
    fractional-second width `formats.parse_iso_dt` accepts); either may be
    None/empty to leave that side of the window open.

    A record with an empty or unparseable timestamp cannot prove it falls
    inside the window, so it is EXCLUDED from the returned list -- but it
    is still counted, so a caller (the CLI / report) can disclose "N
    records were skipped for lacking a timestamp" instead of having them
    silently vanish from the evidence. The service filter (when given) is
    applied first: a record from an unrelated service does not count
    towards `excluded_no_ts` even if its own timestamp happens to be
    unparseable -- it was never a candidate for this investigation's
    evidence in the first place.

    Returns (matched_records, excluded_no_ts_count), matched_records sorted
    with the exact same key as `related()` (timestamp, then source_ref,
    then source_line) so downstream evidence numbering/reporting behaves
    identically regardless of which correlation basis produced the bundle.
    """
    since_dt = parse_iso_dt(since_iso) if since_iso else None
    until_dt = parse_iso_dt(until_iso) if until_iso else None
    out = []
    excluded_no_ts = 0
    for r in records:
        if service and r.service != service:
            continue
        try:
            ts = parse_iso_dt(r.timestamp)
        except (ValueError, TypeError):
            excluded_no_ts += 1
            continue
        if since_dt is not None and ts < since_dt:
            continue
        if until_dt is not None and ts > until_dt:
            continue
        out.append(r)
    return sorted(out, key=_SORT_KEY), excluded_no_ts
