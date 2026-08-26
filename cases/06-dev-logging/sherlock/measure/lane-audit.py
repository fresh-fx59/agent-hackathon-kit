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
                        audit_ledger)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--abort", default="")
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
                          abort_path=args.abort, summary=summary)
    if args.summary_json and summary:
        try:
            with open(args.summary_json, "w", encoding="utf-8") as target:
                json.dump(summary, target, ensure_ascii=False, sort_keys=True)
                target.write("\n")
        except OSError as exc:
            sys.stderr.write("⚠ lane summary not written: %s\n" % exc)
    if breach is None:
        _report(summary)
        return 0
    reason, detail = breach
    print(reason)
    # The verdict first: run-bench.sh reads the first three lines of this
    # stream as the human detail for lane-integrity.json.
    sys.stderr.write("✗ lane integrity: %s — %s\n" % (reason, detail))
    _report(summary)
    return 1


def _report(summary):
    """One line, always, so a rising substitution rate is impossible to miss."""
    if not summary:
        return
    discarded = summary.get("discarded_substitutions", 0)
    accepted = summary.get("accepted_calls", 0)
    total = accepted + discarded
    mark = "⚠" if discarded else "ℹ"
    sys.stderr.write(
        "%s lane cost: %d accepted calls, %d discarded substitutions "
        "(%.1f%% of %d billed answers, %d prompt tokens paid for nothing)%s\n"
        % (mark, accepted, discarded, (100.0 * discarded / total) if total else 0.0,
           total, summary.get("discarded_prompt_tokens", 0),
           (" — " + ", ".join("%s x%d" % (name, count) for name, count
                              in sorted(summary.get("discarded_by_model", {}).items()))
            ) if discarded else ""))


if __name__ == "__main__":
    sys.exit(main())
