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


def audit_ledger(ledger_path, expected_identity="", cache_guard=True,
                 min_rate=DEFAULT_CACHE_MIN_RATE,
                 min_calls=DEFAULT_CACHE_MIN_CALLS, abort_path="",
                 identity_check=True, summary=None, advances_path=""):
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
    prompt/cached tokens, discard count and cost). It is filled as the rows are
    read, so an early breach still leaves true partial counts.
    """
    if summary is not None:
        summary.update({"schema": 1, "accepted_calls": 0, "prompt_tokens": 0,
                        "cached_tokens": 0, "discarded_substitutions": 0,
                        "discarded_prompt_tokens": 0,
                        "discarded_cached_tokens": 0, "discarded_by_model": {},
                        # Named per-route accounting, so a swapped run says what
                        # it actually ran on instead of leaving a reader to
                        # guess from the base URL.
                        "route_rows": 0, "route_generations": {},
                        "route_bases": {}, "route_identities": {},
                        # A RUN THAT SPANNED PROVIDERS MUST SAY SO. See
                        # audit_route_advances and lane-audit.py's
                        # MULTI-ROUTE line.
                        "route_span": 0, "route_advances": 0,
                        "route_advances_blocked": 0,
                        "route_advance_checks": {}})
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
            if summary is not None:
                # The ACCOUNTING counter is unconditional: a reader wants to
                # know what the run ran on either way.
                summary["route_rows"] += 1
                _bump(summary["route_identities"], row_expected)
                _bump(summary["route_bases"], row.get("route_base"))
                _bump(summary["route_generations"], row.get("route_generation"))
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
            if summary is not None:
                try:
                    billed, hit = cache_tokens(row.get("usage"))
                except UsageUnreadable:
                    billed = hit = 0
                name = returned if isinstance(returned, str) else "?"
                summary["discarded_substitutions"] += 1
                summary["discarded_prompt_tokens"] += billed
                summary["discarded_cached_tokens"] += hit
                summary["discarded_by_model"][name] = (
                    summary["discarded_by_model"].get(name, 0) + 1)
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
            if summary is not None:
                summary["accepted_calls"] = calls
                summary["prompt_tokens"] = prompt_tokens
                summary["cached_tokens"] = cached_tokens

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

    if summary is not None:
        summary["route_span"] = len(route_identities_seen)
    advance_breach = audit_route_advances(advances_path,
                                          len(route_identities_seen),
                                          summary=summary)
    if advance_breach:
        return advance_breach

    if cache_guard:
        detail = cache_breach(calls, prompt_tokens, cached_tokens, min_rate, min_calls)
        if detail:
            return "PROMPT_CACHE_COLLAPSE", detail
    return None
