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
                 min_calls=DEFAULT_CACHE_MIN_CALLS, abort_path="",
                 identity_check=True):
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

    expected = expected_identity.strip() if isinstance(expected_identity, str) else ""
    if identity_check and not expected:
        return "EXPECTED_IDENTITY_UNKNOWN", (
            "no expected model identity was supplied for %s, so nothing could "
            "check which model answered — an undeclared identity is a breach, "
            "not a pass (use --no-identity-check for a lane that truly has none)"
            % ledger_path)

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
        if expected and isinstance(returned, str) and returned.strip():
            named += 1
            if not same_family(expected, returned):
                return "RETURNED_MODEL_FAMILY_MISMATCH", (
                    "row %d: asked for %s (family %s), the provider answered as "
                    "%s (family %s) — the run measured a model it did not request"
                    % (index, expected, model_family(expected),
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

    if expected and not named:
        return "RETURNED_MODEL_UNKNOWN", (
            "%d rows in %s and not one names the model that answered, though "
            "%s was required — the identity was never measured"
            % (len(rows), ledger_path, expected))

    if cache_guard:
        detail = cache_breach(calls, prompt_tokens, cached_tokens, min_rate, min_calls)
        if detail:
            return "PROMPT_CACHE_COLLAPSE", detail
    return None
