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

THIS CHECK IS THE ONLY DEFENCE, NOT A SECOND LINE. PR #77 tried to stop the
fan-out on the request side by pinning `[SP]deepseek-v4-flash-0731`. Measured
2026-08-26: that id is not routable. `GET https://linkapi.ai/v1/models` returns
130 models and exactly four `deepseek-v4` ids — `[SP]deepseek-v4-flash`,
`[SP]deepseek-v4-pro` and their `[次]` twins — and the pinned launch got HTTP
503 `model_not_found` ("No available channel for model
[SP]deepseek-v4-flash-0731 under group auto (distributor)") on all 13 of its
calls, with zero billed usage. `-0731` is a value the provider RETURNS; it
cannot be SENT. So the runners request the alias, the expected identity is the
alias, and the returned-side family check below is the whole defence: nothing
upstream of it prevents a substitution, it only detects one. A gate that reads
its own error path as "clean" is not a gate. Hence: every unknown here is a
breach, never a pass. That rule is load-bearing in four places and each
one was a real defect found by review on 2026-08-26:

  * an EMPTY expected identity used to disable the identity check silently.
    It is now `EXPECTED_IDENTITY_UNKNOWN`, a breach. An unpinned lane must say
    so out loud (`identity_check=False` / `--no-identity-check`).
  * `prompt_tokens` that is present but not an `int` used to make the row bill
    NOTHING, so a provider serialising `1000.0` or `"1000"` hid a total cache
    collapse behind `calls == 0`. Numerics are coerced; a present-but-
    unreadable usage field is `USAGE_UNREADABLE`, a breach.
  * a NEWER dated snapshot of the pinned model used to pass the family rule.
  * the 50 % cache floor was justified with FINAL rates while the guard fires
    on the CUMULATIVE rate — see DEFAULT_CACHE_MIN_RATE.

Shared by measure/upstream-log-proxy.py (live, aborts on the offending call)
and measure/lane-audit.py (after the fact, over the finished ledger), so the
family rule cannot drift between the thing that stops a run and the thing that
judges it.
"""
import json
import os
import re

# DEFAULTS, AND THE ONLY NUMBERS THAT JUSTIFY THEM.
#
# The guard does not fire on a run's FINAL cache rate. It fires on the
# CUMULATIVE rate the moment the call count reaches the floor, and a cumulative
# rate is still dragged down by the cold start at that point. Judging the floor
# by final rates — which the first version of this docstring did — certifies a
# margin the guard never sees. Re-derived from the five real ledgers on
# 2026-08-26 with exactly the formula below (cumulative cached/prompt over
# billed 2xx calls):
#
#   run         final   @20     @30    min at >=20   min at >=30
#   v37 BROKEN  28.0 %  12.3 %  14.4 %  10.3 % @23    14.4 % @30
#   v36-full    68.1 %  57.8 %  60.8 %  57.8 % @20    60.8 % @30
#   v36-smoke   74.0 %  54.8 %  69.4 %  54.8 % @20    67.6 % @49
#   v35 r2      73.2 %  54.9 %  60.5 %  54.9 % @20    60.5 % @30
#   v35 r3      88.1 %  86.3 %  91.0 %  86.3 % @20    86.8 % @47
#
# At 20 calls the worst healthy run sits at 54.8 %, i.e. FOUR AND A HALF points
# above a 50 % floor — v36-smoke is at 49.4 % on call 18 and v35 r2 at 51 % on
# call 19. One slower cold start and a healthy paid run dies. That is not a
# margin, it is a coin flip.
#
# So: wait to 30 calls and drop the floor to 35 %. At 30 calls the worst
# healthy run is 60.5 % (25.5 points of headroom) and the broken run is 14.4 %
# (20.6 points below the floor) — a two-sided margin of about twenty points
# instead of five. The cost is 10 extra calls (~6 % of a 180-call run) before
# the CACHE backstop can fire, and it is a backstop: the identity guard catches
# the real v37 substitution on row 2 of 180.
#
# (The project record had v35 r2 and r3 swapped; r2 is 73.2 %, r3 is 88.1 %.)
DEFAULT_CACHE_MIN_RATE = 0.35
DEFAULT_CACHE_MIN_CALLS = 30

# WHAT "THE SAME MODEL" MEANS HERE, and why it is neither string equality nor
# a loose stem match.
#
# Normalising an id means removing exactly two things:
#   1. EVERY leading bracketed routing tag — `[SP]`, `[FREE]`, `[SP][X]`
#   2. case
# and nothing else. `deepseek-v4-flash-0731` stays whole.
#
# WHY THE ROUTING TAG IS STRIPPED ON BOTH SIDES, given that `[SP]` and `[FREE]`
# really are different routing tiers with different cache pools. Because the
# provider does not report the tag reliably and never reported a tag we did not
# ask for. The v36 ledger sent `[SP]deepseek-v4-flash` on all 185 calls and got
# back `deepseek-v4-flash-0731` 119x, `DeepSeek-V4-Flash-0731` 23x,
# `deepseek-v4-flash` 29x and `[SP]deepseek-v4-flash` 11x — the SAME request,
# tagged on 11 rows and untagged on 171. Comparing tags would abort every real
# run on row 1. The tier is not at risk in the way the model id is: it is our
# own request string, it is recorded verbatim in `requested_model`/`sent_model`
# on every ledger row, and it cannot change unless we change it. The model that
# ANSWERS is the thing the provider chooses, and that is what this guards.
#
# THE MATCH RULE, once the tag and case are gone. `returned` matches `expected`
# when it is the expected id, or the expected id plus a further suffix:
#
#     deepseek-v4-flash-0731            == expected              -> match
#     deepseek-v4-flash-0731-preview    expected + a variant     -> match
#     deepseek-v4-flash-0731-fp8        expected + a variant     -> match
#     deepseek-v4-flash                 the alias it pins        -> match
#     deepseek-v4-flash-1210            a DIFFERENT snapshot     -> BREACH
#     deepseek-v4-pro-0813              a different model        -> BREACH
#
# IN PRACTICE, on this provider, `expected` is always the ALIAS
# `[SP]deepseek-v4-flash` (a dated id 503s — see the module docstring), so the
# live shape is row 4 above: we ask for the alias and `deepseek-v4-flash-0731`
# comes back and matches by suffix, while `deepseek-v4-pro-0813` breaches. The
# stamped-expected rows are kept because the rule must stay correct if a
# provider ever does list a dated id.
#
# The alias case is the one exception, and it is narrow on purpose: when the
# expected id carries a release stamp, the id with that ONE stamp removed also
# matches, because `[SP]deepseek-v4-flash-0731` and `[SP]deepseek-v4-flash`
# name the same model and the guard must not fire on its own fix. A DIFFERENT
# stamp does not match — a stamped expectation exists precisely to nail one
# snapshot, and a provider rolling flash to `-1210` is a new cache pool, i.e.
# the v37 failure again wearing a friendlier name.
#
# Suffix matching is what keeps `-preview` / `-fp8` / `-thinking` from killing
# a run that got exactly the model it asked for; the previous rule stripped one
# trailing stamp from each side and read those as different families.
_ROUTING_TAG = re.compile(r"^(?:\[[^]]*\]\s*)+")
# 3+ digits, so a generation marker (`v4`, `gpt-5.5`, `qwen3-235b`) is never
# read as a date and `deepseek-v4` stays distinct from `deepseek-v5`.
_RELEASE_STAMP = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{3,8}|latest|preview)$")


class UsageUnreadable(ValueError):
    """A row carries usage numbers that cannot be read as numbers.

    Not "billed nothing" — unknown. Raised so no caller can quietly count the
    row as free and let a cache collapse pass with `calls == 0`.
    """


def normalised_id(name):
    """A model id with routing tags and case removed, or None when there is none."""
    if not isinstance(name, str):
        return None
    return _ROUTING_TAG.sub("", name.strip()).strip().lower() or None


def model_family(name):
    """Family of a model id, or None when there is no id to speak of.

    The coarse notion: normalised, minus ONE trailing release stamp. Kept for
    the unpinned case and for the human-readable half of a breach message; the
    match decision itself is `same_family`, which is stricter.

    None means UNKNOWN, and callers must treat unknown as a breach rather than
    as a match — that is the whole lesson of this branch.
    """
    text = normalised_id(name)
    if not text:
        return None
    return _RELEASE_STAMP.sub("", text) or None


def same_family(expected, returned):
    """True only when both ids are known AND `returned` names the expected model.

    See the block above for the rule. Both ids unknown is False, not a match:
    two unmeasured things are not the same thing.
    """
    want, got = normalised_id(expected), normalised_id(returned)
    if not want or not got:
        return False
    if got == want or got.startswith(want + "-"):
        return True
    # A pinned id also accepts the unstamped alias it pins — and nothing else.
    stem = _RELEASE_STAMP.sub("", want)
    return bool(stem) and stem != want and got == stem


def _count(value, field):
    """A usage number as a non-negative int, or None when the field is absent.

    Providers are not consistent about JSON number types: the same field comes
    back as `1000`, `1000.0` and `"1000"` across vendors and SDK versions. All
    three mean one thousand tokens. Anything that is NOT a count — a bool, a
    dict, `"lots"`, a negative, a fractional token — raises, because the only
    other option is to score the row as free, and "this row cost nothing" is
    exactly the lie a collapsed cache needs.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise UsageUnreadable("%s is a boolean" % field)
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")) or value % 1:
            raise UsageUnreadable("%s is not a whole number: %r" % (field, value))
        number = int(value)
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except (ValueError, AttributeError):
            raise UsageUnreadable("%s is not a number: %r" % (field, value[:40]))
    else:
        raise UsageUnreadable("%s has type %s" % (field, type(value).__name__))
    if number < 0:
        raise UsageUnreadable("%s is negative: %d" % (field, number))
    return number


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
    request, no usage object, no `prompt_tokens` key — so failed attempts
    neither help nor hurt the rate.

    Raises UsageUnreadable when a usage number is PRESENT and not a count. A
    type change is not "billed nothing": it is the same hiding trick as
    dropping the field, and it is the caller's job to turn it into a breach.
    """
    if not isinstance(usage, dict):
        return 0, 0
    prompt = _count(usage.get("prompt_tokens"), "usage.prompt_tokens")
    if not prompt:
        return 0, 0
    details = usage.get("prompt_tokens_details")
    cached = 0
    if isinstance(details, dict):
        cached = _count(details.get("cached_tokens"),
                        "usage.prompt_tokens_details.cached_tokens") or 0
    return prompt, min(cached, prompt)


def cache_breach(calls, prompt_tokens, cached_tokens,
                 min_rate=DEFAULT_CACHE_MIN_RATE,
                 min_calls=DEFAULT_CACHE_MIN_CALLS):
    """Detail string when the cumulative cache rate has collapsed, else None.

    `rate >= min_rate` passes: the floor is the lowest ACCEPTABLE rate, so a run
    sitting exactly on it is clean. A run one token below it is not.
    """
    if calls < min_calls or prompt_tokens <= 0:
        return None
    rate = cached_tokens / prompt_tokens
    if rate >= min_rate:
        return None
    return ("prompt-cache hit rate %.1f%% after %d billed calls, below the %.1f%% "
            "floor (%d cached / %d prompt tokens) — a provider-side model "
            "substitution splits the cache pool exactly like this"
            % (rate * 100, calls, min_rate * 100, cached_tokens, prompt_tokens))


# ======================================================================
# A PROXY SIGNAL MUST NEVER OVERRULE THE DIRECT MEASUREMENT IT STANDS IN FOR.
#
# The cache floor above is not a cost policy. Its own breach message says what
# it is for: "a provider-side model substitution splits the cache pool exactly
# like this". It was calibrated on the v37 shape, where the provider answered
# with a floating ALIAS and some billed rows named NO MODEL AT ALL, so the
# returned-side family check could not see the substitution and the cache rate
# was the only witness left. On that shape it is the right and only guard.
#
# MEASURED 2026-08-26, CloseRouter, run 20260826T212613Z-v39 (the ledger is
# committed verbatim at measure/tests/fixtures/v39-closerouter-30-calls.upstream.jsonl):
#   * 30 billed calls, HTTP 200 x30, finish_reason tool_calls x30,
#     stream_complete x30, upstream_error None x30, 0 discarded substitutions
#   * returned_model == route_expected_identity == deepseek/deepseek-v4-flash-0731
#     on 30 of 30 rows — the DIRECT signal, present and unanimous
#   * 399,232 cached / 2,427,380 prompt = 16.4 % cumulative, provider-reported
#     cost $0.021036 (lane-audit.py's own line on this exact fixture)
#   * only 7 of the 30 calls reported ANY cached_tokens, while the prompt grew
#     monotonically inside one conversation (45,636 -> 142,609 over calls 1-17,
#     then a fresh 25,593 at call 18 when a subagent started)
# A split cache pool halves hits across two models; this is a gateway that
# simply does not cache most calls. THIS 16.4 % IS A PROVIDER-POLICY FIGURE,
# NOT A TARGET, and it is recorded here for future calibration only. The floor
# is NOT lowered and there is deliberately no per-provider threshold table: a
# threshold nudge moves the cliff instead of removing it, and the next provider
# with a different caching policy walks straight off the new one.
#
# The live proxy guard tripped PROMPT_CACHE_COLLAPSE on call 30 of that healthy
# lane, call 31 got `403 proxy: lane aborted`, and a paid run was over.
#
# SO: the floor may judge ONLY the billed calls whose model identity is not
# directly established. When every billed call is identity-confirmed, a low
# rate is a COST FACT — reported loudly, with the numbers, because it is real
# money — and it is not a breach.
# ======================================================================
CACHE_JUDGEMENT_TERMS = (
    # Billed 2xx rows whose returned model was checked against the identity the
    # row was SENT under and matched. The floor may not judge these.
    "identity_confirmed_calls",
    "identity_confirmed_prompt_tokens",
    "identity_confirmed_cached_tokens",
    # Billed 2xx rows that named NO model, or that could not be checked because
    # no expected identity was declared for them. These, and only these, are
    # what the cache floor is a proxy signal FOR.
    "identity_unconfirmed_calls",
    "identity_unconfirmed_prompt_tokens",
    "identity_unconfirmed_cached_tokens",
)


def cache_terms():
    """A fresh named-counter dict. Never a bare running sum."""
    return dict.fromkeys(CACHE_JUDGEMENT_TERMS, 0)


def cache_terms_gaps(terms):
    """The terms that never got computed. Every key reaches the verdict here.

    This is the guard against this file's signature defect: a term that is
    computed and printed but absent from the exit code. If any of the six is
    missing or not a plain int, `cache_judgement` refuses the ledger instead of
    quietly judging over a bucket nobody filled.
    """
    if not isinstance(terms, dict):
        return CACHE_JUDGEMENT_TERMS
    return tuple(term for term in CACHE_JUDGEMENT_TERMS
                 if type(terms.get(term)) is not int)


def note_cache_call(terms, billed, hit, identity_confirmed):
    """Book one billed call into the confirmed or the unconfirmed bucket."""
    prefix = ("identity_confirmed_" if identity_confirmed
              else "identity_unconfirmed_")
    terms[prefix + "calls"] += 1
    terms[prefix + "prompt_tokens"] += billed
    terms[prefix + "cached_tokens"] += hit


def _rate(cached, prompt):
    return (100.0 * cached / prompt) if prompt else 0.0


def cache_cost_fact(terms, min_rate=DEFAULT_CACHE_MIN_RATE,
                    min_calls=DEFAULT_CACHE_MIN_CALLS):
    """One line stating what the cache actually cost, always. Never a verdict.

    Returned even when the rate is fine, because the operator's question is
    "what did this run pay for uncached prompt tokens", and that answer must not
    only exist on the path where something broke.
    """
    if cache_terms_gaps(terms):
        return ("prompt-cache: NOT MEASURED — %s never reached the judgement"
                % ", ".join(cache_terms_gaps(terms)))
    confirmed = terms["identity_confirmed_calls"]
    unconfirmed = terms["identity_unconfirmed_calls"]
    prompt = (terms["identity_confirmed_prompt_tokens"]
              + terms["identity_unconfirmed_prompt_tokens"])
    cached = (terms["identity_confirmed_cached_tokens"]
              + terms["identity_unconfirmed_cached_tokens"])
    line = ("prompt-cache COST FACT: %.1f%% hit rate over %d billed call(s) "
            "(%d cached / %d prompt tokens); %d identity-confirmed, %d "
            "unconfirmed — the %.1f%% floor judges the %d unconfirmed call(s) "
            "only"
            % (_rate(cached, prompt), confirmed + unconfirmed, cached, prompt,
               confirmed, unconfirmed, min_rate * 100, unconfirmed))
    if (unconfirmed == 0 and confirmed >= min_calls
            and _rate(cached, prompt) < min_rate * 100):
        # LOUDLY. This is the v39 CloseRouter shape: nothing is wrong with the
        # lane and the bill is still bigger than a cached lane's would be.
        line += ("; REAL MONEY: every billed call named the expected model, so "
                 "this low rate is the provider's caching policy, not a "
                 "substitution, and it is NOT a breach")
    return line


def cache_judgement(terms, min_rate=DEFAULT_CACHE_MIN_RATE,
                    min_calls=DEFAULT_CACHE_MIN_CALLS):
    """(reason, detail) when the cache floor is genuinely breached, else None.

    Judged over the UNCONFIRMED bucket only. See the block comment above.
    """
    gaps = cache_terms_gaps(terms)
    if gaps:
        return "CACHE_TERMS_INCOMPLETE", (
            "the cache judgement was asked for before %s were computed — an "
            "unfilled bucket cannot be scored as a healthy one"
            % ", ".join(gaps))
    detail = cache_breach(terms["identity_unconfirmed_calls"],
                          terms["identity_unconfirmed_prompt_tokens"],
                          terms["identity_unconfirmed_cached_tokens"],
                          min_rate, min_calls)
    if not detail:
        return None
    return "PROMPT_CACHE_COLLAPSE", (
        "%s; judged over the %d billed call(s) whose model identity was NOT "
        "directly confirmed (%d identity-confirmed call(s), %d cached / %d "
        "prompt tokens, are excluded — a proxy signal may not overrule the "
        "direct measurement it stands in for)"
        % (detail, terms["identity_unconfirmed_calls"],
           terms["identity_confirmed_calls"],
           terms["identity_confirmed_cached_tokens"],
           terms["identity_confirmed_prompt_tokens"]))


def _ledger_declares_identity(path):
    """True when at least one row carries a usable `route_expected_identity`.

    A run that hot-swapped provider mid-flight has no single global identity to
    pass in, so the rows carry their own. Any read failure answers False, which
    keeps every existing reason code in its existing order.
    """
    try:
        for row in _rows(path):
            value = row.get("route_expected_identity")
            if isinstance(value, str) and value.strip():
                return True
    except (OSError, ValueError, TypeError):
        return False
    return False


def _bump(counter, key):
    """One named counter, keyed by a JSON-safe label. Never a silent sum."""
    label = key if isinstance(key, str) else json.dumps(key, sort_keys=True)
    counter[label] = counter.get(label, 0) + 1


def _rows(path):
    with open(path, encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("ledger line is not an object")
            yield row



# ======================================================================
# THE ADVANCE HISTORY IS PART OF THE VERDICT, not a log file.
#
# The proxy can now ADVANCE the route when substitution retries are spent
# (fix 3): the wrong-model body is still never relayed, but instead of ending
# the run the proxy writes the next provider/model/identity to the live route
# file and re-issues the request. That saves runs - on the 463-row v38 ledger
# one call needed 13 retries against a cap of 12 and ended a 2h42m run after
# 280 good calls - and it changes what the run IS: a report synthesised across
# two models is a different scientific object than one from a single model.
#
# So an advance that is not visible in the artifacts is a lie about the
# measurement, and these are the NAMED terms that make it a breach. Every key
# is summed into the verdict below; none of them is printed-only. That is the
# defect every earlier PR in this series shipped, so it is asserted key by key
# in measure/tests/test_route_advance.py.
# ======================================================================
ROUTE_ADVANCE_CHECKS = (
    # The history exists and cannot be read: the run failed over for reasons
    # nothing recorded.
    "advance_history_unreadable",
    # The history says we advanced and the ledger shows ONE route: the failover
    # left no trace on the calls it changed.
    "advance_not_in_ledger",
    # The proxy's own named counters, checked against the events on disk. A
    # counter that cannot be wrong is a counter nobody has to maintain.
    "advances_performed_mismatch",
    "advances_blocked_mismatch",
    "advances_attempted_lt_performed",
    "routes_exhausted_lt_attempted",
    "requests_refused_missing",
)
_ADVANCE_REASON = {
    "advance_history_unreadable": "ROUTE_ADVANCE_HISTORY_UNREADABLE",
    "advance_not_in_ledger": "ROUTE_ADVANCE_UNRECORDED",
    "advances_performed_mismatch": "ROUTE_ADVANCE_COUNTERS_INCONSISTENT",
    "advances_blocked_mismatch": "ROUTE_ADVANCE_COUNTERS_INCONSISTENT",
    "advances_attempted_lt_performed": "ROUTE_ADVANCE_COUNTERS_INCONSISTENT",
    "routes_exhausted_lt_attempted": "ROUTE_ADVANCE_COUNTERS_INCONSISTENT",
    "requests_refused_missing": "ROUTE_ADVANCE_COUNTERS_INCONSISTENT",
}
_ADVANCE_COUNTER_KEYS = ("advances_attempted", "advances_performed",
                         "advances_blocked", "routes_exhausted",
                         "requests_refused")


def audit_route_advances(advances_path, route_span, summary=None):
    """Judge the advance history against the ledger. Returns (reason, detail).

    `advances_path` EMPTY means the caller does not know where the history is,
    and then this is a no-op: every pre-fix-3 ledger is judged byte-identically
    to before. A ledger that spans routes with NO history file is not a breach
    either - that is what a deliberate hand swap with
    hack/swap-upstream-route.sh looks like, and it is disclosed loudly by
    lane-audit.py rather than refused. What IS a breach is the other direction:
    a history that claims advances the ledger cannot show, or counters that do
    not match the events they were written beside.
    """
    checks = dict.fromkeys(ROUTE_ADVANCE_CHECKS, 0)
    detail = {}
    events = None
    if advances_path and os.path.exists(advances_path):
        try:
            with open(advances_path, encoding="utf-8") as source:
                events = [json.loads(line) for line in source if line.strip()]
            if not all(isinstance(event, dict) for event in events):
                raise ValueError("a history line is not an object")
        except (OSError, ValueError, TypeError) as exc:
            events = None
            checks["advance_history_unreadable"] = 1
            detail["advance_history_unreadable"] = (
                "the route-advance history %s exists and could not be read "
                "(%s) - the run may have changed provider for reasons nothing "
                "recorded" % (advances_path, exc))
    if events:
        performed = [e for e in events if e.get("event") == "route_advance"]
        blocked = [e for e in events if e.get("event") == "route_advance_blocked"]
        if summary is not None:
            summary["route_advances"] = len(performed)
            summary["route_advances_blocked"] = len(blocked)
        counters = events[-1].get("counters")
        if not isinstance(counters, dict) or not all(
                type(counters.get(key)) is int for key in _ADVANCE_COUNTER_KEYS):
            checks["advance_history_unreadable"] = 1
            detail["advance_history_unreadable"] = (
                "the last route-advance event in %s carries no readable "
                "counters (%r) - the accounting for a run that changed "
                "provider was never written" % (advances_path, counters))
            counters = None
        if len(performed) and route_span < 2:
            checks["advance_not_in_ledger"] = 1
            detail["advance_not_in_ledger"] = (
                "%d route advance(s) recorded in %s but the ledger names %d "
                "route identit(y/ies) - a failover that left no trace on the "
                "calls it changed is not auditable"
                % (len(performed), advances_path, route_span))
        if counters is not None:
            if counters["advances_performed"] != len(performed):
                checks["advances_performed_mismatch"] = 1
                detail["advances_performed_mismatch"] = (
                    "advances_performed=%d but %d route_advance event(s) are on "
                    "disk" % (counters["advances_performed"], len(performed)))
            if counters["advances_blocked"] != len(blocked):
                checks["advances_blocked_mismatch"] = 1
                detail["advances_blocked_mismatch"] = (
                    "advances_blocked=%d but %d route_advance_blocked event(s) "
                    "are on disk" % (counters["advances_blocked"], len(blocked)))
            if counters["advances_attempted"] < counters["advances_performed"]:
                checks["advances_attempted_lt_performed"] = 1
                detail["advances_attempted_lt_performed"] = (
                    "advances_attempted=%d is less than advances_performed=%d"
                    % (counters["advances_attempted"],
                       counters["advances_performed"]))
            if counters["routes_exhausted"] < counters["advances_attempted"]:
                checks["routes_exhausted_lt_attempted"] = 1
                detail["routes_exhausted_lt_attempted"] = (
                    "routes_exhausted=%d is less than advances_attempted=%d - "
                    "an advance was attempted without a route running out"
                    % (counters["routes_exhausted"],
                       counters["advances_attempted"]))
            if counters["requests_refused"] < len(blocked):
                checks["requests_refused_missing"] = 1
                detail["requests_refused_missing"] = (
                    "requests_refused=%d but %d advance(s) were blocked, and "
                    "every blocked advance refuses the client it could not "
                    "serve" % (counters["requests_refused"], len(blocked)))
    if summary is not None:
        summary["route_advance_checks"] = dict(checks)
    # THE SUM IS THE GATE. Built from the named dict, so adding a key to
    # ROUTE_ADVANCE_CHECKS cannot leave it out of the verdict by omission.
    if not sum(checks.values()):
        return None
    for key in ROUTE_ADVANCE_CHECKS:
        if checks[key]:
            return _ADVANCE_REASON[key], detail[key]
    raise AssertionError("a route-advance check fired with no detail")


# ======================================================================
# THE COST LINE MAY NEVER PRINT A ZERO IT DID NOT MEASURE.
#
# The v38 full run (2026-08-26, 2h42m, real money) wrote
# `{"accepted_calls": 0, "prompt_tokens": 0, "discarded_substitutions": 0}`
# and printed "0 accepted calls ... 0 prompt tokens paid for nothing", while
# its own abort marker recorded 278 calls / 27,773,863 prompt tokens and its
# ledger held 463 rows, 183 of them discarded substitutions. `audit_ledger`
# zeroed the summary and then returned the live guard's reason before reading
# a single row: on the breach path its docstring's promise of "true partial
# counts" was simply false. The one number that decides whether to spend money
# again was a zero on the exact run where it mattered.
#
# So the accounting is now a SEPARATE walk that runs first and always. It
# never returns a verdict and never raises: a row it cannot parse is COUNTED
# as unaccountable, because a row nobody could read is not a row that cost
# nothing. Absence of proof is not zero cost - the same fail-closed rule the
# rest of this lane follows - so a summary that could not be computed says
# `complete: false` with a reason, and lane-audit.py then refuses to print a
# cost line it did not compute.
# ======================================================================
# An aborted stream returns NO usage block, so `discarded_prompt_tokens` is
# genuinely 0 on the real run: the provider never told us. The same rows do
# carry `request_bytes` (62,773,646 across the run's 183 discards), and ~4
# bytes per token is the ordinary English/JSON ratio. That is an ESTIMATE and
# it is kept in its own key, with its own divisor, and printed with its own
# word. An estimate that can be mistaken for a measurement is worse than no
# estimate at all.
DISCARD_BYTES_PER_TOKEN = 4
_ESTIMATE_BASIS = ("discarded request_bytes / %d bytes-per-token - an ESTIMATE, "
                   "not a provider number" % DISCARD_BYTES_PER_TOKEN)

# EVERY ACCOUNTING TERM, NAMED. The sums the report prints are built from this
# tuple, and `_accounting_gaps` checks that each one actually reached the
# summary; a gap makes the summary incomplete, which lane-audit.py turns into
# LANE_ACCOUNTING_INCOMPLETE and a non-zero exit. A term that is computed and
# printed but never reaches an exit code is the defect class that shipped in
# every earlier PR in this series, so there is no printed-only number here.
ACCOUNTING_TERMS = (
    "ledger_rows", "rows_counted", "unaccountable_rows",
    "call_rows", "event_rows",
    # accepted_rows = calls the client actually received an answer for.
    # accepted_calls = of those, the ones the provider reported usage for.
    # billed_calls = every call row, because linkapi bills a FLAT 0.05 CNY per
    # CALL: calls are the bill there and a token-only line understates it.
    "accepted_rows", "accepted_calls", "billed_calls",
    "prompt_tokens", "cached_tokens",
    "discarded_substitutions", "discarded_prompt_tokens",
    "discarded_cached_tokens", "discarded_request_bytes", "request_bytes",
    "discarded_prompt_tokens_estimated", "estimate_bytes_per_token",
    "cost_usd_reported", "cost_usd_reported_calls",
    "refused_calls", "route_rows", "route_span",
    # THE PROVIDER'S OWN CLOCK, counted separately because it is neither a
    # substitution nor a clean answer: an HTTP 200 that generated tokens for
    # the full generation window and then handed back a gateway error chunk.
    # Nine of them on run 20260827T005241Z-v39. They are BILLED, so a run cut
    # repeatedly by the window must show the waste rather than a mystery.
    # `generation_window_s` is the window they were judged against — 0 when the
    # lane declares none, in which case the other two are always 0.
    "generation_window_exceeded_calls", "generation_window_exceeded_ms",
    "generation_window_s",
)


# ======================================================================
# A DETERMINISTIC 400 IS NOT A PROVIDER BURST, AND MUST NOT SPEND RETRIES.
#
# UPSTREAM_RETRY_MAX exists because linkapi's 400s were transient and
# minute-scale (measured 2026-08-02). These are not. Probed against CloseRouter
# 2026-08-26: `deepseek-v4-flash` with max_tokens=4 answers HTTP 400
# "the output token limit was exhausted by model reasoning before an answer was
# produced; increase max_completion_tokens/max_output_tokens" - a REASONING
# model can spend its entire output budget on thoughts before emitting a token,
# and a separate probe confirmed it (8 output tokens requested, all 8 returned
# as reasoning_tokens, content empty). The v38 dead run's single 400 is the
# sibling shape: 220 KB of request, and an SSE body reading "The
# `reasoning_content` in the thinking mode must be passed back to the API."
#
# Retrying either one twelve times cannot help; both need a request change, not
# patience. So they are matched on the provider's own words, given a NAME, and
# kept out of the burst path entirely. The name and the request's max_tokens go
# in the ledger row so a launcher can decide the tuning with the receipt in
# hand - this file deliberately changes no max_tokens default.
#
# NOTE ALSO, and it is the other half of the same finding: an EMPTY `content`
# is a normal response on a reasoning lane. `_scan_obj` already counts
# `reasoning_content` as a content event, so the first-token deadline does not
# misfire on a thinking model, and run-bench.sh's `broken_session` judges the
# ARTIFACT (no report file) rather than empty prose. Both were re-checked with
# this fix; neither treats "no content" as "no response".
# ======================================================================
_DETERMINISTIC_400 = (
    ("OUTPUT_BUDGET_EXHAUSTED_BY_REASONING",
     ("output token limit was exhausted by model reasoning",
      "increase max_completion_tokens",
      "increase max_output_tokens")),
    ("REASONING_CONTENT_NOT_RELAYED",
     ("reasoning_content` in the thinking mode must be passed back",
      "reasoning_content in the thinking mode must be passed back")),
)


# ======================================================================
# AN OUTPUT BUDGET THE PROVIDER CANNOT DELIVER INSIDE ITS OWN GENERATION
# WINDOW IS NOT A BUDGET - it is a guaranteed failure waiting for a long turn.
#
# Three paid runs in a row died here. On run 20260827T005241Z-v39 (r3) ten of
# 152 calls failed and NINE of them died at 90,341-90,416 ms: a 75-millisecond
# spread across nine independent calls is not jitter, it is a hard ceiling.
# Each one is an HTTP 200 carrying a gateway error chunk, recorded as
# `upstream_error_in_200:upstream_error` with `finish_reason: error`; r2 caught
# the payload verbatim:
#   {"error":{"code":"upstream_error","message":"upstream_timeout","status":502,
#    "metadata":{"provider_name":"Deepseek"}}}
# That is CloseRouter's own upstream generation timeout, 90 seconds. qwen
# surfaces it as `[API Error: ... Request timeout after 90s]`, whose "increase
# contentGenerator.timeout" hint is a HARD-CODED STRING and not a config read,
# so raising a client timeout cannot help. The request has to get smaller.
#
# Measured on r3's own 142 good calls: 142,661 completion tokens over
# 2,073,964 ms of wall clock, of which 909,873 ms was time-to-first-token.
# 1,164,091 ms of actual generation => 122.55 tok/s, average TTFT 6.4 s,
# largest completion actually returned 8,497 tokens. Every call requested up to
# 32,768 output tokens - FIVE TIMES more than the lane can produce inside its
# own window.
#
# So the budget is DERIVED: floor((window - ttft_reserve) x tok/s). The reserve
# is 35 s, the largest TTFT this project has ever recorded (the v38 run), NOT
# the 6.4 s average - the average is not what kills a run.
#
# TWO RULES, both of them load-bearing:
#   * a lane that declares NO window (unset, empty, 0 or -1) is never judged
#     here at all, so linkapi and the free lanes behave exactly as before;
#   * a budget that does not fit is REFUSED, never silently clamped, so the
#     number in the launcher always matches the number on the wire.
# ======================================================================
GENERATION_WINDOW_EXCEEDED = "GENERATION_WINDOW_EXCEEDED"
# The largest TTFT this project has recorded on any run, in seconds.
GENERATION_WINDOW_TTFT_RESERVE_S = 35
# Generation-only throughput, measured on r3's 142 good calls (see above).
GENERATION_WINDOW_TOKENS_PER_S = 122.6
# How close to the window counts as "at the window", as a FRACTION of the
# window rather than a fixed number of seconds - a fixed slack that is sane for
# a 90 s window silently matches everything on a short one. The nine r3 rows
# landed at 90,341-90,416 ms against a 90,000 ms window: all nine within 0.5 %,
# and slightly OVER it. 5 % is generous enough for a slower provider's own
# rounding and nowhere near the tenth failure, which died at 32,354 ms - 36 %
# of the window, and a different animal.
GENERATION_WINDOW_NEAR_FRACTION = 0.05
# The ledger keeps the error CODE, never the message text (a message can echo
# the request), so the needles have to match what is actually recorded. That is
# why the duration gate below is not optional: `upstream_error` on its own is
# far too generic to name a cause.
_WINDOW_ERROR_NEEDLES = ("upstream_timeout", "upstream_error",
                         "request timeout after")


def _positive_number(value):
    """`value` as a float when it is a usable positive number, else None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number if number > 0 else None


def generation_window_declared(generation_window_s):
    """True when this lane declared a generation window worth judging against.

    Unset, empty, 0, -1 and unparseable all mean UNDECLARED, and an undeclared
    window disarms every part of fix 9. That is deliberate: linkapi and the
    free lanes never measured a window, and a check they cannot pass is a check
    that would break them.
    """
    return _positive_number(generation_window_s) is not None


def generation_window_exceeded(text, duration_ms, generation_window_s):
    """True when this row is the provider's own generation clock cutting a turn.

    Needs BOTH halves: the gateway's error shape AND a duration at or near the
    declared window. The tenth r3 failure carries the identical error string at
    32,354 ms and is a different animal; matching on the message alone would
    mislabel it, and matching on the clock alone would mislabel a slow success.
    """
    window = _positive_number(generation_window_s)
    if window is None:
        return False
    duration = _positive_number(duration_ms)
    if duration is None:
        return False
    if duration < window * (1.0 - GENERATION_WINDOW_NEAR_FRACTION) * 1000.0:
        return False
    low = str(text or "").lower()
    return any(needle in low for needle in _WINDOW_ERROR_NEEDLES)


def fitting_max_output_tokens(generation_window_s, tokens_per_s,
                              ttft_reserve_s=GENERATION_WINDOW_TTFT_RESERVE_S):
    """The largest output budget this lane can actually deliver, or 0.

    0 means "not derivable" - an undeclared window, an unmeasured throughput,
    or a reserve that already eats the whole window. It is never a budget.
    """
    window = _positive_number(generation_window_s)
    rate = _positive_number(tokens_per_s)
    if window is None or rate is None:
        return 0
    reserve = _positive_number(ttft_reserve_s) or 0.0
    usable = window - reserve
    if usable <= 0:
        return 0
    return int(usable * rate)


def generation_window_refusal(max_output_tokens, tokens_per_s, ttft_reserve_s,
                              generation_window_s):
    """The abort message for an impossible launch, or None when it fits.

    Names ALL FOUR numbers it judged on and the value that would fit, because a
    refusal that does not say what to set instead only moves the problem. An
    undeclared window returns None without looking at anything else.
    """
    window = _positive_number(generation_window_s)
    if window is None:
        return None
    budget = _positive_number(max_output_tokens)
    if budget is None:
        # No budget declared at all means qwen-code auto-escalates max_tokens
        # (64K floor), which is strictly worse than the value we just refused.
        return ("SHERLOCK_MAX_OUTPUT_TOKENS is unset or unusable (%r) on a lane "
                "that declares a %g s generation window: qwen-code would "
                "auto-escalate max_tokens to at least 65536, which this lane "
                "cannot deliver. Set SHERLOCK_MAX_OUTPUT_TOKENS=%d."
                % (max_output_tokens, window,
                   fitting_max_output_tokens(window, tokens_per_s, ttft_reserve_s)))
    rate = _positive_number(tokens_per_s)
    if rate is None:
        # A declared window that cannot be checked is not a window that passes:
        # unmeasured is not safe, the same fail-closed rule as the rest of this
        # file.
        return ("SHERLOCK_OUTPUT_TOKENS_PER_S is unset or unusable (%r) on a "
                "lane that declares a %g s generation window, so the output "
                "budget of %d tokens could not be checked against it - and "
                "unmeasured is not safe. Measure the lane's generation-only "
                "throughput and declare it."
                % (tokens_per_s, window, int(budget)))
    reserve = _positive_number(ttft_reserve_s) or 0.0
    needed = budget / rate + reserve
    if needed <= window:
        return None
    fits = fitting_max_output_tokens(window, rate, reserve)
    return ("SHERLOCK_MAX_OUTPUT_TOKENS=%d cannot be delivered inside this "
            "lane's generation window: %d tokens / %g tokens-per-second = "
            "%.1f s of generation, + %g s reserved for the first token = "
            "%.1f s, against a declared SHERLOCK_GENERATION_WINDOW_S=%g. "
            "The provider cuts the turn and bills it. Set "
            "SHERLOCK_MAX_OUTPUT_TOKENS=%d or lower."
            % (int(budget), int(budget), rate, budget / rate, reserve, needed,
               window, fits))


def deterministic_refusal(status, text, duration_ms=None,
                          generation_window_s=0):
    """The named class of a failure that retrying cannot fix, else None.

    Matched on the provider's own message because that is the only place the
    distinction lives: linkapi's transient burst and a permanent refusal are
    the same integer.

    GENERATION_WINDOW_EXCEEDED is checked FIRST and for ANY status, because the
    real shape is an HTTP 200 with an error chunk spliced into the stream and
    the gateway can also surface it as a 502 - which IS in the burst-retry set.
    It is deterministic for a long turn, so naming it here is what keeps it out
    of the retry path entirely.
    """
    if generation_window_exceeded(text, duration_ms, generation_window_s):
        return GENERATION_WINDOW_EXCEEDED
    if status != 400 or not text:
        return None
    low = str(text).lower()
    for name, needles in _DETERMINISTIC_400:
        for needle in needles:
            if needle.lower() in low:
                return name
    return None


def _accounting_zero():
    """The accounting half of a fresh summary. Zeros here are placeholders and
    they are only ever believed once `complete` is True."""
    zeros = dict.fromkeys(ACCOUNTING_TERMS, 0)
    zeros["cost_usd_reported"] = 0.0
    zeros["estimate_bytes_per_token"] = DISCARD_BYTES_PER_TOKEN
    zeros.update({
        "schema": 2,
        "complete": False,
        "incomplete_reason": "the ledger was never read",
        "estimate_basis": _ESTIMATE_BASIS,
        "discarded_by_model": {}, "provider_refusals": {},
        # One entry per call the provider's clock cut, with the duration and
        # the request budget that caused it - so a run can say "I was cut by
        # the provider's clock" from its own artifacts.
        "generation_window_exceeded_detail": [],
        "route_generations": {}, "route_bases": {}, "route_identities": {},
        "route_advances": 0, "route_advances_blocked": 0,
        "route_advance_checks": {},
        # Which verdict came from where, so a reader never has to guess whether
        # the live guard or this audit produced the reason.
        "abort_marker_reason": "", "ledger_verdict": "",
    })
    return zeros


def _accounting_gaps(summary):
    """Named terms that are missing or not numbers. Empty tuple = countable."""
    return tuple(term for term in ACCOUNTING_TERMS
                 if not isinstance(summary.get(term), (int, float))
                 or isinstance(summary.get(term), bool))


def _incomplete(summary, reason):
    summary["complete"] = False
    summary["incomplete_reason"] = reason


def _int(value):
    return value if type(value) is int and value > 0 else 0


def _account_call(row, summary, identity_check, generation_window_s=0):
    """One call row into the summary. Never raises, never judges."""
    nbytes = _int(row.get("request_bytes"))
    summary["call_rows"] += 1
    # THE BILL IS PER CALL on linkapi (a flat 0.05 CNY), so every call row
    # counts here - discarded, refused or answered.
    summary["billed_calls"] = summary["call_rows"]
    summary["request_bytes"] += nbytes
    status = row.get("status")
    if type(status) is int and status >= 400:
        summary["refused_calls"] += 1
    refusal = row.get("upstream_refusal_class")
    if isinstance(refusal, str) and refusal.strip():
        _bump(summary["provider_refusals"], refusal.strip())
    usage = row.get("usage")
    if isinstance(usage, dict):
        cost = usage.get("cost")
        if type(cost) in (int, float) and cost > 0:
            # THE PAYER'S OWN NUMBER. CloseRouter returns usage.cost in USD;
            # measured 2026-08-26. It beats every estimate, so it gets its own
            # key and is never mixed with one.
            summary["cost_usd_reported"] = round(
                summary["cost_usd_reported"] + float(cost), 6)
            summary["cost_usd_reported_calls"] += 1
    try:
        billed, hit = cache_tokens(usage)
    except UsageUnreadable:
        billed = hit = 0
    if "route_expected_identity" in row:
        identity = row.get("route_expected_identity")
        if isinstance(identity, str) and identity.strip():
            summary["route_rows"] += 1
            _bump(summary["route_identities"],
                  identity.strip() if identity_check else "")
            _bump(summary["route_bases"], row.get("route_base"))
            _bump(summary["route_generations"], row.get("route_generation"))
    if row.get("discarded_substitution") is True:
        summary["discarded_substitutions"] += 1
        summary["discarded_prompt_tokens"] += billed
        summary["discarded_cached_tokens"] += hit
        summary["discarded_request_bytes"] += nbytes
        summary["discarded_prompt_tokens_estimated"] = (
            summary["discarded_request_bytes"] // DISCARD_BYTES_PER_TOKEN)
        name = row.get("returned_model")
        _bump(summary["discarded_by_model"],
              name if isinstance(name, str) else "?")
        return
    # CUT BY THE PROVIDER'S CLOCK: billed, and not an answer. It is counted
    # above as a call and a billed call, and it stops here - a turn the
    # provider killed at its own generation window never reached the client,
    # so calling it an accepted answer is how nine wasted calls became a
    # mystery on run 20260827T005241Z-v39.
    if (generation_window_exceeded(row.get("upstream_error"),
                                   row.get("duration_ms"), generation_window_s)
            or row.get("upstream_refusal_class") == GENERATION_WINDOW_EXCEEDED):
        summary["generation_window_exceeded_calls"] += 1
        summary["generation_window_exceeded_ms"] += _int(row.get("duration_ms"))
        summary["generation_window_exceeded_detail"].append(
            {"duration_ms": _int(row.get("duration_ms")),
             "request_max_tokens": _int(row.get("request_max_tokens"))})
        return
    summary["accepted_rows"] += 1
    if billed:
        summary["accepted_calls"] += 1
        summary["prompt_tokens"] += billed
        summary["cached_tokens"] += hit


def account_ledger(ledger_path, summary, identity_check=True,
                   generation_window_s=0):
    """Fill `summary` from the WHOLE ledger, whatever the verdict turns out to be.

    Deliberately separate from the verdict walk: the verdict can stop on row 1
    and the accounting must still describe all 463 rows. This is the function
    whose absence printed zeros on a 2h42m paid run.
    """
    try:
        handle = open(ledger_path, encoding="utf-8")
    except OSError as exc:
        _incomplete(summary,
                    "the upstream ledger %s could not be read (%s), so this "
                    "run's cost was never measured - and absence of proof is "
                    "not zero cost" % (ledger_path, exc))
        return
    with handle:
        for line in handle:
            if not line.strip():
                continue
            summary["ledger_rows"] += 1
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if not isinstance(row, dict):
                summary["unaccountable_rows"] += 1
                continue
            summary["rows_counted"] += 1
            # A PROXY EVENT IS NOT A CALL - see the row loop below.
            if isinstance(row.get("event"), str) and row["event"]:
                summary["event_rows"] += 1
                continue
            _account_call(row, summary, identity_check, generation_window_s)
    summary["route_span"] = len(summary["route_identities"])
    # The window these rows were judged against, in the summary rather than
    # only in an argument: a reader must never have to guess whether zero cut
    # calls means "none happened" or "nobody was looking".
    summary["generation_window_s"] = _positive_number(generation_window_s) or 0
    if not summary["ledger_rows"]:
        _incomplete(summary,
                    "the upstream ledger %s has no rows, so no call was ever "
                    "attributed and no cost was ever measured" % ledger_path)
        return
    if summary["unaccountable_rows"]:
        _incomplete(summary,
                    "%d of %d rows in %s could not be parsed, so the totals "
                    "below are a floor and not the cost of the run"
                    % (summary["unaccountable_rows"], summary["ledger_rows"],
                       ledger_path))
        return
    gaps = _accounting_gaps(summary)
    if gaps:
        _incomplete(summary,
                    "the accounting terms %s were never computed for %s"
                    % (", ".join(gaps), ledger_path))
        return
    summary["complete"] = True
    summary["incomplete_reason"] = ""


def audit_ledger(ledger_path, expected_identity="", cache_guard=True,
                 min_rate=DEFAULT_CACHE_MIN_RATE,
                 min_calls=DEFAULT_CACHE_MIN_CALLS, abort_path="",
                 identity_check=True, summary=None, advances_path="",
                 generation_window_s=0):
    """Judge a finished run's upstream ledger. Returns (reason, detail) or None.

    FAIL-CLOSED, deliberately and in every direction. A ledger that is absent,
    empty, unparseable, or missing the fields this reads is NOT a clean run: it
    is a run whose model identity was never measured, and unmeasured is not
    clean. The proxy writes this ledger before it answers the client, so a
    missing file means the proxy died — which is precisely when a substitution
    would go unseen.

    An empty `expected_identity` is likewise not a pass. It used to be: the
    paid launcher set nothing, so on the one run that mattered the identity
    check was off and only the cache guard was live. Pass
    `identity_check=False` to run a lane that genuinely has no declared
    identity; silence no longer buys the same thing.

    DISCARDED SUBSTITUTIONS. A row flagged `discarded_substitution: true` is a
    wrong-model answer the proxy threw away and re-issued; not one byte of it
    reached the arm, so it is NOT part of the measurement and must not be read
    as a breach here — the whole point of the retry is that the run stays
    valid. It WAS billed, so it is counted separately into `summary` and never
    silently. The flag is honoured only when the row really does name a
    different family: it must never become a way to hide a row.

    A MID-RUN ROUTE SWAP IS STILL AUDITABLE. The proxy can now change provider
    and model on a LIVE run (UPSTREAM_ROUTE_FILE), so one ledger can legitimately
    contain calls sent under two different expected identities. Judging every row
    against ONE global `--expected` would then declare a family mismatch on every
    row from the other side of the swap - the audit would refuse exactly the runs
    the hot swap exists to rescue. So each row is judged against the identity IT
    WAS SENT UNDER (`route_expected_identity`), and `--expected` is the fallback
    for rows that carry none. That is not a weakening: a row with no route
    identity is judged exactly as before, and a row WITH one is judged against a
    value the proxy wrote at the moment it made the call, which is strictly
    better evidence than a flag passed to the auditor afterwards.

    Pass a dict as `summary` to receive the accounting (accepted calls, billed
    prompt/cached tokens, discard count and cost). THE ACCOUNTING IS COMPUTED
    FIRST, over the whole ledger, on EVERY exit path - clean, breach, abort
    marker, malformed row, cache collapse. It used to be initialised to zeros
    and then abandoned when an abort marker existed, which printed
    "0 accepted calls, 0 prompt tokens" for a 2h42m paid run whose ledger held
    463 rows. The live guard's reason still WINS, because it was observed with
    the run alive; only the accounting changed.
    """
    if summary is not None:
        summary.update(_accounting_zero())
        account_ledger(ledger_path, summary, identity_check=identity_check,
                       generation_window_s=generation_window_s)
    # THE LIVE GUARD'S REASON WINS - but it no longer short-circuits the
    # accounting above, and the ledger is still judged so that
    # `ledger_verdict` records what the after-the-fact audit made of the same
    # rows. Two independent readings of one run are worth more than one.
    abort_verdict = None
    if abort_path and os.path.exists(abort_path):
        try:
            with open(abort_path, encoding="utf-8") as source:
                row = json.load(source)
            reason = row.get("reason") if isinstance(row, dict) else None
            if not isinstance(reason, str) or not reason:
                raise ValueError("no reason")
            abort_verdict = (reason, str(row.get("detail") or "")[:400])
        except (OSError, ValueError, TypeError):
            abort_verdict = ("LANE_ABORT_UNREADABLE", (
                "the proxy wrote an abort marker at %s and it cannot be read — "
                "the run stopped for a reason nothing recorded" % abort_path))
        if summary is not None:
            summary["abort_marker_reason"] = abort_verdict[0]
    verdict = _ledger_verdict(ledger_path, expected_identity, cache_guard,
                              min_rate, min_calls, identity_check, summary,
                              advances_path)
    if summary is not None and verdict:
        summary["ledger_verdict"] = verdict[0]
    return abort_verdict or verdict


def _ledger_verdict(ledger_path, expected_identity, cache_guard, min_rate,
                    min_calls, identity_check, summary, advances_path):
    """The verdict walk. Judges; does NOT do the accounting (see account_ledger)."""
    expected = expected_identity.strip() if isinstance(expected_identity, str) else ""
    # AN UNDECLARED IDENTITY IS STILL A BREACH — but a ledger whose OWN rows name
    # the identity they were sent under HAS declared it, at the moment of the
    # call, which is better evidence than a flag passed to the auditor
    # afterwards. Pre-scanned rather than deferred to the row loop so the
    # precedence of this reason over LEDGER_MISSING / LEDGER_MALFORMED is
    # unchanged: the helper answers False on an unreadable ledger, so a run with
    # no expected identity and no ledger still fails here exactly as before.
    if identity_check and not expected and not _ledger_declares_identity(ledger_path):
        return "EXPECTED_IDENTITY_UNKNOWN", (
            "no expected model identity was supplied for %s and no row names "
            "the route it was sent on, so nothing could check which model "
            "answered — an undeclared identity is a breach, not a pass (use "
            "--no-identity-check for a lane that truly has none)" % ledger_path)

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
    # THE CACHE FLOOR'S JURISDICTION, as named counters. Handed to `summary` by
    # identity so lane-audit.py can print the cost fact, but the guard reads
    # this local dict and never `summary`: a check that switches off when the
    # caller passes no dict is the dead-gate shape this file exists to prevent.
    terms = cache_terms()
    if summary is not None:
        summary["cache_judgement"] = terms
    # Counted here and not only in `summary`, because `summary` is optional and
    # a check that silently switches off when the caller passes no dict is the
    # dead-gate shape this file exists to prevent.
    route_identity_rows = 0
    # Tracked unconditionally, for the same reason as route_identity_rows:
    # `summary` is optional and a term that switches off when the caller
    # passes no dict is the dead-gate shape this file exists to prevent.
    route_identities_seen = set()
    for index, row in enumerate(rows, 1):
        # A PROXY EVENT IS NOT A CALL. `route_advance`, `route_advance_blocked`
        # and `route_unavailable` rows are written by the proxy alongside the
        # calls, and they attribute no model because no call was attributed:
        # they say the route CHANGED, or that no route could be read at all.
        # Judging them as calls made the whole ledger LEDGER_MALFORMED on the
        # first advance - measured live, 2026-08-26, on a two-provider run that
        # had healed itself correctly.
        #
        # THIS IS NOT A HOLE. An event row that NAMES a model is refused: the
        # `event` key must never become a way to slip a call past the identity
        # check. The proxy writes returned_model=None on every event it emits.
        if isinstance(row.get("event"), str) and row["event"]:
            if row.get("returned_model") is not None:
                return "LEDGER_MALFORMED", (
                    "row %d of %s is an %r event and names returned_model %r - "
                    "an event row is not a call and may not attribute one"
                    % (index, ledger_path, row["event"], row.get("returned_model")))
            continue
        for field in ("requested_model", "returned_model", "status"):
            if field not in row:
                return "LEDGER_MALFORMED", (
                    "row %d of %s has no %r field" % (index, ledger_path, field))
        status = row.get("status")
        returned = row.get("returned_model")
        # THE IDENTITY THIS ROW WAS SENT UNDER. Present only on a run that used
        # a route file; absent rows fall back to the global expected identity,
        # so every pre-route ledger is judged byte-identically to before. A
        # present-but-unusable value is NOT quietly ignored - an unreadable
        # identity is an unmeasured row, and unmeasured is never clean.
        row_identity = row.get("route_expected_identity")
        if "route_expected_identity" in row:
            if not isinstance(row_identity, str) or not row_identity.strip():
                return "ROUTE_IDENTITY_UNREADABLE", (
                    "row %d of %s carries a route_expected_identity that is not "
                    "a non-empty string (%r) — the identity that row was sent "
                    "under was never recorded, and unmeasured is not clean"
                    % (index, ledger_path, row_identity))
            # `identity_check=False` still means "this lane declares no
            # identity"; a route row does not override an explicit opt-out. The
            # blocking counter is therefore only incremented when the check is
            # ON — otherwise --no-identity-check would start failing runs as
            # RETURNED_MODEL_UNKNOWN, which is the opposite of what it asks for.
            row_expected = row_identity.strip() if identity_check else ""
            route_identities_seen.add(row_identity.strip())
            if identity_check:
                route_identity_rows += 1
        else:
            row_expected = expected
        if row.get("discarded_substitution") is True:
            # Honoured ONLY when the row is genuinely a substitution. A flag
            # that could excuse any row would be a hole straight through the
            # identity check, so an unverifiable one is malformed, not clean.
            if not row_expected or same_family(row_expected, returned):
                return "LEDGER_MALFORMED", (
                    "row %d of %s is flagged discarded_substitution but names "
                    "%r against expected %r — the flag is not a way to hide a "
                    "row" % (index, ledger_path, returned, row_expected))
            continue
        answered = type(status) is int and 200 <= status < 300
        if not answered:
            continue
        if row_expected and isinstance(returned, str) and returned.strip():
            named += 1
            if not same_family(row_expected, returned):
                return "RETURNED_MODEL_FAMILY_MISMATCH", (
                    "row %d: asked for %s (family %s), the provider answered as "
                    "%s (family %s) — the run measured a model it did not request"
                    % (index, row_expected, model_family(row_expected),
                       returned, model_family(returned)))
        try:
            billed, hit = cache_tokens(row.get("usage"))
        except UsageUnreadable as exc:
            return "USAGE_UNREADABLE", (
                "row %d of %s: %s — a row whose cost cannot be read cannot be "
                "scored as free, and free is how a cache collapse hides"
                % (index, ledger_path, exc))
        if billed:
            calls += 1
            prompt_tokens += billed
            cached_tokens += hit
            # DIRECTLY CONFIRMED means: this row was judged against a real
            # expected identity, it named a model, and the family matched. The
            # mismatch case never gets here — it returned above.
            note_cache_call(terms, billed, hit,
                            bool(row_expected
                                 and isinstance(returned, str) and returned.strip()
                                 and same_family(row_expected, returned)))

    # LIVE, NOT DECORATIVE. `route_identity_rows` is what makes this gate fire on
    # a swapped run audited with no global --expected: those rows DID declare an
    # identity, so "not one row names the model that answered" is still a breach.
    # Without it the term would be computed and never reach a verdict.
    if (expected or route_identity_rows) and not named:
        return "RETURNED_MODEL_UNKNOWN", (
            "%d rows in %s and not one names the model that answered, though "
            "%s was required — the identity was never measured"
            % (len(rows), ledger_path,
               expected or "a per-row route identity on %d row(s)"
               % route_identity_rows))

    advance_breach = audit_route_advances(advances_path,
                                          len(route_identities_seen),
                                          summary=summary)
    if advance_breach:
        return advance_breach

    if cache_guard:
        breach = cache_judgement(terms, min_rate, min_calls)
        if breach:
            return breach
    return None
