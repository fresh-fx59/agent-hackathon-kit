#!/usr/bin/env python3
"""lane_guard.py — the two lane failures that cost a whole metered run.

WHY THIS FILE EXISTS. On the v37 full run the harness was, by every signal it
owned, healthy: 180 calls, HTTP 200s, a complete stream on nearly all of them,
gates parsed, a verdict written. It was also wrong. `[SP]deepseek-v4-flash` is
an ALIAS, and linkapi fanned those 180 calls across TWO models —
deepseek-v4-pro-0813 (93), deepseek-v4-flash-0731 (81), DeepSeek-V4-Flash-0731
(6, a case variant of the same id). Nobody noticed for days, until a human
diffed the upstream ledger by hand.

Two consequences, and the harness could see BOTH as they happened:

  1. THE ARM UNDER TEST WAS NOT THE ARM REPORTED. Half the turns were answered
     by a different model. Any number from that run describes a mixture.
  2. TWO MODELS MEAN TWO PROVIDER CACHE POOLS. The prompt-cache hit rate fell
     68.1 % -> 28.0 %, and fresh prompt tokens went 5.92M -> 13.38M. That is
     the money signal, and it moves on call ~20, not on call 180.

Pinning the requested id (the previous commit) stops the fan-out. It does NOT
detect a provider that substitutes anyway — only a gate does, and a gate that
reads its own error path as "clean" is not a gate. Hence: every unknown here
is a breach, never a pass.

Shared by measure/upstream-log-proxy.py (live, aborts on the offending call)
and measure/lane-audit.py (after the fact, over the finished ledger), so the
family rule cannot drift between the thing that stops a run and the thing that
judges it.
"""
import json
import os
import re

# DEFAULTS, and where they come from. Every healthy run measured 68-88 %
# (v35 r2 88.1, v35 r3 73.2, v36-smoke 74.0, v36-full 68.1); the broken v37 was
# 28.0. 50 % sits below every healthy run and far above the broken one, so it
# separates them without straddling either. 20 calls because a cold start is
# genuinely uncached — call 1 has nothing to hit — and by 20 calls the v37 rate
# was already unambiguous, while 20 calls is ~11 % of a full run's spend.
DEFAULT_CACHE_MIN_RATE = 0.50
DEFAULT_CACHE_MIN_CALLS = 20

# WHAT "FAMILY" MEANS HERE, and why it is not just string equality.
#
# The pinned id and the alias it pins name the same model: `deepseek-v4-flash`
# and `deepseek-v4-flash-0731` must NOT trip the guard, or the guard fires on
# its own fix. `deepseek-v4-pro-0813` is a DIFFERENT model and must trip, even
# though it shares the `deepseek-v4-` stem. And the provider returned the same
# id in two casings (6 of 180 calls came back `DeepSeek-V4-Flash-0731`), which
# is a display difference, not a substitution.
#
# So the family of an id is the id with three things removed, in this order:
#   1. a leading bracketed routing tag  — `[SP]`, `[FREE]`, whatever is next
#   2. case                             — normalise, never compare casing
#   3. ONE trailing release stamp       — `-0731`, `-20260731`, `-2026-07-31`,
#                                         `-latest`, `-preview`
# and nothing else. In particular the generation marker `v4` survives (the
# stamp pattern needs 3+ digits, `v4` has one), so deepseek-v4 and deepseek-v5
# are different families, which is the whole point.
_ROUTING_TAG = re.compile(r"^\[[^]]*\]")
_RELEASE_STAMP = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{3,8}|latest|preview)$")


def model_family(name):
    """Family of a model id, or None when there is no id to speak of.

    None means UNKNOWN, and callers must treat unknown as a breach rather than
    as a match — that is the whole lesson of this branch.
    """
    if not isinstance(name, str):
        return None
    text = _ROUTING_TAG.sub("", name.strip()).strip().lower()
    if not text:
        return None
    return _RELEASE_STAMP.sub("", text) or None


def same_family(expected, returned):
    """True only when both ids are known AND name the same family."""
    want, got = model_family(expected), model_family(returned)
    return bool(want) and bool(got) and want == got


def cache_tokens(usage):
    """(prompt_tokens, cached_prompt_tokens) for one ledger row's usage.

    THE FORMULA, stated once: cumulative hit rate = sum(cached_tokens) /
    sum(prompt_tokens) over every call that billed prompt tokens. Both numbers
    are the provider's own, read from
    `usage.prompt_tokens_details.cached_tokens` and `usage.prompt_tokens`.
    Rows that carry no `prompt_tokens_details` at all count as ZERO cached
    against their full prompt_tokens — that is what an absent field means on
    this lane (28 of the 180 v37 rows are like that), and counting them as
    "unknown, skip" would let a provider hide a collapse by dropping the field.
    Replaying this over the v37 ledger reproduces 5,192,376 / 18,568,929 =
    27.96 %, the number the post-mortem recorded as 28.0 %.

    Returns (0, 0) for a row that billed nothing — an HTTP error, a refused
    request — so failed attempts neither help nor hurt the rate.
    """
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens")
    if type(prompt) is not int or prompt <= 0:
        return 0, 0
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    if type(cached) is not int or cached < 0:
        cached = 0
    return prompt, min(cached, prompt)


