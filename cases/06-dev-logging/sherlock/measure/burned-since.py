#!/usr/bin/env python3
"""burned-since.py <arm> <stamp> — tokens spent on runs that recorded NOTHING.

    python3 burned-since.py v12 20260801T220000Z

Prints one integer: the input tokens burned by `arm` on runs whose directory
stamp is >= `stamp`. Used by gate.sh as a spend guard.

Why a separate file rather than an inline heredoc in gate.sh: gate.sh already
embeds one `python3 - <<PY` block, and a second one nested inside a shell
function is exactly the kind of quoting that breaks silently and returns an
empty string — which a `-ge` test then reads as zero, i.e. a guard that is
always open. A file cannot fail that way.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 3:
        print(0)
        return
    arm, since = sys.argv[1], sys.argv[2]
    p = os.path.join(HERE, "burned.jsonl")
    total = 0
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("arm") != arm:
                continue
            # run dir stamps are ISO-basic and sort lexically, so a string
            # compare is a time compare — no parsing, no timezone to get wrong.
            if os.path.basename(r.get("run_dir") or "") < since:
                continue
            total += r.get("input_tokens") or 0
    print(total)


if __name__ == "__main__":
    main()
