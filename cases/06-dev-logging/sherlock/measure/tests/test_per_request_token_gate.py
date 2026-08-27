#!/usr/bin/env python3
"""THE OTHER HALF OF THE PROOF GATE: no request may exceed the corporate ceiling.

The plan's gate is two-part and both halves are load-bearing: a clean report AND
every ledger row satisfying `prompt_tokens + request_max_tokens <= 262000`. A run
that obeys the ceiling and delivers nothing is not a pass, and neither is a clean
report built from illegal requests — which is exactly what r6 was: three green
gates, a real 481-line report, and 63 of its 190 rows over the ceiling, peak
prompt 327,639.

Nothing in the harness checked that. `corporate-settings.py prove` shows the
ceiling holds ARITHMETICALLY before launch; this is the empirical half, read off
the wire afterwards, because a settings file that was overridden, ignored or
misspelled looks identical to one that worked.

The declared budget is used, never the completion actually returned: the ceiling
applies to what the REQUEST ASKS FOR. A prompt of 250,000 with max_tokens 20,000
is an illegal request even if the model happens to answer in 12 tokens.

Off by default (0), so every existing lane is untouched.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
AUDIT = os.path.join(MEASURE, "lane-audit.py")
FAILED = []
GATE = 262000
IDENT = "deepseek/deepseek-v4-flash-0731"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def row(prompt, max_tokens, cached=None, **extra):
    body = {"status": 200, "stream_complete": True, "requested_model": "m",
            "sent_model": "m", "returned_model": IDENT,
            "route_expected_identity": IDENT,
            "request_max_tokens": max_tokens,
            "usage": {"prompt_tokens": prompt, "completion_tokens": 10,
                      "total_tokens": prompt + 10,
                      "prompt_tokens_details": {
                          "cached_tokens": prompt if cached is None else cached}}}
    body.update(extra)
    return body


def audit(rows, *args):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    p = subprocess.Popen([sys.executable, AUDIT, "--ledger", path,
                          "--expected", IDENT, "--no-cache-guard"] + list(args),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def main():
    legal = [row(239000, 6700), row(100, 20000), row(228000, 20000)]
    illegal = legal + [row(250000, 20000)]

    rc, out, err = audit(legal)
    check("with no gate declared, nothing is judged against one — every "
          "existing lane is untouched", "PER_REQUEST" not in out + err,
          out + err)

    rc, out, err = audit(legal, "--per-request-token-gate", str(GATE))
    check("a ledger whose every row fits the gate passes", rc == 0,
          "rc=%d %s" % (rc, (out + err)[-400:]))

    rc, out, err = audit(illegal, "--per-request-token-gate", str(GATE))
    check("ONE row over the gate fails the run",
          rc != 0 and "PER_REQUEST_TOKEN_GATE" in out + err,
          "rc=%d %s" % (rc, (out + err)[:400]))
    check("the breach names the arithmetic, not just a verdict",
          "250000" in out + err and "270000" in out + err,
          (out + err)[:500])

    # The declared budget, not the returned completion: an illegal ASK is illegal.
    tight = [row(255000, 20000, completion_hint=1)]
    rc, out, err = audit(tight, "--per-request-token-gate", str(GATE))
    check("the gate reads request_max_tokens, not the completion returned",
          rc != 0, "rc=%d %s" % (rc, (out + err)[:300]))

    # A row that cannot be judged must not pass silently — that is the whole
    # lesson of EXPECTED_IDENTITY_UNKNOWN.
    blind = [row(1000, 20000)]
    blind[0]["usage"] = {"completion_tokens": 5}          # no prompt_tokens
    rc, out, err = audit(blind, "--per-request-token-gate", str(GATE))
    check("a row with no prompt token count is a breach, not a pass",
          rc != 0 and "PER_REQUEST_TOKEN_GATE" in out + err,
          "rc=%d %s" % (rc, (out + err)[:300]))
    blind2 = [row(1000, 20000)]
    del blind2[0]["request_max_tokens"]
    rc, out, err = audit(blind2, "--per-request-token-gate", str(GATE))
    check("a row with no declared output budget is a breach too",
          rc != 0, "rc=%d %s" % (rc, (out + err)[:300]))

    # A discarded substitution never reached the arm; it is not the run's request.
    discarded = legal + [dict(row(300000, 20000), returned_model="other/model",
                              discarded_substitution=True)]
    rc, out, err = audit(discarded, "--per-request-token-gate", str(GATE))
    check("a discarded substitution is not judged as one of the run's requests",
          rc == 0, "rc=%d %s" % (rc, (out + err)[-400:]))

    rc, out, err = audit(legal, "--per-request-token-gate", "-5")
    check("a negative gate is refused rather than read as 'off'", rc != 0,
          "rc=%d %s" % (rc, (out + err)[:300]))

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ the empirical half of the proof gate is live")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
