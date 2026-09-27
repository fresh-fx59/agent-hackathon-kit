#!/usr/bin/env python3
"""Stand-in trusted gate: always reports the repair is not yet done.

It deliberately never opens checkpoint.json — proof that the "repair" verdict
here comes from this independent process, not from the model's own claim.
Exit 1 = not done (same contract as hostgate.py: 0 = pass, else fail).
"""
import sys

if __name__ == "__main__":
    sys.exit(1)
