import hashlib
import json
import re
from datetime import datetime


def rubric_sha(raw_bytes):
    """Compute first 12 hex chars of sha256 of raw bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()[:12]


def load_rules(path):
    """Load rules from JSON file, validate schema, compute rubric_sha."""
    with open(path, "rb") as f:
        raw = f.read()
    catalog = json.loads(raw.decode("utf-8"))

    # Validate required fields in each rule
    for r in catalog["rules"]:
        for key in ("id", "name", "status", "severity", "condition", "hypothesis"):
            if key not in r:
                raise ValueError("rule %s missing %s" % (r.get("id", "?"), key))

    catalog["rubric_sha"] = rubric_sha(raw)
    return catalog


def _ts(record):
    """Parse ISO timestamp to datetime, return None on error."""
    t = record.timestamp.rstrip("Z")
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        return None


def _match(matcher, item):
    """Check if a record item matches all criteria in the matcher dict."""
    r = item["record"]

    # Service must match if specified
    if matcher.get("service") and r.service != matcher["service"]:
        return False

    # Level must match if specified
    if matcher.get("level") and r.level != matcher["level"]:
        return False

    # Attribute key must be present if specified (check presence only, not value)
    if matcher.get("attr") and matcher["attr"] not in r.attrs:
        return False

    # Body regex must match if specified
    if matcher.get("body_regex"):
        if not re.search(matcher["body_regex"], r.body):
            return False

    return True


def _eval_sequence(cond, bundle):
    """
    Evaluate sequence condition: matchers must match in order within time window.
    Returns list of evidence IDs if sequence matches, None otherwise.
    Windowed rules (within_ms set) only fire on fully-timestamped chains.
    """
    seq = cond["sequence"]
    window_ms = cond.get("within_ms")

    hits = []
    start_ts = None
    idx = 0

    for item in bundle.items:
        # Already matched all sequence elements
        if idx >= len(seq):
            break

        # Check if current item matches the next expected sequence element
        if _match(seq[idx], item):
            t = _ts(item["record"])

            # For first match, record start time
            if idx == 0:
                # If window_ms is set and this item's timestamp is unparseable,
                # skip it and keep looking for a first element with valid timestamp
                if window_ms is not None and t is None:
                    continue
                start_ts = t
            # For subsequent matches, check time window
            else:
                # If window_ms is set, the item must have a parseable timestamp
                if window_ms is not None and t is None:
                    # Unparseable timestamp in windowed rule: skip this item
                    continue

                # Check time window (only if start_ts and t are both valid)
                if window_ms is not None and start_ts is not None and t is not None:
                    elapsed_ms = (t - start_ts).total_seconds() * 1000
                    if elapsed_ms > window_ms:
                        # Outside window, don't consume this item
                        continue

            hits.append(item["id"])
            idx += 1

    # Return hits only if all sequence elements matched
    return hits if idx == len(seq) else None


def _eval_all_of(cond, bundle):
    """
    Evaluate all_of condition: all matchers must match (order independent).
    Returns sorted list of unique evidence IDs if all match, None otherwise.
    """
    all_matchers = cond["all_of"]
    hits = []

    for matcher in all_matchers:
        found = [it for it in bundle.items if _match(matcher, it)]

        # If any matcher has no matches, condition fails
        if not found:
            return None

        # Collect evidence IDs from all matches
        hits.extend(it["id"] for it in found)

    # Return sorted unique evidence IDs
    return sorted(set(hits), key=lambda x: int(x.split("-")[1]))


def evaluate(catalog, bundle):
    """
    Evaluate all active rules against evidence bundle.
    Returns list of match dicts with rule_id, name, severity, hypothesis, invariant_ref, evidence_ids, rubric_sha.
    """
    out = []

    for rule in catalog["rules"]:
        # Only evaluate active rules
        if rule["status"] != "active":
            continue

        cond = rule["condition"]

        # Evaluate condition based on type
        if "sequence" in cond:
            ev = _eval_sequence(cond, bundle)
        else:
            ev = _eval_all_of(cond, bundle)

        # If condition matched, add result
        if ev:
            out.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "hypothesis": rule["hypothesis"],
                "invariant_ref": rule.get("invariant_ref"),
                "suggested_fix_ref": rule.get("suggested_fix_ref"),
                "evidence_ids": ev,
                "rubric_sha": catalog["rubric_sha"]
            })

    return out
