#!/usr/bin/env python3
"""lane-audit.py — did this run actually measure the model it says it measured?

The requested model id cannot protect us: linkapi routes only the floating
alias `[SP]deepseek-v4-flash` (a dated `-0731` request is 503 model_not_found —
see lane_guard.py), so a returned-side check is the only defence there is.

The live guard inside upstream-log-proxy.py is the one that saves money: it
trips on the offending call and every later call is refused, so a substitution
costs one call instead of 180. This is the SECOND line, and it exists because
the live guard has a failure mode of its own — the proxy can die, be started
without the expected identity, or never be started at all, and every one of
those looks exactly like "no problem found" from the outside.

So the runner asks this after the run instead of assuming: absent proof is a
breach, not a pass. Exit 0 = clean, 1 = breach (reason code on stdout, one
human line on stderr), 2 = this tool was called wrong.

    lane-audit.py --ledger TRACE.upstream.jsonl \
                  --abort TRACE.upstream.abort.json \
                  --expected '[SP]deepseek-v4-flash'

`--expected` is not optional in practice: an empty one is EXPECTED_IDENTITY_UNKNOWN,
a breach. That is finding #2 of the 2026-08-26 review — the paid launcher set no
identity, so the family check was off on the exact run that got substituted.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lane_guard import (DEFAULT_CACHE_MIN_CALLS, DEFAULT_CACHE_MIN_RATE,  # noqa: E402
                        audit_ledger, cache_cost_fact)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--abort", default="")
    parser.add_argument("--advances", default="",
                        help="TRACE.upstream.route-advances.jsonl. A run "
                             "that changed provider mid-flight is a "
                             "different scientific object than one that "
                             "did not, so the history is checked against "
                             "the ledger and the span is printed where "
                             "nobody can miss it.")
    parser.add_argument("--expected", default="",
                        help="the model id the run committed to. An EMPTY value "
                             "is a breach (EXPECTED_IDENTITY_UNKNOWN), not a "
                             "pass: on the v37 paid launcher it silently "
                             "disabled the identity check on the one run that "
                             "mattered. A lane with no declared identity must "
                             "say so with --no-identity-check.")
    parser.add_argument("--no-identity-check", action="store_true",
                        help="this lane genuinely has no declared model identity")
    parser.add_argument("--no-cache-guard", action="store_true")
    parser.add_argument("--cache-min-rate", type=float, default=DEFAULT_CACHE_MIN_RATE)
    parser.add_argument("--cache-min-calls", type=int, default=DEFAULT_CACHE_MIN_CALLS)
    parser.add_argument("--summary-json", default="",
                        help="write the call/discard accounting here. The "
                             "discarded wrong-model attempts are real money; a "
                             "provider that starts substituting on half its "
                             "calls must be visible in the run's artifacts, not "
                             "only in a bill.")
    args = parser.parse_args(argv)

    summary = {}
    breach = audit_ledger(args.ledger, expected_identity=args.expected,
                          cache_guard=not args.no_cache_guard,
                          identity_check=not args.no_identity_check,
                          min_rate=args.cache_min_rate,
                          min_calls=args.cache_min_calls,
                          abort_path=args.abort, summary=summary,
                          advances_path=args.advances)
    if args.summary_json and summary:
        try:
            with open(args.summary_json, "w", encoding="utf-8") as target:
                json.dump(summary, target, ensure_ascii=False, sort_keys=True)
                target.write("\n")
        except OSError as exc:
            sys.stderr.write("⚠ lane summary not written: %s\n" % exc)
    if breach is None and summary and not summary.get("complete"):
        # A COST THAT WAS NEVER MEASURED IS NOT A CLEAN RUN. Every accounting
        # term is named in lane_guard.ACCOUNTING_TERMS and checked, so this is
        # how the accounting reaches the exit code instead of only the console
        # — a printed-only number is the defect class this lane keeps shipping.
        breach = ("LANE_ACCOUNTING_INCOMPLETE",
                  summary.get("incomplete_reason")
                  or "the run's cost was never measured")
    if breach is None:
        _report(summary, args.cache_min_rate, args.cache_min_calls)
        return 0
    reason, detail = breach
    print(reason)
    # The verdict first: run-bench.sh reads the first three lines of this
    # stream as the human detail for lane-integrity.json.
    sys.stderr.write("✗ lane integrity: %s — %s\n" % (reason, detail))
    _report(summary, args.cache_min_rate, args.cache_min_calls)
    return 1


def _report(summary, min_rate=DEFAULT_CACHE_MIN_RATE,
            min_calls=DEFAULT_CACHE_MIN_CALLS):
    """One line, always — and NEVER a zero it did not measure.

    The v38 full run printed "0 accepted calls, 0 discarded substitutions
    (0.0% of 0 billed answers, 0 prompt tokens paid for nothing)" while its own
    ledger held 463 rows and 27,773,863 prompt tokens. A zero that means
    "nothing happened" and a zero that means "nobody counted" cannot share a
    line, so an uncomputed summary prints the REASON instead of numbers.

    MEASURED and ESTIMATED are separate words for separate keys. The provider
    reports no usage at all on an aborted stream, so the wasted tokens can only
    be estimated from request bytes, and an estimate that can be mistaken for a
    measurement is worse than none.
    """
    if not summary:
        return
    _report_routes(summary)
    _report_cache(summary, min_rate, min_calls)
    if not summary.get("complete"):
        sys.stderr.write(
            "✗ lane cost: NOT MEASURED — %s\n"
            % (summary.get("incomplete_reason")
               or "the accounting was never computed"))
        return
    discarded = summary.get("discarded_substitutions", 0)
    answered = summary.get("accepted_rows", 0)
    charged = summary.get("billed_calls", 0)
    parts = [
        "%d charged calls = %d answered + %d discarded substitutions (%.1f%%)"
        % (charged, answered, discarded,
           (100.0 * discarded / charged) if charged else 0.0),
        "MEASURED: %d calls with provider usage, %d prompt tokens (%d cached), "
        "%d prompt tokens reported on discarded calls, %d request bytes "
        "discarded"
        % (summary.get("accepted_calls", 0), summary.get("prompt_tokens", 0),
           summary.get("cached_tokens", 0),
           summary.get("discarded_prompt_tokens", 0),
           summary.get("discarded_request_bytes", 0)),
        "ESTIMATED (%s): ~%d discarded prompt tokens"
        % (summary.get("estimate_basis", "estimate"),
           summary.get("discarded_prompt_tokens_estimated", 0)),
    ]
    if summary.get("cost_usd_reported_calls"):
        parts.append("provider-reported cost $%s over %d call(s)"
                     % (summary["cost_usd_reported"],
                        summary["cost_usd_reported_calls"]))
    if discarded:
        parts.append("discarded: " + ", ".join(
            "%s x%d" % (name, count) for name, count
            in sorted(summary.get("discarded_by_model", {}).items())))
    if summary.get("provider_refusals"):
        parts.append("refused: " + ", ".join(
            "%s x%d" % (name, count) for name, count
            in sorted(summary["provider_refusals"].items())))
    sys.stderr.write("%s lane cost: %s\n"
                     % ("⚠" if discarded or summary.get("refused_calls")
                        else "ℹ", "; ".join(parts)))


def _report_cache(summary, min_rate, min_calls):
    """THE COST FACT, ALWAYS — even on a clean run.

    A low prompt-cache hit rate on an identity-confirmed lane is no longer a
    breach (see lane_guard.CACHE_JUDGEMENT_TERMS: the v39 CloseRouter run was
    killed at call 30 for a 16.4 % rate while 30 of 30 rows named the exact
    expected model). It is still real money, so it gets a line of its own here
    rather than only existing on the path where the guard fired.
    """
    terms = summary.get("cache_judgement")
    if not isinstance(terms, dict):
        return
    line = cache_cost_fact(terms, min_rate, min_calls)
    sys.stderr.write("%s %s\n" % ("⚠" if "REAL MONEY" in line
                                   or "NOT MEASURED" in line else "ℹ", line))


def _report_routes(summary):
    """THE LINE AN OPERATOR CANNOT MISS.

    A report synthesised across two models is a different scientific
    object than one from a single model, and nothing may bury that. When
    the run stayed on one route this prints nothing at all - the switch is
    opt-in per launch, and a single-model arm must not learn a new line.
    """
    identities = summary.get("route_identities") or {}
    if len(identities) < 2:
        return
    sys.stderr.write(
        "⚠ MULTI-ROUTE RUN: this ledger spans %d routes (%d recorded "
        "advance(s), %d blocked) — the report was synthesised across more "
        "than one provider/model, calls per route: %s\n"
        % (len(identities), summary.get("route_advances", 0),
           summary.get("route_advances_blocked", 0),
           ", ".join("%s x%d" % (name, count) for name, count
                     in sorted(identities.items()))))


if __name__ == "__main__":
    sys.exit(main())
