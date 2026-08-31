#!/usr/bin/env python3
"""On the corporate lane the HUMAN is the context meter, so the runbook is code.

There is no proxy ledger there. The runbook's old troubleshooting row said
"context climbs past 230,000 -> a handoff block was missed", which is a
diagnosis after the fact. v43 needs a positive instruction with the real number.

The number is not hard-coded here: it is read from handoff_threshold() itself,
the same function the driver calls, so the runbook and the driver cannot drift
apart without this test catching it.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
README = os.path.join(SHERLOCK, "skills", "v43", "README.md")
SETTINGS = os.path.join(SHERLOCK, "measure", "corporate-settings.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


spec = importlib.util.spec_from_file_location("corporate_settings", SETTINGS)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)
THRESHOLD = cs.handoff_threshold(262000, 20000)
THRESHOLD_STR = "{:,}".format(THRESHOLD)  # e.g. "169,331"
THRESHOLD_SPACED = THRESHOLD_STR.replace(",", " ")  # e.g. "169 331"

check(os.path.exists(README), "skills/v43/README.md does not exist")
if os.path.exists(README):
    text = open(README, encoding="utf-8").read()
    check(THRESHOLD_STR in text or THRESHOLD_SPACED in text or str(THRESHOLD) in text,
          "the runbook does not state the handoff threshold returned by "
          "handoff_threshold(262000, 20000) == %d" % THRESHOLD)
    check("202700" not in text and "202 700" not in text and "202,700" not in text,
          "the runbook still carries the superseded placeholder 202700")
    check("handoff --partial" in text,
          "the runbook does not tell the human to take a partial handoff")
    check("[!PROVEN]" in text,
          "the runbook does not show the v43 label form")
    check("a handoff block was missed" not in text,
          "the runbook still carries the old after-the-fact diagnosis row")
    check("context climbs past 230,000" not in text
          and "context climbs past 230000" not in text,
          "the runbook still carries the old 230,000 rule-of-thumb row")

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