def cache_breach(calls, prompt_tokens, cached_tokens,
                 min_rate=DEFAULT_CACHE_MIN_RATE,
                 min_calls=DEFAULT_CACHE_MIN_CALLS):
    """Detail string when the cumulative cache rate has collapsed, else None."""
    if calls < min_calls or prompt_tokens <= 0:
        return None
    rate = cached_tokens / prompt_tokens
    if rate >= min_rate:
        return None
    return ("prompt-cache hit rate %.1f%% after %d billed calls, below the %.1f%% "
            "floor (%d cached / %d prompt tokens) — a provider-side model "
            "substitution splits the cache pool exactly like this"
            % (rate * 100, calls, min_rate * 100, cached_tokens, prompt_tokens))


def _rows(path):
    with open(path, encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("ledger line is not an object")
            yield row


def audit_ledger(ledger_path, expected_identity="", cache_guard=True,
                 min_rate=DEFAULT_CACHE_MIN_RATE,
                 min_calls=DEFAULT_CACHE_MIN_CALLS, abort_path=""):
    """Judge a finished run's upstream ledger. Returns (reason, detail) or None.

    FAIL-CLOSED, deliberately and in every direction. A ledger that is absent,
    empty, unparseable, or missing the fields this reads is NOT a clean run: it
    is a run whose model identity was never measured, and unmeasured is not
    clean. The proxy writes this ledger before it answers the client, so a
    missing file means the proxy died — which is precisely when a substitution
    would go unseen.
    """
    if abort_path and os.path.exists(abort_path):
        # The live guard already stopped this run. Its reason wins: it was
        # observed on the call that caused it, with the run still alive.
        try:
            with open(abort_path, encoding="utf-8") as source:
                row = json.load(source)
            reason = row.get("reason") if isinstance(row, dict) else None
            if not isinstance(reason, str) or not reason:
                raise ValueError("no reason")
            return reason, str(row.get("detail") or "")[:400]
        except (OSError, ValueError, TypeError):
            return "LANE_ABORT_UNREADABLE", (
                "the proxy wrote an abort marker at %s and it cannot be read — "
                "the run stopped for a reason nothing recorded" % abort_path)

    try:
        rows = list(_rows(ledger_path))
    except FileNotFoundError:
        return "LEDGER_MISSING", (
            "no upstream ledger at %s — the run has no proof of which model "
            "answered it, and unmeasured is not clean" % ledger_path)
    except (OSError, ValueError, TypeError) as exc:
        return "LEDGER_MALFORMED", (
            "upstream ledger %s could not be read: %s" % (ledger_path, exc))
    if not rows:
        return "LEDGER_EMPTY", (
            "upstream ledger %s has no rows — no call was ever attributed"
            % ledger_path)

    calls = prompt_tokens = cached_tokens = 0
    named = 0
    for index, row in enumerate(rows, 1):
        for field in ("requested_model", "returned_model", "status"):
            if field not in row:
                return "LEDGER_MALFORMED", (
                    "row %d of %s has no %r field" % (index, ledger_path, field))
        status = row.get("status")
        answered = type(status) is int and 200 <= status < 300
        if not answered:
            continue
        returned = row.get("returned_model")
        if expected_identity and isinstance(returned, str) and returned.strip():
            named += 1
            if not same_family(expected_identity, returned):
                return "RETURNED_MODEL_FAMILY_MISMATCH", (
                    "row %d: asked for %s (family %s), the provider answered as "
                    "%s (family %s) — the run measured a model it did not request"
                    % (index, expected_identity, model_family(expected_identity),
                       returned, model_family(returned)))
        billed, hit = cache_tokens(row.get("usage"))
        if billed:
            calls += 1
            prompt_tokens += billed
            cached_tokens += hit

    if expected_identity and not named:
        return "RETURNED_MODEL_UNKNOWN", (
            "%d rows in %s and not one names the model that answered, though "
            "%s was required — the identity was never measured"
            % (len(rows), ledger_path, expected_identity))

    if cache_guard:
        detail = cache_breach(calls, prompt_tokens, cached_tokens, min_rate, min_calls)
        if detail:
            return "PROMPT_CACHE_COLLAPSE", detail
    return None
